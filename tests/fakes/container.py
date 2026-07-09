"""Test container: real SQLite/LanceDB/huey/chunker, fake embedder/fetcher."""

from pathlib import Path

from refindery.adapters.chunking.chonkie_chunker import ChonkieChunker
from refindery.adapters.clock import SystemClock
from refindery.adapters.metadata.sqlite_store import SqliteMetadataStore
from refindery.adapters.observability.duckdb_sink import DuckDbSink
from refindery.adapters.observability.query_log import DuckDbQueryLog
from refindery.adapters.queue.huey_queue import HueyJobQueue
from refindery.adapters.vector.lancedb_store import LanceDbVectorStore
from refindery.application.container import Container
from refindery.application.ports.cluster_engine import (
    ClusterFitResult,
    ClusterParams,
)
from refindery.application.services.backfill import BackfillService
from refindery.application.services.canonicalization import CanonicalizationService
from refindery.application.services.cluster_triggers import IdleDetector
from refindery.application.services.clustering_run import ClusterRunService
from refindery.application.services.compare_service import CompareService
from refindery.application.services.entity_ingest import EntityIngestService
from refindery.application.services.extraction_router import ExtractionRouter
from refindery.application.services.feedback_service import FeedbackService
from refindery.application.services.forget_service import ForgetService
from refindery.application.services.indexing import IndexingService
from refindery.application.services.ingest import IngestService
from refindery.application.services.model_registry import ModelRegistry
from refindery.application.services.search_service import SearchService
from refindery.application.services.similarity_service import SimilarityService
from refindery.config import (
    DuckDbSettings,
    EmbedderSettings,
    HueySettings,
    JobsSettings,
    LanceDbSettings,
    Scope,
    Settings,
    SqliteSettings,
    TokenSpec,
    VectorStoreKind,
)
from refindery.domain.models import EmbeddingModel, JobKind
from tests.fakes.embedder import FakeEmbedder
from tests.fakes.entity_extractor import FakeEntityExtractor
from tests.fakes.extraction import FakeFetcher, FakeHtmlExtractor
from tests.fakes.reranker import FakeReranker
from tests.fakes.surface_embedder import FakeSurfaceEmbedder

TEST_TOKEN = "test-token"  # noqa: S105 — test fixture
TEST_READ_TOKEN = "test-read-token"  # noqa: S105 — test fixture


class _InlineClusterEngine:
    """Runs the real worker synchronously (no process pool) with no reducer."""

    async def fit(self, *, vectors, params: ClusterParams) -> ClusterFitResult:
        from refindery.adapters.cluster.worker import reduce_and_cluster

        labels, probabilities, reduce_ms, cluster_ms = reduce_and_cluster(
            vectors,
            algorithm=params.algorithm,
            reducer="none",
            n_components=params.n_components,
            n_neighbors=params.n_neighbors,
            min_dist=params.min_dist,
            min_cluster_size=params.min_cluster_size,
            min_samples=params.min_samples,
            leiden_resolution=params.leiden_resolution,
            random_state=params.random_state,
        )
        return ClusterFitResult(
            labels=labels,
            probabilities=probabilities,
            reduce_ms=reduce_ms,
            cluster_ms=cluster_ms,
        )


def make_test_settings(tmp_path: Path) -> Settings:
    """Build settings pointing every path at tmp_path with a fake embedder."""
    return Settings(
        auth_token=TEST_TOKEN,  # type: ignore[arg-type]
        auth_tokens=(
            TokenSpec(
                name="readonly",
                token=TEST_READ_TOKEN,  # type: ignore[arg-type]
                scopes=(Scope.READ,),
            ),
        ),
        vector_store=VectorStoreKind.LANCEDB,
        lancedb=LanceDbSettings(path=tmp_path / "lance"),
        sqlite=SqliteSettings(path=tmp_path / "meta.db"),
        huey=HueySettings(path=tmp_path / "huey.db"),
        duckdb=DuckDbSettings(path=tmp_path / "obs.duckdb"),
        embedder=EmbedderSettings(
            provider="fake", model="fake-model", dim=32, max_input_tokens=32_000
        ),
        jobs=JobsSettings(max_attempts=2, backoff_base_s=0.01),
    )


def fake_embedder_factory(model: EmbeddingModel) -> FakeEmbedder:
    """Every model embeds via the deterministic hash embedder."""
    return FakeEmbedder(model_id=model.id, dim=model.dim)


