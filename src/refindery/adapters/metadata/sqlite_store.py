"""SQLite (WAL) implementation of the MetadataStore port.

One aiosqlite connection serialized behind an asyncio lock. The only writer
in the process is the single queue consumer plus the API's small transactional
writes, which is exactly the many-readers/one-writer shape WAL serves well.
SQLite-specific SQL is allowed here (and only here).
"""

import json
import sqlite3
from datetime import datetime
from itertools import batched
from pathlib import Path
from types import TracebackType
from typing import Self

import aiosqlite
from pydantic import TypeAdapter
from uuid6 import uuid7

from refindery.adapters.metadata import migrator
from refindery.application.ports.metadata_store import (
    ChunkStats,
    ClusterMemberRow,
    PageVectorRow,
)
from refindery.domain.clustering import LineageRecord
from refindery.domain.entities import Entity, EntityType
from refindery.domain.errors import JobNotFoundError
from refindery.domain.ids import (
    BlacklistId,
    ChunkId,
    ClusterId,
    EntityId,
    JobId,
    PageId,
    WatchId,
)
from refindery.domain.models import (
    BlacklistKind,
    BlacklistRule,
    Chunk,
    Cluster,
    ClusterProjectionCentroid,
    ClusterProjectionPoint,
    ClusterRun,
    EmbeddingModel,
    EvalReplayResult,
    Job,
    JobKind,
    JobStatus,
    Mention,
    ModelBackfill,
    ModelStatus,
    Page,
    PageStatus,
    TombstoneStatus,
    VectorTombstone,
    Watch,
    WatchKind,
    WatchStatus,
)

_PAGE_COLUMNS = (
    "id, canonical_url, original_url, domain, title, body_text, content_hash, "
    "source, metadata, first_seen_at, last_seen_at, visit_count, indexed_at, status"
)
_JOB_COLUMNS = (
    "id, kind, payload, status, idempotency_key, attempts, max_attempts, "
    "lease_until, last_error, created_at, updated_at"
)
_WATCH_COLUMNS = (
    "id, kind, url, title, enabled, interval_hours, config, next_run_at, "
    "last_run_at, last_status, last_error, last_item_count, created_at, updated_at"
)
_SQLITE_VARIABLE_BATCH_SIZE = 999
_WATCH_CONFIG_ADAPTER = TypeAdapter(dict[str, str])


