# -*- coding: utf-8 -*-
"""Роутер запросов: определяет режим ответа, собирает контекст из базы
знаний и готовит system prompt.

Режимы: pricing | service | article | law | tool | no_answer

Корпуса базы знаний:
- страницы 2kad.ru (услуги, разделы)
- блог 2kad.ru
- нормативные документы (ki-rf.ru/zakon/)
- сервисы и инструменты из раздела «Полезное»

Главное правило: у каждого ответа есть link_doc — материал 2КАД (услуга
или статья), на который ответ заканчивается ссылкой.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from prompts import (
    ARTICLE_PROMPT, LAW_PROMPT, NO_ANSWER_PROMPT, PRICE_MARKERS,
    PRICE_STEMS, PRICE_WEAK_STEMS, PRICE_WORDS, PRICING_PROMPT, SERVICE_PROMPT,
    TOOL_PROMPT,
)
from search import LAW_HINTS, SiteIndex


# Номер нормативного акта в вопросе: П/0393, П/0372/26, 305-ФЗ
ACT_RE = re.compile(
    r"(?:№\s*)?([ПP]\s*/\s*\d{3,4}(?:\s*/\s*\d{2,4})?|\d{1,3}\s*[-–—]\s*ФЗ)",
    re.IGNORECASE,
)

# Страницы, которые не годятся в качестве финальной ссылки
BAD_LINK_SLUGS = {
    "blog", "glavnaya", "kontakty", "politika-konfidencialnosti",
    "poleznoe", "otzyvy", "kejsy",
}


def _price_pattern(stems: List[str], words: List[str]) -> str:
    """Регулярка ценовых вопросов.

    Границы слов ставятся аккуратно: стемы («цен») ищутся по началу слова,
    а «почем» — только как целое слово, иначе ловило бы «почему».
    """
    parts = []
    for k in stems:
        left = r"\b" if k[:1].isalnum() else ""
        parts.append(left + re.escape(k))
    for k in words:
        left = r"\b" if k[:1].isalnum() else ""
        right = r"\b" if k[-1:].isalnum() else ""
        parts.append(left + re.escape(k) + right)
    return "(?:" + "|".join(parts) + ")"


# Веса для эвристики ценового вопроса
PRICE_RE = re.compile(_price_pattern(PRICE_STEMS, PRICE_WORDS), re.IGNORECASE)
# Слабые слова («стоимость», «рассчита») держат в ценовом режиме только
# вместе с вопросительным маркером: иначе «как оспорить кадастровую
# стоимость» уходило в ответ с ценами.
PRICE_WEAK_RE = re.compile(_price_pattern(PRICE_WEAK_STEMS, []), re.IGNORECASE)
PRICE_MARKER_RE = re.compile(_price_pattern(PRICE_MARKERS, []), re.IGNORECASE)


@dataclass
class RoutingDecision:
    mode: str                          # pricing|service|article|law|tool|no_answer
    page: Optional[Dict] = None        # Основной найденный документ
    system_prompt: str = ""            # Готовый system prompt
    found_title: str = ""              # Заголовок для логов
    found_url: str = ""                # URL для логов
    source_kind: str = ""              # page|blog|law|tool
    link_doc: Optional[Dict] = field(default=None)   # Материал 2КАД для финальной ссылки
    link_title: str = ""
    link_url: str = ""
    law_doc: Optional[Dict] = field(default=None)    # Нормативный акт (если есть)


class QueryRouter:
    def __init__(self, site_index: SiteIndex):
        self.idx = site_index
        self.meta = site_index.data  # верхнеуровневые данные (phone, etc.)

    # ---------- helpers ----------
    def _meta(self) -> Dict[str, str]:
        return {
            "phone": self.meta.get("phone", "+7 (4822) 41-57-68"),
            "calculator_url": self.meta.get("calculator_url", "https://2kad.ru/kalkulyator/"),
            "main_url": self.meta.get("main_url", "https://2kad.ru/"),
            "email": self.meta.get("email", "info@2kad.ru"),
        }

    def is_pricing_question(self, q: str) -> bool:
        if PRICE_RE.search(q):
            return True
        if PRICE_WEAK_RE.search(q) and PRICE_MARKER_RE.search(q):
            return True
        return False

    def _truncate(self, text: str, max_chars: int = 1800) -> str:
        if not text:
            return ""
        cut = text[:max_chars]
        for sep in (". ", ".\n", "; ", " — ", ", "):
            i = cut.rfind(sep)
            if i > max_chars * 0.7:
                return cut[:i + 1]
        return cut + "…"

    def _esc(self, text: str) -> str:
        """Экранируем { и } чтобы str.format() не ломался на плейсхолдерах."""
        if not text:
            return ""
        return text.replace("{", "{{").replace("}", "}}")

    def _extract_act(self, q: str) -> str:
        m = ACT_RE.search(q or "")
        if not m:
            return ""
        return re.sub(r"\s+", "", m.group(1)).upper().replace("P/", "П/")

    def _extract_act_from_doc(self, doc: Optional[Dict]) -> str:
        """Номер акта, названный на странице 2КАД (например, П/0592).

        Приоритет у приказов Росреестра («П/xxxx»): именно они регулируют
        кадастровые работы, а не федеральные законы.
        """
        if not doc:
            return ""
        hay = f"{doc.get('title', '')}\n{doc.get('content', '')}"
        fallback = ""
        for m in ACT_RE.finditer(hay):
            act = re.sub(r"\s+", "", m.group(1)).upper().replace("P/", "П/")
            if act.startswith("П/"):
                return act
            fallback = fallback or act
        return fallback

    def _linkable(self, doc: Optional[Dict]) -> bool:
        """Подходит ли документ в качестве финальной ссылки на материал 2КАД."""
        if not doc:
            return False
        if doc.get("type") not in ("page", "post"):
            return False
        if (doc.get("slug") or "") in BAD_LINK_SLUGS:
            return False
        return len(doc.get("content") or "") > 400

    def _best_link(self, candidates: List[Dict]) -> Optional[Dict]:
        for c in candidates:
            if self._linkable(c):
                return c
        return None

    def _link_fields(self, doc: Optional[Dict], meta: Dict) -> Dict[str, str]:
        if not doc:
            return {
                "link_title": "2КАД",
                "link_url": meta["main_url"],
            }
        return {
            "link_title": doc.get("title", "Материал 2КАД"),
            "link_url": doc.get("url", meta["main_url"]),
        }

    # ---------- main ----------
    def route(self, user_query: str) -> RoutingDecision:
        q = (user_query or "").strip()
        meta = self._meta()

        tool, s_tool = self.idx.top(q, "tool", min_score=12.0)
        law, s_law = self.idx.top(q, "law", min_score=4.0)
        post, s_post = self.idx.top(q, "post", min_score=2.5)
        page, s_page = self.idx.top(q, "page", min_score=2.5)

        act = self._extract_act(q)
        law_hint = bool(LAW_HINTS.search(q))
        is_price = self.is_pricing_question(q)
        # Материал 2КАД по карте тем — он задаёт финальную ссылку.
        topic_doc = self.idx.topic_doc(q)

        # ---------- 1. Инструмент из «Полезного» ----------
        # Инструмент важнее калькулятора-как-темы: если человек прямо
        # просит что-то проверить, посчитать или оценить — ведём в сервис.
        # Чистый ценовой вопрос («сколько стоит») остаётся в режиме цены.
        wants_action = bool(re.search(
            r"(посчита|рассчита|калькулятор|провер|оцени|вычисли|какой риск|"
            r"можно ли проверить|нужно проверить)",
            q, re.IGNORECASE))
        if tool and s_tool >= 12.0 and (wants_action or s_tool >= 18.0):
            tool_ctx = self._esc(self._truncate(tool.get("content", ""), 900))
            link = self._link_fields(tool, meta)
            prompt = TOOL_PROMPT.format(
                tool_title=self._esc(tool.get("title", "")),
                tool_text=tool_ctx,
                phone=meta["phone"],
                **link,
            )
            return RoutingDecision(
                mode="tool",
                page=tool,
                system_prompt=prompt,
                found_title=tool.get("title", ""),
                found_url=tool.get("url", ""),
                source_kind="tool",
                link_doc=tool,
                link_title=link["link_title"],
                link_url=link["link_url"],
            )

        # ---------- 2. Цена ----------
        if is_price:
            calculator = self.idx.get("kalkulyator") or {}
            calc_text = calculator.get("content", "")
            calc_excerpt = self._esc(self._truncate(
                calculator.get("excerpt", "") or calc_text, 900))
            service_ctx = self._esc(self._truncate(
                page.get("content", ""), 1200)) if page else ""
            link_doc = self._best_link([topic_doc, page]) or calculator
            link = self._link_fields(link_doc, meta)
            prompt = PRICING_PROMPT.format(
                price_context=self.idx.price_context(),
                calculator_context=calc_excerpt,
                service_context=service_ctx,
                calculator_url=meta["calculator_url"],
                phone=meta["phone"],
                **link,
            )
            return RoutingDecision(
                mode="pricing",
                page=page,
                system_prompt=prompt,
                found_title=(page or calculator).get("title", ""),
                found_url=(page or calculator).get("url", ""),
                source_kind="page",
                link_doc=link_doc,
                link_title=link["link_title"],
                link_url=link["link_url"],
            )

        # ---------- 3. Конкретный акт в вопросе ----------
        if act:
            related = self.idx.find_by_act(act)
            act_doc = self.idx.find_law_by_act(act)
            if act_doc is None and law:
                hay = f"{law.get('title', '')}\n{law.get('content', '')}"
                if act.lower() in hay.lower():
                    act_doc = law
            if act_doc or related:
                link_doc = self._best_link([topic_doc, related, page, post])
                link = self._link_fields(link_doc, meta)
                related_ctx = ""
                if related:
                    related_ctx = self._esc(self._truncate(
                        related.get("content", ""), 1600))
                elif topic_doc:
                    related_ctx = self._esc(self._truncate(
                        topic_doc.get("content", ""), 1600))
                else:
                    related_ctx = "Материал 2КАД по этой теме не найден."
                law_ctx = self._esc(self._truncate(
                    act_doc.get("content", ""), 1800)) if act_doc else (
                    "Текст акта в базе не найден — опирайся на материал 2КАД "
                    "ниже и не называй номер акта, если его нет в тексте."
                )
                law_cats = ", ".join(
                    (act_doc or {}).get("category_names") or []) or "Нормативные акты"
                prompt = LAW_PROMPT.format(
                    law_title=self._esc((act_doc or {}).get("title", "")),
                    law_date=(act_doc or {}).get("date", ""),
                    law_category=self._esc(law_cats),
                    law_content=law_ctx,
                    related_context=related_ctx,
                    phone=meta["phone"],
                    **link,
                )
                return RoutingDecision(
                    mode="law",
                    page=act_doc or related,
                    system_prompt=prompt,
                    found_title=(act_doc or {}).get("title", ""),
                    found_url=(act_doc or {}).get("url", ""),
                    source_kind="law",
                    link_doc=link_doc,
                    link_title=link["link_title"],
                    link_url=link["link_url"],
                    law_doc=act_doc,
                )

        # ---------- 4. Законодательный вопрос по маркерам ----------
        if law_hint and law and s_law >= max(4.0, 0.6 * s_post):
            # Если страница 2КАД по теме называет конкретный приказ —
            # берём именно его, а не документ, который нашёлся по совпадению
            # слов. Например, «Требования к межевому плану» — это П/0592.
            act_doc = None
            topic_act = self._extract_act_from_doc(topic_doc)
            if topic_act:
                act_doc = self.idx.find_law_by_act(topic_act)
            if act_doc is None:
                act_doc = law

            link_doc = self._best_link([topic_doc, post, page])
            link = self._link_fields(link_doc, meta)
            related_ctx = ""
            if link_doc:
                related_ctx = self._esc(self._truncate(
                    link_doc.get("content", ""), 1400))
            else:
                related_ctx = "Материал 2КАД по этой теме не найден."
            law_cats = ", ".join(
                (act_doc or {}).get("category_names") or []) or "Нормативные акты"
            prompt = LAW_PROMPT.format(
                law_title=self._esc((act_doc or {}).get("title", "")),
                law_date=(act_doc or {}).get("date", ""),
                law_category=self._esc(law_cats),
                law_content=self._esc(self._truncate(
                    (act_doc or {}).get("content", ""), 1800)),
                related_context=related_ctx,
                phone=meta["phone"],
                **link,
            )
            return RoutingDecision(
                mode="law",
                page=act_doc,
                system_prompt=prompt,
                found_title=(act_doc or {}).get("title", ""),
                found_url=(act_doc or {}).get("url", ""),
                source_kind="law",
                link_doc=link_doc,
                link_title=link["link_title"],
                link_url=link["link_url"],
                law_doc=act_doc,
            )

        # ---------- 5. Статья блога ----------
        if post and s_post >= s_page:
            link_doc = self._best_link([post, topic_doc]) or post
            link = self._link_fields(link_doc, meta)
            cats = ", ".join(post.get("category_names") or []) or "Блог"
            prompt = ARTICLE_PROMPT.format(
                post_title=self._esc(post.get("title", "")),
                post_url=post.get("url", ""),
                post_content=self._esc(self._truncate(post.get("content", ""), 1600)),
                post_category=self._esc(cats),
                post_date=post.get("date", ""),
                phone=meta["phone"],
                **link,
            )
            return RoutingDecision(
                mode="article",
                page=post,
                system_prompt=prompt,
                found_title=post.get("title", ""),
                found_url=post.get("url", ""),
                source_kind="blog",
                link_doc=link_doc,
                link_title=link["link_title"],
                link_url=link["link_url"],
            )

        # ---------- 6. Страница услуги ----------
        if page:
            link_doc = self._best_link([topic_doc, page]) or page
            link = self._link_fields(link_doc, meta)
            prompt = SERVICE_PROMPT.format(
                page_title=self._esc(page.get("title", "")),
                page_url=page.get("url", ""),
                page_content=self._esc(self._truncate(page.get("content", ""), 1600)),
                phone=meta["phone"],
                **link,
            )
            return RoutingDecision(
                mode="service",
                page=page,
                system_prompt=prompt,
                found_title=page.get("title", ""),
                found_url=page.get("url", ""),
                source_kind="page",
                link_doc=link_doc,
                link_title=link["link_title"],
                link_url=link["link_url"],
            )

        # ---------- 7. Ничего не нашли ----------
        services = self.idx.get("kadastrovye-raboty-dlya-chastnyh-klientov")
        link_doc = self._best_link([post, page]) or services
        link = self._link_fields(link_doc, meta)
        prompt = NO_ANSWER_PROMPT.format(
            main_url=meta["main_url"],
            email=meta["email"],
            phone=meta["phone"],
        )
        return RoutingDecision(
            mode="no_answer",
            page=None,
            system_prompt=prompt,
            found_title="",
            found_url="",
            source_kind="",
            link_doc=link_doc,
            link_title=link["link_title"],
            link_url=link["link_url"],
        )
