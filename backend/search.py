# -*- coding: utf-8 -*-
"""Поиск по индексу знаний 2КАД: страницы сайта + посты блога.

Алгоритм: BM25-lite (TF * IDF) + бусты за заголовок и slug.
Блог и страницы услуг ищутся раздельно, чтобы роутер мог решить,
что лучше подходит под вопрос: страница услуги или статья блога.

Без зависимостей (только stdlib + json/regex).
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple


WORD_RE = re.compile(r"[а-яёa-z0-9]+", re.IGNORECASE)
STOP = set("""
и в на на у не что это как но он она они мы вы от до за по для из с о
а то есть во быть был была были был был быть да нет уже еще или если
к ж из-за при про без над под между также ещё более менее очень
этот эту этом этот то том тот этот эта этим той тех этих такие такой
нужно надо может ли ли же бы вот там тут как
""".split())

# Маркеры вопроса, который явно про блог/законодательство
BLOG_HINTS = re.compile(
    r"\b(блог|статья|статьи|новост|закон|законопроект|приказ|фз|нпа|"
    r"письмо|разъяснени|обзор|аналитик|комментар|что изменилось|"
    r"новый закон|новые правила|практика|кейс|разбор|как читать|"
    r"как получить|как внести|как снять|как проверить)\b",
    re.IGNORECASE,
)


def tokenize(text: str) -> List[str]:
    text = text.lower()
    tokens = WORD_RE.findall(text)
    return [t for t in tokens if t not in STOP and len(t) > 1]


def normalize_query(text: str) -> List[str]:
    return tokenize(text)


class SiteIndex:
    """Загружает site_index.json и предоставляет поиск по страницам и блогу."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data: Dict = json.loads(self.path.read_text(encoding="utf-8"))
        self.pages = self.data["pages"]

        self._tokens: Dict[int, List[str]] = {}
        self._title_tokens: Dict[int, List[str]] = {}
        self._slug_tokens: Dict[int, List[str]] = {}
        self._doc_freq: Dict[str, int] = {}
        self._doc_len: Dict[int, int] = {}
        self._doc_type: Dict[int, str] = {}
        self._tf: Dict[int, Dict[str, int]] = {}

        all_tokens: List[str] = []
        for p in self.pages:
            content_tokens = tokenize(p.get("content", ""))
            title_tokens = tokenize(p.get("title", ""))
            slug_tokens = (p.get("slug") or "").replace("-", " ").split()
            pid = p["id"]
            self._tokens[pid] = content_tokens
            self._title_tokens[pid] = title_tokens
            self._slug_tokens[pid] = slug_tokens
            self._doc_len[pid] = len(content_tokens)
            self._doc_type[pid] = p.get("type", "page")

            tf: Dict[str, int] = {}
            for t in content_tokens:
                tf[t] = tf.get(t, 0) + 1
            self._tf[pid] = tf
            all_tokens.extend(content_tokens)

        for pid, toks in self._tokens.items():
            for t in set(toks):
                self._doc_freq[t] = self._doc_freq.get(t, 0) + 1

        self._avg_dl = (
            sum(self._doc_len.values()) / max(1, len(self._doc_len))
        )
        self._n = len(self._tokens)

    # ---------------- внутреннее ----------------
    def _bm25(self, query_tokens: List[str], pid: int) -> float:
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

        score = 0.0
        for qt in query_tokens:
            df = self._doc_freq.get(qt, 0)
            if df == 0:
                continue
            idf = math.log(1 + (N - df + 0.5) / (df + 0.5))
            tf_d = tf.get(qt, 0)
            tf_t = title_tf.get(qt, 0)
            tf_s = slug_tf.get(qt, 0)

            num = tf_d * (k1 + 1)
            den = tf_d + k1 * (1 - b + b * doc_len / self._avg_dl)
            score += idf * num / den if den else 0

            # бусты: заголовок и slug
            score += tf_t * 3.0
            score += tf_s * 2.0

        return score

    def _post_boost(self, doc: Dict, query: str) -> float:
        """Доп. вес статье блога, если вопрос явно «про блог/закон»."""
        if doc.get("type") != "post":
            return 0.0
        if BLOG_HINTS.search(query or ""):
            return 2.5
        return 0.0

    # ---------------- публичное ----------------
    def search(self, query: str, top_k: int = 3,
               only: Optional[str] = None) -> List[Tuple[Dict, float]]:
        """(doc, score), отсортировано по убыванию.

        only='page' — только страницы сайта,
        only='post' — только посты блога,
        None — по всем.
        """
        q_tokens = normalize_query(query)
        if not q_tokens:
            return []
        scored: List[Tuple[Dict, float]] = []
        for p in self.pages:
            if only and p.get("type", "page") != only:
                continue
            s = self._bm25(q_tokens, p["id"]) + self._post_boost(p, query)
            if s > 0:
                scored.append((p, s))
        scored.sort(key=lambda x: -x[1])
        return scored[:top_k]

    def find_service_page(self, query: str) -> Optional[Dict]:
        """Наиболее подходящая страница услуги (без блога)."""
        results = self.search(query, top_k=1, only="page")
        if results and results[0][1] > 1.5:
            return results[0][0]
        return None

    def find_blog_post(self, query: str) -> Optional[Dict]:
        """Наиболее подходящая статья блога."""
        results = self.search(query, top_k=1, only="post")
        if results and results[0][1] > 1.5:
            return results[0][0]
        return None

    def find_best(self, query: str) -> Tuple[Optional[Dict], Optional[Dict], float]:
        """Возвращает (лучшая_страница, лучшая_статья_блога, score_лучшей).

        Логика выбора:
        - если в вопросе есть маркеры блога/закона — приоритет у статьи,
          если её score >= 0.75 * score страницы;
        - иначе приоритет у страницы, если её score >= score статьи;
        - при равенстве — берём статью (она обычно точнее по конкретике).
        """
        pages = self.search(query, top_k=3, only="page")
        posts = self.search(query, top_k=3, only="post")

        best_page = pages[0] if pages else (None, 0.0)
        best_post = posts[0] if posts else (None, 0.0)

        hints = bool(BLOG_HINTS.search(query or ""))

        if best_page[0] and best_post[0]:
            if hints:
                # вопрос явно про закон/статью — блог важнее, если он близко
                if best_post[1] >= best_page[1] * 0.75:
                    return best_page[0], best_post[0], best_post[1]
                return best_page[0], best_post[0], best_page[1]
            # обычный вопрос — страница услуги приоритетнее
            if best_page[1] >= best_post[1]:
                return best_page[0], best_post[0], best_page[1]
            return best_page[0], best_post[0], best_post[1]

        if best_page[0]:
            return best_page[0], best_post[0], best_page[1]
        if best_post[0]:
            return best_page[0], best_post[0], best_post[1]
        return None, None, 0.0

    def get(self, slug: str) -> Optional[Dict]:
        for p in self.pages:
            if p.get("slug") == slug:
                return p
        return None

    def recent_posts(self, limit: int = 6) -> List[Dict]:
        """Свежие посты блога — для «Что нового» на странице ассистента."""
        posts = [p for p in self.pages if p.get("type") == "post"]
        posts.sort(key=lambda p: (p.get("date") or "", p.get("id", 0)), reverse=True)
        return posts[:limit]

    def stats(self) -> Dict:
        pages_n = sum(1 for p in self.pages if p.get("type", "page") == "page")
        posts_n = sum(1 for p in self.pages if p.get("type") == "post")
        return {
            "pages": pages_n,
            "posts": posts_n,
            "total": len(self.pages),
            "blog_stats": self.data.get("blog_stats", {}),
        }


if __name__ == "__main__":
    import sys
    idx_path = Path(__file__).parent.parent / "data" / "site_index.json"
    idx = SiteIndex(idx_path)
    st = idx.stats()
    print(f"Loaded {st['total']} docs "
          f"({st['pages']} pages, {st['posts']} posts)\n")
    while True:
        try:
            q = input("QUERY> ").strip()
        except EOFError:
            break
        if not q:
            continue
        if q in ("exit", "quit"):
            break
        print("--- blog ---")
        for p, s in idx.search(q, top_k=3, only="post"):
            print(f"  {s:6.2f}  {p['url']}")
            print(f"         {p['title']}")
        print("--- pages ---")
        for p, s in idx.search(q, top_k=3, only="page"):
            print(f"  {s:6.2f}  {p['url']}")
            print(f"         {p['title']}")
        print()
