"""Pydantic request/response models for the HTTP surface."""

from datetime import datetime
from typing import Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    model_validator,
)

from refindery.application.services.similarity_service import Mediation
from refindery.domain.models import BlacklistKind, JobStatus, PageStatus
from refindery.domain.retrieval import RollupStrategy


class IngestPageRequest(BaseModel):
    """POST /v1/pages body."""

    model_config = ConfigDict(extra="forbid")

    url: str
    title: str | None = None
    body_extracted: str | None = None
    body_html: str | None = None
    fetched_at: AwareDatetime | None = None
    source: str | None = None
    metadata: dict[str, JsonValue] | None = None

    @model_validator(mode="after")
    def _body_xor(self) -> Self:
        if self.body_extracted is not None and self.body_html is not None:
            msg = "body_extracted and body_html are mutually exclusive"
            raise ValueError(msg)
        return self


class IngestAcceptedResponse(BaseModel):
    """202: new page queued."""

    page_id: str
    status: Literal["queued"] = "queued"


class IngestRevisitResponse(BaseModel):
    """200: canonical URL already known."""

    page_id: str
    status: PageStatus
    revisit: Literal[True] = True
    content_hash_differs: bool = False


class BlacklistedResponse(BaseModel):
    """403: URL matches a blacklist rule."""

    error: Literal["blacklisted"] = "blacklisted"
    pattern: str


class PageResponse(BaseModel):
    """GET /v1/pages/{id}."""

    page_id: str
    canonical_url: str
    original_url: str
    domain: str
    title: str | None
    body_text: str | None
    source: str | None
    metadata: dict[str, JsonValue] | None
    first_seen_at: datetime
    last_seen_at: datetime
    visit_count: int
    indexed_at: datetime | None
    status: PageStatus


class PageChunkResponse(BaseModel):
    """One stored chunk with its position in the page body."""

    chunk_id: str
    ordinal: int
    text: str
    token_count: int
    char_start: int
    char_end: int


class PageChunksResponse(BaseModel):
    """GET /v1/pages/{id}/chunks."""

    page_id: str
    chunks: list[PageChunkResponse]


class FeatureStatus(BaseModel):
    """Status of an asynchronous enrichment feature for a page."""

    status: JobStatus | Literal["not_queued"] | None = None
    last_error: str | None = None


class PageStatusFeatures(BaseModel):
    """Nested enrichment feature state."""

    entities: FeatureStatus | None = None


class PageStatusResponse(BaseModel):
    """GET /v1/pages/{id}/status."""

    page_id: str
    status: PageStatus
    last_error: str | None = None
    features: PageStatusFeatures | None = None


class JobResponse(BaseModel):
    """Job ledger row (admin surface)."""

    job_id: str
    kind: str
    status: JobStatus
    attempts: int
    max_attempts: int
    last_error: str | None
    created_at: datetime
    updated_at: datetime


class JobListResponse(BaseModel):
    """GET /v1/jobs."""

    jobs: list[JobResponse]


class SearchFiltersBody(BaseModel):
    """First-class search filters."""

    model_config = ConfigDict(extra="forbid")

    domain: str | None = None
    after: AwareDatetime | None = None
    before: AwareDatetime | None = None
    cluster_id: str | None = None
    entity: str | None = None

    @model_validator(mode="after")
    def _range_ordered(self) -> Self:
        if (
            self.after is not None
            and self.before is not None
            and self.after >= self.before
        ):
            msg = "after must be earlier than before"
            raise ValueError(msg)
        return self


