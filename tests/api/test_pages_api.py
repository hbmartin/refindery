"""API contract tests: auth, ingest semantics, status lifecycle, e2e indexing."""

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import httpx
import pytest

from refindery.api.app import create_app
from refindery.domain.ids import JobId, PageId, new_blacklist_id
from refindery.domain.models import BlacklistKind
from tests.fakes.container import TEST_TOKEN, build_test_container, make_test_settings

if TYPE_CHECKING:
    from refindery.adapters.metadata.sqlite_store import SqliteMetadataStore

AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}
CORE_FAILURE = "boom during core indexing"
ENTITY_FAILURE = "entity extraction exploded"
BODY = {
    "url": "https://example.com/article?utm_source=x",
    "title": "An Article",
    "body_extracted": "Hexagonal architecture keeps the domain pure. " * 5,
    "fetched_at": "2026-07-08T10:00:00Z",
    "source": "extension",
}


class _CoreIndexError(RuntimeError):
    def __init__(self) -> None:
        super().__init__(CORE_FAILURE)


class _EntityExtractionError(RuntimeError):
    def __init__(self) -> None:
        super().__init__(ENTITY_FAILURE)


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


@pytest.fixture
async def client(harness):
    http, _container = harness
    return http


async def _wait_for_page_status(client, page_id: str, wanted: str) -> dict:
    async with asyncio.timeout(20):
        while True:
            response = await client.get(f"/v1/pages/{page_id}/status", headers=AUTH)
            assert response.status_code == 200
            data = response.json()
            if data["status"] == wanted:
                return data
            if data["status"] in {"failed", "dead"} and wanted == "indexed":
                pytest.fail(f"page entered {data['status']}: {data.get('last_error')}")
            await asyncio.sleep(0.05)


async def _wait_for_entity_status(client, page_id: str, wanted: str) -> dict:
    async with asyncio.timeout(20):
        while True:
            response = await client.get(f"/v1/pages/{page_id}/status", headers=AUTH)
            assert response.status_code == 200
            data = response.json()
            if data["features"]["entities"]["status"] == wanted:
                return data
            await asyncio.sleep(0.05)


async def test_requires_bearer_token(client):
    assert (await client.post("/v1/pages", json=BODY)).status_code == 401
    bad = {"Authorization": "Bearer wrong"}
    assert (await client.post("/v1/pages", json=BODY, headers=bad)).status_code == 401


async def test_body_xor_validation(client):
    payload = {**BODY, "body_html": "<p>hi</p>"}
    response = await client.post("/v1/pages", json=payload, headers=AUTH)
    assert response.status_code == 422


async def test_ingest_indexes_and_revisits(client):
    response = await client.post("/v1/pages", json=BODY, headers=AUTH)
    assert response.status_code == 202
    page_id = response.json()["page_id"]
    assert response.json()["status"] == "queued"

    await _wait_for_page_status(client, page_id, "indexed")

    # Full page readable, canonical URL stripped of tracking params.
    page = (await client.get(f"/v1/pages/{page_id}", headers=AUTH)).json()
    assert page["canonical_url"] == "https://example.com/article"
    assert page["visit_count"] == 1
    assert page["body_text"].startswith("Hexagonal architecture")

    # Same canonical URL (different tracking params) -> revisit, body discarded.
    revisit_body = {**BODY, "url": "https://example.com/article?fbclid=zzz"}
    response = await client.post("/v1/pages", json=revisit_body, headers=AUTH)
    assert response.status_code == 200
    data = response.json()
    assert data["revisit"] is True
    assert data["page_id"] == page_id
    page = (await client.get(f"/v1/pages/{page_id}", headers=AUTH)).json()
    assert page["visit_count"] == 2


async def test_revisit_flags_differing_hash(client):
    response = await client.post("/v1/pages", json=BODY, headers=AUTH)
    page_id = response.json()["page_id"]
    await _wait_for_page_status(client, page_id, "indexed")

    changed = {**BODY, "body_extracted": "Completely different content now."}
    response = await client.post("/v1/pages", json=changed, headers=AUTH)
    assert response.status_code == 200
    assert response.json()["content_hash_differs"] is True
    # Body was discarded.
    page = (await client.get(f"/v1/pages/{page_id}", headers=AUTH)).json()
    assert page["body_text"].startswith("Hexagonal architecture")


