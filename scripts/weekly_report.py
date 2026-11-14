# -*- coding: utf-8 -*-
"""Еженедельный отчёт по вопросам пользователей ассистента 2КАД.

Что делает:
  1) читает внутренний журнал вопросов с хостинга 2kad.ru
     (REST /wp-json/skp-ai/v1/questions, доступ — Application Password);
  2) группирует вопросы по темам, отделяет пробелы (вопросы без
     найденного материала) от закрытых тем;
  3) просит модель предложить статьи блога и сервисы для раздела
     «Полезное» под реальные вопросы пользователей;
  4) сохраняет отчёт файлом в reports/ и печатает короткую сводку
     в stdout — её можно отправить владельцу в чат.

Запуск:
    py.exe -3 scripts\\weekly_report.py                 # за неделю
    py.exe -3 scripts\\weekly_report.py --days 30       # за месяц
    py.exe -3 scripts\\weekly_report.py --dry-run       # без записи файла

Ключи читаются из локальных .env (в репозиторий не попадают):
  - хостинг  : C:\\Users\\ZotkinEA\\.minimax\\plugins\\hosting-2kad\\.env
  - модель   : <корень проекта>\\.env
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
HOSTING_ENV = Path(
    r"C:\Users\ZotkinEA\.minimax\plugins\hosting-2kad\.env"
)
PROJECT_ENV = ROOT / ".env"
REPORTS_DIR = ROOT / "reports"

# Темы, по которым вопросы считаются «закрытыми» материалом 2КАД.
ANSWERED_MODES = {"pricing", "service", "article", "law", "tool"}


# ────────────────────────────────────────────────────────────────────────────
# Вспомогательные функции
# ────────────────────────────────────────────────────────────────────────────

def load_env(path: Path) -> Dict[str, str]:
    """Читает .env в dict. Значения не печатаются."""
    out: Dict[str, str] = {}
    if not path.exists():
        return out
    with io.open(path, "r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def load_llm():
    """Подключает модель из backend/llm.py с ключами из .env проекта."""
    for k, v in load_env(PROJECT_ENV).items():
        os.environ.setdefault(k, v)
    sys.path.insert(0, str(BACKEND))
    from llm import AnyModelClient  # noqa: E402
    return AnyModelClient()


def fetch_questions(days: int) -> List[Dict[str, Any]]:
    """Читает журнал вопросов с хостинга 2kad.ru."""
    env = load_env(HOSTING_ENV)
    base = (env.get("WP_BASE_URL") or "https://2kad.ru").rstrip("/")
    user = env.get("WP_USER") or ""
    password = env.get("WP_APP_PASSWORD") or ""
    if not user or not password:
        raise RuntimeError(
            "Нет учётных данных WordPress в "
            f"{HOSTING_ENV} (WP_USER / WP_APP_PASSWORD)"
        )

    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    url = (
        f"{base}/wp-json/skp-ai/v1/questions"
        f"?since={urllib.parse.quote(since)}&limit=2000"
    )
    token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Basic {token}",
            "User-Agent": "2kad-weekly-report/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("items") or []


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def is_gap(row: Dict[str, Any]) -> bool:
    """Вопрос, на который не нашлось материала 2КАД."""
    mode = (row.get("mode") or "").strip()
    if mode in ANSWERED_MODES:
        return False
    # Пустой режим = метаданные ответа не пришли — тоже считаем пробелом,
    # но слабее: такие вопросы группируются отдельно в «нет данных».
    return True


def shorten(text: str, limit: int = 110) -> str:
    t = norm(text)
    if len(t) <= limit:
        return t
    cut = t[:limit]
    i = cut.rfind(" ")
    return (cut[:i] if i > limit * 0.6 else cut) + "…"


# ────────────────────────────────────────────────────────────────────────────
# Группировка тем
# ────────────────────────────────────────────────────────────────────────────

# Опорные темы: маркеры заданы ОСНОВАМИ слов, а не целыми словами —
# вопросы приходят в любом падеже («технический план», «технического
# плана», «техплана»). Темы стоят по убыванию специфичности: при равном
# числе совпадений побеждает тема, которая выше.
THEME_RULES = (
    ("затопление и зоны", (
        "затоп", "подтоп", "наводн", "пойм", "зон", "пзз",
        "вид использования", "уит", "ограничен",
    )),
    ("кадастровая стоимость", (
        "кадастров", "стоимость земл", "кс ", "оценк", "рыночн",
        "налог", "оспорить", "пересмотр",
    )),
    ("техплан и объекты", (
        "техплан", "техническ", "здани", "квартир", "помещени",
        "мкд", "этаж", "ввод в эксплуатац", "перепланиров",
        "реконструкц", "объект капитального", "нежил",
    )),
    ("лес и земля", (
        "лес", "лесн", "аренд", "земл", "схема", "карта", "сервитут",
    )),
    ("межевание и границы", (
        "межев", "границ", "участок", "координат", "поворотн", "контур",
        "выписк", "объедин", "перераспредел", "red", "межевой план",
    )),
    ("законодательство", (
        "приказ", "закон", "кодекс", "норматив", "росреестр", "егрн",
        "регистрац", "приостанов", "суд", "практик", "218-фз",
        "измен", "требован",
    )),
    ("цена и сроки", (
        "сколько стоит", "стоимост", "цена", "цены", "смет", "прайс",
        "тариф", "бюджет", "расценк", "срок", "длительност",
    )),
    ("проектирование и изыскания", (
        "проект", "изыскан", "геолог", "геодези", "топограф", "обмер",
    )),
)


def theme_of(text: str) -> str:
    """Тема вопроса.

    Считаем, сколько маркеров темы попало в вопрос, и берём тему с
    наибольшим числом совпадений. Так «участок в зоне затопления»
    уходит в «затопление и зоны» (затоп + зона), а не в «межевание»,
    где маркером служит слово «участок». При равенстве побеждает тема,
    которая в THEME_RULES стоит раньше.
    """
    q = (text or "").lower()
    best = ""
    best_score = 0
    for theme, markers in THEME_RULES:
        score = 0
        for m in markers:
            if m in q:
                score += 1
        if score > best_score:
            best_score = score
            best = theme
    return best or "прочее"


def group_questions(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Раскладывает вопросы по темам, отделяя пробелы."""
    themes: Dict[str, List[str]] = {}
    gaps: List[str] = []
    modes = Counter()
    unanswered_no_mode = 0

    for row in rows:
        q = norm(row.get("q") or "")
        if not q:
            continue
        mode = (row.get("mode") or "").strip()
        modes[mode or "нет данных"] += 1
        if is_gap(row):
            gaps.append(q)
            if not mode:
                unanswered_no_mode += 1
            continue
        themes.setdefault(theme_of(q), []).append(q)

    return {
        "themes": themes,
        "gaps": gaps,
        "modes": dict(modes),
        "gaps_without_mode": unanswered_no_mode,
    }


