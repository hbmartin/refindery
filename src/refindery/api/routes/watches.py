"""Watch management endpoints: create, list, inspect, delete, run-now."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from refindery.api.auth import require_write
from refindery.api.deps import get_container
from refindery.api.schemas import (
    CreateWatchRequest,
    RunWatchResponse,
    WatchListResponse,
    WatchResponse,
)
from refindery.application.container import Container
from refindery.domain.ids import WatchId
from refindery.domain.models import Watch

router = APIRouter(prefix="/v1/watches", tags=["watches"])


def _response(watch: Watch) -> WatchResponse:
    return WatchResponse(
        id=watch.id,
        kind=watch.kind,
        url=watch.url,
        interval_hours=watch.interval_hours,
        enabled=watch.enabled,
        next_run_at=watch.next_run_at,
        last_run_at=watch.last_run_at,
        last_status=watch.last_status,
        last_error=watch.last_error,
        last_item_count=watch.last_item_count,
        created_at=watch.created_at,
    )


@router.post(
    "",
    operation_id="create_watch",
    dependencies=[Depends(require_write)],
    status_code=status.HTTP_201_CREATED,
    summary="Create a watch",
    description=(
        "Register a source (currently an RSS/Atom feed) to poll every "
        "interval_hours. Each poll fetches the source, discovers item URLs, "
        "and ingests each new one into the index."
    ),
)
async def create_watch(
    request: CreateWatchRequest,
    container: Annotated[Container, Depends(get_container)],
) -> JSONResponse:
    """Create a watch (409 when a watch for this kind+url already exists)."""
    watch = await container.watches.create(
        kind=request.kind,
        url=request.url,
        interval_hours=request.interval_hours,
        enabled=request.enabled,
        config=request.config,
    )
    if watch is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="a watch for this kind and url already exists",
        )
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=_response(watch).model_dump(mode="json"),
    )


@router.get("", operation_id="list_watches", summary="List watches")
async def list_watches(
    container: Annotated[Container, Depends(get_container)],
) -> WatchListResponse:
    """All watches, newest first."""
    watches = await container.watches.list_watches()
    return WatchListResponse(watches=[_response(w) for w in watches])


@router.get("/{watch_id}", operation_id="get_watch", summary="Get one watch")
async def get_watch(
    watch_id: str,
    container: Annotated[Container, Depends(get_container)],
) -> WatchResponse:
    """One watch, including its most recent poll outcome."""
    watch = await container.watches.get(WatchId(watch_id))
    if watch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="watch not found"
        )
    return _response(watch)


@router.delete(
    "/{watch_id}",
    operation_id="delete_watch",
    dependencies=[Depends(require_write)],
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a watch",
    description="Stops future polls. Already-ingested pages are unaffected.",
)
async def delete_watch(
    watch_id: str,
    container: Annotated[Container, Depends(get_container)],
) -> None:
    """Delete a watch (404 when it does not exist)."""
    if not await container.watches.delete(WatchId(watch_id)):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="watch not found"
        )


@router.post(
    "/{watch_id}/run",
    operation_id="run_watch",
    dependencies=[Depends(require_write)],
    status_code=status.HTTP_202_ACCEPTED,
    summary="Poll a watch now",
    description="Enqueue an immediate poll and reset the schedule.",
)
async def run_watch(
    watch_id: str,
    container: Annotated[Container, Depends(get_container)],
) -> RunWatchResponse:
    """Trigger an out-of-schedule poll (404 when the watch does not exist)."""
    job_id = await container.watches.run_now(WatchId(watch_id))
    if job_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="watch not found"
        )
    return RunWatchResponse(watch_id=watch_id, job_id=job_id)
