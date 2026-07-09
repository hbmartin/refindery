"""Cluster endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from refindery.api.auth import require_write
from refindery.api.deps import get_container
from refindery.api.schemas import (
    ClusterDetailResponse,
    ClusterListResponse,
    ClusterMemberPage,
    ClusterSummary,
    RecomputeResponse,
)
from refindery.application.container import Container
from refindery.application.services.clustering_run import ClusterRunInFlightError
from refindery.domain.ids import ClusterId
from refindery.domain.models import Cluster

router = APIRouter(prefix="/v1/clusters", tags=["clusters"])


def _summary(cluster: Cluster) -> ClusterSummary:
    return ClusterSummary(
        id=cluster.id,
        label=cluster.label,
        keywords=cluster.keywords,
        size=cluster.size,
        tombstoned=cluster.tombstoned_at is not None,
    )


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
    return ClusterListResponse(clusters=[_summary(c) for c in clusters])


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
        cluster=_summary(cluster),
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
