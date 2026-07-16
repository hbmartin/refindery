"""Watch management API: CRUD, run-now, conflict, and scope enforcement."""

import httpx
import pytest

from refindery.api.app import create_app
from refindery.application.ports.content_extractor import FetchResult
from tests.fakes.chunking import FakeChunker
from tests.fakes.container import (
    TEST_READ_TOKEN,
    TEST_TOKEN,
    build_test_container,
    make_test_settings,
)
from tests.fakes.extraction import FakeFetcher

AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}
READ = {"Authorization": f"Bearer {TEST_READ_TOKEN}"}
FEED_URL = "https://feeds.example.com/rss"


@pytest.fixture
async def client(tmp_path):
    feed = FetchResult(
        url=FEED_URL,
        final_url=FEED_URL,
        status_code=200,
        content_type="application/rss+xml",
        charset="utf-8",
        body=b"<rss version='2.0'><channel></channel></rss>",
    )
    container = build_test_container(
        tmp_path, fetcher=FakeFetcher({FEED_URL: feed}), chunker=FakeChunker()
    )
    app = create_app(make_test_settings(tmp_path), container=container)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as http:
            yield http


async def _create(client, url: str = FEED_URL) -> httpx.Response:
    return await client.post("/v1/watches", json={"url": url}, headers=AUTH)


async def test_create_lists_and_gets_a_watch(client):
    created = await _create(client)
    assert created.status_code == 201
    body = created.json()
    assert body["kind"] == "rss"
    assert body["url"] == FEED_URL
    assert body["interval_hours"] == 24
    assert body["last_status"] == "pending"
    watch_id = body["id"]

    listing = await client.get("/v1/watches", headers=AUTH)
    assert listing.status_code == 200
    assert [w["id"] for w in listing.json()["watches"]] == [watch_id]

    got = await client.get(f"/v1/watches/{watch_id}", headers=AUTH)
    assert got.status_code == 200
    assert got.json()["id"] == watch_id


async def test_create_honors_custom_interval(client):
    response = await client.post(
        "/v1/watches", json={"url": FEED_URL, "interval_hours": 6}, headers=AUTH
    )
    assert response.status_code == 201
    assert response.json()["interval_hours"] == 6


async def test_duplicate_watch_is_conflict(client):
    assert (await _create(client)).status_code == 201
    duplicate = await _create(client)
    assert duplicate.status_code == 409


async def test_get_unknown_watch_is_404(client):
    response = await client.get("/v1/watches/nope", headers=AUTH)
    assert response.status_code == 404


async def test_delete_removes_the_watch(client):
    watch_id = (await _create(client)).json()["id"]
    deleted = await client.delete(f"/v1/watches/{watch_id}", headers=AUTH)
    assert deleted.status_code == 204
    assert (
        await client.get(f"/v1/watches/{watch_id}", headers=AUTH)
    ).status_code == 404
    assert (
        await client.delete(f"/v1/watches/{watch_id}", headers=AUTH)
    ).status_code == 404


async def test_run_now_enqueues_a_poll(client):
    watch_id = (await _create(client)).json()["id"]
    response = await client.post(f"/v1/watches/{watch_id}/run", headers=AUTH)
    assert response.status_code == 202
    assert response.json()["watch_id"] == watch_id
    assert response.json()["job_id"]


async def test_run_unknown_watch_is_404(client):
    response = await client.post("/v1/watches/nope/run", headers=AUTH)
    assert response.status_code == 404


async def test_invalid_url_is_rejected(client):
    response = await client.post("/v1/watches", json={"url": "not-a-url"}, headers=AUTH)
    assert response.status_code == 422


async def test_read_token_can_list_but_not_mutate(client):
    watch_id = (await _create(client)).json()["id"]

    assert (await client.get("/v1/watches", headers=READ)).status_code == 200

    create = await client.post("/v1/watches", json={"url": FEED_URL}, headers=READ)
    assert create.status_code == 403
    run = await client.post(f"/v1/watches/{watch_id}/run", headers=READ)
    assert run.status_code == 403
    delete = await client.delete(f"/v1/watches/{watch_id}", headers=READ)
    assert delete.status_code == 403


async def test_watch_requires_authentication(client):
    response = await client.get("/v1/watches")
    assert response.status_code == 401
