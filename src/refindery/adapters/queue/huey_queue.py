"""Huey-backed JobQueue with a durable ledger.

Division of labor (resolves the spec's jobs-table TODO):

- **huey** (SqliteHuey, embedded consumer thread) provides scheduling, retry
  backoff, and cron. Its queue table is an implementation detail — tasks are
  deleted on dequeue, so it cannot provide recovery or status.
- **The jobs ledger** (metadata store) is the durable source of truth:
  status, attempts, lease_until, last_error, idempotency. Startup recovery
  re-enqueues from the ledger.

The huey task body is a thin dispatcher: it blocks its worker thread on
``run_coroutine_threadsafe`` so all real work — ledger transitions, pipeline
steps, SQLite writes — runs as a coroutine on the single main event loop.
``workers=1`` guarantees at most one job pipeline at a time (the
single-writer invariant).
"""

import asyncio
import logging
import threading
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from datetime import timedelta
from pathlib import Path

from huey import SqliteHuey
from huey.consumer import Consumer
from huey.exceptions import RetryTask

from refindery.adapters.observability.metrics import (
    job_failures_total,
    job_lease_timeouts_total,
)
from refindery.application.ports.clock import Clock
from refindery.application.ports.metadata_store import MetadataStore
from refindery.config import JobsSettings
from refindery.domain.errors import ProviderUnavailableError
from refindery.domain.ids import JobId, new_job_id
from refindery.domain.models import Job, JobKind, JobStatus

logger = logging.getLogger(__name__)

type JobHandler = Callable[[Job], Awaitable[None]]
type DeadJobCallback = Callable[[Job, str], Awaitable[None]]


class EmbeddedConsumer(Consumer):
    """A huey consumer safe to start off the main thread.

    Signal handling belongs to the host process (uvicorn); the embedded
    consumer must not override it, and ``signal.signal`` would raise off
    the main thread anyway.
    """

    def _set_signal_handlers(self) -> None:
        return


