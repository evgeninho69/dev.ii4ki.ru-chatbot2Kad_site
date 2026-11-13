# -*- coding: utf-8 -*-
"""Сборка индекса знаний 2КАД.

Корпуса:
  page  — страницы сайта 2kad.ru (услуги, разделы)
  post  — статьи блога 2kad.ru
  law   — законодательные документы с ki-rf.ru/zakon/ (тип product:
          приказы Росреестра, письма, судебная практика, постановления)
  tool  — сервисы и инструменты из раздела «Полезное» (/poleznoe/)

Запуск:
    python backend/scripts/build_index.py               # с обновлением ki-rf
    python backend/scripts/build_index.py --keep-law    # без обновления ki-rf

Креды WP берутся из .env плагина hosting-2kad:
    C:\\Users\\ZotkinEA\\.minimax\\plugins\\hosting-2kad\\.env
Переменные: WP_USER / WP_APP_PASSWORD / WP_URL.

Если WP или ki-rf недоступны — скрипт НЕ падает: собирает индекс из того,
что есть, и пишет предупреждение в stderr.
"""
from __future__ import annotations

import datetime
import html
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent           # backend/
DATA_DIR = BASE / "data"
INDEX_PATH = DATA_DIR / "site_index.json"
KIRF_CACHE = DATA_DIR / "kirf_cache.json"

PLUGIN_ENV = Path(
    r"C:\Users\ZotkinEA\.minimax\plugins\hosting-2kad\.env"
)

KIRF_API = "https://ki-rf.ru/wp-json/wp/v2"
KIRF_UA = "Mozilla/5.0 (compatible; 2KAD-assistant-indexer/1.0)"

# Рубрики блога 2КАД → человекочитаемое имя
CATEGORY_NAMES = {
    1: "Блог",
    4: "Законодательство",
    5: "Лайфхаки",
    6: "Аналитика",
    9: "Новости",
}

# Рубрики законодательных документов ki-rf, релевантные кадастру
LAW_CATS_RELEVANT = {
    "Нормативно-правовые акты",
    "Письма Росреестра",
    "Верховный Суд РФ",
    "Постановление Правительства РФ",
    "Минстрой",
    "Определения Конституционного суда",
    "Постановление Конституционного суда",
    "ППК \"Роскадастр\"",
    "Арбитражные суды округов",
    "Письма НО СРО КИ",
    "Заключение ГосуДумы",
    "Заключения Совета Федерации",
}

