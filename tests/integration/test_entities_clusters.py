"""M4 integration: canonicalization, cluster runs with stable ids, idle logic."""

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from refindery.application.services.cluster_triggers import IdleDetector
from refindery.config import ClusterSettings
from refindery.domain.entities import Entity, EntityType
from refindery.domain.ids import ClusterId, PageId, new_entity_id, new_page_id
from refindery.domain.models import (
    ClusterRun,
    Mention,
    Page,
    PageStatus,
)
from refindery.domain.rollup import l2_normalize
from tests.fakes.clock import FakeClock
from tests.fakes.container import build_test_container

NOW = datetime(2026, 7, 8, 12, 0, 0, tzinfo=UTC)
DIM = 32


@pytest.fixture
async def container(tmp_path):
    c = build_test_container(tmp_path)
    await c.store.connect()
    await c.store.migrate()
    await c.registry.sync_from_settings(c.configured_model())
    yield c
    await c.store.close()


def _mention(surface: str, entity_type: str = EntityType.TECHNOLOGY) -> Mention:
    return Mention(surface_form=surface, type=entity_type, char_start=0, char_end=1)


async def _seed_page(
    store,
    *,
    title: str = "T",
    domain: str = "x.example",
    when: datetime = NOW,
) -> PageId:
    page = Page(
        id=new_page_id(),
        canonical_url=f"https://{domain}/{new_page_id()}",
        original_url="u",
        domain=domain,
        title=title,
        body_text=f"{title} body text",
        content_hash="h",
        source=None,
        metadata=None,
        first_seen_at=when,
        last_seen_at=when,
        visit_count=1,
        indexed_at=when,
        status=PageStatus.INDEXED,
    )
    await store.insert_page(page)
    return page.id


class TestCanonicalization:
    async def test_exact_alias_links_same_entity(self, container):
        canon = container.canonicalization
        p1 = await _seed_page(container.store)
        p2 = await _seed_page(container.store)
        await canon.link_mentions(page_id=p1, mentions=[_mention("Kubernetes")])
        await canon.link_mentions(page_id=p2, mentions=[_mention("kubernetes")])
        entity = await container.store.resolve_entity("Kubernetes")
        assert entity is not None
        assert entity.page_count == 2
        pages = await container.store.page_ids_for_entity(entity.id)
        assert set(pages) == {p1, p2}

    async def test_edit_distance_match_adds_alias(self, container):
        # Same block (first token), small typo -> incremental edit-distance match.
        canon = container.canonicalization
        page = await _seed_page(container.store)
        await canon.link_mentions(
            page_id=page, mentions=[_mention("Postgres Database")]
        )
        await canon.link_mentions(
            page_id=page, mentions=[_mention("Postgres Databaze")]
        )
        entity = await container.store.resolve_entity("Postgres Database")
        assert entity is not None
        aliases = await container.store.entity_aliases(entity.id)
        assert {"Postgres Database", "Postgres Databaze"} <= set(aliases)

    async def test_different_types_stay_separate(self, container):
        canon = container.canonicalization
        page = await _seed_page(container.store)
        await canon.link_mentions(
            page_id=page,
            mentions=[
                _mention("Mercury", EntityType.PLACE),
                _mention("Mercury", EntityType.PRODUCT),
            ],
        )
        place = await container.store.find_entity_by_alias(
            normalized="mercury", entity_type=EntityType.PLACE
        )
        product = await container.store.find_entity_by_alias(
            normalized="mercury", entity_type=EntityType.PRODUCT
        )
        assert place is not None
        assert product is not None
        assert place.id != product.id

    async def test_periodic_merge_and_undo(self, container):
        store = container.store
        canon = container.canonicalization
        p1 = await _seed_page(store)
        p2 = await _seed_page(store)
        # Two near-duplicate entities created directly (bypassing incremental
        # matching) in the same block; the periodic pass must merge them.
        a = Entity(
            id=new_entity_id(),
            canonical_form="Postgres Database",
            type=EntityType.TECHNOLOGY,
        )
        b = Entity(
            id=new_entity_id(),
            canonical_form="Postgres Databaze",
            type=EntityType.TECHNOLOGY,
        )
        await store.create_entity(
            entity=a,
            surface_form="Postgres Database",
            normalized="postgres database",
            key="postgres",
        )
        await store.create_entity(
            entity=b,
            surface_form="Postgres Databaze",
            normalized="postgres databaze",
            key="postgres",
        )
        await store.add_mentions(
            page_id=p1,
            linked=[
                (a.id, _mention("Postgres Database")),
                (
                    a.id,
                    Mention(
                        surface_form="Postgres Database",
                        type="technology",
                        char_start=50,
                        char_end=51,
                    ),
                ),
            ],
        )
        await store.add_mentions(
            page_id=p2, linked=[(b.id, _mention("Postgres Databaze"))]
        )

        merges = await canon.periodic_recanonicalize()
        assert merges == 1
        survivor = await store.resolve_entity("Postgres Database")
        assert survivor is not None
        assert survivor.id == a.id  # highest mention count wins
        # Both pages now attached to the surviving entity.
        assert set(await store.page_ids_for_entity(survivor.id)) == {p1, p2}
        # Alias-based resolution still works for the merged-away form.
        merged_ref = await store.resolve_entity("Postgres Databaze")
        assert merged_ref is not None
        assert merged_ref.id == survivor.id

        # Undo restores the second entity exactly.
        cursor = await store.conn.execute("SELECT id FROM entity_merges")
        merge_id = (await cursor.fetchone())["id"]
        restored_id = await store.undo_merge(merge_id, now=NOW)
        restored = await store.get_entity(restored_id)
        assert restored is not None
        assert restored.id == b.id
        assert set(await store.page_ids_for_entity(restored_id)) == {p2}
        assert set(await store.page_ids_for_entity(survivor.id)) == {p1}


