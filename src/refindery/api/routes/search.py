"""Search, similarity, and feedback endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from refindery.adapters.observability.metrics import search_duration_seconds
from refindery.adapters.observability.otel import span
from refindery.api.deps import get_container
from refindery.api.schemas import (
    ChunkResult,
    FeedbackRequest,
    PageResult,
    SearchRequest,
    SearchResponse,
    SimilarResponse,
    SimilarResult,
    Suggestion,
)
from refindery.api.schemas import ClusterRef as ApiClusterRef
from refindery.application.container import Container
from refindery.application.services.search_service import (
    SearchFilters,
    SearchQuery,
    SearchResultPage,
)
from refindery.application.services.similarity_service import Mediation
from refindery.domain.errors import (
    FeatureUnavailableError,
    NoActiveModelError,
    PageNotFoundError,
)
from refindery.domain.ids import PageId, QueryId

router = APIRouter(prefix="/v1", tags=["search"])


def _page_result(result: SearchResultPage) -> PageResult:
    return PageResult(
        page_id=result.page.id,
        canonical_url=result.page.canonical_url,
        title=result.page.title,
        domain=result.page.domain,
        first_seen_at=result.page.first_seen_at,
        visit_count=result.page.visit_count,
        score=result.score,
        cluster=(
            None
            if result.cluster is None
            else ApiClusterRef(id=result.cluster.id, label=result.cluster.label)
        ),
        chunks=[
            ChunkResult(
                chunk_id=chunk.id,
                ordinal=chunk.ordinal,
                text=chunk.text,
                score=score,
            )
            for chunk, score in result.chunks
        ],
        exact_match=result.exact_match,
    )


@router.post(
    "/search",
    operation_id="search",
    summary="Search the reading history",
    description=(
        "Hybrid semantic + keyword search over pages the user has read. "
        "Returns grounded passages from the user's own reading history. "
        "Contains no information the user has not read. Returns an empty "
        "result when nothing matches."
    ),
)
async def search(
    request: SearchRequest,
    container: Annotated[Container, Depends(get_container)],
) -> SearchResponse:
    """Run the retrieval pipeline."""
    try:
        with span("search"), search_duration_seconds.time():
            outcome = await container.search.search(
                SearchQuery(
                    query=request.query,
                    k=request.k,
                    offset=request.offset,
                    candidates=request.candidates,
                    rerank=request.rerank,
                    chunks_per_page=request.chunks_per_page,
                    rollup=request.rollup,
                    rollup_m=request.rollup_m,
                    rrf_k=request.rrf_k,
                    suggest=request.suggest,
                    mediation=request.mediation,
                    recency_half_life_days=request.recency_half_life_days,
                    filters=(
                        None
                        if request.filters is None
                        else SearchFilters(
                            domain=request.filters.domain,
                            after=request.filters.after,
                            before=request.filters.before,
                            cluster_id=request.filters.cluster_id,
                            entity=request.filters.entity,
                        )
                    ),
                )
            )
    except FeatureUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc)
        ) from exc
    except NoActiveModelError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    suggestion_pages = await container.store.get_pages(
        [s.page_id for s in outcome.suggestions]
    )
    titles = {p.id: p.title for p in suggestion_pages}
    return SearchResponse(
        query_id=outcome.query_id,
        results=[_page_result(r) for r in outcome.results],
        offset=request.offset,
        has_more=outcome.has_more,
        suggestions=[
            Suggestion(page_id=s.page_id, title=titles.get(s.page_id), reason=s.reason)
            for s in outcome.suggestions
        ],
        timing_ms=outcome.timing_ms,
    )


@router.get(
    "/pages/{page_id}/similar",
    operation_id="similar_to",
    summary="Pages similar to a page",
    description=(
        "Rank other pages from the user's reading history by similarity to "
        "this one. Returns grounded results from the user's own reading "
        "history only."
    ),
)
async def similar_to(
    page_id: str,
    container: Annotated[Container, Depends(get_container)],
    mediation: Annotated[Mediation, Query()] = Mediation.VECTOR,
    k: Annotated[int, Query(ge=1, le=50)] = 10,
) -> SimilarResponse:
    """Rank similar pages using vector|cluster|entity mediation."""
    try:
        similar = await container.similarity.similar(
            page_id=PageId(page_id), mediation=mediation, k=k
        )
    except PageNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="page not found"
        ) from exc
    except FeatureUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc)
        ) from exc
    except NoActiveModelError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    pages = await container.store.get_pages([s.page_id for s in similar])
    by_id = {p.id: p for p in pages}
    return SimilarResponse(
        page_id=page_id,
        mediation=mediation,
        results=[
            SimilarResult(
                page_id=s.page_id,
                canonical_url=by_id[s.page_id].canonical_url,
                title=by_id[s.page_id].title,
                score=s.score,
                reason=s.reason,
            )
            for s in similar
            if s.page_id in by_id
        ],
    )


@router.post(
    "/feedback",
    operation_id="record_feedback",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Record relevance feedback",
)
async def record_feedback(
    request: FeedbackRequest,
    container: Annotated[Container, Depends(get_container)],
) -> dict[str, str]:
    """Append feedback; unknown query_ids are accepted (eval join drops them)."""
    container.feedback.record(
        query_id=QueryId(request.query_id),
        page_id=PageId(request.page_id),
        relevant=request.relevant,
    )
    return {"status": "recorded"}
