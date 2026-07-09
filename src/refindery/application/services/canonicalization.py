"""Corpus-internal entity canonicalization (no external knowledge base).

Incremental (per page at ingest): exact normalized-alias match, then
blocking on (type, first token) with edit-distance-first matching (free)
and cosine fallback over surface-form embeddings.

Periodic (with each cluster run): within each block holding several
entities, group by threshold-graph connected components (single-linkage —
simpler than agglomerative average-linkage and dependency-free; flagged
heuristic) and merge each group into its highest-mention member. Merges are
snapshot-logged first and undoable (LIFO).
"""

import asyncio
import logging

import numpy as np

from refindery.application.ports.clock import Clock
from refindery.application.ports.metadata_store import MetadataStore
from refindery.application.ports.surface_embedder import SurfaceFormEmbedder
from refindery.domain.entities import (
    Entity,
    EntityType,
    block_key,
    normalize_surface_form,
    normalized_edit_distance,
)
from refindery.domain.ids import EntityId, PageId, new_entity_id
from refindery.domain.models import Mention

logger = logging.getLogger(__name__)


class CanonicalizationService:
    """Links mentions to canonical entities; merges near-duplicates."""

    def __init__(
        self,
        *,
        store: MetadataStore,
        surface_embedder: SurfaceFormEmbedder | None,
        clock: Clock,
        cosine_threshold: float = 0.85,
        edit_threshold: float = 0.15,
    ) -> None:
        self._store = store
        self._embedder = surface_embedder
        self._clock = clock
        self._cosine = cosine_threshold
        self._edit = edit_threshold

    # -- incremental (ingest) -------------------------------------------------

    async def link_mentions(self, *, page_id: PageId, mentions: list[Mention]) -> None:
        """Link each mention to an entity (matching or new); record mentions."""
        linked: list[tuple[EntityId, Mention]] = []
        for mention in mentions:
            try:
                entity_type = EntityType(mention.type)
            except ValueError:
                logger.debug("dropping mention with unknown type %r", mention.type)
                continue
            normalized = normalize_surface_form(mention.surface_form)
            if not normalized:
                continue
            entity_id = await self._link_one(
                surface=mention.surface_form,
                normalized=normalized,
                entity_type=entity_type,
            )
            linked.append((entity_id, mention))
        await self._store.add_mentions(page_id=page_id, linked=linked)

    async def _link_one(
        self, *, surface: str, normalized: str, entity_type: EntityType
    ) -> EntityId:
        exact = await self._store.find_entity_by_alias(
            normalized=normalized, entity_type=entity_type
        )
        if exact is not None:
            await self._store.add_alias(
                entity_id=exact.id,
                surface_form=surface,
                normalized=normalized,
                key=block_key(normalized),
            )
            return exact.id

        candidates = await self._store.entities_in_block(
            entity_type=entity_type, key=block_key(normalized)
        )
        if (match := await self._best_match(normalized, candidates)) is not None:
            await self._store.add_alias(
                entity_id=match.id,
                surface_form=surface,
                normalized=normalized,
                key=block_key(normalized),
            )
            return match.id

        entity = Entity(id=new_entity_id(), canonical_form=surface, type=entity_type)
        await self._store.create_entity(
            entity=entity,
            surface_form=surface,
            normalized=normalized,
            key=block_key(normalized),
        )
        return entity.id

    async def _best_match(
        self, normalized: str, candidates: list[Entity]
    ) -> Entity | None:
        survivors: list[tuple[Entity, str]] = []
        for candidate in candidates:
            candidate_norm = normalize_surface_form(candidate.canonical_form)
            if normalized_edit_distance(normalized, candidate_norm) <= self._edit:
                return candidate
            survivors.append((candidate, candidate_norm))
        if not survivors or self._embedder is None:
            return None
        forms = [normalized, *[norm for _, norm in survivors]]
        vectors = await asyncio.to_thread(self._embedder.embed, forms)
        source = vectors[0]
        best: tuple[float, Entity] | None = None
        for (candidate, _), vector in zip(survivors, vectors[1:], strict=True):
            similarity = float(np.dot(source, vector))
            if similarity >= self._cosine and (best is None or similarity > best[0]):
                best = (similarity, candidate)
        return None if best is None else best[1]

    # -- periodic (with cluster runs) -------------------------------------------

    async def periodic_recanonicalize(self) -> int:
        """Merge near-duplicate entities within blocks; refresh idf.

        Returns the number of merges performed.
        """
        merges = 0
        for (
            _entity_type,
            _key,
            entity_ids,
        ) in await self._store.entity_blocks_with_duplicates():
            entities = [
                entity
                for eid in entity_ids
                if (entity := await self._store.get_entity(eid)) is not None
            ]
            if len(entities) < 2:
                continue
            merges += await self._merge_block(entities)
        await self._store.refresh_entity_idf()
        return merges

    async def _merge_block(self, entities: list[Entity]) -> int:
        norms = [normalize_surface_form(e.canonical_form) for e in entities]
        vectors = (
            None
            if self._embedder is None
            else await asyncio.to_thread(self._embedder.embed, norms)
        )

        # Threshold graph -> connected components (single linkage).
        parent = list(range(len(entities)))

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        similarities: dict[tuple[int, int], float] = {}
        for i in range(len(entities)):
            for j in range(i + 1, len(entities)):
                edit = normalized_edit_distance(norms[i], norms[j])
                cosine = (
                    None if vectors is None else float(np.dot(vectors[i], vectors[j]))
                )
                distance = edit if cosine is None else min(edit, 1.0 - cosine)
                if distance <= self._edit:
                    similarities[(i, j)] = 1.0 - distance
                    parent[find(i)] = find(j)

        groups: dict[int, list[int]] = {}
        for i in range(len(entities)):
            groups.setdefault(find(i), []).append(i)

        merges = 0
        for members in groups.values():
            if len(members) < 2:
                continue
            members.sort(key=lambda i: (-entities[i].mention_count, entities[i].id))
            target = entities[members[0]]
            for i in members[1:]:
                similarity = similarities.get((min(members[0], i), max(members[0], i)))
                await self._store.merge_entities(
                    source_id=entities[i].id,
                    target_id=target.id,
                    method="periodic",
                    similarity=similarity,
                    now=self._clock.now(),
                )
                merges += 1
        return merges
