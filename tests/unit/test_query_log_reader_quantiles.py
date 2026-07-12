"""Latency quantiles over the query log (seeded DuckDB file, no server)."""

from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest

from refindery.adapters.observability.query_log import QUERY_LOG_DDL
from refindery.adapters.observability.query_log_reader import DuckDbQueryLogReader


def _seed(db_path: Path, totals_ms: list[float], *, kind: str = "search") -> None:
    with duckdb.connect(str(db_path)) as conn:
        conn.execute(QUERY_LOG_DDL)
        for index, total in enumerate(totals_ms):
            conn.execute(
                """
                INSERT INTO query_log (
                  query_id, ts, kind, query_text, params, active_model, timing_ms
                ) VALUES (
                  ?, TIMESTAMPTZ '2026-07-01 04:00:00+00', ?, 'q', '{}', 'm', ?
                )
                """,
                [f"query-{kind}-{index}", kind, f'{{"total": {total}}}'],
            )


def test_quantiles_over_seeded_runs(tmp_path):
    db = tmp_path / "obs.duckdb"
    _seed(db, [100.0, 200.0, 300.0])
    got = DuckDbQueryLogReader(db).read_latency_quantiles()
    assert got is not None
    assert got.runs == 3
    assert got.p50_ms == pytest.approx(200.0)
    assert got.p50_ms <= got.p95_ms <= 300.0


def test_missing_kind_returns_none(tmp_path):
    db = tmp_path / "obs.duckdb"
    _seed(db, [100.0], kind="compare")
    assert DuckDbQueryLogReader(db).read_latency_quantiles(kind="search") is None


def test_empty_log_returns_none(tmp_path):
    db = tmp_path / "obs.duckdb"
    _seed(db, [])
    assert DuckDbQueryLogReader(db).read_latency_quantiles() is None


def test_naive_since_treated_as_utc_and_bounds_window(tmp_path):
    db = tmp_path / "obs.duckdb"
    _seed(db, [100.0])
    reader = DuckDbQueryLogReader(db)
    before = datetime(2026, 6, 1)  # noqa: DTZ001 — intentionally naive
    after = datetime(2026, 8, 1, tzinfo=UTC)
    assert reader.read_latency_quantiles(since=before) is not None
    assert reader.read_latency_quantiles(since=after) is None
