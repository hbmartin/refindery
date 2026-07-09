"""Deterministic fake surface-form embedder.

Character-trigram hashing: similar strings get similar vectors, so cosine
matching behaves plausibly in tests without any model download.
"""

import hashlib

import numpy as np

from refindery.domain.rollup import Vector, l2_normalize


class FakeSurfaceEmbedder:
    """Trigram-hash embedding; deterministic and locality-sensitive."""

    def __init__(self, dim: int = 64) -> None:
        self._dim = dim

    @property
    def embedder_id(self) -> str:
        """Cache key."""
        return "fake-surface"

    def _one(self, form: str) -> Vector:
        vector = np.zeros(self._dim, dtype=np.float32)
        padded = f"  {form}  "
        for i in range(len(padded) - 2):
            trigram = padded[i : i + 3]
            digest = hashlib.md5(trigram.encode(), usedforsecurity=False).digest()
            vector[int.from_bytes(digest[:4], "big") % self._dim] += 1.0
        return l2_normalize(vector)

    def embed(self, forms: list[str]) -> list[Vector]:
        """Encode each form."""
        return [self._one(form) for form in forms]
