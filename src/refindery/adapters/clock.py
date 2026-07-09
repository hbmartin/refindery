"""System clock adapter."""

from datetime import UTC, datetime


class SystemClock:
    """Wall clock returning tz-aware UTC datetimes."""

    def now(self) -> datetime:
        """Return the current UTC time."""
        return datetime.now(tz=UTC)
