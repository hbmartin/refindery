"""Composition root: build adapters and services from settings.

Tests build a Container with fakes; production uses ``build_container``.
"""

import logging
from dataclasses import dataclass
from inspect import isawaitable
from typing import TYPE_CHECKING

from refindery.adapters.chunking.chonkie_chunker import ChonkieChunker
from refindery.adapters.clock import SystemClock
from refindery.adapters.cluster.engine import ProcessPoolClusterEngine
from refindery.adapters.embedding.catsu_embedder import CatsuEmbedder
from refindery.adapters.extraction.http_fetcher import HttpFetcher
from refindery.adapters.extraction.pdf_pypdf import PypdfExtractor
from refindery.adapters.extractors.chain import ChainExtractor
from refindery.adapters.extractors.gazetteer import GazetteerExtractor
from refindery.adapters.llm.openai_compat import OpenAiCompatClient
from refindery.adapters.metadata.sqlite_store import SqliteMetadataStore
from refindery.adapters.observability.duckdb_sink import DuckDbSink
from refindery.adapters.observability.query_log import DuckDbQueryLog
from refindery.adapters.queue.huey_queue import HueyJobQueue
from refindery.application.ports.chunker import Chunker
from refindery.application.ports.clock import Clock
from refindery.application.ports.content_extractor import ContentExtractor, Fetcher
from refindery.application.ports.embedder import Embedder
from refindery.application.ports.entity_extractor import EntityExtractor
from refindery.application.ports.job_queue import JobQueue
from refindery.application.ports.metadata_store import MetadataStore
from refindery.application.ports.query_log import QueryLogSink
from refindery.application.ports.reranker import Reranker
from refindery.application.ports.vector_store import VectorStore
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
from refindery.config import RerankerKind, Settings, VectorStoreKind
from refindery.domain.canonical_url import CanonicalizationRules
from refindery.domain.errors import ConfigurationError, ExtractionUnavailableError
from refindery.domain.models import EmbeddingModel, JobKind, ModelStatus

if TYPE_CHECKING:
    from refindery.adapters.embedding.surface_forms import Model2VecSurfaceEmbedder

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class Container:
    """Everything the API layer needs, wired."""

    settings: Settings
    clock: Clock
    store: MetadataStore
    vector_store: VectorStore
    chunker: Chunker
    fetcher: Fetcher
    router: ExtractionRouter
    registry: ModelRegistry
    indexing: IndexingService
    ingest: IngestService
    queue: JobQueue
    sink: DuckDbSink
    query_log: QueryLogSink
    similarity: SimilarityService
    search: SearchService
    feedback: FeedbackService
    forget: ForgetService
    canonicalization: CanonicalizationService
    entity_ingest: EntityIngestService
    clustering: ClusterRunService
    idle_detector: IdleDetector
    backfill: BackfillService
    compare: CompareService
    reranker: Reranker | None = None

    async def startup(self) -> None:
        """Connect, migrate, sync registry, recover jobs, start the consumer."""
        logger.warning(
            "job execution is lease-only; long-running jobs are retried only after "
            "lease recovery, not interrupted at timeout"
        )
        logger.warning(
            "query logs retain raw query text and hit ids in %s until manually purged",
            self.settings.duckdb.path,
        )
        self.sink.start()
        await self.store.connect()
        await self.store.migrate()
        await self.registry.sync_from_settings(self.configured_model())
        models = await self.store.list_models(
            statuses=frozenset(
                {ModelStatus.READY, ModelStatus.BACKFILLING, ModelStatus.REGISTERED}
            )
        )
        await self.vector_store.ensure_schema(models)
        await self.queue.recover()
        try:
            await self.indexing.reconcile_entity_jobs()
        except Exception:
            logger.exception("entity job reconciliation failed; continuing startup")
        await self.queue.start()

    async def shutdown(self) -> None:
        """Attempt every cleanup step and log individual failures."""
        steps = (
            ("queue", self.queue.stop),
            ("entity_ingest", self.entity_ingest.close),
            ("clustering", self.clustering.close),
            ("vector_store", self.vector_store.close),
            ("router", self._close_router),
            ("store", self.store.close),
            ("sink", self._close_sink),
        )
        for name, cleanup in steps:
            try:
                result = cleanup()
                if isawaitable(result):
                    await result
            except Exception:
                logger.exception("shutdown cleanup failed: %s", name)

    async def _close_router(self) -> None:
        self.router.close()

    async def _close_sink(self) -> None:
        self.sink.close()

    def configured_model(self) -> EmbeddingModel:
        """Return the embedding model described by settings."""
        embedder = self.settings.embedder
        return EmbeddingModel(
            id=embedder.model,
            provider=embedder.provider,
            model_name=embedder.model,
            dim=embedder.dim,
            max_input_tokens=embedder.max_input_tokens,
            is_active=True,
            status=ModelStatus.READY,
            created_at=self.clock.now(),
        )


