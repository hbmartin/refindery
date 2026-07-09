"""Surface-form embedders: model2vec static model (default)."""

import numpy as np

from refindery.domain.rollup import Vector, l2_normalize

_DEFAULT_MODEL = "minishlab/potion-base-8M"


class Model2VecSurfaceEmbedder:
    """Static-embedding surface-form encoder (no torch, microseconds/call)."""

    def __init__(self, model_name: str = _DEFAULT_MODEL) -> None:
        from model2vec import StaticModel  # noqa: PLC0415 — downloads on first use

        self._model = StaticModel.from_pretrained(model_name)
        self._id = f"model2vec:{model_name}"

    @property
    def embedder_id(self) -> str:
        """Cache key."""
        return self._id

    def embed(self, forms: list[str]) -> list[Vector]:
        """Encode and L2-normalize."""
        matrix = self._model.encode(forms)
        return [l2_normalize(np.asarray(row, dtype=np.float32)) for row in matrix]
