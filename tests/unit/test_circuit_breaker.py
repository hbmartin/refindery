"""Circuit breaker state machine tests (FakeClock-driven)."""

import pytest

from refindery.adapters.resilience.circuit_breaker import (
    BreakerConfig,
    BreakerRegistry,
    BreakerState,
    CircuitBreaker,
)
from refindery.domain.errors import ProviderUnavailableError
from tests.fakes.clock import FakeClock


def _breaker(
    clock: FakeClock, *, threshold: int = 3, cooldown: float = 30.0
) -> CircuitBreaker:
    return CircuitBreaker(
        name="embed:test",
        config=BreakerConfig(failure_threshold=threshold, cooldown_s=cooldown),
        clock=clock,
    )


def test_stays_closed_below_threshold():
    breaker = _breaker(FakeClock())
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state is BreakerState.CLOSED
    breaker.check()  # does not raise


def test_opens_at_threshold_and_fast_fails():
    breaker = _breaker(FakeClock())
    for _ in range(3):
        breaker.record_failure()
    assert breaker.state is BreakerState.OPEN
    with pytest.raises(ProviderUnavailableError) as excinfo:
        breaker.check()
    assert excinfo.value.provider == "embed:test"
    assert 0 < excinfo.value.retry_after_s <= 30.0


def test_success_resets_consecutive_failures():
    breaker = _breaker(FakeClock())
    breaker.record_failure()
    breaker.record_failure()
    breaker.record_success()
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state is BreakerState.CLOSED


def test_cooldown_admits_exactly_one_probe():
    clock = FakeClock()
    breaker = _breaker(clock)
    for _ in range(3):
        breaker.record_failure()
    clock.advance(seconds=30.0)
    breaker.check()  # probe admitted
    assert breaker.state is BreakerState.HALF_OPEN
    with pytest.raises(ProviderUnavailableError):
        breaker.check()  # concurrent call during probe


def test_probe_success_closes():
    clock = FakeClock()
    breaker = _breaker(clock)
    for _ in range(3):
        breaker.record_failure()
    clock.advance(seconds=30.0)
    breaker.check()
    breaker.record_success()
    assert breaker.state is BreakerState.CLOSED
    breaker.check()  # fully closed again


def test_probe_failure_reopens_with_fresh_cooldown():
    clock = FakeClock()
    breaker = _breaker(clock)
    for _ in range(3):
        breaker.record_failure()
    clock.advance(seconds=30.0)
    breaker.check()
    breaker.record_failure()
    assert breaker.state is BreakerState.OPEN
    clock.advance(seconds=29.0)
    with pytest.raises(ProviderUnavailableError):
        breaker.check()  # cooldown restarted at probe failure
    clock.advance(seconds=1.0)
    breaker.check()
    assert breaker.state is BreakerState.HALF_OPEN


def test_still_open_before_cooldown():
    clock = FakeClock()
    breaker = _breaker(clock, cooldown=10.0)
    for _ in range(3):
        breaker.record_failure()
    clock.advance(seconds=9.0)
    with pytest.raises(ProviderUnavailableError) as excinfo:
        breaker.check()
    assert excinfo.value.retry_after_s == pytest.approx(1.0)


def test_registry_returns_one_breaker_per_name():
    registry = BreakerRegistry(
        config=BreakerConfig(failure_threshold=3, cooldown_s=30.0), clock=FakeClock()
    )
    assert registry.get("embed:voyage") is registry.get("embed:voyage")
    assert registry.get("embed:voyage") is not registry.get("rerank:cohere")
