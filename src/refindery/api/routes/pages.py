"""Page ingest and read endpoints."""

from typing import Annotated, assert_never

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from refindery.adapters.observability.metrics import ingest_pages_total
from refindery.adapters.observability.otel import span
from refindery.api.auth import require_write
from refindery.api.deps import get_container
from refindery.api.schemas import (
    BlacklistedResponse,
    FeatureStatus,
    IngestAcceptedResponse,
    IngestBatchAcceptedResult,
    IngestBatchBlacklistedResult,
    IngestBatchRejectedResult,
    IngestBatchRequest,
    IngestBatchResponse,
    IngestBatchRevisitResult,
    IngestPageRequest,
    IngestRevisitResponse,
    PageChunkResponse,
    PageChunksResponse,
    PageResponse,
    PageStatusBatchFoundResult,
    PageStatusBatchMissingResult,
    PageStatusBatchRequest,
    PageStatusBatchResponse,
    PageStatusFeatures,
    PageStatusResponse,
)
from refindery.application.container import Container
from refindery.application.services.ingest import IngestRequest
from refindery.domain.errors import BodyConflictError, ExtractionUnavailableError
from refindery.domain.ids import PageId
from refindery.domain.models import (
    IngestBlacklisted,
    IngestOutcome,
    IngestQueued,
    IngestRevisit,
    JobKind,
    Page,
    PageStatus,
)

router = APIRouter(prefix="/v1/pages", tags=["pages"])


async def _ingest_page(
    request: IngestPageRequest, container: Container
) -> IngestOutcome:
    """Map a validated API request to the shared ingest service."""
    return await container.ingest.ingest(
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


@router.post(
    "",
    operation_id="add_page",
    dependencies=[Depends(require_write)],
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
            outcome = await _ingest_page(request, container)
    except BodyConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
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
        case _ as unreachable:  # pragma: no cover — statically unreachable
            assert_never(unreachable)


@router.post(
    "/batch",
    operation_id="add_pages_batch",
    dependencies=[Depends(require_write)],
    summary="Ingest up to 100 pages",
    description=(
        "Validate and ingest each page independently. Results preserve input "
        "order; malformed or blacklisted items do not prevent other items."
    ),
)
async def add_pages_batch(
    request: IngestBatchRequest,
    container: Annotated[Container, Depends(get_container)],
) -> IngestBatchResponse:
    """Batch ingest with per-item outcomes and deterministic duplicate handling."""
    results: list[
        IngestBatchAcceptedResult
        | IngestBatchRevisitResult
        | IngestBatchBlacklistedResult
        | IngestBatchRejectedResult
    ] = []
    for index, item in enumerate(request.pages):
        try:
            page_request = IngestPageRequest.model_validate(item)
            with span("ingest.batch.item"):
                outcome = await _ingest_page(page_request, container)
        except (ValidationError, BodyConflictError, ValueError) as exc:
            results.append(IngestBatchRejectedResult(index=index, detail=str(exc)))
            continue
        except ExtractionUnavailableError as exc:
            results.append(IngestBatchRejectedResult(index=index, detail=str(exc)))
            continue

        match outcome:
            case IngestQueued(page_id=page_id):
                ingest_pages_total.labels(outcome="queued").inc()
                results.append(IngestBatchAcceptedResult(index=index, page_id=page_id))
            case IngestRevisit(
                page_id=page_id,
                status=page_status,
                content_hash_differs=differs,
            ):
                ingest_pages_total.labels(outcome="revisit").inc()
                results.append(
                    IngestBatchRevisitResult(
                        index=index,
                        page_id=page_id,
                        status=page_status,
                        content_hash_differs=differs,
                    )
                )
            case IngestBlacklisted(pattern=pattern):
                ingest_pages_total.labels(outcome="blacklisted").inc()
                results.append(
                    IngestBatchBlacklistedResult(index=index, pattern=pattern)
                )
            case _ as unreachable:  # pragma: no cover — statically unreachable
                assert_never(unreachable)
    return IngestBatchResponse(results=results)


async def _page_status(page: Page, container: Container) -> PageStatusResponse:
    """Build the shared status representation for a known page."""
    last_error: str | None = None
    if page.status in {PageStatus.FAILED, PageStatus.DEAD}:
        job = await container.store.latest_job_for_page(page_id=page.id)
        last_error = None if job is None else job.last_error
    entity_job = await container.store.latest_job_for_page(
        page_id=page.id, kind=JobKind.EXTRACT_ENTITIES
    )
    entities = (
        FeatureStatus(status="not_queued", last_error=None)
        if entity_job is None
        else FeatureStatus(status=entity_job.status, last_error=entity_job.last_error)
    )
    return PageStatusResponse(
        page_id=page.id,
        status=page.status,
        last_error=last_error,
        features=PageStatusFeatures(entities=entities),
    )


@router.post(
    "/status/batch",
    operation_id="page_status_batch",
    summary="Fetch status for up to 500 pages",
    description=(
        "Return one status per distinct requested page id. Unknown ids are "
        "reported with found=false instead of failing the batch."
    ),
)
async def page_status_batch(
    request: PageStatusBatchRequest,
    container: Annotated[Container, Depends(get_container)],
) -> PageStatusBatchResponse:
    """Batch lifecycle status, deduplicating ids while preserving order."""
    results: list[PageStatusBatchFoundResult | PageStatusBatchMissingResult] = []
    for page_id in dict.fromkeys(request.page_ids):
        page = await container.store.get_page(PageId(page_id))
        if page is None:
            results.append(PageStatusBatchMissingResult(page_id=page_id))
            continue
        current = await _page_status(page, container)
        results.append(
            PageStatusBatchFoundResult(
                page_id=current.page_id,
                status=current.status,
                last_error=current.last_error,
                features=current.features,
            )
        )
    return PageStatusBatchResponse(results=results)


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
        metadata=page.metadata,  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
        first_seen_at=page.first_seen_at,
        last_seen_at=page.last_seen_at,
        visit_count=page.visit_count,
        indexed_at=page.indexed_at,
        status=page.status,
    )


@router.get(
    "/{page_id}/chunks",
    operation_id="get_page_chunks",
    summary="List a page's indexed chunks",
)
async def get_page_chunks(
    page_id: str,
    container: Annotated[Container, Depends(get_container)],
) -> PageChunksResponse:
    """Return chunk text and body offsets in ordinal order."""
    await _require_page(container, page_id)
    chunks = await container.store.chunks_for_page(PageId(page_id))
    return PageChunksResponse(
        page_id=page_id,
        chunks=[
            PageChunkResponse(
                chunk_id=chunk.id,
                ordinal=chunk.ordinal,
                text=chunk.text,
                token_count=chunk.token_count,
                char_start=chunk.char_start,
                char_end=chunk.char_end,
            )
            for chunk in chunks
        ],
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
    return await _page_status(page, container)
