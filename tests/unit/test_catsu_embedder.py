"""CatsuEmbedder tests with a stubbed catsu client (no network)."""

from dataclasses import dataclass, field
from typing import cast

import numpy as np
import pytest
from pydantic import ValidationError

from refindery.adapters.embedding.catsu_embedder import (
    CatsuEmbedder,
    EmbeddingDimensionMismatchError,
)


@dataclass
class _StubResponse:
    embeddings: list[list[float]]


@dataclass
class _StubCatsuClient:
    embeddings: list[list[float]] = field(default_factory=list)
    fail: Exception | None = None
    calls: list[dict] = field(default_factory=list)

    async def aembed(
        self,
        *,
        model: str,
        input: list[str],  # noqa: A002 — mirrors catsu's keyword signature
        provider: str,
        input_type: str,
    ) -> _StubResponse:
        self.calls.append(
            {
                "model": model,
                "input": input,
                "provider": provider,
                "input_type": input_type,
            }
        )
        if self.fail is not None:
            raise self.fail
        return _StubResponse(embeddings=self.embeddings)


def _embedder(stub: _StubCatsuClient, *, provider: str = "voyage") -> CatsuEmbedder:
    embedder = CatsuEmbedder(
        model_id="voyage-3.5",
        provider=provider,
        model_name="voyage-3.5",
        dim=3,
        max_input_tokens=32_000,
    )
    embedder._client = stub  # noqa: SLF001 — inject the stub transport
    return embedder


async def test_embed_documents_returns_float32_vectors():
    stub = _StubCatsuClient(embeddings=[[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]])
    vectors = await _embedder(stub).embed_documents(["a", "b"])
    assert len(vectors) == 2
    assert all(v.dtype == np.float32 and v.shape == (3,) for v in vectors)
    assert stub.calls[0]["input_type"] == "document"


async def test_embed_query_uses_query_input_type():
    stub = _StubCatsuClient(embeddings=[[1.0, 2.0, 3.0]])
    vector = await _embedder(stub).embed_query("what is this")
    assert vector.shape == (3,)
    assert stub.calls[0]["input_type"] == "query"
    assert stub.calls[0]["input"] == ["what is this"]


async def test_voyage_provider_alias():
    stub = _StubCatsuClient(embeddings=[[1.0, 2.0, 3.0]])
    await _embedder(stub, provider="voyage").embed_query("q")
    assert stub.calls[0]["provider"] == "voyageai"


async def test_unaliased_provider_passes_through():
    stub = _StubCatsuClient(embeddings=[[1.0, 2.0, 3.0]])
    await _embedder(stub, provider="openai").embed_query("q")
    assert stub.calls[0]["provider"] == "openai"


async def test_dimension_mismatch_raises():
    stub = _StubCatsuClient(embeddings=[[1.0, 2.0]])
    with pytest.raises(EmbeddingDimensionMismatchError, match="expected 3"):
        await _embedder(stub).embed_query("q")


async def test_provider_errors_propagate():
    stub = _StubCatsuClient(fail=ValueError("rate limited"))
    with pytest.raises(ValueError, match="rate limited"):
        await _embedder(stub).embed_documents(["a"])


@pytest.mark.parametrize(
    "value",
    ["not-a-number", float("nan"), float("inf"), float("-inf"), 1e100, -1e100],
)
async def test_malformed_provider_response_is_rejected(value: object) -> None:
    malformed = cast("list[list[float]]", [[value]])
    stub = _StubCatsuClient(embeddings=malformed)
    with pytest.raises(ValidationError):
        await _embedder(stub).embed_query("q")


async def test_provider_result_count_must_match_inputs():
    stub = _StubCatsuClient(embeddings=[[1.0, 2.0, 3.0]])
    with pytest.raises(RuntimeError, match="1 vectors for 2 inputs"):
        await _embedder(stub).embed_documents(["a", "b"])
