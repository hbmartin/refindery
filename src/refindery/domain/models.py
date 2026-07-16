"""Core domain entities and their status enums.

These are plain dataclasses, not pydantic models: rows come from our own
migrations, so re-validating them per row buys nothing. Pydantic lives at
trust boundaries (HTTP requests/responses, fetched content, settings).
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from refindery.domain.ids import (
    BlacklistId,
    ChunkId,
    ClusterId,
    JobId,
    PageId,
    WatchId,
)

MAX_WATCH_INTERVAL_HOURS = 8_760


class PageStatus(StrEnum):
    """Lifecycle of a page through the indexing pipeline."""

    QUEUED = "queued"
    INDEXING = "indexing"
    INDEXED = "indexed"
    FAILED = "failed"
    DEAD = "dead"


class ModelStatus(StrEnum):
    """Lifecycle of a registered embedding model."""

    REGISTERED = "registered"
    BACKFILLING = "backfilling"
    READY = "ready"
    RETIRED = "retired"


class JobStatus(StrEnum):
    """Ledger status of a durable job."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    DEAD = "dead"


class JobKind(StrEnum):
    """Kinds of durable jobs the queue executes."""

    INDEX_PAGE = "index_page"
    FETCH_AND_INDEX = "fetch_and_index"
    EXTRACT_ENTITIES = "extract_entities"
    BACKFILL_MODEL = "backfill_model"
    CLUSTER = "cluster"
    LABEL_CLUSTERS = "label_clusters"
    CANONICALIZE_ENTITIES = "canonicalize_entities"
    PURGE_VECTORS = "purge_vectors"
    EVAL_REPLAY = "eval_replay"
    POLL_WATCH = "poll_watch"


class BlacklistKind(StrEnum):
    """Whether a blacklist rule matches a URL exactly or a domain suffix."""

    URL = "url"
    DOMAIN = "domain"


class WatchKind(StrEnum):
    """Kinds of pull sources a watch can poll."""

    RSS = "rss"
    YOUTUBE = "youtube"
    PODCAST = "podcast"


class WatchStatus(StrEnum):
    """Outcome of a watch's most recent poll."""

    PENDING = "pending"
    OK = "ok"
    ERROR = "error"


@dataclass(slots=True)
class Page:
    """One canonical web page. One row per canonical_url; never versioned.

    ``body_text`` and ``content_hash`` are ``None`` only while a
    ``fetch_and_index`` job is still resolving the body.
    """

    id: PageId
    canonical_url: str
    original_url: str
    domain: str
    title: str | None
    body_text: str | None
    content_hash: str | None
    source: str | None
    metadata: dict[str, object] | None
    first_seen_at: datetime
    last_seen_at: datetime
    visit_count: int
    indexed_at: datetime | None
    status: PageStatus


@dataclass(frozen=True, slots=True)
class Section:
    """A titled, contiguous span of a page's body text (e.g. a podcast chapter).

    ``char_start``/``char_end`` index into the page body text; ``start_time_s``
    is the source timestamp (podcast chapter start), when the section derives
    from timed media.
    """

    title: str | None
    char_start: int
    char_end: int
    start_time_s: float | None = None


@dataclass(frozen=True, slots=True)
class Chunk:
    """A canonical, model-independent span of a page's body text.

    ``section_title``/``section_start_s`` label the chunk with the chapter it
    belongs to when the page was chunked along section boundaries; both are
    ``None`` for ordinary flat chunking.
    """

    id: ChunkId
    page_id: PageId
    ordinal: int
    text: str
    token_count: int
    char_start: int
    char_end: int
    section_title: str | None = None
    section_start_s: float | None = None


@dataclass(frozen=True, slots=True)
class EmbeddingModel:
    """A registered embedding model and its vector space."""

    id: str
    provider: str
    model_name: str
    dim: int
    max_input_tokens: int
    is_active: bool
    status: ModelStatus
    created_at: datetime


@dataclass(slots=True)
class Job:
    """Durable job ledger row; huey executes, this row is the source of truth."""

    id: JobId
    kind: JobKind
    payload: dict[str, str]
    status: JobStatus
    idempotency_key: str
    created_at: datetime
    updated_at: datetime
    attempts: int = 0
    max_attempts: int = 5
    lease_until: datetime | None = None
    last_error: str | None = None


