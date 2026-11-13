# -*- coding: utf-8 -*-
"""Клиент LLM для 2KAD-чатбота (все провайдеры — OpenAI-совместимые).

Провайдеры выбираются по env и образуют **цепочку** (первый доступный — основной,
остальные — запасные, переключение происходит автоматически при сбое):

| # | Провайдер  | Ключ в env            | Base URL по умолчанию      | Модель по умолчанию   |
|---|------------|-----------------------|----------------------------|-----------------------|
| 1 | DeepSeek   | DEEPSEEK_API_KEY      | https://api.deepseek.com  | deepseek-flash        |
| 2 | AnyModel   | ANYMODEL_API_KEY      | https://anymodel.org/v1   | am/gpt-oss-20b        |
| 3 | Mistral    | MISTRAL_API_KEY       | https://api.mistral.ai/v1 | ministral-8b-latest   |

КЛЮЧЕВЫЕ ОСОБЕННОСТИ:

1. **Real streaming по сети** — `client.stream("POST", ...)` + `aiter_lines()`,
   каждый chunk сразу в StreamingResponse.

2. **STALL DETECTION** — если за STALL_TIMEOUT_S секунд не пришло ни одного chunk'а,
   считаем, что провайдер завис, и переключаемся на следующего в цепочке.
   Каждый полученный chunk сбрасывает счётчик (rolling timeout).

3. **Retry 429** (rate-limit) — 2 попытки с backoff.

4. **Fallback по цепочке** — при 5xx / ConnectError / ReadTimeout / stall / обрыве
   стрима берётся следующий провайдер (или запасная модель того же провайдера,
   если задана переменная *_FALLBACK_MODEL).

5. **DeepSeek thinking mode** — по умолчанию выключен (`DEEPSEEK_THINKING=disabled`):
   для чата на сайте важна скорость. Если включить — модель присылает
   `reasoning_content`, он отдаётся отдельным событием "reasoning".

Переменные окружения:
- DEEPSEEK_API_KEY / DEEPSEEK_API_BASE / DEEPSEEK_MODEL / DEEPSEEK_FALLBACK_MODEL
- ANYMODEL_API_KEY  / ANYMODEL_API_BASE  / ANYMODEL_MODEL  / ANYMODEL_FALLBACK_MODEL
- MISTRAL_API_KEY   / MISTRAL_API_BASE   / MISTRAL_MODEL   / MISTRAL_FALLBACK_MODEL
- DEEPSEEK_THINKING — enabled | disabled (по умолчанию disabled)
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from typing import AsyncGenerator, Dict, List, Optional

import httpx


# Сколько секунд ждать следующий chunk. Каждый полученный chunk сбрасывает таймер.
STALL_TIMEOUT_S = 10.0

# Общий бюджет времени на всю цепочку провайдеров (включая fallback).
# Нужен, чтобы ответ гарантированно ушёл клиенту: прокси перед доменом
# обрывает соединение примерно на 60-й секунде, и без жёсткого дедлайна
# пользователь видел бы пустой чат.
TOTAL_BUDGET_S = 20.0

# Если провайдер два раза подряд завис — временно отключаем LLM и отдаём
# гарантированный ответ из материалов базы знаний (см. app.grounded_answer).
# Через cooldown пробуем снова: если поток прошёл — счётчик сбрасывается.
DEGRADED_COOLDOWN_S = 300.0
DEGRADED_AFTER_FAILURES = 2

# Описание провайдеров. Поля:
#   key_env / base_env / model_env — имена переменных окружения
#   default_base / default_model   — значения по умолчанию
#   default_fallback_model         — запасная модель внутри провайдера (необязательно)
PROVIDERS = [
    {
        "name": "deepseek",
        "key_env": "DEEPSEEK_API_KEY",
        "base_env": "DEEPSEEK_API_BASE",
        "default_base": "https://api.deepseek.com",
        "model_env": "DEEPSEEK_MODEL",
        "default_model": "deepseek-flash",
        "fallback_model_env": "DEEPSEEK_FALLBACK_MODEL",
        "default_fallback_model": "",
    },
    {
        "name": "anymodel",
        "key_env": "ANYMODEL_API_KEY",
        "base_env": "ANYMODEL_API_BASE",
        "default_base": "https://anymodel.org/v1",
        "model_env": "ANYMODEL_MODEL",
        "default_model": "am/gpt-oss-20b",
        "fallback_model_env": "ANYMODEL_FALLBACK_MODEL",
        "default_fallback_model": "",
    },
    {
        "name": "mistral",
        "key_env": "MISTRAL_API_KEY",
        "base_env": "MISTRAL_API_BASE",
        "default_base": "https://api.mistral.ai/v1",
        "model_env": "MISTRAL_MODEL",
        "default_model": "ministral-8b-latest",
        "fallback_model_env": "MISTRAL_FALLBACK_MODEL",
        "default_fallback_model": "mistral-tiny",
    },
]


def _deepseek_extra() -> Dict:
    """Доп. поля запроса для DeepSeek (thinking mode)."""
    mode = os.environ.get("DEEPSEEK_THINKING", "disabled").strip().lower()
    if mode in ("enabled", "on", "true", "1"):
        return {"thinking": {"type": "enabled"}}
    return {"thinking": {"type": "disabled"}}


def build_chain() -> List[Dict]:
    """Собирает цепочку провайдеров из env.

    Порядок: DeepSeek → AnyModel → Mistral. Провайдер без ключа пропускается.
    Если у провайдера задана *_FALLBACK_MODEL — она становится следующей целью
    цепочки (перед следующим провайдером).
    """
    chain: List[Dict] = []
    for p in PROVIDERS:
        key = os.environ.get(p["key_env"], "").strip()
        if not key:
            continue
        base = os.environ.get(p["base_env"], "").strip() or p["default_base"]
        model = os.environ.get(p["model_env"], "").strip() or p["default_model"]
        extra = _deepseek_extra() if p["name"] == "deepseek" else {}

        chain.append({
            "name": p["name"],
            "base_url": base.rstrip("/"),
            "api_key": key,
            "model": model,
            "extra": extra,
        })

        fb = os.environ.get(p["fallback_model_env"], "").strip() or p["default_fallback_model"]
        if fb and fb != model:
            chain.append({
                "name": f"{p['name']}:fallback",
                "base_url": base.rstrip("/"),
                "api_key": key,
                "model": fb,
                "extra": extra,
            })
    return chain


class AnyModelClient:
    """Backward-compat имя класса. Провайдер выбирается по env (см. build_chain)."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        provider: Optional[str] = None,
    ):
        chain = build_chain()

        # Явные аргументы — для тестов и нестандартных развёртываний.
        if api_key:
            chain.insert(0, {
                "name": provider or "custom",
                "base_url": os.environ.get("CUSTOM_API_BASE", "https://api.deepseek.com").rstrip("/"),
                "api_key": api_key,
                "model": model or os.environ.get("DEEPSEEK_MODEL", "deepseek-flash"),
                "extra": _deepseek_extra(),
            })
        elif provider:
            chain = [t for t in chain if t["name"].split(":")[0] == provider] + \
                    [t for t in chain if t["name"].split(":")[0] != provider]

        if not chain:
            raise ValueError(
                "No LLM provider configured. Set one of: "
                "DEEPSEEK_API_KEY (https://platform.deepseek.com/api_keys), "
                "ANYMODEL_API_KEY (https://anymodel.org), "
                "MISTRAL_API_KEY (https://console.mistral.ai) "
                "in .env or env vars."
            )

        self.chain = chain
        self._idx = 0

        # Состояние «деградации»: провайдер завис — перестаём тратить время
        # пользователя и сразу отдаём ответ из базы знаний.
        self._fail_streak = 0
        self._degraded_until = 0.0

        # Атрибуты для backward-compat (код, который смотрит на .model / .api_key).
        self.api_key = chain[0]["api_key"]
        self.model = chain[0]["model"]
        self.base_url = chain[0]["base_url"]
        self.fallback_model = chain[1]["model"] if len(chain) > 1 else ""

    # ---------- свойства текущей цели ----------

    @property
    def target(self) -> Dict:
        return self.chain[self._idx]

    @property
    def provider(self) -> str:
        return self.target["name"]

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.target['api_key']}",
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
        """Стрим ответ от LLM с stall-detection и переключением провайдера."""
        # Каждый запрос стартует с основной модели: после срабатывания
        # fallback индекс цепочки не должен «залипать» на запасной.
        self._idx = 0

        # Деградация: провайдер нестабилен — не тратим время пользователя.
        if time.monotonic() < self._degraded_until:
            yield {
                "type": "error",
                "message": (
                    "degraded: провайдер временно нестабилен, "
                    "ответ формируется по базе знаний"
                ),
            }
            return

        deadline = time.monotonic() + TOTAL_BUDGET_S
        ok = False
        async for ev in self._stream_once(
            messages, model=model,
            max_tokens=max_tokens, temperature=temperature,
            fallback_allowed=fallback, used_fallback=False,
            tried_fallback=[],
            deadline=deadline,
        ):
            if ev["type"] == "done":
                ok = True
            elif ev["type"] == "error":
                self._fail_streak += 1
                if self._fail_streak >= DEGRADED_AFTER_FAILURES:
                    self._degraded_until = time.monotonic() + DEGRADED_COOLDOWN_S
            yield ev
        if ok:
            # Поток прошёл — провайдер считается живым.
            self._fail_streak = 0

    async def _stream_once(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str],
        max_tokens: int,
        temperature: float,
        fallback_allowed: bool,
        used_fallback: bool,
        tried_fallback: list,
        deadline: Optional[float] = None,
    ) -> AsyncGenerator[Dict, None]:
        target = self.target
        # Сколько секунд ещё осталось на весь вызов (включая fallback).
        remaining = (deadline - time.monotonic()) if deadline else TOTAL_BUDGET_S
        if remaining <= 1.0:
            yield {
                "type": "error",
                "message": (
                    f"budget исчерпан: {target['name']}/{target['model']} "
                    f"не успел ответить за {TOTAL_BUDGET_S:.0f} сек"
                ),
            }
            return
        used_model = model or target["model"]
        url = f"{target['base_url']}/chat/completions"
        payload = {
            "model": used_model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        # Доп. поля провайдера (для DeepSeek — thinking mode).
        payload.update(target.get("extra") or {})

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

        # HTTP-таймауты. Read — большой (провайдер в принципе может отвечать долго),
        # но stall detection сработает раньше. Все таймауты ограничены общим
        # бюджетом времени, чтобы ответ гарантированно ушёл клиенту.
        timeout_cfg = httpx.Timeout(
            connect=min(10.0, max(2.0, remaining)),
            read=min(120.0, max(3.0, remaining)),
            write=min(10.0, max(2.0, remaining)),
            pool=min(10.0, max(2.0, remaining)),
        )

        async def _do_fallback(reason: str) -> AsyncGenerator[Dict, None]:
            """Переключение на следующую цель цепочки. Если её нет — error."""
            nonlocal used_fallback
            if not fallback_allowed:
                yield {
                    "type": "error",
                    "message": f"{reason} (fallback already used)",
                }
                return
            used_fallback = True
            tried_fallback.append(f"{target['name']}:{used_model}")
            if self._idx + 1 >= len(self.chain):
                yield {
                    "type": "error",
                    "message": (
                        f"{reason} (нет запасного провайдера; "
                        f"пройдено: {', '.join(tried_fallback) or '—'})"
                    ),
                }
                return
            self._idx += 1
            nxt = self.target
            # Запасной провайдер с укороченным stall timeout — если и тут зависнет,
            # даём виджету шанс ответить хотя бы фразой «уточните».
            async for ev in self._stream_once(
                messages, model=None,
                max_tokens=max_tokens, temperature=temperature,
                fallback_allowed=True, used_fallback=True,
                tried_fallback=tried_fallback,
                deadline=deadline,
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
                            reason = (
                                f"HTTP {resp.status_code} "
                                f"[{target['name']}/{used_model}]: {err_text}"
                            )
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
                                        timeout=min(
                                            STALL_TIMEOUT_S,
                                            max(2.0, (deadline - time.monotonic())
                                                if deadline else STALL_TIMEOUT_S),
                                        ),
                                    )
                                except StopAsyncIteration:
                                    # Провайдер закрыл стрим без [DONE]
                                    break
                                except asyncio.TimeoutError:
                                    # Stall: N секунд без chunk'а
                                    reason = (
                                        f"stall: {target['name']} не присылал chunk'и "
                                        f">{STALL_TIMEOUT_S:.0f} сек "
                                        f"(модель: {used_model})"
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
                                    reason = (
                                        f"upstream error "
                                        f"[{target['name']}/{used_model}]: {msg}"
                                    )
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
                                f"ReadTimeout: {target['name']} не ответил за 120 сек "
                                f"(модель: {used_model})"
                            )
                            async for ev in _do_fallback(reason):
                                yield ev
                            return
                        except httpx.HTTPError as e:
                            e_name = type(e).__name__
                            reason = (
                                f"HTTP error [{e_name}] "
                                f"[{target['name']}/{used_model}]: "
                                f"{str(e).strip() or '(без описания)'}"
                            )
                            async for ev in _do_fallback(reason):
                                yield ev
                            return

                        if _state["in_cot"] is True and _state["buf"].strip():
                            tail = _state["buf"].strip()
                            yield {"type": "content", "delta": tail}
                            _state["in_cot"] = False

                        if not saw_done and not had_error:
                            # Стрим оборвался без [DONE] и без ошибки.
                            # Это типичный stall: 2-4 чанка и тишина.
                            # Если хоть что-то успели — пробуем запасного провайдера,
                            # иначе сразу error.
                            if _state["buf"].strip() or _state["in_cot"] is False:
                                # Был реальный контент — пусть запасной провайдер
                                # попробует ещё раз.
                                reason = (
                                    f"stream ended without [DONE] после "
                                    f"{len(_state['buf'])} чанков "
                                    f"(провайдер: {target['name']}, "
                                    f"модель: {used_model})"
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
                reason = (
                    f"HTTP error [{e_name}] [{target['name']}/{used_model}]: "
                    f"{str(e).strip() or '(без описания)'}"
                )
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
            deadline=time.monotonic() + TOTAL_BUDGET_S,
        ):
            if ev["type"] == "content":
                content += ev["delta"]
            elif ev["type"] == "error":
                last_error = ev["message"]
        if not content.strip():
            msg = last_error or "model returned empty response (upstream timeout?)"
            raise RuntimeError(msg)
        return content.strip()


# Понятное имя для нового кода (старые импорты AnyModelClient работают как раньше).
LLMClient = AnyModelClient