async def test_blacklisted_url_403(harness):
    client, container = harness
    await container.store.conn.execute(
        "INSERT INTO blacklist (id, pattern, kind, reason, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            new_blacklist_id(),
            "bank.com",
            BlacklistKind.DOMAIN,
            "test",
            datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
        ),
    )
    await container.store.conn.commit()
    payload = {**BODY, "url": "https://secure.bank.com/statement"}
    response = await client.post("/v1/pages", json=payload, headers=AUTH)
    assert response.status_code == 403
    assert response.json() == {"error": "blacklisted", "pattern": "bank.com"}


async def test_body_html_ingest_uses_extractor(client):
    payload = {
        "url": "https://example.com/html-page",
        "body_html": "<html><body><h1>Title</h1><p>Paragraph text.</p></body></html>",
    }
    response = await client.post("/v1/pages", json=payload, headers=AUTH)
    assert response.status_code == 202
    page_id = response.json()["page_id"]
    await _wait_for_page_status(client, page_id, "indexed")
    page = (await client.get(f"/v1/pages/{page_id}", headers=AUTH)).json()
    assert "<" not in page["body_text"]
    assert "Paragraph text." in page["body_text"]


async def test_unknown_page_404(client):
    assert (await client.get("/v1/pages/nope", headers=AUTH)).status_code == 404


async def test_health_endpoints(client):
    assert (await client.get("/healthz")).status_code == 200
    assert (await client.get("/readyz")).status_code == 200


async def test_indexed_page_is_searchable_in_vector_store(harness):
    client, container = harness
    response = await client.post("/v1/pages", json=BODY, headers=AUTH)
    page_id = response.json()["page_id"]
    await _wait_for_page_status(client, page_id, "indexed")

    hits = await container.vector_store.sparse_query(text="hexagonal", limit=5)
    assert hits
    assert hits[0].page_id == page_id
    # Page vector rolled up for the active model.
    vectors = await container.store.get_page_vectors(model_id="fake-model")
    assert [row.page_id for row in vectors] == [PageId(page_id)]


async def test_failed_core_index_cleans_artifacts_and_search_excludes(
    tmp_path, monkeypatch
):
    container = build_test_container(tmp_path)
    original = container.vector_store.upsert_chunks

    async def fail_after_write(points) -> None:
        await original(points)
        raise _CoreIndexError

    monkeypatch.setattr(container.vector_store, "upsert_chunks", fail_after_write)
    app = create_app(make_test_settings(tmp_path), container=container)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            response = await client.post("/v1/pages", json=BODY, headers=AUTH)
            assert response.status_code == 202
            page_id = response.json()["page_id"]

            data = await _wait_for_page_status(client, page_id, "dead")
            assert CORE_FAILURE in (data["last_error"] or "")
            assert await container.store.chunks_for_page(PageId(page_id)) == []
            assert await container.vector_store.count_chunks(PageId(page_id)) == 0

            search = await client.post(
                "/v1/search", json={"query": "hexagonal"}, headers=AUTH
            )
            assert search.status_code == 200
            assert all(r["page_id"] != page_id for r in search.json()["results"])


class _FailingEntityExtractor:
    def health_check(self) -> bool:
        return True

    async def extract(self, _text: str) -> None:
        raise _EntityExtractionError


async def test_entity_job_failure_leaves_page_indexed_and_visible_in_status(tmp_path):
    container = build_test_container(tmp_path, extractor=_FailingEntityExtractor())
    app = create_app(make_test_settings(tmp_path), container=container)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            response = await client.post("/v1/pages", json=BODY, headers=AUTH)
            assert response.status_code == 202
            page_id = response.json()["page_id"]
            await _wait_for_page_status(client, page_id, "indexed")

            data = await _wait_for_entity_status(client, page_id, "dead")
            assert data["status"] == "indexed"
            assert data["last_error"] is None
            assert ENTITY_FAILURE in (data["features"]["entities"]["last_error"] or "")

            jobs = (
                await client.get("/v1/jobs?status_filter=dead", headers=AUTH)
            ).json()
            assert any(job["kind"] == "extract_entities" for job in jobs["jobs"])