def _blob_pages(n: int, center: int) -> list[tuple[PageId, np.ndarray]]:
    rng = np.random.default_rng(center)
    out = []
    for _ in range(n):
        vector = np.zeros(DIM, dtype=np.float32)
        vector[center] = 1.0
        vector += rng.normal(0, 0.05, DIM).astype(np.float32)
        out.append((PageId(str(new_page_id())), l2_normalize(vector)))
    return out


class TestClusterRun:
    async def _seed_corpus(self, container, blobs: list[list]) -> None:
        for blob in blobs:
            for page_id, vector in blob:
                page = Page(
                    id=page_id,
                    canonical_url=f"https://c.example/{page_id}",
                    original_url="u",
                    domain="c.example",
                    title=f"Page {page_id[:8]}",
                    body_text="Topic content " * 10,
                    content_hash="h",
                    source=None,
                    metadata=None,
                    first_seen_at=NOW,
                    last_seen_at=NOW,
                    visit_count=1,
                    indexed_at=NOW,
                    status=PageStatus.INDEXED,
                )
                await container.store.insert_page(page)
                await container.store.upsert_page_vector(
                    page_id=page_id,
                    model_id="fake-model",
                    vector=vector.tobytes(),
                )

    async def test_run_creates_stable_clusters(self, container):
        container.clustering._settings = ClusterSettings(min_pages=30)  # noqa: SLF001
        blobs = [_blob_pages(20, 0), _blob_pages(20, 7)]
        await self._seed_corpus(container, blobs)

        run = await container.clustering.run(trigger="manual")
        assert run is not None
        assert run.n_pages == 40
        assert run.n_clusters == 2
        assert run.duration_ms is not None

        clusters = await container.store.list_clusters()
        assert len(clusters) == 2
        assert all(c.keywords for c in clusters)
        assert all(c.label for c in clusters)  # keywords fallback label
        first_ids = sorted(c.id for c in clusters)

        # Second run with a few new pages in each blob: ids persist.
        extra = [_blob_pages(3, 0), _blob_pages(3, 7)]
        await self._seed_corpus(container, extra)
        run2 = await container.clustering.run(trigger="manual")
        assert run2 is not None
        clusters2 = await container.store.list_clusters()
        assert sorted(c.id for c in clusters2) == first_ids
        assert {c.size for c in clusters2} == {23}

        # Lineage recorded both runs.
        cursor = await container.store.conn.execute(
            "SELECT event, COUNT(*) AS n FROM cluster_lineage GROUP BY event"
        )
        events = {row["event"]: row["n"] for row in await cursor.fetchall()}
        assert events.get("created") == 2
        assert events.get("persisted") == 2

        # cluster mediation works now
        source = blobs[0][0][0]
        similar = await container.similarity.similar(
            page_id=source, mediation="cluster", k=5
        )
        assert similar
        blob0_ids = {pid for pid, _ in [*blobs[0], *extra[0]]}
        assert all(s.page_id in blob0_ids for s in similar)

    async def test_run_skipped_below_minimum(self, container):
        assert await container.clustering.run(trigger="manual") is None


