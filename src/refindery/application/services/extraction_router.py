"""Routes fetched content to the extractor registered for its content type."""

from refindery.application.ports.content_extractor import ContentExtractor
from refindery.domain.errors import UnsupportedContentTypeError
from refindery.domain.models import ExtractedContent


class ExtractionRouter:
    """Content-type -> ContentExtractor dispatch; extensible by registration."""

    def __init__(self, extractors: list[ContentExtractor]) -> None:
        self._by_type: dict[str, ContentExtractor] = {}
        for extractor in extractors:
            for content_type in extractor.content_types:
                self._by_type[content_type] = extractor

    def supports(self, content_type: str) -> bool:
        """Whether any extractor handles this content type."""
        return content_type in self._by_type

    async def extract(
        self, *, content_type: str, raw: bytes, charset: str | None
    ) -> ExtractedContent:
        """Extract body text using the matching extractor."""
        if (extractor := self._by_type.get(content_type)) is None:
            raise UnsupportedContentTypeError(content_type)
        return await extractor.extract(raw=raw, charset=charset)

    def close(self) -> None:
        """Close extractor resources that expose a close method."""
        seen: set[int] = set()
        for extractor in self._by_type.values():
            if id(extractor) in seen:
                continue
            seen.add(id(extractor))
            close = getattr(extractor, "close", None)
            if callable(close):
                close()
