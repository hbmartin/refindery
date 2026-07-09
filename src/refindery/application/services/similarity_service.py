"""Page similarity: vector mediation now, cluster/entity mediations in M4."""

from dataclasses import dataclass
from enum import StrEnum

import numpy as np

from refindery.application.ports.metadata_store import MetadataStore
from refindery.domain.errors import NoActiveModelError, PageNotFoundError
from refindery.domain.ids import ClusterId, PageId
from refindery.domain.rollup import Vector


class Mediation(StrEnum):
    """What mediates 'similar': vectors, cluster co-membership, or entities."""

    VECTOR = "vector"
    CLUSTER = "cluster"
    ENTITY = "entity"


@dataclass(frozen=True, slots=True)
class SimilarPage:
    """A similar page with its score and the mediation that produced it."""

    page_id: PageId
    score: float
    reason: str


class SimilarityService:
    """similar_to(page_id) and the suggestions block on /search."""

    def __init__(self, *, store: MetadataStore) -> None:
        self._store = store

    async def similar(
        self,
        *,
        page_id: PageId,
        mediation: Mediation = Mediation.VECTOR,
        k: int = 10,
        exclude: frozenset[PageId] = frozenset(),
    ) -> list[SimilarPage]:
        """Rank pages similar to ``page_id``; excludes the source itself."""
        if await self._store.get_page(page_id) is None:
            raise PageNotFoundError(page_id)
        skip = frozenset({page_id, *exclude})
        match mediation:
            case Mediation.VECTOR:
                return await self._by_vector(page_id, k=k, skip=skip)
            case Mediation.CLUSTER:
                return await self._by_cluster(page_id, k=k, skip=skip)
            case Mediation.ENTITY:
                return await self._by_entity(page_id, k=k, skip=skip)

    async def _vectors(self) -> dict[PageId, Vector]:
        if (model := await self._store.get_active_model()) is None:
            raise NoActiveModelError
        rows = await self._store.get_page_vectors(model_id=model.id)
        return {pid: np.frombuffer(blob, dtype=np.float32) for pid, blob in rows}

    async def _by_vector(
        self, page_id: PageId, *, k: int, skip: frozenset[PageId]
    ) -> list[SimilarPage]:
        vectors = await self._vectors()
        source = vectors.get(page_id)
        if source is None:
            return []
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
        scored: list[SimilarPage] = []
        for pid, probability in members:
            if pid in skip:
                continue
            if source is not None and pid in vectors:
                score = float(np.dot(source, vectors[pid]))
            else:
                score = probability or 0.0
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
        scored: list[SimilarPage] = []
        for pid, shared_weight in candidates.items():
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
