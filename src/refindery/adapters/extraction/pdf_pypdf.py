"""PDF text extraction via pypdf (pure Python, BSD)."""

import asyncio
import io

from pypdf import PdfReader

from refindery.domain.models import ExtractedContent


class PypdfExtractor:
    """ContentExtractor for application/pdf."""

    @property
    def content_types(self) -> frozenset[str]:
        """Handled content types."""
        return frozenset({"application/pdf"})

    async def extract(self, *, raw: bytes, charset: str | None) -> ExtractedContent:  # noqa: ARG002 — port signature; PDFs carry their own encoding
        """Extract text from all pages; title from PDF metadata when present."""

        def _extract() -> ExtractedContent:
            reader = PdfReader(io.BytesIO(raw))
            pages = [page.extract_text() or "" for page in reader.pages]
            title = None
            if reader.metadata is not None and reader.metadata.title:
                title = str(reader.metadata.title)
            return ExtractedContent(
                body_text="\n\n".join(p for p in pages if p.strip()), title=title
            )

        return await asyncio.to_thread(_extract)
