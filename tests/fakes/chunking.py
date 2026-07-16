"""Chunker fake: whole-text single chunk, no tokenizer download."""

from refindery.domain.ids import PageId, new_chunk_id
from refindery.domain.models import Chunk


class FakeChunker:
    """Emits one chunk per non-empty page; avoids the real cl100k tokenizer."""

    def chunk(self, *, page_id: PageId, text: str) -> list[Chunk]:
        """Return a single chunk spanning the whole text."""
        if not text:
            return []
        return [
            Chunk(
                id=new_chunk_id(),
                page_id=page_id,
                ordinal=0,
                text=text,
                token_count=max(1, len(text.split())),
                char_start=0,
                char_end=len(text),
            )
        ]
