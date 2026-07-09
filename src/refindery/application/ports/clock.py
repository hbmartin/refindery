"""Injectable clock port; tests use a controllable fake."""

from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    """Source of the current time (always tz-aware UTC)."""

    def now(self) -> datetime:
        """Return the current UTC time."""
        ...
