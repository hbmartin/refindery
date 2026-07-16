"""Deterministic Chunker fake: fixed-width windows, whitespace-only dropped.

Lets chunking-dependent tests run without chonkie's network-fetched tokenizer.
"""

from refindery.domain.ids import PageId, new_chunk_id
from refindery.domain.models import Chunk


class FixedChunker:
    """Splits text into fixed-width character windows with exact offsets."""

    def __init__(self, size: int = 20) -> None:
        self._size = size

    def chunk(self, *, page_id: PageId, text: str) -> list[Chunk]:
        chunks: list[Chunk] = []
        ordinal = 0
        for start in range(0, len(text), self._size):
            piece = text[start : start + self._size]
            if not piece.strip():
                continue
            chunks.append(
                Chunk(
                    id=new_chunk_id(),
                    page_id=page_id,
                    ordinal=ordinal,
                    text=piece,
                    token_count=len(piece.split()),
                    char_start=start,
                    char_end=start + len(piece),
                )
            )
            ordinal += 1
        return chunks
