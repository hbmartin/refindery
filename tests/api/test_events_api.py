"""SSE event stream: snapshot, live job transitions, heartbeat, shutdown."""

import asyncio
import json
from collections.abc import AsyncIterator

import httpx
import pytest

from refindery.api.app import create_app
from refindery.application.job_events import JobEventBus
from tests.fakes.container import (
    TEST_READ_TOKEN,
    TEST_TOKEN,
    build_test_container,
    make_test_settings,
)
from tests.fakes.streaming_transport import StreamingASGITransport

AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}
READ = {"Authorization": f"Bearer {TEST_READ_TOKEN}"}

PAGE = {
    "url": "https://arch.example/hexagonal",
    "title": "Hexagonal Architecture",
    "body_extracted": "Hexagonal architecture keeps domain logic pure.",
}


@pytest.fixture
async def harness(tmp_path):
    container = build_test_container(tmp_path)
    app = create_app(make_test_settings(tmp_path), container=container)
    async with app.router.lifespan_context(app):
        transport = StreamingASGITransport(app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as http:
            yield http, container


async def _next_event(lines: AsyncIterator[str]) -> tuple[str, dict]:
    """Read one named SSE event, skipping comments and retry hints."""
    event_name = ""
    data = ""
    async for line in lines:
        if line.startswith((":", "retry:")):
            continue
        if line.startswith("event:"):
            event_name = line.removeprefix("event:").strip()
        elif line.startswith("data:"):
            data += line.removeprefix("data:").strip()
        elif not line and event_name:
            return event_name, json.loads(data)
    msg = "stream ended before a complete event"
    raise AssertionError(msg)


async def test_stream_requires_auth(harness):
    client, _container = harness
    response = await client.get("/v1/events")
    assert response.status_code == 401
    await response.aclose()


async def test_snapshot_then_job_transitions(harness):
    client, _container = harness
    async with (
        asyncio.timeout(20),
        client.stream("GET", "/v1/events", headers=READ) as response,
    ):
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        lines = response.aiter_lines()
        name, snapshot = await _next_event(lines)
        assert name == "snapshot"
        assert snapshot == {"jobs": []}

        created = await client.post("/v1/pages", json=PAGE, headers=AUTH)
        assert created.status_code == 202

        # extract_entities also emits; watch only the index_page job.
        statuses: list[str] = []
        while "done" not in statuses:
            name, payload = await _next_event(lines)
            assert name == "job"
            if payload["kind"] == "index_page":
                statuses.append(payload["status"])
        assert statuses[0] == "pending"
        assert "running" in statuses
        assert statuses[-1] == "done"


async def test_snapshot_contains_preexisting_jobs(harness):
    client, _container = harness
    created = await client.post("/v1/pages", json=PAGE, headers=AUTH)
    page_id = created.json()["page_id"]
    async with asyncio.timeout(20):
        while True:
            got = await client.get(f"/v1/pages/{page_id}/status", headers=AUTH)
            if got.json()["status"] == "indexed":
                break
            await asyncio.sleep(0.05)
    async with (
        asyncio.timeout(20),
        client.stream("GET", "/v1/events", headers=READ) as response,
    ):
        name, snapshot = await _next_event(response.aiter_lines())
        assert name == "snapshot"
        kinds = {job["kind"]: job["status"] for job in snapshot["jobs"]}
        assert kinds["index_page"] == "done"


async def test_heartbeat_comments_flow_when_idle(harness):
    client, container = harness
    container.settings.events.heartbeat_s = 0.05
    async with (
        asyncio.timeout(20),
        client.stream("GET", "/v1/events", headers=READ) as response,
    ):
        saw_keepalive = False
        async for line in response.aiter_lines():
            if line.startswith(": keep-alive"):
                saw_keepalive = True
                break
        assert saw_keepalive


async def test_stream_ends_on_bus_close(harness):
    client, container = harness
    async with (
        asyncio.timeout(20),
        client.stream("GET", "/v1/events", headers=READ) as response,
    ):
        lines = response.aiter_lines()
        await _next_event(lines)  # snapshot
        container.events.close()
        remaining = [line async for line in lines]
        assert all(not line.startswith("event: job") for line in remaining)


async def test_subscriber_limit_returns_503(harness):
    client, container = harness
    container.events = JobEventBus(max_subscribers=0)
    response = await client.get("/v1/events", headers=READ)
    assert response.status_code == 503
    await response.aclose()


async def test_disconnect_unsubscribes(harness):
    client, container = harness
    async with (
        asyncio.timeout(20),
        client.stream("GET", "/v1/events", headers=READ) as response,
    ):
        await _next_event(response.aiter_lines())
        assert container.events.subscriber_count == 1
    async with asyncio.timeout(10):
        while True:
            if container.events.subscriber_count == 0:
                break
            await asyncio.sleep(0.02)
