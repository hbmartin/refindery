"""Resilient wrappers implementing the provider ports.

Each wrapper composes, per call: breaker admission check, a per-attempt
timeout (``asyncio.wait_for``), in-call retry for transient errors, and
breaker bookkeeping. Non-transient errors (client bugs like a 400 or a
dimension mismatch) are neither retried nor counted against the breaker —
the provider responded, so they count as breaker successes.

Timeout caveat: cancelling catsu's native future or a ``to_thread`` rerank
call cannot stop the underlying request — the caller is freed and the
breaker records the timeout, but the request may run to completion in the
background.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

import httpx

from refindery.adapters.embedding.catsu_embedder import EmbeddingDimensionMismatchError
from refindery.adapters.observability.metrics import embedding_api_errors_total
from refindery.adapters.resilience.circuit_breaker import CircuitBreaker
from refindery.adapters.resilience.retry import RetryPolicy, call_with_retry
from refindery.application.ports.embedder import Embedder
from refindery.application.ports.reranker import RerankCandidate, Reranker, RerankScore
from refindery.domain.errors import ProviderUnavailableError
from refindery.domain.rollup import Vector

logger = logging.getLogger(__name__)

_TRANSIENT_STATUS = frozenset({408, 429})


def _record_cancellation(breaker: CircuitBreaker | None) -> None:
    if breaker is not None:
        breaker.record_cancellation()


async def _guarded_attempt[T](
    fn: Callable[[], Awaitable[T]],
    *,
    breaker: CircuitBreaker | None,
    timeout_s: float,
    retryable: Callable[[Exception], bool],
    on_failure: Callable[[], None] | None,
) -> T:
    if breaker is not None:
        breaker.check()
    try:
        result = await asyncio.wait_for(fn(), timeout=timeout_s)
    except asyncio.CancelledError:
        _record_cancellation(breaker)
        raise
    except ProviderUnavailableError:
        raise
    except Exception as exc:
        if retryable(exc):
            if breaker is not None:
                breaker.record_failure()
            if on_failure is not None:
                on_failure()
        elif breaker is not None:
            breaker.record_success()
        raise
    if breaker is not None:
        breaker.record_success()
    return result


def is_transient_http(exc: Exception) -> bool:
    """Whether an httpx-surfaced error is worth retrying / counting as outage."""
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return (
            status in _TRANSIENT_STATUS or status >= httpx.codes.INTERNAL_SERVER_ERROR
        )
    return isinstance(exc, httpx.TransportError | TimeoutError)


def is_transient_default(exc: Exception) -> bool:
    """Transient predicate for untyped-exception libraries (catsu, rerankers)."""
    return not isinstance(
        exc, EmbeddingDimensionMismatchError | ProviderUnavailableError
    )


async def guarded_call[T](
    fn: Callable[[], Awaitable[T]],
    *,
    breaker: CircuitBreaker | None,
    policy: RetryPolicy,
    timeout_s: float,
    retryable: Callable[[Exception], bool],
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    on_failure: Callable[[], None] | None = None,
) -> T:
    """Run ``fn`` under breaker admission, per-attempt timeout, and retry."""

    async def attempt() -> T:
        return await _guarded_attempt(
            fn,
            breaker=breaker,
            timeout_s=timeout_s,
            retryable=retryable,
            on_failure=on_failure,
        )

    return await call_with_retry(
        attempt, policy=policy, retryable=retryable, sleep=sleep
    )


class ResilientEmbedder:
    """Embedder port wrapper adding breaker, retry, and timeout."""

    def __init__(
        self,
        *,
        inner: Embedder,
        breaker: CircuitBreaker,
        policy: RetryPolicy,
        timeout_s: float,
        provider: str,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._inner = inner
        self._breaker = breaker
        self._policy = policy
        self._timeout_s = timeout_s
        self._provider = provider
        self._sleep = sleep

    @property
    def model_id(self) -> str:
        """Registry id of the model this embedder serves."""
        return self._inner.model_id

    @property
    def dim(self) -> int:
        """Dimensionality of produced vectors."""
        return self._inner.dim

    @property
    def max_input_tokens(self) -> int:
        """Maximum tokens the model accepts per input."""
        return self._inner.max_input_tokens

    async def embed_documents(self, texts: list[str]) -> list[Vector]:
        """Embed document chunks (storage side)."""
        return await self._call(lambda: self._inner.embed_documents(texts))

    async def embed_query(self, text: str) -> Vector:
        """Embed a query (query side)."""
        return await self._call(lambda: self._inner.embed_query(text))

    async def _call[T](self, fn: Callable[[], Awaitable[T]]) -> T:
        return await guarded_call(
            fn,
            breaker=self._breaker,
            policy=self._policy,
            timeout_s=self._timeout_s,
            retryable=is_transient_default,
            sleep=self._sleep,
            on_failure=embedding_api_errors_total.labels(provider=self._provider).inc,
        )


class ResilientReranker:
    """Reranker port wrapper adding breaker, retry, and timeout."""

    def __init__(
        self,
        *,
        inner: Reranker,
        breaker: CircuitBreaker,
        policy: RetryPolicy,
        timeout_s: float,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._inner = inner
        self._breaker = breaker
        self._policy = policy
        self._timeout_s = timeout_s
        self._sleep = sleep

    @property
    def model_name(self) -> str:
        """Identifier of the reranking model (for the query log)."""
        return self._inner.model_name

    async def rerank(
        self, *, query: str, candidates: list[RerankCandidate]
    ) -> list[RerankScore]:
        """Score all candidates; order of the result is not significant."""
        return await guarded_call(
            lambda: self._inner.rerank(query=query, candidates=candidates),
            breaker=self._breaker,
            policy=self._policy,
            timeout_s=self._timeout_s,
            retryable=is_transient_default,
            sleep=self._sleep,
        )
