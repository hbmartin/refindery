"""Read side of the query log: the offline eval substrate.

The sink (``QueryLogSink``) is write-only by design; eval opens the log
separately and read-only, so scoring never contends with the serving path.
Rows are projected down to page-level rankings — feedback labels are
page-level, so pages are the unit every metric is computed over.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from refindery.domain.ids import PageId, QueryId


@dataclass(frozen=True, slots=True)
class LoggedRun:
    """One query_log row projected to page-level rankings.

    ``final_page_ids`` is the ranking the user actually saw (post-rerank,
    post-rollup, exact matches pinned). ``prererank_page_ids`` is the
    first-occurrence page order of the fused candidate set — under the
    default max rollup this is the ranking rerank-off would have produced,
    modulo exact-match pins and recency decay.
    """

    query_id: QueryId
    ts: datetime
    kind: str  # "search" | "compare_arm"
    query_text: str
    params: dict[str, object]
    active_model: str
    reranker_model: str | None
    final_page_ids: tuple[PageId, ...]
    prererank_page_ids: tuple[PageId, ...]


class QueryLogReader(Protocol):
    """Read-only access to logged runs and feedback labels."""

    def read_runs(self, *, since: datetime | None = None) -> list[LoggedRun]:
        """All logged runs, oldest first; optionally bounded below by ts."""
        ...

    def read_labels(self) -> dict[QueryId, dict[PageId, bool]]:
        """Feedback labels per query; the latest label per page wins."""
        ...
