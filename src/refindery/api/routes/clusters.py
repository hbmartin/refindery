"""Cluster endpoints."""

from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query, status

from refindery.api.auth import require_write
from refindery.api.deps import get_container
from refindery.api.schemas import (
    ClusterDetailResponse,
    ClusterListResponse,
    ClusterMemberPage,
    ClusterProjectionPointResponse,
    ClusterProjectionResponse,
    ClusterRunResponse,
    ClusterRunsResponse,
    ClusterSummary,
    RecomputeResponse,
)
from refindery.application.container import Container
from refindery.application.services.clustering_run import ClusterRunInFlightError
from refindery.domain.ids import ClusterId
from refindery.domain.models import Cluster

router = APIRouter(prefix="/v1/clusters", tags=["clusters"])


def _summary(
    cluster: Cluster, *, centroid: tuple[float, float] | None = None
) -> ClusterSummary:
    return ClusterSummary(
        id=cluster.id,
        label=cluster.label,
        keywords=cluster.keywords,
        size=cluster.size,
        tombstoned=cluster.tombstoned_at is not None,
        projection_x=None if centroid is None else centroid[0],
        projection_y=None if centroid is None else centroid[1],
    )


async def _latest_centroids(container: Container) -> dict[str, tuple[float, float]]:
    runs = await container.store.list_cluster_runs(limit=1)
    if not runs:
        return {}
    _, centroids = await container.store.get_cluster_projection(run_id=runs[0].id)
    return {str(item.cluster_id): (item.x, item.y) for item in centroids}


@router.get(
    "",
    operation_id="list_clusters",
    summary="List reading-topic clusters",
    description=(
        "Topic clusters discovered over the user's reading history. Returns "
        "grounded results derived only from pages the user has read."
    ),
)
async def list_clusters(
    container: Annotated[Container, Depends(get_container)],
    include_tombstoned: Annotated[bool, Query()] = False,  # noqa: FBT002 — FastAPI query param
) -> ClusterListResponse:
    """Live clusters, largest first."""
    clusters = await container.store.list_clusters(
        include_tombstoned=include_tombstoned
    )
    centroids = await _latest_centroids(container)
    return ClusterListResponse(
        clusters=[_summary(c, centroid=centroids.get(c.id)) for c in clusters]
    )


@router.get("/runs", operation_id="list_cluster_runs", summary="List clustering runs")
async def list_cluster_runs(
    container: Annotated[Container, Depends(get_container)],
    limit: Annotated[int, Query(ge=1, le=1_000)] = 100,
) -> ClusterRunsResponse:
    """Return persisted clustering history newest first."""
    runs = await container.store.list_cluster_runs(limit=limit)
    return ClusterRunsResponse(
        runs=[
            ClusterRunResponse(
                id=run.id,
                trigger=run.trigger,
                algorithm=run.algorithm,
                params=cast("dict[str, Any]", run.params),
                started_at=run.started_at,
                finished_at=run.finished_at,
                duration_ms=run.duration_ms,
                n_pages=run.n_pages,
                n_clusters=run.n_clusters,
                n_noise=run.n_noise,
            )
            for run in runs
        ]
    )


@router.get(
    "/projection",
    operation_id="cluster_projection",
    summary="Read a run's two-dimensional page projection",
)
async def cluster_projection(
    container: Annotated[Container, Depends(get_container)],
    run_id: Annotated[str, Query(min_length=1)],
) -> ClusterProjectionResponse:
    """Return page coordinates for a persisted clustering run."""
    if await container.store.get_cluster_run(run_id=run_id) is None:
        raise HTTPException(status_code=404, detail="cluster run not found")
    points, _ = await container.store.get_cluster_projection(run_id=run_id)
    return ClusterProjectionResponse(
        run_id=run_id,
        points=[
            ClusterProjectionPointResponse(
                page_id=point.page_id,
                x=point.x,
                y=point.y,
                cluster_id=point.cluster_id,
            )
            for point in points
        ],
    )


@router.get(
    "/{cluster_id}",
    operation_id="cluster_pages",
    summary="A cluster's label, keywords, and member pages",
    description=(
        "Member pages of one topic cluster from the user's reading history. "
        "Tombstoned clusters remain resolvable by id."
    ),
)
async def cluster_pages(
    cluster_id: str,
    container: Annotated[Container, Depends(get_container)],
) -> ClusterDetailResponse:
    """Cluster detail with members."""
    cluster = await container.store.get_cluster(ClusterId(cluster_id))
    if cluster is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="cluster not found"
        )
    members = await container.store.cluster_members(ClusterId(cluster_id))
    pages = await container.store.get_pages([member.page_id for member in members])
    probability = {member.page_id: member.probability for member in members}
    return ClusterDetailResponse(
        cluster=_summary(
            cluster, centroid=(await _latest_centroids(container)).get(cluster.id)
        ),
        pages=[
            ClusterMemberPage(
                page_id=page.id,
                canonical_url=page.canonical_url,
                title=page.title,
                probability=probability.get(page.id),
            )
            for page in pages
        ],
    )


@router.post(
    "/recompute",
    operation_id="recompute_clusters",
    dependencies=[Depends(require_write)],
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger a cluster run",
)
async def recompute(
    container: Annotated[Container, Depends(get_container)],
) -> RecomputeResponse:
    """Enqueue a manual run; 409 when one is already in flight."""
    try:
        accepted = await container.clustering.request_run(trigger="manual")
    except ClusterRunInFlightError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    return RecomputeResponse(
        accepted=accepted,
        detail=None if accepted else "not enough indexed pages yet",
    )