def _ts(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _dt(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


def _page_from_row(row: sqlite3.Row) -> Page:
    return Page(
        id=PageId(row["id"]),
        canonical_url=row["canonical_url"],
        original_url=row["original_url"],
        domain=row["domain"],
        title=row["title"],
        body_text=row["body_text"],
        content_hash=row["content_hash"],
        source=row["source"],
        metadata=None if row["metadata"] is None else json.loads(row["metadata"]),
        first_seen_at=datetime.fromisoformat(row["first_seen_at"]),
        last_seen_at=datetime.fromisoformat(row["last_seen_at"]),
        visit_count=row["visit_count"],
        indexed_at=_dt(row["indexed_at"]),
        status=PageStatus(row["status"]),
    )


def _job_from_row(row: sqlite3.Row) -> Job:
    return Job(
        id=JobId(row["id"]),
        kind=JobKind(row["kind"]),
        payload=json.loads(row["payload"]),
        status=JobStatus(row["status"]),
        idempotency_key=row["idempotency_key"],
        attempts=row["attempts"],
        max_attempts=row["max_attempts"],
        lease_until=_dt(row["lease_until"]),
        last_error=row["last_error"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _watch_from_row(row: sqlite3.Row) -> Watch:
    config = (
        None
        if row["config"] is None
        else _WATCH_CONFIG_ADAPTER.validate_json(row["config"])
    )
    return Watch(
        id=WatchId(row["id"]),
        kind=WatchKind(row["kind"]),
        url=row["url"],
        title=row["title"],
        enabled=bool(row["enabled"]),
        interval_hours=row["interval_hours"],
        config=config,
        next_run_at=datetime.fromisoformat(row["next_run_at"]),
        last_run_at=_dt(row["last_run_at"]),
        last_status=WatchStatus(row["last_status"]),
        last_error=row["last_error"],
        last_item_count=row["last_item_count"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _chunk_from_row(row: sqlite3.Row) -> Chunk:
    return Chunk(
        id=ChunkId(row["id"]),
        page_id=PageId(row["page_id"]),
        ordinal=row["ordinal"],
        text=row["text"],
        token_count=row["token_count"],
        char_start=row["char_start"],
        char_end=row["char_end"],
    )


def _blacklist_from_row(row: sqlite3.Row) -> BlacklistRule:
    return BlacklistRule(
        id=BlacklistId(row["id"]),
        pattern=row["pattern"],
        kind=BlacklistKind(row["kind"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        reason=row["reason"],
    )


def _domain_suffix_match(*, domain: str, pattern: str) -> bool:
    return domain == pattern or domain.endswith(f".{pattern}")


def _escape_like(value: str) -> str:
    """Escape SQL LIKE wildcards while preserving literal dots/hyphens."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _model_from_row(row: sqlite3.Row) -> EmbeddingModel:
    return EmbeddingModel(
        id=row["id"],
        provider=row["provider"],
        model_name=row["model_name"],
        dim=row["dim"],
        max_input_tokens=row["max_input_tokens"],
        is_active=bool(row["is_active"]),
        status=ModelStatus(row["status"]),
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _entity_from_row(row: sqlite3.Row) -> Entity:
    return Entity(
        id=EntityId(row["id"]),
        canonical_form=row["canonical_form"],
        type=EntityType(row["type"]),
        mention_count=row["mention_count"],
        page_count=row["page_count"],
        idf=row["idf"],
    )


def _cluster_from_row(row: sqlite3.Row) -> Cluster:
    return Cluster(
        id=row["id"],
        label=row["label"],
        keywords=[] if row["keywords"] is None else json.loads(row["keywords"]),
        size=row["size"],
        model_id=row["model_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        tombstoned_at=_dt(row["tombstoned_at"]),
        centroid=row["centroid"],
    )


def _cluster_run_from_row(row: sqlite3.Row) -> ClusterRun:
    return ClusterRun(
        id=row["id"],
        trigger=row["trigger_kind"],
        algorithm=row["algorithm"],
        params=json.loads(row["params"]),
        started_at=datetime.fromisoformat(row["started_at"]),
        finished_at=_dt(row["finished_at"]),
        duration_ms=row["duration_ms"],
        n_pages=row["n_pages"],
        n_clusters=row["n_clusters"],
        n_noise=row["n_noise"],
    )


class _EntityClusterMixin:
    """Entity + cluster methods of SqliteMetadataStore (split for readability)."""

    @property
    def conn(self) -> aiosqlite.Connection:
        """Provided by the concrete store."""
        raise NotImplementedError

    # -- entities -----------------------------------------------------------

    async def find_entity_by_alias(
        self, *, normalized: str, entity_type: EntityType
    ) -> Entity | None:
        """Exact normalized-alias match within a type."""
        cursor = await self.conn.execute(
            "SELECT e.* FROM entities e JOIN entity_aliases a ON a.entity_id = e.id "
            "WHERE a.normalized = ? AND e.type = ? LIMIT 1",
            (normalized, entity_type),
        )
        row = await cursor.fetchone()
        return None if row is None else _entity_from_row(row)

    async def entities_in_block(
        self, *, entity_type: EntityType, key: str
    ) -> list[Entity]:
        """Candidate entities sharing (type, block key)."""
        cursor = await self.conn.execute(
            "SELECT DISTINCT e.* FROM entities e "
            "JOIN entity_aliases a ON a.entity_id = e.id "
            "WHERE e.type = ? AND a.block_key = ?",
            (entity_type, key),
        )
        rows = await cursor.fetchall()
        return [_entity_from_row(row) for row in rows]

    async def create_entity(
        self, *, entity: Entity, surface_form: str, normalized: str, key: str
    ) -> None:
        """Insert an entity with its first alias."""
        await self.conn.execute(
            "INSERT INTO entities (id, canonical_form, type, mention_count, "
            "page_count, idf) VALUES (?, ?, ?, ?, ?, ?)",
            (
                entity.id,
                entity.canonical_form,
                entity.type,
                entity.mention_count,
                entity.page_count,
                entity.idf,
            ),
        )
        await self.conn.execute(
            "INSERT INTO entity_aliases (surface_form, normalized, block_key, "
            "entity_id) VALUES (?, ?, ?, ?)",
            (surface_form, normalized, key, entity.id),
        )
        await self.conn.commit()

    async def add_alias(
        self, *, entity_id: EntityId, surface_form: str, normalized: str, key: str
    ) -> None:
        """Attach an alias (idempotent)."""
        await self.conn.execute(
            "INSERT INTO entity_aliases (surface_form, normalized, block_key, "
            "entity_id) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (surface_form, entity_id) DO NOTHING",
            (surface_form, normalized, key, entity_id),
        )
        await self.conn.commit()

    async def add_mentions(
        self, *, page_id: PageId, linked: list[tuple[EntityId, Mention]]
    ) -> None:
        """Record mentions idempotently; refresh affected entity counts."""
        if not linked:
            return
        await self.conn.executemany(
            "INSERT INTO entity_mentions (entity_id, page_id, chunk_id, "
            "surface_form, char_start, char_end) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (entity_id, page_id, surface_form, char_start) DO NOTHING",
            [
                (
                    entity_id,
                    page_id,
                    mention.chunk_id,
                    mention.surface_form,
                    mention.char_start,
                    mention.char_end,
                )
                for entity_id, mention in linked
            ],
        )
        entity_ids = sorted({entity_id for entity_id, _ in linked})
        await self._refresh_counts(entity_ids)
        await self.conn.commit()

    async def _refresh_counts(self, entity_ids: list[EntityId]) -> None:
        for entity_id in entity_ids:
            await self.conn.execute(
                "UPDATE entities SET "
                "mention_count = (SELECT COUNT(*) FROM entity_mentions "
                "WHERE entity_id = ?), "
                "page_count = (SELECT COUNT(DISTINCT page_id) FROM entity_mentions "
                "WHERE entity_id = ?) WHERE id = ?",
                (entity_id, entity_id, entity_id),
            )

    async def get_entity(self, entity_id: EntityId) -> Entity | None:
        """Fetch one entity."""
        cursor = await self.conn.execute(
            "SELECT * FROM entities WHERE id = ?", (entity_id,)
        )
        row = await cursor.fetchone()
        return None if row is None else _entity_from_row(row)

    async def resolve_entity(self, ref: str) -> Entity | None:
        """Resolve id -> canonical form -> alias."""
        if (entity := await self.get_entity(EntityId(ref))) is not None:
            return entity
        cursor = await self.conn.execute(
            "SELECT * FROM entities WHERE canonical_form = ? LIMIT 1", (ref,)
        )
        if (row := await cursor.fetchone()) is not None:
            return _entity_from_row(row)
        cursor = await self.conn.execute(
            "SELECT e.* FROM entities e JOIN entity_aliases a ON a.entity_id = e.id "
            "WHERE a.surface_form = ? OR a.normalized = ? LIMIT 1",
            (ref, ref.casefold()),
        )
        row = await cursor.fetchone()
        return None if row is None else _entity_from_row(row)

    async def entity_aliases(self, entity_id: EntityId) -> list[str]:
        """All surface forms."""
        cursor = await self.conn.execute(
            "SELECT surface_form FROM entity_aliases WHERE entity_id = ? "
            "ORDER BY surface_form",
            (entity_id,),
        )
        rows = await cursor.fetchall()
        return [row["surface_form"] for row in rows]

    async def page_ids_for_entity(self, entity_id: EntityId) -> list[PageId]:
        """Pages mentioning the entity."""
        cursor = await self.conn.execute(
            "SELECT DISTINCT page_id FROM entity_mentions WHERE entity_id = ?",
            (entity_id,),
        )
        rows = await cursor.fetchall()
        return [PageId(row["page_id"]) for row in rows]

    async def entities_for_page(self, page_id: PageId) -> list[Entity]:
        """Entities mentioned on a page."""
        cursor = await self.conn.execute(
            "SELECT DISTINCT e.* FROM entities e "
            "JOIN entity_mentions m ON m.entity_id = e.id WHERE m.page_id = ? "
            "ORDER BY e.mention_count DESC",
            (page_id,),
        )
        rows = await cursor.fetchall()
        return [_entity_from_row(row) for row in rows]

    async def mention_counts_for_page(self, page_id: PageId) -> dict[EntityId, int]:
        """Per-entity mention counts on a page."""
        cursor = await self.conn.execute(
            "SELECT entity_id, COUNT(*) AS n FROM entity_mentions "
            "WHERE page_id = ? GROUP BY entity_id",
            (page_id,),
        )
        rows = await cursor.fetchall()
        return {EntityId(row["entity_id"]): int(row["n"]) for row in rows}

    async def entity_blocks_with_duplicates(
        self,
    ) -> list[tuple[EntityType, str, list[EntityId]]]:
        """Blocks containing more than one entity."""
        cursor = await self.conn.execute(
            "SELECT e.type AS etype, a.block_key AS bkey, "
            "GROUP_CONCAT(DISTINCT e.id) AS ids "
            "FROM entities e JOIN entity_aliases a ON a.entity_id = e.id "
            "GROUP BY e.type, a.block_key "
            "HAVING COUNT(DISTINCT e.id) > 1"
        )
        rows = await cursor.fetchall()
        return [
            (
                EntityType(row["etype"]),
                row["bkey"],
                [EntityId(x) for x in row["ids"].split(",")],
            )
            for row in rows
        ]

    async def merge_entities(
        self,
        *,
        source_id: EntityId,
        target_id: EntityId,
        method: str,
        similarity: float | None,
        now: datetime,
    ) -> str:
        """Merge source into target; snapshot first so it can be undone."""
        source = await self.get_entity(source_id)
        if source is None:
            msg = f"source entity {source_id!r} not found"
            raise ValueError(msg)
        cursor = await self.conn.execute(
            "SELECT surface_form, normalized, block_key FROM entity_aliases "
            "WHERE entity_id = ?",
            (source_id,),
        )
        moved = [
            {
                "surface_form": row["surface_form"],
                "normalized": row["normalized"],
                "block_key": row["block_key"],
            }
            for row in await cursor.fetchall()
        ]
        cursor = await self.conn.execute(
            "SELECT page_id, chunk_id, surface_form, char_start, char_end "
            "FROM entity_mentions WHERE entity_id = ?",
            (source_id,),
        )
        moved_mentions = [
            {
                "page_id": row["page_id"],
                "chunk_id": row["chunk_id"],
                "surface_form": row["surface_form"],
                "char_start": row["char_start"],
                "char_end": row["char_end"],
            }
            for row in await cursor.fetchall()
        ]
        merge_id = str(uuid7())
        await self.conn.execute(
            "INSERT INTO entity_merges (id, source_entity_snapshot, "
            "target_entity_id, moved_aliases, method, similarity, merged_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                merge_id,
                json.dumps(
                    {
                        "id": source.id,
                        "canonical_form": source.canonical_form,
                        "type": source.type,
                    }
                ),
                target_id,
                json.dumps({"aliases": moved, "mentions": moved_mentions}),
                method,
                similarity,
                _ts(now),
            ),
        )
        await self.conn.execute(
            "UPDATE OR IGNORE entity_aliases SET entity_id = ? WHERE entity_id = ?",
            (target_id, source_id),
        )
        await self.conn.execute(
            "DELETE FROM entity_aliases WHERE entity_id = ?", (source_id,)
        )
        await self.conn.execute(
            "INSERT OR IGNORE INTO entity_mentions "
            "(entity_id, page_id, chunk_id, surface_form, char_start, char_end) "
            "SELECT ?, page_id, chunk_id, surface_form, char_start, char_end "
            "FROM entity_mentions WHERE entity_id = ?",
            (target_id, source_id),
        )
        await self.conn.execute(
            "DELETE FROM entity_mentions WHERE entity_id = ?", (source_id,)
        )
        await self.conn.execute("DELETE FROM entities WHERE id = ?", (source_id,))
        await self._recompute_canonical_form(target_id)
        await self._refresh_counts([target_id])
        await self.conn.commit()
        return merge_id

    async def _recompute_canonical_form(self, entity_id: EntityId) -> None:
        cursor = await self.conn.execute(
            "SELECT surface_form FROM entity_mentions WHERE entity_id = ? "
            "GROUP BY surface_form ORDER BY COUNT(*) DESC, surface_form LIMIT 1",
            (entity_id,),
        )
        row = await cursor.fetchone()
        if row is not None:
            await self.conn.execute(
                "UPDATE OR IGNORE entities SET canonical_form = ? WHERE id = ?",
                (row["surface_form"], entity_id),
            )

    async def undo_merge(self, merge_id: str, *, now: datetime) -> EntityId:
        """Restore a merged entity; LIFO only (raises on out-of-order undo)."""
        cursor = await self.conn.execute(
            "SELECT * FROM entity_merges WHERE id = ?", (merge_id,)
        )
        merge = await cursor.fetchone()
        if merge is None or merge["undone_at"] is not None:
            msg = f"merge {merge_id!r} not found or already undone"
            raise ValueError(msg)
        cursor = await self.conn.execute(
            "SELECT COUNT(*) AS later FROM entity_merges "
            "WHERE undone_at IS NULL AND merged_at > ? "
            "AND (target_entity_id = ? OR source_entity_snapshot LIKE ?)",
            (
                merge["merged_at"],
                merge["target_entity_id"],
                f'%"{merge["target_entity_id"]}"%',
            ),
        )
        later = await cursor.fetchone()
        if later is not None and later["later"] > 0:
            msg = "later merges touch this entity; undo newest first"
            raise ValueError(msg)

        snapshot = json.loads(merge["source_entity_snapshot"])
        moved_snapshot = json.loads(merge["moved_aliases"])
        if isinstance(moved_snapshot, dict):
            moved = moved_snapshot.get("aliases", [])
            moved_mentions = moved_snapshot.get("mentions", [])
        else:
            moved = moved_snapshot
            moved_mentions = []
        source_id = EntityId(snapshot["id"])
        target_id = EntityId(merge["target_entity_id"])
        await self.conn.execute(
            "INSERT INTO entities (id, canonical_form, type) VALUES (?, ?, ?)",
            (source_id, snapshot["canonical_form"], snapshot["type"]),
        )
        moved_forms = [alias["surface_form"] for alias in moved]
        for alias in moved:
            await self.conn.execute(
                "UPDATE OR IGNORE entity_aliases SET entity_id = ? "
                "WHERE surface_form = ? AND entity_id = ?",
                (source_id, alias["surface_form"], target_id),
            )
        if moved_mentions:
            for mention in moved_mentions:
                await self.conn.execute(
                    "UPDATE OR IGNORE entity_mentions SET entity_id = ? "
                    "WHERE entity_id = ? AND page_id = ? AND "
                    "(chunk_id IS ? OR chunk_id = ?) AND surface_form = ? AND "
                    "(char_start IS ? OR char_start = ?) AND "
                    "(char_end IS ? OR char_end = ?)",
                    (
                        source_id,
                        target_id,
                        mention["page_id"],
                        mention["chunk_id"],
                        mention["chunk_id"],
                        mention["surface_form"],
                        mention["char_start"],
                        mention["char_start"],
                        mention["char_end"],
                        mention["char_end"],
                    ),
                )
        elif moved_forms:
            placeholders = ",".join("?" for _ in moved_forms)
            await self.conn.execute(
                "UPDATE OR IGNORE entity_mentions SET entity_id = ? "  # noqa: S608
                f"WHERE entity_id = ? AND surface_form IN ({placeholders})",
                [source_id, target_id, *moved_forms],
            )
        await self.conn.execute(
            "UPDATE entity_merges SET undone_at = ? WHERE id = ?",
            (_ts(now), merge_id),
        )
        await self._recompute_canonical_form(target_id)
        await self._refresh_counts([source_id, target_id])
        await self.conn.commit()
        return source_id

    async def refresh_entity_idf(self) -> None:
        """Idf = ln(N_pages / page_count), page_count > 0."""
        cursor = await self.conn.execute("SELECT COUNT(*) AS n FROM pages")
        row = await cursor.fetchone()
        total = row["n"] if row is not None else 0
        if total == 0:
            return
        await self.conn.execute(
            "UPDATE entities SET idf = LN(CAST(? AS REAL) / page_count) "
            "WHERE page_count > 0",
            (total,),
        )
        await self.conn.commit()

    # -- clusters -----------------------------------------------------------

    async def upsert_cluster(self, cluster: Cluster) -> None:
        """Insert/update; empty label/keywords never clobber existing ones."""
        await self.conn.execute(
            "INSERT INTO clusters (id, label, keywords, size, centroid, model_id, "
            "created_at, updated_at, tombstoned_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL) "
            "ON CONFLICT (id) DO UPDATE SET "
            "size = excluded.size, centroid = excluded.centroid, "
            "model_id = excluded.model_id, updated_at = excluded.updated_at, "
            "tombstoned_at = NULL, "
            "label = COALESCE(excluded.label, clusters.label), "
            "keywords = COALESCE(excluded.keywords, clusters.keywords)",
            (
                cluster.id,
                cluster.label,
                json.dumps(cluster.keywords) if cluster.keywords else None,
                cluster.size,
                cluster.centroid,
                cluster.model_id,
                _ts(cluster.created_at),
                _ts(cluster.updated_at),
            ),
        )
        await self.conn.commit()

    async def replace_cluster_members(
        self, *, cluster_id: ClusterId, members: list[tuple[PageId, float]]
    ) -> None:
        """Replace membership."""
        await self.conn.execute(
            "DELETE FROM cluster_members WHERE cluster_id = ?", (cluster_id,)
        )
        await self.conn.executemany(
            "INSERT INTO cluster_members (cluster_id, page_id, probability) "
            "VALUES (?, ?, ?)",
            [(cluster_id, pid, prob) for pid, prob in members],
        )
        await self.conn.commit()

    async def tombstone_clusters(
        self, cluster_ids: list[ClusterId], *, now: datetime
    ) -> None:
        """Tombstone (retain rows)."""
        if not cluster_ids:
            return
        placeholders = ",".join("?" for _ in cluster_ids)
        await self.conn.execute(
            f"UPDATE clusters SET tombstoned_at = ? WHERE id IN ({placeholders})",  # noqa: S608
            [_ts(now), *cluster_ids],
        )
        await self.conn.commit()

    async def get_cluster(self, cluster_id: ClusterId) -> Cluster | None:
        """Fetch one cluster, tombstoned included."""
        cursor = await self.conn.execute(
            "SELECT * FROM clusters WHERE id = ?", (cluster_id,)
        )
        row = await cursor.fetchone()
        return None if row is None else _cluster_from_row(row)

    async def list_clusters(self, *, include_tombstoned: bool = False) -> list[Cluster]:
        """Clusters, largest first."""
        query = "SELECT * FROM clusters"
        if not include_tombstoned:
            query += " WHERE tombstoned_at IS NULL"
        query += " ORDER BY size DESC, id"
        cursor = await self.conn.execute(query)
        rows = await cursor.fetchall()
        return [_cluster_from_row(row) for row in rows]

    async def cluster_members(self, cluster_id: ClusterId) -> list[ClusterMemberRow]:
        """Members with probability."""
        cursor = await self.conn.execute(
            "SELECT page_id, probability FROM cluster_members "
            "WHERE cluster_id = ? ORDER BY probability DESC, page_id",
            (cluster_id,),
        )
        rows = await cursor.fetchall()
        return [
            ClusterMemberRow(
                page_id=PageId(row["page_id"]), probability=row["probability"]
            )
            for row in rows
        ]

    async def cluster_for_page(self, page_id: PageId) -> Cluster | None:
        """Return the live cluster containing this page."""
        cursor = await self.conn.execute(
            "SELECT c.* FROM clusters c "
            "JOIN cluster_members m ON m.cluster_id = c.id "
            "WHERE m.page_id = ? AND c.tombstoned_at IS NULL LIMIT 1",
            (page_id,),
        )
        row = await cursor.fetchone()
        return None if row is None else _cluster_from_row(row)

    async def clusters_for_pages(self, page_ids: list[PageId]) -> dict[PageId, Cluster]:
        """Live cluster per page id; pages without one are absent."""
        clusters: dict[PageId, Cluster] = {}
        unique_page_ids = list(dict.fromkeys(page_ids))
        for batch in batched(
            unique_page_ids, n=_SQLITE_VARIABLE_BATCH_SIZE, strict=False
        ):
            placeholders = ",".join("?" for _ in batch)
            cursor = await self.conn.execute(
                "SELECT m.page_id AS member_page_id, c.* FROM cluster_members m "  # noqa: S608
                "JOIN clusters c ON m.cluster_id = c.id "
                f"WHERE m.page_id IN ({placeholders}) "
                "AND c.tombstoned_at IS NULL "
                "ORDER BY m.probability DESC, c.id",
                parameters=batch,
            )
            rows = await cursor.fetchall()
            for row in rows:
                page_id = PageId(row["member_page_id"])
                clusters.setdefault(page_id, _cluster_from_row(row))
        return clusters

    async def set_cluster_label(self, *, cluster_id: ClusterId, label: str) -> None:
        """Attach a label."""
        await self.conn.execute(
            "UPDATE clusters SET label = ? WHERE id = ?", (label, cluster_id)
        )
        await self.conn.commit()

    async def insert_cluster_run(self, run: ClusterRun) -> None:
        """Record run start."""
        await self.conn.execute(
            "INSERT INTO cluster_runs (id, trigger_kind, algorithm, params, "
            "started_at) VALUES (?, ?, ?, ?, ?)",
            (
                run.id,
                run.trigger,
                run.algorithm,
                json.dumps(run.params),
                _ts(run.started_at),
            ),
        )
        await self.conn.commit()

    async def finalize_cluster_run(self, run: ClusterRun) -> None:
        """Record completion stats."""
        await self.conn.execute(
            "UPDATE cluster_runs SET finished_at = ?, duration_ms = ?, "
            "n_pages = ?, n_clusters = ?, n_noise = ? WHERE id = ?",
            (
                _ts(run.finished_at),
                run.duration_ms,
                run.n_pages,
                run.n_clusters,
                run.n_noise,
                run.id,
            ),
        )
        await self.conn.commit()

    async def list_cluster_runs(self, *, limit: int = 100) -> list[ClusterRun]:
        """List cluster runs newest first."""
        cursor = await self.conn.execute(
            "SELECT * FROM cluster_runs ORDER BY started_at DESC LIMIT ?", (limit,)
        )
        return [_cluster_run_from_row(row) for row in await cursor.fetchall()]

    async def get_cluster_run(self, *, run_id: str) -> ClusterRun | None:
        """Fetch one cluster run through its primary-key index."""
        cursor = await self.conn.execute(
            "SELECT * FROM cluster_runs WHERE id = ?", (run_id,)
        )
        return _cluster_run_from_row(row) if (row := await cursor.fetchone()) else None

    async def insert_cluster_projection(
        self,
        *,
        points: list[ClusterProjectionPoint],
        centroids: list[ClusterProjectionCentroid],
    ) -> None:
        """Persist projection rows atomically."""
        await self.conn.executemany(
            "INSERT INTO cluster_projection_points "
            "(run_id, page_id, x, y, cluster_id) VALUES (?, ?, ?, ?, ?)",
            [(p.run_id, p.page_id, p.x, p.y, p.cluster_id) for p in points],
        )
        await self.conn.executemany(
            "INSERT INTO cluster_projection_centroids "
            "(run_id, cluster_id, x, y) VALUES (?, ?, ?, ?)",
            [(c.run_id, c.cluster_id, c.x, c.y) for c in centroids],
        )
        await self.conn.commit()

    async def get_cluster_projection(
        self, *, run_id: str
    ) -> tuple[list[ClusterProjectionPoint], list[ClusterProjectionCentroid]]:
        """Read projection rows in stable identifier order."""
        cursor = await self.conn.execute(
            "SELECT * FROM cluster_projection_points WHERE run_id = ? ORDER BY page_id",
            (run_id,),
        )
        points = [
            ClusterProjectionPoint(
                run_id=row["run_id"],
                page_id=PageId(row["page_id"]),
                x=row["x"],
                y=row["y"],
                cluster_id=(
                    None if row["cluster_id"] is None else ClusterId(row["cluster_id"])
                ),
            )
            for row in await cursor.fetchall()
        ]
        cursor = await self.conn.execute(
            "SELECT * FROM cluster_projection_centroids WHERE run_id = ? "
            "ORDER BY cluster_id",
            (run_id,),
        )
        centroids = [
            ClusterProjectionCentroid(
                run_id=row["run_id"],
                cluster_id=ClusterId(row["cluster_id"]),
                x=row["x"],
                y=row["y"],
            )
            for row in await cursor.fetchall()
        ]
        return points, centroids

    async def insert_lineage(
        self, *, run_id: str, records: list[LineageRecord]
    ) -> None:
        """Record lineage events."""
        await self.conn.executemany(
            "INSERT INTO cluster_lineage (run_id, event, cluster_id, parent_ids, "
            "jaccard) VALUES (?, ?, ?, ?, ?)",
            [
                (
                    run_id,
                    record.event,
                    record.cluster_id,
                    json.dumps(list(record.parent_ids)),
                    record.jaccard,
                )
                for record in records
            ],
        )
        await self.conn.commit()

    async def recent_run_durations_ms(self, limit: int = 5) -> list[int]:
        """Recent finished-run durations."""
        cursor = await self.conn.execute(
            "SELECT duration_ms FROM cluster_runs WHERE duration_ms IS NOT NULL "
            "ORDER BY started_at DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [row["duration_ms"] for row in rows]

    async def last_run_finished_at(self) -> datetime | None:
        """Most recent run completion."""
        cursor = await self.conn.execute(
            "SELECT MAX(finished_at) AS ts FROM cluster_runs"
        )
        row = await cursor.fetchone()
        return None if row is None else _dt(row["ts"])

    async def count_indexed_pages(self) -> int:
        """Pages with status=indexed."""
        cursor = await self.conn.execute(
            "SELECT COUNT(*) AS n FROM pages WHERE status = ?",
            (PageStatus.INDEXED,),
        )
        row = await cursor.fetchone()
        return 0 if row is None else row["n"]

    async def pages_indexed_since(self, ts: datetime) -> int:
        """Pages indexed after ts."""
        cursor = await self.conn.execute(
            "SELECT COUNT(*) AS n FROM pages WHERE indexed_at > ?", (_ts(ts),)
        )
        row = await cursor.fetchone()
        return 0 if row is None else row["n"]

    async def last_ingest_at(self) -> datetime | None:
        """Most recent last_seen_at."""
        cursor = await self.conn.execute("SELECT MAX(last_seen_at) AS ts FROM pages")
        row = await cursor.fetchone()
        return None if row is None else _dt(row["ts"])

    # -- backfills ------------------------------------------------------------

    async def chunk_stats(self) -> ChunkStats:
        """(n_chunks, total_tokens) over the whole corpus."""
        cursor = await self.conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(token_count), 0) AS toks FROM chunks"
        )
        row = await cursor.fetchone()
        if row is None:
            return ChunkStats(n_chunks=0, total_tokens=0)
        return ChunkStats(n_chunks=row["n"], total_tokens=row["toks"])

    async def pages_with_chunks_after(
        self, *, cursor: PageId | None, limit: int = 50
    ) -> list[PageId]:
        """Pages (with chunks) ordered by id, after the cursor."""
        if cursor is None:
            result = await self.conn.execute(
                "SELECT DISTINCT page_id FROM chunks ORDER BY page_id LIMIT ?",
                (limit,),
            )
        else:
            result = await self.conn.execute(
                "SELECT DISTINCT page_id FROM chunks WHERE page_id > ? "
                "ORDER BY page_id LIMIT ?",
                (cursor, limit),
            )
        rows = await result.fetchall()
        return [PageId(row["page_id"]) for row in rows]

    async def chunks_for_page(self, page_id: PageId) -> list[Chunk]:
        """All chunks of one page in ordinal order."""
        result = await self.conn.execute(
            "SELECT id, page_id, ordinal, text, token_count, char_start, char_end "
            "FROM chunks WHERE page_id = ? ORDER BY ordinal",
            (page_id,),
        )
        rows = await result.fetchall()
        return [_chunk_from_row(row) for row in rows]

    async def upsert_backfill(self, backfill: ModelBackfill) -> None:
        """Insert or replace backfill state."""
        await self.conn.execute(
            "INSERT INTO model_backfills (model_id, cursor_page_id, total_chunks, "
            "embedded_chunks, total_tokens, started_at, updated_at, finished_at, "
            "last_error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (model_id) DO UPDATE SET "
            "cursor_page_id = excluded.cursor_page_id, "
            "embedded_chunks = excluded.embedded_chunks, "
            "updated_at = excluded.updated_at, "
            "finished_at = excluded.finished_at, "
            "last_error = excluded.last_error",
            (
                backfill.model_id,
                backfill.cursor_page_id,
                backfill.total_chunks,
                backfill.embedded_chunks,
                backfill.total_tokens,
                _ts(backfill.started_at),
                _ts(backfill.updated_at),
                _ts(backfill.finished_at),
                backfill.last_error,
            ),
        )
        await self.conn.commit()

    async def get_backfill(self, model_id: str) -> ModelBackfill | None:
        """Fetch backfill state."""
        result = await self.conn.execute(
            "SELECT * FROM model_backfills WHERE model_id = ?", (model_id,)
        )
        row = await result.fetchone()
        if row is None:
            return None
        return ModelBackfill(
            model_id=row["model_id"],
            cursor_page_id=row["cursor_page_id"],
            total_chunks=row["total_chunks"],
            embedded_chunks=row["embedded_chunks"],
            total_tokens=row["total_tokens"],
            started_at=datetime.fromisoformat(row["started_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            finished_at=_dt(row["finished_at"]),
            last_error=row["last_error"],
        )

    async def put_eval_replay_result(self, result: EvalReplayResult) -> None:
        """Persist a replay report or terminal error."""
        await self.conn.execute(
            "INSERT INTO eval_replay_results "
            "(job_id, report, error, created_at, updated_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(job_id) DO UPDATE SET report = excluded.report, "
            "error = excluded.error, updated_at = excluded.updated_at",
            (
                result.job_id,
                None if result.report is None else json.dumps(result.report),
                result.error,
                _ts(result.created_at),
                _ts(result.updated_at),
            ),
        )
        await self.conn.commit()

    async def get_eval_replay_result(self, job_id: JobId) -> EvalReplayResult | None:
        """Fetch a durable replay result."""
        cursor = await self.conn.execute(
            "SELECT * FROM eval_replay_results WHERE job_id = ?", (job_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return EvalReplayResult(
            job_id=JobId(row["job_id"]),
            report=None if row["report"] is None else json.loads(row["report"]),
            error=row["error"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    async def delete_model(self, model_id: str) -> None:
        """Remove a model row and its page vectors."""
        await self.conn.execute(
            "DELETE FROM page_vectors WHERE model_id = ?", (model_id,)
        )
        await self.conn.execute(
            "DELETE FROM model_backfills WHERE model_id = ?", (model_id,)
        )
        await self.conn.execute(
            "DELETE FROM embedding_models WHERE id = ?", (model_id,)
        )
        await self.conn.commit()


class SqliteMetadataStore(_EntityClusterMixin):
    """MetadataStore implementation over one aiosqlite connection."""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path) if isinstance(path, str) else path
        self._conn: aiosqlite.Connection | None = None

    async def __aenter__(self) -> Self:
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    @property
    def conn(self) -> aiosqlite.Connection:
        """The open connection; raises when not connected."""
        if self._conn is None:
            msg = "store is not connected; call connect() first"
            raise RuntimeError(msg)
        return self._conn

    async def connect(self) -> None:
        """Open the connection and apply pragmas; idempotent."""
        if self._conn is not None:
            return
        if str(self._path) != ":memory:":
            self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(self._path)
        conn.row_factory = sqlite3.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA synchronous=NORMAL")
        await conn.execute("PRAGMA foreign_keys=ON")
        await conn.execute("PRAGMA busy_timeout=5000")
        self._conn = conn

    async def migrate(self) -> None:
        """Apply pending schema migrations, then dialect-specific backfills."""
        await migrator.migrate(self.conn)
        await self.conn.execute(
            "UPDATE jobs SET page_id = json_extract(payload, '$.page_id') "
            "WHERE page_id IS NULL "
            "AND json_extract(payload, '$.page_id') IS NOT NULL"
        )
        await self.conn.commit()

    async def close(self) -> None:
        """Close the connection."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    # -- pages ------------------------------------------------------------

    async def insert_page(self, page: Page) -> None:
        """Insert a new page row."""
        await self.conn.execute(
            f"INSERT INTO pages ({_PAGE_COLUMNS}) "  # noqa: S608
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                page.id,
                page.canonical_url,
                page.original_url,
                page.domain,
                page.title,
                page.body_text,
                page.content_hash,
                page.source,
                None if page.metadata is None else json.dumps(page.metadata),
                _ts(page.first_seen_at),
                _ts(page.last_seen_at),
                page.visit_count,
                _ts(page.indexed_at),
                page.status,
            ),
        )
        await self.conn.commit()

    async def get_page(self, page_id: PageId) -> Page | None:
        """Fetch a page by id."""
        cursor = await self.conn.execute(
            f"SELECT {_PAGE_COLUMNS} FROM pages WHERE id = ?",  # noqa: S608
            (page_id,),
        )
        row = await cursor.fetchone()
        return None if row is None else _page_from_row(row)

    async def get_page_by_canonical_url(self, canonical_url: str) -> Page | None:
        """Fetch a page by canonical URL (revisit detection)."""
        cursor = await self.conn.execute(
            f"SELECT {_PAGE_COLUMNS} FROM pages WHERE canonical_url = ?",  # noqa: S608
            (canonical_url,),
        )
        row = await cursor.fetchone()
        return None if row is None else _page_from_row(row)

    async def get_pages(self, page_ids: list[PageId]) -> list[Page]:
        """Fetch multiple pages preserving input order; missing ids dropped."""
        if not page_ids:
            return []
        placeholders = ",".join("?" for _ in page_ids)
        cursor = await self.conn.execute(
            f"SELECT {_PAGE_COLUMNS} FROM pages WHERE id IN ({placeholders})",  # noqa: S608
            page_ids,
        )
        rows = await cursor.fetchall()
        by_id = {row["id"]: _page_from_row(row) for row in rows}
        return [by_id[pid] for pid in page_ids if pid in by_id]

    async def record_revisit(self, *, page_id: PageId, seen_at: datetime) -> None:
        """Bump last_seen_at and visit_count."""
        await self.conn.execute(
            "UPDATE pages SET last_seen_at = ?, visit_count = visit_count + 1 "
            "WHERE id = ?",
            (_ts(seen_at), page_id),
        )
        await self.conn.commit()

    async def list_page_ids_by_domain(
        self, *, domain: str, limit: int = 20, status: PageStatus | None = None
    ) -> list[PageId]:
        """Page ids for one exact domain, most recently seen first."""
        if status is None:
            cursor = await self.conn.execute(
                "SELECT id FROM pages WHERE domain = ? "
                "ORDER BY last_seen_at DESC LIMIT ?",
                (domain, limit),
            )
        else:
            cursor = await self.conn.execute(
                "SELECT id FROM pages WHERE domain = ? AND status = ? "
                "ORDER BY last_seen_at DESC LIMIT ?",
                (domain, status, limit),
            )
        rows = await cursor.fetchall()
        return [PageId(row["id"]) for row in rows]

    async def set_page_status(
        self,
        *,
        page_id: PageId,
        status: PageStatus,
        indexed_at: datetime | None = None,
    ) -> None:
        """Update page lifecycle status."""
        if indexed_at is None:
            await self.conn.execute(
                "UPDATE pages SET status = ? WHERE id = ?", (status, page_id)
            )
        else:
            await self.conn.execute(
                "UPDATE pages SET status = ?, indexed_at = ? WHERE id = ?",
                (status, _ts(indexed_at), page_id),
            )
        await self.conn.commit()

    async def set_page_body(
        self,
        *,
        page_id: PageId,
        body_text: str,
        content_hash: str,
        title: str | None,
    ) -> None:
        """Fill the body after a deferred fetch resolved it."""
        if title is None:
            await self.conn.execute(
                "UPDATE pages SET body_text = ?, content_hash = ? WHERE id = ?",
                (body_text, content_hash, page_id),
            )
        else:
            await self.conn.execute(
                "UPDATE pages SET body_text = ?, content_hash = ?, title = ? "
                "WHERE id = ?",
                (body_text, content_hash, title, page_id),
            )
        await self.conn.commit()

    # -- chunks & page vectors ---------------------------------------------

    async def replace_chunks(self, *, page_id: PageId, chunks: list[Chunk]) -> None:
        """Replace all chunks of a page (canonical chunking)."""
        await self.conn.execute("DELETE FROM chunks WHERE page_id = ?", (page_id,))
        await self.conn.executemany(
            "INSERT INTO chunks "
            "(id, page_id, ordinal, text, token_count, char_start, char_end) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    c.id,
                    c.page_id,
                    c.ordinal,
                    c.text,
                    c.token_count,
                    c.char_start,
                    c.char_end,
                )
                for c in chunks
            ],
        )
        await self.conn.commit()

    async def get_chunks(self, chunk_ids: list[ChunkId]) -> list[Chunk]:
        """Hydrate chunks by id; missing ids dropped."""
        if not chunk_ids:
            return []
        unique_ids = list(dict.fromkeys(chunk_ids))
        by_id: dict[str, Chunk] = {}
        for id_batch in batched(
            unique_ids, n=_SQLITE_VARIABLE_BATCH_SIZE, strict=False
        ):
            placeholders = ",".join("?" for _ in id_batch)
            query = (  # safe: placeholders contain only generated question marks
                f"SELECT id, page_id, ordinal, text, token_count, char_start, "  # noqa: S608
                f"char_end FROM chunks WHERE id IN ({placeholders})"
            )
            cursor = await self.conn.execute(query, id_batch)
            rows = await cursor.fetchall()
            by_id.update({row["id"]: _chunk_from_row(row) for row in rows})
        return [by_id[cid] for cid in chunk_ids if cid in by_id]

    async def upsert_page_vector(
        self, *, page_id: PageId, model_id: str, vector: bytes
    ) -> None:
        """Store the pooled page vector (float32 bytes) for one model."""
        await self.conn.execute(
            "INSERT INTO page_vectors (page_id, model_id, vector) VALUES (?, ?, ?) "
            "ON CONFLICT (page_id, model_id) DO UPDATE SET vector = excluded.vector",
            (page_id, model_id, vector),
        )
        await self.conn.commit()

    async def get_page_vectors(self, *, model_id: str) -> list[PageVectorRow]:
        """All page vectors for one model (clustering / similarity)."""
        cursor = await self.conn.execute(
            "SELECT page_id, vector FROM page_vectors WHERE model_id = ? "
            "ORDER BY page_id",
            (model_id,),
        )
        rows = await cursor.fetchall()
        return [
            PageVectorRow(page_id=PageId(row["page_id"]), vector=row["vector"])
            for row in rows
        ]

    async def clear_index_artifacts(self, page_id: PageId) -> None:
        """Remove chunks and page vectors after a failed core indexing attempt."""
        await self.conn.execute("DELETE FROM chunks WHERE page_id = ?", (page_id,))
        await self.conn.execute(
            "DELETE FROM page_vectors WHERE page_id = ?", (page_id,)
        )
        await self.conn.commit()

    # -- embedding models ---------------------------------------------------

    async def register_model(self, model: EmbeddingModel) -> None:
        """Insert a model registry row."""
        await self.conn.execute(
            "INSERT INTO embedding_models "
            "(id, provider, model_name, dim, max_input_tokens, is_active, status, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                model.id,
                model.provider,
                model.model_name,
                model.dim,
                model.max_input_tokens,
                model.is_active,
                model.status,
                _ts(model.created_at),
            ),
        )
        await self.conn.commit()

    async def get_model(self, model_id: str) -> EmbeddingModel | None:
        """Fetch one registered model."""
        cursor = await self.conn.execute(
            "SELECT * FROM embedding_models WHERE id = ?", (model_id,)
        )
        row = await cursor.fetchone()
        return None if row is None else _model_from_row(row)

    async def list_models(
        self, *, statuses: frozenset[ModelStatus] | None = None
    ) -> list[EmbeddingModel]:
        """List registered models, optionally filtered by status."""
        if statuses is None:
            cursor = await self.conn.execute(
                "SELECT * FROM embedding_models ORDER BY created_at"
            )
        else:
            placeholders = ",".join("?" for _ in statuses)
            cursor = await self.conn.execute(
                "SELECT * FROM embedding_models "  # noqa: S608
                f"WHERE status IN ({placeholders}) ORDER BY created_at",
                sorted(statuses),
            )
        rows = await cursor.fetchall()
        return [_model_from_row(row) for row in rows]

    async def get_active_model(self) -> EmbeddingModel | None:
        """Return the single active model used by /search."""
        cursor = await self.conn.execute(
            "SELECT * FROM embedding_models WHERE is_active"
        )
        row = await cursor.fetchone()
        return None if row is None else _model_from_row(row)

    async def set_model_status(self, *, model_id: str, status: ModelStatus) -> None:
        """Update a model's lifecycle status."""
        await self.conn.execute(
            "UPDATE embedding_models SET status = ? WHERE id = ?",
            (status, model_id),
        )
        await self.conn.commit()

    async def activate_model(self, model_id: str) -> None:
        """Atomically make this the only active model."""
        await self.conn.execute("BEGIN")
        try:
            await self.conn.execute(
                "UPDATE embedding_models SET is_active = 0 WHERE id != ?", (model_id,)
            )
            await self.conn.execute(
                "UPDATE embedding_models SET is_active = 1 WHERE id = ?", (model_id,)
            )
        except Exception:
            await self.conn.rollback()
            raise
        else:
            await self.conn.commit()

    # -- jobs ledger ---------------------------------------------------------

    async def create_job(self, job: Job) -> bool:
        """Insert a ledger row; False when the idempotency key already exists."""
        try:
            await self.conn.execute(
                f"INSERT INTO jobs ({_JOB_COLUMNS}, page_id) "  # noqa: S608
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    job.id,
                    job.kind,
                    json.dumps(job.payload),
                    job.status,
                    job.idempotency_key,
                    job.attempts,
                    job.max_attempts,
                    _ts(job.lease_until),
                    job.last_error,
                    _ts(job.created_at),
                    _ts(job.updated_at),
                    job.payload.get("page_id"),
                ),
            )
        except sqlite3.IntegrityError:
            await self.conn.rollback()
            return False
        await self.conn.commit()
        return True

    async def get_job(self, job_id: JobId) -> Job | None:
        """Fetch one job."""
        cursor = await self.conn.execute(
            f"SELECT {_JOB_COLUMNS} FROM jobs WHERE id = ?",  # noqa: S608
            (job_id,),
        )
        row = await cursor.fetchone()
        return None if row is None else _job_from_row(row)

    async def list_jobs(
        self,
        *,
        status: JobStatus | None = None,
        kind: JobKind | None = None,
        limit: int = 100,
    ) -> list[Job]:
        """List jobs, newest first."""
        clauses: list[str] = []
        values: list[object] = []
        if status is not None:
            clauses.append("status = ?")
            values.append(status)
        if kind is not None:
            clauses.append("kind = ?")
            values.append(kind)
        where = "" if not clauses else f" WHERE {' AND '.join(clauses)}"
        cursor = await self.conn.execute(
            f"SELECT {_JOB_COLUMNS} FROM jobs{where} "  # noqa: S608
            "ORDER BY created_at DESC LIMIT ?",
            (*values, limit),
        )
        rows = await cursor.fetchall()
        return [_job_from_row(row) for row in rows]

    async def latest_job_for_page(
        self, *, page_id: PageId, kind: JobKind | None = None
    ) -> Job | None:
        """Newest job whose payload contains this page id."""
        if kind is None:
            cursor = await self.conn.execute(
                f"SELECT {_JOB_COLUMNS} FROM jobs WHERE page_id = ? "  # noqa: S608
                "ORDER BY created_at DESC LIMIT 1",
                (page_id,),
            )
        else:
            cursor = await self.conn.execute(
                f"SELECT {_JOB_COLUMNS} FROM jobs WHERE page_id = ? AND kind = ? "  # noqa: S608
                "ORDER BY created_at DESC LIMIT 1",
                (page_id, kind),
            )
        row = await cursor.fetchone()
        return None if row is None else _job_from_row(row)

    async def indexed_pages_missing_entity_extraction(self) -> list[Page]:
        """Indexed pages whose current content has no extract_entities job."""
        cursor = await self.conn.execute(
            f"SELECT {_PAGE_COLUMNS} FROM pages WHERE status = ? "  # noqa: S608
            "AND content_hash IS NOT NULL AND NOT EXISTS ("
            "SELECT 1 FROM jobs WHERE jobs.kind = ? AND jobs.page_id = pages.id "
            "AND json_extract(jobs.payload, '$.content_hash') = pages.content_hash"
            ") ORDER BY first_seen_at",
            (PageStatus.INDEXED, JobKind.EXTRACT_ENTITIES),
        )
        rows = await cursor.fetchall()
        return [_page_from_row(row) for row in rows]

    async def mark_job_running(
        self, *, job_id: JobId, lease_until: datetime, now: datetime
    ) -> None:
        """Transition a job to running with a lease."""
        await self.conn.execute(
            "UPDATE jobs SET status = ?, lease_until = ?, updated_at = ? WHERE id = ?",
            (JobStatus.RUNNING, _ts(lease_until), _ts(now), job_id),
        )
        await self.conn.commit()

    async def mark_job_done(self, *, job_id: JobId, now: datetime) -> None:
        """Transition a job to done."""
        await self.conn.execute(
            "UPDATE jobs SET status = ?, lease_until = NULL, updated_at = ? "
            "WHERE id = ?",
            (JobStatus.DONE, _ts(now), job_id),
        )
        await self.conn.commit()

    async def mark_job_failed(
        self, *, job_id: JobId, attempts: int, last_error: str, now: datetime
    ) -> None:
        """Record a failed attempt (job stays retryable)."""
        await self.conn.execute(
            "UPDATE jobs SET status = ?, attempts = ?, last_error = ?, "
            "lease_until = NULL, updated_at = ? WHERE id = ?",
            (JobStatus.FAILED, attempts, last_error, _ts(now), job_id),
        )
        await self.conn.commit()

    async def mark_job_dead(
        self, *, job_id: JobId, last_error: str, now: datetime
    ) -> None:
        """Dead-letter a job after attempts are exhausted."""
        await self.conn.execute(
            "UPDATE jobs SET status = ?, last_error = ?, lease_until = NULL, "
            "updated_at = ? WHERE id = ?",
            (JobStatus.DEAD, last_error, _ts(now), job_id),
        )
        await self.conn.commit()

    async def reset_job_for_retry(self, *, job_id: JobId, now: datetime) -> Job:
        """Reset a dead job to pending with attempts=0 (manual re-enqueue)."""
        await self.conn.execute(
            "UPDATE jobs SET status = ?, attempts = 0, last_error = NULL, "
            "lease_until = NULL, updated_at = ? WHERE id = ?",
            (JobStatus.PENDING, _ts(now), job_id),
        )
        await self.conn.commit()
        job = await self.get_job(job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        return job

    async def reset_expired_leases(self, *, now: datetime) -> list[Job]:
        """Flip running-past-lease jobs back to pending; return them."""
        cursor = await self.conn.execute(
            f"SELECT {_JOB_COLUMNS} FROM jobs "  # noqa: S608
            "WHERE status = ? AND lease_until IS NOT NULL AND lease_until < ?",
            (JobStatus.RUNNING, _ts(now)),
        )
        rows = await cursor.fetchall()
        expired = [_job_from_row(row) for row in rows]
        if expired:
            placeholders = ",".join("?" for _ in expired)
            await self.conn.execute(
                "UPDATE jobs SET status = ?, lease_until = NULL, updated_at = ? "  # noqa: S608
                f"WHERE id IN ({placeholders})",
                [JobStatus.PENDING, _ts(now), *[j.id for j in expired]],
            )
            await self.conn.commit()
        return expired

    async def list_pending_jobs(self) -> list[Job]:
        """All pending ledger rows (startup recovery re-enqueues them)."""
        cursor = await self.conn.execute(
            f"SELECT {_JOB_COLUMNS} FROM jobs WHERE status = ? "  # noqa: S608
            "ORDER BY created_at",
            (JobStatus.PENDING,),
        )
        rows = await cursor.fetchall()
        return [_job_from_row(row) for row in rows]

    async def list_expired_running_jobs(self, *, now: datetime) -> list[Job]:
        """Return running jobs whose lease expired (read-only; watchdog telemetry)."""
        cursor = await self.conn.execute(
            f"SELECT {_JOB_COLUMNS} FROM jobs "  # noqa: S608
            "WHERE status = ? AND lease_until IS NOT NULL AND lease_until < ?",
            (JobStatus.RUNNING, _ts(now)),
        )
        rows = await cursor.fetchall()
        return [_job_from_row(row) for row in rows]

    async def count_jobs_by_status(self) -> dict[JobStatus, int]:
        """Job counts grouped by ledger status (absent statuses omitted)."""
        cursor = await self.conn.execute(
            "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status"
        )
        rows = await cursor.fetchall()
        return {JobStatus(row["status"]): row["n"] for row in rows}

    async def count_tombstones_by_status(self) -> dict[TombstoneStatus, int]:
        """Vector tombstone counts grouped by status (absent statuses omitted)."""
        cursor = await self.conn.execute(
            "SELECT status, COUNT(*) AS n FROM vector_tombstones GROUP BY status"
        )
        rows = await cursor.fetchall()
        return {TombstoneStatus(row["status"]): row["n"] for row in rows}

    # -- watches ---------------------------------------------------------------

    async def create_watch(self, watch: Watch) -> bool:
        """Insert a watch row; False when (kind, url) already exists."""
        cursor = await self.conn.execute(
            f"INSERT INTO watches ({_WATCH_COLUMNS}) "  # noqa: S608
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(kind, url) DO NOTHING",
            (
                watch.id,
                watch.kind,
                watch.url,
                watch.title,
                watch.enabled,
                watch.interval_hours,
                None if watch.config is None else json.dumps(watch.config),
                _ts(watch.next_run_at),
                _ts(watch.last_run_at),
                watch.last_status,
                watch.last_error,
                watch.last_item_count,
                _ts(watch.created_at),
                _ts(watch.updated_at),
            ),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def get_watch(self, watch_id: WatchId) -> Watch | None:
        """Fetch one watch."""
        cursor = await self.conn.execute(
            f"SELECT {_WATCH_COLUMNS} FROM watches WHERE id = ?",  # noqa: S608
            (watch_id,),
        )
        row = await cursor.fetchone()
        return None if row is None else _watch_from_row(row)

    async def list_watches(self) -> list[Watch]:
        """All watches, newest first."""
        cursor = await self.conn.execute(
            f"SELECT {_WATCH_COLUMNS} FROM watches ORDER BY created_at DESC",  # noqa: S608
        )
        rows = await cursor.fetchall()
        return [_watch_from_row(row) for row in rows]

    async def update_watch(self, watch: Watch) -> bool:
        """Full-row update by id; False when the watch does not exist."""
        cursor = await self.conn.execute(
            "UPDATE watches SET title = ?, enabled = ?, interval_hours = ?, "
            "config = ?, next_run_at = ?, last_run_at = ?, last_status = ?, "
            "last_error = ?, last_item_count = ?, updated_at = ? WHERE id = ?",
            (
                watch.title,
                watch.enabled,
                watch.interval_hours,
                None if watch.config is None else json.dumps(watch.config),
                _ts(watch.next_run_at),
                _ts(watch.last_run_at),
                watch.last_status,
                watch.last_error,
                watch.last_item_count,
                _ts(watch.updated_at),
                watch.id,
            ),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def delete_watch(self, watch_id: WatchId) -> bool:
        """Delete a watch; False when it does not exist."""
        cursor = await self.conn.execute(
            "DELETE FROM watches WHERE id = ?",
            (watch_id,),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def list_due_watches(self, *, now: datetime, limit: int) -> list[Watch]:
        """Return enabled watches with next_run_at <= now, most overdue first."""
        cursor = await self.conn.execute(
            f"SELECT {_WATCH_COLUMNS} FROM watches "  # noqa: S608
            "WHERE enabled = 1 AND next_run_at <= ? ORDER BY next_run_at LIMIT ?",
            (_ts(now), limit),
        )
        rows = await cursor.fetchall()
        return [_watch_from_row(row) for row in rows]

    async def mark_watch_run(
        self, *, watch_id: WatchId, last_run_at: datetime, next_run_at: datetime
    ) -> None:
        """Advance the schedule at enqueue time (decoupled from poll outcome)."""
        await self.conn.execute(
            "UPDATE watches SET last_run_at = ?, next_run_at = ?, updated_at = ? "
            "WHERE id = ?",
            (_ts(last_run_at), _ts(next_run_at), _ts(last_run_at), watch_id),
        )
        await self.conn.commit()

    async def record_watch_result(
        self,
        *,
        watch_id: WatchId,
        status: WatchStatus,
        last_error: str | None,
        item_count: int | None,
        now: datetime,
    ) -> None:
        """Record the outcome of a poll."""
        await self.conn.execute(
            "UPDATE watches SET last_status = ?, last_error = ?, "
            "last_item_count = ?, updated_at = ? WHERE id = ?",
            (status, last_error, item_count, _ts(now), watch_id),
        )
        await self.conn.commit()

    # -- forget / blacklist -----------------------------------------------------

    async def purge_and_blacklist(
        self, rule: BlacklistRule
    ) -> tuple[BlacklistRule, list[PageId]]:
        """Atomically upsert the rule, tombstone matching pages, delete them."""
        try:
            await self.conn.execute(
                "INSERT INTO blacklist (id, pattern, kind, reason, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (rule.id, rule.pattern, rule.kind, rule.reason, _ts(rule.created_at)),
            )
            effective = rule
        except sqlite3.IntegrityError:
            cursor = await self.conn.execute(
                "SELECT id, pattern, kind, reason, created_at FROM blacklist "
                "WHERE pattern = ?",
                (rule.pattern,),
            )
            row = await cursor.fetchone()
            if row is None:  # pragma: no cover — conflict implies existence
                raise
            effective = _blacklist_from_row(row)

        if rule.kind is BlacklistKind.URL:
            cursor = await self.conn.execute(
                "SELECT id FROM pages WHERE canonical_url = ?", (rule.pattern,)
            )
        else:
            cursor = await self.conn.execute(
                "SELECT id FROM pages WHERE domain = ? OR domain LIKE ? ESCAPE '\\'",
                (rule.pattern, f"%.{_escape_like(rule.pattern)}"),
            )
        page_ids = [PageId(row["id"]) for row in await cursor.fetchall()]

        if page_ids:
            now = _ts(rule.created_at)
            await self.conn.executemany(
                "INSERT INTO vector_tombstones "
                "(page_id, status, last_error, created_at, updated_at) "
                "VALUES (?, ?, NULL, ?, ?) "
                "ON CONFLICT (page_id) DO UPDATE SET status = excluded.status, "
                "updated_at = excluded.updated_at",
                [(pid, TombstoneStatus.PENDING, now, now) for pid in page_ids],
            )
            placeholders = ",".join("?" for _ in page_ids)
            await self.conn.execute(
                f"DELETE FROM pages WHERE id IN ({placeholders})",  # noqa: S608
                page_ids,
            )
        await self.conn.commit()
        return effective, page_ids

    async def list_blacklist(self) -> list[BlacklistRule]:
        """All blacklist rules, newest first."""
        cursor = await self.conn.execute(
            "SELECT id, pattern, kind, reason, created_at FROM blacklist "
            "ORDER BY created_at DESC"
        )
        rows = await cursor.fetchall()
        return [_blacklist_from_row(row) for row in rows]

    async def delete_blacklist(self, blacklist_id: str) -> bool:
        """Remove a rule; False if missing."""
        cursor = await self.conn.execute(
            "DELETE FROM blacklist WHERE id = ?", (blacklist_id,)
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    # -- vector tombstones --------------------------------------------------------

    async def list_tombstones(
        self, *, status: TombstoneStatus, limit: int = 500
    ) -> list[VectorTombstone]:
        """Tombstones in one status, oldest first."""
        cursor = await self.conn.execute(
            "SELECT page_id, status, last_error, created_at, updated_at "
            "FROM vector_tombstones WHERE status = ? ORDER BY updated_at LIMIT ?",
            (status, limit),
        )
        rows = await cursor.fetchall()
        return [
            VectorTombstone(
                page_id=PageId(row["page_id"]),
                status=TombstoneStatus(row["status"]),
                last_error=row["last_error"],
                created_at=datetime.fromisoformat(row["created_at"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )
            for row in rows
        ]

    async def set_tombstone_status(
        self,
        *,
        page_ids: list[PageId],
        status: TombstoneStatus,
        now: datetime,
        last_error: str | None = None,
    ) -> None:
        """Advance tombstones."""
        if not page_ids:
            return
        placeholders = ",".join("?" for _ in page_ids)
        await self.conn.execute(
            "UPDATE vector_tombstones SET status = ?, last_error = ?, "  # noqa: S608
            f"updated_at = ? WHERE page_id IN ({placeholders})",
            [status, last_error, _ts(now), *page_ids],
        )
        await self.conn.commit()

    async def delete_tombstones(self, page_ids: list[PageId]) -> None:
        """Remove tombstones."""
        if not page_ids:
            return
        placeholders = ",".join("?" for _ in page_ids)
        await self.conn.execute(
            f"DELETE FROM vector_tombstones WHERE page_id IN ({placeholders})",  # noqa: S608
            page_ids,
        )
        await self.conn.commit()

    # -- blacklist matching -------------------------------------------------------

    async def find_blacklist_match(
        self, *, canonical_url: str, domain: str
    ) -> BlacklistRule | None:
        """Return the first blacklist rule matching this URL/domain, if any.

        ``url`` rules match the canonical URL exactly; ``domain`` rules match
        the domain itself or any subdomain of it.
        """
        cursor = await self.conn.execute(
            "SELECT id, pattern, kind, reason, created_at FROM blacklist "
            "WHERE kind = ? AND pattern = ? LIMIT 1",
            (BlacklistKind.URL, canonical_url),
        )
        row = await cursor.fetchone()
        if row is not None:
            return _blacklist_from_row(row)

        cursor = await self.conn.execute(
            "SELECT id, pattern, kind, reason, created_at FROM blacklist "
            "WHERE kind = ? ORDER BY created_at DESC",
            (BlacklistKind.DOMAIN,),
        )
        for row in await cursor.fetchall():
            rule = _blacklist_from_row(row)
            if _domain_suffix_match(domain=domain, pattern=rule.pattern):
                return rule
        return None