def build_test_container(
    tmp_path: Path,
    *,
    fetcher: FakeFetcher | None = None,
    extractor=None,
    cluster_engine=None,
) -> Container:
    """Wire a container over real local adapters + fakes for external I/O."""
    settings = make_test_settings(tmp_path)
    clock = SystemClock()
    store = SqliteMetadataStore(settings.sqlite.path)
    vector_store = LanceDbVectorStore(path=settings.lancedb.path)
    chunker = ChonkieChunker(target_tokens=64, overlap_tokens=8, hard_max_tokens=96)
    the_fetcher = fetcher or FakeFetcher()
    router = ExtractionRouter([FakeHtmlExtractor()])
    registry = ModelRegistry(
        store=store,
        vector_store=vector_store,
        clock=clock,
        embedder_factory=fake_embedder_factory,
        chunk_hard_max=settings.chunking.hard_max_tokens,
    )
    indexing = IndexingService(
        store=store,
        vector_store=vector_store,
        chunker=chunker,
        registry=registry,
        clock=clock,
        fetcher=the_fetcher,
        router=router,
    )
    queue = HueyJobQueue(
        path=settings.huey.path,
        store=store,
        clock=clock,
        settings=settings.jobs,
        handlers={
            JobKind.INDEX_PAGE: indexing.handle_index_page,
            JobKind.FETCH_AND_INDEX: indexing.handle_fetch_and_index,
        },
        on_dead=indexing.mark_page_dead,
    )
    indexing.set_queue(queue)
    ingest = IngestService(store=store, queue=queue, clock=clock, router=router)
    sink = DuckDbSink(settings.duckdb.path)
    query_log = DuckDbQueryLog(sink)
    reranker = FakeReranker()
    similarity = SimilarityService(store=store)
    search = SearchService(
        store=store,
        vector_store=vector_store,
        registry=registry,
        similarity=similarity,
        query_log=query_log,
        clock=clock,
        reranker=reranker,
        default_recency_half_life_days=settings.search.recency_half_life_days,
    )
    feedback = FeedbackService(query_log=query_log, clock=clock)
    forget = ForgetService(
        store=store, vector_store=vector_store, queue=queue, clock=clock
    )
    queue.add_handler(JobKind.PURGE_VECTORS, forget.handle_purge_vectors)
    canonicalization = CanonicalizationService(
        store=store, surface_embedder=FakeSurfaceEmbedder(), clock=clock
    )
    entity_ingest = EntityIngestService(
        store=store,
        extractor=extractor or FakeEntityExtractor({}),
        canonicalization=canonicalization,
    )
    queue.add_handler(
        JobKind.EXTRACT_ENTITIES, entity_ingest.handle_extract_entities_job
    )
    clustering = ClusterRunService(
        store=store,
        engine=cluster_engine or _InlineClusterEngine(),
        queue=queue,
        clock=clock,
        canonicalization=canonicalization,
        settings=settings.cluster,
    )
    idle_detector = IdleDetector(store=store, clock=clock, settings=settings.cluster)
    queue.add_handler(JobKind.CLUSTER, clustering.handle_cluster_job)
    queue.add_handler(JobKind.CANONICALIZE_ENTITIES, clustering.handle_canonicalize_job)
    backfill = BackfillService(
        store=store,
        vector_store=vector_store,
        registry=registry,
        queue=queue,
        clock=clock,
    )
    queue.add_handler(JobKind.BACKFILL_MODEL, backfill.handle_backfill_job)
    compare = CompareService(
        store=store,
        vector_store=vector_store,
        registry=registry,
        query_log=query_log,
        clock=clock,
        reranker=reranker,
    )
    return Container(
        settings=settings,
        clock=clock,
        store=store,
        vector_store=vector_store,
        chunker=chunker,
        fetcher=the_fetcher,
        router=router,
        registry=registry,
        indexing=indexing,
        ingest=ingest,
        queue=queue,
        sink=sink,
        query_log=query_log,
        similarity=similarity,
        search=search,
        feedback=feedback,
        forget=forget,
        canonicalization=canonicalization,
        entity_ingest=entity_ingest,
        clustering=clustering,
        idle_detector=idle_detector,
        backfill=backfill,
        compare=compare,
        reranker=reranker,
    )
