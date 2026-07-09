"""Reranker port: cross-encoder scoring of chunks against a query.

Reranks chunks, never pages — page rollup happens after, preserving the
chunk-level signal that makes long pages findable.
"""

from dataclasses import dataclass
from typing import Protocol

from refindery.domain.ids import ChunkId


@dataclass(frozen=True, slots=True)
class RerankCandidate:
    """One chunk offered to the reranker."""

    chunk_id: ChunkId
    text: str


@dataclass(frozen=True, slots=True)
class RerankScore:
    """Reranker relevance score for one chunk."""

    chunk_id: ChunkId
    score: float


class Reranker(Protocol):
    """Scores candidate chunks against a query."""

    @property
    def model_name(self) -> str:
        """Identifier of the reranking model (for the query log)."""
        ...

    async def rerank(
        self, *, query: str, candidates: list[RerankCandidate]
    ) -> list[RerankScore]:
        """Score all candidates; order of the result is not significant."""
        ...
