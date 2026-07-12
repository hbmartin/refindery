"""Admin job endpoints: dead-letter visibility and manual retry."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from refindery.api.auth import require_write
from refindery.api.deps import get_container
from refindery.api.schemas import (
    JobListResponse,
    JobResponse,
    JobRetryBatchMissingResult,
    JobRetryBatchRequest,
    JobRetryBatchResponse,
    JobRetryBatchRetriedResult,
    JobRetryBatchSkippedResult,
)
from refindery.application.container import Container
from refindery.domain.errors import JobNotFoundError
from refindery.domain.ids import JobId
from refindery.domain.models import Job, JobKind, JobStatus

router = APIRouter(prefix="/v1/jobs", tags=["jobs"])

_RetryOutcome = (
    JobRetryBatchRetriedResult | JobRetryBatchSkippedResult | JobRetryBatchMissingResult
)


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


def _retried_result(job: Job) -> JobRetryBatchRetriedResult:
    return JobRetryBatchRetriedResult(
        job_id=job.id,
        kind=job.kind,
        status=job.status,
        attempts=job.attempts,
        max_attempts=job.max_attempts,
        last_error=job.last_error,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


async def _retry_one(container: Container, job_id: str) -> _RetryOutcome:
    """Retry one job; the status guard lives here (reset_job_for_retry has none)."""
    job = await container.store.get_job(JobId(job_id))
    if job is None:
        return JobRetryBatchMissingResult(job_id=job_id)
    if job.status is not JobStatus.DEAD:
        return JobRetryBatchSkippedResult(
            job_id=job_id,
            status=job.status,
            detail=f"job is {job.status}, only dead jobs can be retried",
        )
    try:
        await container.queue.retry(JobId(job_id))
    except JobNotFoundError:
        return JobRetryBatchMissingResult(job_id=job_id)
    refreshed = await container.store.get_job(JobId(job_id))
    if refreshed is None:
        return JobRetryBatchMissingResult(job_id=job_id)
    return _retried_result(refreshed)


@router.get("", operation_id="list_jobs", summary="List jobs")
async def list_jobs(
    container: Annotated[Container, Depends(get_container)],
    status_value: Annotated[JobStatus | None, Query(alias="status")] = None,
    status_filter: Annotated[JobStatus | None, Query(deprecated=True)] = None,
    kind: Annotated[JobKind | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=1_000)] = 100,
) -> JobListResponse:
    """List jobs newest first, optionally filtered by status and kind.

    ``status_filter`` is a deprecated compatibility alias for ``status``; a
    request that supplies both is rejected.
    """
    if status_value is not None and status_filter is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="use either status or the deprecated status_filter, not both",
        )
    jobs = await container.store.list_jobs(
        status=status_value or status_filter, kind=kind, limit=limit
    )
    return JobListResponse(jobs=[_to_response(job) for job in jobs])


@router.post(
    "/retry",
    operation_id="retry_jobs_batch",
    dependencies=[Depends(require_write)],
    summary="Retry dead jobs in bulk",
    description=(
        "Retry dead jobs either by explicit `job_ids` (up to 500, deduped, "
        "results in input order) or by selector (all dead jobs, optionally "
        "filtered by `kind`, up to `limit`). Non-dead jobs are reported as "
        "skipped and unknown ids as not_found; the call is idempotent — a "
        "second selector call finds nothing left to retry. Always returns 200."
    ),
)
async def retry_jobs_batch(
    body: JobRetryBatchRequest,
    container: Annotated[Container, Depends(get_container)],
) -> JobRetryBatchResponse:
    """Bulk-retry dead jobs with per-item outcomes."""
    if body.job_ids is not None:
        targets = list(dict.fromkeys(body.job_ids))
    else:
        dead = await container.store.list_jobs(
            status=JobStatus.DEAD, kind=body.kind, limit=body.limit
        )
        targets = [job.id for job in dead]
    results: list[_RetryOutcome] = []
    for job_id in targets:
        results.append(await _retry_one(container, job_id))  # noqa: PERF401 — sequential on purpose (SQLite writes serialize)
    retried = sum(1 for result in results if result.outcome == "retried")
    return JobRetryBatchResponse(
        requested=len(targets), retried=retried, results=results
    )


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
    """Reset a dead job to pending and re-enqueue it; other states return 409."""
    result = await _retry_one(container, job_id)
    match result:
        case JobRetryBatchMissingResult():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="job not found"
            )
        case JobRetryBatchSkippedResult():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=result.detail
            )
        case _:
            return result
