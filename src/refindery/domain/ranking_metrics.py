"""Ranking metrics: agreement stats for /compare, relevance metrics for eval.

Hand-implemented (small, pure, well-defined): no maintained PyPI RBO exists,
and scipy's tau would be a heavy import for fifteen lines. RBO follows the
extrapolated form of Webber, Moffat & Zobel (2010), eq. 32. Relevance
metrics (nDCG, MRR, recall) use binary gains, matching the boolean
feedback labels they are scored against.
"""

import math
from collections.abc import Sequence
from collections.abc import Set as AbstractSet


def _ranked_items(
    ranked: Sequence[str], absolute_ranks: Sequence[int] | None
) -> list[tuple[int, str]]:
    if absolute_ranks is None:
        return list(enumerate(ranked, start=1))
    if len(ranked) != len(absolute_ranks):
        msg = "ranked items and absolute ranks must have equal lengths"
        raise ValueError(msg)
    if any(rank < 1 for rank in absolute_ranks):
        msg = "absolute ranks must be positive"
        raise ValueError(msg)
    return list(zip(absolute_ranks, ranked, strict=True))


def jaccard_at_k(a: Sequence[str], b: Sequence[str], k: int) -> float:
    """Set overlap of the top-k items; both-empty is defined as 1.0."""
    set_a, set_b = set(a[:k]), set(b[:k])
    if not set_a and not set_b:
        return 1.0
    return len(set_a & set_b) / len(set_a | set_b)


def rbo_ext(a: Sequence[str], b: Sequence[str], p: float = 0.9) -> float:
    """Extrapolated rank-biased overlap for finite ranked lists."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    shorter, longer = sorted((list(a), list(b)), key=len)
    s, big_l = len(shorter), len(longer)

    seen_short: set[str] = set()
    seen_long: set[str] = set()
    x_at: dict[int, int] = {}
    overlap = 0
    for depth in range(1, big_l + 1):
        if depth <= s:
            item = shorter[depth - 1]
            if item in seen_long:
                overlap += 1
            seen_short.add(item)
        item = longer[depth - 1]
        if item in seen_short and item not in seen_long:
            overlap += 1
        seen_long.add(item)
        x_at[depth] = overlap

    x_s = x_at[s]
    x_l = x_at[big_l]
    total = sum((x_at[d] / d) * p**d for d in range(1, big_l + 1))
    total += sum((x_s * (d - s) / (s * d)) * p**d for d in range(s + 1, big_l + 1))
    tail = ((x_l - x_s) / big_l + x_s / s) * p**big_l
    return (1 - p) / p * total + tail


def kendall_tau_intersection(a: Sequence[str], b: Sequence[str]) -> float | None:
    """Tau-a over the ordered intersection; None when it has < 2 items.

    Restricted to the intersection, both rankings are tie-free permutations,
    so plain pair counting suffices (O(n^2), trivial at n <= 100).
    """
    common = [item for item in a if item in set(b)]
    if len(common) < 2:
        return None
    rank_b = {item: i for i, item in enumerate(b) if item in set(common)}
    concordant = 0
    discordant = 0
    for i in range(len(common)):
        for j in range(i + 1, len(common)):
            if rank_b[common[i]] < rank_b[common[j]]:
                concordant += 1
            else:
                discordant += 1
    total = concordant + discordant
    return (concordant - discordant) / total


def ndcg_at_k(
    ranked: Sequence[str],
    relevant: AbstractSet[str],
    k: int,
    *,
    absolute_ranks: Sequence[int] | None = None,
) -> float | None:
    """Binary-gain nDCG@k; None when there are no relevant items.

    DCG uses the 1/log2(rank + 1) discount; the ideal DCG places
    min(len(relevant), k) relevant items at the top. ``absolute_ranks``
    preserves stored positions for a paginated ranking slice.
    """
    if not relevant:
        return None
    dcg = sum(
        1.0 / math.log2(rank + 1)
        for rank, item in _ranked_items(ranked, absolute_ranks)
        if rank <= k and item in relevant
    )
    ideal = sum(
        1.0 / math.log2(rank + 1) for rank in range(1, min(len(relevant), k) + 1)
    )
    return dcg / ideal


def reciprocal_rank(
    ranked: Sequence[str],
    relevant: AbstractSet[str],
    *,
    absolute_ranks: Sequence[int] | None = None,
) -> float:
    """1/rank of the first relevant item; optionally use stored positions."""
    matches = [
        rank for rank, item in _ranked_items(ranked, absolute_ranks) if item in relevant
    ]
    return 0.0 if not matches else 1.0 / min(matches)


def recall_at_k(
    ranked: Sequence[str],
    relevant: AbstractSet[str],
    k: int,
    *,
    absolute_ranks: Sequence[int] | None = None,
) -> float | None:
    """Relevant fraction in top k, optionally using stored absolute positions."""
    if not relevant:
        return None
    found = sum(
        1
        for rank, item in _ranked_items(ranked, absolute_ranks)
        if rank <= k and item in relevant
    )
    return found / len(relevant)
