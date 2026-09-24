# -*- coding: utf-8 -*-
"""Клиент AnyModel API для 2KAD-чатбота.

Стрит OpenAI-совместимый chat/completions.
"""
from __future__ import annotations

import json
import os
from typing import AsyncGenerator, Dict, List, Optional

import httpx


class AnyModelClient:
    BASE_URL = "https://anymodel.org/v1"
    DEFAULT_MODEL = "am/gpt-oss-20b"
    FALLBACK_MODEL = "am/nemotron-3.5-lightning-30b-a3b"

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or os.environ.get("ANYMODEL_API_KEY", "")
        if not self.api_key:
            raise ValueError(
                "ANYMODEL_API_KEY is required. "
                "Get one at https://anymodel.org and put it in .env or env var."
            )
        self.model = model or os.environ.get("ANYMODEL_MODEL", self.DEFAULT_MODEL)
        self.fallback_model = self.FALLBACK_MODEL

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def chat_stream(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        max_tokens: int = 600,
        temperature: float = 0.3,
        fallback: bool = True,
    ) -> AsyncGenerator[Dict, None]:
        """Стримит ответ. Yield'ит словари с ключами:
          - {"type": "content", "delta": "..."}
          - {"type": "reasoning", "delta": "..."}
          - {"type": "usage", "prompt_tokens": ..., "completion_tokens": ...}
          - {"type": "done"}
          - {"type": "error", "message": "..."}

        Реализация: делаем обычный POST (stream=true), ждём полный ответ,
        затем парсим SSE-чанки вручную и yield'им по очереди. Это надёжнее,
        чем aiter_text — последний иногда не закрывает соединение после [DONE].
        """
        url = f"{self.BASE_URL}/chat/completions"
        payload = {
            "model": model or self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
            # OpenAI-style: для reasoning-моделей просим короткий CoT
            # (по умолчанию GPT-OSS 20B пишет развёрнутое рассуждение,
            # которое при отсутствии reasoning_content канала протекает в content)
            "reasoning_effort": "low",
        }
        used_fallback = False
        overall_timeout_s = 180

        # Защита от «протекающего» CoT: некоторые провайдеры (особенно GPT-OSS 20B
        # через AnyModel free) не отдают reasoning_content, а суют развёрнутое
        # рассуждение прямо в content (вида "Here's a thinking process:\n1. ...").
        # До начала «делового» ответа такие префиксы отбрасываем.
        import re as _re
        _COT_PREFIX_RE = _re.compile(
            r"^(?:here'?s\s+(?:a\s+)?thinking\s+process[:：]?|"
            r"let'?s\s+think\s+step\s+by\s+step[:：]?|"
            r"анализ\s+запроса[:：]?|"
            r"разбор\s+запроса[:：]?|"
            r"размышление[:：]?|"
            r"thoughts?[:：]?|"
            r"reasoning[:：]?)\s*",
            _re.IGNORECASE,
        )
        # Состояние фильтра (per-stream): пока не нашли маркер ответа — копим.
        # Триггер для выхода из CoT:
        #   1) явно видим `**жирный**` (наш ответ всегда с bold по системному промпту)
        #   2) видим `\n\n` после `1.\n` или `2.\n` (буллет → пустая строка → ответ)
        #   3) длинный (>120) буфер с кириллицей и без нумерованных списков
        _state = {"in_cot": None, "buf": ""}

        try:
            async with httpx.AsyncClient(timeout=overall_timeout_s) as client:
                resp = await client.post(
                    url, headers=self._headers(), json=payload,
                )
                if resp.status_code != 200:
                    err_body = resp.text
                    if fallback and not used_fallback:
                        used_fallback = True
                        async for ev in self.chat_stream(
                            messages, model=self.fallback_model, fallback=False,
                        ):
                            yield ev
                        return
                    yield {
                        "type": "error",
                        "message": f"HTTP {resp.status_code}: {err_body[:500]}",
                    }
                    return

                # Читаем полный текст
                full_text = resp.text

                # Парсим SSE
                buffer = ""
                saw_done = False
                had_error = False
                for chunk in full_text.split("\n"):
                    line = chunk.strip()
                    if not line.startswith("data: "):
                        continue
                    data = line[6:].strip()
                    if data == "[DONE]":
                        saw_done = True
                        yield {"type": "done"}
                        break
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if "error" in obj:
                        err = obj["error"]
                        msg = (
                            err.get("message", "unknown")
                            if isinstance(err, dict)
                            else str(err)
                        )
                        had_error = True
                        if fallback and not used_fallback:
                            used_fallback = True
                            async for ev in self.chat_stream(
                                messages, model=self.fallback_model,
                                fallback=False,
                            ):
                                yield ev
                            return
                        yield {"type": "error", "message": msg}
                        break
                    for ch in obj.get("choices", []):
                        delta = ch.get("delta") or {}
                        if delta.get("reasoning_content"):
                            yield {"type": "reasoning", "delta": delta["reasoning_content"]}
                        if delta.get("content"):
                            piece = delta["content"]
                            if _state["in_cot"] is None:
                                # Первый контент — определяем, это CoT или сразу ответ
                                if _COT_PREFIX_RE.match(piece):
                                    _state["in_cot"] = True
                                    _state["buf"] = _COT_PREFIX_RE.sub(
                                        "", piece, count=1
                                    )
                                    continue
                                _state["in_cot"] = False
                                yield {"type": "content", "delta": piece}
                                continue
                            if _state["in_cot"]:
                                _state["buf"] += piece
                                # Жёсткие маркеры начала ответа
                                if (
                                    "**" in _state["buf"]
                                    or "\n\n" in _state["buf"]
                                    or len(_state["buf"]) > 120
                                    and "\n1." not in _state["buf"]
                                    and "\n2." not in _state["buf"]
                                    and "\n3." not in _state["buf"]
                                ):
                                    answer = _state["buf"].lstrip()
                                    _state["in_cot"] = False
                                    _state["buf"] = ""
                                    if answer:
                                        yield {"type": "content", "delta": answer}
                                continue
                            yield {"type": "content", "delta": piece}
                    if "usage" in obj and obj["usage"]:
                        u = obj["usage"] or {}
                        yield {
                            "type": "usage",
                            "prompt_tokens": u.get("prompt_tokens") or 0,
                            "completion_tokens": u.get("completion_tokens") or 0,
                        }

                # Если стрим закончился, но мы так и в CoT — отдать хвост как content
                # (лучше утечка CoT, чем полностью пустой ответ)
                if _state["in_cot"] is True and _state["buf"].strip():
                    tail = _state["buf"].strip()
                    yield {"type": "content", "delta": tail}
                    _state["in_cot"] = False

                if not saw_done and not had_error:
                    yield {"type": "error", "message": "stream ended without [DONE]"}
        except httpx.HTTPError as e:
            if fallback and not used_fallback:
                used_fallback = True
                async for ev in self.chat_stream(
                    messages, model=self.fallback_model, fallback=False,
                ):
                    yield ev
                return
            yield {"type": "error", "message": f"HTTP error: {e}"}

    async def chat_complete(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        max_tokens: int = 600,
        temperature: float = 0.3,
    ) -> str:
        """Не-стрим вариант — собирает полный ответ в строку."""
        content = ""
        last_error: Optional[str] = None
        async for ev in self.chat_stream(
            messages, model=model,
            max_tokens=max_tokens, temperature=temperature,
        ):
            if ev["type"] == "content":
                content += ev["delta"]
            elif ev["type"] == "error":
                last_error = ev["message"]
        if not content.strip():
            msg = last_error or "model returned empty response (upstream timeout?)"
            raise RuntimeError(msg)
        return content.strip()