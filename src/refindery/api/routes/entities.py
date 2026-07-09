"""Entity endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from refindery.api.deps import get_container
from refindery.api.schemas import (
    EntityDetailResponse,
    EntitySummary,
    PageEntitiesResponse,
    UndoMergeResponse,
)
from refindery.application.container import Container
from refindery.domain.entities import Entity
from refindery.domain.ids import PageId

router = APIRouter(prefix="/v1", tags=["entities"])


def _summary(entity: Entity) -> EntitySummary:
    return EntitySummary(
        id=entity.id,
        canonical_form=entity.canonical_form,
        type=entity.type,
        mention_count=entity.mention_count,
        page_count=entity.page_count,
        idf=entity.idf,
    )


@router.get(
    "/entities/{ref}",
    operation_id="entities",
    summary="Look up an entity",
    description=(
        "Resolve an entity by id, canonical form, or alias (references "
        "survive merges). Returns aliases and the user's pages mentioning "
        "it — grounded in the reading history only."
    ),
)
async def get_entity(
    ref: str,
    container: Annotated[Container, Depends(get_container)],
) -> EntityDetailResponse:
    """Entity detail: canonical form, aliases, pages."""
    entity = await container.store.resolve_entity(ref)
    if entity is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="entity not found"
        )
    aliases = await container.store.entity_aliases(entity.id)
    page_ids = await container.store.page_ids_for_entity(entity.id)
    return EntityDetailResponse(
        entity=_summary(entity),
        aliases=aliases,
        page_ids=list(page_ids),
    )


@router.get(
    "/pages/{page_id}/entities",
    operation_id="page_entities",
    summary="Entities mentioned on a page",
)
async def page_entities(
    page_id: str,
    container: Annotated[Container, Depends(get_container)],
) -> PageEntitiesResponse:
    """Entities linked to one page."""
    if await container.store.get_page(PageId(page_id)) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="page not found"
        )
    entities = await container.store.entities_for_page(PageId(page_id))
    return PageEntitiesResponse(
        page_id=page_id, entities=[_summary(e) for e in entities]
    )


@router.post(
    "/entities/merges/{merge_id}/undo",
    operation_id="undo_entity_merge",
    summary="Undo an entity merge (LIFO only)",
)
async def undo_merge(
    merge_id: str,
    container: Annotated[Container, Depends(get_container)],
) -> UndoMergeResponse:
    """Restore a merged entity from its snapshot."""
    try:
        restored = await container.store.undo_merge(merge_id, now=container.clock.now())
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    return UndoMergeResponse(restored_entity_id=restored)
