"""Shared fixtures: one conformance suite, every VectorStore adapter.

LanceDB runs everywhere (in-process). Qdrant runs when QDRANT_URL is set
(CI service container) or a local Docker daemon can host a testcontainer;
otherwise its param skips.
"""

import os
import uuid
from datetime import UTC, datetime

import pytest

from refindery.adapters.vector.lancedb_store import LanceDbVectorStore
from refindery.domain.models import EmbeddingModel, ModelStatus

DIM = 32
MODEL_A = EmbeddingModel(
    id="model-a",
    provider="fake",
    model_name="model-a",
    dim=DIM,
    max_input_tokens=32_000,
    is_active=True,
    status=ModelStatus.READY,
    created_at=datetime(2026, 1, 1, tzinfo=UTC),
)
MODEL_B = EmbeddingModel(
    id="model-b",
    provider="fake",
    model_name="model-b",
    dim=DIM,
    max_input_tokens=32_000,
    is_active=False,
    status=ModelStatus.READY,
    created_at=datetime(2026, 1, 2, tzinfo=UTC),
)
MODEL_SLASH = EmbeddingModel(
    id="sentence-transformers/all-MiniLM-L6-v2",
    provider="fake",
    model_name="sentence-transformers/all-MiniLM-L6-v2",
    dim=DIM,
    max_input_tokens=32_000,
    is_active=False,
    status=ModelStatus.READY,
    created_at=datetime(2026, 1, 3, tzinfo=UTC),
)


def _qdrant_url() -> str | None:
    return os.environ.get("QDRANT_URL")


@pytest.fixture(params=["lancedb", pytest.param("qdrant", marks=pytest.mark.qdrant)])
async def vector_store(request, tmp_path):
    """Yield a fresh store of each kind, schema prepared for MODEL_A."""
    if request.param == "lancedb":
        store = LanceDbVectorStore(path=tmp_path / "lance")
        await store.ensure_schema([MODEL_A])
        yield store
        await store.close()
        return

    url = _qdrant_url()
    if url is None:
        pytest.skip("qdrant unavailable: set QDRANT_URL or start docker compose")
    from refindery.adapters.vector.qdrant_store import QdrantVectorStore

    collection = f"conformance_{uuid.uuid4().hex[:12]}"
    store = QdrantVectorStore(url=url, collection=collection)
    await store.ensure_schema([MODEL_A])
    yield store
    await store.drop_collection()
    await store.close()
