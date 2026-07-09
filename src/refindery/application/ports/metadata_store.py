"""Metadata store port: pages, chunks, models, jobs, blacklist.

Grows in later milestones (entities, clusters, tombstones). Implementations
must keep all SQL dialect-neutral outside the adapter.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from refindery.domain.clustering import LineageRecord
from refindery.domain.entities import Entity, EntityType
from refindery.domain.ids import ChunkId, ClusterId, EntityId, JobId, PageId
from refindery.domain.models import (
    BlacklistRule,
    Chunk,
    Cluster,
    ClusterRun,
    EmbeddingModel,
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
)


@dataclass(frozen=True, slots=True)
class PageVectorRow:
    """Stored pooled page vector for a single model."""

    page_id: PageId
    vector: bytes


@dataclass(frozen=True, slots=True)
class ClusterMemberRow:
    """One cluster membership row."""

    page_id: PageId
    probability: float | None


@dataclass(frozen=True, slots=True)
class ChunkStats:
    """Corpus-level chunk count and token total."""

    n_chunks: int
    total_tokens: int


class MetadataStore(Protocol):
    """Transactional metadata + durable job ledger."""

    async def connect(self) -> None:
        """Open connections; idempotent."""
        ...

    async def migrate(self) -> None:
        """Apply pending schema migrations."""
        ...

    async def close(self) -> None:
        """Close connections."""
        ...

    # -- pages ------------------------------------------------------------

    async def insert_page(self, page: Page) -> None:
        """Insert a new page row."""
        ...

    async def get_page(self, page_id: PageId) -> Page | None:
        """Fetch a page by id."""
        ...

    async def get_page_by_canonical_url(self, canonical_url: str) -> Page | None:
        """Fetch a page by canonical URL (revisit detection)."""
        ...

    async def get_pages(self, page_ids: list[PageId]) -> list[Page]:
        """Fetch multiple pages preserving input order; missing ids dropped."""
        ...

    async def record_revisit(self, *, page_id: PageId, seen_at: datetime) -> None:
        """Bump last_seen_at and visit_count."""
        ...

    async def list_page_ids_by_domain(
        self, *, domain: str, limit: int = 20
    ) -> list[PageId]:
        """Page ids for one exact domain, most recently seen first."""
        ...

    async def set_page_status(
        self,
        *,
        page_id: PageId,
        status: PageStatus,
        indexed_at: datetime | None = None,
    ) -> None:
        """Update page lifecycle status."""
        ...

    async def set_page_body(
        self,
        *,
        page_id: PageId,
        body_text: str,
        content_hash: str,
        title: str | None,
    ) -> None:
        """Fill the body after a deferred fetch resolved it."""
        ...

    # -- chunks & page vectors ---------------------------------------------

    async def replace_chunks(self, *, page_id: PageId, chunks: list[Chunk]) -> None:
        """Replace all chunks of a page (canonical chunking)."""
        ...

    async def get_chunks(self, chunk_ids: list[ChunkId]) -> list[Chunk]:
        """Hydrate chunks by id; missing ids dropped."""
        ...

    async def upsert_page_vector(
        self, *, page_id: PageId, model_id: str, vector: bytes
    ) -> None:
        """Store the pooled page vector (float32 bytes) for one model."""
        ...

    async def get_page_vectors(self, *, model_id: str) -> list[PageVectorRow]:
        """All page vectors for one model (clustering / similarity)."""
        ...

    async def clear_index_artifacts(self, page_id: PageId) -> None:
        """Remove chunks and page vectors for a page after a core index failure."""
        ...

    # -- embedding models ---------------------------------------------------

    async def register_model(self, model: EmbeddingModel) -> None:
        """Insert a model registry row."""
        ...

    async def get_model(self, model_id: str) -> EmbeddingModel | None:
        """Fetch one registered model."""
        ...

    async def list_models(
        self, *, statuses: frozenset[ModelStatus] | None = None
    ) -> list[EmbeddingModel]:
        """List registered models, optionally filtered by status."""
        ...

    async def get_active_model(self) -> EmbeddingModel | None:
        """Return the single active model used by /search."""
        ...

    async def set_model_status(self, *, model_id: str, status: ModelStatus) -> None:
        """Update a model's lifecycle status."""
        ...

    async def activate_model(self, model_id: str) -> None:
        """Atomically make this the only active model."""
        ...

    # -- jobs ledger ---------------------------------------------------------

    async def create_job(self, job: Job) -> bool:
        """Insert a ledger row; False when the idempotency key already exists."""
        ...

    async def get_job(self, job_id: JobId) -> Job | None:
        """Fetch one job."""
        ...

    async def list_jobs(
        self, *, status: JobStatus | None = None, limit: int = 100
    ) -> list[Job]:
        """List jobs, newest first."""
        ...

    async def latest_job_for_page(
        self, *, page_id: PageId, kind: JobKind | None = None
    ) -> Job | None:
        """Newest job row for a page payload, optionally restricted by kind."""
        ...

    async def mark_job_running(
        self, *, job_id: JobId, lease_until: datetime, now: datetime
    ) -> None:
        """Transition a job to running with a lease."""
        ...

    async def mark_job_done(self, *, job_id: JobId, now: datetime) -> None:
        """Transition a job to done."""
        ...

    async def mark_job_failed(
        self, *, job_id: JobId, attempts: int, last_error: str, now: datetime
    ) -> None:
        """Record a failed attempt (job stays retryable)."""
        ...

    async def mark_job_dead(
        self, *, job_id: JobId, last_error: str, now: datetime
    ) -> None:
        """Dead-letter a job after attempts are exhausted."""
        ...

    async def reset_job_for_retry(self, *, job_id: JobId, now: datetime) -> Job:
        """Reset a dead job to pending with attempts=0 (manual re-enqueue)."""
        ...

    async def reset_expired_leases(self, *, now: datetime) -> list[Job]:
        """Flip running-past-lease jobs back to pending; return them."""
        ...

    async def list_pending_jobs(self) -> list[Job]:
        """All pending ledger rows (startup recovery re-enqueues them)."""
        ...

    # -- blacklist & forget -----------------------------------------------------

    async def find_blacklist_match(
        self, *, canonical_url: str, domain: str
    ) -> BlacklistRule | None:
        """Return the first blacklist rule matching this URL/domain, if any."""
        ...

    async def purge_and_blacklist(
        self, rule: BlacklistRule
    ) -> tuple[BlacklistRule, list[PageId]]:
        """Atomically: upsert the rule, tombstone + delete matching pages.

        Idempotent on pattern (re-forgetting returns the existing rule).
        Returns the effective rule and the purged page ids. Metadata is
        authoritative immediately; vectors are deleted asynchronously.
        """
        ...

    async def list_blacklist(self) -> list[BlacklistRule]:
        """All blacklist rules, newest first."""
        ...

    async def delete_blacklist(self, blacklist_id: str) -> bool:
        """Remove a rule (does not restore purged content); False if missing."""
        ...

    # -- vector tombstones -------------------------------------------------------

    async def list_tombstones(
        self, *, status: TombstoneStatus, limit: int = 500
    ) -> list[VectorTombstone]:
        """Tombstones in one status, oldest first."""
        ...

    async def set_tombstone_status(
        self,
        *,
        page_ids: list[PageId],
        status: TombstoneStatus,
        now: datetime,
        last_error: str | None = None,
    ) -> None:
        """Advance tombstones."""
        ...

    async def delete_tombstones(self, page_ids: list[PageId]) -> None:
        """Remove tombstones (after verified retention expires)."""
        ...

    # -- entities (M4) ------------------------------------------------------------

    async def find_entity_by_alias(
        self, *, normalized: str, entity_type: EntityType
    ) -> Entity | None:
        """Exact normalized-alias match within a type."""
        ...

    async def entities_in_block(
        self, *, entity_type: EntityType, key: str
    ) -> list[Entity]:
        """Candidate entities sharing (type, first-token block key)."""
        ...

    async def create_entity(
        self, *, entity: Entity, surface_form: str, normalized: str, key: str
    ) -> None:
        """Insert an entity with its first alias."""
        ...

    async def add_alias(
        self, *, entity_id: EntityId, surface_form: str, normalized: str, key: str
    ) -> None:
        """Attach an alias (idempotent on (surface_form, entity_id))."""
        ...

    async def add_mentions(
        self, *, page_id: PageId, linked: list[tuple[EntityId, Mention]]
    ) -> None:
        """Record mentions (idempotent) and refresh affected entity counts."""
        ...

    async def get_entity(self, entity_id: EntityId) -> Entity | None:
        """Fetch one entity."""
        ...

    async def resolve_entity(self, ref: str) -> Entity | None:
        """Resolve id -> canonical form -> alias (merge-stable references)."""
        ...

    async def entity_aliases(self, entity_id: EntityId) -> list[str]:
        """All surface forms of an entity."""
        ...

    async def page_ids_for_entity(self, entity_id: EntityId) -> list[PageId]:
        """Pages mentioning an entity."""
        ...

    async def entities_for_page(self, page_id: PageId) -> list[Entity]:
        """Entities mentioned on a page."""
        ...

    async def entity_blocks_with_duplicates(
        self,
    ) -> list[tuple[EntityType, str, list[EntityId]]]:
        """Blocks holding more than one entity (periodic re-canonicalization)."""
        ...

    async def merge_entities(
        self,
        *,
        source_id: EntityId,
        target_id: EntityId,
        method: str,
        similarity: float | None,
        now: datetime,
    ) -> str:
        """Merge source into target (snapshot logged first); returns merge id."""
        ...

    async def undo_merge(self, merge_id: str, *, now: datetime) -> EntityId:
        """Restore a merged entity (LIFO only); returns the restored id."""
        ...

    async def refresh_entity_idf(self) -> None:
        """Recompute idf = ln(N_pages / page_count) for all entities."""
        ...

    # -- clusters (M4) ---------------------------------------------------------------

    async def upsert_cluster(self, cluster: Cluster) -> None:
        """Insert or update a cluster row.

        Label/keywords are preserved on update when the new values are empty.
        """
        ...

    async def replace_cluster_members(
        self, *, cluster_id: ClusterId, members: list[tuple[PageId, float]]
    ) -> None:
        """Replace a cluster's membership."""
        ...

    async def tombstone_clusters(
        self, cluster_ids: list[ClusterId], *, now: datetime
    ) -> None:
        """Mark clusters tombstoned (rows retained, excluded from listings)."""
        ...

    async def get_cluster(self, cluster_id: ClusterId) -> Cluster | None:
        """Fetch a cluster (tombstoned included — stale refs degrade gracefully)."""
        ...

    async def list_clusters(self, *, include_tombstoned: bool = False) -> list[Cluster]:
        """Live clusters (optionally tombstoned too), largest first."""
        ...

    async def cluster_members(self, cluster_id: ClusterId) -> list[ClusterMemberRow]:
        """Members with soft-membership probability."""
        ...

    async def cluster_for_page(self, page_id: PageId) -> Cluster | None:
        """Return the live cluster containing this page, if any."""
        ...

    async def set_cluster_label(self, *, cluster_id: ClusterId, label: str) -> None:
        """Attach an LLM label."""
        ...

    async def insert_cluster_run(self, run: ClusterRun) -> None:
        """Record a run start."""
        ...

    async def finalize_cluster_run(self, run: ClusterRun) -> None:
        """Record a run's completion stats."""
        ...

    async def insert_lineage(
        self, *, run_id: str, records: list[LineageRecord]
    ) -> None:
        """Record lineage events for a run."""
        ...

    async def recent_run_durations_ms(self, limit: int = 5) -> list[int]:
        """Durations of the most recent finished runs."""
        ...

    async def last_run_finished_at(self) -> datetime | None:
        """When the last cluster run finished."""
        ...

    async def count_indexed_pages(self) -> int:
        """Pages with status=indexed."""
        ...

    async def pages_indexed_since(self, ts: datetime) -> int:
        """Pages indexed after ``ts``."""
        ...

    async def last_ingest_at(self) -> datetime | None:
        """Most recent last_seen_at across pages (idle detection)."""
        ...

    # -- backfills (M5) -------------------------------------------------------------

    async def chunk_stats(self) -> ChunkStats:
        """(n_chunks, total_tokens) over the whole corpus — exact estimate."""
        ...

    async def pages_with_chunks_after(
        self, *, cursor: PageId | None, limit: int = 50
    ) -> list[PageId]:
        """Page ids (with chunks) ordered by id, after the cursor."""
        ...

    async def chunks_for_page(self, page_id: PageId) -> list[Chunk]:
        """All chunks of one page in ordinal order."""
        ...

    async def upsert_backfill(self, backfill: ModelBackfill) -> None:
        """Insert or replace backfill state."""
        ...

    async def get_backfill(self, model_id: str) -> ModelBackfill | None:
        """Fetch backfill state."""
        ...

    async def delete_model(self, model_id: str) -> None:
        """Remove a model row and its page vectors (retire)."""
        ...