# Сервисы и инструменты раздела «Полезное».
# url        — куда ведёт карточка на сайте
# title      — как называется
# text       — что делает (попадает в ответ ассистента)
# keywords   — слова, по которым ассистент узнаёт инструмент
TOOLS = [
    {
        "title": "Калькулятор стоимости кадастровых работ",
        "url": "https://2kad.ru/kalkulyator/",
        "text": "Расчёт предварительной стоимости кадастровых работ: вид объекта, "
                "площадь и расстояние от Твери. Учитывает надбавку за выезд.",
        "keywords": ["калькулятор", "посчитать стоимость", "рассчитать стоимость",
                     "сколько стоит", "предварительная стоимость", "расчёт стоимости",
                     "цена работ", "стоимость работ"],
    },
    {
        "title": "Проверка участка на затопление",
        "url": "https://2kad.ru/proverka-zatopleniya/",
        "text": "Проверка участка на затопление и подтопление по официальным данным "
                "зоны с особыми условиями использования территории (ЗОУИТ): "
                "доля площади в зоне, пересечения с водными объектами, вывод.",
        "keywords": ["затопление", "затоплен", "подтопление", "зоуит", "зона с особыми",
                     "проверить на затопление", "наводнение", "вода на участке",
                     "зона затопления", "проверка затопления"],
    },
    {
        "title": "Оценка кадастровой стоимости участка",
        "url": "https://2kad.ru/ocenka-uchastka/",
        "text": "Расчёт кадастровой стоимости земельного участка и подготовка "
                "отчёта об оценке, снижение стоимости в ЕГРН.",
        "keywords": ["оценка участка", "кадастровая стоимость", "ккс", "упущенная выгода",
                     "стоимость участка", "оценить участок", "рыночная стоимость участка",
                     "занижена стоимость", "уменьшить стоимость", "пересмотр стоимости"],
    },
    {
        "title": "Аудит имущества по кадастровому номеру",
        "url": "https://2kad.ru/audit-imushchestva/",
        "text": "Проверка имущества по кадастровому номеру: границы, зарегистрированные "
                "здания и сооружения, охранные зоны, наличие координат.",
        "keywords": ["аудит имущества", "проверить участок", "что известно",
                     "проверка по кадастровому номеру", "что есть в егрн",
                     "сведения о участке", "проверить недвижимость"],
    },
    {
        "title": "ПЗЗ Тверской области",
        "url": "https://2kad.ru/gradostroitelstvo/",
        "text": "Архив правил землепользования и застройки по районам Тверской области: "
                "документы, карты зонирования, история изменений с 2023 года.",
        "keywords": ["пзз", "правила землепользования", "градостроитель", "зонирование",
                     "территориальная зона", "вн", "вид использования", "пзз тверской"],
    },
    {
        "title": "Государственная кадастровая оценка (ГКО)",
        "url": "https://2kad.ru/goskadrovaya-ocenka/",
        "text": "Проект отчёта ГКО по Тверской области: стоимость по кадастровым "
                "районам, УПКС по сегментам, порядок подачи замечаний.",
        "keywords": ["гко", "государственная кадастровая оценка", "укс", "упкс",
                     "новое качество", "пересмотр гко", "замечания по гко"],
    },
    {
        "title": "Подсказки для собственников",
        "url": "https://2kad.ru/podskazki-dlya-sobstvennikov/",
        "text": "Памятки простым языком: межевание, техплан, оформление прав, ГПЗУ, "
                "вынос границ, ПЗЗ, типичные ошибки, выбор кадастрового инженера.",
        "keywords": ["подсказки", "памятка", "инструкция", "что делать", "пошагово",
                     "разбор", "как выбрать кадастрового инженера"],
    },
    {
        "title": "Узаконивание построенного в зоне затопления",
        "url": "https://2kad.ru/uzakonenie-v-zone-zatopleniya/",
        "text": "Порядок узаконивания дома или бани, построенных в зоне затопления: "
                "документы, ограничения, что можно сделать с постройкой.",
        "keywords": ["узаконить в зоне затопления", "построил в зоне затопления",
                     "дом в зоне затопления", "легализовать постройку", "узаконивание"],
    },
]

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"[ \t\r\f\v]+")
NL_RE = re.compile(r"\n{3,}")


# ---------------------------------------------------------------- utils
def strip_html(raw: str) -> str:
    """HTML → чистый текст с переносами строк по блокам."""
    if not raw:
        return ""
    s = raw
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<\s*br\s*/?\s*>", "\n", s, flags=re.I)
    s = re.sub(r"<\s*/\s*(p|div|li|h[1-6]|tr|section|article|blockquote)\s*>",
               "\n", s, flags=re.I)
    s = re.sub(r"<\s*(li|p|div|h[1-6]|tr)\b[^>]*>", "\n", s, flags=re.I)
    s = TAG_RE.sub(" ", s)
    s = html.unescape(s)
    s = s.replace("\xa0", " ")
    s = WS_RE.sub(" ", s)
    s = "\n".join(line.strip() for line in s.split("\n"))
    s = NL_RE.sub("\n\n", s)
    return s.strip()


def read_wp_creds() -> tuple[str, str, str]:
    """(user, app_password, base_url) из .env плагина hosting-2kad."""
    vals: dict[str, str] = {}
    if PLUGIN_ENV.exists():
        for line in PLUGIN_ENV.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            vals[k.strip()] = v.strip().strip('"').strip("'")
    user = vals.get("WP_USER", "")
    pwd = vals.get("WP_APP_PASSWORD", vals.get("WP_PASS", ""))
    base = vals.get("WP_URL", "https://2kad.ru").rstrip("/")
    if base.endswith("/wp-json/wp/v2"):
        base = base[: -len("/wp-json/wp/v2")]
    return user, pwd, base


def _http_json(url: str, headers: dict | None = None, timeout: int = 60):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", "replace")
        return json.loads(raw), dict(r.headers)