class HueyJobQueue:
    """JobQueue implementation: ledger writes + huey enqueue + recovery."""

    def __init__(
        self,
        *,
        path: Path,
        store: MetadataStore,
        clock: Clock,
        settings: JobsSettings,
        handlers: Mapping[JobKind, JobHandler],
        on_dead: DeadJobCallback | None = None,
    ) -> None:
        self._store = store
        self._clock = clock
        self._settings = settings
        self._handlers = dict(handlers)
        self._on_dead = on_dead
        self._loop: asyncio.AbstractEventLoop | None = None
        self._consumer: EmbeddedConsumer | None = None
        self._consumer_thread: threading.Thread | None = None
        path.parent.mkdir(parents=True, exist_ok=True)
        self.huey = SqliteHuey(name="refindery", filename=str(path))
        self._task = self.huey.task(name="execute_job")(self._run_job_in_worker)

    # -- JobQueue port ------------------------------------------------------

    async def enqueue(
        self,
        *,
        kind: JobKind,
        payload: dict[str, str],
        idempotency_key: str,
    ) -> JobId | None:
        """Insert a ledger row and hand the job to huey.

        Returns None when the idempotency key already exists (duplicate).
        """
        now = self._clock.now()
        job = Job(
            id=new_job_id(),
            kind=kind,
            payload=payload,
            status=JobStatus.PENDING,
            idempotency_key=idempotency_key,
            max_attempts=self._settings.max_attempts,
            created_at=now,
            updated_at=now,
        )
        if not await self._store.create_job(job):
            logger.debug(
                "duplicate job for key %s; enqueue is a no-op", idempotency_key
            )
            return None
        await asyncio.to_thread(self._task, str(job.id))
        return job.id

    async def retry(self, job_id: JobId) -> JobId:
        """Re-enqueue a dead job (resets attempts)."""
        job = await self._store.reset_job_for_retry(
            job_id=job_id, now=self._clock.now()
        )
        await asyncio.to_thread(self._task, str(job.id))
        return job.id

    async def recover(self) -> int:
        """Re-enqueue expired leases and pending ledger rows.

        Runs before the consumer starts. Duplicate deliveries are harmless:
        execution re-checks ledger status and skips anything not runnable.
        """
        now = self._clock.now()
        expired = await self._store.reset_expired_leases(now=now)
        pending = await self._store.list_pending_jobs()
        seen: set[JobId] = set()
        recovered = 0
        for job in [*expired, *pending]:
            if job.id in seen:
                continue
            seen.add(job.id)
            await asyncio.to_thread(self._task, str(job.id))
            recovered += 1
        if recovered:
            logger.info("recovered %d jobs at startup", recovered)
        return recovered

    def add_handler(self, kind: JobKind, handler: JobHandler) -> None:
        """Register a handler after construction (breaks wiring cycles)."""
        self._handlers[kind] = handler

    def register_periodic(
        self,
        *,
        name: str,
        schedule: Callable[[object], bool],
        handler: Callable[[], Coroutine[None, None, None]],
    ) -> None:
        """Register a cron-style task (call before start()).

        ``schedule`` is a huey validator, e.g. ``crontab(minute="*/10")``.
        The handler runs on the main event loop like every other job.
        """

        def body() -> None:
            if self._loop is None:  # pragma: no cover — scheduler races startup
                return
            asyncio.run_coroutine_threadsafe(handler(), self._loop).result()

        self.huey.periodic_task(schedule, name=name)(body)

    async def start(self) -> None:
        """Capture the main loop and start the embedded consumer thread."""
        self._loop = asyncio.get_running_loop()
        self._consumer = EmbeddedConsumer(
            self.huey,
            workers=1,
            worker_type="thread",
            periodic=True,
            initial_delay=0.05,
            max_delay=1.0,
            check_worker_health=False,
        )
        self._consumer_thread = threading.Thread(
            target=self._consumer.start, name="huey-consumer", daemon=True
        )
        self._consumer_thread.start()

    async def stop(self) -> None:
        """Stop the consumer, waiting for the in-flight job to finish."""
        if self._consumer is not None:
            await asyncio.to_thread(self._consumer.stop, True)  # noqa: FBT003
            self._consumer = None
        self._consumer_thread = None

    # -- execution ------------------------------------------------------------

    def _run_job_in_worker(self, job_id: str) -> None:
        """Huey task body (worker thread): dispatch to the main event loop."""
        if self._loop is None:
            msg = "job queue consumer running before start()"
            raise RuntimeError(msg)
        future = asyncio.run_coroutine_threadsafe(
            self._execute(JobId(job_id)), self._loop
        )
        retry_delay = future.result()
        if retry_delay is not None:
            raise RetryTask(delay=retry_delay)

    async def _execute(self, job_id: JobId) -> float | None:
        """Run one job on the main loop; return a retry delay or None."""
        job = await self._store.get_job(job_id)
        if job is None:
            logger.warning("job %s delivered but missing from ledger", job_id)
            return None
        if job.status not in {JobStatus.PENDING, JobStatus.FAILED}:
            logger.debug(
                "job %s in status %s; skipping duplicate delivery", job_id, job.status
            )
            return None

        now = self._clock.now()
        lease_until = now + timedelta(minutes=self._settings.lease_minutes)
        await self._store.mark_job_running(
            job_id=job.id, lease_until=lease_until, now=now
        )
        timeout_s = (
            self._settings.handler_timeout_s
            if self._settings.handler_timeout_s is not None
            else self._settings.lease_minutes * 60.0
        )
        lease_timeout = asyncio.timeout(timeout_s)
        try:
            if (handler := self._handlers.get(job.kind)) is None:
                msg = f"no handler registered for job kind {job.kind!r}"
                raise RuntimeError(msg)  # noqa: TRY301
            # Cooperative cancellation at lease expiry frees the sole worker.
            # A CancelledError from loop shutdown is a BaseException and
            # propagates untouched — it must never hit the failure ledger.
            async with lease_timeout:
                await handler(job)
        except TimeoutError as exc:
            if lease_timeout.expired():
                logger.exception(
                    "job %s (%s) exceeded its lease (%.0fs); cancelled",
                    job.id,
                    job.kind,
                    timeout_s,
                )
                job_lease_timeouts_total.labels(kind=job.kind).inc()
                error = f"lease timeout after {timeout_s:.0f}s"
            else:
                # A provider timeout that escaped the handler, not the lease.
                logger.exception("job %s (%s) failed", job.id, job.kind)
                error = repr(exc)
            return await self._record_failure(job, error=error)
        except ProviderUnavailableError as exc:
            # Outage deferral: a breaker-open fast-fail must not burn the
            # attempt budget; requeue after the provider's cooldown.
            logger.warning("job %s (%s) deferred: %s", job.id, job.kind, exc)
            await self._store.mark_job_failed(
                job_id=job.id,
                attempts=job.attempts,
                last_error=str(exc),
                now=self._clock.now(),
            )
            return max(self._settings.backoff_base_s, exc.retry_after_s)
        except Exception as exc:
            logger.exception("job %s (%s) failed", job.id, job.kind)
            return await self._record_failure(job, error=repr(exc))
        await self._store.mark_job_done(job_id=job.id, now=self._clock.now())
        return None

    async def _record_failure(self, job: Job, *, error: str) -> float | None:
        job_failures_total.labels(kind=job.kind).inc()
        attempts = job.attempts + 1
        now = self._clock.now()
        if attempts >= job.max_attempts:
            await self._store.mark_job_dead(job_id=job.id, last_error=error, now=now)
            if self._on_dead is not None:
                await self._on_dead(job, error)
            return None
        await self._store.mark_job_failed(
            job_id=job.id, attempts=attempts, last_error=error, now=now
        )
        return self._settings.backoff_base_s * (2.0**attempts)
