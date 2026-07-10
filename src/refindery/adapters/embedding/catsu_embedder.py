"""Embedder adapter over catsu (Voyage/OpenAI/Cohere/... behind one client).

``dim``/``max_input_tokens`` come from the registry/settings — catsu does not
guarantee exposing them per model — and the returned vector length is checked
against ``dim`` on every call.
"""

import numpy as np
from catsu import Client
from pydantic import BaseModel, ConfigDict, FiniteFloat, field_validator

from refindery.domain.rollup import Vector

_PROVIDER_ALIASES = {"voyage": "voyageai"}
_FLOAT32_MAX = float(np.finfo(np.float32).max)


class _EmbeddingResponse(BaseModel):
    """Validated subset of catsu's provider response."""

    model_config = ConfigDict(from_attributes=True)

    embeddings: list[list[FiniteFloat]]

    @field_validator("embeddings")
    @classmethod
    def _values_fit_float32(cls, embeddings: list[list[float]]) -> list[list[float]]:
        for vector in embeddings:
            for value in vector:
                if abs(value) > _FLOAT32_MAX:
                    msg = "embedding values must fit in float32"
                    raise ValueError(msg)
        return embeddings


class EmbeddingDimensionMismatchError(RuntimeError):
    """The provider returned vectors of an unexpected dimension."""

    def __init__(self, *, model_id: str, expected: int, got: int) -> None:
        super().__init__(
            f"model {model_id!r} returned {got}-d vectors, expected {expected}; "
            f"fix the registered dim"
        )


class CatsuEmbedder:
    """Embedder port implementation for one registered model."""

    def __init__(
        self,
        *,
        model_id: str,
        provider: str,
        model_name: str,
        dim: int,
        max_input_tokens: int,
    ) -> None:
        self._model_id = model_id
        self._provider = _PROVIDER_ALIASES.get(provider, provider)
        self._model_name = model_name
        self._dim = dim
        self._max_input_tokens = max_input_tokens
        self._client = Client()

    @property
    def model_id(self) -> str:
        """Registry id of the model this embedder serves."""
        return self._model_id

    @property
    def dim(self) -> int:
        """Dimensionality of produced vectors."""
        return self._dim

    @property
    def max_input_tokens(self) -> int:
        """Maximum tokens the model accepts per input."""
        return self._max_input_tokens

    async def embed_documents(self, texts: list[str]) -> list[Vector]:
        """Embed document chunks (storage side)."""
        return await self._embed(texts, input_type="document")

    async def embed_query(self, text: str) -> Vector:
        """Embed a query (query side)."""
        vectors = await self._embed([text], input_type="query")
        return vectors[0]

    async def _embed(self, texts: list[str], *, input_type: str) -> list[Vector]:
        response = await self._client.aembed(
            model=self._model_name,
            input=texts,
            provider=self._provider,
            input_type=input_type,
        )
        parsed = _EmbeddingResponse.model_validate(response)
        if len(parsed.embeddings) != len(texts):
            msg = (
                f"model {self._model_id!r} returned {len(parsed.embeddings)} vectors "
                f"for {len(texts)} inputs"
            )
            raise RuntimeError(msg)
        vectors = [np.asarray(row, dtype=np.float32) for row in parsed.embeddings]
        for vector in vectors:
            if vector.shape != (self._dim,):
                raise EmbeddingDimensionMismatchError(
                    model_id=self._model_id,
                    expected=self._dim,
                    got=int(vector.shape[0]),
                )
        return vectors
