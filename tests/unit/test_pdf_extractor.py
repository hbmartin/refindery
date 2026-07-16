"""PypdfExtractor: cleanup passes over committed fixture PDFs and pure helpers.

Fixtures under ``tests/fixtures/pdf`` were generated once with reportlab (a
generation-time tool, not a project dependency); see the module docstring of
the extractor for what each pass does.
"""

from pathlib import Path

import pytest

from refindery.adapters.extraction.pdf_pypdf import (
    PypdfExtractor,
    _dehyphenate,
    _normalize,
    _repeated_lines,
    _strip_page,
)
from refindery.adapters.observability.metrics import registry

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "pdf"


def _pdf(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


async def test_extracts_body_and_title_and_dehyphenates():
    result = await PypdfExtractor().extract(raw=_pdf("simple.pdf"), charset=None)
    assert result.title == "Quarterly Report"
    assert "information about the annual budget" in result.body_text
    assert "informa-" not in result.body_text


async def test_strips_repeated_headers_and_page_numbers():
    result = await PypdfExtractor().extract(
        raw=_pdf("headers_footers.pdf"), charset=None
    )
    assert "ACME CONFIDENTIAL" not in result.body_text
    assert "Page 1 of 4" not in result.body_text
    assert "Body text for section number 1 here." in result.body_text
    assert "Body text for section number 4 here." in result.body_text


async def test_stripping_can_be_disabled():
    result = await PypdfExtractor(strip_repeated_lines=False).extract(
        raw=_pdf("headers_footers.pdf"), charset=None
    )
    assert "ACME CONFIDENTIAL" in result.body_text


async def test_encrypted_empty_password_is_readable():
    result = await PypdfExtractor().extract(
        raw=_pdf("encrypted_empty.pdf"), charset=None
    )
    assert "Encrypted but readable content." in result.body_text


async def test_password_protected_pdf_raises():
    with pytest.raises(ValueError, match="password-protected"):
        await PypdfExtractor().extract(raw=_pdf("encrypted_locked.pdf"), charset=None)


async def test_scanned_pdf_yields_empty_and_counts_metric():
    name, labels = "refindery_pdf_pages_total", {"outcome": "empty"}
    before = registry.get_sample_value(name, labels) or 0.0
    result = await PypdfExtractor().extract(raw=_pdf("scanned.pdf"), charset=None)
    after = registry.get_sample_value(name, labels) or 0.0
    assert result.body_text == ""
    assert after == before + 1.0


def test_normalize_folds_ligatures_and_collapses_whitespace():
    cleaned = _normalize("The ﬁle is eﬃcient  and  neat  ")
    assert cleaned == "The file is efficient and neat"


def test_dehyphenate_joins_only_lowercase_line_breaks():
    assert _dehyphenate("co-\noperate") == "cooperate"
    assert _dehyphenate("Full-\nStop") == "Full-\nStop"


def test_repeated_lines_detects_cross_page_boilerplate():
    pages = [
        "HEADER\nunique one\nFOOTER",
        "HEADER\nunique two\nFOOTER",
        "HEADER\nunique three\nFOOTER",
    ]
    repeated = _repeated_lines(pages, scan=1, ratio=0.6)
    assert "HEADER" in repeated
    assert "FOOTER" in repeated
    assert "unique one" not in repeated


def test_strip_page_removes_boilerplate_and_zone_page_numbers():
    page = "HEADER\nreal content here\n12"
    out = _strip_page(page, repeated=frozenset({"HEADER"}), scan=1)
    assert out == "real content here"
