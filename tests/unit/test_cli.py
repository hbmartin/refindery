"""CLI dispatch and eval-score smoke tests."""

import json
from datetime import UTC, datetime

import pytest

from refindery.adapters.observability.duckdb_sink import DuckDbSink
from refindery.adapters.observability.query_log import DuckDbQueryLog
from refindery.application.ports.query_log import (
    FeedbackRecord,
    LoggedHit,
    LoggedPage,
    QueryLogRecord,
)
from refindery.cli import main
from refindery.domain.ids import ChunkId, PageId, QueryId

TS = datetime(2026, 7, 1, tzinfo=UTC)


def _seed_log(db_path) -> None:
    sink = DuckDbSink(db_path)
    log = DuckDbQueryLog(sink)
    sink.start()
    hit = LoggedHit(chunk_id=ChunkId("c1"), page_id=PageId("p1"), score=0.9)
    log.log_query(
        QueryLogRecord(
            query_id=QueryId("q1"),
            ts=TS,
            kind="search",
            query_text="hexagonal ports",
            params={"k": 10, "rollup": "max"},
            active_model="fake-model",
            reranker_model="fake-reranker",
            candidate_set=(hit,),
            dense_hits=(hit,),
            sparse_hits=(hit,),
            final_pages=(LoggedPage(page_id=PageId("p1"), score=0.9, rank=1),),
            timing_ms={"total": 1.0},
        )
    )
    log.log_feedback(
        FeedbackRecord(
            query_id=QueryId("q1"), page_id=PageId("p1"), relevant=True, ts=TS
        )
    )
    sink.close()


def test_eval_score_prints_table_and_writes_json(tmp_path, capsys):
    db = tmp_path / "obs.duckdb"
    _seed_log(db)
    out = tmp_path / "report.json"

    main(["eval", "score", "--db", str(db), "--json", str(out)])

    printed = capsys.readouterr().out
    assert "1 labeled, 1 scored" in printed
    assert "fake-model" in printed
    report = json.loads(out.read_text())
    assert report["scored"] == 1
    assert report["models"][0]["ndcg"] == pytest.approx(1.0)


def test_eval_score_missing_db_exits_cleanly(tmp_path):
    with pytest.raises(SystemExit, match="query log not found"):
        main(["eval", "score", "--db", str(tmp_path / "missing.duckdb")])


def test_no_subcommand_serves(monkeypatch, tmp_path):
    calls: dict[str, str | int] = {}

    def fake_run(app: object, *, host: str, port: int, log_level: str) -> None:
        calls["host"] = host
        calls["port"] = port

    monkeypatch.setattr("uvicorn.run", fake_run)
    monkeypatch.setattr("refindery.api.app.create_app", lambda settings: "app")
    from tests.fakes.container import make_test_settings

    monkeypatch.setattr(
        "refindery.config.load_settings", lambda: make_test_settings(tmp_path)
    )

    main([])

    assert calls == {"host": "127.0.0.1", "port": 8000}
