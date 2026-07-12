"""Read-oriented administration endpoints for the web UI."""

# ruff: noqa: D101

import asyncio
from dataclasses import asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, SecretStr

from refindery.adapters.observability.metrics_history import (
    DuckDbMetricsReader,
    MetricSeries,
    current_counters,
    current_gauges,
)
from refindery.adapters.observability.query_log_reader import (
    DetailedLoggedRun,
    DuckDbQueryLogReader,
    LatencyQuantiles,
)
from refindery.api.auth import Principal, require_read, require_write
from refindery.api.deps import get_container
from refindery.application.container import Container
from refindery.application.services.eval_service import EvalService, ScoreReport
from refindery.domain.ids import JobId
from refindery.domain.models import JobKind, JobStatus, TombstoneStatus

router = APIRouter(prefix="/v1/admin", tags=["admin"])
identity_router = APIRouter(prefix="/v1", tags=["auth"])


class WhoAmIResponse(BaseModel):
    """Authenticated token identity and effective scopes."""

    name: str
    scopes: list[str]


class MetricPointResponse(BaseModel):
    ts: datetime
    value: float


class MetricSeriesResponse(BaseModel):
    sample: str
    labels: dict[str, str]
    metric_type: str
    points: list[MetricPointResponse]


class MetricsTimeseriesResponse(BaseModel):
    metric: str
    series: list[MetricSeriesResponse]
    current: list[MetricSeriesResponse]


class LoggedHitResponse(BaseModel):
    chunk_id: str
    page_id: str
    score: float


class LoggedPageResponse(BaseModel):
    page_id: str
    score: float
    rank: int


class QueryLogRunResponse(BaseModel):
    query_id: str
    ts: datetime
    kind: str
    compare_id: str | None
    query_text: str
    params: dict[str, Any]
    active_model: str
    reranker_model: str | None
    candidate_set: list[LoggedHitResponse]
    dense_hits: list[LoggedHitResponse]
    sparse_hits: list[LoggedHitResponse]
    final_pages: list[LoggedPageResponse]
    timing_ms: dict[str, float]
    feedback: dict[str, bool]


class QueryLogListResponse(BaseModel):
    runs: list[QueryLogRunResponse]


class EvalScoreRequest(BaseModel):
    k: int = Field(default=10, ge=1, le=1_000)
    since: datetime | None = None
    model: str | None = None


class EvalReplayRequest(BaseModel):
    model_a: str | None = None
    model_b: str | None = None
    rerank_a: bool = True
    rerank_b: bool = True
    k: int = Field(default=10, ge=1, le=1_000)
    candidates: int = Field(default=100, ge=1, le=10_000)
    limit: int | None = Field(default=None, ge=1, le=10_000)


class EvalReplayAcceptedResponse(BaseModel):
    job_id: str
    result_url: str


class EvalReplayResultResponse(BaseModel):
    job_id: str
    status: JobStatus
    report: dict[str, Any] | None = None
    error: str | None = None


class AdminConfigResponse(BaseModel):
    settings: dict[str, Any]
    mutability: dict[str, Literal["boot_only"]]


class TombstoneBacklogSummary(BaseModel):
    pending: int
    deleted: int
    verified: int


class JobsSummary(BaseModel):
    by_status: dict[JobStatus, int]
    queue_depth: int
    dead: int
    failures_by_kind: dict[str, int]


class SearchLatencySummary(BaseModel):
    since: datetime | None
    runs: int
    p50_ms: float
    p95_ms: float


class BreakerStateSummary(BaseModel):
    name: str
    state: Literal["closed", "half_open", "open"]


class MetricsSummaryResponse(BaseModel):
    generated_at: datetime
    tombstones: TombstoneBacklogSummary
    jobs: JobsSummary
    query_log_dropped_rows: int
    embedding_errors_by_provider: dict[str, int]
    rerank_degraded_total: int
    search_latency: SearchLatencySummary | None
    breakers: list[BreakerStateSummary]


class McpToolResponse(BaseModel):
    name: str
    description: str | None = None
    inputSchema: dict[str, Any] = Field(default_factory=dict)  # noqa: N815 — MCP wire name


class McpAdminResponse(BaseModel):
    tools: list[McpToolResponse]
    enable_mutating_tools: bool


@identity_router.get("/whoami", operation_id="whoami", summary="Identify caller")
async def whoami(
    principal: Annotated[Principal, Depends(require_read)],
) -> WhoAmIResponse:
    """Return the authenticated token name and its effective scopes."""
    return WhoAmIResponse(
        name=principal.name, scopes=sorted(scope.value for scope in principal.scopes)
    )


def _series_response(series: MetricSeries) -> MetricSeriesResponse:
    return MetricSeriesResponse(
        sample=series.sample,
        labels=series.labels,
        metric_type=series.metric_type,
        points=[MetricPointResponse(ts=p.ts, value=p.value) for p in series.points],
    )


