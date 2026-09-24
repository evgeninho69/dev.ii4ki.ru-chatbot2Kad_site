# -*- coding: utf-8 -*-
"""Роутер запросов: определяет, в какую ветку (price/info) отправить,
собирает контекст, формирует system prompt.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional

from prompts import (
    INFO_PROMPT, NO_ANSWER_PROMPT, PRICE_KEYWORDS, PRICING_PROMPT,
)
from search import SiteIndex


# Веса для эвристики ценового вопроса
PRICE_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in PRICE_KEYWORDS) + r")",
    re.IGNORECASE,
)


@dataclass
class RoutingDecision:
    mode: str                          # "pricing" | "info" | "no_answer"
    page: Optional[Dict]               # Найденная страница
    system_prompt: str                 # Готовый system prompt
    found_title: str = ""              # Заголовок для логов
    found_url: str = ""                # URL для логов


class QueryRouter:
    def __init__(self, site_index: SiteIndex):
        self.idx = site_index
        self.meta = site_index.data  # верхнеуровневые данные (phone, etc.)

    # ---------- helpers ----------
    def _meta(self) -> Dict[str, str]:
        return {
            "phone": self.meta.get("phone", "+7 (4822) 41-57-68"),
            "calculator_url": self.meta.get("calculator_url", "https://2kad.ru/kalkulyator/"),
            "main_url": self.meta.get("pages", [{}])[0].get("url", "https://2kad.ru/"),
            "email": self.meta.get("email", "info@2kad.ru"),
        }

    def is_pricing_question(self, q: str) -> bool:
        return bool(PRICE_RE.search(q))

    def _truncate(self, text: str, max_chars: int = 1800) -> str:
        if len(text) <= max_chars:
            return text
        cut = text[:max_chars]
        for sep in [". ", "; "]:
            i = cut.rfind(sep)
            if i > max_chars * 0.7:
                return cut[:i + 1]
        return cut + "…"

    def _esc(self, text: str) -> str:
        """Экранируем { и } чтобы str.format() не ломался на плейсхолдерах."""
        if not text:
            return ""
        return text.replace("{", "{{").replace("}", "}}")

    # ---------- main ----------
    def route(self, user_query: str) -> RoutingDecision:
        meta = self._meta()
        is_price = self.is_pricing_question(user_query)
        page = self.idx.find_service_page(user_query)
        # Strip URL to bare slug for title sanitization

        # Режим PRICING
        if is_price:
            calculator = self.idx.get("kalkulyator") or {}
            calc_text = calculator.get("content", "")
            calc_excerpt = self._esc(self._truncate(calculator.get("excerpt", ""), 1200))
            service_ctx = self._esc(page.get("excerpt", "")[:1200]) if page else ""

            sys_prompt = PRICING_PROMPT.format(
                calculator_context=calc_excerpt,
                service_context=service_ctx,
                calculator_url=meta["calculator_url"],
                phone=meta["phone"],
            )
            return RoutingDecision(
                mode="pricing",
                page=page,
                system_prompt=sys_prompt,
                found_title=(page or calculator).get("title", ""),
                found_url=(page or calculator).get("url", ""),
            )

        # Режим INFO — есть страница
        if page:
            content_excerpt = self._esc(self._truncate(page.get("content", ""), 2200))
            sys_prompt = INFO_PROMPT.format(
                page_title=self._esc(page["title"]),
                page_url=page["url"],
                page_content=content_excerpt,
                phone=meta["phone"],
            )
            return RoutingDecision(
                mode="info",
                page=page,
                system_prompt=sys_prompt,
                found_title=page["title"],
                found_url=page["url"],
            )

        # Режим NO_ANSWER — ничего не нашли
        main_url = self.meta["pages"][0]["url"] if self.meta["pages"] else "https://2kad.ru/"
        sys_prompt = NO_ANSWER_PROMPT.format(
            main_url=main_url,
            email=meta["email"],
            phone=meta["phone"],
        )
        return RoutingDecision(
            mode="no_answer",
            page=None,
            system_prompt=sys_prompt,
            found_title="(no page)",
            found_url="",
        )