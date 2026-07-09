"""Qdrant implementation of the VectorStore port.

Single collection: named dense vectors ``dense_{model_id}`` (one per model)
plus one ``sparse_bm25`` sparse vector space (IDF modifier applied
server-side; term frequencies encoded client-side via fastembed). A point id
is the chunk id, so one point carries every model's vector — which is why
the port upserts multi-vector points and backfills via partial vector
updates rather than per-model point upserts.

Fusion is client-side via the shared ``rrf_fuse`` (Qdrant's server-side RRF
exposes no ``k`` parameter, and the query log needs both arms anyway).
"""

import asyncio
import time
from collections.abc import Sequence
from datetime import datetime

from qdrant_client import AsyncQdrantClient
from qdrant_client import models as qm

from refindery.adapters.vector.sparse import Bm25SparseEncoder, SparseVec
from refindery.application.ports.vector_store import (
    ArmTiming,
    ChunkPoint,
    HybridHits,
    HybridQuery,
    StoreFilter,
)
from refindery.domain.ids import ChunkId, PageId
from refindery.domain.models import EmbeddingModel
from refindery.domain.retrieval import ChunkHit, rrf_fuse
from refindery.domain.rollup import Vector

_SPARSE_NAME = "sparse_bm25"


def _dense_name(model_id: str) -> str:
    return f"dense_{model_id}"


def _epoch(value: datetime) -> int:
    return int(value.timestamp())


def _filter(filters: StoreFilter | None) -> qm.Filter | None:
    if filters is None:
        return None
    must: list[qm.Condition] = []
    if filters.domain is not None:
        must.append(
            qm.FieldCondition(key="domain", match=qm.MatchValue(value=filters.domain))
        )
    if filters.after is not None or filters.before is not None:
        must.append(
            qm.FieldCondition(
                key="first_seen_at",
                range=qm.Range(
                    gte=None if filters.after is None else _epoch(filters.after),
                    lt=None if filters.before is None else _epoch(filters.before),
                ),
            )
        )
    if filters.cluster_id is not None:
        must.append(
            qm.FieldCondition(
                key="cluster_id", match=qm.MatchValue(value=filters.cluster_id)
            )
        )
    if filters.page_ids is not None:
        values = sorted(filters.page_ids) or ["__match_nothing__"]
        must.append(qm.FieldCondition(key="page_id", match=qm.MatchAny(any=values)))
    return qm.Filter(must=must) if must else None


def _hit(point: qm.ScoredPoint) -> ChunkHit:
    payload = point.payload or {}
    return ChunkHit(
        chunk_id=ChunkId(str(point.id)),
        page_id=PageId(str(payload["page_id"])),
        ordinal=int(payload["ordinal"]),
        score=float(point.score),
    )