# ────────────────────────────────────────────────────────────────────────────
# Модель: статьи блога и сервисы «Полезного»
# ────────────────────────────────────────────────────────────────────────────

REPORT_PROMPT = """\
Ты — редактор блога кадастрового инженера ООО «Центр Недвижимости 2кад»
(Тверь, работают с 2013 года). По реальным вопросам пользователей нужно
подготовить контент-план.

ЗАКРЫТЫЕ ТЕМЫ (на них ассистент ответил, материалы 2КАД есть):
{closed}

ПРОБЕЛЫ (вопросы без найденного материала — их стоит закрыть):
{gaps}

РЕЖИСТР ОТВЕТА: blog, article — статья для блога 2kad.ru; service — сервис
для раздела «Полезное» на сайте.

ЗАДАЧА. На основе ТОЛЬКО этих вопросов предложи:
1) до 6 статей для блога — каждая: рабочее название (как заголовок статьи на
   сайте 2kad.ru), какой вопрос закрывает (кратко), и 2-3 ключевых подпункта;
2) до 4 сервисов для раздела «Полезное» — каждый: название, что делает для
   пользователя, на какие данные опирается (ЕГРН/НСПД/ПЗЗ/ЗОУИТ/приказы),
   и какой пробел закрывает.

ТРЕБОВАНИЯ:
- Только русский язык, деловой тон, без воды и без обещаний.
- Никаких выдуманных цен, сроков, номеров актов и площадей.
- Каждая идея — привязана к конкретному вопросу из списка выше.
- Не повторяй темы, которые уже закрыты материалами 2КАД.
- Ответ строго в JSON без пояснений:
{{"articles": [{{"title": "...", "question": "...", "points": ["...", "..."]}}],
  "services": [{{"name": "...", "what": "...", "data": "...", "gap": "..."}}]}}
"""