@router.get(
    "/metrics/timeseries",
    operation_id="admin_metrics_timeseries",
    summary="Read metric history",
)
async def metrics_timeseries(
    container: Annotated[Container, Depends(get_container)],
    metric: Annotated[str, Query(min_length=1)],
    since: datetime | None = None,
    step: Annotated[float, Query(gt=0)] = 60.0,
) -> MetricsTimeseriesResponse:
    """Read historical rollups plus current gauge values for one metric."""
    reader = DuckDbMetricsReader(container.settings.duckdb.path)
    exists = await asyncio.to_thread(reader.metric_exists, metric)
    if not exists:
        raise HTTPException(status_code=404, detail="metric not found")
    series = await asyncio.to_thread(
        reader.read_series, metric=metric, since=since, step_s=step
    )
    return MetricsTimeseriesResponse(
        metric=metric,
        series=[_series_response(item) for item in series],
        current=[_series_response(item) for item in current_gauges(metric)],
    )


_BREAKER_STATES: dict[float, Literal["closed", "half_open", "open"]] = {
    0.0: "closed",
    1.0: "half_open",
    2.0: "open",
}


def _counter_by_label(metric: str, label: str) -> dict[str, int]:
    return {
        series.labels[label]: int(series.points[0].value)
        for series in current_counters(metric)
        if label in series.labels
    }


def _counter_total(metric: str) -> int:
    return int(sum(series.points[0].value for series in current_counters(metric)))


def _latency_summary(
    container: Container, since: datetime | None
) -> SearchLatencySummary | None:
    try:
        reader = DuckDbQueryLogReader(container.settings.duckdb.path)
    except FileNotFoundError:
        return None
    quantiles: LatencyQuantiles | None = reader.read_latency_quantiles(since=since)
    if quantiles is None:
        return None
    return SearchLatencySummary(
        since=since,
        runs=quantiles.runs,
        p50_ms=quantiles.p50_ms,
        p95_ms=quantiles.p95_ms,
    )


@router.get(
    "/metrics/summary",
    operation_id="admin_metrics_summary",
    summary="Typed operational canaries",
    description=(
        "One JSON snapshot of operational health: job-ledger counts, vector "
        "tombstone backlog, dropped observability rows, provider error "
        "counters, search latency quantiles, and circuit breaker states. "
        "Counter-derived fields are process-lifetime totals that reset on "
        "restart; `since` bounds the latency window only."
    ),
)
async def metrics_summary(
    container: Annotated[Container, Depends(get_container)],
    since: datetime | None = None,
) -> MetricsSummaryResponse:
    """Aggregate typed canaries the UI would otherwise parse from /metrics."""
    job_counts = await container.store.count_jobs_by_status()
    tombstone_counts = await container.store.count_tombstones_by_status()
    latency = await asyncio.to_thread(_latency_summary, container, since)
    return MetricsSummaryResponse(
        generated_at=container.clock.now(),
        tombstones=TombstoneBacklogSummary(
            pending=tombstone_counts.get(TombstoneStatus.PENDING, 0),
            deleted=tombstone_counts.get(TombstoneStatus.DELETED, 0),
            verified=tombstone_counts.get(TombstoneStatus.VERIFIED, 0),
        ),
        jobs=JobsSummary(
            by_status={status: job_counts.get(status, 0) for status in JobStatus},
            queue_depth=job_counts.get(JobStatus.PENDING, 0),
            dead=job_counts.get(JobStatus.DEAD, 0),
            failures_by_kind=_counter_by_label("refindery_job_failures", "kind"),
        ),
        query_log_dropped_rows=container.sink.dropped,
        embedding_errors_by_provider=_counter_by_label(
            "refindery_embedding_api_errors", "provider"
        ),
        rerank_degraded_total=_counter_total("refindery_rerank_degraded"),
        search_latency=latency,
        breakers=[
            BreakerStateSummary(
                name=series.labels.get("name", series.sample),
                state=_BREAKER_STATES.get(series.points[0].value, "closed"),
            )
            for series in current_gauges("refindery_circuit_breaker_state")
        ],
    )


def _query_reader(container: Container) -> DuckDbQueryLogReader:
    try:
        return DuckDbQueryLogReader(container.settings.duckdb.path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _run_response(run: DetailedLoggedRun) -> QueryLogRunResponse:
    return QueryLogRunResponse.model_validate(asdict(run))


@router.get("/query-log", operation_id="admin_query_log", summary="List query-log runs")
async def query_log(
    container: Annotated[Container, Depends(get_container)],
    since: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=1_000)] = 100,
    kind: Annotated[Literal["search", "compare_arm"] | None, Query()] = None,
) -> QueryLogListResponse:
    """List retrieval traces newest first, optionally filtered by time and kind.

    Each trace includes candidate, dense, sparse, and final hit sets, per-stage
    timing, and the latest relevance feedback. The default limit is 100 and the
    maximum is 1,000.
    """
    rows = await asyncio.to_thread(
        _query_reader(container).read_detailed_runs,
        since=since,
        kind=kind,
        limit=limit,
    )
    return QueryLogListResponse(runs=[_run_response(row) for row in rows])


