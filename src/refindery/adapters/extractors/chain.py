"""Chain extractor: first healthy adapter wins, per-call fall-through."""

import logging
from inspect import isawaitable

from refindery.application.ports.entity_extractor import EntityExtractor
from refindery.domain.models import Mention

logger = logging.getLogger(__name__)


class ChainExtractor:
    """Ordered fall-through over the configured extractor chain."""

    def __init__(self, extractors: list[EntityExtractor]) -> None:
        self._extractors = extractors

    def health_check(self) -> bool:
        """Healthy when any link is."""
        return any(e.health_check() for e in self._extractors)

    async def extract(self, text: str) -> list[Mention]:
        """First healthy extractor's output; exceptions fall through."""
        for extractor in self._extractors:
            if not extractor.health_check():
                continue
            try:
                return await extractor.extract(text)
            except Exception:  # noqa: BLE001 — fall through to the next link
                logger.warning(
                    "extractor %s failed; falling through",
                    type(extractor).__name__,
                    exc_info=True,
                )
        return []

    async def aclose(self) -> None:
        """Close links that expose close/aclose hooks."""
        for extractor in self._extractors:
            aclose = getattr(extractor, "aclose", None)
            if callable(aclose):
                result = aclose()
                if isawaitable(result):
                    await result
                continue
            close = getattr(extractor, "close", None)
            if callable(close):
                close()
