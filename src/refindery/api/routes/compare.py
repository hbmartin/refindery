"""A/B model comparison endpoint."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from refindery.api.deps import get_container
from refindery.api.schemas import (
    CompareAgreement,
    ComparedPage,
    CompareModelRun,
    CompareRequest,
    CompareResponse,
)
from refindery.application.container import Container
from refindery.application.services.compare_service import ModelNotComparableError
from refindery.domain.errors import ModelNotFoundError

router = APIRouter(prefix="/v1", tags=["compare"])


@router.post(
    "/compare",
    operation_id="compare",
    summary="Compare embedding models on one query",
    description=(
        "Runs the full retrieval pipeline once per model; the sparse arm and "
        "reranker are identical across arms, so the delta isolates the "
        "embedder. Returns per-model rankings over the user's reading "
        "history plus agreement statistics (Jaccard@k, RBO, Kendall's tau)."
    ),
)
async def compare(
    request: CompareRequest,
    container: Annotated[Container, Depends(get_container)],
) -> CompareResponse:
    """Run the comparison."""
    try:
        outcome = await container.compare.compare(
            query=request.query,
            model_ids=request.models,
            k=request.k,
            candidates=request.candidates,
            rerank=request.rerank,
        )
    except (ModelNotFoundError, ModelNotComparableError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    return CompareResponse(
        compare_id=outcome.compare_id,
        query=request.query,
        runs=[
            CompareModelRun(
                model=arm.model_id,
                results=[
                    ComparedPage(
                        page_id=page.id,
                        canonical_url=page.canonical_url,
                        title=page.title,
                        score=score,
                    )
                    for page, score in arm.pages
                ],
            )
            for arm in outcome.arms
        ],
        agreement=[
            CompareAgreement(
                model_a=pair.model_a,
                model_b=pair.model_b,
                jaccard_at_k=pair.jaccard_at_k,
                rbo=pair.rbo,
                kendall_tau=pair.kendall_tau,
                intersection_size=pair.intersection_size,
            )
            for pair in outcome.agreement
        ],
    )
