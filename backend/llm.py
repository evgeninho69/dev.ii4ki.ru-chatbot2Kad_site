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
        }
        used_fallback = False
        overall_timeout_s = 180

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
                        if delta.get("content"):
                            yield {"type": "content", "delta": delta["content"]}
                        if delta.get("reasoning_content"):
                            yield {"type": "reasoning", "delta": delta["reasoning_content"]}
                    if "usage" in obj and obj["usage"]:
                        u = obj["usage"] or {}
                        yield {
                            "type": "usage",
                            "prompt_tokens": u.get("prompt_tokens") or 0,
                            "completion_tokens": u.get("completion_tokens") or 0,
                        }

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