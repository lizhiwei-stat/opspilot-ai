"""Reciprocal Rank Fusion (Cormack et al., 2009): score(d) = Σ_m 1 / (k + rank_m(d)).
Ranks are 1-based; documents missing from a list contribute nothing for that list."""

from __future__ import annotations

from collections.abc import Hashable, Sequence


def rrf(rankings: Sequence[Sequence[Hashable]], k: int = 60) -> list[tuple[Hashable, float]]:
    scores: dict[Hashable, float] = {}
    first_seen: dict[Hashable, int] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
            first_seen.setdefault(item, len(first_seen))
    return sorted(scores.items(), key=lambda kv: (-kv[1], first_seen[kv[0]]))