class QdrantVectorStore:
    """VectorStore implementation over one Qdrant collection."""

    def __init__(
        self,
        *,
        url: str,
        collection: str = "refindery_chunks",
        api_key: str | None = None,
    ) -> None:
        self._client = AsyncQdrantClient(url=url, api_key=api_key)
        self._collection = collection
        self._encoder: Bm25SparseEncoder | None = None

    async def _sparse(self) -> Bm25SparseEncoder:
        if self._encoder is None:
            self._encoder = await asyncio.to_thread(Bm25SparseEncoder)
        return self._encoder

    # -- schema ------------------------------------------------------------

    async def ensure_schema(self, models: list[EmbeddingModel]) -> None:
        """Create the collection (with all named vector spaces) if missing."""
        if await self._client.collection_exists(self._collection):
            for model in models:
                await self.add_model(model)
            return
        await self._client.create_collection(
            collection_name=self._collection,
            vectors_config={
                _dense_name(m.id): qm.VectorParams(
                    size=m.dim, distance=qm.Distance.COSINE
                )
                for m in models
            },
            sparse_vectors_config={
                _SPARSE_NAME: qm.SparseVectorParams(modifier=qm.Modifier.IDF)
            },
        )
        for key, schema in (
            ("page_id", qm.PayloadSchemaType.KEYWORD),
            ("domain", qm.PayloadSchemaType.KEYWORD),
            ("cluster_id", qm.PayloadSchemaType.KEYWORD),
            ("first_seen_at", qm.PayloadSchemaType.INTEGER),
        ):
            await self._client.create_payload_index(
                collection_name=self._collection,
                field_name=key,
                field_schema=schema,
            )

    async def add_model(self, model: EmbeddingModel) -> None:
        """Add a named dense vector space for a new model (Qdrant >= 1.18)."""
        info = await self._client.get_collection(self._collection)
        existing = info.config.params.vectors
        if isinstance(existing, dict) and _dense_name(model.id) in existing:
            return
        await self._client.create_vector_name(
            collection_name=self._collection,
            vector_name=_dense_name(model.id),
            vector_name_config=qm.DenseVectorNameConfig(
                dense=qm.DenseVectorConfig(size=model.dim, distance=qm.Distance.COSINE)
            ),
        )

    async def drop_model(self, model_id: str) -> None:
        """Remove a model's named vector space and its data (Qdrant >= 1.18)."""
        await self._client.delete_vector_name(
            collection_name=self._collection,
            vector_name=_dense_name(model_id),
        )

    async def drop_collection(self) -> None:
        """Delete the whole collection (tests / hard reset)."""
        await self._client.delete_collection(self._collection)

    # -- writes ------------------------------------------------------------

    async def upsert_chunks(self, points: list[ChunkPoint]) -> None:
        """Full point upsert: payload + sparse + every model's dense vector."""
        if not points:
            return
        encoder = await self._sparse()
        sparse = await asyncio.to_thread(
            encoder.encode_documents, [p.text for p in points]
        )
        structs = [
            qm.PointStruct(
                id=p.chunk_id,
                vector={
                    **{
                        _dense_name(model_id): vector.tolist()
                        for model_id, vector in p.vectors.items()
                    },
                    _SPARSE_NAME: qm.SparseVector(indices=sv.indices, values=sv.values),
                },
                payload={
                    "page_id": p.page_id,
                    "ordinal": p.ordinal,
                    "domain": p.domain,
                    "first_seen_at": _epoch(p.first_seen_at),
                    "cluster_id": p.cluster_id,
                },
            )
            for p, sv in zip(points, sparse, strict=True)
        ]
        await self._client.upsert(
            collection_name=self._collection, points=structs, wait=True
        )

    async def backfill_vectors(
        self, *, model_id: str, points: list[ChunkPoint]
    ) -> None:
        """Partial vector update: adds this model's vectors, touches nothing else."""
        if not points:
            return
        await self._client.update_vectors(
            collection_name=self._collection,
            points=[
                qm.PointVectors(
                    id=p.chunk_id,
                    vector={_dense_name(model_id): p.vectors[model_id].tolist()},
                )
                for p in points
                if model_id in p.vectors
            ],
            wait=True,
        )

    async def delete_pages(self, page_ids: Sequence[PageId]) -> None:
        """Delete all points belonging to these pages."""
        if not page_ids:
            return
        await self._client.delete(
            collection_name=self._collection,
            points_selector=qm.FilterSelector(
                filter=qm.Filter(
                    must=[
                        qm.FieldCondition(
                            key="page_id",
                            match=qm.MatchAny(any=list(page_ids)),
                        )
                    ]
                )
            ),
            wait=True,
        )

    async def count_chunks(self, page_id: PageId) -> int:
        """Count points for one page."""
        result = await self._client.count(
            collection_name=self._collection,
            count_filter=qm.Filter(
                must=[
                    qm.FieldCondition(key="page_id", match=qm.MatchValue(value=page_id))
                ]
            ),
            exact=True,
        )
        return result.count

    # -- reads -------------------------------------------------------------

    async def dense_query(
        self,
        *,
        model_id: str,
        vector: Vector,
        limit: int,
        filters: StoreFilter | None = None,
    ) -> list[ChunkHit]:
        """Dense kNN over one model's named vector space."""
        response = await self._client.query_points(
            collection_name=self._collection,
            query=vector.tolist(),
            using=_dense_name(model_id),
            limit=limit,
            query_filter=_filter(filters),
            with_payload=True,
        )
        return [_hit(point) for point in response.points]

    async def sparse_query(
        self,
        *,
        text: str,
        limit: int,
        filters: StoreFilter | None = None,
    ) -> list[ChunkHit]:
        """BM25 sparse arm (shared across models)."""
        encoder = await self._sparse()
        query_vec: SparseVec = await asyncio.to_thread(encoder.encode_query, text)
        if not query_vec.indices:
            return []
        response = await self._client.query_points(
            collection_name=self._collection,
            query=qm.SparseVector(indices=query_vec.indices, values=query_vec.values),
            using=_SPARSE_NAME,
            limit=limit,
            query_filter=_filter(filters),
            with_payload=True,
        )
        return [_hit(point) for point in response.points]

    async def hybrid_query(self, query: HybridQuery) -> HybridHits:
        """Run both arms concurrently and fuse client-side."""
        started = time.perf_counter()
        dense, sparse = await asyncio.gather(
            self.dense_query(
                model_id=query.model_id,
                vector=query.dense_vector,
                limit=query.per_arm_limit,
                filters=query.filters,
            ),
            self.sparse_query(
                text=query.sparse_text,
                limit=query.per_arm_limit,
                filters=query.filters,
            ),
        )
        arms_ms = (time.perf_counter() - started) * 1_000.0
        fuse_started = time.perf_counter()
        fused = rrf_fuse(dense=dense, sparse=sparse, k=query.rrf_k)[: query.fused_limit]
        fuse_ms = (time.perf_counter() - fuse_started) * 1_000.0
        return HybridHits(
            dense=dense,
            sparse=sparse,
            fused=fused,
            timing=ArmTiming(dense_ms=arms_ms, sparse_ms=arms_ms, fuse_ms=fuse_ms),
        )

    async def close(self) -> None:
        """Close the client."""
        await self._client.close()
