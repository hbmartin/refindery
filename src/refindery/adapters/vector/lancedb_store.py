"""LanceDB (zero-daemon, in-process) implementation of the VectorStore port.

Layout: one ``chunks`` table holds the filterable payload plus the text with
a native Lance FTS index (the shared sparse arm — tantivy was removed in
lancedb 0.34); one safe ``vectors_<slug>_<hash>`` table per model holds that
model's dense vectors plus a payload copy for filter pushdown.

The chunks table is ``optimize()``d after every write: lancedb 0.34's FTS
scan over *unindexed* rows silently returns no results when a matching
document contains a repeated term, so search must never rely on it —
optimize folds fresh rows into the index immediately.
Table-per-model keeps add/drop-model trivial (create/drop table) at the cost
of duplicated payload — irrelevant at personal scale.

LanceDB's Python API is synchronous; every method hops to a thread.
"""

import asyncio
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

import lancedb
import pyarrow as pa
from lancedb.index import FTS

from refindery.adapters.vector.hybrid import run_hybrid_query
from refindery.adapters.vector.names import safe_model_name
from refindery.application.ports.vector_store import (
    ChunkPoint,
    HybridHits,
    HybridQuery,
    StoreFilter,
)
from refindery.domain.ids import ChunkId, PageId
from refindery.domain.models import EmbeddingModel
from refindery.domain.retrieval import ChunkHit
from refindery.domain.rollup import Vector

_CHUNKS_TABLE = "chunks"

if TYPE_CHECKING:
    from lancedb.query import LanceVectorQueryBuilder


def _vectors_table(model_id: str) -> str:
    return safe_model_name(prefix="vectors", model_id=model_id)


def _epoch(value: datetime) -> int:
    return int(value.timestamp())


def _sql_quote(value: str) -> str:
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


def _where(filters: StoreFilter | None) -> str | None:
    if filters is None:
        return None
    clauses: list[str] = []
    if filters.domain is not None:
        clauses.append(f"domain = {_sql_quote(filters.domain)}")
    if filters.after is not None:
        clauses.append(f"first_seen_at >= {_epoch(filters.after)}")
    if filters.before is not None:
        clauses.append(f"first_seen_at < {_epoch(filters.before)}")
    if filters.page_ids is not None:
        if not filters.page_ids:
            clauses.append("page_id = ''")  # matches nothing
        else:
            quoted = ",".join(_sql_quote(pid) for pid in sorted(filters.page_ids))
            clauses.append(f"page_id IN ({quoted})")
    return " AND ".join(clauses) if clauses else None


def _payload_fields() -> list[pa.Field]:
    return [
        pa.field("chunk_id", pa.string()),
        pa.field("page_id", pa.string()),
        pa.field("ordinal", pa.int32()),
        pa.field("domain", pa.string()),
        pa.field("first_seen_at", pa.int64()),
        pa.field("cluster_id", pa.string(), nullable=True),
    ]


def _chunks_schema() -> pa.Schema:
    return pa.schema([*_payload_fields(), pa.field("text", pa.string())])


def _vectors_schema(dim: int) -> pa.Schema:
    return pa.schema(
        [*_payload_fields(), pa.field("vector", pa.list_(pa.float32(), dim))]
    )


def _payload_row(point: ChunkPoint) -> dict[str, object]:
    return {
        "chunk_id": point.chunk_id,
        "page_id": point.page_id,
        "ordinal": point.ordinal,
        "domain": point.domain,
        "first_seen_at": _epoch(point.first_seen_at),
        "cluster_id": point.cluster_id,
    }


