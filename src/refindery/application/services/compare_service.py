"""A/B model comparison: the delta isolates the embedder.

The full pipeline runs once per model. The sparse arm is fetched once
(chunk ids are canonical across model spaces) and the reranker instance is
identical across arms. Each arm is logged as a ``compare_arm`` query-log
row sharing a compare_id.
"""

from dataclasses import dataclass

from refindery.application.ports.clock import Clock
from refindery.application.ports.metadata_store import MetadataStore
from refindery.application.ports.query_log import (
    LoggedHit,
    LoggedPage,
    QueryLogRecord,
    QueryLogSink,
)
from refindery.application.ports.reranker import RerankCandidate, Reranker
from refindery.application.ports.vector_store import VectorStore
from refindery.application.services.model_registry import ModelRegistry
from refindery.domain.errors import ModelNotFoundError, RefinderyError
from refindery.domain.ids import PageId, new_query_id
from refindery.domain.models import ModelStatus, Page, PageStatus
from refindery.domain.ranking_metrics import (
    jaccard_at_k,
    kendall_tau_intersection,
    rbo_ext,
)
from refindery.domain.retrieval import (
    ChunkHit,
    RollupStrategy,
    ScoredChunk,
    rollup_pages,
    rrf_fuse,
)


class ModelNotComparableError(RefinderyError):
    """Only ready models can be compared (partial indexes corrupt stats)."""

    def __init__(self, model_id: str, status: str) -> None:
        super().__init__(
            f"model {model_id!r} is {status}; only ready models can be compared"
        )


@dataclass(frozen=True, slots=True)
class CompareArm:
    """One model's ranked pages."""

    model_id: str
    pages: list[tuple[Page, float]]


@dataclass(frozen=True, slots=True)
class PairAgreement:
    """Agreement statistics for one model pair."""

    model_a: str
    model_b: str
    jaccard_at_k: float
    rbo: float
    kendall_tau: float | None
    intersection_size: int


@dataclass(frozen=True, slots=True)
class CompareOutcome:
    """Everything /v1/compare returns."""

    compare_id: str
    arms: list[CompareArm]
    agreement: list[PairAgreement]


class CompareService:
    """Runs the pipeline per model and computes agreement."""

    def __init__(
        self,
        *,
        store: MetadataStore,
        vector_store: VectorStore,
        registry: ModelRegistry,
        query_log: QueryLogSink,
        clock: Clock,
        reranker: Reranker | None = None,
    ) -> None:
        self._store = store
        self._vector_store = vector_store
        self._registry = registry
        self._query_log = query_log
        self._clock = clock
        self._reranker = reranker

    async def compare(
        self,
        *,
        query: str,
        model_ids: list[str],
        k: int = 10,
        candidates: int = 100,
        rerank: bool = True,
    ) -> CompareOutcome:
        """Run each arm sequentially (respects embedding rate limits)."""
        models = []
        for model_id in model_ids:
            model = await self._store.get_model(model_id)
            if model is None:
                raise ModelNotFoundError(model_id)
            if model.status is not ModelStatus.READY:
                raise ModelNotComparableError(model_id, model.status)
            models.append(model)

        compare_id = str(new_query_id())
        sparse = await self._vector_store.sparse_query(text=query, limit=candidates)

        arms: list[CompareArm] = []
        ranked_ids: dict[str, list[str]] = {}
        for model in models:
            arm = await self._run_arm(
                model_id=model.id,
                query=query,
                sparse=sparse,
                k=k,
                candidates=candidates,
                rerank=rerank,
                compare_id=compare_id,
            )
            arms.append(arm)
            ranked_ids[model.id] = [str(page.id) for page, _ in arm.pages]

        agreement = [
            PairAgreement(
                model_a=a,
                model_b=b,
                jaccard_at_k=jaccard_at_k(ranked_ids[a], ranked_ids[b], k),
                rbo=rbo_ext(ranked_ids[a], ranked_ids[b]),
                kendall_tau=kendall_tau_intersection(ranked_ids[a], ranked_ids[b]),
                intersection_size=len(set(ranked_ids[a]) & set(ranked_ids[b])),
            )
            for i, a in enumerate(ranked_ids)
            for b in list(ranked_ids)[i + 1 :]
        ]
        return CompareOutcome(compare_id=compare_id, arms=arms, agreement=agreement)

    async def _run_arm(
        self,
        *,
        model_id: str,
        query: str,
        sparse: list[ChunkHit],
        k: int,
        candidates: int,
        rerank: bool,
        compare_id: str,
    ) -> CompareArm:
        model = await self._registry.require_model(model_id)
        embedder = self._registry.embedder_for(model)
        vector = await embedder.embed_query(query)
        dense = await self._vector_store.dense_query(
            model_id=model_id, vector=vector, limit=candidates
        )
        fused = rrf_fuse(dense=dense, sparse=sparse)[:candidates]

        rerank_by_id: dict[str, float] = {}
        if rerank and self._reranker is not None and fused:
            chunks = await self._store.get_chunks([hit.chunk_id for hit in fused])
            scores = await self._reranker.rerank(
                query=query,
                candidates=[
                    RerankCandidate(chunk_id=chunk.id, text=chunk.text)
                    for chunk in chunks
                ],
            )
            rerank_by_id = {score.chunk_id: score.score for score in scores}

        scored = [
            ScoredChunk(
                chunk_id=hit.chunk_id,
                page_id=hit.page_id,
                ordinal=hit.ordinal,
                fusion_score=hit.score,
                rerank_score=rerank_by_id.get(hit.chunk_id),
            )
            for hit in fused
        ]
        page_scores = rollup_pages(chunks=scored, strategy=RollupStrategy.MAX)[:k]
        pages = await self._store.get_pages([p.page_id for p in page_scores])
        by_id: dict[PageId, Page] = {
            page.id: page for page in pages if page.status is PageStatus.INDEXED
        }
        ranked = [
            (by_id[p.page_id], p.score) for p in page_scores if p.page_id in by_id
        ]

        self._query_log.log_query(
            QueryLogRecord(
                query_id=new_query_id(),
                ts=self._clock.now(),
                kind="compare_arm",
                compare_id=compare_id,
                query_text=query,
                params={"k": k, "candidates": candidates, "rerank": rerank},
                active_model=model_id,
                reranker_model=(
                    self._reranker.model_name
                    if rerank and self._reranker is not None
                    else None
                ),
                candidate_set=tuple(
                    LoggedHit(chunk_id=h.chunk_id, page_id=h.page_id, score=h.score)
                    for h in fused
                ),
                dense_hits=tuple(
                    LoggedHit(chunk_id=h.chunk_id, page_id=h.page_id, score=h.score)
                    for h in dense
                ),
                sparse_hits=tuple(
                    LoggedHit(chunk_id=h.chunk_id, page_id=h.page_id, score=h.score)
                    for h in sparse
                ),
                final_pages=tuple(
                    LoggedPage(page_id=page.id, score=score, rank=rank)
                    for rank, (page, score) in enumerate(ranked, start=1)
                ),
                timing_ms={},
            )
        )
        return CompareArm(model_id=model_id, pages=ranked)
