"""Query log port: the eval substrate.

Every search logs its full pre-rerank candidate set and both retrieval arms,
so reranker lift and per-arm contribution can be measured offline without
re-running retrieval. Logging is non-blocking by contract — implementations
buffer and must never stall the query path.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from refindery.domain.ids import ChunkId, PageId, QueryId


@dataclass(frozen=True, slots=True)
class LoggedHit:
    """One scored chunk hit as logged."""

    chunk_id: ChunkId
    page_id: PageId
    score: float


@dataclass(frozen=True, slots=True)
class LoggedPage:
    """One final ranked page as logged."""

    page_id: PageId
    score: float
    rank: int


@dataclass(frozen=True, slots=True)
class QueryLogRecord:
    """One /search execution (or one /compare arm)."""

    query_id: QueryId
    ts: datetime
    kind: str  # "search" | "compare_arm"
    query_text: str
    params: dict[str, object]
    active_model: str
    reranker_model: str | None
    candidate_set: tuple[LoggedHit, ...]
    dense_hits: tuple[LoggedHit, ...]
    sparse_hits: tuple[LoggedHit, ...]
    final_pages: tuple[LoggedPage, ...]
    timing_ms: dict[str, float]
    compare_id: str | None = None


@dataclass(frozen=True, slots=True)
class FeedbackRecord:
    """Relevance feedback joined to the log at eval time."""

    query_id: QueryId
    page_id: PageId
    relevant: bool
    ts: datetime = field(compare=False, kw_only=True)


class QueryLogSink(Protocol):
    """Non-blocking, append-only query log."""

    def log_query(self, record: QueryLogRecord) -> None:
        """Buffer a query record; never blocks."""
        ...

    def log_feedback(self, record: FeedbackRecord) -> None:
        """Buffer a feedback record; never blocks."""
        ...
