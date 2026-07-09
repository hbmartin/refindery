"""Embedding model registry: registration rules and embedder lookup.

The invariant this service owns: no registered model may have a token budget
below the canonical chunk hard max — accepting one would force a re-chunk,
invalidating every other model's index and destroying A/B comparability.
"""

from collections.abc import Callable

from refindery.application.ports.clock import Clock
from refindery.application.ports.embedder import Embedder
from refindery.application.ports.metadata_store import MetadataStore
from refindery.application.ports.vector_store import VectorStore
from refindery.domain.errors import ModelBudgetError, ModelNotFoundError
from refindery.domain.models import EmbeddingModel, ModelStatus

type EmbedderFactory = Callable[[EmbeddingModel], Embedder]

INDEXABLE_STATUSES = frozenset({ModelStatus.READY, ModelStatus.BACKFILLING})


class ModelRegistry:
    """Registration, activation, and embedder instances per model."""

    def __init__(
        self,
        *,
        store: MetadataStore,
        vector_store: VectorStore,
        clock: Clock,
        embedder_factory: EmbedderFactory,
        chunk_hard_max: int,
    ) -> None:
        self._store = store
        self._vector_store = vector_store
        self._clock = clock
        self._factory = embedder_factory
        self._hard_max = chunk_hard_max
        self._embedders: dict[str, Embedder] = {}

    async def sync_from_settings(self, model: EmbeddingModel) -> None:
        """Startup: ensure the configured model is registered, ready, active."""
        existing = await self._store.get_model(model.id)
        if existing is None:
            self._validate_budget(model)
            await self._store.register_model(model)
        if await self._store.get_active_model() is None:
            await self._store.activate_model(model.id)

    async def register(self, model: EmbeddingModel) -> EmbeddingModel:
        """Register a new model (status=registered, not active)."""
        self._validate_budget(model)
        await self._store.register_model(model)
        await self._vector_store.add_model(model)
        return model

    def _validate_budget(self, model: EmbeddingModel) -> None:
        if model.max_input_tokens < self._hard_max:
            raise ModelBudgetError(
                model_id=model.id,
                max_input_tokens=model.max_input_tokens,
                hard_max=self._hard_max,
            )

    async def indexable_models(self) -> list[EmbeddingModel]:
        """Models whose vector spaces receive new chunks (ready or backfilling)."""
        return await self._store.list_models(statuses=INDEXABLE_STATUSES)

    def embedder_for(self, model: EmbeddingModel) -> Embedder:
        """Return (and cache) the embedder instance for a model."""
        if (embedder := self._embedders.get(model.id)) is None:
            embedder = self._factory(model)
            self._embedders[model.id] = embedder
        return embedder

    async def require_model(self, model_id: str) -> EmbeddingModel:
        """Fetch a model or raise ModelNotFoundError."""
        if (model := await self._store.get_model(model_id)) is None:
            raise ModelNotFoundError(model_id)
        return model
