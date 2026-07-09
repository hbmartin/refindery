"""VectorStore conformance suite.

Every adapter must pass identically. Assertions are rank-level, never
score-equality — BM25 analyzers legitimately differ between stores. The one
exact contract is fusion: ``fused == rrf_fuse(dense, sparse, k)``.
"""

import uuid
from datetime import UTC, datetime, timedelta

from refindery.application.ports.vector_store import (
    ChunkPoint,
    HybridQuery,
    StoreFilter,
)
from refindery.domain.ids import ChunkId, PageId
from refindery.domain.retrieval import rrf_fuse
from refindery.domain.rollup import Vector
from tests.conformance.conftest import DIM, MODEL_A, MODEL_B
from tests.fakes.embedder import hash_vector

T0 = datetime(2026, 6, 1, tzinfo=UTC)

CORPUS = [
    ("p1", 0, "Hexagonal architecture keeps domain logic pure and portable."),
    ("p1", 1, "Ports and adapters isolate infrastructure from the domain."),
    ("p2", 0, "HDBSCAN finds lumpy clusters and labels the rest as noise."),
    ("p2", 1, "UMAP reduces embeddings before density clustering begins."),
    ("p3", 0, "The zanzibar authorization paper describes google relationships."),
]


def _point(
    page: str,
    ordinal: int,
    text: str,
    *,
    days: int = 0,
    domain: str = "example.com",
    models: tuple[str, ...] = (MODEL_A.id,),
) -> ChunkPoint:
    return ChunkPoint(
        chunk_id=ChunkId(str(uuid.uuid5(uuid.NAMESPACE_URL, f"{page}:{ordinal}"))),
        page_id=PageId(page),
        ordinal=ordinal,
        text=text,
        vectors={m: hash_vector(f"{m}:{text}", DIM) for m in models},
        domain=domain,
        first_seen_at=T0 + timedelta(days=days),
        cluster_id=None,
    )


def _corpus_points() -> list[ChunkPoint]:
    return [
        _point(page, ordinal, text, days=i, domain=f"{page}.example")
        for i, (page, ordinal, text) in enumerate(CORPUS)
    ]


def _query_vector(text: str, model_id: str = MODEL_A.id) -> Vector:
    return hash_vector(f"{model_id}:{text}", DIM)


async def test_dense_query_returns_exact_match_first(vector_store):
    points = _corpus_points()
    await vector_store.upsert_chunks(points)
    target = points[2]
    hits = await vector_store.dense_query(
        model_id=MODEL_A.id, vector=_query_vector(target.text), limit=3
    )
    assert hits
    assert hits[0].chunk_id == target.chunk_id
    assert hits[0].page_id == target.page_id
    assert hits[0].ordinal == target.ordinal


async def test_sparse_query_finds_rare_token(vector_store):
    points = _corpus_points()
    await vector_store.upsert_chunks(points)
    hits = await vector_store.sparse_query(text="zanzibar", limit=5)
    assert hits
    assert hits[0].page_id == "p3"


async def test_sparse_query_matches_repeated_terms(vector_store):
    # Regression: lancedb's unindexed-row FTS scan silently missed documents
    # containing a repeated query term; adapters must index on write.
    text = "Hexagonal patterns everywhere: hexagonal ports, hexagonal adapters."
    await vector_store.upsert_chunks(
        [_point("p9", 0, text, days=9, domain="p9.example")]
    )
    hits = await vector_store.sparse_query(text="hexagonal", limit=5)
    assert any(h.page_id == "p9" for h in hits)


async def test_hybrid_fused_equals_shared_rrf(vector_store):
    points = _corpus_points()
    await vector_store.upsert_chunks(points)
    query = HybridQuery(
        model_id=MODEL_A.id,
        dense_vector=_query_vector(points[0].text),
        sparse_text="clusters noise",
        per_arm_limit=5,
        fused_limit=5,
        rrf_k=60,
    )
    result = await vector_store.hybrid_query(query)
    assert result.dense
    assert result.sparse
    expected = rrf_fuse(dense=result.dense, sparse=result.sparse, k=60)[:5]
    assert [h.chunk_id for h in result.fused] == [h.chunk_id for h in expected]


async def test_domain_filter_excludes(vector_store):
    points = _corpus_points()
    await vector_store.upsert_chunks(points)
    hits = await vector_store.dense_query(
        model_id=MODEL_A.id,
        vector=_query_vector(points[0].text),
        limit=10,
        filters=StoreFilter(domain="p2.example"),
    )
    assert hits
    assert {h.page_id for h in hits} == {"p2"}


async def test_time_range_filter(vector_store):
    points = _corpus_points()  # days offset = index
    await vector_store.upsert_chunks(points)
    hits = await vector_store.sparse_query(
        text="architecture domain clusters zanzibar",
        limit=10,
        filters=StoreFilter(
            after=T0 + timedelta(days=2), before=T0 + timedelta(days=5)
        ),
    )
    got_chunks = {h.chunk_id for h in hits}
    allowed = {p.chunk_id for p in points[2:5]}
    assert got_chunks <= allowed
    assert got_chunks


async def test_page_ids_filter(vector_store):
    points = _corpus_points()
    await vector_store.upsert_chunks(points)
    hits = await vector_store.dense_query(
        model_id=MODEL_A.id,
        vector=_query_vector(points[0].text),
        limit=10,
        filters=StoreFilter(page_ids=frozenset({PageId("p1")})),
    )
    assert hits
    assert {h.page_id for h in hits} == {"p1"}


async def test_upsert_is_idempotent(vector_store):
    points = _corpus_points()
    await vector_store.upsert_chunks(points)
    await vector_store.upsert_chunks(points)
    assert await vector_store.count_chunks(PageId("p1")) == 2


async def test_delete_pages_removes_everywhere(vector_store):
    points = _corpus_points()
    await vector_store.upsert_chunks(points)
    await vector_store.delete_pages([PageId("p1"), PageId("p3")])
    assert await vector_store.count_chunks(PageId("p1")) == 0
    assert await vector_store.count_chunks(PageId("p3")) == 0
    assert await vector_store.count_chunks(PageId("p2")) == 2
    hits = await vector_store.dense_query(
        model_id=MODEL_A.id, vector=_query_vector(points[0].text), limit=10
    )
    assert {h.page_id for h in hits} == {"p2"}


async def test_model_isolation_and_backfill(vector_store):
    points = _corpus_points()
    await vector_store.upsert_chunks(points)
    await vector_store.add_model(MODEL_B)

    # Backfill model B vectors onto the existing points.
    backfill = [
        ChunkPoint(
            chunk_id=p.chunk_id,
            page_id=p.page_id,
            ordinal=p.ordinal,
            text=p.text,
            vectors={MODEL_B.id: hash_vector(f"{MODEL_B.id}:{p.text}", DIM)},
            domain=p.domain,
            first_seen_at=p.first_seen_at,
            cluster_id=p.cluster_id,
        )
        for p in points
    ]
    await vector_store.backfill_vectors(model_id=MODEL_B.id, points=backfill)

    target = points[4]
    hits_b = await vector_store.dense_query(
        model_id=MODEL_B.id,
        vector=_query_vector(target.text, model_id=MODEL_B.id),
        limit=3,
    )
    assert hits_b
    assert hits_b[0].chunk_id == target.chunk_id

    # Model A's space is untouched by the backfill.
    hits_a = await vector_store.dense_query(
        model_id=MODEL_A.id, vector=_query_vector(target.text), limit=3
    )
    assert hits_a
    assert hits_a[0].chunk_id == target.chunk_id

    await vector_store.drop_model(MODEL_B.id)
