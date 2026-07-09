"""Forget/blacklist API tests: purge semantics and tombstone lifecycle."""

import asyncio

import httpx
import pytest

from refindery.api.app import create_app
from refindery.domain.ids import PageId
from refindery.domain.models import TombstoneStatus
from tests.fakes.container import TEST_TOKEN, build_test_container, make_test_settings

AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}


def _page(url: str, text: str) -> dict:
    return {"url": url, "title": url, "body_extracted": text}


@pytest.fixture
async def harness(tmp_path):
    container = build_test_container(tmp_path)
    app = create_app(make_test_settings(tmp_path), container=container)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as http:
            yield http, container


async def _ingest_and_wait(client, pages: list[dict]) -> list[str]:
    ids = []
    for body in pages:
        response = await client.post("/v1/pages", json=body, headers=AUTH)
        assert response.status_code == 202
        ids.append(response.json()["page_id"])
    async with asyncio.timeout(30):
        for page_id in ids:
            while True:
                got = await client.get(f"/v1/pages/{page_id}/status", headers=AUTH)
                if got.json()["status"] == "indexed":
                    break
                await asyncio.sleep(0.05)
    return ids


async def _wait_tombstones(
    container, page_ids: list[str], status: TombstoneStatus
) -> list:
    async with asyncio.timeout(20):
        while True:
            rows = await container.store.list_tombstones(status=status)
            if {t.page_id for t in rows} >= set(page_ids):
                return rows
            await asyncio.sleep(0.05)


async def test_forget_requires_exactly_one_target(harness):
    client, _container = harness
    both = await client.post(
        "/v1/forget", json={"url": "https://a.com/x", "domain": "a.com"}, headers=AUTH
    )
    neither = await client.post("/v1/forget", json={}, headers=AUTH)
    assert both.status_code == 422
    assert neither.status_code == 422


async def test_forget_service_requires_exactly_one_target(harness):
    _client, container = harness
    with pytest.raises(ValueError, match="provide exactly one"):
        await container.forget.forget(url="https://a.com/x", domain="a.com")
    with pytest.raises(ValueError, match="provide exactly one"):
        await container.forget.forget()


async def test_forget_domain_purges_and_blocks(harness):
    client, container = harness
    ids = await _ingest_and_wait(
        client,
        [
            _page("https://tracker.example/a", "secret alpha content here"),
            _page("https://sub.tracker.example/b", "secret beta content here"),
            _page("https://keep.example/c", "unrelated gamma content"),
        ],
    )

    response = await client.post(
        "/v1/forget", json={"domain": "tracker.example"}, headers=AUTH
    )
    assert response.status_code == 200
    data = response.json()
    assert data["pages_purged"] == 2
    assert data["kind"] == "domain"

    # Metadata is authoritative immediately.
    for page_id in ids[:2]:
        assert (
            await client.get(f"/v1/pages/{page_id}", headers=AUTH)
        ).status_code == 404
    assert (await client.get(f"/v1/pages/{ids[2]}", headers=AUTH)).status_code == 200

    # Search never returns purged pages.
    result = await client.post(
        "/v1/search", json={"query": "secret content", "suggest": 0}, headers=AUTH
    )
    got = {r["page_id"] for r in result.json()["results"]}
    assert got <= {ids[2]}

    # Purge job deletes vectors; tombstones advance.
    await _wait_tombstones(container, ids[:2], TombstoneStatus.DELETED)
    assert await container.vector_store.count_chunks(PageId(ids[0])) == 0
    assert await container.vector_store.count_chunks(PageId(ids[1])) == 0

    # Verification sweep marks them verified.
    await container.forget.verify_tombstones()
    await _wait_tombstones(container, ids[:2], TombstoneStatus.VERIFIED)

    # Future ingest of that domain is rejected.
    blocked = await client.post(
        "/v1/pages",
        json=_page("https://tracker.example/new", "more"),
        headers=AUTH,
    )
    assert blocked.status_code == 403
    assert blocked.json()["pattern"] == "tracker.example"


async def test_forget_domain_normalizes_and_escapes_sql_wildcards(harness):
    client, _container = harness
    ids = await _ingest_and_wait(
        client,
        [
            _page("https://a_b.example/a", "secret alpha"),
            _page("https://sub.a_b.example/b", "secret beta"),
            _page("https://axb.example/c", "secret gamma"),
        ],
    )

    response = await client.post(
        "/v1/forget",
        json={"domain": "https://www.a_b.example/some/path"},
        headers=AUTH,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["pattern"] == "a_b.example"
    assert data["pages_purged"] == 2

    assert (await client.get(f"/v1/pages/{ids[0]}", headers=AUTH)).status_code == 404
    assert (await client.get(f"/v1/pages/{ids[1]}", headers=AUTH)).status_code == 404
    assert (await client.get(f"/v1/pages/{ids[2]}", headers=AUTH)).status_code == 200


async def test_forget_url_is_idempotent(harness):
    client, _container = harness
    await _ingest_and_wait(client, [_page("https://one.example/x", "x content")])
    first = await client.post(
        "/v1/forget", json={"url": "https://one.example/x?utm_source=t"}, headers=AUTH
    )
    second = await client.post(
        "/v1/forget", json={"url": "https://one.example/x"}, headers=AUTH
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["blacklist_id"] == second.json()["blacklist_id"]
    assert second.json()["pages_purged"] == 0


async def test_unblacklist_allows_reingest_but_never_restores(harness):
    client, _container = harness
    ids = await _ingest_and_wait(client, [_page("https://gone.example/x", "temp")])
    response = await client.post(
        "/v1/forget", json={"domain": "gone.example"}, headers=AUTH
    )
    rule_id = response.json()["blacklist_id"]

    listing = await client.get("/v1/blacklist", headers=AUTH)
    assert [e["id"] for e in listing.json()["entries"]] == [rule_id]

    deleted = await client.delete(f"/v1/blacklist/{rule_id}", headers=AUTH)
    assert deleted.status_code == 204
    assert (await client.get("/v1/blacklist", headers=AUTH)).json()["entries"] == []

    # Content is still gone...
    assert (await client.get(f"/v1/pages/{ids[0]}", headers=AUTH)).status_code == 404
    # ...but re-ingest is allowed again (new page id).
    again = await client.post(
        "/v1/pages", json=_page("https://gone.example/x", "temp"), headers=AUTH
    )
    assert again.status_code == 202

    missing = await client.delete(f"/v1/blacklist/{rule_id}", headers=AUTH)
    assert missing.status_code == 404
