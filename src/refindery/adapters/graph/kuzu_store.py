"""Kùzu-backed :class:`GraphStore`: an embedded, derived entity graph.

Kùzu is embedded and synchronous. One ``kuzu.Database`` is held per path for
the process lifetime; a fresh ``kuzu.Connection`` is opened per operation
(connections off a single ``Database`` are safe for concurrent read+write, but
one ``Connection`` must not be shared across threads). All synchronous work
runs in ``asyncio.to_thread``, mirroring the LanceDB adapter.

Opening a second ``Database`` on the same directory while this one is live can
corrupt the store, so exactly one adapter instance owns a given path.
"""

import asyncio
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import cast

import kuzu

from refindery.application.ports.graph_store import PageProjection, SharedEntityPage
from refindery.domain.ids import PageId

_SCHEMA: tuple[str, ...] = (
    "CREATE NODE TABLE IF NOT EXISTS Page("
    "id STRING, domain STRING, first_seen_at STRING, total_idf DOUBLE, "
    "PRIMARY KEY(id))",
    "CREATE NODE TABLE IF NOT EXISTS Entity("
    "id STRING, canonical_form STRING, type STRING, idf DOUBLE, PRIMARY KEY(id))",
    "CREATE REL TABLE IF NOT EXISTS MENTIONS(FROM Page TO Entity, count INT64)",
    "CREATE REL TABLE IF NOT EXISTS CO_OCCURS(FROM Entity TO Entity, count INT64)",
)

# ``total_idf`` (sum of the page's entity IDFs) is denormalized onto the Page
# node so the Jaccard query is an O(1) property lookup per candidate instead of
# a second MENTIONS traversal.
_UPSERT_PAGE = (
    "MERGE (p:Page {id: $pid}) "
    "ON CREATE SET p.domain = $domain, p.first_seen_at = $fs, p.total_idf = $total_idf "
    "ON MATCH SET p.domain = $domain, p.first_seen_at = $fs, p.total_idf = $total_idf"
)
_CLEAR_PAGE_MENTIONS = "MATCH (p:Page {id: $pid})-[r:MENTIONS]->(:Entity) DELETE r"
_UPSERT_ENTITY = (
    "MERGE (e:Entity {id: $eid}) "
    "ON CREATE SET e.canonical_form = $cf, e.type = $type, e.idf = $idf "
    "ON MATCH SET e.canonical_form = $cf, e.type = $type, e.idf = $idf"
)
_LINK_MENTION = (
    "MATCH (p:Page {id: $pid}), (e:Entity {id: $eid}) "
    "MERGE (p)-[r:MENTIONS]->(e) "
    "ON CREATE SET r.count = $count ON MATCH SET r.count = $count"
)
_DELETE_PAGE = "MATCH (p:Page {id: $pid}) DETACH DELETE p"
_RESET: tuple[str, ...] = (
    "MATCH (e:Entity) DETACH DELETE e",
    "MATCH (p:Page) DETACH DELETE p",
)
_CLEAR_CO_OCCURS = "MATCH (:Entity)-[r:CO_OCCURS]->(:Entity) DELETE r"
_REBUILD_CO_OCCURS = (
    "MATCH (p:Page)-[:MENTIONS]->(a:Entity), (p)-[:MENTIONS]->(b:Entity) "
    "WHERE a.id < b.id "
    "WITH a, b, count(*) AS c "
    "MERGE (a)-[r:CO_OCCURS]->(b) ON CREATE SET r.count = c ON MATCH SET r.count = c"
)
# IDF-weighted Jaccard over entity sets, in-graph (mirrors _by_entity). Page
# total IDFs come from the denormalized ``total_idf`` property (no per-candidate
# traversal); ``shared`` is the only aggregate and is isolated in its own WITH
# before any arithmetic (Kùzu rejects a sum() nested in a grouping projection).
_PAGES_SHARING_ENTITIES = (
    "MATCH (src:Page {id: $pid})-[:MENTIONS]->(e:Entity)<-[:MENTIONS]-(o:Page) "
    "WHERE o.id <> $pid "
    "WITH src, o, sum(e.idf) AS shared, count(e) AS shared_n "
    "WITH o.id AS page_id, shared, shared_n, "
    "(src.total_idf + o.total_idf - shared) AS denom "
    "WHERE denom > 0 "
    "RETURN page_id, shared / denom AS score, shared_n AS shared "
    "ORDER BY score DESC, page_id "
    "LIMIT $limit"
)


