"""Ingest service: the single entry point for new pages.

Flow: canonicalize -> blacklist check -> revisit detection -> body
resolution -> insert + enqueue. POST returns immediately; when no body was
supplied, a fetch_and_index job resolves it asynchronously.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from refindery.application.ports.clock import Clock
from refindery.application.ports.content_extractor import FetchRoute
from refindery.application.ports.job_queue import JobQueue
from refindery.application.ports.metadata_store import MetadataStore
from refindery.application.services.extraction_router import ExtractionRouter
from refindery.domain.canonical_url import CanonicalizationRules, canonicalize
from refindery.domain.content_hash import content_hash
from refindery.domain.errors import BodyConflictError
from refindery.domain.ids import new_page_id
from refindery.domain.models import (
    IngestBlacklisted,
    IngestOutcome,
    IngestQueued,
    IngestRevisit,
    JobKind,
    Page,
    PageStatus,
)


@dataclass(frozen=True, slots=True)
class IngestRequest:
    """Validated ingest input (the API layer maps its pydantic model here)."""

    url: str
    title: str | None = None
    body_extracted: str | None = None
    body_html: str | None = None
    fetched_at: datetime | None = None
    source: str | None = None
    metadata: Mapping[str, object] | None = None
    fetch_route: FetchRoute = FetchRoute.AUTO


class IngestService:
    """Handles POST /v1/pages semantics."""

    def __init__(
        self,
        *,
        store: MetadataStore,
        queue: JobQueue,
        clock: Clock,
        router: ExtractionRouter,
        rules: CanonicalizationRules | None = None,
    ) -> None:
        self._store = store
        self._queue = queue
        self._clock = clock
        self._router = router
        self._rules = rules or CanonicalizationRules()

    async def ingest(self, request: IngestRequest) -> IngestOutcome:
        """Ingest one page; returns Queued | Revisit | Blacklisted."""
        if request.body_extracted is not None and request.body_html is not None:
            raise BodyConflictError

        canonical = canonicalize(request.url, rules=self._rules)

        rule = await self._store.find_blacklist_match(
            canonical_url=canonical.url, domain=canonical.domain
        )
        if rule is not None:
            return IngestBlacklisted(pattern=rule.pattern)

        existing = await self._store.get_page_by_canonical_url(canonical.url)
        if existing is not None:
            await self._store.record_revisit(
                page_id=existing.id, seen_at=self._clock.now()
            )
            offered = await self._offered_body_for_hash(request)
            differs = (
                offered is not None
                and existing.content_hash is not None
                and content_hash(offered) != existing.content_hash
            )
            return IngestRevisit(
                page_id=existing.id,
                status=existing.status,
                content_hash_differs=differs,
            )

        body_text = await self._resolve_body(request)
        now = self._clock.now()
        page = Page(
            id=new_page_id(),
            canonical_url=canonical.url,
            original_url=request.url,
            domain=canonical.domain,
            title=request.title,
            body_text=body_text,
            content_hash=None if body_text is None else content_hash(body_text),
            source=request.source,
            metadata=None if request.metadata is None else dict(request.metadata),
            first_seen_at=request.fetched_at or now,
            last_seen_at=request.fetched_at or now,
            visit_count=1,
            indexed_at=None,
            status=PageStatus.QUEUED,
        )
        await self._store.insert_page(page)

        if body_text is None:
            payload: dict[str, str] = {"page_id": page.id}
            if request.fetch_route is not FetchRoute.AUTO:
                payload["fetch_route"] = request.fetch_route.value
            await self._queue.enqueue(
                kind=JobKind.FETCH_AND_INDEX,
                payload=payload,
                idempotency_key=f"fetch:{page.id}",
            )
        else:
            await self._queue.enqueue(
                kind=JobKind.INDEX_PAGE,
                payload={"page_id": page.id},
                idempotency_key=f"index:{page.id}:{page.content_hash}",
            )
        return IngestQueued(page_id=page.id)

    async def _offered_body_for_hash(self, request: IngestRequest) -> str | None:
        """Return normalized body text offered during a revisit, if any."""
        if request.body_extracted is not None:
            return request.body_extracted
        if request.body_html is not None:
            extracted = await self._router.extract(
                content_type="text/html",
                raw=request.body_html.encode("utf-8"),
                charset="utf-8",
            )
            return extracted.body_text
        return None

    async def _resolve_body(self, request: IngestRequest) -> str | None:
        """Resolve body text inline; None defers to a fetch_and_index job."""
        if request.body_extracted is not None:
            return request.body_extracted
        if request.body_html is not None:
            extracted = await self._router.extract(
                content_type="text/html",
                raw=request.body_html.encode("utf-8"),
                charset="utf-8",
            )
            return extracted.body_text
        return None
