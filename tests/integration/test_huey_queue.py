"""De-risk checkpoint: embedded huey consumer + jobs ledger.

Proves the dispatcher pattern works end to end: the consumer thread executes
tasks by driving coroutines on the main event loop; retries back off through
the ledger; exhausted jobs dead-letter; recovery re-enqueues.
"""

import asyncio
import threading
from collections.abc import AsyncIterator, Mapping
from datetime import timedelta
from pathlib import Path

import pytest

from refindery.adapters.metadata.sqlite_store import SqliteMetadataStore
from refindery.adapters.queue.huey_queue import (
    DeadJobCallback,
    HueyJobQueue,
    JobHandler,
    RetriedJobCallback,
)
from refindery.application.ports.clock import Clock
from refindery.config import JobsSettings
from refindery.domain.ids import JobId, new_job_id
from refindery.domain.models import Job, JobKind, JobStatus
from tests.fakes.clock import FakeClock

WAIT_S = 15.0


async def _wait_for_status(
    store: SqliteMetadataStore, job_id: JobId, status: JobStatus
) -> Job:
    async with asyncio.timeout(WAIT_S):
        while True:
            job = await store.get_job(job_id)
            if job is not None and job.status is status:
                return job
            await asyncio.sleep(0.05)


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[SqliteMetadataStore]:
    async with SqliteMetadataStore(tmp_path / "meta.db") as s:
        await s.migrate()
        yield s


def _queue(
    tmp_path: Path,
    store: SqliteMetadataStore,
    handlers: Mapping[JobKind, JobHandler],
    *,
    on_dead: DeadJobCallback | None = None,
    on_retry: RetriedJobCallback | None = None,
    max_attempts: int = 5,
    clock: Clock | None = None,
    handler_timeout_s: float | None = None,
) -> HueyJobQueue:
    return HueyJobQueue(
        path=tmp_path / "huey.db",
        store=store,
        clock=clock or FakeClock(),
        settings=JobsSettings(
            max_attempts=max_attempts,
            backoff_base_s=0.01,
            handler_timeout_s=handler_timeout_s,
        ),
        handlers=handlers,
        on_dead=on_dead,
        on_retry=on_retry,
    )


async def test_executes_on_main_loop_and_completes(tmp_path, store):
    main_thread = threading.get_ident()
    seen: list[tuple[str, int]] = []

    async def handler(job: Job) -> None:
        seen.append((job.payload["page_id"], threading.get_ident()))

    queue = _queue(tmp_path, store, {JobKind.INDEX_PAGE: handler})
    await queue.start()
    try:
        job_id = await queue.enqueue(
            kind=JobKind.INDEX_PAGE,
            payload={"page_id": "p1"},
            idempotency_key="index:p1:h1",
        )
        assert job_id is not None
        job = await _wait_for_status(store, job_id, JobStatus.DONE)
        assert job.attempts == 0
        assert seen == [("p1", main_thread)]
    finally:
        await queue.stop()


async def test_enqueue_is_idempotent(tmp_path, store):
    async def handler(job: Job) -> None:
        await asyncio.sleep(0)

    queue = _queue(tmp_path, store, {JobKind.INDEX_PAGE: handler})
    await queue.start()
    try:
        first = await queue.enqueue(
            kind=JobKind.INDEX_PAGE, payload={}, idempotency_key="dup"
        )
        second = await queue.enqueue(
            kind=JobKind.INDEX_PAGE, payload={}, idempotency_key="dup"
        )
        assert first is not None
        assert second is None
    finally:
        await queue.stop()


async def test_retries_then_dead_letters_and_manual_retry(tmp_path, store):
    calls: list[int] = []
    dead: list[str] = []
    retried: list[Job] = []

    async def flaky(job: Job) -> None:
        calls.append(1)
        msg = "boom"
        raise RuntimeError(msg)

    async def on_dead(job: Job, error: str) -> None:
        dead.append(error)

    async def on_retry(job: Job) -> None:
        retried.append(job)

    queue = _queue(
        tmp_path,
        store,
        {JobKind.INDEX_PAGE: flaky},
        on_dead=on_dead,
        on_retry=on_retry,
        max_attempts=3,
    )
    await queue.start()
    try:
        job_id = await queue.enqueue(
            kind=JobKind.INDEX_PAGE, payload={}, idempotency_key="flaky"
        )
        assert job_id is not None
        job = await _wait_for_status(store, job_id, JobStatus.DEAD)
        assert len(calls) == 3
        assert job.last_error is not None
        assert "boom" in job.last_error
        assert len(dead) == 1

        # manual retry resets and re-executes (fails again -> dead again)
        calls.clear()
        await queue.retry(job_id)
        await _wait_for_status(store, job_id, JobStatus.DEAD)
        assert len(calls) == 3
        assert len(retried) == 1
        assert retried[0].status is JobStatus.PENDING
        assert retried[0].attempts == 0
    finally:
        await queue.stop()


