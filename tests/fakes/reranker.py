"""Deterministic fake reranker: scores by query-token overlap."""

from refindery.application.ports.reranker import RerankCandidate, RerankScore


class FakeReranker:
    """Token-overlap scoring; higher overlap wins."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    @property
    def model_name(self) -> str:
        """Identifier for the query log."""
        return "fake-reranker"

    async def rerank(
        self, *, query: str, candidates: list[RerankCandidate]
    ) -> list[RerankScore]:
        """Jaccard-ish overlap between query tokens and candidate tokens."""
        self.calls.append((query, len(candidates)))
        query_tokens = set(query.lower().split())
        scores: list[RerankScore] = []
        for candidate in candidates:
            tokens = set(candidate.text.lower().split())
            overlap = len(query_tokens & tokens)
            scores.append(
                RerankScore(
                    chunk_id=candidate.chunk_id,
                    score=overlap / (len(query_tokens) or 1),
                )
            )
        return scores
