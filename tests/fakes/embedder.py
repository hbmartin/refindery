"""Deterministic fake embedder: sha256-derived vectors, no network, no keys.

Identical text always maps to the identical vector, so retrieval tests can
assert exact top hits by querying with a chunk's own text.
"""

import hashlib

import numpy as np

from refindery.domain.rollup import Vector, l2_normalize


def hash_vector(text: str, dim: int) -> Vector:
    """Deterministically derive an L2-normalized vector from text."""
    raw = bytearray()
    counter = 0
    while len(raw) < dim:
        block = hashlib.sha256(f"{counter}:{text}".encode()).digest()
        raw.extend(block)
        counter += 1
    ints = np.frombuffer(bytes(raw[:dim]), dtype=np.uint8).astype(np.float32)
    return l2_normalize(ints - 127.5)


class FakeEmbedder:
    """Embedder port fake."""

    def __init__(self, model_id: str = "fake-model", dim: int = 32) -> None:
        self._model_id = model_id
        self._dim = dim
        self.document_calls: list[list[str]] = []
        self.query_calls: list[str] = []

    @property
    def model_id(self) -> str:
        """Registry id."""
        return self._model_id

    @property
    def dim(self) -> int:
        """Vector dimensionality."""
        return self._dim

    @property
    def max_input_tokens(self) -> int:
        """Token budget (generous; chunking is tested elsewhere)."""
        return 32_000

    async def embed_documents(self, texts: list[str]) -> list[Vector]:
        """Embed each text deterministically."""
        self.document_calls.append(list(texts))
        return [hash_vector(t, self._dim) for t in texts]

    async def embed_query(self, text: str) -> Vector:
        """Embed the query deterministically (same space as documents)."""
        self.query_calls.append(text)
        return hash_vector(text, self._dim)
