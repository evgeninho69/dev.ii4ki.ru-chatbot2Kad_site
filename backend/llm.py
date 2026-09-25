# -*- coding: utf-8 -*-
"""Клиент Mistral AI API для 2KAD-чатбота.

Использует OpenAI-совместимый endpoint: https://api.mistral.ai/v1/chat/completions.

КЛЮЧЕВОЕ ОТЛИЧИЕ от первой версии: real streaming по сети.
Раньше мы делали `client.post(...)` без `stream=True` и потом читали
`resp.text` — это блокировало наш сервер до полного закрытия соединения
с Mistral. У Mistral free-tier стрим может висеть 60-120 сек перед
закрытием, и весь этот срок FastAPI не отдавал байты браузеру →
браузер видел ReadTimeout при живом ответе от Mistral.

Теперь используем `client.stream("POST", ...)` + `aiter_lines()`,
и каждый chunk немедленно передаётся в StreamingResponse.
Браузер видит «размышление» уже через 0.3-2 сек (первый content chunk),
а не через 90+ сек (когда Mistral закроет соединение).

Переменные окружения:
- MISTRAL_API_KEY  — обязательно
- MISTRAL_MODEL    — модель (по умолчанию ministral-8b-latest)
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
        """Стримит ответ от Mistral ПО-НАСТОЯЩЕМУ: каждый chunk сразу клиенту.

        Схема:
          Mistral SSE  →  yield событие  →  StreamingResponse FastAPI
                              ↓
                    (browser видит chunk за <2 сек от начала запроса)

        При ошибке (429, 5xx, timeout) сначала пробуем fallback 1 раз,
        затем — error event. CoT-фильтр для редкой утечки reasoning в content.
        """
        used_fallback = False
        # Connect — 10 сек (DNS+TLS+HTTP), Read — 110 сек (бóльшая часть LLM-ответа)
        # Внутри потока используем connect=10, read=110 — но реальное время
        # прихода первого chunk обычно 0.5-3 сек.
        async for ev in self._stream_once(
            messages, model=model,
            max_tokens=max_tokens, temperature=temperature,
            connect_s=10.0, read_s=110.0,
            fallback_allowed=fallback, used_fallback=False,
            tried_fallback=[],
        ):
            yield ev

    async def _stream_once(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str],
        max_tokens: int,
        temperature: float,
        connect_s: float,
        read_s: float,
        fallback_allowed: bool,
        used_fallback: bool,
        tried_fallback: list,
    ) -> AsyncGenerator[Dict, None]:
        url = f"{self.BASE_URL}/chat/completions"
        payload = {
            "model": model or self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }

        # CoT-фильтр
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

        # Retry на 429 (rate-limit)
        max_retries_429 = 2
        try:
            for attempt in range(max_retries_429 + 1):
                timeout_cfg = httpx.Timeout(read_s, connect=connect_s)
                try:
                    async with httpx.AsyncClient(timeout=timeout_cfg) as client:
                        async with client.stream(
                            "POST", url,
                            headers=self._headers(),
                            json=payload,
                        ) as resp:

                            # Получили заголовки — обработаем
                            if resp.status_code == 429 and attempt < max_retries_429:
                                import asyncio as _a
                                await _a.sleep(1.5 * (attempt + 1))
                                continue  # retry

                            if resp.status_code != 200:
                                err_body = await resp.aread()
                                err_text = err_body.decode("utf-8", errors="replace")[:500]
                                # Попытка fallback
                                if fallback_allowed and not used_fallback:
                                    used_fallback = True
                                    tried_fallback.append(model or self.model)
                                    async for ev in self._stream_once(
                                        messages, model=self.fallback_model,
                                        max_tokens=max_tokens, temperature=temperature,
                                        connect_s=connect_s, read_s=read_s,
                                        fallback_allowed=False, used_fallback=True,
                                        tried_fallback=tried_fallback,
                                    ):
                                        yield ev
                                    return
                                yield {
                                    "type": "error",
                                    "message": f"HTTP {resp.status_code}: {err_text}",
                                }
                                return

                            # Стрим открыт — парсим SSE в реальном времени
                            saw_done = False
                            had_error = False
                            buf = ""
                            try:
                                async for raw_line in resp.aiter_lines():
                                    if not raw_line:
                                        continue
                                    line = raw_line.strip()
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
                                        if fallback_allowed and not used_fallback:
                                            used_fallback = True
                                            async for ev in self._stream_once(
                                                messages, model=self.fallback_model,
                                                max_tokens=max_tokens, temperature=temperature,
                                                connect_s=connect_s, read_s=read_s,
                                                fallback_allowed=False, used_fallback=True,
                                                tried_fallback=tried_fallback,
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
                            except httpx.ReadTimeout:
                                if fallback_allowed and not used_fallback:
                                    used_fallback = True
                                    tried_fallback.append(model or self.model)
                                    async for ev in self._stream_once(
                                        messages, model=self.fallback_model,
                                        max_tokens=max_tokens, temperature=temperature,
                                        connect_s=connect_s, read_s=read_s,
                                        fallback_allowed=False, used_fallback=True,
                                        tried_fallback=tried_fallback,
                                    ):
                                        yield ev
                                    return
                                yield {
                                    "type": "error",
                                    "message": "ReadTimeout: Mistral не ответил за 110 сек (тир перегружен)",
                                }
                                return

                            if _state["in_cot"] is True and _state["buf"].strip():
                                tail = _state["buf"].strip()
                                yield {"type": "content", "delta": tail}
                                _state["in_cot"] = False

                            if not saw_done and not had_error:
                                yield {"type": "error", "message": "stream ended without [DONE]"}

                            # Успешное завершение стрима — retry не нужен
                            return

                except httpx.HTTPError as e:
                    # HTTP-уровневая ошибка: ConnectError, ReadTimeout, etc.
                    if fallback_allowed and not used_fallback:
                        used_fallback = True
                        tried_fallback.append(model or self.model)
                        async for ev in self._stream_once(
                            messages, model=self.fallback_model,
                            max_tokens=max_tokens, temperature=temperature,
                            connect_s=connect_s, read_s=read_s,
                            fallback_allowed=False, used_fallback=True,
                            tried_fallback=tried_fallback,
                        ):
                            yield ev
                        return
                    e_name = type(e).__name__
                    e_msg = str(e).strip() or "(без описания)"
                    yield {
                        "type": "error",
                        "message": f"HTTP error [{e_name}]: {e_msg}",
                    }
                    return
                # Если прошли через retry по 429 — return
                break

        except Exception as e:
            yield {"type": "error", "message": f"unhandled: {type(e).__name__}: {e}"}

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
        async for ev in self._stream_once(
            messages, model=model,
            max_tokens=max_tokens, temperature=temperature,
            connect_s=10.0, read_s=110.0,
            fallback_allowed=True, used_fallback=False,
            tried_fallback=[],
        ):
            if ev["type"] == "content":
                content += ev["delta"]
            elif ev["type"] == "error":
                last_error = ev["message"]
        if not content.strip():
            msg = last_error or "model returned empty response (upstream timeout?)"
            raise RuntimeError(msg)
        return content.strip()
