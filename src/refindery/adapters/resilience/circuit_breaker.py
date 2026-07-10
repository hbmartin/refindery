"""Per-provider circuit breaker.

One breaker guards one failure domain (a provider, not a model): consecutive
transient failures open it, and while open every call fast-fails with
:class:`ProviderUnavailableError` instead of burning timeouts and job retry
budget. After a cooldown a single probe call is admitted (half-open); its
outcome closes or re-opens the breaker.

Time comes from the injected :class:`Clock` port (wall clock, not monotonic)
so tests drive transitions with ``FakeClock``; an NTP step can shorten or
lengthen a cooldown, which is acceptable here. No lock is needed: everything
runs on the single main event loop and transitions contain no awaits.
"""

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from refindery.adapters.observability.metrics import (
    circuit_breaker_open_total,
    circuit_breaker_state,
)
from refindery.application.ports.clock import Clock
from refindery.domain.errors import ProviderUnavailableError

if TYPE_CHECKING:
    from datetime import datetime

logger = logging.getLogger(__name__)


class BreakerState(StrEnum):
    """Circuit breaker states."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


_STATE_GAUGE = {
    BreakerState.CLOSED: 0,
    BreakerState.HALF_OPEN: 1,
    BreakerState.OPEN: 2,
}


@dataclass(frozen=True, slots=True)
class BreakerConfig:
    """Failure threshold and cooldown for one breaker."""

    failure_threshold: int
    cooldown_s: float


class CircuitBreaker:
    """Consecutive-failure breaker for one provider."""

    def __init__(self, *, name: str, config: BreakerConfig, clock: Clock) -> None:
        self._name = name
        self._config = config
        self._clock = clock
        self._state = BreakerState.CLOSED
        self._consecutive_failures = 0
        self._opened_at: datetime | None = None
        self._probe_inflight = False
        circuit_breaker_state.labels(name=name).set(_STATE_GAUGE[self._state])

    @property
    def name(self) -> str:
        """Failure-domain label, e.g. ``embed:voyage``."""
        return self._name

    @property
    def state(self) -> BreakerState:
        """Current breaker state."""
        return self._state

    def check(self) -> None:
        """Admit the call or raise :class:`ProviderUnavailableError`.

        While open, raises until the cooldown elapses, then transitions to
        half-open and admits exactly one probe; concurrent calls during the
        probe are rejected.
        """
        match self._state:
            case BreakerState.CLOSED:
                return
            case BreakerState.OPEN:
                remaining = self._cooldown_remaining()
                if remaining > 0:
                    raise ProviderUnavailableError(
                        provider=self._name, retry_after_s=remaining
                    )
                self._transition(BreakerState.HALF_OPEN)
                self._probe_inflight = True
            case BreakerState.HALF_OPEN:
                if self._probe_inflight:
                    raise ProviderUnavailableError(
                        provider=self._name,
                        retry_after_s=self._config.cooldown_s,
                    )
                self._probe_inflight = True

    def record_success(self) -> None:
        """Close the breaker and reset counters — the provider responded."""
        self._probe_inflight = False
        self._consecutive_failures = 0
        if self._state is not BreakerState.CLOSED:
            self._transition(BreakerState.CLOSED)

    def record_failure(self) -> None:
        """Count a transient failure; open at the threshold or on probe failure."""
        self._probe_inflight = False
        self._consecutive_failures += 1
        if self._state is BreakerState.HALF_OPEN or (
            self._state is BreakerState.CLOSED
            and self._consecutive_failures >= self._config.failure_threshold
        ):
            self._open()

    def _open(self) -> None:
        self._opened_at = self._clock.now()
        self._transition(BreakerState.OPEN)
        circuit_breaker_open_total.labels(name=self._name).inc()
        logger.warning(
            "circuit breaker %s opened after %d consecutive failures; "
            "cooling down for %.0fs",
            self._name,
            self._consecutive_failures,
            self._config.cooldown_s,
        )

    def _transition(self, state: BreakerState) -> None:
        if state in {BreakerState.CLOSED, BreakerState.HALF_OPEN}:
            logger.info("circuit breaker %s -> %s", self._name, state)
        self._state = state
        circuit_breaker_state.labels(name=self._name).set(_STATE_GAUGE[state])

    def _cooldown_remaining(self) -> float:
        if self._opened_at is None:
            return 0.0
        elapsed = (self._clock.now() - self._opened_at).total_seconds()
        return max(0.0, self._config.cooldown_s - elapsed)


class BreakerRegistry:
    """Create-or-return one breaker per failure-domain name."""

    def __init__(self, *, config: BreakerConfig, clock: Clock) -> None:
        self._config = config
        self._clock = clock
        self._breakers: dict[str, CircuitBreaker] = {}

    def get(self, name: str) -> CircuitBreaker:
        """Return the breaker for ``name``, creating it on first use."""
        if (breaker := self._breakers.get(name)) is None:
            breaker = CircuitBreaker(name=name, config=self._config, clock=self._clock)
            self._breakers[name] = breaker
        return breaker
