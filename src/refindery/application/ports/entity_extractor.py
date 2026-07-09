"""Entity extraction port (implemented in M4)."""

from typing import Protocol

from refindery.domain.models import Mention


class EntityExtractor(Protocol):
    """Extracts typed entity mentions from page text."""

    def health_check(self) -> bool:
        """Run a cheap canary check that this extractor produces sane output."""
        ...

    async def extract(self, text: str) -> list[Mention]:
        """Extract mentions with character offsets."""
        ...
