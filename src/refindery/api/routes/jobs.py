"""Admin job endpoints: dead-letter visibility and manual retry."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from refindery.api.auth import require_write
from refindery.api.deps import get_container
from refindery.api.schemas import JobListResponse, JobResponse
from refindery.application.container import Container
from refindery.domain.errors import JobNotFoundError
from refindery.domain.ids import JobId
from refindery.domain.models import Job, JobStatus

router = APIRouter(prefix="/v1/jobs", tags=["jobs"])


def _to_response(job: Job) -> JobResponse:
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


@router.get("", operation_id="list_jobs", summary="List jobs")
async def list_jobs(
    container: Annotated[Container, Depends(get_container)],
    status_filter: Annotated[JobStatus | None, "status"] = None,
    limit: int = 100,
) -> JobListResponse:
    """List jobs, newest first, optionally filtered by status."""
    jobs = await container.store.list_jobs(status=status_filter, limit=limit)
    return JobListResponse(jobs=[_to_response(job) for job in jobs])


@router.post(
    "/{job_id}/retry",
    operation_id="retry_job",
    dependencies=[Depends(require_write)],
    summary="Retry a dead job",
)
async def retry_job(
    job_id: str,
    container: Annotated[Container, Depends(get_container)],
) -> JobResponse:
    """Reset a dead job to pending and re-enqueue it."""
    job = await container.store.get_job(JobId(job_id))
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="job not found"
        )
    if job.status is not JobStatus.DEAD:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"job is {job.status}, only dead jobs can be retried",
        )
    try:
        await container.queue.retry(JobId(job_id))
    except JobNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="job not found"
        ) from exc
    refreshed = await container.store.get_job(JobId(job_id))
    if refreshed is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="job not found"
        )
    return _to_response(refreshed)
