"""End-to-end eval harness test: ingest, search, feedback, score, replay."""

import asyncio
import time
from pathlib import Path

import httpx
import pytest

from refindery.adapters.observability.query_log_reader import DuckDbQueryLogReader
from refindery.api.app import create_app
from refindery.application.services.eval_service import ArmSpec, EvalService
from tests.fakes.container import TEST_TOKEN, build_test_container, make_test_settings

AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}

PAGES = [
    {
        "url": "https://arch.example/hexagonal",
        "title": "Hexagonal Architecture",
        "body_extracted": (
            "Hexagonal architecture keeps domain logic pure. Ports and "
            "adapters isolate infrastructure so the core stays testable."
        ),
        "fetched_at": "2026-06-01T10:00:00Z",
    },
    {
        "url": "https://ml.example/clustering",
        "title": "Density Clustering",
        "body_extracted": (
            "HDBSCAN finds lumpy clusters of arbitrary shape and marks "
            "outliers as noise. UMAP reduces embeddings first."
        ),
        "fetched_at": "2026-06-05T10:00:00Z",
    },
]


@pytest.fixture
async def harness(tmp_path):
    container = build_test_container(tmp_path)
    app = create_app(make_test_settings(tmp_path), container=container)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as http:
            ids = []
            for body in PAGES:
                response = await http.post("/v1/pages", json=body, headers=AUTH)
                assert response.status_code == 202
                ids.append(response.json()["page_id"])
            async with asyncio.timeout(30):
                for page_id in ids:
                    while True:
                        got = await http.get(
                            f"/v1/pages/{page_id}/status", headers=AUTH
                        )
                        if got.json()["status"] == "indexed":
                            break
                        await asyncio.sleep(0.05)
            yield http, container


async def _reader_with_runs(db_path: Path, *, count: int) -> DuckDbQueryLogReader:
    """Wait for the sink to flush, then return a read-only reader."""
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            reader = DuckDbQueryLogReader(db_path)
            if len(reader.read_runs()) >= count and reader.read_labels():
                return reader
        except Exception:  # noqa: BLE001, S110 — file/tables land on first flush
            pass
        await asyncio.sleep(0.2)
    pytest.fail("query log rows never flushed")
    raise AssertionError  # unreachable: pytest.fail raises


async def test_score_and_replay_end_to_end(harness, tmp_path):
    client, container = harness

    # Two labeled searches for the same intent, one unlabeled search.
    query_ids = []
    for text in ("hexagonal ports", "Hexagonal Ports"):
        response = await client.post("/v1/search", json={"query": text}, headers=AUTH)
        assert response.status_code == 200
        data = response.json()
        query_ids.append(data["query_id"])
        top = data["results"][0]["page_id"]
        feedback = await client.post(
            "/v1/feedback",
            json={"query_id": data["query_id"], "page_id": top, "relevant": True},
            headers=AUTH,
        )
        assert feedback.status_code == 202
    unlabeled = await client.post(
        "/v1/search", json={"query": "density clustering"}, headers=AUTH
    )
    assert unlabeled.status_code == 200

    reader = await _reader_with_runs(tmp_path / "obs.duckdb", count=3)

    # -- score_log over the real log ---------------------------------------
    service = EvalService(reader=reader)
    report = service.score_log(k=10)
    assert report.logged == 3
    assert report.labeled == 2
    assert report.scored == 2
    (model_report,) = report.models
    assert model_report.model == "fake-model"
    assert 0.0 < model_report.ndcg <= 1.0
    assert 0.0 < model_report.recall <= 1.0
    assert model_report.rerank_lift is not None  # FakeReranker was active

    runs = reader.read_runs()
    assert all(run.final_page_ids for run in runs)
    assert all(run.prererank_page_ids for run in runs)

    # -- replay: rerank on vs off, no new log rows -------------------------
    replay = await service.replay(
        compare=container.compare,
        active_model_id="fake-model",
        arm_a=ArmSpec(rerank=True),
        arm_b=ArmSpec(rerank=False),
        k=10,
        candidates=50,
    )
    # the two labeled searches share normalized text -> one golden query
    assert replay.golden_queries == 1
    assert 0.0 <= replay.arm_a.ndcg <= 1.0
    assert 0.0 <= replay.arm_b.ndcg <= 1.0
    assert set(replay.deltas) == {"ndcg", "reciprocal_rank", "recall"}
    assert len(replay.queries) == 1

    # flush everything; replay must not have written new query_log rows
    container.sink.close()
    assert len(reader.read_runs()) == 3


async def test_latest_feedback_label_wins(harness, tmp_path):
    client, _container = harness
    response = await client.post(
        "/v1/search", json={"query": "hexagonal ports"}, headers=AUTH
    )
    data = response.json()
    top = data["results"][0]["page_id"]
    for relevant in (True, False):
        feedback = await client.post(
            "/v1/feedback",
            json={"query_id": data["query_id"], "page_id": top, "relevant": relevant},
            headers=AUTH,
        )
        assert feedback.status_code == 202

    reader = await _reader_with_runs(tmp_path / "obs.duckdb", count=1)

    labels = reader.read_labels()
    assert labels[data["query_id"]][top] is False


def test_reader_requires_existing_file(tmp_path):
    with pytest.raises(FileNotFoundError, match="query log not found"):
        DuckDbQueryLogReader(tmp_path / "missing.duckdb")
