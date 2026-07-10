"""In-call retry with jittered exponential backoff.

This is the cheap layer beneath the circuit breaker: it absorbs single
transient blips (a 429, a dropped connection) without re-running a whole
job. ``asyncio.CancelledError`` is a ``BaseException`` and always propagates
un-retried — load-bearing for lease-timeout cancellation in the job queue.
"""

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

_JITTER = 0.1
_module_rng = random.Random()  # noqa: S311 — backoff jitter, not cryptography


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Total attempt count and backoff bounds for one call."""

    attempts: int
    base_delay_s: float
    max_delay_s: float


async def call_with_retry[T](
    fn: Callable[[], Awaitable[T]],
    *,
    policy: RetryPolicy,
    retryable: Callable[[Exception], bool],
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rng: random.Random | None = None,
) -> T:
    """Invoke ``fn`` up to ``policy.attempts`` times, retrying transient errors.

    Only exceptions passing ``retryable`` are retried; anything else (and the
    final attempt's error) is re-raised. ``sleep``/``rng`` are injectable for
    deterministic tests.
    """
    jitter_rng = _module_rng if rng is None else rng
    attempt = 0
    while True:
        try:
            return await fn()
        except Exception as exc:
            attempt += 1
            if attempt >= policy.attempts or not retryable(exc):
                raise
            delay = min(policy.max_delay_s, policy.base_delay_s * 2 ** (attempt - 1))
            await sleep(delay * (1 + jitter_rng.uniform(-_JITTER, _JITTER)))
