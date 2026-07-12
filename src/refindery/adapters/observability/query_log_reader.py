"""QueryLogReader over the observability DuckDB file, opened read-only.

The sink opens its connection per batch and CHECKPOINTs on close exactly so
this reader can attach ``read_only=True`` between appends — scoring never
takes a write lock and never contends with a running server.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from refindery.adapters.observability.duckdb_sink import open_read_only
from refindery.application.ports.query_log_reader import LoggedRun
from refindery.domain.ids import PageId, QueryId

# ts is fetched as epoch micros: fetching TIMESTAMPTZ values directly would
# require pytz, which is not a dependency.
_RUNS_SQL = """
SELECT
  query_id,
  epoch_us(ts),
  kind,
  query_text,
  params,
  active_model,
  reranker_model,
  list_transform(final_pages, p -> p.page_id) AS final_page_ids,
  list_transform(final_pages, p -> p.rank) AS final_page_ranks,
  list_transform(candidate_set, c -> c.page_id) AS candidate_page_ids
FROM query_log
WHERE ts >= coalesce(?, ts)
ORDER BY ts
"""

_LABELS_SQL = """
SELECT query_id, page_id, relevant
FROM feedback
ORDER BY ts
"""

# timing_ms is a JSON column; ->> extracts the total as text.
_QUANTILES_SQL = """
SELECT
  COUNT(*),
  quantile_cont(CAST(timing_ms ->> '$.total' AS DOUBLE), 0.50),
  quantile_cont(CAST(timing_ms ->> '$.total' AS DOUBLE), 0.95)
FROM query_log
WHERE ts >= coalesce(?, ts) AND kind = ?
"""

_DETAIL_SQL = """
SELECT query_id, epoch_us(ts), kind, compare_id, query_text, params,
       active_model, reranker_model, candidate_set, dense_hits, sparse_hits,
       final_pages, timing_ms
FROM query_log
WHERE ts >= coalesce(?, ts)
  AND kind = coalesce(?, kind)
  AND query_id = coalesce(?, query_id)
ORDER BY ts DESC
LIMIT ?
"""


@dataclass(frozen=True, slots=True)
class LatencyQuantiles:
    """Latency quantiles over logged runs of one kind."""

    runs: int
    p50_ms: float
    p95_ms: float


@dataclass(frozen=True, slots=True)
class DetailedLoggedRun:
    """Full query-log row for the administration API."""

    query_id: str
    ts: datetime
    kind: str
    compare_id: str | None
    query_text: str
    params: dict[str, object]
    active_model: str
    reranker_model: str | None
    candidate_set: list[dict[str, object]]
    dense_hits: list[dict[str, object]]
    sparse_hits: list[dict[str, object]]
    final_pages: list[dict[str, object]]
    timing_ms: dict[str, float]
    feedback: dict[str, bool]


def _first_occurrence(page_ids: list[str]) -> tuple[PageId, ...]:
    """Candidate pages in fused-score order, deduped to first occurrence.

    Under the default max rollup this is the page ranking the pipeline
    would have produced with rerank off (modulo exact-match pins and
    recency decay).
    """
    return tuple(dict.fromkeys(PageId(p) for p in page_ids))


class DuckDbQueryLogReader:
    """Read-only QueryLogReader for one observability DuckDB file."""

    def __init__(self, path: Path) -> None:
        if not path.is_file():
            msg = f"query log not found: {path} (has the server logged any queries?)"
            raise FileNotFoundError(msg)
        self._path = path

    def read_runs(self, *, since: datetime | None = None) -> list[LoggedRun]:
        """All logged runs, oldest first; a timezone-naive lower bound is UTC."""
        if since is not None and since.tzinfo is None:
            since = since.replace(tzinfo=UTC)
        with open_read_only(self._path) as conn:
            rows = conn.execute(_RUNS_SQL, [since]).fetchall()
        return [
            LoggedRun(
                query_id=QueryId(query_id),
                ts=datetime.fromtimestamp(ts_us / 1_000_000, tz=UTC),
                kind=kind,
                query_text=query_text,
                params=json.loads(params),
                active_model=active_model,
                reranker_model=reranker_model,
                final_page_ids=tuple(PageId(p) for p in final_page_ids or []),
                final_page_ranks=tuple(int(rank) for rank in final_page_ranks or []),
                prererank_page_ids=_first_occurrence(candidate_page_ids or []),
            )
            for (
                query_id,
                ts_us,
                kind,
                query_text,
                params,
                active_model,
                reranker_model,
                final_page_ids,
                final_page_ranks,
                candidate_page_ids,
            ) in rows
        ]

    def read_latency_quantiles(
        self, *, since: datetime | None = None, kind: str = "search"
    ) -> LatencyQuantiles | None:
        """p50/p95 of total timing over runs of one kind; None when no rows."""
        if since is not None and since.tzinfo is None:
            since = since.replace(tzinfo=UTC)
        with open_read_only(self._path) as conn:
            row = conn.execute(_QUANTILES_SQL, [since, kind]).fetchone()
        if row is None or not row[0] or row[1] is None or row[2] is None:
            return None
        return LatencyQuantiles(
            runs=int(row[0]), p50_ms=float(row[1]), p95_ms=float(row[2])
        )

    def read_labels(self) -> dict[QueryId, dict[PageId, bool]]:
        """Feedback labels per query; rows are ts-ordered so the latest wins."""
        with open_read_only(self._path) as conn:
            rows = conn.execute(_LABELS_SQL).fetchall()
        labels: dict[QueryId, dict[PageId, bool]] = {}
        for query_id, page_id, relevant in rows:
            labels.setdefault(QueryId(query_id), {})[PageId(page_id)] = relevant
        return labels

    def read_detailed_runs(
        self,
        *,
        since: datetime | None = None,
        kind: str | None = None,
        query_id: str | None = None,
        limit: int = 100,
    ) -> list[DetailedLoggedRun]:
        """Read full log rows newest first with latest feedback labels."""
        if since is not None and since.tzinfo is None:
            since = since.replace(tzinfo=UTC)
        with open_read_only(self._path) as conn:
            rows = conn.execute(_DETAIL_SQL, [since, kind, query_id, limit]).fetchall()
        labels = self.read_labels()
        return [
            DetailedLoggedRun(
                query_id=row[0],
                ts=datetime.fromtimestamp(row[1] / 1_000_000, tz=UTC),
                kind=row[2],
                compare_id=row[3],
                query_text=row[4],
                params=json.loads(row[5]),
                active_model=row[6],
                reranker_model=row[7],
                candidate_set=_structs(row[8]),
                dense_hits=_structs(row[9]),
                sparse_hits=_structs(row[10]),
                final_pages=_structs(row[11]),
                timing_ms=json.loads(row[12]),
                feedback={
                    str(page_id): relevant
                    for page_id, relevant in labels.get(QueryId(row[0]), {}).items()
                },
            )
            for row in rows
        ]


def _structs(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [
        {str(key): item_value for key, item_value in item.items()}
        for item in value
        if isinstance(item, Mapping)
    ]
