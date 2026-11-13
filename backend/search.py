# -*- coding: utf-8 -*-
"""Поиск по индексу знаний 2КАД.

Корпуса:
  page — страницы сайта 2kad.ru (услуги, разделы)
  post — статьи блога 2kad.ru
  law  — законодательные документы (ki-rf.ru/zakon/)
  tool — сервисы и инструменты из раздела «Полезное»

Алгоритм: BM25-lite (TF * IDF) + бусты за заголовок и slug.
Корпуса ищутся раздельно, чтобы роутер мог решить, что лучше подходит
под вопрос: услуга, статья блога, нормативный акт или инструмент.

Без зависимостей (только stdlib + json/regex).
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from topics import match_topic


WORD_RE = re.compile(r"[а-яёa-z0-9]+", re.IGNORECASE)

# Окончания, которые отбрасываем при стемминге. Без этого «межевые планы» из
# вопроса не находили документ, где написано «межевого плана», и ассистент
# отвечал не про тот приказ. Список — только частые падежные/числовые формы,
# корни короче 4 букв не трогаем.
RU_ENDINGS = (
    "ами", "ями", "остью", "ости", "ость", "ыми", "ими", "ого", "его",
    "ому", "ему", "ых", "их", "ая", "яя", "ое", "ее", "ые", "ие", "ий",
    "ый", "ам", "ям", "ах", "ях", "ов", "ев", "ой", "ей", "ем", "ом",
    "ую", "юю", "ью", "ию", "ки", "ок",
)
RU_ENDING_ONE = "аяуюыиоеь"


def stem_ru(token: str) -> str:
    """Очень грубый стеммер русского языка.

    Цель — не морфология, а совпадение падежных форм: «межевые планы»,
    «межевого плана», «межевым планом» должны находить один документ.
    """
    if len(token) < 5:
        return token
    for suf in RU_ENDINGS:
        if token.endswith(suf):
            stem = token[: -len(suf)]
            return stem if len(stem) >= 4 else token
    if token[-1] in RU_ENDING_ONE:
        stem = token[:-1]
        if len(stem) >= 4:
            # «участка» → «участк» → «участ»
            if stem.endswith("к") and len(stem) - 1 >= 4:
                return stem[:-1]
            return stem
    return token


STOP = set("""
и в на на у не что это как но он она они мы вы от до за по для из с о
а то есть во быть был была были был был быть да нет уже еще или если
к ж из-за при про без над под между также ещё более менее очень
этот эту этом этот то том тот этот эта этим той тех этих такие такой
нужно надо может ли ли же бы вот там тут как
если после перед между под над при без
делать сделать сделал делал нужно нужна нужны нужное
мочь могут мог есть чем
мой моя мои свой свои своих тоже здесь
""".split())

# Ключевые слова сервисов из «Полезного», которые слишком общие: по ним нельзя
# понять, что человек просит именно этот сервис. «Узаконивание» — показательный
# пример: вопрос «построил дом, что делать для узаконивания» уводил в сервис
# «Узаконивание построенного в зоне затопления».
GENERIC_TOOL_KEYWORDS = {
    "узаконивание", "легализовать постройку", "что делать", "разбор",
    "инструкция", "пошагово", "памятка", "подсказки", "сколько стоит",
    "стоимость работ", "цена работ", "предварительная стоимость",
    "проверить участок", "проверить недвижимость", "что известно",
    "кадастровая стоимость", "стоимость участка",
}

# Некоторые сервисы нужно не просто назвать, а именно запросить: сервис
# проверки участка на затопление уместен, когда человек просит ПРОВЕРИТЬ
# участок. Вопрос «как узаконить дом в зоне затопления» — это про узаконивание,
# и он должен вести на посадочную страницу, а не на сервис проверки.
TOOL_REQUIRED_HINTS = {
    "proverka-zatopleniya": ("провер", "зоуит"),
}

# Признаки вопроса по тематике 2КАД. Если ни одного — вопрос не про кадастр
# и недвижимость, и ассистент честно отвечает, что помогает по своей теме.
DOMAIN_STEMS = (
    "кадастр", "недвижим", "участ", "дом", "здани", "объект", "помещен",
    "квартир", "комнат", "дорог", "сет", "гараж", "дач", "баня", "постро",
    "строительств", "строй", "реконструкц", "перепланиров", "оформ",
    "узакон", "легализ", "прав", "собственност", "регистрац", "егрн",
    "межев", "техплан", "техническ", "план", "приостанов", "вынос",
    "границ", "координат", "точн", "эксплуатац", "ввод", "разрешени",
    "гпзу", "пзз", "зонирован", "территор", "оцен", "стоимост", "гко",
    "упкс", "затопл", "подтопл", "зоуит", "наводн", "приказ", "закон",
    "норматив", "росреестр", "акт", "суд", "документ", "справк", "выпис",
    "калькулятор", "аудит", "проверк", "услуг", "цена", "стоим", "смет",
    "срок", "налог", "ипотек", "материнск", "наследств", "дарени",
    "купл", "аренд", "выкуп", "обременен",
)

# Маркеры вопроса, который явно «про закон/нормативку»
BLOG_HINTS = re.compile(
    r"\b(блог|статья|статьи|новост|закон|законопроект|приказ|фз|нпа|"
    r"письмо|разъяснени|обзор|аналитик|комментар|что изменилось|"
    r"новый закон|новые правила|практика|кейс|разбор|как читать|"
    r"как получить|как внести|как снять|как проверить)\b",
    re.IGNORECASE,
)

# Маркеры законодательного вопроса — по ним ищем прежде всего нормативку
LAW_HINTS = re.compile(
    r"(приказ|закон\b|законодательств|нормативн|правов\w*|судебн\w*|"
    r"письм\w* (росреестра|минстроя|минэконом)|постановлени\w*|"
    r"№\s*[ПP]\s*/\s*\d|[ПP]\s*/\s*\d{3,4}\s*/\s*\d{2,4}|"
    r"\d+\s*[-–—]\s*ФЗ|№\s*\d+\s*[-–—]\s*ФЗ|"
    r"верховн\w* суд|конституционн\w* суд|арбитражн\w*|"
    r"вступил\w* в силу|ужесточ\w*|отменен\w*|утратил\w* силу|"
    r"изменил\w*\s+(закон|порядок|правила|требования)|"
    r"что изменилось|действует ли|обязател\w* ли)",
    re.IGNORECASE,
)

# Рубрики законодательных документов, релевантные кадастру и недвижимости
LAW_CATS_RELEVANT = (
    "нормативно-правовые акты", "письма росреестра", "верховный суд",
    "постановление правительства", "минстрой", "конституционн",
    "роскадастр", "арбитражные суды", "государственн", "госдум",
    "совета федерации", "заключение",
)


def tokenize(text: str) -> List[str]:
    text = text.lower()
    tokens = WORD_RE.findall(text)
    return [stem_ru(t) for t in tokens if t not in STOP and len(t) > 1]


def normalize_query(text: str) -> List[str]:
    return tokenize(text)


def _same_root(a: str, b: str) -> bool:
    """Одинаковый корень слова: «экспертизы» ~ «экспертиза».

    Нужен для сопоставления названий видов работ в прайсе с вопросом
    пользователя, где слово стоит в падежной форме. Допускаем до двух
    различающихся окончаний («землеустроительной» ~ «землеустроительная»).
    """
    if a == b:
        return True
    n = min(len(a), len(b)) - 2
    if n < 5:
        return False
    return a[:n] == b[:n]


def _root_match(a: str, b: str) -> bool:
    """Совпадение двух слов по корню.

    «построил» ~ «построек», «документ» ~ «документац», «межев» ~ «межева».
    Грубый стеммер обрезает окончания, поэтому одни и те же слова в разных
    падежах выглядят по-разному — а для ответа нужно понимать, что речь
    о том же предмете.
    """
    if not a or not b:
        return False
    if a.startswith(b) or b.startswith(a):
        return True
    if len(a) >= 5 and len(b) >= 5 and a[:5] == b[:5]:
        return True
    return False


def _phrase_in_stems(key_stems, q_stems) -> bool:
    """Есть ли в вопросе слова ключевого выражения (по корню, по порядку)."""
    if not key_stems or not q_stems:
        return False
    i = 0
    for ks in key_stems:
        found = False
        while i < len(q_stems):
            qs = q_stems[i]
            i += 1
            if _root_match(qs, ks):
                found = True
                break
        if not found:
            return False
    return True


def is_domain_question(query: str) -> bool:
    """Похож ли вопрос на тематику 2КАД (кадастр, недвижимость, документы)."""
    for t in normalize_query(query):
        for k in DOMAIN_STEMS:
            if t.startswith(k):
                return True
            if len(t) >= 5 and k.startswith(t):
                return True
    return False


def normalize_act(text: str) -> str:
    """«П / 0393 / 26» → «П/0393/26», «305 - ФЗ» → «305-ФЗ»."""
    s = re.sub(r"\s+", "", text or "")
    return s.replace("П/".lower(), "П/").upper().replace("ФЗ", "ФЗ")


class SiteIndex:
    """Загружает site_index.json и предоставляет поиск по корпусам."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data: Dict = json.loads(self.path.read_text(encoding="utf-8"))
        self.pages = self.data["pages"]

        self._tokens: Dict[str, List[str]] = {}
        self._title_tokens: Dict[str, List[str]] = {}
        self._slug_tokens: Dict[str, List[str]] = {}
        self._excerpt_tokens: Dict[str, List[str]] = {}
        self._doc_freq: Dict[str, int] = {}
        self._doc_len: Dict[str, int] = {}
        self._price_rows: Optional[List[Dict]] = None
        self._doc_type: Dict[str, str] = {}
        self._tf: Dict[str, Dict[str, int]] = {}

        all_tokens: List[str] = []
        for p in self.pages:
            content_tokens = tokenize(p.get("content", ""))
            title_tokens = tokenize(p.get("title", ""))
            slug_tokens = (p.get("slug") or "").replace("-", " ").split()
            # Предмет документа (excerpt) — у нормативных актов это точная
            # формулировка: «требования к подготовке межевого плана». Без
            # него поиск по «межевые планы» уводил на посторонние приказы.
            excerpt_tokens = tokenize(p.get("excerpt", "") or "")
            pid = str(p["id"])
            self._tokens[pid] = content_tokens
            self._title_tokens[pid] = title_tokens
            self._slug_tokens[pid] = slug_tokens
            self._excerpt_tokens[pid] = excerpt_tokens
            self._doc_len[pid] = len(content_tokens)
            self._doc_type[pid] = p.get("type", "page")

            tf: Dict[str, int] = {}
            for t in content_tokens:
                tf[t] = tf.get(t, 0) + 1
            self._tf[pid] = tf
            all_tokens.extend(content_tokens)

        # Частота слова в корпусе: учитываем и текст, и заголовок, и
        # предмет документа — иначе слово из заголовка считается «неизвестным».
        for pid, toks in self._tokens.items():
            all_t = (set(toks)
                     | set(self._title_tokens.get(pid, []))
                     | set(self._excerpt_tokens.get(pid, [])))
            for t in all_t:
                self._doc_freq[t] = self._doc_freq.get(t, 0) + 1

        self._avg_dl = (
            sum(self._doc_len.values()) / max(1, len(self._doc_len))
        )
        self._n = len(self._tokens)

    # ---------------- внутреннее ----------------
    def _bm25(self, query_tokens: List[str], pid: str) -> float:
        k1 = 1.5
        b = 0.75
        doc_tokens = self._tokens[pid]
        doc_len = self._doc_len[pid]
        N = self._n
        if not doc_tokens:
            return 0.0

        tf = self._tf[pid]
        title_tf: Dict[str, int] = {}
        for t in self._title_tokens[pid]:
            title_tf[t] = title_tf.get(t, 0) + 1
        slug_tf: Dict[str, int] = {}
        for t in self._slug_tokens[pid]:
            slug_tf[t] = slug_tf.get(t, 0) + 1
        excerpt_tf: Dict[str, int] = {}
        for t in self._excerpt_tokens.get(pid, []):
            excerpt_tf[t] = excerpt_tf.get(t, 0) + 1

        score = 0.0
        for qt in query_tokens:
            df = self._doc_freq.get(qt, 0)
            if df == 0:
                continue
            idf = math.log(1 + (N - df + 0.5) / (df + 0.5))
            tf_d = tf.get(qt, 0)
            tf_t = title_tf.get(qt, 0)
            tf_s = slug_tf.get(qt, 0)
            tf_e = excerpt_tf.get(qt, 0)

            num = tf_d * (k1 + 1)
            den = tf_d + k1 * (1 - b + b * doc_len / self._avg_dl)
            score += idf * num / den if den else 0

            # Поля: заголовок, предмет документа, slug. С насыщением —
            # иначе длинный «предмет» с двадцатью повторами слова «план»
            # перевешивал документ, который как раз про эту тему.
            if tf_t:
                score += idf * (tf_t * 3.0) / (tf_t + 1.0)
            if tf_e:
                score += idf * (tf_e * 2.5) / (tf_e + 1.0)
            if tf_s:
                score += idf * (tf_s * 2.0) / (tf_s + 1.0)

        return score

    def _post_boost(self, doc: Dict, query: str) -> float:
        """Доп. вес статье блога, если вопрос явно «про блог/закон»."""
        if doc.get("type") != "post":
            return 0.0
        if BLOG_HINTS.search(query or ""):
            return 2.5
        return 0.0

    def _law_boost(self, doc: Dict, query: str) -> float:
        """Вес нормативному документу: профильные рубрики + законодательные
        маркеры в вопросе."""
        score = 0.0
        if doc.get("type") != "law":
            return 0.0
        cats = " ".join(doc.get("category_names") or []).lower()
        if any(c in cats for c in LAW_CATS_RELEVANT):
            score += 2.0
        if doc.get("relevant"):
            score += 1.0
        if LAW_HINTS.search(query or ""):
            score += 2.5
        return score

    def _tool_boost(self, doc: Dict, query: str) -> float:
        """Инструменты — только если в вопросе есть их слова."""
        if doc.get("type") != "tool":
            return 0.0
        q = (query or "").lower()
        for kw in doc.get("keywords") or []:
            if kw.lower() in q:
                return 4.0
        return 0.0

    def _phrase_bonus(self, q_tokens: List[str], pid: str) -> float:
        """Бонус за совпадение фразы: соседние слова вопроса подряд в тексте.

        BM25 не учитывает порядок слов, поэтому документ, где «межевого плана»
        упоминается десять раз, обгонял приказ, который как раз утверждает
        требования к межевому плану. Фраза в «предмете» документа ценится
        выше, чем в теле.
        """
        if len(q_tokens) < 2:
            return 0.0
        pairs = {(a, b) for a, b in zip(q_tokens, q_tokens[1:]) if a != b}
        if not pairs:
            return 0.0

        def hits_in(seq: List[str]) -> int:
            n = 0
            prev = None
            for t in seq:
                if prev is not None and (prev, t) in pairs:
                    n += 1
                prev = t
            return n

        body = hits_in(self._tokens.get(pid, []))
        subject = hits_in(self._excerpt_tokens.get(pid, []))
        title = hits_in(self._title_tokens.get(pid, []))
        return min(body, 3) * 0.8 + min(subject, 2) * 2.5 + min(title, 2) * 1.5

    # ---------------- публичное ----------------
    def search(self, query: str, top_k: int = 3,
               only: Optional[str] = None) -> List[Tuple[Dict, float]]:
        """(doc, score), отсортировано по убыванию.

        only='page' | 'post' | 'law' | 'tool' | None — фильтр корпуса.
        """
        q_tokens = normalize_query(query)
        if not q_tokens:
            return []
        scored: List[Tuple[Dict, float]] = []
        for p in self.pages:
            if only and p.get("type", "page") != only:
                continue
            pid = str(p["id"])
            s = (self._bm25(q_tokens, pid)
                 + self._post_boost(p, query)
                 + self._law_boost(p, query)
                 + self._tool_boost(p, query))
            if s > 0:
                scored.append((p, s))
        scored.sort(key=lambda x: -x[1])
        # Пересчитываем с бонусом за фразу только верхние кандидаты —
        # полный проход по всем документам не нужен и стоил бы времени.
        pool = scored[: max(top_k * 10, 150)]
        if len(pool) > 1:
            for i, (p, s) in enumerate(pool):
                pool[i] = (p, s + self._phrase_bonus(q_tokens, str(p["id"])))
            pool.sort(key=lambda x: -x[1])
        return pool[:top_k]

    def top(self, query: str, only: str,
            min_score: float = 1.5) -> Tuple[Optional[Dict], float]:
        """Лучший документ корпуса и его score (0 — ничего не нашли)."""
        res = self.search(query, top_k=1, only=only)
        if res and res[0][1] > min_score:
            return res[0][0], res[0][1]
        return None, 0.0

    def find_service_page(self, query: str) -> Optional[Dict]:
        """Наиболее подходящая страница услуги (без блога и закона)."""
        doc, _ = self.top(query, "page")
        return doc

    def find_blog_post(self, query: str) -> Optional[Dict]:
        """Наиболее подходящая статья блога."""
        doc, _ = self.top(query, "post")
        return doc

    def find_law(self, query: str) -> Optional[Dict]:
        """Наиболее подходящий нормативный документ."""
        doc, _ = self.top(query, "law", min_score=2.0)
        return doc

    def find_tool(self, query: str) -> Optional[Dict]:
        """Инструмент из «Полезного», если вопрос про него."""
        doc, _ = self.top(query, "tool", min_score=2.0)
        return doc

    def find_tool_strict(self, query: str) -> Tuple[Optional[Dict], List[str]]:
        """Сервис из «Полезного» — только по буквальному ключевому слову.

        По очкам BM25 сервис выбирался неверно: документ «Узаконивание
        построенного в зоне затопления» большой и насыщен словом
        «узаконивание», поэтому он выигрывал на вопросе «построил дом, что
        делать для узаконивания». Теперь сервис определяется только тем,
        действительно ли его ключевое слово написано в вопросе.
        """
        q_stems = normalize_query(query)
        best = None
        best_hits: List[str] = []
        best_score = 0
        for p in self.pages:
            if p.get("type") != "tool":
                continue
            hits: List[str] = []
            for kw in p.get("keywords") or []:
                k = (kw or "").strip().lower()
                if not k or k in GENERIC_TOOL_KEYWORDS:
                    continue
                key_stems = [stem_ru(w) for w in WORD_RE.findall(k)]
                if not key_stems:
                    continue
                if _phrase_in_stems(key_stems, q_stems):
                    hits.append(k)
            if not hits:
                continue
            need = TOOL_REQUIRED_HINTS.get(p.get("slug") or "")
            if need:
                if not any(any(_root_match(qs, k) for qs in q_stems)
                           for k in need):
                    continue
            hits.sort(key=len, reverse=True)
            score = len(hits[0]) + sum(len(h) for h in hits[1:])
            if score > best_score:
                best, best_hits, best_score = p, hits, score
        return best, best_hits

    def unmatched_terms(self, query: str) -> List[str]:
        """Слова вопроса, которых нет в базе знаний.

        Это сигнал «вопроса нет на сайте»: например «сгорел» не встречается
        ни в одном материале 2КАД — значит отвечать нужно по общим знаниям,
        а не подставлять случайную статью.
        """
        out: List[str] = []
        for t in normalize_query(query):
            if len(t) >= 4 and self._doc_freq.get(t, 0) == 0 and t not in out:
                out.append(t)
        return out

    def coverage(self, query: str, doc: Dict) -> float:
        """Доля значимых слов вопроса, найденных в документе.

        Сравнение идёт по корню слова: «построил» и «построек» — одно и то же.
        """
        if not doc:
            return 0.0
        q = normalize_query(query)
        if not q:
            return 0.0
        pid = str(doc.get("id"))
        have = (set(self._tokens.get(pid, []))
                | set(self._title_tokens.get(pid, []))
                | set(self._excerpt_tokens.get(pid, [])))
        hits = 0
        for t in q:
            if any(_root_match(d, t) for d in have):
                hits += 1
        return hits / float(len(q))

    def distinguishing_terms(self, query: str) -> List[str]:
        """Редкие слова вопроса — именно они отличают тему от соседней.

        Если слово встречается в базе один-два раза, материал про него
        должен быть заголовком, а не случайной строчкой внутри статьи.
        """
        out: List[str] = []
        for t in normalize_query(query):
            df = self._doc_freq.get(t, 0)
            if len(t) >= 4 and 0 < df <= 3 and t not in out:
                out.append(t)
        return out

    def headline_coverage(self, query: str, doc: Dict) -> float:
        """Доля слов вопроса, попавших в заголовок и предмет документа."""
        if not doc:
            return 0.0
        q = normalize_query(query)
        if not q:
            return 0.0
        pid = str(doc.get("id"))
        head = (set(self._title_tokens.get(pid, []))
                | set(self._excerpt_tokens.get(pid, [])))
        hits = 0
        for t in q:
            if any(_root_match(d, t) for d in head):
                hits += 1
        return hits / float(len(q))

    def headline_has_all(self, query: str, doc: Dict, terms: List[str]) -> bool:
        """Есть ли все редкие слова вопроса в заголовке/предмете документа."""
        if not doc or not terms:
            return True
        pid = str(doc.get("id"))
        head = (set(self._title_tokens.get(pid, []))
                | set(self._excerpt_tokens.get(pid, [])))
        for t in terms:
            if not any(_root_match(d, t) for d in head):
                return False
        return True

    def topic_doc(self, query: str) -> Optional[Dict]:
        """Материал 2КАД по карте тем (topics.py).

        Карта важнее поиска: она даёт предсказуемую финальную ссылку на нужную
        услугу или нужную статью. Если тема в карте не описана — берём None
        и работает обычный поиск по базе знаний.
        """
        tokens = [stem_ru(t) for t in WORD_RE.findall((query or "").lower())]
        topic = match_topic(tokens)
        if not topic:
            return None
        doc = self.get(topic["slug"])
        if doc and doc.get("type") == "page":
            return doc
        return None

    def find_by_act(self, act: str) -> Optional[Dict]:
        """Статья 2КАД или страница услуги, где упоминается конкретный акт
        («П/0393», «305-ФЗ»). Берём самую свежую публикацию."""
        needle = normalize_act(act)
        if not needle:
            return None
        best: Optional[Dict] = None
        for p in self.pages:
            if p.get("type") not in ("post", "page"):
                continue
            hay = f"{p.get('title', '')}\n{p.get('content', '')}"
            if needle.lower() in hay.lower():
                if best is None or (p.get("date") or "") > (best.get("date") or ""):
                    best = p
        return best

    def find_law_by_act(self, act: str) -> Optional[Dict]:
        """Нормативный документ с указанным номером («П/0393», «305-ФЗ»).

        Сначала ищем совпадение в НАЗВАНИИ документа (сам акт), и только
        потом — в тексте (ссылка на другой акт внутри документа — это
        не тот документ, который спрашивали).
        """
        needle = normalize_act(act).lower()
        if not needle:
            return None
        in_title: Optional[Dict] = None
        in_text: Optional[Dict] = None
        for p in self.pages:
            if p.get("type") != "law":
                continue
            title = (p.get("title") or "").lower()
            content = (p.get("content") or "").lower()
            if needle in title:
                if in_title is None or (p.get("date") or "") > (in_title.get("date") or ""):
                    in_title = p
            elif needle in content:
                if in_text is None or (p.get("date") or "") > (in_text.get("date") or ""):
                    in_text = p
        return in_title or in_text

    def get(self, slug: str) -> Optional[Dict]:
        for p in self.pages:
            if p.get("slug") == slug:
                return p
        return None

    # ---------------- публичный прайс ----------------
    #
    # Цены берутся ДОСЛОВНО со страницы /ceny-biznes/ — это единственный
    # источник, который ассистент вправе называть пользователю. Никаких
    # «примерно», никаких цифр из памяти модели.
    #
    # Строка прайса в индексе выглядит так (4 строки подряд):
    #   Технический план (здание, МКД, ОНС, помещение)
    #   от 15 000 ₽
    #   5–10 рабочих дней
    #   Технический план, зарегистрированный в ЕГРН
    _PRICE_ROW_RE = re.compile(
        r"^([^\n]+)\n(от\s*[\d\s\u00a0]+\s*₽)\n([^\n]+)\n([^\n]+)$", re.M)

    def price_rows(self) -> List[Dict]:
        """Публичный прайс 2КАД: [{work, price, term}, ...]."""
        if self._price_rows is None:
            rows: List[Dict] = []
            doc = self.get("ceny-biznes") or {}
            text = doc.get("content") or ""
            for m in self._PRICE_ROW_RE.finditer(text):
                work = m.group(1).strip()
                # «Стоимость» / «Срок» — это заголовки таблицы, а не вид работы
                if work.lower() in ("вид работы", "стоимость", "срок",
                                     "итоговый документ"):
                    continue
                rows.append({
                    "work": work,
                    "price": re.sub(r"\s+", " ", m.group(2).strip()),
                    "term": m.group(3).strip(),
                })
            self._price_rows = rows
        return self._price_rows

    def price_context(self) -> str:
        """Прайс как блок контекста для промпта."""
        rows = self.price_rows()
        if not rows:
            return ""
        lines = [f"- {r['work']} — {r['price']} ({r['term']})" for r in rows]
        block = ("ПУБЛИЧНЫЙ ПРАЙС 2КАД (страница «Стоимость кадастровых работ "
                 "для организаций», /ceny-biznes/):\n" + "\n".join(lines))

        # Раздел «Часть по стоимости»: варианты (сложный, вспомогательный,
        # линейный объект) и правила выезда — их нет в основной таблице.
        doc = self.get("ceny-biznes") or {}
        text = doc.get("content") or ""
        start = text.find("Часть по стоимости")
        end = text.find("Рассчитаем стоимость по вашему объекту")
        if start != -1 and end > start:
            faq = text[start:end].strip()
            if len(faq) > 1200:
                faq = faq[:1200].rsplit("\n", 1)[0]
            block += ("\n\nУТОЧНЕНИЯ ПО СТОИМОСТИ (с той же страницы):\n" + faq)
        return block

    def price_match(self, query: str) -> Optional[Dict]:
        """Строка прайса, соответствующая вопросу (по совпадению слов).

        Сравнение мягкое — по общему началу слова: «землеустроительной» из
        вопроса должен найти «Землеустроительная» из прайса.
        """
        rows = self.price_rows()
        if not rows:
            return None
        q = tokenize(query)
        best, best_score = None, 0
        for r in rows:
            t = tokenize(r["work"])
            score = 0
            for a in q:
                for b in t:
                    if _same_root(a, b):
                        score += 1
                        break
            if score > best_score:
                best, best_score = r, score
        return best if best_score > 0 else None

    def best_passages(self, query: str, doc: Dict, max_sentences: int = 4,
                      max_chars: int = 800) -> str:
        """Извлекательные выдержки из документа по запросу.

        Гарантированный ответ, когда LLM недоступен или провайдер завис:
        текст берётся ДОСЛОВНО из найденного материала (страницы услуги,
        статьи блога или нормативного акта) — ничего не выдумывается,
        только выбираются наиболее релевантные предложения и сохраняется
        их исходный порядок.
        """
        content = (doc.get("content") or "").strip()
        if not content:
            return ""

        raw = re.split(r"(?<=[.!?…])\s+|\n+", content)
        sentences = []
        for s in raw:
            s = s.strip(" \t•·-—")
            words = s.split()
            # отбрасываем заголовки, вопросы из FAQ и короткие огрызки
            if len(s) <= 40 or len(words) < 5:
                continue
            if s.endswith("?"):
                continue
            sentences.append(s)
        if not sentences:
            return content[:max_chars].strip()

        q_tokens = set(normalize_query(query))
        scored = []
        for i, s in enumerate(sentences):
            toks = set(tokenize(s))
            if not toks:
                continue
            hit = sum(1 for t in toks if t in q_tokens)
            if hit == 0:
                continue
            density = hit / max(1, len(toks))
            # конкретика (числа, даты, номера документов, цены) ценится выше
            concrete = 1.0 if re.search(r"\d|₽|%", s) else 0.0
            score = hit + 2.0 * density + 0.5 * concrete
            scored.append((score, i, s))

        if scored:
            top = sorted(scored, key=lambda x: (-x[0], x[1]))[:max_sentences]
            picked = sorted(top, key=lambda x: x[1])
        else:
            picked = list(enumerate(sentences))[:max_sentences]

        out = " ".join(s for _, _, s in picked).strip()
        if len(out) > max_chars:
            out = out[:max_chars].rsplit(" ", 1)[0] + "…"
        return out

    def recent_posts(self, limit: int = 6) -> List[Dict]:
        """Свежие посты блога — для «Что нового» на странице ассистента."""
        posts = [p for p in self.pages if p.get("type") == "post"]
        posts.sort(key=lambda p: (p.get("date") or "", p.get("id", 0)), reverse=True)
        return posts[:limit]

    def stats(self) -> Dict:
        def count(t: str) -> int:
            return sum(1 for p in self.pages if p.get("type", "page") == t)

        return {
            "pages": count("page"),
            "posts": count("post"),
            "laws": count("law"),
            "tools": count("tool"),
            "total": len(self.pages),
            "blog_stats": self.data.get("blog_stats", {}),
        }


if __name__ == "__main__":
    idx_path = Path(__file__).parent.parent / "data" / "site_index.json"
    idx = SiteIndex(idx_path)
    st = idx.stats()
    print(f"Loaded {st['total']} docs "
          f"({st['pages']} pages, {st['posts']} posts, "
          f"{st['laws']} laws, {st['tools']} tools)\n")
    while True:
        try:
            q = input("QUERY> ").strip()
        except EOFError:
            break
        if not q:
            continue
        if q in ("exit", "quit"):
            break
        for only in ("law", "post", "page", "tool"):
            hits = idx.search(q, top_k=2, only=only)
            if hits:
                print(f"--- {only} ---")
                for p, s in hits:
                    print(f"  {s:6.2f}  {p['title'][:80]}")
                    print(f"         {p['url']}")
        print()
