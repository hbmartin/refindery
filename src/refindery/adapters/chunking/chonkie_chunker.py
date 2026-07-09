"""Sentence-aware chunking via chonkie with a hard token ceiling.

Chunking is canonical and model-independent: one chunking, every embedding
model embeds the same spans. The cl100k tokenizer is the canonical counter.
chonkie targets ``chunk_size`` but can exceed it on pathological input, so a
post-pass re-splits anything above the hard max (which model registration
guarantees is within every registered model's budget).
"""

from chonkie import SentenceChunker, TokenChunker

from refindery.domain.ids import PageId, new_chunk_id
from refindery.domain.models import Chunk

_TOKENIZER = "cl100k_base"


class ChonkieChunker:
    """Chunker port implementation over chonkie's SentenceChunker."""

    def __init__(
        self,
        *,
        target_tokens: int = 448,
        overlap_tokens: int = 64,
        hard_max_tokens: int = 512,
    ) -> None:
        self._hard_max = hard_max_tokens
        self._sentence = SentenceChunker(
            tokenizer=_TOKENIZER,
            chunk_size=target_tokens,
            chunk_overlap=overlap_tokens,
        )
        self._splitter = TokenChunker(
            tokenizer=_TOKENIZER,
            chunk_size=hard_max_tokens,
            chunk_overlap=0,
        )

    def chunk(self, *, page_id: PageId, text: str) -> list[Chunk]:
        """Split ``text`` into ordered chunks with char offsets and token counts."""
        if not text.strip():
            return []
        spans: list[tuple[str, int, int, int]] = []
        for piece in self._sentence.chunk(text):
            if piece.token_count <= self._hard_max:
                spans.append(
                    (piece.text, piece.start_index, piece.end_index, piece.token_count)
                )
                continue
            spans.extend(
                (
                    sub.text,
                    piece.start_index + sub.start_index,
                    piece.start_index + sub.end_index,
                    sub.token_count,
                )
                for sub in self._splitter.chunk(piece.text)
            )
        return [
            Chunk(
                id=new_chunk_id(),
                page_id=page_id,
                ordinal=ordinal,
                text=chunk_text,
                token_count=token_count,
                char_start=char_start,
                char_end=char_end,
            )
            for ordinal, (chunk_text, char_start, char_end, token_count) in enumerate(
                spans
            )
        ]
