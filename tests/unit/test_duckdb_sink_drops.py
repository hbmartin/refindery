"""Sink overflow: drop-oldest accounting reaches the Prometheus counter."""

from refindery.adapters.observability.duckdb_sink import DuckDbSink, TableSpec
from refindery.adapters.observability.metrics import query_log_dropped_total


def _counter_value() -> float:
    return query_log_dropped_total._value.get()  # noqa: SLF001 — test-only registry peek


def test_queue_overflow_increments_dropped_and_counter(tmp_path):
    sink = DuckDbSink(tmp_path / "obs.duckdb", max_queue=1)
    sink.register_table(TableSpec(name="t", ddl="", columns=("a",)))
    before = _counter_value()

    # Writer thread never started: the queue fills at 1 and overflows after.
    sink.append("t", ("row-1",))
    sink.append("t", ("row-2",))
    sink.append("t", ("row-3",))

    assert sink.dropped == 2
    # Registry is session-global: assert the delta, never the absolute value.
    assert _counter_value() - before == 2
