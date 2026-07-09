"""Entity extraction job: extract entities from an indexed page and link them."""

import logging
from inspect import isawaitable

from refindery.application.ports.entity_extractor import EntityExtractor
from refindery.application.ports.metadata_store import MetadataStore
from refindery.application.services.canonicalization import CanonicalizationService
from refindery.domain.errors import PageNotFoundError
from refindery.domain.ids import PageId
from refindery.domain.models import Job, PageStatus

logger = logging.getLogger(__name__)


class EntityIngestService:
    """Steps 4+5 of the indexing pipeline (extract + incremental canon)."""

    def __init__(
        self,
        *,
        store: MetadataStore,
        extractor: EntityExtractor,
        canonicalization: CanonicalizationService,
    ) -> None:
        self._store = store
        self._extractor = extractor
        self._canonicalization = canonicalization

    async def handle_extract_entities_job(self, job: Job) -> None:
        """Extract and link entities for an already-indexed page."""
        page_id = PageId(job.payload["page_id"])
        page = await self._store.get_page(page_id)
        if page is None:
            raise PageNotFoundError(page_id)
        if page.status is not PageStatus.INDEXED:
            logger.info(
                "skipping entity extraction for page %s in status %s",
                page.id,
                page.status,
            )
            return
        expected_hash = job.payload.get("content_hash")
        if expected_hash is not None and page.content_hash != expected_hash:
            logger.info(
                "skipping stale entity extraction for page %s: %s != %s",
                page.id,
                expected_hash,
                page.content_hash,
            )
            return
        if not self._extractor.health_check():
            msg = "configured entity extractor became unhealthy"
            raise RuntimeError(msg)
        if page.body_text is None:
            return
        mentions = await self._extractor.extract(page.body_text)
        if mentions:
            await self._canonicalization.link_mentions(
                page_id=page.id, mentions=mentions
            )
        logger.debug("page %s: %d mentions", page.id, len(mentions))

    async def close(self) -> None:
        """Release extractor resources when present."""
        aclose = getattr(self._extractor, "aclose", None)
        if callable(aclose):
            result = aclose()
            if isawaitable(result):
                await result
            return
        close = getattr(self._extractor, "close", None)
        if callable(close):
            close()
