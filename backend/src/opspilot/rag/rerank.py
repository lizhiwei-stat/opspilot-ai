"""Pluggable rerankers. A reranker scores (query, passage) PAIRS jointly, unlike an embedding
model which encodes query and passage independently (bi-encoder). Stage 2 ships a CPU-only
lexical reranker; a cross-encoder can implement the same Protocol later."""

from __future__ import annotations

import math
from collections import Counter
from typing import Protocol

from opspilot.rag.tokenize import query_tokens, tokenize


class Reranker(Protocol):
    name: str

    def score(self, query: str, passages: list[str]) -> list[float]: ...


class NoopReranker:
    name = "none"

    def score(self, query: str, passages: list[str]) -> list[float]:
        return [0.0] * len(passages)


class LexicalOverlapReranker:
    """IDF-weighted query-term coverage over the candidate set, in [0, 1].

    coverage = Σ idf(t) for query terms t present in the passage / Σ idf(t) over all query
    terms, plus a small bonus for code-like exact identifiers. Deterministic, ~sub-ms per pair.
    It is a *lexical* signal: it cannot recognise paraphrases with no shared terms.
    """

    name = "lexical-overlap-v1"

    def score(self, query: str, passages: list[str]) -> list[float]:
        q = query_tokens(query)
        if not q or not passages:
            return [0.0] * len(passages)
        toks = [set(tokenize(p)) for p in passages]
        n = len(passages)
        df = Counter(t for s in toks for t in set(q) & s)
        idf = {t: math.log(1 + (n + 1) / (df.get(t, 0) + 0.5)) for t in q}
        total = sum(idf.values())
        codes = [t for t in q if any(c.isdigit() for c in t) and len(t) >= 4]
        out = []
        for s in toks:
            cov = sum(idf[t] for t in q if t in s) / total
            bonus = 0.1 * sum(1 for c in codes if c in s) / max(1, len(codes)) if codes else 0.0
            out.append(round(min(1.0, cov + bonus), 6))
        return out


def coverage(query: str, passage: str) -> float:
    """Unweighted share of query terms found in passage (used as QA evidence signal)."""
    q = query_tokens(query)
    if not q:
        return 0.0
    s = set(tokenize(passage))
    return sum(1 for t in q if t in s) / len(q)
