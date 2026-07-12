"""Web UI administration API contracts."""

import asyncio
from datetime import UTC, datetime

import httpx
import pytest

from refindery.api.app import create_app
from refindery.domain.models import ClusterRun
from tests.fakes.container import (
    TEST_READ_TOKEN,
    TEST_TOKEN,
    build_test_container,
    make_test_settings,
)

READ = {"Authorization": f"Bearer {TEST_READ_TOKEN}"}
FULL = {"Authorization": f"Bearer {TEST_TOKEN}"}


@pytest.fixture
async def admin_harness(tmp_path):
    """Serve the real local adapters behind an in-process HTTP client."""
    container = build_test_container(tmp_path)
    app = create_app(make_test_settings(tmp_path), container=container)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            yield client, container, app


async def test_identity_config_mcp_and_metrics(admin_harness):
    """Read-scoped callers can bootstrap every global administration panel."""
    client, _, _ = admin_harness
    whoami = await client.get("/v1/whoami", headers=READ)
    assert whoami.json() == {"name": "readonly", "scopes": ["read"]}

    config = await client.get("/v1/admin/config", headers=READ)
    assert config.status_code == 200
    assert config.json()["settings"]["auth_token"] == "[REDACTED]"  # noqa: S105
    assert set(config.json()["mutability"].values()) == {"boot_only"}

    mcp = await client.get("/v1/admin/mcp", headers=READ)
    assert mcp.status_code == 200
    assert mcp.json()["enable_mutating_tools"] is False
    assert any(tool["name"] == "search" for tool in mcp.json()["tools"])

    await asyncio.sleep(0.3)
    metrics = await client.get(
        "/v1/admin/metrics/timeseries",
        params={"metric": "refindery_queue_depth", "step": 15},
        headers=READ,
    )
    assert metrics.status_code == 200
    assert metrics.json()["metric"] == "refindery_queue_depth"
    assert metrics.json()["current"]


async def test_query_log_jobs_and_openapi_contract(admin_harness):
    """Admin list filters and log reads expose real FastAPI query parameters."""
    client, _, app = admin_harness
    search = await client.post("/v1/search", json={"query": "nothing"}, headers=READ)
    assert search.status_code == 200
    await asyncio.sleep(0.3)

    runs = await client.get(
        "/v1/admin/query-log", params={"kind": "search"}, headers=READ
    )
    assert runs.status_code == 200
    assert runs.json()["runs"][0]["query_id"] == search.json()["query_id"]
    detail = await client.get(
        f"/v1/admin/query-log/{search.json()['query_id']}", headers=READ
    )
    assert detail.status_code == 200
    assert "timing_ms" in detail.json()
    assert "dense_hits" in detail.json()

    jobs = await client.get(
        "/v1/jobs", params={"status": "done", "kind": "index_page"}, headers=READ
    )
    assert jobs.status_code == 200
    parameters = {
        parameter["name"]
        for parameter in app.openapi()["paths"]["/v1/jobs"]["get"]["parameters"]
    }
    assert {"status", "status_filter", "kind", "limit"} <= parameters


async def test_replay_is_accepted_and_unknown_resources_are_404(admin_harness):
    """Replay submission is durable and missing detail resources are explicit."""
    client, _, _ = admin_harness
    replay_body = {"rerank_a": False, "rerank_b": True, "k": 5, "candidates": 20}
    read_only = await client.post(
        "/v1/admin/eval/replay", json=replay_body, headers=READ
    )
    assert read_only.status_code == 403
    accepted = await client.post(
        "/v1/admin/eval/replay",
        json=replay_body,
        headers=FULL,
    )
    assert accepted.status_code == 202
    assert accepted.json()["result_url"].endswith(accepted.json()["job_id"])
    polled = await client.get(accepted.json()["result_url"], headers=READ)
    assert polled.status_code == 200
    assert polled.json()["status"] in {"pending", "running", "done", "failed", "dead"}

    missing_query = await client.get("/v1/admin/query-log/missing", headers=READ)
    assert missing_query.status_code == 404
    missing_model = await client.get("/v1/models/missing/backfill", headers=FULL)
    assert missing_model.status_code == 404


async def test_cluster_projection_distinguishes_empty_run_from_missing(admin_harness):
    """A persisted run remains addressable before it has projection points."""
    client, container, _ = admin_harness
    run = ClusterRun(
        id="empty-run",
        trigger="manual",
        algorithm="hdbscan",
        params={},
        started_at=datetime(2026, 7, 10, tzinfo=UTC),
    )
    await container.store.insert_cluster_run(run)

    empty = await client.get(
        "/v1/clusters/projection", params={"run_id": run.id}, headers=READ
    )
    assert empty.status_code == 200
    assert empty.json() == {"run_id": run.id, "points": []}

    missing = await client.get(
        "/v1/clusters/projection", params={"run_id": "missing"}, headers=READ
    )
    assert missing.status_code == 404


async def test_metrics_summary_fresh_instance(admin_harness):
    """Store-derived fields are exact zeros; registry fields asserted loosely."""
    client, _, _ = admin_harness
    response = await client.get("/v1/admin/metrics/summary", headers=READ)
    assert response.status_code == 200
    body = response.json()
    assert body["tombstones"] == {"pending": 0, "deleted": 0, "verified": 0}
    assert set(body["jobs"]["by_status"]) == {
        "pending",
        "running",
        "done",
        "failed",
        "dead",
    }
    assert body["jobs"]["queue_depth"] == body["jobs"]["by_status"]["pending"]
    assert body["search_latency"] is None
    # Registry-derived fields are process-global: assert shape, never zeros.
    assert body["query_log_dropped_rows"] >= 0
    assert body["rerank_degraded_total"] >= 0
    assert isinstance(body["embedding_errors_by_provider"], dict)
    assert isinstance(body["breakers"], list)
    assert body["generated_at"]


async def test_metrics_summary_populated(admin_harness):
    """Tombstones, done jobs, and latency quantiles appear after activity."""
    client, _container, _app = admin_harness
    page = {
        "url": "https://arch.example/hexagonal",
        "title": "Hexagonal Architecture",
        "body_extracted": "Hexagonal architecture keeps the domain pure.",
    }
    created = await client.post("/v1/pages", json=page, headers=FULL)
    page_id = created.json()["page_id"]
    async with asyncio.timeout(30):
        while True:
            got = await client.get(f"/v1/pages/{page_id}/status", headers=FULL)
            if got.json()["status"] == "indexed":
                break
            await asyncio.sleep(0.05)
    for query in ("hexagonal", "domain"):
        search = await client.post("/v1/search", json={"query": query}, headers=FULL)
        assert search.status_code == 200
    forget = await client.post("/v1/forget", json={"url": page["url"]}, headers=FULL)
    assert forget.status_code == 200

    async with asyncio.timeout(30):
        while True:
            response = await client.get("/v1/admin/metrics/summary", headers=READ)
            body = response.json()
            latency = body["search_latency"]
            if latency is not None and latency["runs"] >= 2:
                break
            await asyncio.sleep(0.2)
    assert latency["p50_ms"] <= latency["p95_ms"]
    tombstones = body["tombstones"]
    assert sum(tombstones.values()) >= 1
    assert body["jobs"]["by_status"]["done"] >= 1


async def test_metrics_summary_future_since_nulls_latency(admin_harness):
    client, _, _ = admin_harness
    response = await client.get(
        "/v1/admin/metrics/summary",
        params={"since": "2999-01-01T00:00:00Z"},
        headers=READ,
    )
    assert response.status_code == 200
    assert response.json()["search_latency"] is None
