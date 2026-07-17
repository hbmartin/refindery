"""Graph projection job: build the derived entity graph from the store.

Runs as the ``GRAPH_PROJECT`` job, isolated from indexing so a projection
failure never breaks the ingest/entity pipeline. Two modes:

- ``page`` — incrementally (re)project one page's ``MENTIONS`` (a clean per-page
  rewrite, so re-projection is idempotent).
- ``rebuild`` — reset and reproject every indexed page, then recompute
  ``CO_OCCURS`` in-graph. This is the source of truth; it runs after periodic
  re-canonicalization (which merges entities and changes their ids).
"""

import logging

from refindery.application.ports.graph_store import (
    EntityRef,
    GraphStore,
    PageProjection,
)
from refindery.application.ports.metadata_store import MetadataStore
from refindery.domain.ids import PageId
from refindery.domain.models import Job, PageStatus

logger = logging.getLogger(__name__)

_REBUILD_BATCH = 200


class GraphProjectionService:
    """Handles ``GRAPH_PROJECT`` jobs: per-page projection and full rebuild."""

    def __init__(self, *, store: MetadataStore, graph_store: GraphStore) -> None:
        self._store = store
        self._graph_store = graph_store

    async def handle_job(self, job: Job) -> None:
        """Project one page (``mode=page``) or rebuild all (``mode=rebuild``)."""
        match job.payload.get("mode", "page"):
            case "rebuild":
                await self._rebuild()
            case _:
                await self._project_page(PageId(job.payload["page_id"]))

    async def _project_page(self, page_id: PageId) -> None:
        if (projection := await self._build_projection(page_id)) is not None:
            await self._graph_store.project_page(projection)

    async def _rebuild(self) -> None:
        await self._graph_store.reset()
        cursor: PageId | None = None
        projected = 0
        while page_ids := await self._store.pages_with_chunks_after(
            cursor=cursor, limit=_REBUILD_BATCH
        ):
            for page_id in page_ids:
                if (proj := await self._build_projection(page_id)) is not None:
                    await self._graph_store.project_page(proj)
                    projected += 1
            cursor = page_ids[-1]
        await self._graph_store.rebuild_co_occurrence()
        logger.info("graph rebuild: projected %d pages", projected)

    async def _build_projection(self, page_id: PageId) -> PageProjection | None:
        page = await self._store.get_page(page_id)
        if page is None or page.status is not PageStatus.INDEXED:
            return None
        entities = await self._store.entities_for_page(page_id)
        counts = await self._store.mention_counts_for_page(page_id)
        refs = tuple(
            EntityRef(
                id=entity.id,
                canonical_form=entity.canonical_form,
                type=entity.type,
                idf=entity.idf if entity.idf is not None else 1.0,
                count=counts.get(entity.id, 1),
            )
            for entity in entities
        )
        return PageProjection(
            page_id=page.id,
            domain=page.domain,
            first_seen_at=page.first_seen_at,
            entities=refs,
        )
