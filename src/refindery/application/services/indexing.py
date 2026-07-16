"""Indexing pipeline.

Chunk -> embed (every indexable model) -> upsert -> page-vector rollup ->
status transitions.

Chunk ids are deterministic — ``uuid5(page_id : content_hash : ordinal)`` —
so retries and re-index runs upsert the same vector-store points instead of
orphaning old ones.

Entity extraction is a separate durable job, so retrieval artifacts can be
visible even when entity enrichment needs a retry.
"""

import asyncio
import logging
import uuid
from dataclasses import replace
from functools import partial
from itertools import pairwise
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, HttpUrl, model_validator

from refindery.application.ports.chunker import Chunker
from refindery.application.ports.clock import Clock
from refindery.application.ports.content_extractor import (
    Fetcher,
    FetchResult,
    FetchRoute,
    RoutedFetcher,
)
from refindery.application.ports.job_queue import JobQueue
from refindery.application.ports.metadata_store import MetadataStore
from refindery.application.ports.podcast_producer import PodcastProducer
from refindery.application.ports.vector_store import ChunkPoint, VectorStore
from refindery.application.services.chapter_chunking import chunk_with_sections
from refindery.application.services.extraction_router import ExtractionRouter
from refindery.application.services.model_registry import ModelRegistry
from refindery.domain.content_hash import content_hash
from refindery.domain.errors import (
    FetchFailedError,
    PageHasNoBodyError,
    PageNotFoundError,
)
from refindery.domain.ids import ChunkId, PageId
from refindery.domain.job_keys import extract_entities_key
from refindery.domain.models import Job, JobKind, Page, PageStatus, Section
from refindery.domain.rollup import PoolingStrategy, Vector, page_vector

logger = logging.getLogger(__name__)


class _PersistedSection(BaseModel):
    """Validated section hydrated from page metadata JSON."""

    model_config = ConfigDict(extra="forbid", strict=True)

    title: str | None = None
    char_start: int = Field(ge=0)
    char_end: int = Field(gt=0)
    start_time_s: FiniteFloat | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _positive_width(self) -> Self:
        if self.char_end <= self.char_start:
            msg = "section char_end must be greater than char_start"
            raise ValueError(msg)
        return self

    def to_domain(self) -> Section:
        """Map validated persistence data to the domain type."""
        return Section(
            title=self.title,
            char_start=self.char_start,
            char_end=self.char_end,
            start_time_s=self.start_time_s,
        )


class _SectionsMetadata(BaseModel):
    """Validated section block within Page.metadata."""

    model_config = ConfigDict(extra="ignore", strict=True)

    sections: list[_PersistedSection]


class _PodcastMetadata(BaseModel):
    """Validated producer-routing block within Page.metadata."""

    model_config = ConfigDict(extra="ignore", strict=True)

    transcript_url: HttpUrl
    transcript_type: str | None = None
    chapters_url: HttpUrl | None = None
    enclosure_url: HttpUrl | None = None
    description: str | None = None


def _sections_metadata(
    sections: tuple[Section, ...] | None,
) -> dict[str, object] | None:
    """Serialize extracted section boundaries for durable page metadata."""
    if not sections:
        return None
    return {
        "sections": [
            {
                "title": section.title,
                "char_start": section.char_start,
                "char_end": section.char_end,
                "start_time_s": section.start_time_s,
            }
            for section in sections
        ]
    }


def _sections_from_metadata(
    metadata: dict[str, object] | None, *, body_len: int | None = None
) -> tuple[Section, ...] | None:
    """Validate and hydrate section boundaries persisted in page metadata."""
    if metadata is None or "sections" not in metadata:
        return None
    validated = _SectionsMetadata.model_validate(metadata)
    sections = tuple(section.to_domain() for section in validated.sections)
    if not sections:
        return None
    if body_len is not None:
        tiled = (
            sections[0].char_start == 0
            and sections[-1].char_end == body_len
            and all(
                left.char_end == right.char_start for left, right in pairwise(sections)
            )
        )
        if not tiled:
            msg = "persisted sections must tile the complete page body"
            raise ValueError(msg)
    return sections


def _podcast_from_metadata(
    metadata: dict[str, object] | None,
) -> _PodcastMetadata | None:
    """Validate and hydrate optional podcast producer-routing metadata."""
    if metadata is None or "podcast" not in metadata:
        return None
    return _PodcastMetadata.model_validate(metadata["podcast"])


def deterministic_chunk_id(*, page_id: PageId, page_hash: str, ordinal: int) -> ChunkId:
    """Stable chunk id so retries upsert the same vector-store points."""
    return ChunkId(
        str(uuid.uuid5(uuid.NAMESPACE_URL, f"{page_id}:{page_hash}:{ordinal}"))
    )


