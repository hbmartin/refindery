"""Tests for the chonkie chunker adapter."""

from refindery.adapters.chunking.chonkie_chunker import ChonkieChunker
from refindery.domain.ids import PageId

PAGE = PageId("page-1")


def _chunker(**kwargs) -> ChonkieChunker:
    defaults = {"target_tokens": 100, "overlap_tokens": 20, "hard_max_tokens": 120}
    return ChonkieChunker(**{**defaults, **kwargs})


def test_empty_and_whitespace_text_yield_no_chunks():
    assert _chunker().chunk(page_id=PAGE, text="") == []
    assert _chunker().chunk(page_id=PAGE, text="   \n\t ") == []


def test_short_text_single_chunk():
    chunks = _chunker().chunk(page_id=PAGE, text="One short sentence.")
    assert len(chunks) == 1
    assert chunks[0].ordinal == 0
    assert chunks[0].page_id == PAGE


def test_offsets_roundtrip_into_source():
    text = ("The quick brown fox jumps over the lazy dog. " * 40).strip()
    chunks = _chunker().chunk(page_id=PAGE, text=text)
    assert len(chunks) > 1
    for chunk in chunks:
        assert text[chunk.char_start : chunk.char_end] == chunk.text


def test_ordinals_sequential():
    text = "A sentence here. " * 100
    chunks = _chunker().chunk(page_id=PAGE, text=text)
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))


def test_token_budget_respected():
    text = "Words in a normal sentence keep flowing onward. " * 80
    chunks = _chunker().chunk(page_id=PAGE, text=text)
    assert all(c.token_count <= 120 for c in chunks)


def test_hard_max_enforced_on_pathological_input():
    # One giant "sentence" with no delimiters anywhere.
    text = "token " * 2_000
    chunks = _chunker().chunk(page_id=PAGE, text=text.strip())
    assert all(c.token_count <= 120 for c in chunks)
    for chunk in chunks:
        assert text[chunk.char_start : chunk.char_end] == chunk.text


def test_overlap_between_consecutive_chunks():
    text = "The quick brown fox jumps over the lazy dog. " * 40
    chunks = _chunker().chunk(page_id=PAGE, text=text)
    assert len(chunks) > 1
    # overlapping window: next chunk starts before this one ends
    assert chunks[1].char_start < chunks[0].char_end
