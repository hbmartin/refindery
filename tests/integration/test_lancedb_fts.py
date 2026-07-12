"""Characterization canary for lancedb 0.34's FTS over unindexed rows.

Pins the behaviors that justify the LanceDB adapter's optimize() strategy
and FTS index config on the locked lancedb version. If a lancedb upgrade
changes any assertion here, revisit ``adapters/vector/lancedb_store.py``:

- the unindexed flat scan silently misses SOME matching rows (observed with
  a repeated-term doc; the trigger is fragment-dependent, not a clean
  "repeated term" rule) -> ``upsert_chunks`` must ``optimize()`` before
  returning;
- deleted rows disappear from FTS results without ``optimize()`` ->
  ``delete_pages`` needs none;
- phrase queries require positions and RAISE on a positionless index ->
  ``ensure_schema`` must build ``FTS(with_position=True,
  remove_stop_words=False)``.
"""

from datetime import UTC, datetime
from pathlib import Path

import lancedb
import numpy as np
import pyarrow as pa
import pytest
from lancedb.index import FTS

from refindery.adapters.vector.lancedb_store import LanceDbVectorStore
from refindery.application.ports.vector_store import ChunkPoint
from refindery.domain.ids import ChunkId, PageId
from refindery.domain.models import EmbeddingModel, ModelStatus

_SCHEMA = pa.schema([pa.field("chunk_id", pa.string()), pa.field("text", pa.string())])

# The exact corpus reproducing the silent miss on lancedb 0.34.0: the
# single-occurrence "zebrafish" row IS found while unindexed, the repeated
# "kumquat" row is NOT. Do not simplify these rows — the miss is
# fragment-dependent and this combination is the pinned repro.
_ROWS = [
    {"chunk_id": "c1", "text": "the zebrafish swims alone tonight"},
    {"chunk_id": "c2", "text": "kumquat kumquat kumquat everywhere kumquat"},
    {"chunk_id": "c3", "text": "an unrelated filler row about nothing"},
]

_ADJACENCY_ROWS = [
    {"chunk_id": "adj", "text": "hexagonal ports and adapters keep the core pure"},
    {"chunk_id": "rev", "text": "ports for the hexagonal system live in the app"},
]


def _table(path: Path, *, config: FTS | None = None) -> lancedb.table.Table:
    path.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(path))
    table = db.create_table("chunks", schema=_SCHEMA)
    table.create_index("text", config=config or FTS(), replace=True)
    return table


def _merge(table: lancedb.table.Table, rows: list[dict[str, str]]) -> None:
    # Mirrors LanceDbVectorStore._merge: the merge_insert upsert path.
    (
        table.merge_insert("chunk_id")
        .when_matched_update_all()
        .when_not_matched_insert_all()
        .execute(rows)
    )


def _found(table: lancedb.table.Table, query: str) -> list[str]:
    hits = table.search(query, query_type="fts").limit(5).to_list()
    return [hit["chunk_id"] for hit in hits]


def _unindexed_rows(table: lancedb.table.Table) -> int:
    stats = table.index_stats("text_idx")
    assert stats is not None
    return stats.num_unindexed_rows


def test_unindexed_rows_can_be_silently_missed(tmp_path):
    table = _table(tmp_path / "lance")
    _merge(table, _ROWS)
    assert _unindexed_rows(table) == 3

    # The flat scan finds one unindexed row but silently misses the other:
    # this is why upsert_chunks optimize()s before returning. If the kumquat
    # assertion ever fails after a lancedb bump, the flat scan got fixed —
    # revisit the per-upsert optimize().
    assert _found(table, "zebrafish") == ["c1"]
    assert _found(table, "kumquat") == []

    table.optimize()
    assert _unindexed_rows(table) == 0
    assert _found(table, "zebrafish") == ["c1"]
    assert _found(table, "kumquat") == ["c2"]


def test_delete_is_visible_without_optimize(tmp_path):
    table = _table(tmp_path / "lance")
    _merge(table, _ROWS)
    table.optimize()
    assert _found(table, "kumquat") == ["c2"]

    table.delete("chunk_id = 'c2'")

    # Deleted rows are masked at query time with no further optimize() —
    # which is why delete_pages performs none.
    assert _found(table, "kumquat") == []