@router.get(
    "/query-log/{query_id}",
    operation_id="admin_query_log_detail",
    summary="Read one query-log run",
)
async def query_log_detail(
    query_id: str,
    container: Annotated[Container, Depends(get_container)],
) -> QueryLogRunResponse:
    """Return one full retrieval trace with rankings, timing, and feedback."""
    rows = await asyncio.to_thread(
        _query_reader(container).read_detailed_runs, query_id=query_id, limit=1
    )
    if not rows:
        raise HTTPException(status_code=404, detail="query not found")
    return _run_response(rows[0])


@router.post(
    "/eval/score", operation_id="admin_eval_score", summary="Score logged runs"
)
async def eval_score(
    body: EvalScoreRequest,
    container: Annotated[Container, Depends(get_container)],
) -> ScoreReport:
    """Score logged rankings synchronously without calling external providers."""
    service = EvalService(reader=_query_reader(container))
    return await asyncio.to_thread(
        service.score_log, k=body.k, since=body.since, model=body.model
    )


@router.post(
    "/eval/replay",
    operation_id="admin_eval_replay",
    summary="Enqueue a live eval replay",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_write)],
)
async def eval_replay(
    body: EvalReplayRequest,
    container: Annotated[Container, Depends(get_container)],
) -> EvalReplayAcceptedResponse:
    """Enqueue a durable two-arm replay that may call paid model providers.

    The response is a 202 with a job ID and result URL. Polling the result needs
    only read scope; the report or failure survives process restarts.
    """
    job_id = await container.admin_eval.enqueue(payload=body.model_dump())
    return EvalReplayAcceptedResponse(
        job_id=job_id, result_url=f"/v1/admin/eval/replay/{job_id}"
    )


@router.get(
    "/eval/replay/{job_id}",
    operation_id="admin_eval_replay_result",
    summary="Poll a live eval replay",
)
async def eval_replay_result(
    job_id: str,
    container: Annotated[Container, Depends(get_container)],
) -> EvalReplayResultResponse:
    """Read durable replay status, its completed report, or terminal failure."""
    job = await container.store.get_job(JobId(job_id))
    if job is None or job.kind is not JobKind.EVAL_REPLAY:
        raise HTTPException(status_code=404, detail="eval replay job not found")
    result = await container.store.get_eval_replay_result(JobId(job_id))
    return EvalReplayResultResponse(
        job_id=job_id,
        status=job.status,
        report=None if result is None else result.report,
        error=(job.last_error if result is None else result.error),
    )


def _redact(value: object) -> object:  # noqa: PLR0911
    match value:
        case SecretStr():
            return "[REDACTED]"
        case BaseModel():
            return {
                name: _redact(getattr(value, name)) for name in type(value).model_fields
            }
        case dict():
            return {str(key): _redact(item) for key, item in value.items()}
        case tuple() | list():
            return [_redact(item) for item in value]
        case Path():
            return str(value)
        case Enum():
            return value.value
        case _:
            return value


def _field_paths(model: BaseModel, *, prefix: str = "") -> list[str]:
    paths: list[str] = []
    for name in type(model).model_fields:
        path = f"{prefix}.{name}" if prefix else name
        value = getattr(model, name)
        if isinstance(value, BaseModel):
            paths.extend(_field_paths(value, prefix=path))
        else:
            paths.append(path)
    return paths


@router.get(
    "/config", operation_id="admin_config", summary="Read effective configuration"
)
async def admin_config(
    container: Annotated[Container, Depends(get_container)],
) -> AdminConfigResponse:
    """Return effective settings with secrets redacted recursively.

    Every setting is marked ``boot_only`` because runtime mutation is not
    supported.
    """
    return AdminConfigResponse(
        settings=cast("dict[str, Any]", _redact(container.settings)),
        mutability=dict.fromkeys(_field_paths(container.settings), "boot_only"),
    )


@router.get("/mcp", operation_id="admin_mcp", summary="Inspect mounted MCP tools")
async def admin_mcp(request: Request) -> McpAdminResponse:
    """Return actual mounted tool metadata and mutating-tool visibility."""
    return McpAdminResponse(
        tools=request.app.state.mcp_tools,
        enable_mutating_tools=request.app.state.enable_mutating_tools,
    )