class TestIdleDetector:
    async def test_threshold_and_gates(self, container):
        clock = FakeClock(NOW)
        settings = ClusterSettings(min_pages=1, min_new_pages=2)
        detector = IdleDetector(store=container.store, clock=clock, settings=settings)

        # No ingest yet -> never idle-run.
        assert await detector.should_run() is False

        await _seed_page(container.store)
        # Default threshold (no run history) is 15 minutes.
        assert (await detector.idle_threshold()) == timedelta(minutes=15)
        assert await detector.should_run() is False  # not idle long enough
        clock.advance(minutes=16)
        assert await detector.should_run() is True  # no prior run, pages >= min

        # Record a finished run; now min_new_pages gates.
        run = ClusterRun(
            id="r1",
            trigger="manual",
            algorithm="hdbscan",
            params={},
            started_at=clock.now(),
            finished_at=clock.now(),
            duration_ms=120_000,
        )
        await container.store.insert_cluster_run(run)
        await container.store.finalize_cluster_run(run)
        # duration-derived threshold: clamp(120s * 3, 5m, 60m) = 6 minutes
        assert (await detector.idle_threshold()) == timedelta(minutes=6)
        clock.advance(minutes=30)
        assert await detector.should_run() is False  # only 1 new page since run

        await _seed_page(container.store, when=clock.now())
        await _seed_page(container.store, when=clock.now())
        # New ingest resets idleness...
        assert await detector.should_run() is False
        clock.advance(minutes=7)
        assert await detector.should_run() is True

    async def test_threshold_clamped(self, container):
        clock = FakeClock(NOW)
        detector = IdleDetector(
            store=container.store, clock=clock, settings=ClusterSettings()
        )
        run = ClusterRun(
            id="r-long",
            trigger="manual",
            algorithm="hdbscan",
            params={},
            started_at=clock.now(),
            finished_at=clock.now(),
            duration_ms=100_000_000,
        )
        await container.store.insert_cluster_run(run)
        await container.store.finalize_cluster_run(run)
        assert (await detector.idle_threshold()) == timedelta(minutes=60)


class TestClusterEndpoints:
    async def test_tombstoned_resolvable_but_unlisted(self, container):
        page_id = await _seed_page(container.store)
        from refindery.domain.models import Cluster

        cluster = Cluster(
            id="c-old",
            label="Old Topic",
            keywords=["old"],
            size=1,
            model_id="fake-model",
            created_at=NOW,
            updated_at=NOW,
        )
        await container.store.upsert_cluster(cluster)
        await container.store.replace_cluster_members(
            cluster_id=ClusterId("c-old"), members=[(page_id, 1.0)]
        )
        await container.store.tombstone_clusters([ClusterId("c-old")], now=NOW)
        assert await container.store.list_clusters() == []
        listed = await container.store.list_clusters(include_tombstoned=True)
        assert [c.id for c in listed] == ["c-old"]
        got = await container.store.get_cluster(ClusterId("c-old"))
        assert got is not None
        assert got.tombstoned_at is not None
