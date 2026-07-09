"""Application settings.

Everything is configurable via environment variables with the ``REFINDERY_``
prefix and ``__`` as the nesting delimiter, e.g.::

    REFINDERY_AUTH_TOKEN=...
    REFINDERY_VECTOR_STORE=lancedb
    REFINDERY_CHUNKING__TARGET_TOKENS=448

Provider API keys use their native variables (``VOYAGE_API_KEY``, ...).
"""

from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from refindery.domain.canonical_url import DEFAULT_TRACKING_PARAMS
from refindery.domain.rollup import PoolingStrategy


class VectorStoreKind(StrEnum):
    """Which vector store adapter to use."""

    QDRANT = "qdrant"
    LANCEDB = "lancedb"


class RerankerKind(StrEnum):
    """Which reranker adapter to use."""

    NONE = "none"
    API = "api"
    LOCAL = "local"


class TraceExporter(StrEnum):
    """Where OpenTelemetry spans are exported."""

    OFF = "off"
    CONSOLE = "console"
    OTLP = "otlp"


class QdrantSettings(BaseModel):
    """Qdrant connection settings."""

    url: str = "http://127.0.0.1:6333"
    api_key: SecretStr | None = None
    collection: str = "refindery_chunks"


class LanceDbSettings(BaseModel):
    """LanceDB storage settings."""

    path: Path = Path("data/lancedb")


class SqliteSettings(BaseModel):
    """SQLite metadata store settings."""

    path: Path = Path("data/refindery.db")


class DuckDbSettings(BaseModel):
    """DuckDB observability sink settings."""

    path: Path = Path("data/observability.duckdb")


class HueySettings(BaseModel):
    """Huey queue storage settings."""

    path: Path = Path("data/huey.db")


class EmbedderSettings(BaseModel):
    """Active embedding model.

    ``dim``/``max_input_tokens`` are authoritative here because not every
    provider SDK exposes them.
    """

    provider: str = "voyage"
    model: str = "voyage-3.5"
    dim: int = Field(default=1024, ge=1)
    max_input_tokens: int = Field(default=32_000, ge=1)


class RerankerSettings(BaseModel):
    """Reranker selection."""

    kind: RerankerKind = RerankerKind.API
    provider: str = "cohere"
    model: str = "rerank-v3.5"


class ChunkingSettings(BaseModel):
    """Canonical chunking parameters (model-independent)."""

    target_tokens: int = Field(default=448, ge=1)
    overlap_tokens: int = Field(default=64, ge=0)
    hard_max_tokens: int = Field(default=512, ge=1)


class CanonicalizeSettings(BaseModel):
    """URL canonicalization overrides."""

    tracking_params: tuple[str, ...] = DEFAULT_TRACKING_PARAMS


class IndexingSettings(BaseModel):
    """Indexing pipeline knobs."""

    page_vector_pooling: PoolingStrategy = PoolingStrategy.MEAN


class FetchSettings(BaseModel):
    """Outbound fetch limits for the fetch_and_index path."""

    timeout_s: float = Field(default=10.0, gt=0)
    max_bytes: int = Field(default=10_000_000, ge=1)


class JobsSettings(BaseModel):
    """Durable job execution parameters."""

    max_attempts: int = Field(default=5, ge=1)
    lease_minutes: int = Field(default=15, ge=1)
    backoff_base_s: float = Field(default=2.0, gt=0)


class McpSettings(BaseModel):
    """MCP server surface configuration."""

    enable_mutating_tools: bool = False


class EntitySettings(BaseModel):
    """Entity extraction and canonicalization configuration."""

    extractor_chain: tuple[Literal["gliner", "spacy", "gazetteer", "llm"], ...] = (
        "gliner",
        "spacy",
        "gazetteer",
    )
    gazetteer_patterns_path: Path | None = None
    surface_embedder: str = "static"
    cosine_threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    edit_distance_threshold: float = Field(default=0.15, ge=0.0, le=1.0)


class ClusterSettings(BaseModel):
    """Clustering configuration."""

    algorithm: Literal["hdbscan", "kmeans"] = "hdbscan"
    reducer: Literal["umap", "pca", "none"] = "umap"
    cron: str | None = None
    min_pages: int = Field(default=50, ge=1)
    min_new_pages: int = Field(default=20, ge=1)
    idle_default_minutes: int = Field(default=15, ge=1)

    @field_validator("cron")
    @classmethod
    def _cron_shape(cls, value: str | None) -> str | None:
        if value is None:
            return None
        fields = value.split()
        if not 1 <= len(fields) <= 5:
            msg = "cluster cron must contain 1 to 5 crontab fields"
            raise ValueError(msg)
        return value


class LlmSettings(BaseModel):
    """Optional OpenAI-compatible endpoint (labels, LLM entity extraction)."""

    base_url: str | None = None
    api_key: SecretStr | None = None
    model: str = "llama3.2"


class ObservabilitySettings(BaseModel):
    """Tracing/logging/metrics configuration."""

    traces: TraceExporter = TraceExporter.OFF
    otlp_endpoint: str | None = None
    json_logs: bool = True


class Settings(BaseSettings):
    """Root settings object; the composition root builds everything from this."""

    model_config = SettingsConfigDict(
        env_prefix="REFINDERY_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    auth_token: SecretStr
    bind_host: str = "127.0.0.1"
    bind_port: int = 8000
    vector_store: VectorStoreKind = VectorStoreKind.QDRANT

    qdrant: QdrantSettings = QdrantSettings()
    lancedb: LanceDbSettings = LanceDbSettings()
    sqlite: SqliteSettings = SqliteSettings()
    duckdb: DuckDbSettings = DuckDbSettings()
    huey: HueySettings = HueySettings()
    embedder: EmbedderSettings = EmbedderSettings()
    reranker: RerankerSettings = RerankerSettings()
    chunking: ChunkingSettings = ChunkingSettings()
    canonicalize: CanonicalizeSettings = CanonicalizeSettings()
    indexing: IndexingSettings = IndexingSettings()
    fetch: FetchSettings = FetchSettings()
    jobs: JobsSettings = JobsSettings()
    mcp: McpSettings = McpSettings()
    entity: EntitySettings = EntitySettings()
    cluster: ClusterSettings = ClusterSettings()
    llm: LlmSettings = LlmSettings()
    observability: ObservabilitySettings = ObservabilitySettings()


def load_settings() -> Settings:
    """Build settings from environment variables and .env.

    Required fields (auth_token) come from the environment; type checkers
    cannot see that, hence the suppressions.
    """
    return Settings()  # type: ignore[call-arg]  # ty: ignore[missing-argument]  # pyrefly: ignore[missing-argument]
