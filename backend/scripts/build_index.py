# -*- coding: utf-8 -*-
"""Сборка индекса знаний 2КАД: страницы сайта + посты блога.

Запускается вручную:
    python backend/scripts/build_index.py

Что делает:
1. Берёт существующие «страницы» из site_index.json (их собирал отдельный сборщик).
2. Забирает опубликованные посты блога через WP REST API.
3. Добавляет посты в pages[] с пометкой type="post", kind="blog", date, categories.
4. Перезаписывает site_index.json.

Креды WP берутся из .env плагина hosting-2kad:
    C:\\Users\\ZotkinEA\\.minimax\\plugins\\hosting-2kad\\.env
Переменные: WP_USER / WP_APP_PASSWORD (или WP_URL).

Если WP недоступен — скрипт НЕ падает: собирает индекс только из страниц
и пишет предупреждение в stderr.
"""
from __future__ import annotations

import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent           # backend/
DATA_DIR = BASE / "data"
INDEX_PATH = DATA_DIR / "site_index.json"

PLUGIN_ENV = Path(
    r"C:\Users\ZotkinEA\.minimax\plugins\hosting-2kad\.env"
)

# Рубрики блога → человекочитаемое имя
CATEGORY_NAMES = {
    1: "Блог",
    4: "Законодательство",
    5: "Лайфхаки",
    6: "Аналитика",
    9: "Новости",
}

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"[ \t\r\f\v]+")
NL_RE = re.compile(r"\n{3,}")


# ---------------------------------------------------------------- utils
def strip_html(raw: str) -> str:
    """HTML → чистый текст с переносами строк по блокам."""
    if not raw:
        return ""
    s = raw
    # скрипты/стили выкидываем целиком
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s, flags=re.S | re.I)
    # блочные элементы → перенос строки
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
    base = vals.get("WP_URL", "https://2kad.ru")
    base = base.rstrip("/")
    if base.endswith("/wp-json/wp/v2"):
        base = base[: -len("/wp-json/wp/v2")]
    return user, pwd, base


def fetch_posts(user: str, pwd: str, base: str, per_page: int = 100) -> list[dict]:
    """Все опубликованные посты (без дублей) через WP REST."""
    posts: list[dict] = []
    page = 1
    while True:
        q = urllib.parse.urlencode({
            "per_page": per_page,
            "page": page,
            "status": "publish",
            "orderby": "date",
            "order": "desc",
            "_fields": "id,slug,title,excerpt,content,date,modified,categories,link",
        })
        url = f"{base}/wp-json/wp/v2/posts?{q}"
        req = urllib.request.Request(url)
        if user and pwd:
            import base64
            token = base64.b64encode(f"{user}:{pwd}".encode()).decode()
            req.add_header("Authorization", f"Basic {token}")
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                chunk = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 400 and page > 1:
                break            # больше страниц нет
            raise
        if not chunk:
            break
        posts.extend(chunk)
        if len(chunk) < per_page:
            break
        page += 1
        if page > 20:            # страховка
            break
    return posts


# ---------------------------------------------------------------- main
def main() -> int:
    if not INDEX_PATH.exists():
        print(f"Индекс не найден: {INDEX_PATH}", file=sys.stderr)
        return 1

    index = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    pages = index.get("pages", [])
    before = len(pages)

    # убираем прежние посты — чтобы не копились дубли при повторном запуске
    pages = [p for p in pages if p.get("type") != "post"]

    user, pwd, base = read_wp_creds()
    if not user or not pwd:
        print("WP-креды не найдены в .env плагина hosting-2kad — "
              "индекс оставлен только из страниц.", file=sys.stderr)
        index["pages"] = pages
        INDEX_PATH.write_text(
            json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        return 0

    try:
        raw_posts = fetch_posts(user, pwd, base)
    except Exception as e:                                   # noqa: BLE001
        print(f"Не удалось забрать посты ({type(e).__name__}: {e}) — "
              "индекс оставлен только из страниц.", file=sys.stderr)
        index["pages"] = pages
        INDEX_PATH.write_text(
            json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        return 0

    added = 0
    for p in raw_posts:
        title_html = (p.get("title") or {}).get("rendered", "")
        title = strip_html(title_html)
        if not title:
            continue
        content = strip_html((p.get("content") or {}).get("rendered", ""))
        excerpt = strip_html((p.get("excerpt") or {}).get("rendered", ""))
        cats = p.get("categories") or []
        cat_names = [CATEGORY_NAMES.get(c, "") for c in cats]
        cat_names = [c for c in cat_names if c]
        pages.append({
            "id": p["id"],
            "type": "post",
            "kind": "blog",
            "slug": p.get("slug", ""),
            "url": p.get("link", ""),
            "title": title,
            "excerpt": excerpt,
            "content": content,
            "len": len(content),
            "date": (p.get("date") or "")[:10],
            "modified": (p.get("modified") or "")[:10],
            "categories": cats,
            "category_names": cat_names,
        })
        added += 1

    index["pages"] = pages
    index["blog_stats"] = {
        "posts": added,
        "total_documents": len(pages),
        "built_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
    }
    INDEX_PATH.write_text(
        json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    size_kb = INDEX_PATH.stat().st_size / 1024
    print(f"Страниц было: {before}")
    print(f"Постов добавлено: {added}")
    print(f"Всего документов: {len(pages)}")
    print(f"Индекс: {INDEX_PATH} ({size_kb:.1f} КБ)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
