"""Periodic Prometheus registry snapshots persisted in DuckDB."""

import asyncio
import json
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from refindery.adapters.observability.duckdb_sink import (
    DuckDbSink,
    TableSpec,
    open_read_only,
)
from refindery.adapters.observability.metrics import registry

METRICS_DDL = """
CREATE TABLE IF NOT EXISTS metrics_samples (
  ts TIMESTAMPTZ NOT NULL,
  metric VARCHAR NOT NULL,
  sample VARCHAR NOT NULL,
  labels JSON NOT NULL,
  metric_type VARCHAR NOT NULL,
  value DOUBLE NOT NULL
);
"""


@dataclass(frozen=True, slots=True)
class MetricPoint:
    """One persisted or current metric point."""

    ts: datetime
    value: float


@dataclass(frozen=True, slots=True)
class MetricSeries:
    """One distinct labeled Prometheus sample series."""

    sample: str
    labels: dict[str, str]
    metric_type: str
    points: list[MetricPoint]


class MetricsSnapshotter:
    """Collect the local registry periodically without HTTP self-scraping."""

    def __init__(self, sink: DuckDbSink, *, interval_s: float) -> None:
        self._sink = sink
        self._interval_s = interval_s
        self._task: asyncio.Task[None] | None = None
        sink.register_table(
            TableSpec(
                name="metrics_samples",
                ddl=METRICS_DDL,
                columns=("ts", "metric", "sample", "labels", "metric_type", "value"),
            )
        )

    def snapshot(self) -> None:
        """Append one consistent registry collection."""
        now = datetime.now(tz=UTC)
        for family in registry.collect():
            for sample in family.samples:
                self._sink.append(
                    "metrics_samples",
                    (
                        now,
                        family.name,
                        sample.name,
                        json.dumps(dict(sample.labels), sort_keys=True),
                        family.type,
                        float(sample.value),
                    ),
                )

    def start(self) -> None:
        """Start periodic collection on the current event loop."""
        self.snapshot()
        self._task = asyncio.create_task(self._run(), name="metrics-snapshotter")

    async def stop(self) -> None:
        """Stop periodic collection."""
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self._interval_s)
            self.snapshot()


class DuckDbMetricsReader:
    """Read and downsample persisted registry snapshots."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def metric_exists(self, metric: str) -> bool:
        """Whether at least one snapshot exists for a metric family."""
        with open_read_only(self._path) as conn:
            row = conn.execute(
                "SELECT 1 FROM metrics_samples WHERE metric = ? LIMIT 1", [metric]
            ).fetchone()
        return row is not None or any(
            family.name == metric for family in registry.collect()
        )

    def read_series(
        self, *, metric: str, since: datetime | None, step_s: float
    ) -> list[MetricSeries]:
        """Read and downsample all labeled series in a metric family."""
        if since is not None and since.tzinfo is None:
            since = since.replace(tzinfo=UTC)
        with open_read_only(self._path) as conn:
            rows = conn.execute(
                "SELECT epoch_us(ts), sample, labels, metric_type, value "
                "FROM metrics_samples WHERE metric = ? AND ts >= coalesce(?, ts) "
                "ORDER BY ts",
                [metric, since],
            ).fetchall()
        grouped: dict[tuple[str, str, str], list[MetricPoint]] = {}
        for ts_us, sample, labels, metric_type, value in rows:
            key = (sample, labels, metric_type)
            grouped.setdefault(key, []).append(
                MetricPoint(
                    ts=datetime.fromtimestamp(ts_us / 1_000_000, tz=UTC),
                    value=float(value),
                )
            )
        return [
            MetricSeries(
                sample=sample,
                labels=json.loads(labels),
                metric_type=metric_type,
                points=_downsample(points, metric_type=metric_type, step_s=step_s),
            )
            for (sample, labels, metric_type), points in grouped.items()
        ]


_CUMULATIVE_TYPES = frozenset({"counter", "histogram", "summary"})


def _downsample(
    points: list[MetricPoint], *, metric_type: str, step_s: float
) -> list[MetricPoint]:
    """Last-value gauges and per-window deltas for cumulative metric families.

    Cumulative families report the rise over each window measured against the
    previous window's last value; the first window is seeded from its own first
    sample so every window (including the first) is a consistent within-window
    delta. A negative step means the counter reset (e.g. a process restart), so
    the increment since the reset is lost and the window is clamped to 0.
    """
    buckets: dict[int, list[MetricPoint]] = {}
    for point in points:
        bucket = int(point.ts.timestamp() // step_s)
        buckets.setdefault(bucket, []).append(point)
    output: list[MetricPoint] = []
    previous: float | None = None
    for bucket_points in buckets.values():
        latest = bucket_points[-1]
        value = latest.value
        if metric_type in _CUMULATIVE_TYPES:
            baseline = bucket_points[0].value if previous is None else previous
            value = max(0.0, latest.value - baseline)
            previous = latest.value
        output.append(MetricPoint(ts=latest.ts, value=value))
    return output


def current_gauges(metric: str) -> list[MetricSeries]:
    """Return current gauge samples for a metric family."""
    now = datetime.now(tz=UTC)
    return [
        MetricSeries(
            sample=sample.name,
            labels=dict(sample.labels),
            metric_type=family.type,
            points=[MetricPoint(ts=now, value=float(sample.value))],
        )
        for family in registry.collect()
        if family.name == metric and family.type == "gauge"
        for sample in family.samples
    ]


def current_counters(metric: str) -> list[MetricSeries]:
    """Return current counter samples for a metric family.

    Pass the stripped family name (prometheus_client removes the ``_total``
    suffix from counter family names). Values are process-lifetime totals.
    """
    now = datetime.now(tz=UTC)
    return [
        MetricSeries(
            sample=sample.name,
            labels=dict(sample.labels),
            metric_type=family.type,
            points=[MetricPoint(ts=now, value=float(sample.value))],
        )
        for family in registry.collect()
        if family.name == metric and family.type == "counter"
        for sample in family.samples
        if sample.name.endswith("_total")
    ]
