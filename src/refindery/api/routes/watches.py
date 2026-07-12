"""Watch management endpoints: scheduled pull sources (RSS feeds, ...)."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from refindery.api.auth import require_write
from refindery.api.deps import get_container
from refindery.api.schemas import (
    CreateWatchRequest,
    UpdateWatchRequest,
    WatchListResponse,
    WatchResponse,
    WatchRunAcceptedResponse,
)
from refindery.application.container import Container
from refindery.application.services.watch_service import WatchPatch
from refindery.domain.errors import WatchNotFoundError
from refindery.domain.ids import WatchId
from refindery.domain.models import Watch

router = APIRouter(prefix="/v1/watches", tags=["watches"])


def _watch_response(watch: Watch) -> WatchResponse:
    return WatchResponse(
        id=watch.id,
        kind=watch.kind,
        url=watch.url,
        title=watch.title,
        enabled=watch.enabled,
        interval_hours=watch.interval_hours,
        config=watch.config,
        next_run_at=watch.next_run_at,
        last_run_at=watch.last_run_at,
        last_status=watch.last_status,
        last_error=watch.last_error,
        last_item_count=watch.last_item_count,
        created_at=watch.created_at,
    )


def _not_found(watch_id: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail=f"watch {watch_id!r} not found"
    )


@router.post(
    "",
    operation_id="create_watch",
    dependencies=[Depends(require_write)],
    status_code=status.HTTP_201_CREATED,
    summary="Create a watch",
    description=(
        "Register a URL to poll on a schedule (default every 24 hours). Each "
        "poll discovers the source's current item URLs and ingests the new "
        "ones, so they become searchable. Kind 'rss' polls an RSS/Atom feed. "
        "Already-indexed and blacklisted items are skipped automatically. "
        "The first poll runs within a minute of creation."
    ),
)
async def create_watch(
    request: CreateWatchRequest,
    container: Annotated[Container, Depends(get_container)],
) -> WatchResponse:
    """Create a watch; 409 when the same (kind, url) is already watched."""
    if request.kind not in container.watches.supported_kinds:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=(
                f"watch kind {request.kind!r} is not available in this "
                "deployment (its optional extra is not installed)"
            ),
        )
    watch = await container.watches.create(
        kind=request.kind,
        url=request.url,
        title=request.title,
        interval_hours=request.interval_hours,
        enabled=request.enabled,
        config=request.config,
    )
    if watch is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"a {request.kind} watch for {request.url!r} already exists",
        )
    return _watch_response(watch)


@router.get(
    "",
    operation_id="list_watches",
    summary="List watches",
    description=(
        "All watches with their schedules and last-poll health "
        "(status, error, item count)."
    ),
)
async def list_watches(
    container: Annotated[Container, Depends(get_container)],
) -> WatchListResponse:
    """All watches, newest first."""
    watches = await container.watches.list_all()
    return WatchListResponse(watches=[_watch_response(watch) for watch in watches])


@router.get(
    "/{watch_id}",
    operation_id="get_watch",
    summary="Get a watch",
    description="One watch with its schedule and last-poll health.",
)
async def get_watch(
    watch_id: str,
    container: Annotated[Container, Depends(get_container)],
) -> WatchResponse:
    """Fetch one watch."""
    watch = await container.watches.get(WatchId(watch_id))
    if watch is None:
        raise _not_found(watch_id)
    return _watch_response(watch)


@router.patch(
    "/{watch_id}",
    operation_id="update_watch",
    dependencies=[Depends(require_write)],
    summary="Update a watch",
    description=(
        "Partially update a watch: pause/resume with 'enabled', change the "
        "poll interval, title, or per-kind config. Omitted fields are left "
        "unchanged; the URL is immutable (delete and recreate instead). "
        "Changing the interval reschedules the next poll from the last run."
    ),
)
async def update_watch(
    watch_id: str,
    request: UpdateWatchRequest,
    container: Annotated[Container, Depends(get_container)],
) -> WatchResponse:
    """Apply a partial update."""
    watch = await container.watches.update(
        WatchId(watch_id),
        WatchPatch(
            enabled=request.enabled,
            interval_hours=request.interval_hours,
            title=request.title,
            config=request.config,
        ),
    )
    if watch is None:
        raise _not_found(watch_id)
    return _watch_response(watch)


@router.delete(
    "/{watch_id}",
    operation_id="delete_watch",
    dependencies=[Depends(require_write)],
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a watch",
    description=(
        "Stop polling this source. Pages already ingested by the watch stay "
        "indexed; use forget to remove content."
    ),
)
async def delete_watch(
    watch_id: str,
    container: Annotated[Container, Depends(get_container)],
) -> None:
    """Delete a watch."""
    if not await container.watches.delete(WatchId(watch_id)):
        raise _not_found(watch_id)


@router.post(
    "/{watch_id}/run",
    operation_id="run_watch",
    dependencies=[Depends(require_write)],
    status_code=status.HTTP_202_ACCEPTED,
    summary="Poll a watch now",
    description=(
        "Enqueue an immediate poll instead of waiting for the schedule; the "
        "next scheduled poll moves to one interval from now. Returns the "
        "poll job id; track it via the jobs API."
    ),
)
async def run_watch(
    watch_id: str,
    container: Annotated[Container, Depends(get_container)],
) -> WatchRunAcceptedResponse:
    """Trigger an immediate poll."""
    try:
        job_id = await container.watches.run_now(WatchId(watch_id))
    except WatchNotFoundError as exc:
        raise _not_found(watch_id) from exc
    return WatchRunAcceptedResponse(watch_id=watch_id, job_id=job_id)
