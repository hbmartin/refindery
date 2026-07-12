"""Bulk job retry: selector and explicit-id modes, per-item outcomes."""

import asyncio
from datetime import UTC, datetime

import httpx
import pytest

from refindery.api.app import create_app
from refindery.application.ports.content_extractor import FetchResult
from refindery.domain.ids import new_job_id
from refindery.domain.models import Job, JobKind, JobStatus
from tests.fakes.container import TEST_TOKEN, build_test_container, make_test_settings
from tests.fakes.extraction import FakeFetcher

AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}
NOW = datetime(2026, 7, 8, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
async def harness(tmp_path):
    fetcher = FakeFetcher()
    container = build_test_container(tmp_path, fetcher=fetcher)
    app = create_app(make_test_settings(tmp_path), container=container)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as http:
            yield http, container, fetcher


async def _seed_job(
    container,
    *,
    kind: JobKind = JobKind.EXTRACT_ENTITIES,
    dead: bool = True,
    key: str | None = None,
) -> str:
    job = Job(
        id=new_job_id(),
        kind=kind,
        payload={},
        status=JobStatus.PENDING,
        idempotency_key=key or f"seed:{new_job_id()}",
        created_at=NOW,
        updated_at=NOW,
    )
    assert await container.store.create_job(job)
    if dead:
        await container.store.mark_job_dead(job_id=job.id, last_error="boom", now=NOW)
    return job.id


async def test_selector_mode_retries_only_dead_jobs(harness):
    client, container, _fetcher = harness
    dead_one = await _seed_job(container)
    dead_two = await _seed_job(container)
    pending = await _seed_job(container, dead=False)

    response = await client.post("/v1/jobs/retry", json={}, headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["requested"] == 2
    assert body["retried"] == 2
    assert {r["job_id"] for r in body["results"]} == {dead_one, dead_two}
    assert all(r["outcome"] == "retried" for r in body["results"])
    assert all(r["status"] == "pending" for r in body["results"])
    _ = pending

    # Idempotent: nothing dead remains.
    again = await client.post("/v1/jobs/retry", json={}, headers=AUTH)
    assert again.json() == {"requested": 0, "retried": 0, "results": []}


async def test_explicit_ids_mixed_outcomes_in_input_order(harness):
    client, container, _fetcher = harness
    dead = await _seed_job(container)
    pending = await _seed_job(container, dead=False)

    response = await client.post(
        "/v1/jobs/retry",
        json={"job_ids": [pending, "missing-id", dead, dead]},
        headers=AUTH,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["requested"] == 3  # duplicate id deduped
    assert body["retried"] == 1
    outcomes = [(r["job_id"], r["outcome"]) for r in body["results"]]
    assert outcomes == [
        (pending, "skipped"),
        ("missing-id", "not_found"),
        (dead, "retried"),
    ]
    assert body["results"][0]["status"] == "pending"
    assert "only dead jobs" in body["results"][0]["detail"]


async def test_kind_and_limit_filter_selector(harness):
    client, container, _fetcher = harness
    entities = await _seed_job(container, kind=JobKind.EXTRACT_ENTITIES)
    await _seed_job(container, kind=JobKind.CLUSTER)

    response = await client.post(
        "/v1/jobs/retry", json={"kind": "extract_entities"}, headers=AUTH
    )
    assert [r["job_id"] for r in response.json()["results"]] == [entities]

    await _seed_job(container, kind=JobKind.CLUSTER)
    limited = await client.post("/v1/jobs/retry", json={"limit": 1}, headers=AUTH)
    assert limited.json()["requested"] == 1


@pytest.mark.parametrize(
    "body",
    [
        {"status": "failed"},
        {"job_ids": []},
        {"job_ids": ["x"], "kind": "cluster"},
        {"unexpected": True},
    ],
)
async def test_invalid_bodies_are_422(harness, body):
    client, _container, _fetcher = harness
    response = await client.post("/v1/jobs/retry", json=body, headers=AUTH)
    assert response.status_code == 422


async def test_retried_fetch_job_reexecutes(harness):
    client, _container, fetcher = harness
    url = "https://flaky.example/article"
    created = await client.post("/v1/pages", json={"url": url}, headers=AUTH)
    page_id = created.json()["page_id"]

    # No fake response configured: the fetch job dies after max_attempts (2).
    async with asyncio.timeout(30):
        while True:
            got = await client.get(f"/v1/pages/{page_id}/status", headers=AUTH)
            if got.json()["status"] == "dead":
                break
            await asyncio.sleep(0.05)

    fetcher.responses[url] = FetchResult(
        url=url,
        final_url=url,
        status_code=200,
        content_type="text/html",
        charset="utf-8",
        body=b"<html><body>now it works</body></html>",
    )
    response = await client.post("/v1/jobs/retry", json={}, headers=AUTH)
    assert response.json()["retried"] == 1

    async with asyncio.timeout(30):
        while True:
            got = await client.get(f"/v1/pages/{page_id}/status", headers=AUTH)
            if got.json()["status"] == "indexed":
                break
            await asyncio.sleep(0.05)
