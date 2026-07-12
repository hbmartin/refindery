"""Watch service: scheduled pull sources that fan out into ingest.

Scheduling invariants:

- ``tick`` advances ``next_run_at`` at enqueue time, never the poll handler,
  so a permanently failing poll job cannot freeze a watch's schedule.
- The poll job's idempotency key embeds the scheduled ``next_run_at``, so a
  racing duplicate tick dedups to a no-op while later ticks still enqueue.
- Discovered URLs go through ``IngestService.ingest``, which dedups by
  canonical URL globally (across watches and manual adds).
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta

from refindery.application.ports.clock import Clock
from refindery.application.ports.job_queue import JobQueue
from refindery.application.ports.metadata_store import MetadataStore
from refindery.application.ports.watch_source import WatchItem, WatchSource
from refindery.application.services.ingest import IngestRequest, IngestService
from refindery.config import WatchSettings
from refindery.domain.errors import (
    WatchFanOutError,
    WatchNotFoundError,
    WatchSourceUnavailableError,
)
from refindery.domain.ids import JobId, WatchId, new_watch_id
from refindery.domain.models import (
    IngestBlacklisted,
    IngestQueued,
    IngestRevisit,
    Job,
    JobKind,
    Watch,
    WatchKind,
    WatchStatus,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _UnsetValue:
    """Sentinel that distinguishes omitted PATCH fields from explicit nulls."""


UNSET = _UnsetValue()


@dataclass(frozen=True, slots=True)
class WatchPatch:
    """Partial watch update with explicit clearing for nullable fields."""

    enabled: bool | None = None
    interval_hours: int | None = None
    title: str | None | _UnsetValue = UNSET
    config: dict[str, str] | None | _UnsetValue = UNSET


@dataclass(frozen=True, slots=True)
class FanOutTally:
    """Per-poll ingest outcome counts."""

    queued: int = 0
    revisits: int = 0
    blacklisted: int = 0
    errors: int = 0


class WatchService:
    """CRUD, scheduling tick, and poll handler for watches."""

    def __init__(
        self,
        *,
        store: MetadataStore,
        queue: JobQueue,
        clock: Clock,
        ingest: IngestService,
        sources: Mapping[WatchKind, WatchSource],
        settings: WatchSettings,
    ) -> None:
        self._store = store
        self._queue = queue
        self._clock = clock
        self._ingest = ingest
        self._sources = sources
        self._settings = settings

    @property
    def supported_kinds(self) -> frozenset[WatchKind]:
        """Watch kinds with a wired source (extras may disable some)."""
        return frozenset(self._sources)

    # -- CRUD ------------------------------------------------------------

    async def create(
        self,
        *,
        kind: WatchKind,
        url: str,
        title: str | None = None,
        interval_hours: int | None = None,
        enabled: bool = True,
        config: dict[str, str] | None = None,
    ) -> Watch | None:
        """Create a watch due immediately; None when (kind, url) exists."""
        now = self._clock.now()
        watch = Watch(
            id=new_watch_id(),
            kind=kind,
            url=url,
            title=title,
            enabled=enabled,
            interval_hours=interval_hours or self._settings.default_interval_hours,
            config=config,
            next_run_at=now,
            last_run_at=None,
            last_status=WatchStatus.PENDING,
            last_error=None,
            last_item_count=None,
            created_at=now,
            updated_at=now,
        )
        created = await self._store.create_watch(watch)
        return watch if created else None

    async def get(self, watch_id: WatchId) -> Watch | None:
        """Fetch one watch."""
        return await self._store.get_watch(watch_id)

    async def list_all(self) -> list[Watch]:
        """All watches, newest first."""
        return await self._store.list_watches()

    async def update(self, watch_id: WatchId, patch: WatchPatch) -> Watch | None:
        """Apply a partial update; None when the watch does not exist."""
        watch = await self._store.get_watch(watch_id)
        if watch is None:
            return None
        now = self._clock.now()
        if patch.enabled is not None:
            watch.enabled = patch.enabled
        if not isinstance(patch.title, _UnsetValue):
            watch.title = patch.title
        if not isinstance(patch.config, _UnsetValue):
            watch.config = patch.config
        if (
            patch.interval_hours is not None
            and patch.interval_hours != watch.interval_hours
        ):
            watch.interval_hours = patch.interval_hours
            base = watch.last_run_at or now
            watch.next_run_at = base + timedelta(hours=patch.interval_hours)
        watch.updated_at = now
        await self._store.update_watch(watch)
        return watch

    async def delete(self, watch_id: WatchId) -> bool:
        """Delete a watch; False when it does not exist."""
        return await self._store.delete_watch(watch_id)

    async def run_now(self, watch_id: WatchId) -> JobId | None:
        """Enqueue an immediate poll and advance the schedule.

        Returns the poll job id (None only on a same-instant duplicate);
        raises WatchNotFoundError for an unknown watch.
        """
        watch = await self._store.get_watch(watch_id)
        if watch is None:
            raise WatchNotFoundError(watch_id)
        now = self._clock.now()
        job_id = await self._queue.enqueue(
            kind=JobKind.POLL_WATCH,
            payload={"watch_id": watch.id},
            idempotency_key=f"poll_watch:{watch.id}:manual:{now.isoformat()}",
        )
        await self._store.mark_watch_run(
            watch_id=watch.id,
            last_run_at=now,
            next_run_at=now + timedelta(hours=watch.interval_hours),
        )
        return job_id

    # -- scheduling ------------------------------------------------------

    async def tick(self) -> int:
        """Enqueue one poll job per due watch; returns how many were enqueued."""
        now = self._clock.now()
        due = await self._store.list_due_watches(
            now=now, limit=self._settings.max_due_per_tick
        )
        enqueued = 0
        for watch in due:
            try:
                job_id = await self._queue.enqueue(
                    kind=JobKind.POLL_WATCH,
                    payload={"watch_id": watch.id},
                    idempotency_key=(
                        f"poll_watch:{watch.id}:{watch.next_run_at.isoformat()}"
                    ),
                )
                await self._store.mark_watch_run(
                    watch_id=watch.id,
                    last_run_at=now,
                    next_run_at=now + timedelta(hours=watch.interval_hours),
                )
            except Exception:  # one bad watch must not block the rest
                logger.exception("watch tick failed for watch %s", watch.id)
                continue
            if job_id is not None:
                enqueued += 1
        return enqueued

    # -- poll handler ------------------------------------------------------

    async def handle_poll_watch(self, job: Job) -> None:
        """Discover a watch's items and fan them out into ingest."""
        watch_id = WatchId(job.payload["watch_id"])
        watch = await self._store.get_watch(watch_id)
        if watch is None or not watch.enabled:
            logger.info("poll_watch %s skipped: watch missing or disabled", watch_id)
            return
        source = self._sources.get(watch.kind)
        if source is None:
            unavailable = WatchSourceUnavailableError(kind=watch.kind)
            await self._record_error(watch, str(unavailable))
            raise unavailable
        try:
            items = self._cap(
                await source.discover(url=watch.url, config=watch.config or {})
            )
        except Exception as exc:
            await self._record_error(watch, str(exc))
            raise
        tally = await self._fan_out(watch, items)
        if items and tally.errors == len(items):
            failure = WatchFanOutError(watch_id=watch.id, item_count=len(items))
            await self._record_error(watch, str(failure))
            raise failure
        await self._store.record_watch_result(
            watch_id=watch.id,
            status=WatchStatus.OK,
            last_error=None,
            item_count=len(items),
            now=self._clock.now(),
        )
        logger.info(
            "poll_watch %s: %d items (%d queued, %d revisits, %d blacklisted, "
            "%d errors)",
            watch.id,
            len(items),
            tally.queued,
            tally.revisits,
            tally.blacklisted,
            tally.errors,
        )

    async def _record_error(self, watch: Watch, detail: str) -> None:
        await self._store.record_watch_result(
            watch_id=watch.id,
            status=WatchStatus.ERROR,
            last_error=detail,
            item_count=None,
            now=self._clock.now(),
        )

    def _cap(self, items: list[WatchItem]) -> list[WatchItem]:
        """Keep the newest max_items_per_poll items; log what was dropped."""
        cap = self._settings.max_items_per_poll
        if len(items) <= cap:
            return items
        logger.warning(
            "watch poll returned %d items; keeping newest %d", len(items), cap
        )
        ordered = sorted(
            items,
            key=lambda item: (item.published_at is not None, item.published_at),
            reverse=True,
        )
        return ordered[:cap]

    async def _fan_out(self, watch: Watch, items: list[WatchItem]) -> FanOutTally:
        """Ingest each discovered item; one bad item never aborts the poll."""
        queued = revisits = blacklisted = errors = 0
        for item in items:
            try:
                outcome = await self._ingest.ingest(
                    IngestRequest(
                        url=item.url,
                        title=item.title,
                        source=f"watch:{watch.kind}:{watch.id}",
                    )
                )
            except Exception:  # one bad item must not abort the poll
                logger.exception("watch %s: ingest failed for %s", watch.id, item.url)
                errors += 1
                continue
            match outcome:
                case IngestQueued():
                    queued += 1
                case IngestRevisit():
                    revisits += 1
                case IngestBlacklisted():
                    blacklisted += 1
        return FanOutTally(
            queued=queued,
            revisits=revisits,
            blacklisted=blacklisted,
            errors=errors,
        )
