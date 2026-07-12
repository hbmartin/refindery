"""JobEventBus: delivery order, overflow, close semantics, subscriber limit."""

from datetime import UTC, datetime

import pytest

from refindery.application.job_events import (
    JobEvent,
    JobEventBus,
    SubscriberLimitError,
)
from refindery.domain.ids import new_job_id
from refindery.domain.models import Job, JobKind, JobStatus

NOW = datetime(2026, 7, 8, 12, 0, 0, tzinfo=UTC)


def _job() -> Job:
    return Job(
        id=new_job_id(),
        kind=JobKind.INDEX_PAGE,
        payload={},
        status=JobStatus.PENDING,
        idempotency_key=f"k:{new_job_id()}",
        created_at=NOW,
        updated_at=NOW,
    )


def _event(status: JobStatus = JobStatus.PENDING) -> JobEvent:
    return JobEvent.from_job(_job(), status=status)


def test_from_job_overrides_only_supplied_fields():
    job = _job()
    event = JobEvent.from_job(job, status=JobStatus.RUNNING, attempts=2)
    assert event.job_id == job.id
    assert event.status is JobStatus.RUNNING
    assert event.attempts == 2
    assert event.max_attempts == job.max_attempts
    assert event.last_error is None
    assert event.updated_at == job.updated_at


async def test_fifo_delivery_to_all_subscribers():
    bus = JobEventBus()
    first, second = _event(), _event(JobStatus.DONE)
    with bus.subscribed() as queue_a, bus.subscribed() as queue_b:
        bus.publish(first)
        bus.publish(second)
        assert await queue_a.get() is first
        assert await queue_a.get() is second
        assert await queue_b.get() is first
        assert await queue_b.get() is second


async def test_overflow_drops_oldest():
    bus = JobEventBus(queue_size=2)
    events = [_event() for _ in range(3)]
    with bus.subscribed() as queue:
        for event in events:
            bus.publish(event)
        assert await queue.get() is events[1]
        assert await queue.get() is events[2]
        assert queue.empty()


async def test_close_sends_sentinel_and_mutes_publish():
    bus = JobEventBus()
    with bus.subscribed() as queue:
        bus.close()
        assert await queue.get() is None
        bus.publish(_event())
        assert queue.empty()
    assert not bus.has_capacity()


async def test_subscriber_limit():
    bus = JobEventBus(max_subscribers=1)
    with bus.subscribed():
        assert not bus.has_capacity()
        with pytest.raises(SubscriberLimitError), bus.subscribed():
            pass  # pragma: no cover — never reached
    assert bus.has_capacity()


async def test_context_exit_unsubscribes():
    bus = JobEventBus()
    with bus.subscribed():
        assert bus.subscriber_count == 1
    assert bus.subscriber_count == 0
    bus.publish(_event())  # no subscriber: publish is a silent no-op
