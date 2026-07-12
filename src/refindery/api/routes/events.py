"""Server-sent events: live job status changes for the web UI.

Auth is the normal bearer header (the UI streams with fetch-based SSE, not
native EventSource); query-string tokens are deliberately unsupported — they
leak into access logs. This route is intentionally NOT an MCP tool: a
never-returning tool call would hang MCP clients.
"""

import asyncio
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from refindery.api.deps import get_container
from refindery.api.schemas import JobListResponse, JobResponse
from refindery.application.container import Container
from refindery.application.job_events import JobEvent
from refindery.domain.models import Job

router = APIRouter(prefix="/v1/events", tags=["events"])


def _job_response(job: Job) -> JobResponse:
    return JobResponse(
        job_id=job.id,
        kind=job.kind,
        status=job.status,
        attempts=job.attempts,
        max_attempts=job.max_attempts,
        last_error=job.last_error,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def _event_response(event: JobEvent) -> JobResponse:
    return JobResponse(
        job_id=event.job_id,
        kind=event.kind,
        status=event.status,
        attempts=event.attempts,
        max_attempts=event.max_attempts,
        last_error=event.last_error,
        created_at=event.created_at,
        updated_at=event.updated_at,
    )


async def _stream(container: Container) -> AsyncIterator[str]:
    heartbeat_s = container.settings.events.heartbeat_s
    # Subscribe BEFORE the snapshot so no transition falls into a gap.
    with container.events.subscribed() as queue:
        yield "retry: 3000\n\n"
        jobs = await container.store.list_jobs(limit=100)
        snapshot = JobListResponse(jobs=[_job_response(job) for job in jobs])
        yield f"event: snapshot\ndata: {snapshot.model_dump_json()}\n\n"
        event_id = 0
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=heartbeat_s)
            except TimeoutError:
                yield ": keep-alive\n\n"
                continue
            if item is None:  # close sentinel: server is shutting down
                return
            event_id += 1
            payload = _event_response(item).model_dump_json()
            yield f"id: {event_id}\nevent: job\ndata: {payload}\n\n"


@router.get(
    "",
    operation_id="stream_events",
    summary="Stream job status changes (SSE)",
    description=(
        "A text/event-stream of job ledger transitions. Opens with a "
        "`snapshot` event carrying the current job list (same shape as GET "
        "/v1/jobs), then one `job` event per status change (pending, "
        "running, done, failed, dead), with `: keep-alive` comments between "
        "events. Returns 503 when the subscriber limit is reached. "
        "Authenticate with the normal bearer header (fetch-based SSE); "
        "query-string tokens are not supported."
    ),
)
async def stream_events(
    container: Annotated[Container, Depends(get_container)],
) -> StreamingResponse:
    """Stream job transitions until the client disconnects."""
    if not container.events.has_capacity():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="event stream subscriber limit reached",
        )
    return StreamingResponse(
        _stream(container),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
