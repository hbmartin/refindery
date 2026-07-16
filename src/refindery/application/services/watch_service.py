"""Watch service: schedule source polls and fan discovered URLs into ingest.

A minute-level periodic calls :meth:`tick`, which enqueues one durable
``POLL_WATCH`` job per due watch (with a time-varying idempotency key) and
advances the schedule. The ``POLL_WATCH`` handler fetches the source, parses
its child URLs, and calls :class:`IngestService` per URL — reusing all
canonicalization, blacklist, dedup, and fetch/extract/index machinery. The
watch never fetches article bodies itself.

Scheduling is advanced in :meth:`tick` (at enqueue time), not in the handler,
so a permanently-failing poll cannot freeze ``next_run_at``: each interval
produces a fresh idempotency key and the watch keeps firing.
"""

import logging
from collections.abc import Mapping
from datetime import timedelta

from refindery.application.ports.clock import Clock
from refindery.application.ports.content_extractor import Fetcher
from refindery.application.ports.feed_parser import FeedItem, FeedParser
from refindery.application.ports.job_queue import JobQueue
from refindery.application.ports.metadata_store import MetadataStore
from refindery.application.services.ingest import IngestRequest, IngestService
from refindery.config import WatchSettings
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


class WatchService:
    """Manages watches and executes their periodic polls."""

    def __init__(
        self,
        *,
        store: MetadataStore,
        queue: JobQueue,
        clock: Clock,
        fetcher: Fetcher,
        ingest: IngestService,
        parsers: Mapping[WatchKind, FeedParser],
        settings: WatchSettings,
    ) -> None:
        self._store = store
        self._queue = queue
        self._clock = clock
        self._fetcher = fetcher
        self._ingest = ingest
        self._parsers = parsers
        self._settings = settings

    # -- CRUD -------------------------------------------------------------------

    async def create(
        self,
        *,
        kind: WatchKind,
        url: str,
        interval_hours: int | None = None,
        enabled: bool = True,
        config: Mapping[str, object] | None = None,
    ) -> Watch | None:
        """Create a watch; None when (kind, url) already exists.

        ``next_run_at`` is set to now so the first poll fires on the next tick.
        """
        now = self._clock.now()
        watch = Watch(
            id=new_watch_id(),
            kind=kind,
            url=url,
            interval_hours=interval_hours or self._settings.default_interval_hours,
            enabled=enabled,
            config=None if config is None else dict(config),
            next_run_at=now,
            last_run_at=None,
            last_status=WatchStatus.PENDING,
            last_error=None,
            last_item_count=None,
            created_at=now,
        )
        return watch if await self._store.create_watch(watch) else None

    async def list_watches(self) -> list[Watch]:
        """All watches, newest first."""
        return await self._store.list_watches()

    async def get(self, watch_id: WatchId) -> Watch | None:
        """Fetch one watch."""
        return await self._store.get_watch(watch_id)

    async def delete(self, watch_id: WatchId) -> bool:
        """Delete a watch; False when it did not exist."""
        return await self._store.delete_watch(watch_id)

    async def run_now(self, watch_id: WatchId) -> JobId | None:
        """Enqueue an immediate poll and reset the schedule.

        Returns the enqueued job id, or None when the watch does not exist.
        The manual idempotency key embeds the current instant, so it is always
        unique — a None return unambiguously means "not found".
        """
        watch = await self._store.get_watch(watch_id)
        if watch is None:
            return None
        now = self._clock.now()
        job_id = await self._queue.enqueue(
            kind=JobKind.POLL_WATCH,
            payload={"watch_id": watch.id},
            idempotency_key=f"poll_watch:{watch.id}:manual:{now.isoformat()}",
        )
        await self._store.mark_watch_run(
            watch_id=watch.id,
            next_run_at=now + timedelta(hours=watch.interval_hours),
            last_run_at=now,
        )
        return job_id

    # -- scheduling -------------------------------------------------------------

    async def tick(self) -> int:
        """Enqueue a poll for every due watch; return how many were enqueued.

        One failing watch never blocks the rest. The idempotency key uses the
        watch's scheduled instant, so a duplicate tick is a harmless no-op.
        """
        now = self._clock.now()
        enqueued = 0
        for watch in await self._store.list_due_watches(now=now):
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
                    next_run_at=now + timedelta(hours=watch.interval_hours),
                    last_run_at=now,
                )
                if job_id is not None:
                    enqueued += 1
            except Exception:
                # Isolate one watch's failure so the rest still get enqueued.
                logger.exception("watch %s tick failed", watch.id)
        return enqueued

    # -- poll handler -----------------------------------------------------------

    async def handle_poll_watch(self, job: Job) -> None:
        """Fetch a watch's source, parse it, and fan out ingests per URL."""
        watch = await self._store.get_watch(WatchId(job.payload["watch_id"]))
        if watch is None or not watch.enabled:
            return
        parser = self._parsers.get(watch.kind)
        if parser is None:  # pragma: no cover — guarded by create() validation
            await self._store.record_watch_result(
                watch_id=watch.id,
                status=WatchStatus.ERROR,
                last_error=f"no parser registered for {watch.kind}",
                item_count=None,
                now=self._clock.now(),
            )
            return
        try:
            result = await self._fetcher.fetch(watch.url)
            items = await parser.parse(raw=result.body, base_url=result.final_url)
        except Exception as exc:
            await self._store.record_watch_result(
                watch_id=watch.id,
                status=WatchStatus.ERROR,
                last_error=repr(exc),
                item_count=None,
                now=self._clock.now(),
            )
            raise
        kept, dropped = self._cap(items)
        counts = await self._fan_out(watch, kept)
        await self._store.record_watch_result(
            watch_id=watch.id,
            status=WatchStatus.OK,
            last_error=None,
            item_count=len(kept),
            now=self._clock.now(),
        )
        logger.info(
            "watch %s (%s): %d items kept, %d dropped, %s",
            watch.id,
            watch.url,
            len(kept),
            dropped,
            counts,
        )

    def _cap(self, items: list[FeedItem]) -> tuple[list[FeedItem], int]:
        """Keep the newest ``max_items_per_poll`` items; return (kept, dropped)."""
        cap = self._settings.max_items_per_poll
        if len(items) <= cap:
            return items, 0
        return items[:cap], len(items) - cap

    async def _fan_out(self, watch: Watch, items: list[FeedItem]) -> dict[str, int]:
        """Ingest each item URL; tally outcomes. One bad URL never aborts."""
        counts = {"queued": 0, "revisit": 0, "blacklisted": 0, "error": 0}
        source = f"watch:{watch.kind.value}:{watch.id}"
        for item in items:
            try:
                outcome = await self._ingest.ingest(
                    IngestRequest(
                        url=item.url,
                        title=item.title,
                        source=source,
                        fetched_at=item.published_at,
                    )
                )
            except Exception:
                # One bad item never aborts the whole poll.
                logger.exception("watch %s: ingest failed for %s", watch.id, item.url)
                counts["error"] += 1
                continue
            match outcome:
                case IngestQueued():
                    counts["queued"] += 1
                case IngestRevisit():
                    counts["revisit"] += 1
                case IngestBlacklisted():
                    counts["blacklisted"] += 1
        return counts
