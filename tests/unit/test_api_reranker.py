"""ApiReranker tests with the rerankers factory stubbed (no network)."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import refindery.adapters.reranking.api as api_module
from refindery.adapters.reranking.api import ApiReranker
from refindery.application.ports.reranker import RerankCandidate
from refindery.domain.ids import ChunkId


class _StubRanker:
    def __init__(self, scores: list[float | None]) -> None:
        self._scores = scores
        self.calls: list[dict[str, object]] = []

    def rank(
        self, *, query: str, docs: list[str], doc_ids: list[str]
    ) -> SimpleNamespace:
        self.calls.append({"query": query, "docs": docs, "doc_ids": doc_ids})
        return SimpleNamespace(
            results=[
                SimpleNamespace(document=SimpleNamespace(doc_id=doc_id), score=score)
                for doc_id, score in zip(doc_ids, self._scores, strict=True)
            ]
        )


def _reranker(monkeypatch: pytest.MonkeyPatch, ranker: _StubRanker) -> ApiReranker:
    monkeypatch.setattr(api_module, "RerankersFactory", lambda *_a, **_k: ranker)
    return ApiReranker(provider="cohere", model="rerank-v3.5")


def test_factory_returning_none_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_module, "RerankersFactory", lambda *_a, **_k: None)
    with pytest.raises(RuntimeError, match="could not build"):
        ApiReranker(provider="cohere", model="rerank-v3.5")


def test_model_name_and_api_key_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COHERE_API_KEY", "env-key")
    captured: dict[str, object] = {}

    def factory(
        model: str, *, model_type: str, api_key: str, verbose: bool
    ) -> _StubRanker:
        captured.update(model=model, model_type=model_type, api_key=api_key)
        return _StubRanker([])

    monkeypatch.setattr(api_module, "RerankersFactory", factory)
    reranker = ApiReranker(provider="cohere", model="rerank-v3.5")
    assert reranker.model_name == "cohere:rerank-v3.5"
    assert captured == {
        "model": "rerank-v3.5",
        "model_type": "cohere",
        "api_key": "env-key",
    }


async def test_empty_candidates_short_circuit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ranker = _StubRanker([])
    reranker = _reranker(monkeypatch, ranker)
    assert await reranker.rerank(query="q", candidates=[]) == []
    assert ranker.calls == []


async def test_scores_map_and_none_becomes_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ranker = _StubRanker([0.9, None])
    reranker = _reranker(monkeypatch, ranker)
    scores = await reranker.rerank(
        query="q",
        candidates=[
            RerankCandidate(chunk_id=ChunkId("c1"), text="first"),
            RerankCandidate(chunk_id=ChunkId("c2"), text="second"),
        ],
    )
    by_id = {s.chunk_id: s.score for s in scores}
    assert by_id == {ChunkId("c1"): 0.9, ChunkId("c2"): 0.0}
    assert ranker.calls[0]["docs"] == ["first", "second"]


async def test_provider_errors_propagate(monkeypatch: pytest.MonkeyPatch) -> None:
    class _BrokenRanker:
        def rank(self, **_kwargs) -> SimpleNamespace:
            msg = "api down"
            raise ConnectionError(msg)

    monkeypatch.setattr(
        api_module, "RerankersFactory", lambda *_a, **_k: _BrokenRanker()
    )
    reranker = ApiReranker(provider="cohere", model="rerank-v3.5")
    with pytest.raises(ConnectionError, match="api down"):
        await reranker.rerank(
            query="q", candidates=[RerankCandidate(chunk_id=ChunkId("c1"), text="t")]
        )


async def test_malformed_provider_response_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _MalformedRanker:
        def rank(self, **_kwargs) -> SimpleNamespace:
            return SimpleNamespace(
                results=[SimpleNamespace(document=SimpleNamespace(), score="bad")]
            )

    monkeypatch.setattr(
        api_module, "RerankersFactory", lambda *_a, **_k: _MalformedRanker()
    )
    reranker = ApiReranker(provider="cohere", model="rerank-v3.5")
    with pytest.raises(ValidationError):
        await reranker.rerank(
            query="q", candidates=[RerankCandidate(chunk_id=ChunkId("c1"), text="t")]
        )


@pytest.mark.parametrize("score", [float("nan"), float("inf"), float("-inf")])
async def test_non_finite_provider_score_is_rejected(
    monkeypatch: pytest.MonkeyPatch, score: float
) -> None:
    ranker = _StubRanker([score])
    reranker = _reranker(monkeypatch, ranker)
    with pytest.raises(ValidationError):
        await reranker.rerank(
            query="q", candidates=[RerankCandidate(chunk_id=ChunkId("c1"), text="t")]
        )
