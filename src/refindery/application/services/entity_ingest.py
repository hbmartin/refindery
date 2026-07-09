"""Pipeline hook: extract entities from a freshly indexed page and link them."""

import logging

from refindery.application.ports.entity_extractor import EntityExtractor
from refindery.application.services.canonicalization import CanonicalizationService
from refindery.domain.models import Chunk, Page

logger = logging.getLogger(__name__)


class EntityIngestService:
    """Steps 4+5 of the indexing pipeline (extract + incremental canon)."""

    def __init__(
        self,
        *,
        extractor: EntityExtractor | None,
        canonicalization: CanonicalizationService,
    ) -> None:
        self._extractor = extractor
        self._canonicalization = canonicalization

    async def on_page_indexed(self, page: Page, _chunks: list[Chunk]) -> None:
        """IndexingService hook."""
        if self._extractor is None or not self._extractor.health_check():
            return
        if page.body_text is None:
            return
        mentions = await self._extractor.extract(page.body_text)
        if mentions:
            await self._canonicalization.link_mentions(
                page_id=page.id, mentions=mentions
            )
        logger.debug("page %s: %d mentions", page.id, len(mentions))