def default_embedder_factory(model: EmbeddingModel) -> Embedder:
    """Build a production embedder for a registered model."""
    return CatsuEmbedder(
        model_id=model.id,
        provider=model.provider,
        model_name=model.model_name,
        dim=model.dim,
        max_input_tokens=model.max_input_tokens,
    )


def _build_vector_store(settings: Settings) -> VectorStore:
    match settings.vector_store:
        case VectorStoreKind.LANCEDB:
            from refindery.adapters.vector.lancedb_store import (  # noqa: PLC0415 — lazy: heavy optional adapter
                LanceDbVectorStore,
            )

            return LanceDbVectorStore(path=settings.lancedb.path)
        case VectorStoreKind.QDRANT:
            from refindery.adapters.vector.qdrant_store import (  # noqa: PLC0415 — lazy: heavy optional adapter
                QdrantVectorStore,
            )

            api_key = settings.qdrant.api_key
            return QdrantVectorStore(
                url=settings.qdrant.url,
                collection=settings.qdrant.collection,
                api_key=None if api_key is None else api_key.get_secret_value(),
            )
        case _:
            msg = f"unknown vector store {settings.vector_store!r}"
            raise ConfigurationError(msg)


def _build_extractor(settings: Settings) -> EntityExtractor:
    """Build the configured extractor chain; startup fails when none are healthy."""
    links: list[EntityExtractor] = []
    attempted: list[str] = []
    for name in settings.entity.extractor_chain:
        attempted.append(name)
        match name:
            case "gliner":
                from refindery.adapters.extractors.gliner_spacy import (  # noqa: PLC0415
                    GlinerExtractor,
                )

                try:
                    links.append(GlinerExtractor())
                except Exception as exc:  # noqa: BLE001 — extra not installed
                    logger.info("gliner extractor unavailable: %s", exc)
            case "spacy":
                from refindery.adapters.extractors.spacy_ner import (  # noqa: PLC0415
                    SpacyExtractor,
                )

                links.append(SpacyExtractor())
            case "gazetteer":
                links.append(
                    GazetteerExtractor(settings.entity.gazetteer_patterns_path)
                )
            case "llm":
                from refindery.adapters.extractors.llm import (  # noqa: PLC0415
                    LlmExtractor,
                )

                links.append(LlmExtractor(_build_llm(settings)))
            case _:
                logger.warning("unknown entity extractor configured: %s", name)
    healthy = [link for link in links if link.health_check()]
    if not healthy:
        attempted_text = ", ".join(attempted) or "<empty chain>"
        msg = (
            "no healthy entity extractor in configured chain "
            f"({attempted_text}); install NER support with `uv sync --extra ner`, "
            "configure REFINDERY_ENTITY__EXTRACTOR_CHAIN, add a gazetteer file, "
            "or configure REFINDERY_LLM__BASE_URL for the llm extractor"
        )
        raise ConfigurationError(msg)
    return ChainExtractor(healthy)


def _build_llm(settings: Settings) -> OpenAiCompatClient | None:
    if settings.llm.base_url is None:
        return None
    api_key = settings.llm.api_key
    return OpenAiCompatClient(
        base_url=settings.llm.base_url,
        model=settings.llm.model,
        api_key=None if api_key is None else api_key.get_secret_value(),
    )


def _build_surface_embedder() -> "Model2VecSurfaceEmbedder | None":
    try:
        from refindery.adapters.embedding.surface_forms import (  # noqa: PLC0415 — downloads a model
            Model2VecSurfaceEmbedder,
        )

        return Model2VecSurfaceEmbedder()
    except Exception:  # noqa: BLE001 — exact/edit canonicalization still works
        logger.warning(
            "surface-form embedder unavailable; entity canonicalization uses "
            "exact/edit matching only",
            exc_info=True,
        )
        return None


def _build_reranker(settings: Settings) -> Reranker | None:
    match settings.reranker.kind:
        case RerankerKind.NONE:
            return None
        case RerankerKind.API | RerankerKind.LOCAL:
            from refindery.adapters.reranking.api import (  # noqa: PLC0415 — lazy: may need provider keys
                ApiReranker,
            )

            provider = (
                "cross-encoder"
                if settings.reranker.kind is RerankerKind.LOCAL
                else settings.reranker.provider
            )
            try:
                return ApiReranker(provider=provider, model=settings.reranker.model)
            except Exception:  # noqa: BLE001 — degrade to fusion-only ranking
                logger.warning(
                    "reranker %s/%s unavailable; searches use fusion scores",
                    provider,
                    settings.reranker.model,
                )
                return None
        case _:
            msg = f"unknown reranker kind {settings.reranker.kind!r}"
            raise ConfigurationError(msg)


