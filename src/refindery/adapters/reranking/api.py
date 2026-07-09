"""API reranker over the rerankers library (Cohere Rerank 3.5, Voyage rerank-2.5).

API rerankers are first-class per the spec; local cross-encoders need torch
and load lazily (LocalReranker) when installed.
"""

import asyncio
import os

from rerankers import Reranker as RerankersFactory

from refindery.application.ports.reranker import RerankCandidate, RerankScore
from refindery.domain.ids import ChunkId

_KEY_ENV = {"cohere": "COHERE_API_KEY", "voyage": "VOYAGE_API_KEY"}


class ApiReranker:
    """Reranker port implementation over an API provider."""

    def __init__(
        self, *, provider: str, model: str, api_key: str | None = None
    ) -> None:
        key = api_key or os.environ.get(_KEY_ENV.get(provider, ""), None)
        ranker = RerankersFactory(model, model_type=provider, api_key=key, verbose=0)
        if ranker is None:
            msg = f"rerankers could not build a {provider!r} ranker for {model!r}"
            raise RuntimeError(msg)
        self._ranker = ranker
        self._name = f"{provider}:{model}"

    @property
    def model_name(self) -> str:
        """Identifier for the query log."""
        return self._name

    async def rerank(
        self, *, query: str, candidates: list[RerankCandidate]
    ) -> list[RerankScore]:
        """Score candidates via the provider (network call in a thread)."""
        if not candidates:
            return []
        ranked = await asyncio.to_thread(
            self._ranker.rank,
            query=query,
            docs=[c.text for c in candidates],
            doc_ids=[str(c.chunk_id) for c in candidates],
        )
        return [
            RerankScore(
                chunk_id=ChunkId(str(result.document.doc_id)),
                score=0.0 if result.score is None else float(result.score),
            )
            for result in ranked.results
        ]
