"""Page similarity: vector mediation now, cluster/entity mediations in M4."""

import asyncio
import logging
from dataclasses import dataclass
from enum import StrEnum

import numpy as np

from refindery.application.ports.graph_store import GraphStore
from refindery.application.ports.metadata_store import (
    ClusterMemberRow,
    MetadataStore,
    PageVectorRow,
)
from refindery.application.services.indexed_pages import indexed_page_ids
from refindery.domain.errors import NoActiveModelError, PageNotFoundError
from refindery.domain.ids import ClusterId, PageId
from refindery.domain.models import PageStatus
from refindery.domain.rollup import Vector

logger = logging.getLogger(__name__)


class Mediation(StrEnum):
    """What mediates 'similar': vectors, cluster, entities, or the graph."""

    VECTOR = "vector"
    CLUSTER = "cluster"
    ENTITY = "entity"
    GRAPH = "graph"


@dataclass(frozen=True, slots=True)
class SimilarPage:
    """A similar page with its score and the mediation that produced it."""

    page_id: PageId
    score: float
    reason: str


class SimilarityService:
    """similar_to(page_id) and the suggestions block on /search."""

    def __init__(
        self, *, store: MetadataStore, graph_store: GraphStore | None = None
    ) -> None:
        self._store = store
        self._graph_store = graph_store

    async def similar(
        self,
        *,
        page_id: PageId,
        mediation: Mediation = Mediation.VECTOR,
        k: int = 10,
        exclude: frozenset[PageId] = frozenset(),
    ) -> list[SimilarPage]:
        """Rank pages similar to ``page_id``; excludes the source itself."""
        page = await self._store.get_page(page_id)
        if page is None:
            raise PageNotFoundError(page_id)
        if page.status is not PageStatus.INDEXED:
            return []
        skip = frozenset({page_id, *exclude})
        match mediation:
            case Mediation.VECTOR:
                return await self._by_vector(page_id, k=k, skip=skip)
            case Mediation.CLUSTER:
                return await self._by_cluster(page_id, k=k, skip=skip)
            case Mediation.ENTITY:
                return await self._by_entity(page_id, k=k, skip=skip)
            case Mediation.GRAPH:
                return await self._by_graph(page_id, k=k, skip=skip)

    async def _vectors(self) -> dict[PageId, Vector]:
        if (model := await self._store.get_active_model()) is None:
            raise NoActiveModelError
        rows = await self._store.get_page_vectors(model_id=model.id)
        indexed_ids = await indexed_page_ids(self._store, [row.page_id for row in rows])
        return await asyncio.to_thread(self._decode_vectors, rows, indexed_ids)

    @staticmethod
    def _decode_vectors(
        rows: list[PageVectorRow], indexed_ids: frozenset[PageId]
    ) -> dict[PageId, Vector]:
        return {
            row.page_id: np.frombuffer(row.vector, dtype=np.float32)
            for row in rows
            if row.page_id in indexed_ids
        }

    async def _by_vector(
        self, page_id: PageId, *, k: int, skip: frozenset[PageId]
    ) -> list[SimilarPage]:
        vectors = await self._vectors()
        source = vectors.get(page_id)
        if source is None:
            return []
        return await asyncio.to_thread(
            self._rank_vectors, vectors, source=source, k=k, skip=skip
        )

    @staticmethod
    def _rank_vectors(
        vectors: dict[PageId, Vector],
        *,
        source: Vector,
        k: int,
        skip: frozenset[PageId],
    ) -> list[SimilarPage]:
        scored = [
            SimilarPage(
                page_id=pid,
                score=float(np.dot(source, vector)),
                reason=Mediation.VECTOR,
            )
            for pid, vector in vectors.items()
            if pid not in skip
        ]
        scored.sort(key=lambda s: (-s.score, s.page_id))
        return scored[:k]

    async def _by_cluster(
        self, page_id: PageId, *, k: int, skip: frozenset[PageId]
    ) -> list[SimilarPage]:
        cluster = await self._store.cluster_for_page(page_id)
        if cluster is None:
            return []
        members = await self._store.cluster_members(ClusterId(cluster.id))
        vectors = await self._vectors()
        source = vectors.get(page_id)
        indexed_ids = await indexed_page_ids(
            self._store, [member.page_id for member in members]
        )
        return await asyncio.to_thread(
            self._rank_cluster_members,
            members,
            indexed_ids,
            vectors,
            source=source,
            k=k,
            skip=skip,
        )

    @staticmethod
    def _rank_cluster_members(
        members: list[ClusterMemberRow],
        indexed_ids: frozenset[PageId],
        vectors: dict[PageId, Vector],
        *,
        source: Vector | None,
        k: int,
        skip: frozenset[PageId],
    ) -> list[SimilarPage]:
        scored: list[SimilarPage] = []
        for member in members:
            pid = member.page_id
            if pid in skip or pid not in indexed_ids:
                continue
            if source is not None and pid in vectors:
                score = float(np.dot(source, vectors[pid]))
            else:
                score = member.probability or 0.0
            scored.append(
                SimilarPage(page_id=pid, score=score, reason=Mediation.CLUSTER)
            )
        scored.sort(key=lambda s: (-s.score, s.page_id))
        return scored[:k]

    async def _by_entity(
        self, page_id: PageId, *, k: int, skip: frozenset[PageId]
    ) -> list[SimilarPage]:
        source_entities = await self._store.entities_for_page(page_id)
        if not source_entities:
            return []
        idf = {e.id: e.idf if e.idf is not None else 1.0 for e in source_entities}
        source_ids = set(idf)
        candidates: dict[PageId, float] = {}
        for entity in source_entities:
            weight = idf[entity.id]
            for pid in await self._store.page_ids_for_entity(entity.id):
                if pid in skip:
                    continue
                candidates[pid] = candidates.get(pid, 0.0) + weight
        indexed_ids = await indexed_page_ids(self._store, candidates)
        scored: list[SimilarPage] = []
        for pid, shared_weight in candidates.items():
            if pid not in indexed_ids:
                continue
            other = await self._store.entities_for_page(pid)
            union_weight = shared_weight + sum(
                (e.idf if e.idf is not None else 1.0)
                for e in other
                if e.id not in source_ids
            )
            scored.append(
                SimilarPage(
                    page_id=pid,
                    score=shared_weight / union_weight if union_weight else 0.0,
                    reason=Mediation.ENTITY,
                )
            )
        scored.sort(key=lambda s: (-s.score, s.page_id))
        return scored[:k]

    async def _by_graph(
        self, page_id: PageId, *, k: int, skip: frozenset[PageId]
    ) -> list[SimilarPage]:
        """Graph-backed shared-entity similarity; falls back to ``_by_entity``.

        The graph computes the same IDF-weighted Jaccard as ``_by_entity`` via a
        page->entity->page traversal. When no graph is configured, or it has no
        result for this page yet, entity mediation answers instead so the
        response never regresses below today's behavior.
        """
        if self._graph_store is not None:
            try:
                scored = await self._graph_candidates(page_id, k=k, skip=skip)
            except Exception:
                # Contract: a failing graph store must never break search;
                # degrade to entity mediation.
                logger.exception(
                    "graph similarity failed for %s; falling back to entity", page_id
                )
            else:
                if scored:
                    return scored
        return await self._by_entity(page_id, k=k, skip=skip)

    async def _graph_candidates(
        self, page_id: PageId, *, k: int, skip: frozenset[PageId]
    ) -> list[SimilarPage]:
        assert self._graph_store is not None  # noqa: S101 — guarded by caller
        shared = await self._graph_store.pages_sharing_entities(
            page_id=page_id, limit=k + len(skip)
        )
        candidates = [s for s in shared if s.page_id not in skip]
        indexed = await indexed_page_ids(self._store, [s.page_id for s in candidates])
        return [
            SimilarPage(page_id=s.page_id, score=s.score, reason=Mediation.GRAPH)
            for s in candidates
            if s.page_id in indexed
        ][:k]
