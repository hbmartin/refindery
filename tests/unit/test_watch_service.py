"""WatchService: scheduling tick, poll fan-out, dedup, and failure isolation."""

from datetime import UTC, datetime, timedelta

import pytest

from refindery.application.ports.watch_source import WatchItem
from refindery.application.services.watch_service import WatchPatch, WatchService
from refindery.config import WatchSettings
from refindery.domain.errors import FetchFailedError, WatchNotFoundError
from refindery.domain.ids import WatchId, new_blacklist_id
from refindery.domain.models import (
    BlacklistKind,
    BlacklistRule,
    JobKind,
    WatchKind,
    WatchStatus,
)
from tests.fakes.clock import FakeClock
from tests.fakes.container import build_test_container
from tests.fakes.watch import FakeWatchSource

FEED_URL = "https://blog.example/feed.xml"
NOW = datetime(2026, 7, 8, 12, 0, 0, tzinfo=UTC)


def _items(*urls: str) -> list[WatchItem]:
    return [WatchItem(url=url, title=f"Title of {url}") for url in urls]


@pytest.fixture
async def harness(tmp_path):
    clock = FakeClock(NOW)
    source = FakeWatchSource()
    container = build_test_container(
        tmp_path, clock=clock, watch_sources={WatchKind.RSS: source}
    )
    await container.store.connect()
    await container.store.migrate()
    yield container, clock, source
    await container.store.close()


async def _poll_jobs(container) -> list:
    return await container.store.list_jobs(kind=JobKind.POLL_WATCH)


async def test_create_and_duplicate_conflict(harness):
    container, _clock, _source = harness
    watch = await container.watches.create(kind=WatchKind.RSS, url=FEED_URL)
    assert watch is not None
    assert watch.interval_hours == 24
    assert watch.next_run_at == NOW
    assert watch.last_status is WatchStatus.PENDING
    duplicate = await container.watches.create(kind=WatchKind.RSS, url=FEED_URL)
    assert duplicate is None


async def test_tick_enqueues_and_advances_schedule(harness):
    container, _clock, _source = harness
    watch = await container.watches.create(kind=WatchKind.RSS, url=FEED_URL)
    enqueued = await container.watches.tick()
    assert enqueued == 1
    jobs = await _poll_jobs(container)
    assert len(jobs) == 1
    assert jobs[0].payload == {"watch_id": watch.id}
    assert jobs[0].idempotency_key == f"poll_watch:{watch.id}:{NOW.isoformat()}"
    refreshed = await container.watches.get(watch.id)
    assert refreshed.last_run_at == NOW
    assert refreshed.next_run_at == NOW + timedelta(hours=24)
    # The schedule advanced, so an immediate second tick has nothing due.
    assert await container.watches.tick() == 0


async def test_racing_duplicate_tick_dedups_on_idempotency_key(harness):
    container, _clock, _source = harness
    watch = await container.watches.create(kind=WatchKind.RSS, url=FEED_URL)
    first = await container.queue.enqueue(
        kind=JobKind.POLL_WATCH,
        payload={"watch_id": watch.id},
        idempotency_key=f"poll_watch:{watch.id}:{NOW.isoformat()}",
    )
    duplicate = await container.queue.enqueue(
        kind=JobKind.POLL_WATCH,
        payload={"watch_id": watch.id},
        idempotency_key=f"poll_watch:{watch.id}:{NOW.isoformat()}",
    )
    assert first is not None
    assert duplicate is None


async def test_tick_covers_multiple_due_watches(harness):
    container, _clock, _source = harness
    await container.watches.create(kind=WatchKind.RSS, url=FEED_URL)
    await container.watches.create(kind=WatchKind.RSS, url="https://other.example/rss")
    assert await container.watches.tick() == 2
    assert len(await _poll_jobs(container)) == 2


async def test_poll_fans_out_new_urls_and_records_ok(harness):
    container, _clock, source = harness
    source.items[FEED_URL] = _items(
        "https://blog.example/posts/a", "https://blog.example/posts/b"
    )
    watch = await container.watches.create(kind=WatchKind.RSS, url=FEED_URL)
    await container.watches.tick()
    (job,) = await _poll_jobs(container)
    await container.watches.handle_poll_watch(job)

    fetch_jobs = await container.store.list_jobs(kind=JobKind.FETCH_AND_INDEX)
    assert len(fetch_jobs) == 2
    refreshed = await container.watches.get(watch.id)
    assert refreshed.last_status is WatchStatus.OK
    assert refreshed.last_item_count == 2
    assert refreshed.last_error is None
    page = await container.store.get_page_by_canonical_url(
        "https://blog.example/posts/a"
    )
    assert page is not None
    assert page.source == f"watch:rss:{watch.id}"
    assert page.title == "Title of https://blog.example/posts/a"


async def test_second_poll_is_all_revisits(harness):
    container, clock, source = harness
    source.items[FEED_URL] = _items("https://blog.example/posts/a")
    watch = await container.watches.create(kind=WatchKind.RSS, url=FEED_URL)
    await container.watches.tick()
    (job,) = await _poll_jobs(container)
    await container.watches.handle_poll_watch(job)
    assert len(await container.store.list_jobs(kind=JobKind.FETCH_AND_INDEX)) == 1

    clock.advance(minutes=60 * 25)
    assert await container.watches.tick() == 1
    second = next(
        j
        for j in await _poll_jobs(container)
        if j.idempotency_key != job.idempotency_key
    )
    await container.watches.handle_poll_watch(second)
    # Revisit: no new fetch job, page seen twice.
    assert len(await container.store.list_jobs(kind=JobKind.FETCH_AND_INDEX)) == 1
    page = await container.store.get_page_by_canonical_url(
        "https://blog.example/posts/a"
    )
    assert page.visit_count == 2
    refreshed = await container.watches.get(watch.id)
    assert refreshed.last_status is WatchStatus.OK


