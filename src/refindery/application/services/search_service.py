"""Hybrid search: embed -> dense+sparse -> RRF -> rerank -> rollup -> pages.

Includes the exact-match pre-pass: a query that parses as a URL or bare
domain pins the matching page(s) at rank 1 — the cheap, high-value "I'm
pasting the URL back" refind case.
"""

import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime

from refindery.application.ports.clock import Clock
from refindery.application.ports.metadata_store import MetadataStore
from refindery.application.ports.query_log import (
    LoggedHit,
    LoggedPage,
    QueryLogRecord,
    QueryLogSink,
)
from refindery.application.ports.reranker import RerankCandidate, Reranker
from refindery.application.ports.vector_store import (
    HybridQuery,
    StoreFilter,
    VectorStore,
)
from refindery.application.services.model_registry import ModelRegistry
from refindery.application.services.similarity_service import (
    Mediation,
    SimilarityService,
    SimilarPage,
)
from refindery.application.timing import StageTimer
from refindery.domain.canonical_url import CanonicalizationRules, canonicalize
from refindery.domain.errors import (
    FeatureUnavailableError,
    NoActiveModelError,
    RefinderyError,
)
from refindery.domain.ids import ClusterId, PageId, QueryId, new_query_id
from refindery.domain.models import Chunk, Page
from refindery.domain.retrieval import (
    ChunkHit,
    PageScore,
    RollupStrategy,
    ScoredChunk,
    apply_recency_decay,
    rollup_pages,
)

logger = logging.getLogger(__name__)

_BARE_DOMAIN = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9-]+)+$")
_EXACT_MATCH_SCORE = 1.0
_MAX_ENTITY_PAGES = 10_000


class EntityFilterTooBroadError(RefinderyError):
    """The entity filter matches too many pages to push into the store."""

    def __init__(self, *, entity: str, matches: int) -> None:
        self.entity = entity
        super().__init__(
            f"entity filter {entity!r} matches {matches} pages "
            f"(cap {_MAX_ENTITY_PAGES}); narrow the query"
        )


@dataclass(frozen=True, slots=True)
class SearchFilters:
    """First-class filters, pushed into the vector store."""

    domain: str | None = None
    after: datetime | None = None
    before: datetime | None = None
    cluster_id: str | None = None
    entity: str | None = None  # id or canonical form; resolvable from M4


@dataclass(frozen=True, slots=True)
class SearchQuery:
    """Validated search parameters (API defaults mirror the spec)."""

    query: str
    k: int = 10
    candidates: int = 100
    rerank: bool = True
    chunks_per_page: int = 2
    rollup: RollupStrategy = RollupStrategy.MAX
    rollup_m: int = 3
    rrf_k: int = 60
    suggest: int = 3
    mediation: Mediation = Mediation.VECTOR
    recency_half_life_days: float | None = None
    filters: SearchFilters | None = None


@dataclass(frozen=True, slots=True)
class ClusterRef:
    """Cluster membership shown on a result."""

    id: str
    label: str | None


@dataclass(frozen=True, slots=True)
class SearchResultPage:
    """One ranked page with its matched chunks (whole chunk text)."""

    page: Page
    score: float
    chunks: tuple[tuple[Chunk, float], ...]
    exact_match: bool = False
    cluster: ClusterRef | None = None


@dataclass(frozen=True, slots=True)
class SearchOutcome:
    """Everything the API response needs."""

    query_id: QueryId
    results: list[SearchResultPage]
    suggestions: list[SimilarPage]
    timing_ms: dict[str, float] = field(default_factory=dict)


