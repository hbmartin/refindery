"""ResilientEmbedder / ResilientReranker wrapper tests."""

import asyncio

import numpy as np
import pytest

from refindery.adapters.embedding.catsu_embedder import EmbeddingDimensionMismatchError
from refindery.adapters.resilience.circuit_breaker import (
    BreakerConfig,
    BreakerState,
    CircuitBreaker,
)
from refindery.adapters.resilience.retry import RetryPolicy
from refindery.adapters.resilience.wrappers import (
    ResilientEmbedder,
    ResilientReranker,
)
from refindery.application.ports.reranker import RerankCandidate, RerankScore
from refindery.domain.errors import ProviderUnavailableError
from refindery.domain.ids import ChunkId
from tests.fakes.clock import FakeClock

POLICY = RetryPolicy(attempts=2, base_delay_s=0.001, max_delay_s=0.002)


async def _no_sleep(_delay: float) -> None:
    return


def _breaker(*, threshold: int = 3) -> CircuitBreaker:
    return CircuitBreaker(
        name="embed:test",
        config=BreakerConfig(failure_threshold=threshold, cooldown_s=30.0),
        clock=FakeClock(),
    )


class _StubEmbedder:
    def __init__(self, *, fail: Exception | None = None, hang: bool = False) -> None:
        self.calls = 0
        self._fail = fail
        self._hang = hang

    @property
    def model_id(self) -> str:
        return "stub-model"

    @property
    def dim(self) -> int:
        return 3

    @property
    def max_input_tokens(self) -> int:
        return 100

    async def embed_documents(self, texts: list[str]) -> list[np.ndarray]:
        self.calls += 1
        if self._hang:
            await asyncio.Event().wait()
        if self._fail is not None:
            raise self._fail
        return [np.zeros(3, dtype=np.float32) for _ in texts]

    async def embed_query(self, text: str) -> np.ndarray:
        return (await self.embed_documents([text]))[0]


def _embedder(
    inner: _StubEmbedder,
    breaker: CircuitBreaker,
    *,
    timeout_s: float = 5.0,
) -> ResilientEmbedder:
    return ResilientEmbedder(
        inner=inner,
        breaker=breaker,
        policy=POLICY,
        timeout_s=timeout_s,
        provider="test",
        sleep=_no_sleep,
    )


async def test_delegates_properties_and_success():
    inner = _StubEmbedder()
    wrapper = _embedder(inner, _breaker())
    assert wrapper.model_id == "stub-model"
    assert wrapper.dim == 3
    assert wrapper.max_input_tokens == 100
    vectors = await wrapper.embed_documents(["a", "b"])
    assert len(vectors) == 2


async def test_transient_failures_open_breaker_and_shield_inner():
    inner = _StubEmbedder(fail=ConnectionError("down"))
    breaker = _breaker(threshold=2)
    wrapper = _embedder(inner, breaker)

    with pytest.raises(ConnectionError):
        await wrapper.embed_documents(["a"])  # 2 attempts = 2 failures -> open
    assert breaker.state is BreakerState.OPEN
    assert inner.calls == 2

    with pytest.raises(ProviderUnavailableError):
        await wrapper.embed_documents(["a"])
    assert inner.calls == 2  # inner never touched while open


async def test_timeout_counts_as_breaker_failure():
    inner = _StubEmbedder(hang=True)
    breaker = _breaker(threshold=2)
    wrapper = _embedder(inner, breaker, timeout_s=0.01)

    with pytest.raises(TimeoutError):
        await wrapper.embed_documents(["a"])
    assert breaker.state is BreakerState.OPEN
    assert inner.calls == 2


async def test_dimension_mismatch_is_not_retried_and_keeps_breaker_closed():
    error = EmbeddingDimensionMismatchError(model_id="m", expected=3, got=5)
    inner = _StubEmbedder(fail=error)
    breaker = _breaker(threshold=1)
    wrapper = _embedder(inner, breaker)

    with pytest.raises(EmbeddingDimensionMismatchError):
        await wrapper.embed_query("a")
    assert inner.calls == 1
    assert breaker.state is BreakerState.CLOSED


class _StubReranker:
    def __init__(self, *, fail: Exception | None = None) -> None:
        self.calls = 0
        self._fail = fail

    @property
    def model_name(self) -> str:
        return "stub-reranker"

    async def rerank(
        self,
        *,
        query: str,  # noqa: ARG002 — port signature
        candidates: list[RerankCandidate],
    ) -> list[RerankScore]:
        self.calls += 1
        if self._fail is not None:
            raise self._fail
        return [RerankScore(chunk_id=c.chunk_id, score=0.5) for c in candidates]


async def test_reranker_delegates_and_opens_on_failures():
    breaker = CircuitBreaker(
        name="rerank:test",
        config=BreakerConfig(failure_threshold=2, cooldown_s=30.0),
        clock=FakeClock(),
    )
    inner = _StubReranker()
    wrapper = ResilientReranker(
        inner=inner, breaker=breaker, policy=POLICY, timeout_s=5.0, sleep=_no_sleep
    )
    assert wrapper.model_name == "stub-reranker"
    scores = await wrapper.rerank(
        query="q", candidates=[RerankCandidate(chunk_id=ChunkId("c1"), text="t")]
    )
    assert scores == [RerankScore(chunk_id=ChunkId("c1"), score=0.5)]

    failing = _StubReranker(fail=RuntimeError("api down"))
    failing_wrapper = ResilientReranker(
        inner=failing, breaker=breaker, policy=POLICY, timeout_s=5.0, sleep=_no_sleep
    )
    with pytest.raises(RuntimeError):
        await failing_wrapper.rerank(
            query="q", candidates=[RerankCandidate(chunk_id=ChunkId("c1"), text="t")]
        )
    assert breaker.state is BreakerState.OPEN
    with pytest.raises(ProviderUnavailableError):
        await wrapper.rerank(
            query="q", candidates=[RerankCandidate(chunk_id=ChunkId("c1"), text="t")]
        )
