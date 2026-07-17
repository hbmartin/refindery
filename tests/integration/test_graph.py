"""Integration tests for the Kùzu graph store, projection, and mediation."""

from datetime import UTC, datetime

import pytest

from refindery.adapters.metadata.sqlite_store import SqliteMetadataStore
from refindery.application.ports.graph_store import EntityRef, PageProjection
from refindery.application.services.canonicalization import CanonicalizationService
from refindery.application.services.graph_projection import GraphProjectionService
from refindery.application.services.similarity_service import (
    Mediation,
    SimilarityService,
)
from refindery.domain.entities import EntityType
from refindery.domain.ids import EntityId, PageId, new_job_id, new_page_id
from refindery.domain.models import (
    Job,
    JobKind,
    JobStatus,
    Mention,
    Page,
    PageStatus,
)
from tests.fakes.clock import FakeClock
from tests.fakes.surface_embedder import FakeSurfaceEmbedder

pytest.importorskip("kuzu")

from refindery.adapters.graph.kuzu_store import KuzuGraphStore

NOW = datetime(2026, 7, 8, 12, 0, 0, tzinfo=UTC)


def _indexed_page(url: str) -> Page:
    return Page(
        id=new_page_id(),
        canonical_url=url,
        original_url=url,
        domain="example.com",
        title="A Title",
        body_text="body",
        content_hash="hash",
        source="extension",
        metadata=None,
        first_seen_at=NOW,
        last_seen_at=NOW,
        visit_count=1,
        indexed_at=NOW,
        status=PageStatus.INDEXED,
    )


def _ref(eid: str, form: str, idf: float, count: int = 1) -> EntityRef:
    return EntityRef(
        id=EntityId(eid),
        canonical_form=form,
        type=EntityType.TECHNOLOGY,
        idf=idf,
        count=count,
    )


def _projection(page_id: str, entities: tuple[EntityRef, ...]) -> PageProjection:
    return PageProjection(
        page_id=PageId(page_id),
        domain="example.com",
        first_seen_at=NOW,
        entities=entities,
    )


def _mention(form: str) -> Mention:
    return Mention(
        surface_form=form, type=EntityType.TECHNOLOGY, char_start=0, char_end=1
    )


def _graph_job(payload: dict[str, str]) -> Job:
    return Job(
        id=new_job_id(),
        kind=JobKind.GRAPH_PROJECT,
        payload=payload,
        status=JobStatus.RUNNING,
        idempotency_key=f"test:{new_job_id()}",
        created_at=NOW,
        updated_at=NOW,
    )


async def test_kuzu_store_ranks_shared_entities_by_jaccard(tmp_path) -> None:
    store = KuzuGraphStore(path=tmp_path / "graph")
    await store.ensure_schema()
    try:
        k = _ref("e_k", "Kubernetes", idf=2.0)
        d = _ref("e_d", "Docker", idf=1.5)
        p = _ref("e_p", "Python", idf=1.0)
        await store.project_page(_projection("p1", (k, d)))
        await store.project_page(_projection("p2", (k, p)))
        await store.project_page(_projection("p3", (p,)))

        results = await store.pages_sharing_entities(page_id=PageId("p1"), limit=10)

        # p1 shares Kubernetes with p2; p3 shares nothing with p1.
        assert [r.page_id for r in results] == [PageId("p2")]
        # idf-weighted Jaccard: 2.0 / (3.5 + 3.0 - 2.0) = 0.4444...
        assert results[0].score == pytest.approx(2.0 / 4.5)
        assert results[0].shared == 1

        # Re-projection is idempotent (counts do not stack).
        await store.project_page(_projection("p1", (k, d)))
        again = await store.pages_sharing_entities(page_id=PageId("p1"), limit=10)
        assert again == results
    finally:
        await store.close()


async def test_kuzu_store_delete_reset_and_co_occurrence(tmp_path) -> None:
    store = KuzuGraphStore(path=tmp_path / "graph")
    await store.ensure_schema()
    try:
        k = _ref("e_k", "Kubernetes", idf=2.0)
        await store.project_page(_projection("p1", (k,)))
        await store.project_page(_projection("p2", (k,)))
        await store.rebuild_co_occurrence()  # no-op on single-entity pages

        await store.delete_pages([PageId("p1")])
        assert await store.pages_sharing_entities(page_id=PageId("p2"), limit=10) == []

        await store.reset()
        # p2 itself is gone after reset, so it has no entities to share.
        assert await store.pages_sharing_entities(page_id=PageId("p2"), limit=10) == []
    finally:
        await store.close()


async def _seed_shared_entities(store: SqliteMetadataStore) -> tuple[Page, Page, Page]:
    canon = CanonicalizationService(
        store=store,
        surface_embedder=FakeSurfaceEmbedder(),
        clock=FakeClock(NOW),
    )
    p1, p2, p3 = (_indexed_page(f"https://example.com/{n}") for n in ("a", "b", "c"))
    for page in (p1, p2, p3):
        await store.insert_page(page)
    await canon.link_mentions(
        page_id=p1.id, mentions=[_mention("Kubernetes"), _mention("Docker")]
    )
    await canon.link_mentions(page_id=p2.id, mentions=[_mention("Kubernetes")])
    await canon.link_mentions(page_id=p3.id, mentions=[_mention("Postgres")])
    return p1, p2, p3


async def test_graph_projection_service_powers_graph_mediation(tmp_path) -> None:
    store = SqliteMetadataStore(tmp_path / "meta.db")
    await store.connect()
    await store.migrate()
    graph = KuzuGraphStore(path=tmp_path / "graph")
    await graph.ensure_schema()
    try:
        p1, p2, p3 = await _seed_shared_entities(store)
        projection = GraphProjectionService(store=store, graph_store=graph)
        for page in (p1, p2, p3):
            await projection.handle_job(
                _graph_job({"mode": "page", "page_id": page.id})
            )

        similarity = SimilarityService(store=store, graph_store=graph)
        results = await similarity.similar(page_id=p1.id, mediation=Mediation.GRAPH)

        ids = [r.page_id for r in results]
        assert p2.id in ids  # shares Kubernetes
        assert p3.id not in ids  # shares nothing
        assert all(r.reason == Mediation.GRAPH for r in results)

        # A full rebuild reproduces the same graph.
        await projection.handle_job(_graph_job({"mode": "rebuild"}))
        rebuilt = await similarity.similar(page_id=p1.id, mediation=Mediation.GRAPH)
        assert [r.page_id for r in rebuilt] == ids
    finally:
        await graph.close()
        await store.close()


async def test_graph_mediation_falls_back_to_entity_when_absent(tmp_path) -> None:
    store = SqliteMetadataStore(tmp_path / "meta.db")
    await store.connect()
    await store.migrate()
    try:
        p1, _p2, _p3 = await _seed_shared_entities(store)
        similarity = SimilarityService(store=store, graph_store=None)
        graph_res = await similarity.similar(page_id=p1.id, mediation=Mediation.GRAPH)
        entity_res = await similarity.similar(page_id=p1.id, mediation=Mediation.ENTITY)
        assert [r.page_id for r in graph_res] == [r.page_id for r in entity_res]
    finally:
        await store.close()
