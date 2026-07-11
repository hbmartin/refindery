"""Prometheus metrics: the single home for every metric object.

Plain prometheus-client (most metrics are non-HTTP, an instrumentator would
cover little). Exposed by the authenticated ``GET /metrics`` route (see
``api/routes/health.py``), which renders this registry via ``render_metrics()``.
"""

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

registry = CollectorRegistry()

ingest_pages_total = Counter(
    "refindery_ingest_pages_total",
    "Pages accepted for ingest",
    ["outcome"],  # queued | revisit | blacklisted
    registry=registry,
)
job_failures_total = Counter(
    "refindery_job_failures_total",
    "Job attempts that failed",
    ["kind"],
    registry=registry,
)
queue_depth = Gauge(
    "refindery_queue_depth",
    "Pending jobs in the ledger",
    registry=registry,
)
search_duration_seconds = Histogram(
    "refindery_search_duration_seconds",
    "End-to-end /search latency",
    registry=registry,
)
rerank_duration_seconds = Histogram(
    "refindery_rerank_duration_seconds",
    "Reranker latency per query",
    registry=registry,
)
embedding_api_errors_total = Counter(
    "refindery_embedding_api_errors_total",
    "Embedding provider errors",
    ["provider"],
    registry=registry,
)
cluster_run_duration_seconds = Histogram(
    "refindery_cluster_run_duration_seconds",
    "Cluster run duration",
    registry=registry,
)
query_log_dropped_total = Counter(
    "refindery_query_log_dropped_total",
    "Observability rows dropped under pressure",
    registry=registry,
)
vector_tombstone_backlog = Gauge(
    "refindery_vector_tombstone_backlog",
    "Vector tombstones by status",
    ["status"],
    registry=registry,
)
purged_page_hits_total = Counter(
    "refindery_purged_page_hits_total",
    "Search hits dropped because their page was purged",
    registry=registry,
)
circuit_breaker_state = Gauge(
    "refindery_circuit_breaker_state",
    "Circuit breaker state (0=closed, 1=half_open, 2=open)",
    ["name"],
    registry=registry,
)
circuit_breaker_open_total = Counter(
    "refindery_circuit_breaker_open_total",
    "Times a circuit breaker opened",
    ["name"],
    registry=registry,
)
rerank_degraded_total = Counter(
    "refindery_rerank_degraded_total",
    "Searches served fusion-only because reranking failed",
    registry=registry,
)
job_lease_timeouts_total = Counter(
    "refindery_job_lease_timeouts_total",
    "Jobs cancelled because they exceeded their lease",
    ["kind"],
    registry=registry,
)
jobs_lease_expired = Gauge(
    "refindery_jobs_lease_expired",
    "RUNNING jobs whose lease has expired (watchdog observation)",
    registry=registry,
)


def render_metrics() -> tuple[bytes, str]:
    """(payload, content type) for the /metrics endpoint."""
    return generate_latest(registry), CONTENT_TYPE_LATEST
