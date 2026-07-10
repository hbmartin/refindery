"""Web UI administration API contracts."""

import asyncio

import httpx
import pytest

from refindery.api.app import create_app
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
