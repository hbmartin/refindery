"""Forget (purge + blacklist) and blacklist management endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from refindery.api.auth import require_write
from refindery.api.deps import get_container
from refindery.api.schemas import (
    BlacklistEntry,
    BlacklistResponse,
    ForgetRequest,
    ForgetResponse,
)
from refindery.application.container import Container
from refindery.domain.models import BlacklistRule

router = APIRouter(prefix="/v1", tags=["forget"])


def _entry(rule: BlacklistRule) -> BlacklistEntry:
    return BlacklistEntry(
        id=rule.id,
        pattern=rule.pattern,
        kind=rule.kind,
        reason=rule.reason,
        created_at=rule.created_at,
    )


@router.post(
    "/forget",
    operation_id="forget",
    dependencies=[Depends(require_write)],
    summary="Purge and blacklist a URL or domain",
    description=(
        "Permanently remove matching pages from the index and blacklist the "
        "pattern so future ingests are rejected. This deletes user data; "
        "removal of the blacklist rule later does not restore content."
    ),
)
async def forget(
    request: ForgetRequest,
    container: Annotated[Container, Depends(get_container)],
) -> ForgetResponse:
    """Purge + blacklist, atomically."""
    try:
        outcome = await container.forget.forget(
            url=request.url, domain=request.domain, reason=request.reason
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    return ForgetResponse(
        blacklist_id=outcome.rule.id,
        pattern=outcome.rule.pattern,
        kind=outcome.rule.kind,
        pages_purged=outcome.pages_purged,
        vector_deletes_queued=outcome.vector_deletes_queued,
    )


@router.get("/blacklist", operation_id="list_blacklist", summary="List blacklist rules")
async def list_blacklist(
    container: Annotated[Container, Depends(get_container)],
) -> BlacklistResponse:
    """All rules, newest first."""
    rules = await container.store.list_blacklist()
    return BlacklistResponse(entries=[_entry(rule) for rule in rules])


@router.delete(
    "/blacklist/{blacklist_id}",
    operation_id="unblacklist",
    dependencies=[Depends(require_write)],
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a blacklist rule",
    description="Future ingests are allowed again; purged content stays purged.",
)
async def delete_blacklist(
    blacklist_id: str,
    container: Annotated[Container, Depends(get_container)],
) -> None:
    """Un-blacklist (does not restore content)."""
    if not await container.store.delete_blacklist(blacklist_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="rule not found"
        )