async def ask_model(llm, prompt: str) -> Dict[str, Any]:
    raw = await llm.chat_complete(
        [{"role": "user", "content": prompt}],
        max_tokens=3000,
        temperature=0.35,
    )
    txt = (raw or "").strip()
    # Модель могла обернуть JSON в блок кода — снимаем обёртку.
    txt = re.sub(r"^```(?:json)?\s*|\s*```$", "", txt, flags=re.MULTILINE)
    start = txt.find("{")
    end = txt.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return {"articles": [], "services": []}
    try:
        data = json.loads(txt[start:end + 1])
    except json.JSONDecodeError:
        try:
            data = json.loads(txt[start:end + 1].replace(",\n}", "\n}"))
        except json.JSONDecodeError:
            return {"articles": [], "services": []}
    if not isinstance(data, dict):
        return {"articles": [], "services": []}
    data.setdefault("articles", [])
    data.setdefault("services", [])
    return data


# ────────────────────────────────────────────────────────────────────────────
# Отчёт
# ────────────────────────────────────────────────────────────────────────────

def build_report(days: int, rows: List[Dict[str, Any]],
                 grouped: Dict[str, Any], ideas: Dict[str, Any]) -> str:
    now = datetime.now()
    period = (now - timedelta(days=days)).strftime("%d.%m.%Y")
    themes = grouped["themes"]
    gaps = grouped["gaps"]

    lines: List[str] = []
    lines.append(f"# Отчёт по вопросам пользователей ассистента 2КАД")
    lines.append("")
    lines.append(f"**Период:** {period} — {now.strftime('%d.%m.%Y')} ({days} дн.)  ")
    lines.append(f"**Всего вопросов:** {len(rows)}  ")
    lines.append(f"**Закрытых тем:** {sum(len(v) for v in themes.values())}  ")
    lines.append(f"**Пробелов (нет материала):** {len(gaps)}")
    lines.append("")
    lines.append(f"_Сформировано автоматически, {now.strftime('%d.%m.%Y %H:%M')}._")
    lines.append("")

    lines.append("## Темы вопросов")
    lines.append("")
    if themes:
        ordered = sorted(themes.items(), key=lambda kv: -len(kv[1]))
        for theme, items in ordered:
            lines.append(f"### {theme} — {len(items)}")
            lines.append("")
            for q in items[:6]:
                lines.append(f"- {shorten(q, 150)}")
            if len(items) > 6:
                lines.append(f"- …ещё {len(items) - 6}")
            lines.append("")
    else:
        lines.append("_Вопросов в периоде не зафиксировано._")
        lines.append("")

    lines.append("## Пробелы в базе знаний")
    lines.append("")
    if gaps:
        lines.append("Вопросы, на которые ассистент не нашёл материала 2КАД:")
        lines.append("")
        for q in gaps[:25]:
            lines.append(f"- {shorten(q, 150)}")
        if len(gaps) > 25:
            lines.append(f"- …ещё {len(gaps) - 25}")
    else:
        lines.append("_Пробелов нет — на все вопросы найден материал._")
    lines.append("")

    articles = ideas.get("articles") or []
    services = ideas.get("services") or []
    lines.append("## Варианты статей для блога")
    lines.append("")
    if articles:
        for i, a in enumerate(articles, 1):
            title = norm(str(a.get("title") or "")).strip()
            if not title:
                continue
            lines.append(f"**{i}. {title}**")
            lines.append("")
            q = norm(str(a.get("question") or "")).strip()
            if q:
                lines.append(f"- Закрывает вопрос: {shorten(q, 160)}")
            for p in (a.get("points") or [])[:4]:
                lines.append(f"- {norm(str(p))}")
            lines.append("")
    else:
        lines.append("_Модель не предложила статьи (мало данных за период)._")
        lines.append("")

    lines.append("## Сервисы для раздела «Полезное»")
    lines.append("")
    if services:
        for i, s in enumerate(services, 1):
            name = norm(str(s.get("name") or "")).strip()
            if not name:
                continue
            lines.append(f"**{i}. {name}**")
            lines.append("")
            for key, label in (("what", "Что делает"),
                               ("data", "На каких данных"),
                               ("gap", "Какой пробел закрывает")):
                v = norm(str(s.get(key) or "")).strip()
                if v:
                    lines.append(f"- {label}: {shorten(v, 200)}")
            lines.append("")
    else:
        lines.append("_Модель не предложила сервисы (мало данных за период)._")
        lines.append("")

    lines.append("## Режимы ответов")
    lines.append("")
    for mode, cnt in sorted(grouped["modes"].items(), key=lambda kv: -kv[1]):
        lines.append(f"- `{mode}` — {cnt}")
    lines.append("")

    return "\n".join(lines)


