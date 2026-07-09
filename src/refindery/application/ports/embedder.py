"""Embedding port; one instance per registered embedding model."""

from typing import Protocol

from refindery.domain.rollup import Vector


class Embedder(Protocol):
    """Embeds documents and queries into a single model's vector space.

    ``dim`` and ``max_input_tokens`` come from the model registry / settings
    (not every provider SDK exposes them); adapters must assert returned
    vector length equals ``dim`` on first use.
    """

    @property
    def model_id(self) -> str:
        """Registry id of the model this embedder serves."""
        ...

    @property
    def dim(self) -> int:
        """Dimensionality of produced vectors."""
        ...

    @property
    def max_input_tokens(self) -> int:
        """Maximum tokens the model accepts per input."""
        ...

    async def embed_documents(self, texts: list[str]) -> list[Vector]:
        """Embed document chunks (storage side)."""
        ...

    async def embed_query(self, text: str) -> Vector:
        """Embed a query (query side; providers may use asymmetric prompts)."""
        ...
