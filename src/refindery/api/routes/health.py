"""Liveness and readiness endpoints (no auth: they leak nothing)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from refindery.adapters.observability.metrics import render_metrics
from refindery.api.auth import require_read
from refindery.api.deps import get_container
from refindery.application.container import Container

router = APIRouter(tags=["health"])


@router.get(
    "/metrics",
    operation_id="metrics",
    dependencies=[Depends(require_read)],
    summary="Read Prometheus metrics",
    description="Return the Prometheus text exposition for authenticated scrapers.",
    response_class=Response,
    responses={
        200: {
            "content": {"text/plain": {"schema": {"type": "string"}}},
            "description": "Prometheus text exposition.",
        }
    },
)
async def metrics() -> Response:
    """Prometheus metrics (bearer auth; scrapers support bearer_token)."""
    payload, content_type = render_metrics()
    return Response(content=payload, media_type=content_type)


@router.get(
    "/healthz",
    operation_id="healthz",
    summary="Check process liveness",
    description="Return success when the API process is running. No authentication.",
)
async def healthz() -> dict[str, str]:
    """Process is up."""
    return {"status": "ok"}


@router.get(
    "/readyz",
    operation_id="readyz",
    summary="Check service readiness",
    description=(
        "Check that the metadata store is reachable and an embedding model is "
        "active. Returns 503 until both conditions hold. No authentication."
    ),
    responses={503: {"description": "A required dependency is unavailable."}},
)
async def readyz(
    response: Response,
    container: Annotated[Container, Depends(get_container)],
) -> dict[str, object]:
    """Dependencies are usable: metadata store reachable, a model is active."""
    try:
        active = await container.store.get_active_model()
    except Exception:  # noqa: BLE001 — readiness must not 500
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "metadata store unavailable"}
    if active is None:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "no active embedding model"}
    return {
        "status": "ready",
        "capabilities": {"batch_ingest": True, "batch_status": True},
    }
