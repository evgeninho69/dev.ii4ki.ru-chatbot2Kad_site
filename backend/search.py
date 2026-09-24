# -*- coding: utf-8 -*-
"""Простой keyword-based поиск по индексу страниц.

Для MVP хватает: BM25-lite (TF * IDF) + учёт заголовка и slug.
Без зависимостей (только stdlib + json/regex).
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple


WORD_RE = re.compile(r"[а-яёa-z0-9]+", re.IGNORECASE)
STOP = set("""
и в на на у не что это как но он она они мы вы от до за по для из с о
а то есть во быть был была были был был быть да нет уже еще или если
к ж из-за при про без над под между также ещё более менее очень
этот эту этом этот то том тот этот эта этим той тех этих такие такой
""".split())


def tokenize(text: str) -> List[str]:
    text = text.lower()
    tokens = WORD_RE.findall(text)
    return [t for t in tokens if t not in STOP and len(t) > 1]


def normalize_query(text: str) -> List[str]:
    return tokenize(text)


class SiteIndex:
    """Загружает site_index.json и предоставляет поиск."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data: Dict = json.loads(self.path.read_text(encoding="utf-8"))
        self.pages = self.data["pages"]
        # Построим индекс: page_id → {tokens, title_tokens, slug_tokens}
        self._tokens: Dict[int, List[str]] = {}
        self._title_tokens: Dict[int, List[str]] = {}
        self._slug_tokens: Dict[int, List[str]] = {}
        self._doc_freq: Dict[str, int] = {}
        self._doc_len: Dict[int, int] = {}

        all_tokens = []
        for p in self.pages:
            content_tokens = tokenize(p["content"])
            title_tokens = tokenize(p["title"])
            slug_tokens = p["slug"].replace("-", " ").split()
            pid = p["id"]
            self._tokens[pid] = content_tokens
            self._title_tokens[pid] = title_tokens
            self._slug_tokens[pid] = slug_tokens
            self._doc_len[pid] = len(content_tokens)
            all_tokens.extend(content_tokens)

        # doc frequency
        for pid, toks in self._tokens.items():
            for t in set(toks):
                self._doc_freq[t] = self._doc_freq.get(t, 0) + 1

        self._avg_dl = (
            sum(self._doc_len.values()) / max(1, len(self._doc_len))
        )

    def _bm25(self, query_tokens: List[str], pid: int) -> float:
        # BM25 параметры (стандартные)
        k1 = 1.5
        b = 0.75
        score = 0.0
        doc_tokens = self._tokens[pid]
        doc_len = self._doc_len[pid]
        N = len(self._tokens)
        if not doc_tokens:
            return 0.0

        # tf по каждому токену в документе
        tf = {}
        for t in doc_tokens:
            tf[t] = tf.get(t, 0) + 1

        title_tf = {}
        for t in self._title_tokens[pid]:
            title_tf[t] = title_tf.get(t, 0) + 1
        slug_tf = {}
        for t in self._slug_tokens[pid]:
            slug_tf[t] = slug_tf.get(t, 0) + 1

        for qt in query_tokens:
            df = self._doc_freq.get(qt, 0)
            if df == 0:
                continue
            idf = math.log(1 + (N - df + 0.5) / (df + 0.5))
            tf_d = tf.get(qt, 0)
            tf_t = title_tf.get(qt, 0)
            tf_s = slug_tf.get(qt, 0)

            # BM25 по контенту
            num = tf_d * (k1 + 1)
            den = tf_d + k1 * (1 - b + b * doc_len / self._avg_dl)
            score += idf * num / den if den else 0

            # Бусты за заголовок и slug
            score += tf_t * 3.0
            score += tf_s * 2.0

        return score

    def search(self, query: str, top_k: int = 3) -> List[Tuple[Dict, float]]:
        """Возвращает список (page, score), top_k лучших."""
        q_tokens = normalize_query(query)
        if not q_tokens:
            return []
        scored = []
        for p in self.pages:
            s = self._bm25(q_tokens, p["id"])
            if s > 0:
                scored.append((p, s))
        scored.sort(key=lambda x: -x[1])
        return scored[:top_k]

    def find_service_page(self, query: str) -> Optional[Dict]:
        """Находит наиболее подходящую страницу услуги."""
        results = self.search(query, top_k=1)
        if results and results[0][1] > 1.5:
            return results[0][0]
        return None

    def get(self, slug: str) -> Optional[Dict]:
        for p in self.pages:
            if p["slug"] == slug:
                return p
        return None


if __name__ == "__main__":
    # CLI-тест
    import sys
    idx_path = Path(__file__).parent / "data" / "site_index.json"
    idx = SiteIndex(idx_path)
    print(f"Loaded {len(idx.pages)} pages\n")
    while True:
        try:
            q = input("QUERY> ").strip()
        except EOFError:
            break
        if not q:
            continue
        if q in ("exit", "quit"):
            break
        results = idx.search(q, top_k=3)
        if not results:
            print("  (no results)")
            continue
        for p, s in results:
            print(f"  {s:6.2f}  {p['url']}")
            print(f"         {p['title']}")
        print()