async def test_blacklisted_item_is_skipped(harness):
    container, _clock, source = harness
    blocked = "https://blog.example/posts/blocked"
    await container.store.purge_and_blacklist(
        BlacklistRule(
            id=new_blacklist_id(),
            pattern=blocked,
            kind=BlacklistKind.URL,
            created_at=NOW,
        )
    )
    source.items[FEED_URL] = _items(blocked, "https://blog.example/posts/ok")
    await container.watches.create(kind=WatchKind.RSS, url=FEED_URL)
    await container.watches.tick()
    (job,) = await _poll_jobs(container)
    await container.watches.handle_poll_watch(job)
    assert await container.store.get_page_by_canonical_url(blocked) is None
    assert len(await container.store.list_jobs(kind=JobKind.FETCH_AND_INDEX)) == 1


async def test_cap_keeps_newest_items(harness):
    container, clock, source = harness
    capped = WatchService(
        store=container.store,
        queue=container.queue,
        clock=clock,
        ingest=container.ingest,
        sources={WatchKind.RSS: source},
        settings=WatchSettings(max_items_per_poll=2),
    )
    source.items[FEED_URL] = [
        WatchItem(
            url=f"https://blog.example/posts/{i}",
            published_at=NOW - timedelta(days=i),
        )
        for i in range(4)
    ]
    watch = await capped.create(kind=WatchKind.RSS, url=FEED_URL)
    assert watch is not None
    await capped.tick()
    (job,) = await _poll_jobs(container)
    await capped.handle_poll_watch(job)
    newest = await container.store.get_page_by_canonical_url(
        "https://blog.example/posts/0"
    )
    oldest = await container.store.get_page_by_canonical_url(
        "https://blog.example/posts/3"
    )
    assert newest is not None
    assert oldest is None
    refreshed = await capped.get(watch.id)
    assert refreshed is not None
    assert refreshed.last_item_count == 2


async def test_disabled_and_deleted_watches_are_noop_polls(harness):
    container, _clock, source = harness
    source.items[FEED_URL] = _items("https://blog.example/posts/a")
    watch = await container.watches.create(kind=WatchKind.RSS, url=FEED_URL)
    await container.watches.tick()
    (job,) = await _poll_jobs(container)

    await container.watches.update(watch.id, WatchPatch(enabled=False))
    await container.watches.handle_poll_watch(job)
    assert await container.store.list_jobs(kind=JobKind.FETCH_AND_INDEX) == []

    await container.watches.delete(watch.id)
    await container.watches.handle_poll_watch(job)
    assert await container.store.list_jobs(kind=JobKind.FETCH_AND_INDEX) == []


async def test_discover_error_records_error_and_isolates_siblings(harness):
    container, _clock, source = harness
    # FEED_URL has no preset items, so discover raises FetchFailedError.
    bad = await container.watches.create(kind=WatchKind.RSS, url=FEED_URL)
    good_url = "https://other.example/rss"
    source.items[good_url] = _items("https://other.example/posts/a")
    good = await container.watches.create(kind=WatchKind.RSS, url=good_url)
    await container.watches.tick()
    jobs = {j.payload["watch_id"]: j for j in await _poll_jobs(container)}

    with pytest.raises(FetchFailedError):
        await container.watches.handle_poll_watch(jobs[bad.id])
    await container.watches.handle_poll_watch(jobs[good.id])

    bad_refreshed = await container.watches.get(bad.id)
    assert bad_refreshed.last_status is WatchStatus.ERROR
    assert "no fake items configured" in bad_refreshed.last_error
    good_refreshed = await container.watches.get(good.id)
    assert good_refreshed.last_status is WatchStatus.OK


async def test_update_reschedules_on_interval_change(harness):
    container, _clock, _source = harness
    watch = await container.watches.create(kind=WatchKind.RSS, url=FEED_URL)
    updated = await container.watches.update(watch.id, WatchPatch(interval_hours=6))
    assert updated.interval_hours == 6
    # Never run: reschedule counts from now.
    assert updated.next_run_at == NOW + timedelta(hours=6)
    assert (
        await container.watches.update(WatchId("missing"), WatchPatch(enabled=False))
        is None
    )


async def test_run_now_enqueues_manual_job_and_advances(harness):
    container, _clock, _source = harness
    watch = await container.watches.create(kind=WatchKind.RSS, url=FEED_URL)
    job_id = await container.watches.run_now(watch.id)
    assert job_id is not None
    (job,) = await _poll_jobs(container)
    assert job.idempotency_key == f"poll_watch:{watch.id}:manual:{NOW.isoformat()}"
    refreshed = await container.watches.get(watch.id)
    assert refreshed.next_run_at == NOW + timedelta(hours=24)
    with pytest.raises(WatchNotFoundError):
        await container.watches.run_now(WatchId("missing"))
