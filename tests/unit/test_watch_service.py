"""Watch scheduling math and poll fan-out into the ingest pipeline."""

from datetime import UTC, datetime, timedelta

import pytest

from refindery.application.ports.content_extractor import FetchResult
from refindery.application.ports.feed_parser import FeedItem
from refindery.application.services.watch_service import WatchService
from refindery.config import WatchSettings
from refindery.domain.errors import FetchFailedError
from refindery.domain.ids import WatchId, new_job_id
from refindery.domain.models import Job, JobKind, JobStatus, WatchKind, WatchStatus
from tests.fakes.chunking import FakeChunker
from tests.fakes.container import build_test_container
from tests.fakes.extraction import FakeFetcher
from tests.fakes.feeds import FakeFeedParser

FEED_URL = "https://feeds.example.com/rss"
T0 = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)


class _FixedClock:
    """Controllable clock so interval math is deterministic."""

    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta


def _feed_result() -> FetchResult:
    return FetchResult(
        url=FEED_URL,
        final_url=FEED_URL,
        status_code=200,
        content_type="application/rss+xml",
        charset="utf-8",
        body=b"<feed/>",
    )


def _items(*urls: str) -> list[FeedItem]:
    return [FeedItem(url=url) for url in urls]


def _poll_job(watch_id: str, clock: _FixedClock) -> Job:
    now = clock.now()
    return Job(
        id=new_job_id(),
        kind=JobKind.POLL_WATCH,
        payload={"watch_id": watch_id},
        status=JobStatus.RUNNING,
        idempotency_key=f"test:{watch_id}",
        created_at=now,
        updated_at=now,
    )


def _service(
    container,
    clock: _FixedClock,
    parser: FakeFeedParser,
    settings: WatchSettings | None = None,
) -> WatchService:
    return WatchService(
        store=container.store,
        queue=container.queue,
        clock=clock,
        fetcher=container.fetcher,
        ingest=container.ingest,
        parsers={WatchKind.RSS: parser},
        settings=settings or WatchSettings(),
    )


@pytest.fixture
async def container(tmp_path):
    wired = build_test_container(
        tmp_path,
        fetcher=FakeFetcher({FEED_URL: _feed_result()}),
        chunker=FakeChunker(),
    )
    await wired.store.connect()
    await wired.store.migrate()
    yield wired
    await wired.store.close()


async def test_tick_enqueues_due_watch_and_advances_schedule(container):
    clock = _FixedClock(T0)
    service = _service(container, clock, FakeFeedParser())
    watch = await service.create(kind=WatchKind.RSS, url=FEED_URL, interval_hours=24)
    assert watch is not None

    assert await service.tick() == 1
    jobs = await container.store.list_jobs(kind=JobKind.POLL_WATCH)
    assert len(jobs) == 1
    refreshed = await service.get(WatchId(watch.id))
    assert refreshed is not None
    assert refreshed.next_run_at == T0 + timedelta(hours=24)


async def test_tick_is_idempotent_for_the_same_scheduled_instant(container):
    clock = _FixedClock(T0)
    service = _service(container, clock, FakeFeedParser())
    watch = await service.create(kind=WatchKind.RSS, url=FEED_URL)
    assert watch is not None
    await service.tick()

    # A duplicate tick at the same next_run_at reuses the idempotency key.
    await container.store.mark_watch_run(
        watch_id=WatchId(watch.id), next_run_at=T0, last_run_at=T0
    )
    assert await service.tick() == 0
    assert len(await container.store.list_jobs(kind=JobKind.POLL_WATCH)) == 1


async def test_tick_enqueues_every_due_watch(container):
    clock = _FixedClock(T0)
    service = _service(container, clock, FakeFeedParser())
    assert await service.create(kind=WatchKind.RSS, url=FEED_URL) is not None
    assert (
        await service.create(kind=WatchKind.RSS, url="https://feeds.example.com/other")
        is not None
    )

    assert await service.tick() == 2
    assert len(await container.store.list_jobs(kind=JobKind.POLL_WATCH)) == 2


async def test_poll_fans_out_one_ingest_per_item(container):
    clock = _FixedClock(T0)
    parser = FakeFeedParser(_items("https://example.com/a", "https://example.com/b"))
    service = _service(container, clock, parser)
    watch = await service.create(kind=WatchKind.RSS, url=FEED_URL)
    assert watch is not None

    await service.handle_poll_watch(_poll_job(watch.id, clock))

    fetches = await container.store.list_jobs(kind=JobKind.FETCH_AND_INDEX)
    assert len(fetches) == 2
    assert parser.calls == [FEED_URL]
    refreshed = await service.get(WatchId(watch.id))
    assert refreshed is not None
    assert refreshed.last_status == WatchStatus.OK
    assert refreshed.last_item_count == 2


