"""Surface-form embedder loading behavior."""

import sys
from types import ModuleType

import numpy as np
import numpy.typing as npt

from refindery.adapters.embedding.surface_forms import Model2VecSurfaceEmbedder


def test_model2vec_surface_embedder_loads_model_lazily(monkeypatch):
    class FakeStaticModel:
        calls = 0

        @classmethod
        def from_pretrained(cls, model_name: str) -> "FakeStaticModel":
            cls.calls += 1
            assert model_name == "fake/model"
            return cls()

        def encode(self, forms: list[str]) -> npt.NDArray[np.float32]:
            return np.ones((len(forms), 4), dtype=np.float32)

    module = ModuleType("model2vec")
    module.__dict__["StaticModel"] = FakeStaticModel
    monkeypatch.setitem(sys.modules, "model2vec", module)

    embedder = Model2VecSurfaceEmbedder("fake/model")

    assert FakeStaticModel.calls == 0
    vectors = embedder.embed(["alpha", "beta"])
    assert FakeStaticModel.calls == 1
    assert len(vectors) == 2

    embedder.embed(["gamma"])
    assert FakeStaticModel.calls == 1