@dataclass(frozen=True, slots=True)
class BlacklistRule:
    """A forget rule: exact canonical URL or domain suffix."""

    id: BlacklistId
    pattern: str
    kind: BlacklistKind
    created_at: datetime
    reason: str | None = None


@dataclass(slots=True)
class Watch:
    """A pull source polled on its own schedule; discovered URLs are ingested.

    ``next_run_at`` is advanced by the scheduling tick at enqueue time, never
    by the poll handler, so a permanently failing poll cannot freeze the
    schedule. ``config`` holds per-kind options (e.g. ``max_entries``).
    """

    id: WatchId
    kind: WatchKind
    url: str
    title: str | None
    enabled: bool
    interval_hours: int
    config: dict[str, str] | None
    next_run_at: datetime
    last_run_at: datetime | None
    last_status: WatchStatus
    last_error: str | None
    last_item_count: int | None
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True)
class Cluster:
    """A stable-id cluster of pages."""

    id: str
    label: str | None
    keywords: list[str]
    size: int
    model_id: str
    created_at: datetime
    updated_at: datetime
    tombstoned_at: datetime | None = None
    centroid: bytes | None = None


@dataclass(slots=True)
class ClusterRun:
    """One clustering run's record."""

    id: str
    trigger: str
    algorithm: str
    params: dict[str, object]
    started_at: datetime
    finished_at: datetime | None = None
    duration_ms: int | None = None
    n_pages: int | None = None
    n_clusters: int | None = None
    n_noise: int | None = None


@dataclass(frozen=True, slots=True)
class ClusterProjectionPoint:
    """A page's persisted two-dimensional position for one cluster run."""

    run_id: str
    page_id: PageId
    x: float
    y: float
    cluster_id: ClusterId | None


@dataclass(frozen=True, slots=True)
class ClusterProjectionCentroid:
    """A cluster's centroid in the same projection space as its pages."""

    run_id: str
    cluster_id: ClusterId
    x: float
    y: float


@dataclass(frozen=True, slots=True)
class EvalReplayResult:
    """Durable serialized output or failure for an eval replay job."""

    job_id: JobId
    report: dict[str, object] | None
    error: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True)
class ModelBackfill:
    """Resumable backfill state for one model."""

    model_id: str
    total_chunks: int
    total_tokens: int
    started_at: datetime
    updated_at: datetime
    cursor_page_id: str | None = None
    embedded_chunks: int = 0
    finished_at: datetime | None = None
    last_error: str | None = None


class TombstoneStatus(StrEnum):
    """Lifecycle of a purged page's vector-deletion tombstone."""

    PENDING = "pending"
    DELETED = "deleted"
    VERIFIED = "verified"


@dataclass(frozen=True, slots=True)
class VectorTombstone:
    """A purged page whose vectors must be (verifiably) deleted."""

    page_id: PageId
    status: TombstoneStatus
    created_at: datetime
    updated_at: datetime
    last_error: str | None = None


@dataclass(frozen=True, slots=True)
class ExtractedContent:
    """Output of a content extractor: markdown body plus optional title.

    ``sections`` carries structural boundaries (e.g. podcast chapters) discovered
    during extraction; ``None`` means "no structure", and chunking falls back to
    the flat sentence-based path.
    """

    body_text: str
    title: str | None = None
    sections: tuple[Section, ...] | None = None


@dataclass(frozen=True, slots=True)
class Mention:
    """A single entity mention found in page text."""

    surface_form: str
    type: str
    char_start: int | None = None
    char_end: int | None = None
    chunk_id: ChunkId | None = None


@dataclass(frozen=True, slots=True)
class IngestQueued:
    """Outcome: a new page was accepted and queued for indexing."""

    page_id: PageId


@dataclass(frozen=True, slots=True)
class IngestRevisit:
    """Outcome: the canonical URL was already known; visit recorded."""

    page_id: PageId
    status: PageStatus
    content_hash_differs: bool


@dataclass(frozen=True, slots=True)
class IngestBlacklisted:
    """Outcome: the URL matched a blacklist rule; nothing stored."""

    pattern: str


IngestOutcome = IngestQueued | IngestRevisit | IngestBlacklisted
