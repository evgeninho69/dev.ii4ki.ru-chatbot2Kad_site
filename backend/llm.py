# -*- coding: utf-8 -*-
"""Клиент Mistral AI API для 2KAD-чатбота.

Использует OpenAI-совместимый endpoint: https://api.mistral.ai/v1/chat/completions.

Переменные окружения:
- MISTRAL_API_KEY  — обязательно
- MISTRAL_MODEL    — модель (по умолчанию ministral-8b-latest)

Известные ограничения по тиру (проверено 2026-09-25):
- ✓ ministral-8b-latest, ministral-3b-latest, open-mistral-7b, mistral-tiny
- ✗ mistral-small-latest, mistral-large-latest → 403 «not in tier»
- ⚠️ mistral-medium-latest → 429 «rate limit» (нагрузка растёт)
"""
from __future__ import annotations

import json
import os
from typing import AsyncGenerator, Dict, List, Optional

import httpx


class AnyModelClient:
    """Backward-compat имя класса. Использует Mistral API."""

    BASE_URL = "https://api.mistral.ai/v1"
    DEFAULT_MODEL = "ministral-8b-latest"
    FALLBACK_MODEL = "mistral-tiny"

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or os.environ.get("MISTRAL_API_KEY", "")
        if not self.api_key:
            raise ValueError(
                "MISTRAL_API_KEY is required. "
                "Get one at https://console.mistral.ai and put it in .env or env var."
            )
        self.model = model or os.environ.get("MISTRAL_MODEL", self.DEFAULT_MODEL)
        self.fallback_model = self.FALLBACK_MODEL

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def chat_stream(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        max_tokens: int = 600,
        temperature: float = 0.3,
        fallback: bool = True,
    ) -> AsyncGenerator[Dict, None]:
        """Стримит ответ от Mistral. Yield'ит словари:
          - {"type": "content", "delta": "..."}
          - {"type": "reasoning", "delta": "..."}    # пусто для Mistral
          - {"type": "usage", ...}
          - {"type": "done"}
          - {"type": "error", "message": "..."}
        """
        url = f"{self.BASE_URL}/chat/completions"
        payload = {
            "model": model or self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        used_fallback = False
        overall_timeout_s = 60

        # CoT-фильтр (защита от провайдерских префиксов в content)
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
        _state = {"in_cot": None, "buf": ""}

        try:
            async with httpx.AsyncClient(timeout=overall_timeout_s) as client:
                resp = await client.post(
                    url, headers=self._headers(), json=payload,
                )
                # 429 — rate limit: retry 1 раз через 1.5 сек
                if resp.status_code == 429 and fallback and not used_fallback:
                    import asyncio as _a
                    await _a.sleep(1.5)
                    async for ev in self.chat_stream(
                        messages, model=model, fallback=fallback,
                    ):
                        yield ev
                    return

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

                full_text = resp.text
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
            # Гарантируем непустое сообщение даже при пустом str(e)
            e_name = type(e).__name__
            e_msg = str(e).strip() if str(e).strip() else "(без описания)"
            yield {
                "type": "error",
                "message": f"HTTP error [{e_name}]: {e_msg}",
            }

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
