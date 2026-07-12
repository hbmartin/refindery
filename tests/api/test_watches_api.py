"""Watches API: CRUD, PATCH semantics, run-now, and end-to-end feed ingestion."""

import asyncio

import httpx
import pytest

from refindery.api.app import create_app
from refindery.application.ports.content_extractor import FetchResult
from tests.fakes.container import TEST_TOKEN, build_test_container, make_test_settings
from tests.fakes.extraction import FakeFetcher

AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}

FEED_URL = "https://blog.example/feed.xml"
ARTICLE_URL = "https://blog.example/posts/first"
FEED_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Example Blog</title>
  <item>
    <title>First Post</title>
    <link>{ARTICLE_URL}</link>
  </item>
</channel></rss>
""".encode()


def _fetch_result(url: str, *, content_type: str, body: bytes) -> FetchResult:
    return FetchResult(
        url=url,
        final_url=url,
        status_code=200,
        content_type=content_type,
        charset="utf-8",
        body=body,
    )


@pytest.fixture
async def harness(tmp_path):
    fetcher = FakeFetcher(
        {
            FEED_URL: _fetch_result(
                FEED_URL, content_type="application/rss+xml", body=FEED_XML
            ),
            ARTICLE_URL: _fetch_result(
                ARTICLE_URL,
                content_type="text/html",
                body=b"<html><body>Grand opening of the example blog.</body></html>",
            ),
        }
    )
    container = build_test_container(tmp_path, fetcher=fetcher)
    app = create_app(make_test_settings(tmp_path), container=container)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as http:
            yield http, container


async def test_create_returns_watch_with_pending_health(harness):
    client, _container = harness
    response = await client.post(
        "/v1/watches", json={"url": FEED_URL, "title": "Example"}, headers=AUTH
    )
    assert response.status_code == 201
    body = response.json()
    assert body["kind"] == "rss"
    assert body["url"] == FEED_URL
    assert body["title"] == "Example"
    assert body["enabled"] is True
    assert body["interval_hours"] == 24
    assert body["last_status"] == "pending"
    assert body["last_run_at"] is None
    assert body["last_item_count"] is None


async def test_create_duplicate_is_409(harness):
    client, _container = harness
    first = await client.post("/v1/watches", json={"url": FEED_URL}, headers=AUTH)
    assert first.status_code == 201
    duplicate = await client.post("/v1/watches", json={"url": FEED_URL}, headers=AUTH)
    assert duplicate.status_code == 409


@pytest.mark.parametrize("url", ["not-a-url", "ftp://files.example/feed", ""])
async def test_create_rejects_non_http_urls(harness, url):
    client, _container = harness
    response = await client.post("/v1/watches", json={"url": url}, headers=AUTH)
    assert response.status_code == 422


async def test_list_and_get(harness):
    client, _container = harness
    created = await client.post("/v1/watches", json={"url": FEED_URL}, headers=AUTH)
    watch_id = created.json()["id"]
    listed = await client.get("/v1/watches", headers=AUTH)
    assert listed.status_code == 200
    assert [w["id"] for w in listed.json()["watches"]] == [watch_id]
    got = await client.get(f"/v1/watches/{watch_id}", headers=AUTH)
    assert got.status_code == 200
    assert got.json()["id"] == watch_id
    missing = await client.get("/v1/watches/nope", headers=AUTH)
    assert missing.status_code == 404


async def test_patch_updates_enabled_and_interval(harness):
    client, _container = harness
    created = await client.post("/v1/watches", json={"url": FEED_URL}, headers=AUTH)
    watch_id = created.json()["id"]
    patched = await client.patch(
        f"/v1/watches/{watch_id}",
        json={"enabled": False, "interval_hours": 6},
        headers=AUTH,
    )
    assert patched.status_code == 200
    body = patched.json()
    assert body["enabled"] is False
    assert body["interval_hours"] == 6
    assert body["next_run_at"] > created.json()["next_run_at"]
    missing = await client.patch(
        "/v1/watches/nope", json={"enabled": True}, headers=AUTH
    )
    assert missing.status_code == 404


async def test_patch_rejects_url_change(harness):
    client, _container = harness
    created = await client.post("/v1/watches", json={"url": FEED_URL}, headers=AUTH)
    response = await client.patch(
        f"/v1/watches/{created.json()['id']}",
        json={"url": "https://elsewhere.example/feed"},
        headers=AUTH,
    )
    assert response.status_code == 422


async def test_delete_then_404(harness):
    client, _container = harness
    created = await client.post("/v1/watches", json={"url": FEED_URL}, headers=AUTH)
    watch_id = created.json()["id"]
    deleted = await client.delete(f"/v1/watches/{watch_id}", headers=AUTH)
    assert deleted.status_code == 204
    again = await client.delete(f"/v1/watches/{watch_id}", headers=AUTH)
    assert again.status_code == 404


async def test_run_unknown_watch_is_404(harness):
    client, _container = harness
    response = await client.post("/v1/watches/nope/run", headers=AUTH)
    assert response.status_code == 404


async def test_run_now_polls_feed_and_indexes_articles(harness):
    client, container = harness
    created = await client.post("/v1/watches", json={"url": FEED_URL}, headers=AUTH)
    watch_id = created.json()["id"]
    run = await client.post(f"/v1/watches/{watch_id}/run", headers=AUTH)
    assert run.status_code == 202
    body = run.json()
    assert body["watch_id"] == watch_id
    assert body["job_id"] is not None

    async with asyncio.timeout(30):
        while True:
            got = await client.get(f"/v1/watches/{watch_id}", headers=AUTH)
            if got.json()["last_status"] == "ok":
                break
            await asyncio.sleep(0.05)
    assert got.json()["last_item_count"] == 1

    page = await container.store.get_page_by_canonical_url(ARTICLE_URL)
    assert page is not None
    async with asyncio.timeout(30):
        while True:
            status = await client.get(f"/v1/pages/{page.id}/status", headers=AUTH)
            if status.json()["status"] == "indexed":
                break
            await asyncio.sleep(0.05)

    search = await client.post(
        "/v1/search", json={"query": "grand opening"}, headers=AUTH
    )
    assert search.status_code == 200
    urls = [result["canonical_url"] for result in search.json()["results"]]
    assert ARTICLE_URL in urls


async def test_scheduled_tick_polls_due_watch(harness):
    client, container = harness
    created = await client.post("/v1/watches", json={"url": FEED_URL}, headers=AUTH)
    watch_id = created.json()["id"]
    # New watches are due immediately; the production minute-periodic calls
    # tick() — tests drive it directly.
    assert await container.watches.tick() == 1
    async with asyncio.timeout(30):
        while True:
            got = await client.get(f"/v1/watches/{watch_id}", headers=AUTH)
            if got.json()["last_status"] == "ok":
                break
            await asyncio.sleep(0.05)
    assert got.json()["last_run_at"] is not None