class KuzuGraphStore:
    """:class:`GraphStore` over an embedded Kùzu database directory.

    The database is opened lazily on first use (``ensure_schema``), never in
    ``__init__``. This keeps the read-only eval bootstrap — which constructs a
    container but never touches the graph — from opening a second ``Database``
    on a path a live server already holds.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._db: kuzu.Database | None = None
        self._lock = threading.Lock()
        self._rebuilding = False

    def _database(self) -> kuzu.Database:
        # Double-checked locking: reads/writes run in asyncio.to_thread worker
        # threads, so the first-use open must not race (two Database instances
        # on one path corrupt the store).
        if self._db is None:
            with self._lock:
                if self._db is None:
                    # Kùzu owns the path (it must not pre-exist as a directory);
                    # only the parent needs to exist.
                    self._path.parent.mkdir(parents=True, exist_ok=True)
                    self._db = kuzu.Database(str(self._path))
        return self._db

    def _write(self, statements: Sequence[tuple[str, dict[str, object]]]) -> None:
        # One transaction per call: atomic (a mid-batch failure rolls back) and
        # a single fsync instead of one per auto-committed statement. Writes are
        # serialized by the single-writer job queue, so no write-write contention.
        conn = kuzu.Connection(self._database())
        conn.execute("BEGIN TRANSACTION")
        try:
            for query, params in statements:
                conn.execute(query, parameters=params)
        except Exception:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")

    def _read(self, query: str, params: dict[str, object]) -> list[list[object]]:
        conn = kuzu.Connection(self._database())
        raw = conn.execute(query, parameters=params)
        # A single-statement query yields one QueryResult (execute may return a
        # list for multi-statement queries, which _read never sends).
        result = cast("kuzu.QueryResult", raw[0] if isinstance(raw, list) else raw)
        rows: list[list[object]] = []
        while result.has_next():
            rows.append(cast("list[object]", result.get_next()))
        return rows

    async def ensure_schema(self) -> None:
        """Create node/rel tables if absent (idempotent)."""
        await asyncio.to_thread(self._write, [(ddl, {}) for ddl in _SCHEMA])

    async def close(self) -> None:
        """Close the embedded database handle (releases the directory lock)."""
        if (db := self._db) is not None:
            self._db = None
            await asyncio.to_thread(db.close)

    async def project_page(self, projection: PageProjection) -> None:
        """Upsert a page, its entities, and a clean rewrite of its MENTIONS."""
        total_idf = sum(ent.idf for ent in projection.entities)
        statements: list[tuple[str, dict[str, object]]] = [
            (
                _UPSERT_PAGE,
                {
                    "pid": projection.page_id,
                    "domain": projection.domain,
                    "fs": projection.first_seen_at.isoformat(),
                    "total_idf": total_idf,
                },
            ),
            (_CLEAR_PAGE_MENTIONS, {"pid": projection.page_id}),
        ]
        for ent in projection.entities:
            statements.append(
                (
                    _UPSERT_ENTITY,
                    {
                        "eid": ent.id,
                        "cf": ent.canonical_form,
                        "type": str(ent.type),
                        "idf": ent.idf,
                    },
                )
            )
            statements.append(
                (
                    _LINK_MENTION,
                    {"pid": projection.page_id, "eid": ent.id, "count": ent.count},
                )
            )
        await asyncio.to_thread(self._write, statements)

    async def delete_pages(self, page_ids: Sequence[PageId]) -> None:
        """Remove pages and their edges (forget/purge)."""
        await asyncio.to_thread(
            self._write, [(_DELETE_PAGE, {"pid": pid}) for pid in page_ids]
        )

    async def reset(self) -> None:
        """Drop all nodes and edges (start of a full rebuild)."""
        await asyncio.to_thread(self._write, [(stmt, {}) for stmt in _RESET])

    async def begin_rebuild(self) -> None:
        """Mark the graph unavailable so reads fall back until the rebuild ends."""
        self._rebuilding = True

    async def end_rebuild(self) -> None:
        """Mark the graph available again after a rebuild finishes."""
        self._rebuilding = False

    async def rebuild_co_occurrence(self) -> None:
        """Recompute CO_OCCURS from the current MENTIONS edges, in-graph."""
        await asyncio.to_thread(
            self._write, [(_CLEAR_CO_OCCURS, {}), (_REBUILD_CO_OCCURS, {})]
        )

    async def pages_sharing_entities(
        self, *, page_id: PageId, limit: int
    ) -> list[SharedEntityPage]:
        """Rank pages by IDF-weighted shared-entity Jaccard with ``page_id``."""
        if self._rebuilding:
            # A rebuild is in flight; an empty result routes the caller to the
            # authoritative entity mediation rather than a half-built graph.
            return []
        rows = await asyncio.to_thread(
            self._read, _PAGES_SHARING_ENTITIES, {"pid": page_id, "limit": limit}
        )
        return [
            SharedEntityPage(
                page_id=PageId(cast("str", row[0])),
                score=cast("float", row[1]),
                shared=cast("int", row[2]),
            )
            for row in rows
        ]