def _build_extractors() -> list[ContentExtractor]:
    extractors: list[ContentExtractor] = [PypdfExtractor()]
    try:
        from refindery.adapters.extraction.pulpie_html import (  # noqa: PLC0415 — lazy: requires the html extra
            PulpieHtmlExtractor,
        )

        extractors.append(PulpieHtmlExtractor())
    except ExtractionUnavailableError:
        pass  # html extra not installed; body_html ingest fails with install hint
    return extractors


def build_container(settings: Settings) -> Container:
    """Wire production adapters and services."""
    clock = SystemClock()
    store = SqliteMetadataStore(settings.sqlite.path)
    vector_store = _build_vector_store(settings)
    chunker = ChonkieChunker(
        target_tokens=settings.chunking.target_tokens,
        overlap_tokens=settings.chunking.overlap_tokens,
        hard_max_tokens=settings.chunking.hard_max_tokens,
    )
    fetcher = HttpFetcher(
        timeout_s=settings.fetch.timeout_s, max_bytes=settings.fetch.max_bytes
    )
    router = ExtractionRouter(_build_extractors())
    registry = ModelRegistry(
        store=store,
        vector_store=vector_store,
        clock=clock,
        embedder_factory=default_embedder_factory,
        chunk_hard_max=settings.chunking.hard_max_tokens,
    )
    indexing = IndexingService(
        store=store,
        vector_store=vector_store,
        chunker=chunker,
        registry=registry,
        clock=clock,
        fetcher=fetcher,
        router=router,
        pooling=settings.indexing.page_vector_pooling,
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
    ingest = IngestService(
        store=store,
        queue=queue,
        clock=clock,
        router=router,
        rules=CanonicalizationRules(
            tracking_params=settings.canonicalize.tracking_params
        ),
    )
    sink = DuckDbSink(settings.duckdb.path)
    query_log = DuckDbQueryLog(sink)
    reranker = _build_reranker(settings)
    similarity = SimilarityService(store=store)
    search = SearchService(
        store=store,
        vector_store=vector_store,
        registry=registry,
        similarity=similarity,
        query_log=query_log,
        clock=clock,
        reranker=reranker,
        rules=CanonicalizationRules(
            tracking_params=settings.canonicalize.tracking_params
        ),
    )
    feedback = FeedbackService(query_log=query_log, clock=clock)
    forget = ForgetService(
        store=store,
        vector_store=vector_store,
        queue=queue,
        clock=clock,
        rules=CanonicalizationRules(
            tracking_params=settings.canonicalize.tracking_params
        ),
    )
    queue.add_handler(JobKind.PURGE_VECTORS, forget.handle_purge_vectors)

    canonicalization = CanonicalizationService(
        store=store,
        surface_embedder=_build_surface_embedder(),
        clock=clock,
        cosine_threshold=settings.entity.cosine_threshold,
        edit_threshold=settings.entity.edit_distance_threshold,
    )
    entity_ingest = EntityIngestService(
        store=store,
        extractor=_build_extractor(settings),
        canonicalization=canonicalization,
    )
    queue.add_handler(
        JobKind.EXTRACT_ENTITIES, entity_ingest.handle_extract_entities_job
    )
    clustering = ClusterRunService(
        store=store,
        engine=ProcessPoolClusterEngine(),
        queue=queue,
        clock=clock,
        canonicalization=canonicalization,
        settings=settings.cluster,
        labeler=_build_llm(settings),
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
        pooling=settings.indexing.page_vector_pooling,
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

    from huey import crontab  # noqa: PLC0415 — scheduling detail

    queue.register_periodic(
        name="verify_tombstones",
        schedule=crontab(minute="*/10"),
        handler=forget.verify_tombstones,
    )

    async def _idle_tick() -> None:
        if await idle_detector.should_run():
            await clustering.request_run(trigger="idle")

    queue.register_periodic(
        name="cluster_idle_tick", schedule=crontab(minute="*"), handler=_idle_tick
    )
    if settings.cluster.cron is not None:
        fields = settings.cluster.cron.split()
        cron_schedule = crontab(
            minute=fields[0],
            hour=fields[1] if len(fields) > 1 else "*",
            day=fields[2] if len(fields) > 2 else "*",
            month=fields[3] if len(fields) > 3 else "*",
            day_of_week=fields[4] if len(fields) > 4 else "*",
        )

        async def _cron_tick() -> None:
            await clustering.request_run(trigger="cron")

        queue.register_periodic(
            name="cluster_cron", schedule=cron_schedule, handler=_cron_tick
        )
    return Container(
        settings=settings,
        clock=clock,
        store=store,
        vector_store=vector_store,
        chunker=chunker,
        fetcher=fetcher,
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
