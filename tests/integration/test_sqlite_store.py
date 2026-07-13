"""Integration tests for the SQLite metadata store."""

import sqlite3
from collections.abc import Iterable
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import aiosqlite
import pytest
from pydantic import ValidationError

from refindery.adapters.metadata.sqlite_store import SqliteMetadataStore
from refindery.domain.ids import (
    ClusterId,
    JobId,
    PageId,
    new_blacklist_id,
    new_chunk_id,
    new_job_id,
    new_page_id,
    new_watch_id,
)
from refindery.domain.models import (
    BlacklistKind,
    BlacklistRule,
    Chunk,
    Cluster,
    EmbeddingModel,
    Job,
    JobKind,
    JobStatus,
    ModelStatus,
    Page,
    PageStatus,
    TombstoneStatus,
    Watch,
    WatchKind,
    WatchStatus,
)

NOW = datetime(2026, 7, 8, 12, 0, 0, tzinfo=UTC)


def _page(canonical_url: str = "https://example.com/a") -> Page:
    return Page(
        id=new_page_id(),
        canonical_url=canonical_url,
        original_url=f"{canonical_url}?utm_source=x",
        domain="example.com",
        title="A Title",
        body_text="hello world",
        content_hash="abc123",
        source="extension",
        metadata={"k": "v"},
        first_seen_at=NOW,
        last_seen_at=NOW,
        visit_count=1,
        indexed_at=None,
        status=PageStatus.QUEUED,
    )


def _job(idempotency_key: str = "index_page:p1:h1") -> Job:
    return Job(
        id=new_job_id(),
        kind=JobKind.INDEX_PAGE,
        payload={"page_id": "p1"},
        status=JobStatus.PENDING,
        idempotency_key=idempotency_key,
        created_at=NOW,
        updated_at=NOW,
    )


def _model(model_id: str = "voyage-3.5", *, is_active: bool = False) -> EmbeddingModel:
    return EmbeddingModel(
        id=model_id,
        provider="voyage",
        model_name=model_id,
        dim=32,
        max_input_tokens=32_000,
        is_active=is_active,
        status=ModelStatus.READY,
        created_at=NOW,
    )


