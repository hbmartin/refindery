"""Per-stage latency capture for the retrieval pipeline."""

import time
from collections.abc import Iterator
from contextlib import contextmanager


class StageTimer:
    """Accumulates stage durations in milliseconds."""

    def __init__(self) -> None:
        self.timings_ms: dict[str, float] = {}
        self._started = time.perf_counter()

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        """Time one named stage."""
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed = (time.perf_counter() - start) * 1_000.0
            self.timings_ms[name] = self.timings_ms.get(name, 0.0) + elapsed

    def record(self, name: str, value_ms: float) -> None:
        """Record an externally measured stage (e.g. adapter arm timings)."""
        self.timings_ms[name] = self.timings_ms.get(name, 0.0) + value_ms

    def total_ms(self) -> float:
        """Milliseconds since construction."""
        return (time.perf_counter() - self._started) * 1_000.0
