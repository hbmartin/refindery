"""Pure retrieval primitives shared by all vector-store adapters.

Fusion is deliberately client-side in every adapter (Qdrant's server-side RRF
exposes no ``k`` parameter, and the query log needs both arms anyway), so this
one function defines hybrid fusion for the whole system. The conformance suite
asserts every adapter's fused output equals ``rrf_fuse`` of its arms.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from refindery.domain.ids import ChunkId, PageId


@dataclass(frozen=True, slots=True)
class ChunkHit:
    """A scored chunk reference returned by a vector store arm."""

    chunk_id: ChunkId
    page_id: PageId
    ordinal: int
    score: float


class RollupStrategy(StrEnum):
    """How chunk scores pool into a page score.

    No magic-number bonuses in v1: rerankers are calibrated, and arbitrary
    boosts would destroy their ranking. A bonus variant may be added later
    only if eval proves lift.
    """

    MAX = "max"
    MEAN_TOP_M = "mean_top_m"
    SUM_RRF = "sum_rrf"


@dataclass(frozen=True, slots=True)
class ScoredChunk:
    """A candidate chunk carrying both fusion and (optional) rerank scores."""

    chunk_id: ChunkId
    page_id: PageId
    ordinal: int
    fusion_score: float
    rerank_score: float | None = None

    @property
    def effective_score(self) -> float:
        """Rerank score when present, fusion score otherwise."""
        return self.fusion_score if self.rerank_score is None else self.rerank_score


@dataclass(frozen=True, slots=True)
class PageScore:
    """A page's rolled-up score plus its matching chunks, best first."""

    page_id: PageId
    score: float
    chunks: tuple[ScoredChunk, ...]


def rollup_pages(
    *,
    chunks: Sequence[ScoredChunk],
    strategy: RollupStrategy = RollupStrategy.MAX,
    top_m: int = 3,
) -> list[PageScore]:
    """Roll chunk scores up to ranked pages.

    ``sum_rrf`` orders pages by summed fusion scores (the reranker then only
    affects chunk display order); the other strategies use effective scores.
    """
    by_page: dict[PageId, list[ScoredChunk]] = {}
    for chunk in chunks:
        by_page.setdefault(chunk.page_id, []).append(chunk)

    pages: list[PageScore] = []
    for page_id, page_chunks in by_page.items():
        ordered = sorted(page_chunks, key=lambda c: (-c.effective_score, c.chunk_id))
        match strategy:
            case RollupStrategy.MAX:
                score = ordered[0].effective_score
            case RollupStrategy.MEAN_TOP_M:
                head = ordered[:top_m]
                score = sum(c.effective_score for c in head) / len(head)
            case RollupStrategy.SUM_RRF:
                score = sum(c.fusion_score for c in page_chunks)
        pages.append(PageScore(page_id=page_id, score=score, chunks=tuple(ordered)))
    return sorted(pages, key=lambda p: (-p.score, p.page_id))


def apply_recency_decay(
    pages: Sequence[PageScore],
    *,
    first_seen: dict[PageId, datetime],
    now: datetime,
    half_life_days: float,
) -> list[PageScore]:
    """Decay page scores by age: ``score * 0.5 ** (age_days / half_life)``."""
    decayed: list[PageScore] = []
    for page in pages:
        seen = first_seen.get(page.page_id)
        if seen is None:
            decayed.append(page)
            continue
        age_days = max((now - seen).total_seconds(), 0.0) / 86_400.0
        factor = 0.5 ** (age_days / half_life_days)
        decayed.append(
            PageScore(
                page_id=page.page_id,
                score=page.score * factor,
                chunks=page.chunks,
            )
        )
    return sorted(decayed, key=lambda p: (-p.score, p.page_id))


def rrf_fuse(
    *,
    dense: Sequence[ChunkHit],
    sparse: Sequence[ChunkHit],
    k: int = 60,
) -> list[ChunkHit]:
    """Reciprocal-rank-fuse two ranked arms into one ranked list.

    Score per chunk is ``sum(1 / (k + rank))`` over the arms it appears in
    (rank is 1-based). Ties break on chunk_id for determinism.
    """
    scores: dict[ChunkId, float] = {}
    meta: dict[ChunkId, ChunkHit] = {}
    for arm in (dense, sparse):
        for rank, hit in enumerate(arm, start=1):
            scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (k + rank)
            meta.setdefault(hit.chunk_id, hit)
    ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    return [
        ChunkHit(
            chunk_id=chunk_id,
            page_id=meta[chunk_id].page_id,
            ordinal=meta[chunk_id].ordinal,
            score=score,
        )
        for chunk_id, score in ordered
    ]