def _watch(
    url: str = "https://example.com/feed.xml",
    *,
    config: dict[str, str] | None = None,
) -> Watch:
    return Watch(
        id=new_watch_id(),
        kind=WatchKind.RSS,
        url=url,
        title="Example feed",
        enabled=True,
        interval_hours=24,
        config=config,
        next_run_at=NOW,
        last_run_at=None,
        last_status=WatchStatus.PENDING,
        last_error=None,
        last_item_count=None,
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.fixture
async def store(tmp_path):
    async with SqliteMetadataStore(tmp_path / "test.db") as s:
        await s.migrate()
        yield s


async def test_page_roundtrip(store):
    page = _page()
    await store.insert_page(page)
    got = await store.get_page(page.id)
    assert got == page
    assert (await store.get_page_by_canonical_url(page.canonical_url)) == page
    assert await store.get_page(PageId("missing")) is None


async def test_revisit_bumps_count_and_seen(store):
    page = _page()
    await store.insert_page(page)
    later = NOW + timedelta(hours=3)
    await store.record_revisit(page_id=page.id, seen_at=later)
    got = await store.get_page(page.id)
    assert got is not None
    assert got.visit_count == 2
    assert got.last_seen_at == later
    assert got.first_seen_at == NOW


async def test_page_status_and_body(store):
    page = _page()
    page.body_text = None
    page.content_hash = None
    await store.insert_page(page)
    await store.set_page_body(
        page_id=page.id, body_text="fetched", content_hash="h2", title="T2"
    )
    await store.set_page_status(
        page_id=page.id, status=PageStatus.INDEXED, indexed_at=NOW
    )
    got = await store.get_page(page.id)
    assert got is not None
    assert got.body_text == "fetched"
    assert got.title == "T2"
    assert got.status is PageStatus.INDEXED
    assert got.indexed_at == NOW


async def test_chunks_replace_and_hydrate_ordered(store):
    page = _page()
    await store.insert_page(page)
    chunks = [
        Chunk(
            id=new_chunk_id(),
            page_id=page.id,
            ordinal=i,
            text=f"chunk {i}",
            token_count=3,
            char_start=i * 10,
            char_end=i * 10 + 8,
        )
        for i in range(3)
    ]
    await store.replace_chunks(page_id=page.id, chunks=chunks)
    ids = [chunks[2].id, chunks[0].id]
    got = await store.get_chunks(ids)
    assert [c.id for c in got] == ids

    # replacing removes old chunks
    await store.replace_chunks(page_id=page.id, chunks=chunks[:1])
    assert await store.get_chunks([chunks[2].id]) == []


async def test_get_chunks_batches_and_preserves_duplicate_order(store):
    page = _page()
    await store.insert_page(page)
    chunks = [
        Chunk(
            id=new_chunk_id(),
            page_id=page.id,
            ordinal=i,
            text=f"chunk {i}",
            token_count=3,
            char_start=i * 10,
            char_end=i * 10 + 8,
        )
        for i in range(1_001)
    ]
    await store.replace_chunks(page_id=page.id, chunks=chunks)
    requested = [chunks[-1].id, *[chunk.id for chunk in chunks], chunks[-1].id]

    got = await store.get_chunks(requested)

    assert [chunk.id for chunk in got] == requested


async def test_page_vectors_upsert(store):
    page = _page()
    await store.insert_page(page)
    await store.register_model(_model())
    await store.upsert_page_vector(
        page_id=page.id, model_id="voyage-3.5", vector=b"\x00\x01"
    )
    await store.upsert_page_vector(
        page_id=page.id, model_id="voyage-3.5", vector=b"\x02\x03"
    )
    vectors = await store.get_page_vectors(model_id="voyage-3.5")
    assert [(row.page_id, row.vector) for row in vectors] == [(page.id, b"\x02\x03")]
    stats = await store.chunk_stats()
    assert stats.n_chunks == 0
    assert stats.total_tokens == 0


async def test_model_registry_single_active(store):
    await store.register_model(_model("m-a", is_active=True))
    await store.register_model(_model("m-b"))
    await store.activate_model("m-b")
    active = await store.get_active_model()
    assert active is not None
    assert active.id == "m-b"
    models = await store.list_models(statuses=frozenset({ModelStatus.READY}))
    assert {m.id for m in models} == {"m-a", "m-b"}


async def test_job_idempotency(store):
    assert await store.create_job(_job()) is True
    assert await store.create_job(_job()) is False  # same idempotency key


async def test_watch_roundtrip_validates_config(store) -> None:
    watch = _watch(config={"category": "engineering"})
    assert await store.create_watch(watch) is True
    assert await store.get_watch(watch.id) == watch


async def test_watch_rejects_invalid_persisted_config(store) -> None:
    watch = _watch()
    assert await store.create_watch(watch) is True
    await store.conn.execute(
        "UPDATE watches SET config = ? WHERE id = ?", ("[]", watch.id)
    )
    await store.conn.commit()

    with pytest.raises(ValidationError, match="Input should be an object"):
        await store.get_watch(watch.id)


async def test_duplicate_watch_does_not_rollback_pending_work(store) -> None:
    watch = _watch()
    assert await store.create_watch(watch) is True
    pending_rule = BlacklistRule(
        id=new_blacklist_id(),
        pattern="https://pending.example/page",
        kind=BlacklistKind.URL,
        created_at=NOW,
    )
    await store.conn.execute(
        "INSERT INTO blacklist (id, pattern, kind, reason, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            pending_rule.id,
            pending_rule.pattern,
            pending_rule.kind,
            pending_rule.reason,
            pending_rule.created_at.isoformat(),
        ),
    )

    duplicate = replace(watch, id=new_watch_id())
    assert await store.create_watch(duplicate) is False
    cursor = await store.conn.execute(
        "SELECT id FROM blacklist WHERE id = ?", (pending_rule.id,)
    )
    assert await cursor.fetchone() is not None


