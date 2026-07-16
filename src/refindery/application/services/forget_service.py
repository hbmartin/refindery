"""Forget: purge a URL or domain and blacklist it, atomically.

Metadata deletion is authoritative and immediate (purged pages can never
surface in results — hydration drops them); vector deletion is asynchronous
via tombstones with a verification sweep.
"""

import json
import logging
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlsplit

from refindery.adapters.observability.metrics import vector_tombstone_backlog
from refindery.application.ports.clock import Clock
from refindery.application.ports.job_queue import JobQueue
from refindery.application.ports.metadata_store import MetadataStore
from refindery.application.ports.vector_store import VectorStore
from refindery.domain.canonical_url import CanonicalizationRules, canonicalize
from refindery.domain.ids import PageId, new_blacklist_id
from refindery.domain.job_keys import purge_vectors_key
from refindery.domain.models import (
    BlacklistKind,
    BlacklistRule,
    Job,
    JobKind,
    TombstoneStatus,
)

logger = logging.getLogger(__name__)

_VERIFIED_RETENTION_DAYS = 30


@dataclass(frozen=True, slots=True)
class ForgetOutcome:
    """What POST /v1/forget returns."""

    rule: BlacklistRule
    pages_purged: int
    vector_deletes_queued: int


class ForgetService:
    """Purge + blacklist + vector tombstone lifecycle."""

    def __init__(
        self,
        *,
        store: MetadataStore,
        vector_store: VectorStore,
        queue: JobQueue,
        clock: Clock,
        rules: CanonicalizationRules | None = None,
    ) -> None:
        self._store = store
        self._vector_store = vector_store
        self._queue = queue
        self._clock = clock
        self._rules = rules or CanonicalizationRules()

    async def forget(
        self,
        *,
        url: str | None = None,
        domain: str | None = None,
        reason: str | None = None,
    ) -> ForgetOutcome:
        """Purge and blacklist one URL or one domain (exactly one given)."""
        match (url, domain):
            case (str() as target_url, None):
                pattern = canonicalize(target_url, rules=self._rules).url
                kind = BlacklistKind.URL
            case (None, str() as target_domain):
                pattern = self._normalize_domain(target_domain)
                kind = BlacklistKind.DOMAIN
            case _:
                msg = "provide exactly one of url or domain"
                raise ValueError(msg)

        rule = BlacklistRule(
            id=new_blacklist_id(),
            pattern=pattern,
            kind=kind,
            created_at=self._clock.now(),
            reason=reason,
        )
        effective, purged = await self._store.purge_and_blacklist(rule)
        if purged:
            await self._enqueue_purge(purged)
        return ForgetOutcome(
            rule=effective,
            pages_purged=len(purged),
            vector_deletes_queued=len(purged),
        )

    @staticmethod
    def _normalize_domain(value: str) -> str:
        """Leniently normalize a domain-like forget target."""
        text = value.strip().lower()
        if "://" not in text:
            text = f"//{text}"
        parts = urlsplit(text)
        host = parts.hostname or parts.path.split("/", maxsplit=1)[0]
        host = host.removeprefix("www.").rstrip(".")
        if not host or "." not in host:
            msg = f"invalid domain: {value!r}"
            raise ValueError(msg)
        return host

    async def _enqueue_purge(self, page_ids: list[PageId]) -> None:
        await self._queue.enqueue(
            kind=JobKind.PURGE_VECTORS,
            payload={"page_ids": json.dumps(page_ids)},
            idempotency_key=purge_vectors_key(page_ids),
        )

    # -- job handlers -------------------------------------------------------------

    async def handle_purge_vectors(self, job: Job) -> None:
        """Delete purged pages' vectors; tombstones advance to deleted."""
        page_ids = [PageId(pid) for pid in json.loads(job.payload["page_ids"])]
        await self._vector_store.delete_pages(page_ids)
        await self._store.set_tombstone_status(
            page_ids=page_ids,
            status=TombstoneStatus.DELETED,
            now=self._clock.now(),
        )

    async def verify_tombstones(self) -> None:
        """Periodic sweep: verify deletions, re-drive stragglers, GC old rows."""
        now = self._clock.now()

        deleted = await self._store.list_tombstones(status=TombstoneStatus.DELETED)
        verified: list[PageId] = []
        lying: list[PageId] = []
        for tombstone in deleted:
            remaining = await self._vector_store.count_chunks(tombstone.page_id)
            (verified if remaining == 0 else lying).append(tombstone.page_id)
        if verified:
            await self._store.set_tombstone_status(
                page_ids=verified, status=TombstoneStatus.VERIFIED, now=now
            )
        if lying:
            logger.warning("tombstones reverted to pending: %d", len(lying))
            await self._store.set_tombstone_status(
                page_ids=lying,
                status=TombstoneStatus.PENDING,
                now=now,
                last_error="vectors still present after delete",
            )

        pending = await self._store.list_tombstones(status=TombstoneStatus.PENDING)
        stale = [
            t.page_id for t in pending if now - t.updated_at > timedelta(minutes=10)
        ]
        if stale:
            await self._enqueue_purge(stale)

        old_verified = [
            t.page_id
            for t in await self._store.list_tombstones(status=TombstoneStatus.VERIFIED)
            if now - t.updated_at > timedelta(days=_VERIFIED_RETENTION_DAYS)
        ]
        if old_verified:
            await self._store.delete_tombstones(old_verified)

        counts = await self._store.count_tombstones_by_status()
        for tombstone_status in TombstoneStatus:
            vector_tombstone_backlog.labels(status=tombstone_status.value).set(
                counts.get(tombstone_status, 0)
            )
