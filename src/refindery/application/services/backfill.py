"""Model backfill: embed the existing corpus for a newly registered model.

Resumable via a page-granular cursor (a crash re-embeds at most one page;
vector upserts are idempotent). Rate-limited by a Clock-driven token bucket.
Cost estimates are exact — chunk token counts are already stored — and USD
appears only when the user configured a price map (prices drift; never
hardcode them).
"""

import asyncio
import logging
from dataclasses import dataclass

from refindery.application.ports.clock import Clock
from refindery.application.ports.embedder import Embedder
from refindery.application.ports.job_queue import JobQueue
from refindery.application.ports.metadata_store import MetadataStore
from refindery.application.ports.vector_store import ChunkPoint, VectorStore
from refindery.application.services.model_registry import ModelRegistry
from refindery.domain.ids import PageId
from refindery.domain.models import Job, JobKind, ModelBackfill, ModelStatus
from refindery.domain.rollup import PoolingStrategy, page_vector

logger = logging.getLogger(__name__)

_EMBED_BATCH = 128
_PAGE_BATCH = 25


class RateLimiter:
    """Requests/minute + tokens/minute budget over the injectable clock."""

    def __init__(self, *, clock: Clock, rpm: int = 300, tpm: int = 1_000_000) -> None:
        self._clock = clock
        self._rpm = rpm
        self._tpm = tpm
        self._window_start = clock.now()
        self._requests = 0
        self._tokens = 0

    def wait_seconds(self, *, tokens: int) -> float:
        """Seconds to wait before the next request fits the budget."""
        now = self._clock.now()
        elapsed = (now - self._window_start).total_seconds()
        if elapsed >= 60.0:
            self._window_start = now
            self._requests = 0
            self._tokens = 0
            elapsed = 0.0
        if self._requests + 1 > self._rpm or self._tokens + tokens > self._tpm:
            return max(60.0 - elapsed, 0.0)
        return 0.0

    def record(self, *, tokens: int) -> None:
        """Account one request."""
        self._requests += 1
        self._tokens += tokens

    async def acquire(self, *, tokens: int) -> None:
        """Wait until the request fits, then account it."""
        while (wait := self.wait_seconds(tokens=tokens)) > 0:  # noqa: ASYNC110 — time-budget poll, no event to wait on
            await asyncio.sleep(wait)
        self.record(tokens=tokens)


@dataclass(frozen=True, slots=True)
class BackfillEstimate:
    """Dry-run response for POST /v1/models/{id}/backfill."""

    model_id: str
    n_chunks: int
    total_tokens: int
    est_cost_usd: float | None
    est_duration_s: float | None


class BackfillService:
    """Registers, estimates, and runs backfills."""

    def __init__(
        self,
        *,
        store: MetadataStore,
        vector_store: VectorStore,
        registry: ModelRegistry,
        queue: JobQueue,
        clock: Clock,
        pooling: PoolingStrategy = PoolingStrategy.MEAN,
        price_per_mtok_usd: dict[str, float] | None = None,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self._store = store
        self._vector_store = vector_store
        self._registry = registry
        self._queue = queue
        self._clock = clock
        self._pooling = pooling
        self._prices = price_per_mtok_usd or {}
        self._limiter = rate_limiter or RateLimiter(clock=clock)

    async def estimate(self, model_id: str) -> BackfillEstimate:
        """Exact cost/duration estimate from stored chunk stats."""
        model = await self._registry.require_model(model_id)
        stats = await self._store.chunk_stats()
        price = self._prices.get(model.provider)
        return BackfillEstimate(
            model_id=model_id,
            n_chunks=stats.n_chunks,
            total_tokens=stats.total_tokens,
            est_cost_usd=(
                None if price is None else stats.total_tokens / 1_000_000 * price
            ),
            est_duration_s=(
                (stats.n_chunks / _EMBED_BATCH) * 0.5 if stats.n_chunks else 0.0
            ),
        )

    async def start(self, model_id: str) -> None:
        """Mark backfilling and enqueue the durable job."""
        model = await self._registry.require_model(model_id)
        stats = await self._store.chunk_stats()
        now = self._clock.now()
        existing = await self._store.get_backfill(model_id)
        if existing is None or existing.finished_at is not None:
            await self._store.upsert_backfill(
                ModelBackfill(
                    model_id=model.id,
                    total_chunks=stats.n_chunks,
                    total_tokens=stats.total_tokens,
                    started_at=now,
                    updated_at=now,
                )
            )
        await self._store.set_model_status(
            model_id=model.id, status=ModelStatus.BACKFILLING
        )
        await self._queue.enqueue(
            kind=JobKind.BACKFILL_MODEL,
            payload={"model_id": model.id},
            idempotency_key=f"backfill:{model.id}:{now.isoformat()}",
        )

    async def handle_backfill_job(self, job: Job) -> None:
        """Resume from the cursor; embed page by page; finish -> ready."""
        model_id = job.payload["model_id"]
        model = await self._registry.require_model(model_id)
        embedder = self._registry.embedder_for(model)
        state = await self._store.get_backfill(model_id)
        if state is None:
            logger.warning("backfill job without state for %s", model_id)
            return

        cursor: PageId | None = (
            None if state.cursor_page_id is None else PageId(state.cursor_page_id)
        )
        while True:
            page_ids = await self._store.pages_with_chunks_after(
                cursor=cursor, limit=_PAGE_BATCH
            )
            if not page_ids:
                break
            for page_id in page_ids:
                await self._embed_page(model.id, embedder, page_id)
                cursor = page_id
                state.cursor_page_id = cursor
                state.embedded_chunks += len(await self._store.chunks_for_page(page_id))
                state.updated_at = self._clock.now()
                await self._store.upsert_backfill(state)

        state.finished_at = self._clock.now()
        state.updated_at = state.finished_at
        await self._store.upsert_backfill(state)
        await self._store.set_model_status(model_id=model.id, status=ModelStatus.READY)
        logger.info(
            "backfill of %s complete: %d chunks", model.id, state.embedded_chunks
        )

    async def _embed_page(
        self, model_id: str, embedder: Embedder, page_id: PageId
    ) -> None:
        page = await self._store.get_page(page_id)
        chunks = await self._store.chunks_for_page(page_id)
        if page is None or not chunks:
            return
        vectors = []
        for start in range(0, len(chunks), _EMBED_BATCH):
            batch = chunks[start : start + _EMBED_BATCH]
            await self._limiter.acquire(
                tokens=sum(chunk.token_count for chunk in batch)
            )
            vectors.extend(
                await embedder.embed_documents([chunk.text for chunk in batch])
            )
        points = [
            ChunkPoint(
                chunk_id=chunk.id,
                page_id=page_id,
                ordinal=chunk.ordinal,
                text=chunk.text,
                vectors={model_id: vector},
                domain=page.domain,
                first_seen_at=page.first_seen_at,
            )
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]
        await self._vector_store.backfill_vectors(model_id=model_id, points=points)
        pooled = page_vector(vectors, strategy=self._pooling)
        await self._store.upsert_page_vector(
            page_id=page_id, model_id=model_id, vector=pooled.tobytes()
        )
