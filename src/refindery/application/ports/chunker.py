"""Chunking port: canonical, model-independent, sentence-aware chunking."""

from typing import Protocol

from refindery.domain.ids import PageId
from refindery.domain.models import Chunk


class Chunker(Protocol):
    """Splits page body text into canonical chunks.

    Pure CPU work — services call it via an executor. One chunking is shared
    by every embedding model, so chunk size must respect the smallest
    registered model budget (enforced at model registration, not here).
    """

    def chunk(self, *, page_id: PageId, text: str) -> list[Chunk]:
        """Split ``text`` into ordered chunks with char offsets and token counts."""
        ...
