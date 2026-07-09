"""QueryLogSink implementation over the DuckDB sink.

Native ``LIST<STRUCT>`` columns keep the log SQL-queryable (``UNNEST`` the
candidate set to measure reranker lift offline). Feedback is a separate
append-only table joined at read time — never an UPDATE on a log.
"""

import json

from refindery.adapters.observability.duckdb_sink import DuckDbSink, TableSpec
from refindery.application.ports.query_log import (
    FeedbackRecord,
    LoggedHit,
    QueryLogRecord,
)

QUERY_LOG_DDL = """
CREATE TABLE IF NOT EXISTS query_log (
  query_id VARCHAR NOT NULL,
  ts TIMESTAMPTZ NOT NULL,
  kind VARCHAR NOT NULL,
  compare_id VARCHAR,
  query_text VARCHAR NOT NULL,
  params JSON NOT NULL,
  active_model VARCHAR NOT NULL,
  reranker_model VARCHAR,
  candidate_set STRUCT(chunk_id VARCHAR, page_id VARCHAR, score DOUBLE)[],
  dense_hits STRUCT(chunk_id VARCHAR, page_id VARCHAR, score DOUBLE)[],
  sparse_hits STRUCT(chunk_id VARCHAR, page_id VARCHAR, score DOUBLE)[],
  final_pages STRUCT(page_id VARCHAR, score DOUBLE, rank INTEGER)[],
  timing_ms JSON NOT NULL
);
CREATE TABLE IF NOT EXISTS feedback (
  query_id VARCHAR NOT NULL,
  page_id VARCHAR NOT NULL,
  relevant BOOLEAN NOT NULL,
  ts TIMESTAMPTZ NOT NULL
);
CREATE OR REPLACE VIEW query_log_with_feedback AS
  SELECT q.*, f.page_id AS fb_page_id, f.relevant AS fb_relevant
  FROM query_log q LEFT JOIN feedback f USING (query_id);
"""

_QUERY_COLUMNS = (
    "query_id",
    "ts",
    "kind",
    "compare_id",
    "query_text",
    "params",
    "active_model",
    "reranker_model",
    "candidate_set",
    "dense_hits",
    "sparse_hits",
    "final_pages",
    "timing_ms",
)
_FEEDBACK_COLUMNS = ("query_id", "page_id", "relevant", "ts")


def _hits(hits: tuple[LoggedHit, ...]) -> list[dict[str, object]]:
    return [
        {"chunk_id": h.chunk_id, "page_id": h.page_id, "score": h.score} for h in hits
    ]


class DuckDbQueryLog:
    """QueryLogSink over a shared DuckDbSink."""

    def __init__(self, sink: DuckDbSink) -> None:
        self._sink = sink
        sink.register_table(
            TableSpec(name="query_log", ddl=QUERY_LOG_DDL, columns=_QUERY_COLUMNS)
        )
        sink.register_table(
            TableSpec(name="feedback", ddl="", columns=_FEEDBACK_COLUMNS)
        )

    def log_query(self, record: QueryLogRecord) -> None:
        """Buffer one query record."""
        self._sink.append(
            "query_log",
            (
                record.query_id,
                record.ts,
                record.kind,
                record.compare_id,
                record.query_text,
                json.dumps(record.params, default=str),
                record.active_model,
                record.reranker_model,
                _hits(record.candidate_set),
                _hits(record.dense_hits),
                _hits(record.sparse_hits),
                [
                    {"page_id": p.page_id, "score": p.score, "rank": p.rank}
                    for p in record.final_pages
                ],
                json.dumps(record.timing_ms),
            ),
        )

    def log_feedback(self, record: FeedbackRecord) -> None:
        """Buffer one feedback record."""
        self._sink.append(
            "feedback",
            (record.query_id, record.page_id, record.relevant, record.ts),
        )