async def test_job_lifecycle(store):
    job = _job()
    await store.create_job(job)
    await store.mark_job_running(
        job_id=job.id, lease_until=NOW + timedelta(minutes=15), now=NOW
    )
    got = await store.get_job(job.id)
    assert got is not None
    assert got.status is JobStatus.RUNNING
    await store.mark_job_failed(job_id=job.id, attempts=1, last_error="boom", now=NOW)
    await store.mark_job_dead(job_id=job.id, last_error="boom", now=NOW)
    dead = await store.list_jobs(status=JobStatus.DEAD)
    assert [j.id for j in dead] == [job.id]
    reset = await store.reset_job_for_retry(job_id=job.id, now=NOW)
    assert reset.status is JobStatus.PENDING
    assert reset.attempts == 0


async def test_expired_lease_recovery(store):
    job = _job()
    await store.create_job(job)
    await store.mark_job_running(
        job_id=job.id, lease_until=NOW + timedelta(minutes=15), now=NOW
    )
    # not yet expired
    assert await store.reset_expired_leases(now=NOW + timedelta(minutes=1)) == []
    expired = await store.reset_expired_leases(now=NOW + timedelta(minutes=16))
    assert [j.id for j in expired] == [job.id]
    pending = await store.list_pending_jobs()
    assert [j.id for j in pending] == [job.id]


async def test_reset_retry_missing_job_raises(store):
    from refindery.domain.errors import JobNotFoundError

    with pytest.raises(JobNotFoundError):
        await store.reset_job_for_retry(job_id=JobId("nope"), now=NOW)


async def test_page_delete_cascades_chunks_and_vectors(store):
    page = _page()
    await store.insert_page(page)
    await store.register_model(_model())
    chunk = Chunk(
        id=new_chunk_id(),
        page_id=page.id,
        ordinal=0,
        text="c",
        token_count=1,
        char_start=0,
        char_end=1,
    )
    await store.replace_chunks(page_id=page.id, chunks=[chunk])
    await store.upsert_page_vector(
        page_id=page.id, model_id="voyage-3.5", vector=b"\x00"
    )
    await store.conn.execute("DELETE FROM pages WHERE id = ?", (page.id,))
    await store.conn.commit()
    assert await store.get_chunks([chunk.id]) == []
    assert await store.get_page_vectors(model_id="voyage-3.5") == []


async def test_cluster_members_return_named_rows(store):
    page = _page()
    await store.insert_page(page)
    cluster = Cluster(
        id="cluster-a",
        label=None,
        keywords=[],
        size=1,
        model_id="voyage-3.5",
        created_at=NOW,
        updated_at=NOW,
    )
    await store.upsert_cluster(cluster)
    await store.replace_cluster_members(
        cluster_id=ClusterId("cluster-a"), members=[(page.id, 0.75)]
    )
    members = await store.cluster_members(ClusterId("cluster-a"))
    assert [(member.page_id, member.probability) for member in members] == [
        (page.id, 0.75)
    ]


