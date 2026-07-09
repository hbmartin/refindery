"""Indexing pipeline.

Chunk -> embed (every indexable model) -> upsert -> page-vector rollup ->
status transitions.

Chunk ids are deterministic — ``uuid5(page_id : content_hash : ordinal)`` —
so retries and re-index runs upsert the same vector-store points instead of
orphaning old ones.

Entity extraction and cluster-staleness are pipeline hooks (no-ops until M4).
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import replace
from functools import partial

from refindery.application.ports.chunker import Chunker
from refindery.application.ports.clock import Clock
from refindery.application.ports.content_extractor import Fetcher
from refindery.application.ports.metadata_store import MetadataStore
from refindery.application.ports.vector_store import ChunkPoint, VectorStore
from refindery.application.services.extraction_router import ExtractionRouter
from refindery.application.services.model_registry import ModelRegistry
from refindery.domain.content_hash import content_hash
from refindery.domain.errors import PageHasNoBodyError, PageNotFoundError
from refindery.domain.ids import ChunkId, PageId
from refindery.domain.models import Chunk, Job, Page, PageStatus
from refindery.domain.rollup import PoolingStrategy, Vector, page_vector

logger = logging.getLogger(__name__)

type PageHook = Callable[[Page, list[Chunk]], Awaitable[None]]


async def _noop_hook(_page: Page, _chunks: list[Chunk]) -> None:
    return


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
        pooling: PoolingStrategy = PoolingStrategy.MEAN,
        on_page_indexed: PageHook = _noop_hook,
    ) -> None:
        self._store = store
        self._vector_store = vector_store
        self._chunker = chunker
        self._registry = registry
        self._clock = clock
        self._fetcher = fetcher
        self._router = router
        self._pooling = pooling
        self._on_page_indexed = on_page_indexed

    def set_page_hook(self, hook: PageHook) -> None:
        """Attach the entity-extraction hook (breaks wiring cycles)."""
        self._on_page_indexed = hook

    # -- job handlers ---------------------------------------------------------

    async def handle_index_page(self, job: Job) -> None:
        """index_page job: run the pipeline for an already-resolved body."""
        page = await self._require_page(PageId(job.payload["page_id"]))
        await self._index(page)

    async def handle_fetch_and_index(self, job: Job) -> None:
        """fetch_and_index job: resolve the body by fetching, then index."""
        page = await self._require_page(PageId(job.payload["page_id"]))
        if page.body_text is None:
            result = await self._fetcher.fetch(page.original_url)
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
            )
            refreshed = await self._store.get_page(page.id)
            if refreshed is None:
                raise PageNotFoundError(page.id)
            page = refreshed
        await self._index(page)

    async def mark_page_dead(self, job: Job, error: str) -> None:
        """Dead-job callback: the page is excluded from search."""
        page_id = PageId(job.payload.get("page_id", ""))
        if page_id:
            logger.warning("page %s dead after job %s: %s", page_id, job.id, error)
            await self._store.set_page_status(page_id=page_id, status=PageStatus.DEAD)

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
            raise
        await self._store.set_page_status(
            page_id=page.id,
            status=PageStatus.INDEXED,
            indexed_at=self._clock.now(),
        )

    async def _run_pipeline(self, page: Page) -> None:
        assert page.body_text is not None  # noqa: S101 — checked by _index
        assert page.content_hash is not None  # noqa: S101
        loop = asyncio.get_running_loop()
        raw_chunks = await loop.run_in_executor(
            None, partial(self._chunker.chunk, page_id=page.id, text=page.body_text)
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
        vectors_by_model: dict[str, list[Vector]] = {}
        for model in models:
            embedder = self._registry.embedder_for(model)
            vectors_by_model[model.id] = await embedder.embed_documents(
                [chunk.text for chunk in chunks]
            )

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

        await self._on_page_indexed(page, chunks)
