"""DuckDB append-only observability sink (query log + future trace events).

Single-writer pattern: producers ``put_nowait`` onto a bounded queue from the
event loop; one dedicated thread drains it in batches. The connection is
opened per batch and closed after (checkpointed), so offline eval can open
the file ``read_only=True`` between appends. A full queue drops the oldest
records and counts the drops — observability must never stall ingest or the
query path.
"""

import logging
import queue
import threading
from dataclasses import dataclass
from pathlib import Path

import duckdb

logger = logging.getLogger(__name__)

_SENTINEL = None
_BATCH_MAX = 256
_POLL_S = 0.25


@dataclass(frozen=True, slots=True)
class TableSpec:
    """A registered sink table: DDL plus insert column order."""

    name: str
    ddl: str
    columns: tuple[str, ...]


class DuckDbSink:
    """ObservabilitySink implementation over one DuckDB file."""

    def __init__(self, path: Path, *, max_queue: int = 10_000) -> None:
        self._path = path
        self._tables: dict[str, TableSpec] = {}
        self._queue: queue.Queue[tuple[str, tuple[object, ...]] | None] = queue.Queue(
            maxsize=max_queue
        )
        self._thread: threading.Thread | None = None
        self.dropped = 0

    def register_table(self, spec: TableSpec) -> None:
        """Declare a table before start(); DDL runs idempotently at startup."""
        self._tables[spec.name] = spec

    def append(self, table: str, values: tuple[object, ...]) -> None:
        """Buffer one row (values in registered column order); never blocks."""
        try:
            self._queue.put_nowait((table, values))
        except queue.Full:
            self.dropped += 1
            try:  # drop-oldest keeps the log fresh under pressure
                self._queue.get_nowait()
                self._queue.put_nowait((table, values))
            except (queue.Empty, queue.Full):
                logger.warning("observability queue thrashing; row dropped")

    def start(self) -> None:
        """Run DDL and start the writer thread."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = duckdb.connect(str(self._path))
        try:
            for spec in self._tables.values():
                if spec.ddl.strip():
                    conn.execute(spec.ddl)
        finally:
            conn.close()
        self._thread = threading.Thread(
            target=self._writer_loop, name="duckdb-sink", daemon=True
        )
        self._thread.start()

    def close(self) -> None:
        """Drain remaining rows and stop the writer."""
        if self._thread is None:
            return
        self._queue.put(_SENTINEL)
        self._thread.join(timeout=10.0)
        self._thread = None

    @property
    def alive(self) -> bool:
        """Writer thread health (feeds /readyz)."""
        return self._thread is not None and self._thread.is_alive()

    # -- writer thread -------------------------------------------------------

    def _writer_loop(self) -> None:
        stopping = False
        while not stopping:
            batch: list[tuple[str, tuple[object, ...]]] = []
            try:
                item = self._queue.get(timeout=_POLL_S)
            except queue.Empty:
                continue
            if item is _SENTINEL:
                stopping = True
            elif item is not None:
                batch.append(item)
            while len(batch) < _BATCH_MAX:
                try:
                    item = self._queue.get_nowait()
                except queue.Empty:
                    break
                if item is _SENTINEL:
                    stopping = True
                    break
                if item is not None:
                    batch.append(item)
            if batch:
                self._flush(batch)

    def _flush(self, batch: list[tuple[str, tuple[object, ...]]]) -> None:
        by_table: dict[str, list[tuple[object, ...]]] = {}
        for table, values in batch:
            by_table.setdefault(table, []).append(values)
        try:
            conn = duckdb.connect(str(self._path))
            try:
                for table, rows in by_table.items():
                    spec = self._tables[table]
                    placeholders = ", ".join("?" for _ in spec.columns)
                    conn.executemany(
                        f"INSERT INTO {spec.name} VALUES ({placeholders})",  # noqa: S608 — registered table names, ? placeholders
                        rows,
                    )
                conn.execute("CHECKPOINT")
            finally:
                conn.close()
        except Exception:
            logger.exception("observability sink flush failed; batch dropped")
            self.dropped += len(batch)
