"""Surface-form embedding port for entity canonicalization.

A dedicated small static model (not the active document embedder) keeps the
cosine threshold calibrated across active-model swaps and costs nothing at
ingest. The active-embedder alternative exists behind config.
"""

from typing import Protocol

from refindery.domain.rollup import Vector


class SurfaceFormEmbedder(Protocol):
    """Embeds normalized entity surface forms (synchronous, cheap)."""

    @property
    def embedder_id(self) -> str:
        """Cache key for surface_vectors."""
        ...

    def embed(self, forms: list[str]) -> list[Vector]:
        """Embed normalized surface forms; L2-normalized outputs."""
        ...
