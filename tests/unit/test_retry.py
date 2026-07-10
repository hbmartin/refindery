"""In-call retry tests with injected sleep and rng."""

import asyncio
import random
from collections.abc import Awaitable, Callable

import pytest

from refindery.adapters.resilience.retry import RetryPolicy, call_with_retry

POLICY = RetryPolicy(attempts=3, base_delay_s=0.25, max_delay_s=2.0)


def _recording_sleep(delays: list[float]) -> Callable[[float], Awaitable[None]]:
    async def sleep(delay: float) -> None:
        delays.append(delay)

    return sleep


async def test_success_after_transient_failure():
    calls: list[int] = []
    delays: list[float] = []

    async def flaky() -> str:
        calls.append(1)
        if len(calls) == 1:
            msg = "blip"
            raise ValueError(msg)
        return "ok"

    result = await call_with_retry(
        flaky,
        policy=POLICY,
        retryable=lambda _exc: True,
        sleep=_recording_sleep(delays),
        rng=random.Random(0),
    )
    assert result == "ok"
    assert len(calls) == 2
    assert len(delays) == 1
    assert 0.25 * 0.9 <= delays[0] <= 0.25 * 1.1


async def test_non_retryable_raises_immediately():
    calls: list[int] = []
    delays: list[float] = []

    async def broken() -> None:
        calls.append(1)
        msg = "client bug"
        raise ValueError(msg)

    with pytest.raises(ValueError, match="client bug"):
        await call_with_retry(
            broken,
            policy=POLICY,
            retryable=lambda _exc: False,
            sleep=_recording_sleep(delays),
        )
    assert len(calls) == 1
    assert delays == []


async def test_exhaustion_reraises_last_error():
    calls: list[int] = []
    delays: list[float] = []

    async def always_fails() -> None:
        calls.append(1)
        msg = f"failure {len(calls)}"
        raise RuntimeError(msg)

    with pytest.raises(RuntimeError, match="failure 3"):
        await call_with_retry(
            always_fails,
            policy=POLICY,
            retryable=lambda _exc: True,
            sleep=_recording_sleep(delays),
            rng=random.Random(0),
        )
    assert len(calls) == 3
    assert len(delays) == 2


async def test_backoff_doubles_and_caps():
    delays: list[float] = []
    policy = RetryPolicy(attempts=5, base_delay_s=1.0, max_delay_s=3.0)

    async def always_fails() -> None:
        raise RuntimeError

    with pytest.raises(RuntimeError):
        await call_with_retry(
            always_fails,
            policy=policy,
            retryable=lambda _exc: True,
            sleep=_recording_sleep(delays),
            rng=random.Random(0),
        )
    # jitter is ±10%, so compare against the un-jittered schedule 1, 2, 3, 3
    assert len(delays) == 4
    for delay, expected in zip(delays, [1.0, 2.0, 3.0, 3.0], strict=True):
        assert expected * 0.9 <= delay <= expected * 1.1


async def test_cancelled_error_propagates_unretried():
    calls: list[int] = []

    async def cancelled() -> None:
        calls.append(1)
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await call_with_retry(
            cancelled,
            policy=POLICY,
            retryable=lambda _exc: True,
            sleep=_recording_sleep([]),
        )
    assert len(calls) == 1
