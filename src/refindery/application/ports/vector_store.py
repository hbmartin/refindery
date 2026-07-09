"""Vector store port: dense + sparse retrieval over chunk vectors.

Write shape: a ``ChunkPoint`` carries the vectors of **every** model being
indexed (in Qdrant, one point holds all named vectors — a per-model upsert
would clobber the others). ``upsert_chunks`` fully replaces points and is
used by the indexing pipeline (which always embeds for all active/backfilling
models); ``backfill_vectors`` adds one model's vectors to existing points
without touching the rest.

The sparse arm takes raw text; how it is represented (BM25 sparse vectors,
tantivy FTS, ...) is an adapter concern. Hybrid fusion is client-side via
``refindery.domain.retrieval.rrf_fuse`` in every adapter so results are
identical across stores (the conformance suite asserts this).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from refindery.domain.ids import ChunkId, ClusterId, PageId
from refindery.domain.models import EmbeddingModel
from refindery.domain.retrieval import ChunkHit
from refindery.domain.rollup import Vector


@dataclass(frozen=True, slots=True)
class ChunkPoint:
    """One chunk to upsert: per-model vectors plus the filterable payload."""

    chunk_id: ChunkId
    page_id: PageId
    ordinal: int
    text: str
    vectors: dict[str, Vector]
    domain: str
    first_seen_at: datetime
    cluster_id: ClusterId | None = None


@dataclass(frozen=True, slots=True)
class StoreFilter:
    """Filters pushed into the store (pre-filter where the store supports it)."""

    domain: str | None = None
    after: datetime | None = None
    before: datetime | None = None
    page_ids: frozenset[PageId] | None = None


@dataclass(frozen=True, slots=True)
class HybridQuery:
    """A hybrid retrieval request over one model's space plus the shared sparse arm."""

    model_id: str
    dense_vector: Vector
    sparse_text: str
    per_arm_limit: int
    fused_limit: int
    rrf_k: int = 60
    filters: StoreFilter | None = None


@dataclass(frozen=True, slots=True)
class ArmTiming:
    """Per-arm latency breakdown filled by the adapter."""

    dense_ms: float
    sparse_ms: float
    fuse_ms: float


@dataclass(frozen=True, slots=True)
class HybridHits:
    """Hybrid query result.

    Both arms are always populated (the query log records them) alongside
    the RRF-fused ranking.
    """

    dense: list[ChunkHit]
    sparse: list[ChunkHit]
    fused: list[ChunkHit]
    timing: ArmTiming


class VectorStore(Protocol):
    """Upsert/query dense + sparse chunk vectors with payload filtering."""

    async def ensure_schema(self, models: list[EmbeddingModel]) -> None:
        """Create/update storage so every model has a vector space."""
        ...

    async def add_model(self, model: EmbeddingModel) -> None:
        """Add a vector space for a newly registered model."""
        ...

    async def drop_model(self, model_id: str) -> None:
        """Drop a retired model's vector space."""
        ...

    async def upsert_chunks(self, points: list[ChunkPoint]) -> None:
        """Idempotently upsert chunks (full point replace, all models)."""
        ...

    async def backfill_vectors(
        self, *, model_id: str, points: list[ChunkPoint]
    ) -> None:
        """Add one model's vectors to existing chunks without clobbering others."""
        ...

    async def delete_pages(self, page_ids: Sequence[PageId]) -> None:
        """Delete all chunks of the given pages across every model space."""
        ...

    async def count_chunks(self, page_id: PageId) -> int:
        """Count stored chunks for a page (tombstone verification)."""
        ...

    async def dense_query(
        self,
        *,
        model_id: str,
        vector: Vector,
        limit: int,
        filters: StoreFilter | None = None,
    ) -> list[ChunkHit]:
        """Run dense kNN over one model's vectors."""
        ...

    async def sparse_query(
        self,
        *,
        text: str,
        limit: int,
        filters: StoreFilter | None = None,
    ) -> list[ChunkHit]:
        """Sparse/lexical arm; shared across models."""
        ...

    async def hybrid_query(self, query: HybridQuery) -> HybridHits:
        """Run both arms and fuse; returns arms + fused + timings."""
        ...

    async def close(self) -> None:
        """Release connections/resources."""
        ...
