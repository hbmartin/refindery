"""Keyword-spotting fake entity extractor."""

from refindery.domain.models import Mention


class FakeEntityExtractor:
    """Emits a mention wherever a configured keyword appears."""

    def __init__(self, keywords: dict[str, str]) -> None:
        self._keywords = keywords  # surface form -> entity type

    def health_check(self) -> bool:
        """Report always healthy."""
        return True

    async def extract(self, text: str) -> list[Mention]:
        """Scan for configured keywords (case-insensitive)."""
        lowered = text.lower()
        mentions: list[Mention] = []
        for surface, entity_type in self._keywords.items():
            start = lowered.find(surface.lower())
            if start >= 0:
                mentions.append(
                    Mention(
                        surface_form=text[start : start + len(surface)],
                        type=entity_type,
                        char_start=start,
                        char_end=start + len(surface),
                    )
                )
        return mentions