class SearchRequest(BaseModel):
    """POST /v1/search body."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=4_000)
    k: int = Field(default=10, ge=1, le=100)
    offset: int = Field(default=0, ge=0)
    candidates: int = Field(default=100, ge=1, le=1_000)
    rerank: bool = True
    chunks_per_page: int = Field(default=2, ge=0, le=10)
    rollup: RollupStrategy = RollupStrategy.MAX
    rollup_m: int = Field(default=3, ge=1, le=20)
    rrf_k: int = Field(default=60, ge=1)
    suggest: int = Field(default=3, ge=0, le=10)
    mediation: Mediation = Mediation.VECTOR
    recency_half_life_days: float | None = Field(default=None, gt=0)
    filters: SearchFiltersBody | None = None

    @model_validator(mode="after")
    def _candidates_cover_k(self) -> Self:
        # The candidate pool is the only ranking that exists past the final
        # slice, so it must be deep enough to cover the requested page.
        if self.candidates < self.offset + self.k:
            msg = "candidates must be >= offset + k"
            raise ValueError(msg)
        return self


class ClusterRef(BaseModel):
    """Cluster membership shown on a result (populated from M4)."""

    id: str
    label: str | None


class ChunkResult(BaseModel):
    """One matched chunk, whole text."""

    chunk_id: str
    ordinal: int
    text: str
    score: float


class PageResult(BaseModel):
    """One ranked page."""

    page_id: str
    canonical_url: str
    title: str | None
    domain: str
    first_seen_at: datetime
    visit_count: int
    score: float
    cluster: ClusterRef | None = None
    chunks: list[ChunkResult]
    exact_match: bool = False


class Suggestion(BaseModel):
    """A related page appended to the response."""

    page_id: str
    title: str | None
    reason: str


class SearchResponse(BaseModel):
    """POST /v1/search response."""

    query_id: str
    results: list[PageResult]
    offset: int
    has_more: bool
    suggestions: list[Suggestion]
    timing_ms: dict[str, float]


class SimilarResult(BaseModel):
    """One similar page."""

    page_id: str
    canonical_url: str
    title: str | None
    score: float
    reason: str


class SimilarResponse(BaseModel):
    """GET /v1/pages/{id}/similar response."""

    page_id: str
    mediation: Mediation
    results: list[SimilarResult]


class FeedbackRequest(BaseModel):
    """POST /v1/feedback body."""

    model_config = ConfigDict(extra="forbid")

    query_id: str
    page_id: str
    relevant: bool


class ForgetRequest(BaseModel):
    """POST /v1/forget body: exactly one of url/domain."""

    model_config = ConfigDict(extra="forbid")

    url: str | None = None
    domain: str | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def _exactly_one_target(self) -> Self:
        if (self.url is None) == (self.domain is None):
            msg = "provide exactly one of url or domain"
            raise ValueError(msg)
        return self


class ForgetResponse(BaseModel):
    """POST /v1/forget response."""

    blacklist_id: str
    pattern: str
    kind: BlacklistKind
    pages_purged: int
    vector_deletes_queued: int


class BlacklistEntry(BaseModel):
    """One blacklist rule."""

    id: str
    pattern: str
    kind: BlacklistKind
    reason: str | None
    created_at: datetime


class BlacklistResponse(BaseModel):
    """GET /v1/blacklist response."""

    entries: list[BlacklistEntry]


class ClusterSummary(BaseModel):
    """One cluster."""

    id: str
    label: str | None
    keywords: list[str]
    size: int
    tombstoned: bool = False
    projection_x: float | None = None
    projection_y: float | None = None


class ClusterListResponse(BaseModel):
    """GET /v1/clusters response."""

    clusters: list[ClusterSummary]


class ClusterMemberPage(BaseModel):
    """A page inside a cluster."""

    page_id: str
    canonical_url: str
    title: str | None
    probability: float | None


class ClusterDetailResponse(BaseModel):
    """GET /v1/clusters/{id} response."""

    cluster: ClusterSummary
    pages: list[ClusterMemberPage]


class RecomputeResponse(BaseModel):
    """POST /v1/clusters/recompute response."""

    accepted: bool
    detail: str | None = None


class ClusterRunResponse(BaseModel):
    """One persisted clustering run."""

    id: str
    trigger: str
    algorithm: str
    params: dict[str, JsonValue]
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None
    n_pages: int | None
    n_clusters: int | None
    n_noise: int | None


class ClusterRunsResponse(BaseModel):
    """GET /v1/clusters/runs."""

    runs: list[ClusterRunResponse]


class ClusterProjectionPointResponse(BaseModel):
    """A page point in a clustering run's display projection."""

    page_id: str
    x: float
    y: float
    cluster_id: str | None