async def test_reconcile_enqueues_entity_jobs_lost_before_enqueue(harness):
    client, container = harness
    response = await client.post("/v1/pages", json=BODY, headers=AUTH)
    assert response.status_code == 202
    page_id = response.json()["page_id"]
    await _wait_for_entity_status(client, page_id, "done")

    # Simulate a crash between the indexed status flip and the enqueue: the
    # page is indexed but no extract_entities job exists in the ledger.
    store = cast("SqliteMetadataStore", container.store)
    await store.conn.execute("DELETE FROM jobs WHERE kind = 'extract_entities'")
    await store.conn.commit()
    data = await _wait_for_entity_status(client, page_id, "not_queued")
    assert data["status"] == "indexed"

    assert await container.indexing.reconcile_entity_jobs() == 1
    data = await _wait_for_entity_status(client, page_id, "done")
    assert data["status"] == "indexed"


async def test_startup_continues_when_entity_reconcile_fails(tmp_path, monkeypatch):
    container = build_test_container(tmp_path)

    async def exploding_reconcile() -> int:
        msg = "reconcile exploded"
        raise RuntimeError(msg)

    monkeypatch.setattr(
        container.indexing, "reconcile_entity_jobs", exploding_reconcile
    )
    app = create_app(make_test_settings(tmp_path), container=container)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            # The consumer started despite the reconcile failure: ingest
            # still runs end to end.
            response = await client.post("/v1/pages", json=BODY, headers=AUTH)
            assert response.status_code == 202
            await _wait_for_page_status(client, response.json()["page_id"], "indexed")


async def test_reconcile_isolates_per_page_enqueue_failures(harness, monkeypatch):
    client, container = harness
    page_ids: list[str] = []
    for i in range(2):
        payload = {**BODY, "url": f"https://example.com/article-{i}"}
        response = await client.post("/v1/pages", json=payload, headers=AUTH)
        assert response.status_code == 202
        page_ids.append(response.json()["page_id"])
    for page_id in page_ids:
        await _wait_for_entity_status(client, page_id, "done")

    # Simulate the crash window for both pages, then make the first enqueue
    # fail: reconciliation must skip that page and still enqueue the other.
    store = cast("SqliteMetadataStore", container.store)
    await store.conn.execute("DELETE FROM jobs WHERE kind = 'extract_entities'")
    await store.conn.commit()

    real_enqueue = container.queue.enqueue
    attempts = 0

    async def enqueue_failing_once(**kwargs) -> JobId | None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            msg = "enqueue exploded"
            raise RuntimeError(msg)
        return await real_enqueue(**kwargs)

    monkeypatch.setattr(container.queue, "enqueue", enqueue_failing_once)

    assert await container.indexing.reconcile_entity_jobs() == 1
    # The skipped page is healed by the next reconcile pass.
    assert await container.indexing.reconcile_entity_jobs() == 1
    for page_id in page_ids:
        data = await _wait_for_entity_status(client, page_id, "done")
        assert data["status"] == "indexed"


async def test_dead_job_visible_and_page_dead(client):
    # Fetch path with no fake response -> job fails -> dead after 2 attempts.
    payload = {"url": "https://example.com/will-fail"}
    response = await client.post("/v1/pages", json=payload, headers=AUTH)
    assert response.status_code == 202
    page_id = response.json()["page_id"]

    data = await _wait_for_page_status(client, page_id, "dead")
    assert "no fake response" in (data["last_error"] or "")

    jobs = (await client.get("/v1/jobs?status_filter=dead", headers=AUTH)).json()
    assert len(jobs["jobs"]) == 1
    job_id = jobs["jobs"][0]["job_id"]

    # Manual retry runs it again (still failing -> dead again).
    retried = await client.post(f"/v1/jobs/{job_id}/retry", headers=AUTH)
    assert retried.status_code == 200
    assert retried.json()["status"] == "pending"
