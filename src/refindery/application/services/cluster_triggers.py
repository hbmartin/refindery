"""Cluster run triggers: idle detection (cron and manual live elsewhere).

Idle rule (spec §8.3, run-duration-derived): re-cluster only when ingest has
been quiet for ``clamp(median(last 5 run durations) * 3, 5min, 60min)`` —
the system never re-clusters more often than clustering costs — AND at least
``min_new_pages`` were indexed since the last run.
"""

import logging
import statistics
from datetime import timedelta

from refindery.application.ports.clock import Clock
from refindery.application.ports.metadata_store import MetadataStore
from refindery.config import ClusterSettings

logger = logging.getLogger(__name__)

_MIN_IDLE = timedelta(minutes=5)
_MAX_IDLE = timedelta(minutes=60)


class IdleDetector:
    """Pure decision logic over store reads and the injectable clock."""

    def __init__(
        self, *, store: MetadataStore, clock: Clock, settings: ClusterSettings
    ) -> None:
        self._store = store
        self._clock = clock
        self._settings = settings

    async def idle_threshold(self) -> timedelta:
        """Run-duration-derived threshold with a default before history exists."""
        durations = await self._store.recent_run_durations_ms(limit=5)
        if not durations:
            return timedelta(minutes=self._settings.idle_default_minutes)
        median_ms = statistics.median(durations)
        derived = timedelta(milliseconds=median_ms * 3)
        return max(_MIN_IDLE, min(derived, _MAX_IDLE))

    async def should_run(self) -> bool:
        """Idle long enough AND enough new pages since the last run."""
        last_ingest = await self._store.last_ingest_at()
        if last_ingest is None:
            return False
        now = self._clock.now()
        if now - last_ingest < await self.idle_threshold():
            return False
        last_run = await self._store.last_run_finished_at()
        if last_run is None:
            return await self._store.count_indexed_pages() >= self._settings.min_pages
        new_pages = await self._store.pages_indexed_since(last_run)
        return new_pages >= self._settings.min_new_pages