class ClusterProjectionResponse(BaseModel):
    """GET /v1/clusters/projection."""

    run_id: str
    points: list[ClusterProjectionPointResponse]


class EntitySummary(BaseModel):
    """One canonical entity."""

    id: str
    canonical_form: str
    type: str
    mention_count: int
    page_count: int
    idf: float | None


class EntityDetailResponse(BaseModel):
    """GET /v1/entities/{ref} response."""

    entity: EntitySummary
    aliases: list[str]
    page_ids: list[str]


class PageEntitiesResponse(BaseModel):
    """GET /v1/pages/{id}/entities response."""

    page_id: str
    entities: list[EntitySummary]


class UndoMergeResponse(BaseModel):
    """POST /v1/entities/merges/{id}/undo response."""

    restored_entity_id: str


class RegisterModelRequest(BaseModel):
    """POST /v1/models body."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    model_name: str
    dim: int = Field(ge=1, le=8_192)
    max_input_tokens: int = Field(ge=1)
    id: str | None = None


class ModelInfo(BaseModel):
    """One registered embedding model."""

    id: str
    provider: str
    model_name: str
    dim: int
    max_input_tokens: int
    is_active: bool
    status: str


class ModelListResponse(BaseModel):
    """GET /v1/models response."""

    models: list[ModelInfo]


class BackfillRequest(BaseModel):
    """POST /v1/models/{id}/backfill body."""

    model_config = ConfigDict(extra="forbid")

    confirm: bool = False


class BackfillEstimateResponse(BaseModel):
    """Dry-run estimate (confirm=false)."""

    model_id: str
    n_chunks: int
    total_tokens: int
    est_cost_usd: float | None
    est_duration_s: float | None
    confirm_required: Literal[True] = True


class BackfillStartedResponse(BaseModel):
    """202-style acknowledgement (confirm=true)."""

    model_id: str
    status: Literal["backfilling"] = "backfilling"


class ModelBackfillResponse(BaseModel):
    """GET /v1/models/{id}/backfill."""

    model_id: str
    status: Literal["not_started", "running", "complete", "failed"]
    total_chunks: int
    embedded_chunks: int
    total_tokens: int
    cursor_page_id: str | None
    started_at: datetime | None
    updated_at: datetime | None
    finished_at: datetime | None
    last_error: str | None


class CompareRequest(BaseModel):
    """POST /v1/compare body."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=4_000)
    models: list[str] = Field(min_length=2, max_length=5)
    k: int = Field(default=10, ge=1, le=100)
    candidates: int = Field(default=100, ge=1, le=1_000)
    rerank: bool = True

    @model_validator(mode="after")
    def _unique_models(self) -> Self:
        if len(set(self.models)) != len(self.models):
            msg = "models must be unique"
            raise ValueError(msg)
        if self.candidates < self.k:
            msg = "candidates must be >= k"
            raise ValueError(msg)
        return self


class ComparedPage(BaseModel):
    """One ranked page in a compare arm."""

    page_id: str
    canonical_url: str
    title: str | None
    score: float


class CompareModelRun(BaseModel):
    """One model's ranking."""

    model: str
    results: list[ComparedPage]


class CompareAgreement(BaseModel):
    """Agreement stats for one model pair."""

    model_a: str
    model_b: str
    jaccard_at_k: float
    rbo: float
    kendall_tau: float | None
    intersection_size: int


class CompareResponse(BaseModel):
    """POST /v1/compare response."""

    compare_id: str
    query: str
    runs: list[CompareModelRun]
    agreement: list[CompareAgreement]
