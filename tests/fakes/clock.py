"""Controllable clock for deterministic tests."""

from datetime import UTC, datetime, timedelta


class FakeClock:
    """A clock that only moves when told to."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 7, 8, 12, 0, 0, tzinfo=UTC)

    def now(self) -> datetime:
        """Return the frozen current time."""
        return self._now

    def advance(self, seconds: float = 0.0, *, minutes: float = 0.0) -> None:
        """Move time forward."""
        self._now += timedelta(seconds=seconds, minutes=minutes)
