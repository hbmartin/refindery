"""PDF text extraction via pypdf (pure Python, BSD).

Beyond pypdf's raw text layer this applies cleanup passes that matter for
downstream chunking and embedding: de-hyphenation of line-break splits, Unicode
(NFKC) and whitespace normalization, and removal of running headers/footers and
page-number lines that would otherwise repeat into every chunk. Encrypted PDFs
are opened with an empty password; image-only (scanned) PDFs have no text layer
and are surfaced via a metric and a warning rather than silently indexed empty.

pypdf does not do column or table detection, so genuinely multi-column and
tabular layouts are out of scope here -- that belongs to a richer, optional
extraction engine.
"""

import asyncio
import io
import logging
import math
import re
import unicodedata
from collections import Counter

from pypdf import PdfReader

from refindery.adapters.observability.metrics import pdf_pages_total
from refindery.domain.models import ExtractedContent

logger = logging.getLogger(__name__)

_HYPHEN_BREAK = re.compile(r"(?<=[a-z])-\n(?=[a-z])")
_TRAILING_WS = re.compile(r"[^\S\n]+\n")
_SPACE_RUN = re.compile(r"[^\S\n]{2,}")
_BLANK_RUNS = re.compile(r"\n{3,}")
_PAGE_NUMBER_LINE = re.compile(
    r"^(?:page\s+)?\d+(?:\s*(?:/|of)\s*\d+)?$", re.IGNORECASE
)


def _extract_pages(reader: PdfReader) -> list[str]:
    """Extract each page's text layer (empty string when a page has none)."""
    return [page.extract_text() or "" for page in reader.pages]


def _ensure_decrypted(reader: PdfReader) -> None:
    """Open an empty-password encrypted PDF; raise if a password is required."""
    if not reader.is_encrypted:
        return
    if not reader.decrypt(""):
        msg = "PDF is password-protected; cannot extract text"
        raise ValueError(msg)


def _record_page_metrics(pages: list[str]) -> None:
    """Count text-bearing vs empty (scanned/image-only) pages."""
    if text_pages := sum(1 for page in pages if page.strip()):
        pdf_pages_total.labels(outcome="text").inc(text_pages)
    if empty_pages := len(pages) - text_pages:
        pdf_pages_total.labels(outcome="empty").inc(empty_pages)


def _repeated_lines(pages: list[str], *, scan: int, ratio: float) -> frozenset[str]:
    """Return stripped lines recurring in the top/bottom ``scan`` lines of pages."""
    counts: Counter[str] = Counter()
    for page in pages:
        nonempty = [line.strip() for line in page.split("\n") if line.strip()]
        tail = nonempty[-scan:] if scan > 0 else []
        counts.update({*nonempty[:scan], *tail})
    threshold = max(2, math.ceil(ratio * len(pages)))
    return frozenset(line for line, count in counts.items() if count >= threshold)


def _strip_page(page: str, *, repeated: frozenset[str], scan: int) -> str:
    """Drop repeated header/footer lines and zone-local page-number lines."""
    lines = page.split("\n")
    nonempty_idx = [i for i, line in enumerate(lines) if line.strip()]
    zone = {*nonempty_idx[:scan], *(nonempty_idx[-scan:] if scan > 0 else [])}
    kept: list[str] = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped and stripped in repeated:
            continue
        if i in zone and stripped and _PAGE_NUMBER_LINE.match(stripped):
            continue
        kept.append(line)
    return "\n".join(kept)


def _dehyphenate(text: str) -> str:
    """Rejoin words split by a hyphen at a line break (lowercase-only)."""
    return _HYPHEN_BREAK.sub("", text)


def _normalize(text: str) -> str:
    """NFKC-normalize and tidy whitespace for stable downstream tokens."""
    text = unicodedata.normalize("NFKC", text)
    text = _TRAILING_WS.sub("\n", text)
    text = _SPACE_RUN.sub(" ", text)
    return _BLANK_RUNS.sub("\n\n", text).strip()


def _title(reader: PdfReader) -> str | None:
    """PDF document title from metadata, when present and non-empty."""
    meta = reader.metadata
    if meta is not None and meta.title:
        return str(meta.title)
    return None


class PypdfExtractor:
    """ContentExtractor for application/pdf."""

    def __init__(
        self,
        *,
        strip_repeated_lines: bool = True,
        repeated_line_ratio: float = 0.6,
        repeated_line_scan: int = 2,
        min_pages_for_stripping: int = 3,
    ) -> None:
        self._strip = strip_repeated_lines
        self._ratio = repeated_line_ratio
        self._scan = repeated_line_scan
        self._min_pages = min_pages_for_stripping

    @property
    def content_types(self) -> frozenset[str]:
        """Handled content types."""
        return frozenset({"application/pdf"})

    async def extract(self, *, raw: bytes, charset: str | None) -> ExtractedContent:  # noqa: ARG002 — port signature; PDFs carry their own encoding
        """Extract cleaned body text; title from PDF metadata when present."""

        def _extract() -> ExtractedContent:
            reader = PdfReader(io.BytesIO(raw))
            _ensure_decrypted(reader)
            pages = _extract_pages(reader)
            _record_page_metrics(pages)
            if self._strip and len(pages) >= self._min_pages and self._scan > 0:
                repeated = _repeated_lines(pages, scan=self._scan, ratio=self._ratio)
                pages = [
                    _strip_page(page, repeated=repeated, scan=self._scan)
                    for page in pages
                ]
            body = _normalize(_dehyphenate("\n\n".join(p for p in pages if p.strip())))
            if pages and not body:
                logger.warning(
                    "pdf produced no extractable text across %d page(s); "
                    "likely a scanned or image-only document",
                    len(pages),
                )
            return ExtractedContent(body_text=body, title=_title(reader))

        return await asyncio.to_thread(_extract)
