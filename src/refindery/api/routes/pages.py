"""Page ingest and read endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from refindery.adapters.observability.metrics import ingest_pages_total
from refindery.adapters.observability.otel import span
from refindery.api.deps import get_container
from refindery.api.schemas import (
    BlacklistedResponse,
    IngestAcceptedResponse,
    IngestPageRequest,
    IngestRevisitResponse,
    PageResponse,
    PageStatusResponse,
)
from refindery.application.container import Container
from refindery.application.services.ingest import IngestRequest
from refindery.domain.errors import ExtractionUnavailableError
from refindery.domain.ids import PageId
from refindery.domain.models import (
    IngestBlacklisted,
    IngestQueued,
    IngestRevisit,
    Page,
    PageStatus,
)

router = APIRouter(prefix="/v1/pages", tags=["pages"])


@router.post(
    "",
    operation_id="add_page",
    summary="Ingest a page",
    description=(
        "Add a page the user read to the index. body_extracted and body_html "
        "are mutually exclusive; when neither is given the URL is fetched "
        "asynchronously. Known canonical URLs record a revisit instead."
    ),
    responses={
        202: {"model": IngestAcceptedResponse},
        200: {"model": IngestRevisitResponse},
        403: {"model": BlacklistedResponse},
    },
)
async def add_page(
    request: IngestPageRequest,
    container: Annotated[Container, Depends(get_container)],
) -> JSONResponse:
    """Single ingest endpoint (manual adds included)."""
    try:
        with span("ingest"):
            outcome = await container.ingest.ingest(
                IngestRequest(
                    url=request.url,
                    title=request.title,
                    body_extracted=request.body_extracted,
                    body_html=request.body_html,
                    fetched_at=request.fetched_at,
                    source=request.source,
                    metadata=request.metadata,
                )
            )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except ExtractionUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc)
        ) from exc

    match outcome:
        case IngestQueued(page_id=page_id):
            ingest_pages_total.labels(outcome="queued").inc()
            accepted = IngestAcceptedResponse(page_id=page_id)
            return JSONResponse(
                status_code=status.HTTP_202_ACCEPTED,
                content=accepted.model_dump(mode="json"),
            )
        case IngestRevisit(
            page_id=page_id, status=page_status, content_hash_differs=differs
        ):
            ingest_pages_total.labels(outcome="revisit").inc()
            revisit = IngestRevisitResponse(
                page_id=page_id,
                status=page_status,
                content_hash_differs=differs,
            )
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content=revisit.model_dump(mode="json"),
            )
        case IngestBlacklisted(pattern=pattern):
            ingest_pages_total.labels(outcome="blacklisted").inc()
            blocked = BlacklistedResponse(pattern=pattern)
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content=blocked.model_dump(mode="json"),
            )


async def _require_page(container: Container, page_id: str) -> Page:
    page = await container.store.get_page(PageId(page_id))
    if page is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="page not found"
        )
    return page


@router.get(
    "/{page_id}",
    operation_id="get_page",
    summary="Fetch a page",
    description=(
        "Return the full stored body text and metadata of one page from the "
        "user's reading history."
    ),
)
async def get_page(
    page_id: str,
    container: Annotated[Container, Depends(get_container)],
) -> PageResponse:
    """Full body_text + metadata."""
    page = await _require_page(container, page_id)
    return PageResponse(
        page_id=page.id,
        canonical_url=page.canonical_url,
        original_url=page.original_url,
        domain=page.domain,
        title=page.title,
        body_text=page.body_text,
        source=page.source,
        metadata=page.metadata,  # type: ignore[arg-type]
        first_seen_at=page.first_seen_at,
        last_seen_at=page.last_seen_at,
        visit_count=page.visit_count,
        indexed_at=page.indexed_at,
        status=page.status,
    )


@router.get(
    "/{page_id}/status",
    operation_id="page_status",
    summary="Page indexing status",
    description="Return the indexing lifecycle status of a page.",
)
async def page_status(
    page_id: str,
    container: Annotated[Container, Depends(get_container)],
) -> PageStatusResponse:
    """queued|indexing|indexed|failed|dead, with last_error when failed."""
    page = await _require_page(container, page_id)
    last_error: str | None = None
    if page.status in {PageStatus.FAILED, PageStatus.DEAD}:
        jobs = await container.store.list_jobs(status=None, limit=200)
        for job in jobs:
            if job.payload.get("page_id") == page.id and job.last_error:
                last_error = job.last_error
                break
    return PageStatusResponse(
        page_id=page.id, status=page.status, last_error=last_error
    )