# ---------------------------------------------------------------- 2kad.ru
def fetch_wp(user: str, pwd: str, base: str, kind: str) -> list[dict]:
    """kind='posts' | 'pages' — все опубликованные записи через WP REST."""
    out: list[dict] = []
    page = 1
    per_page = 100
    while True:
        q = urllib.parse.urlencode({
            "per_page": per_page,
            "page": page,
            "status": "publish",
            "orderby": "date",
            "order": "desc",
            "_fields": "id,slug,title,excerpt,content,date,modified,categories,link",
        })
        url = f"{base}/wp-json/wp/v2/{kind}?{q}"
        req = urllib.request.Request(url)
        if user and pwd:
            import base64
            token = base64.b64encode(f"{user}:{pwd}".encode()).decode()
            req.add_header("Authorization", f"Basic {token}")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                chunk = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 400 and page > 1:
                break
            raise
        if not chunk:
            break
        out.extend(chunk)
        if len(chunk) < per_page:
            break
        page += 1
        if page > 30:
            break
    return out


# ---------------------------------------------------------------- ki-rf.ru
def fetch_kirf_law(max_docs: int = 1200) -> list[dict]:
    """Документы раздела «Законодательство» ki-rf.ru (тип product).

    Страница /posts на ki-rf — это съезды и форумы кадастровых инженеров,
    они в базу знаний НЕ идут (см. AGENTS.md, раздел 2.3).
    """
    cats: dict[int, str] = {}
    try:
        cat_items, _ = _http_json(
            f"{KIRF_API}/product_cat?per_page=100&_fields=id,name",
            headers={"User-Agent": KIRF_UA},
        )
        cats = {int(c["id"]): c["name"] for c in cat_items}
    except Exception as e:                                     # noqa: BLE001
        print(f"ki-rf: рубрики не получены ({type(e).__name__})", file=sys.stderr)

    docs: list[dict] = []
    page = 1
    per_page = 100
    while len(docs) < max_docs:
        q = urllib.parse.urlencode({
            "per_page": per_page,
            "page": page,
            "orderby": "date",
            "order": "desc",
            "_fields": "id,slug,title,excerpt,content,date,link,product_cat",
        })
        try:
            items, _ = _http_json(f"{KIRF_API}/product?{q}",
                                   headers={"User-Agent": KIRF_UA}, timeout=90)
        except urllib.error.HTTPError as e:
            if e.code == 400 and page > 1:
                break
            raise
        if not items:
            break
        for it in items:
            title = strip_html((it.get("title") or {}).get("rendered", ""))
            if not title:
                continue
            content = strip_html((it.get("content") or {}).get("rendered", ""))
            excerpt = strip_html((it.get("excerpt") or {}).get("rendered", ""))
            if not content:
                content = excerpt
            cat_names = [cats.get(int(c), "") for c in (it.get("product_cat") or [])]
            cat_names = [c for c in cat_names if c]
            docs.append({
                "id": f"kirf-{it['id']}",
                "type": "law",
                "kind": "law",
                "slug": it.get("slug", ""),
                "url": it.get("link", ""),
                "title": title,
                "excerpt": excerpt or content[:400],
                "content": content[:2600],
                "len": len(content),
                "date": (it.get("date") or "")[:10],
                "category_names": cat_names,
                "relevant": any(c in LAW_CATS_RELEVANT for c in cat_names),
            })
        if len(items) < per_page:
            break
        page += 1
        print(f"  ki-rf: страница {page-1}, документов {len(docs)}", file=sys.stderr)
    return docs


def load_cached_law() -> list[dict]:
    if not KIRF_CACHE.exists():
        return []
    try:
        return json.loads(KIRF_CACHE.read_text(encoding="utf-8")).get("docs", [])
    except Exception:                                          # noqa: BLE001
        return []