class SearchService:
    """Orchestrates the retrieval pipeline."""

    def __init__(
        self,
        *,
        store: MetadataStore,
        vector_store: VectorStore,
        registry: ModelRegistry,
        similarity: SimilarityService,
        query_log: QueryLogSink,
        clock: Clock,
        reranker: Reranker | None = None,
        rules: CanonicalizationRules | None = None,
    ) -> None:
        self._store = store
        self._vector_store = vector_store
        self._registry = registry
        self._similarity = similarity
        self._query_log = query_log
        self._clock = clock
        self._reranker = reranker
        self._rules = rules or CanonicalizationRules()

    async def search(self, request: SearchQuery) -> SearchOutcome:
        """Run the full pipeline and log the execution."""
        timer = StageTimer()
        query_id = new_query_id()

        if (model := await self._store.get_active_model()) is None:
            raise NoActiveModelError
        embedder = self._registry.embedder_for(model)

        store_filter = await self._resolve_filters(request.filters)
        exact_pages = await self._exact_match_pages(request.query)

        with timer.stage("embed"):
            query_vector = await embedder.embed_query(request.query)

        hits = await self._vector_store.hybrid_query(
            HybridQuery(
                model_id=model.id,
                dense_vector=query_vector,
                sparse_text=request.query,
                per_arm_limit=request.candidates,
                fused_limit=request.candidates,
                rrf_k=request.rrf_k,
                filters=store_filter,
            )
        )
        timer.record("dense", hits.timing.dense_ms)
        timer.record("sparse", hits.timing.sparse_ms)
        timer.record("fuse", hits.timing.fuse_ms)

        scored = await self._rerank(request, hits.fused, timer)

        with timer.stage("rollup"):
            pages = rollup_pages(
                chunks=scored, strategy=request.rollup, top_m=request.rollup_m
            )
        hydrated = await self._hydrate(
            request, pages, exact_pages=exact_pages, timer=timer
        )

        suggestions = await self._suggestions(request, hydrated)

        outcome = SearchOutcome(
            query_id=query_id,
            results=hydrated[: request.k],
            suggestions=suggestions,
            timing_ms={**timer.timings_ms, "total": timer.total_ms()},
        )
        self._log(
            request,
            outcome,
            model_id=model.id,
            fused=hits.fused,
            dense=hits.dense,
            sparse=hits.sparse,
        )
        return outcome

    # -- stages -----------------------------------------------------------

    async def _resolve_filters(
        self, filters: SearchFilters | None
    ) -> StoreFilter | None:
        if filters is None:
            return None
        page_ids: frozenset[PageId] | None = None
        if filters.entity is not None:
            entity = await self._store.resolve_entity(filters.entity)
            if entity is None:
                page_ids = frozenset()
            else:
                matches = await self._store.page_ids_for_entity(entity.id)
                if len(matches) > _MAX_ENTITY_PAGES:
                    raise EntityFilterTooBroadError(
                        entity=filters.entity, matches=len(matches)
                    )
                page_ids = frozenset(matches)
        return StoreFilter(
            domain=filters.domain,
            after=filters.after,
            before=filters.before,
            cluster_id=(
                None if filters.cluster_id is None else ClusterId(filters.cluster_id)
            ),
            page_ids=page_ids,
        )

    async def _exact_match_pages(self, query: str) -> list[PageId]:
        """URL or bare-domain queries pin exact matches at rank 1."""
        text = query.strip()
        if text.startswith(("http://", "https://")):
            try:
                canonical = canonicalize(text, rules=self._rules)
            except ValueError:
                return []
            page = await self._store.get_page_by_canonical_url(canonical.url)
            return [] if page is None else [page.id]
        if _BARE_DOMAIN.match(text.lower()):
            return await self._store.list_page_ids_by_domain(
                domain=text.lower().removeprefix("www."), limit=5
            )
        return []

    async def _rerank(
        self, request: SearchQuery, fused: list[ChunkHit], timer: StageTimer
    ) -> list[ScoredChunk]:
        fusion_scores = {hit.chunk_id: hit for hit in fused}
        if not request.rerank or self._reranker is None or not fused:
            return [
                ScoredChunk(
                    chunk_id=hit.chunk_id,
                    page_id=hit.page_id,
                    ordinal=hit.ordinal,
                    fusion_score=hit.score,
                )
                for hit in fused
            ]
        chunks = await self._store.get_chunks([hit.chunk_id for hit in fused])
        with timer.stage("rerank"):
            scores = await self._reranker.rerank(
                query=request.query,
                candidates=[
                    RerankCandidate(chunk_id=chunk.id, text=chunk.text)
                    for chunk in chunks
                ],
            )
        rerank_by_id = {score.chunk_id: score.score for score in scores}
        return [
            ScoredChunk(
                chunk_id=hit.chunk_id,
                page_id=hit.page_id,
                ordinal=hit.ordinal,
                fusion_score=hit.score,
                rerank_score=rerank_by_id.get(hit.chunk_id),
            )
            for hit in fusion_scores.values()
        ]

    async def _hydrate(
        self,
        request: SearchQuery,
        pages: list[PageScore],
        *,
        exact_pages: list[PageId],
        timer: StageTimer,
    ) -> list[SearchResultPage]:
        with timer.stage("hydrate"):
            page_rows = await self._store.get_pages(
                [*exact_pages, *[p.page_id for p in pages]]
            )
            by_id = {page.id: page for page in page_rows}

            if request.recency_half_life_days is not None:
                pages = apply_recency_decay(
                    pages,
                    first_seen={
                        p.page_id: by_id[p.page_id].first_seen_at
                        for p in pages
                        if p.page_id in by_id
                    },
                    now=self._clock.now(),
                    half_life_days=request.recency_half_life_days,
                )

            results: list[SearchResultPage] = []
            seen: set[PageId] = set()
            for page_id in exact_pages:
                if (page := by_id.get(page_id)) is not None:
                    results.append(
                        SearchResultPage(
                            page=page,
                            score=_EXACT_MATCH_SCORE,
                            chunks=(),
                            exact_match=True,
                        )
                    )
                    seen.add(page_id)

            for page_score in pages:
                if page_score.page_id in seen:
                    continue
                page = by_id.get(page_score.page_id)
                if page is None:
                    # Purged mid-flight: metadata is authoritative; drop the hit.
                    logger.info("dropping hit for purged page %s", page_score.page_id)
                    continue
                top_chunks = page_score.chunks[: request.chunks_per_page]
                chunk_rows = await self._store.get_chunks(
                    [chunk.chunk_id for chunk in top_chunks]
                )
                chunk_by_id = {chunk.id: chunk for chunk in chunk_rows}
                cluster = await self._store.cluster_for_page(page.id)
                results.append(
                    SearchResultPage(
                        page=page,
                        score=page_score.score,
                        chunks=tuple(
                            (chunk_by_id[c.chunk_id], c.effective_score)
                            for c in top_chunks
                            if c.chunk_id in chunk_by_id
                        ),
                        cluster=(
                            None
                            if cluster is None
                            else ClusterRef(id=cluster.id, label=cluster.label)
                        ),
                    )
                )
                seen.add(page_score.page_id)
        return results

    async def _suggestions(
        self, request: SearchQuery, results: list[SearchResultPage]
    ) -> list[SimilarPage]:
        if request.suggest <= 0 or not results:
            return []
        exclude = frozenset(r.page.id for r in results[: request.k])
        try:
            return await self._similarity.similar(
                page_id=results[0].page.id,
                mediation=request.mediation,
                k=request.suggest,
                exclude=exclude,
            )
        except FeatureUnavailableError:
            raise
        except Exception:  # noqa: BLE001 — suggestions must never fail a search
            logger.warning("suggestions failed", exc_info=True)
            return []

    def _log(
        self,
        request: SearchQuery,
        outcome: SearchOutcome,
        *,
        model_id: str,
        fused: list[ChunkHit],
        dense: list[ChunkHit],
        sparse: list[ChunkHit],
    ) -> None:
        def logged(hits: list[ChunkHit]) -> tuple[LoggedHit, ...]:
            return tuple(
                LoggedHit(chunk_id=h.chunk_id, page_id=h.page_id, score=h.score)
                for h in hits
            )

        record = QueryLogRecord(
            query_id=outcome.query_id,
            ts=self._clock.now(),
            kind="search",
            query_text=request.query,
            params={
                "k": request.k,
                "candidates": request.candidates,
                "rerank": request.rerank,
                "rollup": request.rollup,
                "rrf_k": request.rrf_k,
                "filters": None if request.filters is None else asdict(request.filters),
                "exact_match": any(r.exact_match for r in outcome.results),
            },
            active_model=model_id,
            reranker_model=(
                self._reranker.model_name
                if request.rerank and self._reranker is not None
                else None
            ),
            candidate_set=logged(fused),
            dense_hits=logged(dense),
            sparse_hits=logged(sparse),
            final_pages=tuple(
                LoggedPage(page_id=r.page.id, score=r.score, rank=rank)
                for rank, r in enumerate(outcome.results, start=1)
            ),
            timing_ms=outcome.timing_ms,
        )
        self._query_log.log_query(record)
