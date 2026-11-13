# -*- coding: utf-8 -*-
"""Роутер запросов: определяет ветку (pricing / info / blog / no_answer),
собирает контекст из базы знаний, формирует system prompt.

Источники контекста:
- страницы услуг 2kad.ru (site_index.json, type=page)
- блог 2kad.ru (site_index.json, type=post) — 83 статьи
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional

from prompts import (
    BLOG_PROMPT, INFO_PROMPT, NO_ANSWER_PROMPT, PRICE_KEYWORDS, PRICING_PROMPT,
)
from search import SiteIndex


# Веса для эвристики ценового вопроса
PRICE_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in PRICE_KEYWORDS) + r")",
    re.IGNORECASE,
)


@dataclass
class RoutingDecision:
    mode: str                          # "pricing" | "info" | "blog" | "no_answer"
    page: Optional[Dict]               # Найденный документ (страница или пост)
    system_prompt: str                 # Готовый system prompt
    found_title: str = ""              # Заголовок для логов
    found_url: str = ""                # URL для логов
    source_kind: str = ""              # "page" | "blog" | ""


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
        service_page = self.idx.find_service_page(user_query)
        blog_post = self.idx.find_blog_post(user_query)

        # --- Режим PRICING ---
        if is_price:
            calculator = self.idx.get("kalkulyator") or {}
            calc_text = calculator.get("content", "")
            calc_excerpt = self._esc(self._truncate(calculator.get("excerpt", "") or calc_text, 1200))
            service_ctx = self._esc(self._truncate(service_page.get("content", ""), 1200)) if service_page else ""

            sys_prompt = PRICING_PROMPT.format(
                calculator_context=calc_excerpt,
                service_context=service_ctx,
                calculator_url=meta["calculator_url"],
                phone=meta["phone"],
            )
            return RoutingDecision(
                mode="pricing",
                page=service_page,
                system_prompt=sys_prompt,
                found_title=(service_page or calculator).get("title", ""),
                found_url=(service_page or calculator).get("url", ""),
                source_kind="page",
            )

        # --- Выбор источника: блог или страница услуги ---
        page, post, _score = self.idx.find_best(user_query)

        # Блог выигрывает, если:
        #  - в вопросе есть маркеры блога/закона (find_best уже учёл), или
        #  - статья заметно сильнее совпала, чем страница услуги
        use_blog = False
        if post:
            if page is None:
                use_blog = True
            else:
                p_score = self._score_only(user_query, page)
                b_score = self._score_only(user_query, post)
                if b_score >= p_score * 0.9:
                    use_blog = True

        # --- Режим BLOG ---
        if use_blog and post:
            content_excerpt = self._esc(self._truncate(post.get("content", ""), 1400))
            cats = ", ".join(post.get("category_names", [])) or "Блог"
            sys_prompt = BLOG_PROMPT.format(
                post_title=self._esc(post["title"]),
                post_url=post["url"],
                post_content=content_excerpt,
                post_category=self._esc(cats),
                post_date=self._esc(post.get("date", "")),
                phone=meta["phone"],
            )
            return RoutingDecision(
                mode="blog",
                page=post,
                system_prompt=sys_prompt,
                found_title=post["title"],
                found_url=post["url"],
                source_kind="blog",
            )

        # --- Режим INFO — страница услуги ---
        if page:
            content_excerpt = self._esc(self._truncate(page.get("content", ""), 1600))
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
                source_kind="page",
            )

        # --- Режим BLOG (fallback — страницу не нашли, но пост нашли) ---
        if post:
            content_excerpt = self._esc(self._truncate(post.get("content", ""), 1400))
            cats = ", ".join(post.get("category_names", [])) or "Блог"
            sys_prompt = BLOG_PROMPT.format(
                post_title=self._esc(post["title"]),
                post_url=post["url"],
                post_content=content_excerpt,
                post_category=self._esc(cats),
                post_date=self._esc(post.get("date", "")),
                phone=meta["phone"],
            )
            return RoutingDecision(
                mode="blog",
                page=post,
                system_prompt=sys_prompt,
                found_title=post["title"],
                found_url=post["url"],
                source_kind="blog",
            )

        # --- Режим NO_ANSWER ---
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

    # ---------- служебное ----------
    def _score_only(self, query: str, doc: Dict) -> float:
        only = "post" if doc.get("type") == "post" else "page"
        res = self.idx.search(query, top_k=1, only=only)
        if res and res[0][0]["id"] == doc["id"]:
            return res[0][1]
        return 0.0
