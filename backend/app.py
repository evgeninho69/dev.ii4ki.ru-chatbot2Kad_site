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
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
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
    return {
        "status": "ok",
        "time": int(time.time()),
        "index_loaded": _site_index is not None,
    }


@app.get("/api/index-stats")
async def index_stats():
    idx = get_index()
    return {
        "pages": len(idx.pages),
        "site": idx.data.get("site"),
        "company": idx.data.get("company"),
    }


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
            }, ensure_ascii=False).encode() + b"\n"

            try:
                async for ev in llm.chat_stream(messages):
                    if ev["type"] == "content":
                        full += ev["delta"]
                        yield json.dumps({
                            "type": "content",
                            "delta": ev["delta"],
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
                        yield json.dumps({
                            "type": "error",
                            "message": ev["message"],
                        }, ensure_ascii=False).encode() + b"\n"
                        return
                    elif ev["type"] == "done":
                        break

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


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8765))
    log.info(f"Starting 2KAD chatbot on port {port}")
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)