async def test_clusters_for_pages_deduplicates_and_batches_legacy_sqlite_limit(
    store: SqliteMetadataStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    page_ids = [PageId(f"page-{index}") for index in range(1_001)]
    member_pages = [
        replace(_page(f"https://example.com/{index}"), id=page_ids[index])
        for index in (998, 999)
    ]
    for page in member_pages:
        await store.insert_page(page)
    cluster = Cluster(
        id="cluster-across-batches",
        label="Across batches",
        keywords=[],
        size=len(member_pages),
        model_id="voyage-3.5",
        created_at=NOW,
        updated_at=NOW,
    )
    await store.upsert_cluster(cluster)
    await store.replace_cluster_members(
        cluster_id=ClusterId(cluster.id),
        members=[(page.id, 0.75) for page in member_pages],
    )

    original_execute = store.conn.execute
    parameter_counts: list[int] = []

    async def execute_with_legacy_limit(
        sql: str, parameters: Iterable[object] | None = None
    ) -> aiosqlite.Cursor:
        parameter_batch = tuple(parameters or ())
        if len(parameter_batch) > 999:
            msg = "too many SQL variables"
            raise sqlite3.OperationalError(msg)
        parameter_counts.append(len(parameter_batch))
        return await original_execute(sql, parameters=parameter_batch)

    monkeypatch.setattr(store.conn, "execute", execute_with_legacy_limit)

    clusters = await store.clusters_for_pages([*page_ids, *page_ids])

    assert parameter_counts == [999, 2]
    assert clusters == {page.id: cluster for page in member_pages}


async def test_latest_job_for_page_returns_newest_and_filters_by_kind(store):
    page = _page()
    await store.insert_page(page)
    index_job = Job(
        id=new_job_id(),
        kind=JobKind.INDEX_PAGE,
        payload={"page_id": page.id},
        status=JobStatus.DONE,
        idempotency_key=f"index_page:{page.id}",
        created_at=NOW,
        updated_at=NOW,
    )
    entity_job = Job(
        id=new_job_id(),
        kind=JobKind.EXTRACT_ENTITIES,
        payload={"page_id": page.id, "content_hash": "abc123"},
        status=JobStatus.DEAD,
        idempotency_key=f"entities:{page.id}:abc123",
        created_at=NOW + timedelta(minutes=1),
        updated_at=NOW + timedelta(minutes=1),
    )
    assert await store.create_job(index_job)
    assert await store.create_job(entity_job)

    newest = await store.latest_job_for_page(page_id=page.id)
    assert newest is not None
    assert newest.id == entity_job.id
    index_only = await store.latest_job_for_page(
        page_id=page.id, kind=JobKind.INDEX_PAGE
    )
    assert index_only is not None
    assert index_only.id == index_job.id
    assert await store.latest_job_for_page(page_id=PageId("missing")) is None


async def test_list_page_ids_by_domain_status_filter(store):
    indexed = _page("https://example.com/indexed")
    queued = _page("https://example.com/queued")
    await store.insert_page(indexed)
    await store.insert_page(queued)
    await store.set_page_status(
        page_id=indexed.id, status=PageStatus.INDEXED, indexed_at=NOW
    )

    everything = await store.list_page_ids_by_domain(domain="example.com")
    assert set(everything) == {indexed.id, queued.id}
    only_indexed = await store.list_page_ids_by_domain(
        domain="example.com", status=PageStatus.INDEXED
    )
    assert only_indexed == [indexed.id]


async def test_indexed_pages_missing_entity_extraction(store):
    missing = _page("https://example.com/missing")
    stale = _page("https://example.com/stale")
    covered = _page("https://example.com/covered")
    queued = _page("https://example.com/queued")
    for page in (missing, stale, covered, queued):
        await store.insert_page(page)
    for page in (missing, stale, covered):
        await store.set_page_status(
            page_id=page.id, status=PageStatus.INDEXED, indexed_at=NOW
        )
    for target, content_hash in ((covered, "abc123"), (stale, "old-hash")):
        assert await store.create_job(
            Job(
                id=new_job_id(),
                kind=JobKind.EXTRACT_ENTITIES,
                payload={"page_id": target.id, "content_hash": content_hash},
                status=JobStatus.DONE,
                idempotency_key=f"entities:{target.id}:{content_hash}",
                created_at=NOW,
                updated_at=NOW,
            )
        )

    pages = await store.indexed_pages_missing_entity_extraction()
    # `covered` has a job for its current hash; `stale`'s job predates a
    # content change and `missing` never got one; `queued` is not indexed.
    assert {page.id for page in pages} == {missing.id, stale.id}


async def test_count_jobs_by_status(store):
    assert await store.count_jobs_by_status() == {}
    first = _job("k1")
    second = _job("k2")
    dead = _job("k3")
    for job in (first, second, dead):
        assert await store.create_job(job)
    await store.mark_job_dead(job_id=dead.id, last_error="boom", now=NOW)
    counts = await store.count_jobs_by_status()
    assert counts == {JobStatus.PENDING: 2, JobStatus.DEAD: 1}


async def test_count_tombstones_by_status(store):
    assert await store.count_tombstones_by_status() == {}
    page = _page("https://example.com/purge-me")
    await store.insert_page(page)
    rule = BlacklistRule(
        id=new_blacklist_id(),
        pattern="https://example.com/purge-me",
        kind=BlacklistKind.URL,
        created_at=NOW,
    )
    _rule, purged = await store.purge_and_blacklist(rule)
    assert purged == [page.id]
    counts = await store.count_tombstones_by_status()
    assert counts == {TombstoneStatus.PENDING: 1}
