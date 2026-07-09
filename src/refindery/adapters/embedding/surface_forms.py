"""Surface-form embedders: model2vec static model (default)."""

from threading import Lock
from typing import Protocol, cast

import numpy as np
import numpy.typing as npt

from refindery.domain.rollup import Vector, l2_normalize

_DEFAULT_MODEL = "minishlab/potion-base-8M"


class _StaticModelLike(Protocol):
    def encode(self, sentences: list[str]) -> npt.NDArray[np.float32]:
        """Encode surface forms."""
        ...


class Model2VecSurfaceEmbedder:
    """Static-embedding surface-form encoder (no torch, microseconds/call)."""

    def __init__(self, model_name: str = _DEFAULT_MODEL) -> None:
        self._model_name = model_name
        self._model: _StaticModelLike | None = None
        self._lock = Lock()
        self._id = f"model2vec:{model_name}"

    @property
    def embedder_id(self) -> str:
        """Cache key."""
        return self._id

    def embed(self, forms: list[str]) -> list[Vector]:
        """Encode and L2-normalize."""
        matrix = self._load_model().encode(forms)
        return [l2_normalize(np.asarray(row, dtype=np.float32)) for row in matrix]

    def _load_model(self) -> _StaticModelLike:
        if self._model is None:
            with self._lock:
                if self._model is None:
                    from model2vec import StaticModel  # noqa: PLC0415

                    self._model = cast(
                        "_StaticModelLike",
                        StaticModel.from_pretrained(self._model_name),
                    )
        model = self._model
        if model is None:
            msg = "surface-form model failed to load"
            raise RuntimeError(msg)
        return model