class LanceDbVectorStore:
    """VectorStore implementation over a local LanceDB directory."""

    def __init__(self, path: Path) -> None:
        self._path = path
        path.mkdir(parents=True, exist_ok=True)
        self._db = lancedb.connect(str(path))

    def _table_names(self) -> list[str]:
        return list(self._db.list_tables().tables or [])

    # -- schema ------------------------------------------------------------

    async def ensure_schema(self, models: list[EmbeddingModel]) -> None:
        """Create the chunks table and one vectors table per model."""

        def _ensure() -> None:
            chunks = self._db.create_table(
                _CHUNKS_TABLE, schema=_chunks_schema(), exist_ok=True
            )
            if not any(i.name == "text_idx" for i in chunks.list_indices()):
                chunks.create_index("text", config=FTS(), replace=True)
            for model in models:
                self._db.create_table(
                    _vectors_table(model.id),
                    schema=_vectors_schema(model.dim),
                    exist_ok=True,
                )

        await asyncio.to_thread(_ensure)

    async def add_model(self, model: EmbeddingModel) -> None:
        """Create the vectors table for a new model."""
        await asyncio.to_thread(
            self._db.create_table,
            _vectors_table(model.id),
            schema=_vectors_schema(model.dim),
            exist_ok=True,
        )

    async def drop_model(self, model_id: str) -> None:
        """Drop a model's vectors table."""

        def _drop() -> None:
            if _vectors_table(model_id) in self._table_names():
                self._db.drop_table(_vectors_table(model_id))

        await asyncio.to_thread(_drop)

    # -- writes ------------------------------------------------------------

    async def upsert_chunks(self, points: list[ChunkPoint]) -> None:
        """Upsert payload + text, then every model's vectors; rebuild FTS."""
        if not points:
            return

        def _upsert() -> None:
            chunks = self._db.open_table(_CHUNKS_TABLE)
            rows = [{**_payload_row(p), "text": p.text} for p in points]
            self._merge(chunks, rows)
            chunks.optimize()
            model_ids = {m for p in points for m in p.vectors}
            for model_id in sorted(model_ids):
                self._write_model_vectors(model_id, points)

        await asyncio.to_thread(_upsert)

    async def backfill_vectors(
        self, *, model_id: str, points: list[ChunkPoint]
    ) -> None:
        """Write one model's vectors without touching other tables."""
        if not points:
            return
        await asyncio.to_thread(self._write_model_vectors, model_id, points)

    def _write_model_vectors(self, model_id: str, points: list[ChunkPoint]) -> None:
        table = self._db.open_table(_vectors_table(model_id))
        rows = [
            {**_payload_row(p), "vector": p.vectors[model_id].tolist()}
            for p in points
            if model_id in p.vectors
        ]
        if rows:
            self._merge(table, rows)

    @staticmethod
    def _merge(table: lancedb.table.Table, rows: list[dict[str, object]]) -> None:
        last_error: Exception | None = None
        for _attempt in range(3):
            try:
                (
                    table.merge_insert("chunk_id")
                    .when_matched_update_all()
                    .when_not_matched_insert_all()
                    .execute(rows)
                )
            except Exception as exc:  # noqa: BLE001 — optimistic concurrency retry
                last_error = exc
                time.sleep(0.05)
            else:
                return
        if last_error is not None:
            raise last_error

    async def delete_pages(self, page_ids: Sequence[PageId]) -> None:
        """Delete chunks of these pages from every table; rebuild FTS."""
        if not page_ids:
            return

        def _delete() -> None:
            quoted = ",".join(_sql_quote(pid) for pid in page_ids)
            predicate = f"page_id IN ({quoted})"
            chunks = self._db.open_table(_CHUNKS_TABLE)
            chunks.delete(predicate)
            chunks.optimize()
            for name in self._table_names():
                if name.startswith("vectors_"):
                    self._db.open_table(name).delete(predicate)

        await asyncio.to_thread(_delete)

    async def count_chunks(self, page_id: PageId) -> int:
        """Count stored chunks for one page."""
        return await asyncio.to_thread(
            self._db.open_table(_CHUNKS_TABLE).count_rows,
            f"page_id = {_sql_quote(page_id)}",
        )

    # -- reads -------------------------------------------------------------

    async def dense_query(
        self,
        *,
        model_id: str,
        vector: Vector,
        limit: int,
        filters: StoreFilter | None = None,
    ) -> list[ChunkHit]:
        """Dense kNN over one model's vectors table."""

        def _query() -> list[ChunkHit]:
            table = self._db.open_table(_vectors_table(model_id))
            search = cast(
                "LanceVectorQueryBuilder",
                table.search(vector.tolist(), vector_column_name="vector"),
            )
            search = search.metric("cosine").limit(limit)
            if (where := _where(filters)) is not None:
                search = search.where(where)
            rows = search.to_list()
            return [
                ChunkHit(
                    chunk_id=ChunkId(row["chunk_id"]),
                    page_id=PageId(row["page_id"]),
                    ordinal=row["ordinal"],
                    score=1.0 - float(row["_distance"]),
                )
                for row in rows
            ]

        return await asyncio.to_thread(_query)

    async def sparse_query(
        self,
        *,
        text: str,
        limit: int,
        filters: StoreFilter | None = None,
    ) -> list[ChunkHit]:
        """Tantivy FTS over the shared chunks table."""

        def _query() -> list[ChunkHit]:
            table = self._db.open_table(_CHUNKS_TABLE)
            if table.count_rows() == 0:
                return []
            search = table.search(text, query_type="fts").limit(limit)
            if (where := _where(filters)) is not None:
                search = search.where(where)
            rows = search.to_list()
            return [
                ChunkHit(
                    chunk_id=ChunkId(row["chunk_id"]),
                    page_id=PageId(row["page_id"]),
                    ordinal=row["ordinal"],
                    score=float(row["_score"]),
                )
                for row in rows
            ]

        return await asyncio.to_thread(_query)

    async def hybrid_query(self, query: HybridQuery) -> HybridHits:
        """Run both arms concurrently and fuse client-side."""
        return await run_hybrid_query(
            query=query,
            dense_arm=lambda: self.dense_query(
                model_id=query.model_id,
                vector=query.dense_vector,
                limit=query.per_arm_limit,
                filters=query.filters,
            ),
            sparse_arm=lambda: self.sparse_query(
                text=query.sparse_text,
                limit=query.per_arm_limit,
                filters=query.filters,
            ),
        )

    async def close(self) -> None:
        """LanceDB is in-process; nothing to release."""


def utc_from_epoch(value: int) -> datetime:
    """Inverse of the adapter's epoch encoding (test helper)."""
    return datetime.fromtimestamp(value, tz=UTC)
