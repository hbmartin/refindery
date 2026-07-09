"""QueryLogReader over the observability DuckDB file, opened read-only.

The sink opens its connection per batch and CHECKPOINTs on close exactly so
this reader can attach ``read_only=True`` between appends — scoring never
takes a write lock and never contends with a running server.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import duckdb

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
        """All logged runs, oldest first; optionally bounded below by ts."""
        with duckdb.connect(str(self._path), read_only=True) as conn:
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

    def read_labels(self) -> dict[QueryId, dict[PageId, bool]]:
        """Feedback labels per query; rows are ts-ordered so the latest wins."""
        with duckdb.connect(str(self._path), read_only=True) as conn:
            rows = conn.execute(_LABELS_SQL).fetchall()
        labels: dict[QueryId, dict[PageId, bool]] = {}
        for query_id, page_id, relevant in rows:
            labels.setdefault(QueryId(query_id), {})[PageId(page_id)] = relevant
        return labels