def build_summary(days: int, rows: List[Dict[str, Any]],
                  grouped: Dict[str, Any], ideas: Dict[str, Any],
                  report_path: Path) -> str:
    """Короткая сводка в чат (без длинных перечислений)."""
    themes = grouped["themes"]
    gaps = grouped["gaps"]
    parts: List[str] = []
    parts.append(f"**Отчёт по вопросам ассистента 2КАД** — {days} дн.")
    parts.append("")
    parts.append(f"Вопросов: {len(rows)} · закрытых тем: {sum(len(v) for v in themes.values())} · пробелов: {len(gaps)}")
    parts.append("")
    if themes:
        top = sorted(themes.items(), key=lambda kv: -len(kv[1]))[:5]
        parts.append("Темы: " + " · ".join(f"{t} ({len(v)})" for t, v in top))
        parts.append("")
    articles = [norm(str(a.get("title") or "")).strip()
                for a in (ideas.get("articles") or [])]
    articles = [a for a in articles if a][:6]
    if articles:
        parts.append("Статьи блога:")
        parts.extend(f"- {a}" for a in articles)
        parts.append("")
    services = [norm(str(s.get("name") or "")).strip()
                for s in (ideas.get("services") or [])]
    services = [s for s in services if s][:4]
    if services:
        parts.append("Сервисы для «Полезного»:")
        parts.extend(f"- {s}" for s in services)
        parts.append("")
    if gaps:
        parts.append(f"Пробелы (пример): " + "; ".join(
            shorten(g, 70) for g in gaps[:3]))
        parts.append("")
    parts.append(f"Полный отчёт: {report_path}")
    return "\n".join(parts)


# ────────────────────────────────────────────────────────────────────────────
# main
# ────────────────────────────────────────────────────────────────────────────

async def run(args) -> int:
    print(f"Читаю журнал вопросов за {args.days} дн. …", flush=True)
    rows = fetch_questions(args.days)
    print(f"  записей: {len(rows)}", flush=True)

    grouped = group_questions(rows)

    closed_txt = "\n".join(
        f"- [{theme}] {shorten(q, 120)}"
        for theme, items in grouped["themes"].items()
        for q in items[:25]
    ) or "(нет)"
    gaps_txt = "\n".join(f"- {shorten(q, 120)}" for q in grouped["gaps"][:40]) or "(нет)"

    ideas: Dict[str, Any] = {"articles": [], "services": []}
    try:
        llm = load_llm()
        print("Запрашиваю идеи у модели…", flush=True)
        ideas = await ask_model(llm, REPORT_PROMPT.format(
            closed=closed_txt, gaps=gaps_txt))
        print(f"  статей: {len(ideas.get('articles') or [])}, "
              f"сервисов: {len(ideas.get('services') or [])}", flush=True)
    except Exception as e:
        print(f"  модель недоступна ({e}) — отчёт без идей", flush=True)

    now = datetime.now()
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS_DIR / f"weekly-{now.strftime('%Y-%m-%d')}.md"
    report = build_report(args.days, rows, grouped, ideas)

    if args.dry_run:
        print("\n" + "=" * 70)
        print(report)
        print("=" * 70)
        print("dry-run: файл не записан")
        return 0

    report_path.write_text(report, encoding="utf-8")
    print(f"Отчёт записан: {report_path}", flush=True)
    print("-" * 70)
    print(build_summary(args.days, rows, grouped, ideas, report_path))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Еженедельный отчёт 2КАД")
    ap.add_argument("--days", type=int, default=7, help="период в днях (по умолчанию 7)")
    ap.add_argument("--dry-run", action="store_true", help="печатает отчёт, не сохраняет")
    args = ap.parse_args()
    import asyncio
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
