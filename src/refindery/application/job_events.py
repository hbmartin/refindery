"""In-process job event bus feeding the SSE stream.

Single-loop only: every publish happens on the main event loop (all ledger
transitions run there via ``HueyJobQueue``), so no locking is needed. A slow
subscriber drops its oldest events — each event carries the job's full state,
so the newest event per job is the one that matters.
"""

import asyncio
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Self

from refindery.domain.models import Job, JobKind, JobStatus

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class JobEvent:
    """Full job state at one ledger transition."""

    job_id: str
    kind: JobKind
    status: JobStatus
    attempts: int
    max_attempts: int
    last_error: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_job(
        cls,
        job: Job,
        *,
        status: JobStatus | None = None,
        attempts: int | None = None,
        last_error: str | None = None,
        updated_at: datetime | None = None,
    ) -> Self:
        """Build an event from a ledger row; None overrides keep the row's value."""
        return cls(
            job_id=job.id,
            kind=job.kind,
            status=status if status is not None else job.status,
            attempts=attempts if attempts is not None else job.attempts,
            max_attempts=job.max_attempts,
            last_error=last_error if last_error is not None else job.last_error,
            created_at=job.created_at,
            updated_at=updated_at if updated_at is not None else job.updated_at,
        )


class SubscriberLimitError(Exception):
    """No subscriber slot available (or the bus is closed)."""


class JobEventBus:
    """Fan-out of job events to bounded per-subscriber queues."""

    def __init__(self, *, queue_size: int = 256, max_subscribers: int = 16) -> None:
        self._queue_size = queue_size
        self._max_subscribers = max_subscribers
        self._subscribers: list[asyncio.Queue[JobEvent | None]] = []
        self._closed = False

    @property
    def subscriber_count(self) -> int:
        """Currently subscribed stream count."""
        return len(self._subscribers)

    def has_capacity(self) -> bool:
        """Whether one more subscriber may attach."""
        return not self._closed and len(self._subscribers) < self._max_subscribers

    @contextmanager
    def subscribed(self) -> Iterator["asyncio.Queue[JobEvent | None]"]:
        """Attach a subscriber queue for the duration of the context."""
        if not self.has_capacity():
            raise SubscriberLimitError
        queue: asyncio.Queue[JobEvent | None] = asyncio.Queue(maxsize=self._queue_size)
        self._subscribers.append(queue)
        try:
            yield queue
        finally:
            with_queue = [q for q in self._subscribers if q is not queue]
            self._subscribers = with_queue

    def publish(self, event: JobEvent) -> None:
        """Deliver to every subscriber, dropping the oldest event when full."""
        if self._closed:
            return
        for queue in self._subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                try:  # newest-wins: each event is the job's full state
                    queue.get_nowait()
                    queue.put_nowait(event)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    logger.warning("job event subscriber thrashing; event dropped")

    def close(self) -> None:
        """Mute future publishes and signal every subscriber to end its stream."""
        if self._closed:
            return
        self._closed = True
        for queue in self._subscribers:
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                    queue.put_nowait(None)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    logger.warning("job event subscriber missed close sentinel")