async def test_manual_retry_fires_on_retry_before_reenqueue(tmp_path, store):
    """Deterministic ordering check: no consumer runs, so nothing races.

    The hook must observe the ledger already reset (PENDING) but the huey
    re-enqueue not yet performed (pending_count still 1).
    """

    async def never_runs(job: Job) -> None:
        msg = "consumer is not started in this test"
        raise AssertionError(msg)

    observed: list[tuple[JobStatus, int]] = []
    queue: HueyJobQueue | None = None

    async def on_retry(job: Job) -> None:
        assert queue is not None
        observed.append((job.status, queue.huey.pending_count()))

    queue = _queue(
        tmp_path,
        store,
        {JobKind.INDEX_PAGE: never_runs},
        on_retry=on_retry,
    )
    job_id = await queue.enqueue(
        kind=JobKind.INDEX_PAGE,
        payload={"page_id": "p1"},
        idempotency_key="index:p1:h1",
    )
    assert job_id is not None
    assert queue.huey.pending_count() == 1
    await store.mark_job_dead(job_id=job_id, last_error="boom", now=FakeClock().now())

    await queue.retry(job_id)

    assert observed == [(JobStatus.PENDING, 1)]
    assert queue.huey.pending_count() == 2


async def test_lease_timeout_cancels_stuck_job_and_frees_worker(tmp_path, store):
    release = asyncio.Event()  # never set: the handler is stuck
    done: list[str] = []

    async def stuck(job: Job) -> None:
        await release.wait()

    async def quick(job: Job) -> None:
        done.append(job.idempotency_key)

    queue = _queue(
        tmp_path,
        store,
        {JobKind.INDEX_PAGE: stuck, JobKind.FETCH_AND_INDEX: quick},
        max_attempts=2,
        handler_timeout_s=0.2,
    )
    await queue.start()
    try:
        stuck_id = await queue.enqueue(
            kind=JobKind.INDEX_PAGE, payload={}, idempotency_key="stuck"
        )
        quick_id = await queue.enqueue(
            kind=JobKind.FETCH_AND_INDEX, payload={}, idempotency_key="quick"
        )
        assert stuck_id is not None
        assert quick_id is not None
        # The stuck job times out twice and dead-letters...
        job = await _wait_for_status(store, stuck_id, JobStatus.DEAD)
        assert job.last_error is not None
        assert "lease timeout" in job.last_error
        # ...and the sole worker was freed to run the second job.
        await _wait_for_status(store, quick_id, JobStatus.DONE)
        assert done == ["quick"]
    finally:
        release.set()
        await queue.stop()


async def test_provider_unavailable_defers_without_burning_attempts(tmp_path, store):
    from refindery.domain.errors import ProviderUnavailableError

    calls: list[int] = []

    async def outage_then_ok(job: Job) -> None:
        calls.append(1)
        if len(calls) < 3:
            raise ProviderUnavailableError(provider="embed:test", retry_after_s=0.01)

    # max_attempts=1: any counted failure would dead-letter immediately, so
    # reaching DONE proves deferrals never touched the attempt budget.
    queue = _queue(
        tmp_path, store, {JobKind.INDEX_PAGE: outage_then_ok}, max_attempts=1
    )
    await queue.start()
    try:
        job_id = await queue.enqueue(
            kind=JobKind.INDEX_PAGE, payload={}, idempotency_key="outage"
        )
        assert job_id is not None
        job = await _wait_for_status(store, job_id, JobStatus.DONE)
        assert len(calls) == 3
        assert job.attempts == 0
    finally:
        await queue.stop()


async def test_recover_re_enqueues_expired_lease_and_orphaned_pending(tmp_path, store):
    done: list[str] = []

    async def handler(job: Job) -> None:
        done.append(job.idempotency_key)

    clock = FakeClock()
    queue = _queue(tmp_path, store, {JobKind.INDEX_PAGE: handler}, clock=clock)

    # Simulate a crash: ledger rows exist but huey has no tasks for them.
    stale = Job(
        id=new_job_id(),
        kind=JobKind.INDEX_PAGE,
        payload={},
        status=JobStatus.PENDING,
        idempotency_key="crashed-pending",
        created_at=clock.now(),
        updated_at=clock.now(),
    )
    await store.create_job(stale)
    running = Job(
        id=new_job_id(),
        kind=JobKind.INDEX_PAGE,
        payload={},
        status=JobStatus.PENDING,
        idempotency_key="crashed-running",
        created_at=clock.now(),
        updated_at=clock.now(),
    )
    await store.create_job(running)
    await store.mark_job_running(
        job_id=running.id,
        lease_until=clock.now() - timedelta(minutes=30),
        now=clock.now(),
    )

    await queue.start()
    try:
        recovered = await queue.recover()
        assert recovered == 2
        await _wait_for_status(store, stale.id, JobStatus.DONE)
        await _wait_for_status(store, running.id, JobStatus.DONE)
        assert sorted(done) == ["crashed-pending", "crashed-running"]
    finally:
        await queue.stop()
