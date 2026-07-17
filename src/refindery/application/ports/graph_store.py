"""Graph store port: a derived, rebuildable graph index over the corpus.

The graph is a **secondary index, never source of truth** — SQLite and the
vector store stay authoritative, and the whole graph is reconstructable from
them (``reset`` + re-``project_page`` per indexed page). It is optional: when
no graph store is configured the graph features cleanly no-op.

Phase 0 models two edge kinds:

- ``(:Page)-[:MENTIONS {count}]->(:Entity)`` — maintained incrementally, one
  clean per-page replace at a time (a page's mentions are fully rewritten, so
  re-projection is idempotent).
- ``(:Entity)-[:CO_OCCURS {count}]->(:Entity)`` — a *derived* projection of
  ``MENTIONS`` recomputed in-graph by ``rebuild_co_occurrence``; not maintained
  incrementally (incremental co-occurrence deltas cannot be made consistent
  under entity merges, so a full rebuild is the source of truth).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from refindery.domain.entities import EntityType
from refindery.domain.ids import EntityId, PageId


@dataclass(frozen=True, slots=True)
class EntityRef:
    """One entity mentioned on a page: its corpus IDF and per-page count."""

    id: EntityId
    canonical_form: str
    type: EntityType
    idf: float
    count: int


@dataclass(frozen=True, slots=True)
class PageProjection:
    """A page and its entity mentions, ready to write into the graph."""

    page_id: PageId
    domain: str
    first_seen_at: datetime
    entities: tuple[EntityRef, ...]


@dataclass(frozen=True, slots=True)
class SharedEntityPage:
    """A candidate page related to a source through shared entities.

    ``score`` is the IDF-weighted Jaccard over entity sets (the same measure
    as ``SimilarityService._by_entity``), computed in-graph.
    """

    page_id: PageId
    score: float
    shared: int


class GraphStore(Protocol):
    """Write/query the derived entity graph. All methods are best-effort.

    A configured-but-failing graph store must never break the ingest or search
    pipelines: callers treat a raised error or empty result as "no graph" and
    fall back to existing behavior.
    """

    async def ensure_schema(self) -> None:
        """Create node/rel tables if absent (idempotent)."""
        ...

    async def close(self) -> None:
        """Release the database handle."""
        ...

    async def project_page(self, projection: PageProjection) -> None:
        """Upsert a page, its entities, and a clean rewrite of its MENTIONS."""
        ...

    async def delete_pages(self, page_ids: Sequence[PageId]) -> None:
        """Remove pages and their edges (forget/purge)."""
        ...

    async def reset(self) -> None:
        """Drop all nodes and edges (start of a full rebuild)."""
        ...

    async def rebuild_co_occurrence(self) -> None:
        """Recompute CO_OCCURS from the current MENTIONS edges, in-graph."""
        ...

    async def pages_sharing_entities(
        self, *, page_id: PageId, limit: int
    ) -> list[SharedEntityPage]:
        """Rank pages by IDF-weighted shared-entity Jaccard with ``page_id``."""
        ...
