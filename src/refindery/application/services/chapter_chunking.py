"""Chapter-aware chunking: split within section boundaries, never across them.

Wraps any :class:`Chunker` so podcast chapters (or any titled section spans)
become hard chunk boundaries: each section is chunked independently, chunks
carry their section title/timestamp, and the chapter title is prepended into
the chunk text so it is searchable without a schema change. The underlying
``Chunker`` port is deliberately untouched — this is pure orchestration.
"""

from dataclasses import replace

from refindery.application.ports.chunker import Chunker
from refindery.domain.ids import PageId
from refindery.domain.models import Chunk, Section


def chunk_with_sections(
    chunker: Chunker,
    *,
    page_id: PageId,
    text: str,
    sections: tuple[Section, ...] | None,
    prepend_titles: bool = True,
) -> list[Chunk]:
    """Chunk ``text`` within each section span, preserving a global ordinal.

    With no sections, delegates to the flat ``chunker.chunk``. Otherwise every
    section is chunked on its own slice (so no chunk crosses a boundary), char
    offsets are shifted back onto ``text``, and each chunk is labelled with its
    section title/timestamp. When ``prepend_titles`` is set and the section has
    a title, the title is prepended to the chunk text (so ``chunk.text`` is no
    longer byte-equal to ``text[char_start:char_end]`` — by design; no consumer
    relies on that equality).
    """
    if not sections:
        return chunker.chunk(page_id=page_id, text=text)
    chunks: list[Chunk] = []
    ordinal = 0
    for section in sections:
        sub_text = text[section.char_start : section.char_end]
        for piece in chunker.chunk(page_id=page_id, text=sub_text):
            body = (
                f"{section.title}\n\n{piece.text}"
                if prepend_titles and section.title
                else piece.text
            )
            chunks.append(
                replace(
                    piece,
                    ordinal=ordinal,
                    text=body,
                    char_start=section.char_start + piece.char_start,
                    char_end=section.char_start + piece.char_end,
                    section_title=section.title,
                    section_start_s=section.start_time_s,
                )
            )
            ordinal += 1
    return chunks