def save_cached_law(docs: list[dict]) -> None:
    KIRF_CACHE.write_text(
        json.dumps({"docs": docs, "saved_at":
                    datetime.datetime.now().isoformat(timespec="seconds")},
                   ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------- tools
def build_tools() -> list[dict]:
    out = []
    for i, t in enumerate(TOOLS, 1):
        out.append({
            "id": f"tool-{i}",
            "type": "tool",
            "kind": "tool",
            "slug": t["url"].rstrip("/").split("/")[-1],
            "url": t["url"],
            "title": t["title"],
            "excerpt": t["text"],
            "content": t["text"] + "\n\n" + " ".join(t["keywords"]),
            "len": len(t["text"]),
            "date": "",
            "category_names": ["Сервисы 2КАД"],
            "keywords": t["keywords"],
        })
    return out


# ---------------------------------------------------------------- main
def main() -> int:
    keep_law = "--keep-law" in sys.argv
    index = json.loads(INDEX_PATH.read_text(encoding="utf-8")) if INDEX_PATH.exists() else {}
    old_pages = index.get("pages", [])
    old_law = [p for p in old_pages if p.get("type") == "law"]
    old_tools = [p for p in old_pages if p.get("type") == "tool"]

    user, pwd, base = read_wp_creds()
    pages: list[dict] = []
    posts: list[dict] = []
    if user and pwd:
        try:
            raw_pages = fetch_wp(user, pwd, base, "pages")
            raw_posts = fetch_wp(user, pwd, base, "posts")
        except Exception as e:                                 # noqa: BLE001
            print(f"WP недоступен ({type(e).__name__}: {e}) — "
                  "оставляем прежние страницы.", file=sys.stderr)
            raw_pages, raw_posts = [], []
        for p in raw_pages:
            title = strip_html((p.get("title") or {}).get("rendered", ""))
            if not title:
                continue
            content = strip_html((p.get("content") or {}).get("rendered", ""))
            pages.append({
                "id": p["id"],
                "type": "page",
                "kind": "site",
                "slug": p.get("slug", ""),
                "url": p.get("link", f"{base}/{p.get('slug', '')}/"),
                "title": title,
                "excerpt": strip_html((p.get("excerpt") or {}).get("rendered", "")),
                "content": content,
                "len": len(content),
                "date": (p.get("date") or "")[:10],
                "modified": (p.get("modified") or "")[:10],
            })
        for p in raw_posts:
            title = strip_html((p.get("title") or {}).get("rendered", ""))
            if not title:
                continue
            content = strip_html((p.get("content") or {}).get("rendered", ""))
            cat_names = [CATEGORY_NAMES.get(c, "") for c in (p.get("categories") or [])]
            posts.append({
                "id": p["id"],
                "type": "post",
                "kind": "blog",
                "slug": p.get("slug", ""),
                "url": p.get("link", ""),
                "title": title,
                "excerpt": strip_html((p.get("excerpt") or {}).get("rendered", "")),
                "content": content,
                "len": len(content),
                "date": (p.get("date") or "")[:10],
                "modified": (p.get("modified") or "")[:10],
                "categories": p.get("categories") or [],
                "category_names": [c for c in cat_names if c],
            })
    else:
        print("WP-креды не найдены — страницы и посты не обновляются.", file=sys.stderr)
        pages = [p for p in old_pages if p.get("type") in (None, "page")]
        posts = [p for p in old_pages if p.get("type") == "post"]

    # ---- законодательный корпус
    if keep_law and old_law:
        law = old_law
        print(f"Законодательный корпус оставлен из кэша: {len(law)}")
    else:
        try:
            law = fetch_kirf_law()
            if law:
                save_cached_law(law)
        except Exception as e:                                 # noqa: BLE001
            print(f"ki-rf недоступен ({type(e).__name__}: {e}) — "
                  "законодательный корпус оставлен прежним.", file=sys.stderr)
            law = old_law

    tools = build_tools()
    if not tools:
        tools = old_tools

    docs = pages + posts + law + tools
    index["pages"] = docs
    index["site"] = "2kad.ru"
    index["company"] = "ООО «Центр Недвижимости 2кад»"
    index["phone"] = "+7 (4822) 41-57-68"
    index["email"] = "info@2kad.ru"
    index["calculator_url"] = "https://2kad.ru/kalkulyator/"
    index["main_url"] = "https://2kad.ru/"
    index["tools"] = [{"title": t["title"], "url": t["url"]} for t in tools]
    index["blog_stats"] = {
        "posts": len(posts),
        "laws": len(law),
        "tools": len(tools),
        "pages": len(pages),
        "total_documents": len(docs),
        "built_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    INDEX_PATH.write_text(
        json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    size_kb = INDEX_PATH.stat().st_size / 1024
    print(f"Страниц 2КАД: {len(pages)}")
    print(f"Постов блога:  {len(posts)}")
    print(f"Документов ki-rf: {len(law)}")
    print(f"Инструментов:  {len(tools)}")
    print(f"Всего документов: {len(docs)}")
    print(f"Индекс: {INDEX_PATH} ({size_kb:.1f} КБ)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