async def test_second_poll_is_a_revisit_no_op(container):
    clock = _FixedClock(T0)
    parser = FakeFeedParser(_items("https://example.com/a"))
    service = _service(container, clock, parser)
    watch = await service.create(kind=WatchKind.RSS, url=FEED_URL)
    assert watch is not None

    await service.handle_poll_watch(_poll_job(watch.id, clock))
    await service.handle_poll_watch(_poll_job(watch.id, clock))

    fetches = await container.store.list_jobs(kind=JobKind.FETCH_AND_INDEX)
    assert len(fetches) == 1


async def test_blacklisted_item_is_skipped(container):
    clock = _FixedClock(T0)
    parser = FakeFeedParser(_items("https://example.com/a", "https://example.com/b"))
    service = _service(container, clock, parser)
    watch = await service.create(kind=WatchKind.RSS, url=FEED_URL)
    assert watch is not None
    await container.forget.forget(url="https://example.com/a")

    await service.handle_poll_watch(_poll_job(watch.id, clock))

    assert (
        await container.store.get_page_by_canonical_url("https://example.com/a") is None
    )
    assert (
        await container.store.get_page_by_canonical_url("https://example.com/b")
        is not None
    )


async def test_poll_caps_at_max_items_per_poll(container):
    clock = _FixedClock(T0)
    parser = FakeFeedParser(
        _items(
            "https://example.com/a",
            "https://example.com/b",
            "https://example.com/c",
        )
    )
    service = _service(container, clock, parser, WatchSettings(max_items_per_poll=2))
    watch = await service.create(kind=WatchKind.RSS, url=FEED_URL)
    assert watch is not None

    await service.handle_poll_watch(_poll_job(watch.id, clock))

    fetches = await container.store.list_jobs(kind=JobKind.FETCH_AND_INDEX)
    assert len(fetches) == 2
    refreshed = await service.get(WatchId(watch.id))
    assert refreshed is not None
    assert refreshed.last_item_count == 2


async def test_disabled_watch_poll_is_a_no_op(container):
    clock = _FixedClock(T0)
    service = _service(
        container, clock, FakeFeedParser(_items("https://example.com/a"))
    )
    watch = await service.create(kind=WatchKind.RSS, url=FEED_URL, enabled=False)
    assert watch is not None

    await service.handle_poll_watch(_poll_job(watch.id, clock))

    assert FEED_URL not in container.fetcher.calls
    assert await container.store.list_jobs(kind=JobKind.FETCH_AND_INDEX) == []


async def test_deleted_watch_poll_is_a_no_op(container):
    clock = _FixedClock(T0)
    service = _service(
        container, clock, FakeFeedParser(_items("https://example.com/a"))
    )
    watch = await service.create(kind=WatchKind.RSS, url=FEED_URL)
    assert watch is not None
    assert await service.delete(WatchId(watch.id)) is True

    await service.handle_poll_watch(_poll_job(watch.id, clock))
    assert FEED_URL not in container.fetcher.calls


async def test_fetch_failure_records_error_and_reraises(container):
    clock = _FixedClock(T0)
    service = _service(container, clock, FakeFeedParser())
    watch = await service.create(
        kind=WatchKind.RSS, url="https://feeds.example.com/missing"
    )
    assert watch is not None

    with pytest.raises(FetchFailedError):
        await service.handle_poll_watch(_poll_job(watch.id, clock))

    refreshed = await service.get(WatchId(watch.id))
    assert refreshed is not None
    assert refreshed.last_status == WatchStatus.ERROR
    assert refreshed.last_error is not None


async def test_run_now_enqueues_and_resets_schedule(container):
    clock = _FixedClock(T0)
    service = _service(container, clock, FakeFeedParser())
    watch = await service.create(kind=WatchKind.RSS, url=FEED_URL, interval_hours=24)
    assert watch is not None
    await container.store.mark_watch_run(
        watch_id=WatchId(watch.id),
        next_run_at=T0 + timedelta(days=10),
        last_run_at=T0,
    )
    clock.advance(timedelta(hours=1))

    job_id = await service.run_now(WatchId(watch.id))
    assert job_id is not None
    assert len(await container.store.list_jobs(kind=JobKind.POLL_WATCH)) == 1
    refreshed = await service.get(WatchId(watch.id))
    assert refreshed is not None
    assert refreshed.next_run_at == clock.now() + timedelta(hours=24)


async def test_run_now_missing_watch_returns_none(container):
    clock = _FixedClock(T0)
    service = _service(container, clock, FakeFeedParser())
    assert await service.run_now(WatchId("does-not-exist")) is None


async def test_create_duplicate_kind_url_returns_none(container):
    clock = _FixedClock(T0)
    service = _service(container, clock, FakeFeedParser())
    assert await service.create(kind=WatchKind.RSS, url=FEED_URL) is not None
    assert await service.create(kind=WatchKind.RSS, url=FEED_URL) is None