class IndexingService:
    """Executes index_page and fetch_and_index jobs."""

    def __init__(
        self,
        *,
        store: MetadataStore,
        vector_store: VectorStore,
        chunker: Chunker,
        registry: ModelRegistry,
        clock: Clock,
        fetcher: Fetcher,
        router: ExtractionRouter,
        queue: JobQueue | None = None,
        pooling: PoolingStrategy = PoolingStrategy.MEAN,
        podcast_producer: PodcastProducer | None = None,
    ) -> None:
        self._store = store
        self._vector_store = vector_store
        self._chunker = chunker
        self._registry = registry
        self._clock = clock
        self._fetcher = fetcher
        self._router = router
        self._queue = queue
        self._pooling = pooling
        self._podcast_producer = podcast_producer

    def set_queue(self, queue: JobQueue) -> None:
        """Attach the durable queue after construction (breaks wiring cycles)."""
        self._queue = queue

    # -- job handlers ---------------------------------------------------------

    async def handle_index_page(self, job: Job) -> None:
        """index_page job: run the pipeline for an already-resolved body."""
        page = await self._require_page(PageId(job.payload["page_id"]))
        await self._index(page)

    async def handle_fetch_and_index(self, job: Job) -> None:
        """fetch_and_index job: resolve the body by fetching, then index."""
        page = await self._require_page(PageId(job.payload["page_id"]))
        if page.body_text is None:
            route = FetchRoute(job.payload.get("fetch_route", FetchRoute.AUTO))
            result = await self._fetch_body(page, route=route)
            extracted = await self._router.extract(
                content_type=result.content_type,
                raw=result.body,
                charset=result.charset,
            )
            await self._store.set_page_body(
                page_id=page.id,
                body_text=extracted.body_text,
                content_hash=content_hash(extracted.body_text),
                title=page.title or extracted.title,
                metadata=_sections_metadata(extracted.sections),
            )
            refreshed = await self._store.get_page(page.id)
            if refreshed is None:
                raise PageNotFoundError(page.id)
            page = refreshed
        await self._index(page)

    async def _fetch_body(self, page: Page, *, route: FetchRoute) -> FetchResult:
        """Prefer a feed-published podcast transcript, then use normal routing."""
        podcast = _podcast_from_metadata(page.metadata)
        if self._podcast_producer is not None and podcast is not None:
            try:
                return await self._podcast_producer.build(
                    episode_url=page.original_url,
                    transcript_url=str(podcast.transcript_url),
                    transcript_type=podcast.transcript_type,
                    chapters_url=(
                        None
                        if podcast.chapters_url is None
                        else str(podcast.chapters_url)
                    ),
                    description=podcast.description,
                )
            except Exception:  # noqa: BLE001 — audio is the deliberate fallback
                logger.warning(
                    "published podcast transcript failed for %s; falling back to audio",
                    page.original_url,
                    exc_info=True,
                )
        if route is FetchRoute.AUTO:
            return await self._fetcher.fetch(page.original_url)
        if isinstance(self._fetcher, RoutedFetcher):
            return await self._fetcher.fetch_routed(page.original_url, route=route)
        raise FetchFailedError(
            url=page.original_url,
            detail=f"fetch route {route.value!r} is unavailable",
        )

    async def mark_page_dead(self, job: Job, error: str) -> None:
        """Dead-job callback: the page is excluded from search."""
        if job.kind not in {JobKind.INDEX_PAGE, JobKind.FETCH_AND_INDEX}:
            return
        page_id = PageId(job.payload.get("page_id", ""))
        if page_id:
            logger.warning("page %s dead after job %s: %s", page_id, job.id, error)
            await self._store.set_page_status(page_id=page_id, status=PageStatus.DEAD)

    async def mark_page_queued(self, job: Job) -> None:
        """Manual-retry callback: undo mark_page_dead; the page re-enters the queue."""
        if job.kind not in {JobKind.INDEX_PAGE, JobKind.FETCH_AND_INDEX}:
            return
        page_id = PageId(job.payload.get("page_id", ""))
        if page_id:
            logger.info("page %s re-queued by manual retry of job %s", page_id, job.id)
            await self._store.set_page_status(page_id=page_id, status=PageStatus.QUEUED)

    # -- pipeline --------------------------------------------------------------

    async def _require_page(self, page_id: PageId) -> Page:
        if (page := await self._store.get_page(page_id)) is None:
            raise PageNotFoundError(page_id)
        return page

    async def _index(self, page: Page) -> None:
        if page.body_text is None or page.content_hash is None:
            raise PageHasNoBodyError(page.id)
        await self._store.set_page_status(page_id=page.id, status=PageStatus.INDEXING)
        try:
            await self._run_pipeline(page)
        except Exception:
            await self._store.set_page_status(page_id=page.id, status=PageStatus.FAILED)
            await self._cleanup_failed_core(page)
            raise
        await self._store.set_page_status(
            page_id=page.id,
            status=PageStatus.INDEXED,
            indexed_at=self._clock.now(),
        )
        await self._enqueue_entity_extraction(page)

    async def _run_pipeline(self, page: Page) -> None:
        assert page.body_text is not None  # noqa: S101 — checked by _index
        assert page.content_hash is not None  # noqa: S101
        loop = asyncio.get_running_loop()
        raw_chunks = await loop.run_in_executor(
            None,
            partial(
                chunk_with_sections,
                self._chunker,
                page_id=page.id,
                text=page.body_text,
                sections=_sections_from_metadata(
                    page.metadata, body_len=len(page.body_text)
                ),
            ),
        )
        chunks = [
            replace(
                chunk,
                id=deterministic_chunk_id(
                    page_id=page.id,
                    page_hash=page.content_hash,
                    ordinal=chunk.ordinal,
                ),
            )
            for chunk in raw_chunks
        ]
        if not chunks:
            logger.info("page %s produced no chunks; indexed empty", page.id)
            await self._store.replace_chunks(page_id=page.id, chunks=[])
            return

        models = await self._registry.indexable_models()
        texts = [chunk.text for chunk in chunks]
        embedders = [self._registry.embedder_for(model) for model in models]
        embedded = await asyncio.gather(
            *(embedder.embed_documents(texts) for embedder in embedders)
        )
        vectors_by_model: dict[str, list[Vector]] = {
            model.id: vectors for model, vectors in zip(models, embedded, strict=True)
        }

        points = [
            ChunkPoint(
                chunk_id=chunk.id,
                page_id=page.id,
                ordinal=chunk.ordinal,
                text=chunk.text,
                vectors={
                    model_id: vectors[i]
                    for model_id, vectors in vectors_by_model.items()
                },
                domain=page.domain,
                first_seen_at=page.first_seen_at,
                cluster_id=None,
            )
            for i, chunk in enumerate(chunks)
        ]
        await self._store.replace_chunks(page_id=page.id, chunks=chunks)
        await self._vector_store.upsert_chunks(points)

        for model_id, vectors in vectors_by_model.items():
            pooled = page_vector(vectors, strategy=self._pooling)
            await self._store.upsert_page_vector(
                page_id=page.id, model_id=model_id, vector=pooled.tobytes()
            )

    async def reconcile_entity_jobs(self) -> int:
        """Enqueue entity jobs lost in the indexed->enqueue crash window."""
        if self._queue is None:
            return 0
        pages = await self._store.indexed_pages_missing_entity_extraction()
        reconciled = 0
        for page in pages:
            if page.content_hash is None:
                continue
            try:
                await self._enqueue_entity_extraction(page)
            except Exception:  # noqa: BLE001 — recovery must keep scanning pages
                logger.warning(
                    "failed to enqueue entity extraction for page %s during "
                    "reconciliation",
                    page.id,
                    exc_info=True,
                )
                continue
            reconciled += 1
        if reconciled:
            logger.info(
                "reconciled %d indexed pages missing entity extraction jobs",
                reconciled,
            )
        return reconciled

    async def _enqueue_entity_extraction(self, page: Page) -> None:
        if self._queue is None or (page_hash := page.content_hash) is None:
            return
        await self._queue.enqueue(
            kind=JobKind.EXTRACT_ENTITIES,
            payload={"page_id": page.id, "content_hash": page_hash},
            idempotency_key=extract_entities_key(
                page_id=page.id, content_hash=page_hash
            ),
        )

    async def _cleanup_failed_core(self, page: Page) -> None:
        """Best-effort cleanup so failed pages cannot retain retrieval artifacts."""
        try:
            await self._store.clear_index_artifacts(page.id)
        except Exception:  # noqa: BLE001 — best-effort cleanup must not mask failure
            logger.warning(
                "failed to clear metadata artifacts for page %s",
                page.id,
                exc_info=True,
            )
        try:
            await self._vector_store.delete_pages([page.id])
        except Exception:  # noqa: BLE001 — best-effort cleanup must not mask failure
            logger.warning(
                "failed to delete vector artifacts for page %s",
                page.id,
                exc_info=True,
            )
