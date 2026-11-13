# -*- coding: utf-8 -*-
"""FastAPI-приложение 2KAD-чатбота.

Эндпоинты:
- POST /api/chat  — отправить сообщение, получить ответ (стримом или JSON)
- GET  /api/health — health-check
- GET  /api/index-stats — статистика индекса
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from llm import AnyModelClient
from router import QueryRouter
from search import SiteIndex

# ---------- logging ----------
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("2kad-chatbot")

# ---------- paths ----------
BASE = Path(__file__).parent
DATA_DIR = BASE / "data"
INDEX_PATH = DATA_DIR / "site_index.json"

# ---------- in-memory state ----------
HISTORY_MAX = 200  # последних N диалогов в памяти
SESSIONS: Dict[str, deque] = {}
LOGS: deque = deque(maxlen=500)  # последние логи для /api/logs


def push_log(record: dict):
    record["ts"] = int(time.time())
    LOGS.append(record)


# ---------- singletons ----------
_site_index: Optional[SiteIndex] = None
_router: Optional[QueryRouter] = None
_llm: Optional[AnyModelClient] = None


def get_index() -> SiteIndex:
    global _site_index
    if _site_index is None:
        log.info(f"Loading site index from {INDEX_PATH}")
        _site_index = SiteIndex(INDEX_PATH)
    return _site_index


def get_router() -> QueryRouter:
    global _router
    if _router is None:
        _router = QueryRouter(get_index())
    return _router


def get_llm() -> AnyModelClient:
    global _llm
    if _llm is None:
        _llm = AnyModelClient()
    return _llm


def llm_status() -> Dict:
    """Активный провайдер LLM и цепочка запасных — для /api/health.

    Health не должен падать ни при каких настройках: недостающие атрибуты
    клиента читаются через getattr. Иначе 500 на /api/health роняет
    docker-healthcheck, контейнер уходит в рестарт-цикл, а Traefik
    начинает отдавать 502.
    """
    try:
        client = get_llm()
    except Exception as e:  # ключи не заданы — health не должен падать
        return {"configured": False, "error": str(e)[:200]}
    try:
        chain = [
            f"{t.get('name', '?')}:{t.get('model', '?')}"
            for t in (getattr(client, "chain", None) or [])
            if isinstance(t, dict)
        ]
        provider = getattr(client, "provider", None)
        if not provider and chain:
            provider = chain[0].split(":")[0]
        model = getattr(client, "model", None)
        if not model and chain:
            model = chain[0].split(":", 1)[-1]
        return {
            "configured": True,
            "provider": provider,
            "model": model,
            "chain": chain,
        }
    except Exception as e:
        return {"configured": True, "error": str(e)[:200]}


def grounded_answer(question: str, decision, phone: str) -> str:
    """Гарантированный ответ по найденному материалу — без LLM.

    Используется, когда провайдер LLM завис или недоступен: выдержки
    берутся ДОСЛОВНО из найденного материала, поэтому ответ остаётся
    фактическим, проверяемым и появляется мгновенно. Финальная ссылка
    всегда ведёт на материал 2КАД (услугу или статью).
    """
    idx = get_index()
    doc = decision.page or {}
    main_url = idx.data.get("main_url", "https://2kad.ru/")

    link_title = decision.link_title or "материал 2КАД"
    link_url = decision.link_url or main_url
    tail = f"\n\nПодробнее: [{link_title}]({link_url})"

    # ---------- свободный ответ ----------
    if decision.mode == "general":
        return (
            "Уточните, пожалуйста, детали — какой объект и что именно с ним "
            "произошло. Тогда подскажу порядок действий по вашей ситуации. "
            f"По кадастровым вопросам Твери и области: {phone}."
        )

    # ---------- инструмент ----------
    if decision.mode == "tool":
        body = doc.get("excerpt") or doc.get("content") or ""
        body += ("\n\nЧто потребуется: кадастровый номер или адрес объекта. "
                 f"Если нужна консультация — позвоните {phone}.")
        return body.strip() + tail

    # ---------- цена ----------
    if decision.mode == "pricing":
        calc_url = idx.data.get("calculator_url", "https://2kad.ru/kalkulyator/")
        row = idx.price_match(question)
        body = ""
        if row:
            body = (
                f"**{row['work']}** — {row['price']}, срок {row['term']}. "
                "Это минимальная планка по составу работ: итоговая стоимость "
                "зависит от площади, объёма и состава документов, а также от "
                "надбавки за выезд.\n\n"
            )
        else:
            body = (
                "Стоимость зависит от вида работ, площади объекта, расстояния от Твери "
                "и состояния документов — точный расчёт делают после изучения объекта.\n\n"
            )
        body += (
            f"Посчитайте предварительную стоимость в калькуляторе: {calc_url}\n"
            "Точная стоимость определяется после изучения документов на объект и "
            f"согласования объёма работ. Перезвоним в течение рабочего дня. "
            f"Менеджер: {phone}."
        )
        return body + tail

    # ---------- закон: практика 2КАД + сам акт ----------
    if decision.mode == "law":
        related = decision.link_doc
        passages = ""
        if related:
            passages = idx.best_passages(question, related, max_sentences=4,
                                         max_chars=900)
        act = decision.law_doc or {}
        act_title = act.get("title") or ""
        act_url = act.get("url") or ""
        act_date = act.get("date") or ""
        if act_title:
            act_line = f"Нормативный акт: {act_title}"
            if act_date:
                act_line += f" от {act_date}"
            if act_url:
                act_line = f"Нормативный акт: [{act_title}]({act_url})"
                if act_date:
                    act_line += f" от {act_date}"
            act_line += "."
            passages = (passages + " " + act_line).strip() if passages else act_line
        if not passages:
            title = doc.get("title") or "материал"
            passages = (
                f"По теме «{title}» подробная информация собрана в материале "
                "на сайте."
            )
        return passages + tail

    # ---------- статья или услуга ----------
    passages = idx.best_passages(question, doc, max_sentences=4, max_chars=900)
    if not passages:
        title = doc.get("title") or "материал"
        passages = (
            f"По теме «{title}» подробная информация собрана в материале "
            "на сайте."
        )
        passages += f"\n\nЕсли нужна консультация — позвоните {phone}."
    return passages + tail


# Финальная строка «Подробнее: [название](ссылка)» — её формирует система.
LINK_LINE_RE = re.compile(
    r"^[ \t]*(?:\*\*)?[ \t]*Подробнее\b[^\n]*$",
    re.IGNORECASE | re.MULTILINE,
)

# «Подробнее: [название](ссылка)» в середине строки.
LINK_INLINE_RE = re.compile(
    r"(?:\*\*)?Подробнее\s*:\s*\[[^\]]*\]\([^)\s]*\)(?:\*\*)?",
    re.IGNORECASE,
)

# Markdown-ссылка: [текст](адрес)
MD_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)\s]+)\)")


def strip_link_lines(text: str) -> str:
    """Убирает строки «Подробнее: …» — финальную ссылку добавляет система."""
    if not text:
        return ""
    return LINK_LINE_RE.sub("", text)


def is_link_line(line: str) -> bool:
    """Строка целиком про финальную ссылку — такую пишет система, не модель."""
    return bool(re.match(
        r"^[ \t]*(?:\*\*)?[ \t]*Подробнее\b", line.strip(), re.IGNORECASE))


def strip_hallucinated_links(text: str) -> str:
    """Убирает markdown-ссылки на 2kad.ru, которых нет в базе знаний.

    Модель иногда сочиняет правдоподобный адрес — например
    «/blog/obzor-izmeneniy-za-3-kvartal-2026» вместо реальной ссылки на
    материал. Такой ссылке в ответе не место: оставляем текст, убираем адрес.
    """
    if not text or "](" not in text:
        return text
    try:
        known = get_index().known_urls()
    except Exception:
        return text

    def repl(m):
        label, url = m.group(1), m.group(2)
        if "2kad.ru" not in url:
            return m.group(0)
        if url.rstrip("/") in known:
            return m.group(0)
        return label

    return MD_LINK_RE.sub(repl, text)


def clean_chunk(text: str) -> str:
    """Текст для вывода клиенту: без «Подробнее: …» и без выдуманных ссылок."""
    if not text:
        return ""
    return strip_hallucinated_links(LINK_INLINE_RE.sub("", text))


def ensure_link(text: str, decision) -> str:
    """Гарантирует, что ответ заканчивается ссылкой на материал 2КАД.

    Финальную строку формирует система, а не LLM: модель регулярно брала
    название материала из одного документа и адрес из другого (например,
    «Подробнее: [С 1 января 2026…](https://2kad.ru/blog/)»). Поэтому все
    строки «Подробнее: …» из ответа удаляются, а в конец добавляется ровно
    одна ссылка — та, что выбрал роутер.
    """
    body = strip_hallucinated_links(strip_link_lines(text or "").strip())
    if not decision.link_url:
        # Материала нет — ссылку не добавляем и не оставляем «Подробнее: …»
        # от модели: подставлять случайный материал нельзя.
        return body
    title = decision.link_title or "материал 2КАД"
    link = f"Подробнее: [{title}]({decision.link_url})"
    if not body:
        return link
    return f"{body}\n\n{link}"


# ---------- FastAPI ----------
app = FastAPI(
    title="2KAD Chatbot API",
    description="Виртуальный ассистент сайта 2kad.ru",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://2kad.ru",
        "https://www.2kad.ru",
        "http://localhost:8080",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["*"],
)


# ---------- models ----------
class ChatMessage(BaseModel):
    role: str = Field(..., pattern="^(user|assistant|system)$")
    content: str


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    stream: bool = True


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    mode: str
    page_url: Optional[str] = None
    page_title: Optional[str] = None
    usage: Dict[str, int] = {}


# ---------- helpers ----------
def get_or_create_session(session_id: Optional[str]) -> str:
    if session_id and session_id in SESSIONS:
        return session_id
    new_id = session_id or uuid.uuid4().hex[:16]
    SESSIONS[new_id] = deque(maxlen=20)  # последние 20 сообщений
    return new_id


# ---------- endpoints ----------
@app.get("/api/health")
async def health():
    """Health-check контейнера. Всегда 200 — иначе docker/swarm роняет
    реплику, а домен отдаёт 502."""
    try:
        return {
            "status": "ok",
            "time": int(time.time()),
            "index_loaded": _site_index is not None,
            "llm": llm_status(),
        }
    except Exception as e:  # pragma: no cover — health не должен падать
        log.exception("health check failed")
        return {
            "status": "degraded",
            "time": int(time.time()),
            "error": str(e)[:200],
        }


@app.get("/api/index-stats")
async def index_stats():
    idx = get_index()
    return {
        "pages": len(idx.pages),
        "site": idx.data.get("site"),
        "company": idx.data.get("company"),
        **idx.stats(),
    }


@app.get("/api/recent-posts")
async def recent_posts(limit: int = 6):
    """Свежие материалы блога — для врезки «Свежие материалы» на странице
    ИИ-ассистента. Отдаём реальные заголовки, рубрики, даты и ссылки."""
    idx = get_index()
    n = max(1, min(limit, 12))
    items = []
    for p in idx.recent_posts(n):
        items.append({
            "id": p.get("id"),
            "title": p.get("title", ""),
            "url": p.get("url", ""),
            "date": p.get("date", ""),
            "rubric": (p.get("category_names") or ["Блог"])[0],
            "excerpt": (p.get("excerpt") or "")[:220],
        })
    return {"items": items, "total_posts": idx.stats().get("posts", 0)}


@app.get("/api/suggestions")
async def suggestions(limit: int = 8):
    """Темы для быстрых вопросов — берём из заголовков блога,
    чтобы подсказки совпадали с реальным содержимым базы знаний."""
    idx = get_index()
    n = max(1, min(limit, 12))
    out = []
    seen = set()
    for p in idx.recent_posts(40):
        t = (p.get("title") or "").strip()
        if not t or t in seen:
            continue
        seen.add(t)
        out.append({"title": t, "url": p.get("url", "")})
        if len(out) >= n:
            break
    return {"items": out}


@app.post("/api/chat")
async def chat(req: ChatRequest):
    if not req.message.strip():
        raise HTTPException(400, "empty message")
    sid = get_or_create_session(req.session_id)
    history = SESSIONS[sid]
    router = get_router()
    decision = router.route(req.message)

    log.info(
        f"[{sid[:8]}] mode={decision.mode} "
        f"page={decision.found_title!r} url={decision.found_url}"
    )

    # Собираем сообщения для LLM: system + история + user
    messages: List[Dict[str, str]] = [
        {"role": "system", "content": decision.system_prompt}
    ]
    for h in history:
        messages.append(h)
    messages.append({"role": "user", "content": req.message})

    llm = get_llm()

    if req.stream:
        async def stream_resp() -> AsyncGenerator[bytes, None]:
            full = ""
            usage = {}
            yield json.dumps({
                "type": "meta",
                "session_id": sid,
                "mode": decision.mode,
                "page_url": decision.page["url"] if decision.page else None,
                "page_title": decision.page["title"] if decision.page else None,
                "source_kind": decision.source_kind or None,
                "source_rubric": (
                    (decision.page.get("category_names") or [None])[0]
                    if decision.page and decision.page.get("category_names")
                    else None
                ),
                "source_date": (
                    decision.page.get("date") if decision.page else None
                ),
                "link_url": decision.link_url or None,
                "link_title": decision.link_title or None,
            }, ensure_ascii=False).encode() + b"\n"

            try:
                # Отдаём текст построчно: так строка «Подробнее: …», которую
                # написала модель, всегда видна целиком и может быть вычищена.
                # Раньше вычищался только хвост потока — и модельная ссылка
                # в начале ответа доходила до пользователя.
                cur = ""
                async for ev in llm.chat_stream(messages):
                    if ev["type"] == "content":
                        delta = ev["delta"]
                        full += ev["delta"]
                        cur += delta
                        while "\n" in cur:
                            line, cur = cur.split("\n", 1)
                            if is_link_line(line):
                                continue
                            out = clean_chunk(line + "\n")
                            if out:
                                yield json.dumps({
                                    "type": "content",
                                    "delta": out,
                                }, ensure_ascii=False).encode() + b"\n"
                        if len(cur) > 3000:
                            # Совсем длинная строка без переноса — отдаём.
                            out = clean_chunk(cur)
                            cur = ""
                            if out:
                                yield json.dumps({
                                    "type": "content",
                                    "delta": out,
                                }, ensure_ascii=False).encode() + b"\n"
                    elif ev["type"] == "reasoning":
                        yield json.dumps({
                            "type": "reasoning",
                            "delta": ev["delta"],
                        }, ensure_ascii=False).encode() + b"\n"
                    elif ev["type"] == "usage":
                        usage = {
                            "prompt_tokens": ev.get("prompt_tokens", 0),
                            "completion_tokens": ev.get("completion_tokens", 0),
                        }
                    elif ev["type"] == "error":
                        # LLM упал — но если роутер нашёл релевантную страницу,
                        # НЕ показываем пользователю техническую ошибку.
                        # Вместо этого выдаём fallback-ответ со ссылкой на страницу
                        # и призывом позвонить. Виджет обработает это как обычное
                        # сообщение.
                        log.warning(
                            f"LLM error for {sid[:8]} "
                            f"(mode={decision.mode}): {ev['message'][:200]}"
                        )
                        # Если уже был частичный ответ — сбрасываем его:
                        # пользователь не должен видеть обрывки + fallback вместе.
                        had_partial = bool(full.strip())
                        if had_partial:
                            yield json.dumps({
                                "type": "replace",
                                "text": "",
                            }, ensure_ascii=False).encode() + b"\n"
                        if decision.page:
                            phone = get_index().data.get("phone", "+7 (4822) 41-57-68")
                            # Ответ строится из самого материала (выдержки из
                            # найденной статьи/страницы) — фактично и без
                            # технических формулировок.
                            fallback_text = grounded_answer(
                                req.message, decision, phone
                            )
                            # Отдаём как обычный content-чанк + done,
                            # чтобы виджет отобразил это как нормальное сообщение.
                            yield json.dumps({
                                "type": "content",
                                "delta": fallback_text,
                            }, ensure_ascii=False).encode() + b"\n"
                            history.append({"role": "user", "content": req.message})
                            history.append({"role": "assistant", "content": fallback_text})
                            yield json.dumps({
                                "type": "done",
                                "usage": usage,
                            }, ensure_ascii=False).encode() + b"\n"
                            push_log({
                                "session_id": sid[:8],
                                "mode": decision.mode,
                                "q": req.message[:120],
                                "reply_len": len(fallback_text),
                                "usage": usage,
                                "page": decision.found_url,
                                "fallback": True,
                                "partial_before": had_partial,
                            })
                            return
                        # Материала нет — вместо технической ошибки даём
                        # понятный ответ с контактами. Ссылку на материал не
                        # добавляем: подбирать «хоть что-нибудь» нельзя.
                        phone = get_index().data.get("phone", "+7 (4822) 41-57-68")
                        main_url = get_index().data.get(
                            "main_url", "https://2kad.ru/"
                        )
                        if decision.mode == "general":
                            fallback_text = (
                                "Уточните, пожалуйста, детали — какой объект и "
                                "что именно с ним произошло. Тогда подскажу "
                                "порядок действий по вашей ситуации. "
                                f"Менеджер: {phone}."
                            )
                        else:
                            link_title = decision.link_title or "Кадастровые работы 2КАД"
                            link_url = decision.link_url or main_url
                            fallback_text = (
                                "Уточните, пожалуйста, детали — так я подберу нужный "
                                "материал. По кадастровым работам, проектированию и "
                                "инженерным изысканиям в Твери и области — "
                                f"{link_title}: {link_url}. "
                                f"Менеджер: {phone}.\n\n"
                                f"Подробнее: [{link_title}]({link_url})"
                            )
                        yield json.dumps({
                            "type": "content",
                            "delta": fallback_text,
                        }, ensure_ascii=False).encode() + b"\n"
                        history.append({"role": "user", "content": req.message})
                        history.append({"role": "assistant", "content": fallback_text})
                        yield json.dumps({
                            "type": "done",
                            "usage": usage,
                        }, ensure_ascii=False).encode() + b"\n"
                        push_log({
                            "session_id": sid[:8],
                            "mode": decision.mode,
                            "q": req.message[:120],
                            "reply_len": len(fallback_text),
                            "usage": usage,
                            "page": None,
                            "fallback": True,
                            "partial_before": had_partial,
                        })
                        return
                    elif ev["type"] == "done":
                        break

                # Последняя строка ответа (возможно неполная).
                if cur.strip() and not is_link_line(cur):
                    out = clean_chunk(cur)
                    if out:
                        yield json.dumps({
                            "type": "content",
                            "delta": out,
                        }, ensure_ascii=False).encode() + b"\n"

                # Финальную ссылку формирует система — ровно одна и правильная.
                link_line = ensure_link("", decision)
                if link_line:
                    yield json.dumps({
                        "type": "content",
                        "delta": "\n\n" + link_line,
                    }, ensure_ascii=False).encode() + b"\n"

                # Правило «ответ заканчивается ссылкой на материал 2КАД»
                # соблюдается даже если LLM про ссылку забыл.
                full = ensure_link(full, decision)

                # Сохраняем в историю
                history.append({"role": "user", "content": req.message})
                history.append({"role": "assistant", "content": full})

                yield json.dumps({
                    "type": "done",
                    "usage": usage,
                }, ensure_ascii=False).encode() + b"\n"

                push_log({
                    "session_id": sid[:8],
                    "mode": decision.mode,
                    "q": req.message[:120],
                    "reply_len": len(full),
                    "usage": usage,
                    "page": decision.found_url,
                })
            except Exception as e:
                log.exception("chat stream failed")
                yield json.dumps({
                    "type": "error",
                    "message": str(e)[:500],
                }, ensure_ascii=False).encode() + b"\n"

        return StreamingResponse(
            stream_resp(),
            media_type="application/x-ndjson",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    # Non-stream: сразу весь ответ
    try:
        reply = await llm.chat_complete(messages)
    except RuntimeError as e:
        log.error(f"chat_complete failed for {sid[:8]}: {e}")
        raise HTTPException(502, f"Upstream LLM error: {e}")
    reply = ensure_link(reply, decision)
    history.append({"role": "user", "content": req.message})
    history.append({"role": "assistant", "content": reply})
    push_log({
        "session_id": sid[:8],
        "mode": decision.mode,
        "q": req.message[:120],
        "reply_len": len(reply),
        "page": decision.found_url,
    })
    return ChatResponse(
        session_id=sid,
        reply=reply,
        mode=decision.mode,
        page_url=decision.page["url"] if decision.page else None,
        page_title=decision.page["title"] if decision.page else None,
    )


@app.get("/api/logs")
async def get_logs():
    return {"logs": list(LOGS)[-60:]}


# ---------- static (widget) ----------
STATIC_DIR = BASE.parent / "frontend"
if STATIC_DIR.exists():
    app.mount(
        "/widget",
        StaticFiles(directory=str(STATIC_DIR)),
        name="widget",
    )

    # Маршрут для страницы ИИ-ассистента на 2kad.ru (/ai-assistant/).
    # WP-страница с этим slug делает redirect сюда (или iframe).
    @app.get("/ai-assistant", response_class=HTMLResponse)
    async def ai_assistant_page():
        ai_path = STATIC_DIR / "ai-assistant.html"
        if ai_path.exists():
            return HTMLResponse(content=ai_path.read_text(encoding="utf-8"))
        return HTMLResponse(
            content="<h1>AI Assistant page not found</h1>",
            status_code=404,
        )

    # Дополнительный алиас — отдаём по / и /ai
    @app.get("/ai-page", response_class=HTMLResponse)
    async def ai_page_alias():
        return await ai_assistant_page()


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8765))
    log.info(f"Starting 2KAD chatbot on port {port}")
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)