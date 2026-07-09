"""Embedding model registry endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from refindery.api.auth import require_write
from refindery.api.deps import get_container
from refindery.api.schemas import (
    BackfillEstimateResponse,
    BackfillRequest,
    BackfillStartedResponse,
    ModelInfo,
    ModelListResponse,
    RegisterModelRequest,
)
from refindery.application.container import Container
from refindery.domain.errors import ModelBudgetError, ModelNotFoundError
from refindery.domain.models import EmbeddingModel, ModelStatus

router = APIRouter(prefix="/v1/models", tags=["models"])


def _info(model: EmbeddingModel) -> ModelInfo:
    return ModelInfo(
        id=model.id,
        provider=model.provider,
        model_name=model.model_name,
        dim=model.dim,
        max_input_tokens=model.max_input_tokens,
        is_active=model.is_active,
        status=model.status,
    )


@router.get("", operation_id="list_models_api", summary="List embedding models")
async def list_models(
    container: Annotated[Container, Depends(get_container)],
) -> ModelListResponse:
    """All registered models."""
    models = await container.store.list_models()
    return ModelListResponse(models=[_info(m) for m in models])


@router.post(
    "",
    operation_id="register_model",
    dependencies=[Depends(require_write)],
    status_code=status.HTTP_201_CREATED,
    summary="Register an embedding model",
)
async def register_model(
    request: RegisterModelRequest,
    container: Annotated[Container, Depends(get_container)],
) -> ModelInfo:
    """Register (not active, no vectors yet). Rejected below the chunk budget."""
    model_id = request.id or request.model_name
    if await container.store.get_model(model_id) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="model already registered"
        )
    model = EmbeddingModel(
        id=model_id,
        provider=request.provider,
        model_name=request.model_name,
        dim=request.dim,
        max_input_tokens=request.max_input_tokens,
        is_active=False,
        status=ModelStatus.REGISTERED,
        created_at=container.clock.now(),
    )
    try:
        await container.registry.register(model)
    except ModelBudgetError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    return _info(model)


@router.post(
    "/{model_id}/backfill",
    operation_id="backfill_model",
    dependencies=[Depends(require_write)],
    summary="Estimate or start a backfill",
    description=(
        "confirm=false returns an exact dry-run estimate (chunk token counts "
        "are stored); confirm=true starts the durable, resumable backfill."
    ),
    responses={200: {"model": BackfillEstimateResponse}},
)
async def backfill_model(
    model_id: str,
    request: BackfillRequest,
    container: Annotated[Container, Depends(get_container)],
) -> BackfillEstimateResponse | BackfillStartedResponse:
    """Dry-run by default; explicit confirmation spends money."""
    try:
        if not request.confirm:
            estimate = await container.backfill.estimate(model_id)
            return BackfillEstimateResponse(
                model_id=estimate.model_id,
                n_chunks=estimate.n_chunks,
                total_tokens=estimate.total_tokens,
                est_cost_usd=estimate.est_cost_usd,
                est_duration_s=estimate.est_duration_s,
            )
        await container.backfill.start(model_id)
    except ModelNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    return BackfillStartedResponse(model_id=model_id)


@router.post(
    "/{model_id}/activate",
    operation_id="activate_model",
    dependencies=[Depends(require_write)],
    summary="Make this the active search model",
)
async def activate_model(
    model_id: str,
    container: Annotated[Container, Depends(get_container)],
) -> ModelInfo:
    """Atomic flip; only ready models can be activated."""
    model = await container.store.get_model(model_id)
    if model is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="model not found"
        )
    if model.status is not ModelStatus.READY:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"model is {model.status}; only ready models activate",
        )
    await container.store.activate_model(model_id)
    refreshed = await container.store.get_model(model_id)
    assert refreshed is not None  # noqa: S101 — just activated
    return _info(refreshed)


@router.delete(
    "/{model_id}",
    operation_id="retire_model",
    dependencies=[Depends(require_write)],
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Retire a model and drop its vector space",
)
async def retire_model(
    model_id: str,
    container: Annotated[Container, Depends(get_container)],
) -> None:
    """409 when active; drops the vector space and page vectors."""
    model = await container.store.get_model(model_id)
    if model is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="model not found"
        )
    if model.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="cannot retire the active model; activate another first",
        )
    await container.vector_store.drop_model(model_id)
    await container.store.delete_model(model_id)