def test_replace_index_rebuilds_existing_rows(tmp_path):
    table = _table(tmp_path / "lance")
    _merge(table, _ROWS)
    table.optimize()

    table.create_index(
        "text",
        config=FTS(with_position=True, remove_stop_words=False),
        replace=True,
    )

    # The rebuild is synchronous and complete: no unindexed remainder, rows
    # stay searchable, and the live config is introspectable — the adapter's
    # ensure_schema migration relies on all three.
    assert _unindexed_rows(table) == 0
    assert _found(table, "kumquat") == ["c2"]
    details = next(
        index.index_details
        for index in table.list_indices()
        if index.name == "text_idx"
    )
    assert details is not None
    assert details.get("with_position") is True
    assert details.get("remove_stop_words") is False


def test_phrase_queries_require_positions(tmp_path):
    positionless = _table(tmp_path / "a")
    _merge(positionless, _ADJACENCY_ROWS)
    positionless.optimize()
    # A balanced quoted phrase in a user query raises on the default
    # positionless index — the adapter must never build one.
    with pytest.raises(RuntimeError, match="position"):
        _found(positionless, '"hexagonal ports"')

    positioned = _table(
        tmp_path / "b", config=FTS(with_position=True, remove_stop_words=False)
    )
    _merge(positioned, _ADJACENCY_ROWS)
    positioned.optimize()
    assert _found(positioned, '"hexagonal ports"') == ["adj"]
    assert _found(positioned, '"ports hexagonal"') == []
    assert set(_found(positioned, "hexagonal ports")) == {"adj", "rev"}


# -- adapter-level behavior (LanceDbVectorStore drives the same lancedb) -----

_MODEL = EmbeddingModel(
    id="model-a",
    provider="fake",
    model_name="model-a",
    dim=4,
    max_input_tokens=32_000,
    is_active=True,
    status=ModelStatus.READY,
    created_at=datetime(2026, 1, 1, tzinfo=UTC),
)


def _chunk_point(page: str, text: str) -> ChunkPoint:
    return ChunkPoint(
        chunk_id=ChunkId(f"{page}:0"),
        page_id=PageId(page),
        ordinal=0,
        text=text,
        vectors={_MODEL.id: np.zeros(4, dtype=np.float32)},
        domain=f"{page}.example",
        first_seen_at=datetime(2026, 6, 1, tzinfo=UTC),
        cluster_id=None,
    )


async def test_adapter_quoted_phrase_matches_adjacency_only(tmp_path):
    store = LanceDbVectorStore(path=tmp_path / "lance")
    await store.ensure_schema([_MODEL])
    await store.upsert_chunks(
        [
            _chunk_point("adj", "hexagonal ports and adapters keep the core pure"),
            _chunk_point("rev", "ports for the hexagonal system live in the app"),
        ]
    )

    hits = await store.sparse_query(text='"hexagonal ports"', limit=5)

    assert [hit.page_id for hit in hits] == ["adj"]
    await store.close()


async def test_ensure_schema_migrates_positionless_index(tmp_path):
    store = LanceDbVectorStore(path=tmp_path / "lance")
    await store.ensure_schema([_MODEL])
    await store.upsert_chunks(
        [_chunk_point("adj", "hexagonal ports and adapters keep the core pure")]
    )
    # Simulate an install created before the phrase-capable config; a second
    # connection avoids reaching into the adapter's internals.
    chunks = lancedb.connect(str(tmp_path / "lance")).open_table("chunks")
    chunks.create_index("text", config=FTS(), replace=True)
    with pytest.raises(RuntimeError, match="position"):
        await store.sparse_query(text='"hexagonal ports"', limit=5)

    await store.ensure_schema([_MODEL])  # detects the downgrade and rebuilds

    # Re-open: the pre-migration handle stays pinned to its opened version.
    rebuilt = lancedb.connect(str(tmp_path / "lance")).open_table("chunks")
    details = next(
        index.index_details
        for index in rebuilt.list_indices()
        if index.name == "text_idx"
    )
    assert details is not None
    assert details.get("with_position") is True
    hits = await store.sparse_query(text='"hexagonal ports"', limit=5)
    assert [hit.page_id for hit in hits] == ["adj"]
    await store.close()
