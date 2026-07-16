"""Chapter-aware chunking: sections become hard boundaries, titles ride along."""

from refindery.application.services.chapter_chunking import chunk_with_sections
from refindery.domain.ids import PageId
from refindery.domain.models import Section
from tests.fakes.chunking import FixedChunker as _FixedChunker

_PAGE = PageId("page-1")


def _sections(text: str) -> tuple[Section, ...]:
    # "AAAA...(40) | BBBB...(30) | CCCC...(30)" split into three titled chapters.
    return (
        Section(title="Intro", char_start=0, char_end=40, start_time_s=0.0),
        Section(title="Body", char_start=40, char_end=70, start_time_s=120.0),
        Section(title="Outro", char_start=70, char_end=len(text), start_time_s=300.0),
    )


def test_no_sections_delegates_to_flat_chunker():
    text = "abcdefghij" * 6
    flat = _FixedChunker().chunk(page_id=_PAGE, text=text)
    result = chunk_with_sections(
        _FixedChunker(), page_id=_PAGE, text=text, sections=None
    )
    assert [c.text for c in result] == [c.text for c in flat]
    assert all(c.section_title is None for c in result)


def test_empty_sections_delegates_to_flat_chunker():
    text = "abcdefghij" * 6
    result = chunk_with_sections(_FixedChunker(), page_id=_PAGE, text=text, sections=())
    assert [c.text for c in result] == [
        c.text for c in _FixedChunker().chunk(page_id=_PAGE, text=text)
    ]


def test_no_chunk_crosses_a_section_boundary():
    text = "".join(ch * 10 for ch in "ABCDEFGHIJ")  # 100 chars
    sections = _sections(text)
    chunks = chunk_with_sections(
        _FixedChunker(size=12), page_id=_PAGE, text=text, sections=sections
    )
    for chunk in chunks:
        owning = [s for s in sections if s.char_start <= chunk.char_start < s.char_end]
        assert len(owning) == 1, chunk
        assert chunk.char_end <= owning[0].char_end


def test_global_ordinal_is_contiguous_and_increasing():
    text = "".join(ch * 10 for ch in "ABCDEFGHIJ")
    chunks = chunk_with_sections(
        _FixedChunker(size=12), page_id=_PAGE, text=text, sections=_sections(text)
    )
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))


def test_titles_are_prepended_but_offsets_index_the_body():
    text = "".join(ch * 10 for ch in "ABCDEFGHIJ")
    sections = _sections(text)
    chunks = chunk_with_sections(
        _FixedChunker(size=12), page_id=_PAGE, text=text, sections=sections
    )
    for chunk in chunks:
        assert chunk.section_title in {"Intro", "Body", "Outro"}
        prefix = f"{chunk.section_title}\n\n"
        assert chunk.text.startswith(prefix)
        # char offsets still index the raw body, not the prefixed chunk text.
        body = text[chunk.char_start : chunk.char_end]
        assert chunk.text.removeprefix(prefix) == body


def test_prepend_disabled_leaves_text_untouched():
    text = "".join(ch * 10 for ch in "ABCDEFGHIJ")
    chunks = chunk_with_sections(
        _FixedChunker(size=12),
        page_id=_PAGE,
        text=text,
        sections=_sections(text),
        prepend_titles=False,
    )
    for chunk in chunks:
        assert chunk.text == text[chunk.char_start : chunk.char_end]
        assert chunk.section_title is not None


def test_untitled_section_carries_no_prefix_and_null_title():
    text = "abcdefghij" * 5  # 50 chars
    sections = (
        Section(title=None, char_start=0, char_end=20, start_time_s=None),
        Section(title="Named", char_start=20, char_end=50, start_time_s=42.0),
    )
    chunks = chunk_with_sections(
        _FixedChunker(size=12), page_id=_PAGE, text=text, sections=sections
    )
    leading = [c for c in chunks if c.char_start < 20]
    assert leading
    for chunk in leading:
        assert chunk.section_title is None
        assert chunk.section_start_s is None
        assert chunk.text == text[chunk.char_start : chunk.char_end]
    named = [c for c in chunks if c.char_start >= 20]
    assert all(c.section_start_s == 42.0 for c in named)
