# -*- coding: utf-8 -*-
"""Клиент Mistral AI API для 2KAD-чатбота.

Использует OpenAI-совместимый endpoint: https://api.mistral.ai/v1/chat/completions.

КЛЮЧЕВЫЕ ОТЛИЧИЯ от предыдущих версий:

1. **Real streaming по сети** — `client.stream("POST", ...)` + `aiter_lines()`,
   каждый chunk сразу в StreamingResponse.

2. **STALL DETECTION** (ГЛАВНАЯ ПРОБЛЕМА FREE TIER):
   Mistral free-tier присылает 2-4 символа ответа, потом **зависает на 60-120 сек**,
   не закрывая соединение. Клиент (браузер виджета) устаёт ждать и выдаёт ReadTimeout.
   Решение: если за 15 сек не пришло ни одного chunk — НЕМЕДЛЕННЫЙ fallback на
   `mistral-tiny`. Каждый chunk сбрасывает счётчик (rolling timeout).

3. **Retry 429** (rate-limit) — 2 попытки с backoff.

4. **Fallback once** на `mistral-tiny` при: 5xx, ConnectError, ReadTimeout, stall.

Переменные окружения:
- MISTRAL_API_KEY  — обязательно
- MISTRAL_MODEL    — модель (по умолчанию ministral-8b-latest)
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import AsyncGenerator, Dict, List, Optional

import httpx


# Сколько секунд ждать следующий chunk. Каждый полученный chunk сбрасывает таймер.
STALL_TIMEOUT_S = 15.0


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
        """Стримит ответ от Mistral с stall-detection и fallback."""
        async for ev in self._stream_once(
            messages, model=model,
            max_tokens=max_tokens, temperature=temperature,
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

        # CoT-фильтр: некоторые модели вставляют в content рассуждения.
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

        # HTTP таймауты. Read — большой (Mistral в принципе может отвечать долго),
        # но stall detection сработает раньше.
        timeout_cfg = httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0)

        async def _do_fallback(reason: str) -> AsyncGenerator[Dict, None]:
            """Один fallback на mistral-tiny. Если и там провал — error."""
            nonlocal used_fallback
            if not fallback_allowed or used_fallback:
                yield {
                    "type": "error",
                    "message": f"{reason} (fallback already used)",
                }
                return
            used_fallback = True
            tried_fallback.append(model or self.model)
            # Fallback с укороченным stall timeout — если и тут зависнет,
            # даём виджету шанс ответить хотя бы фразой «уточните».
            async for ev in self._stream_once(
                messages, model=self.fallback_model,
                max_tokens=max_tokens, temperature=temperature,
                fallback_allowed=False, used_fallback=True,
                tried_fallback=tried_fallback,
            ):
                yield ev

        # Retry на 429 (rate-limit)
        max_retries_429 = 2
        for attempt in range(max_retries_429 + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout_cfg) as client:
                    async with client.stream(
                        "POST", url,
                        headers=self._headers(),
                        json=payload,
                    ) as resp:

                        if resp.status_code == 429 and attempt < max_retries_429:
                            await asyncio.sleep(1.5 * (attempt + 1))
                            continue  # retry same model

                        if resp.status_code != 200:
                            err_body = await resp.aread()
                            err_text = err_body.decode("utf-8", errors="replace")[:500]
                            reason = f"HTTP {resp.status_code}: {err_text}"
                            async for ev in _do_fallback(reason):
                                yield ev
                            return

                        # 200 OK — парсим SSE с stall-detection
                        saw_done = False
                        had_error = False
                        # Итератор по строкам; каждый await next() обёрнут в wait_for.
                        line_iter = resp.aiter_lines()

                        try:
                            while True:
                                try:
                                    raw_line = await asyncio.wait_for(
                                        line_iter.__anext__(),
                                        timeout=STALL_TIMEOUT_S,
                                    )
                                except StopAsyncIteration:
                                    # Mistral закрыл стрим без [DONE]
                                    break
                                except asyncio.TimeoutError:
                                    # Stall: N секунд без chunk'а
                                    reason = (
                                        f"stall: Mistral не присылал chunk'и "
                                        f">{STALL_TIMEOUT_S:.0f} сек "
                                        f"(модель: {model or self.model})"
                                    )
                                    async for ev in _do_fallback(reason):
                                        yield ev
                                    return

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
                                    reason = f"upstream error: {msg}"
                                    async for ev in _do_fallback(reason):
                                        yield ev
                                    return
                                for ch in obj.get("choices", []):
                                    delta = ch.get("delta") or {}
                                    if delta.get("reasoning_content"):
                                        yield {
                                            "type": "reasoning",
                                            "delta": delta["reasoning_content"],
                                        }
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
                            reason = (
                                f"ReadTimeout: Mistral не ответил за 120 сек "
                                f"(модель: {model or self.model})"
                            )
                            async for ev in _do_fallback(reason):
                                yield ev
                            return
                        except httpx.HTTPError as e:
                            e_name = type(e).__name__
                            reason = f"HTTP error [{e_name}]: {str(e).strip() or '(без описания)'}"
                            async for ev in _do_fallback(reason):
                                yield ev
                            return

                        if _state["in_cot"] is True and _state["buf"].strip():
                            tail = _state["buf"].strip()
                            yield {"type": "content", "delta": tail}
                            _state["in_cot"] = False

                        if not saw_done and not had_error:
                            # Стрим оборвался без [DONE] и без ошибки.
                            # Это типичный stall free-tier: 2-4 чанка и тишина.
                            # Если хоть что-то успели — пробуем fallback,
                            # иначе сразу error.
                            if _state["buf"].strip() or _state["in_cot"] is False:
                                # Был реальный контент — пусть fallback попробует
                                # ещё раз, иногда mistral-tiny стримит чище.
                                reason = (
                                    f"stream ended without [DONE] после "
                                    f"{len(_state['buf'])} чанков (модель: "
                                    f"{model or self.model})"
                                )
                                async for ev in _do_fallback(reason):
                                    yield ev
                                return
                            yield {
                                "type": "error",
                                "message": "stream ended without [DONE] and no content",
                            }
                        # Успешное завершение (или был error → return выше)
                        return

            except httpx.HTTPError as e:
                # ConnectError, NetworkError и т.п.
                e_name = type(e).__name__
                reason = f"HTTP error [{e_name}]: {str(e).strip() or '(без описания)'}"
                async for ev in _do_fallback(reason):
                    yield ev
                return
            # Если прошли через retry по 429 — выходим
            break

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
