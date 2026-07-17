"""Shared fixtures: one conformance suite, every VectorStore adapter.

LanceDB runs everywhere (in-process). The qdrant param resolves its target
in priority order: the ``QDRANT_URL`` env var wins (a server URL, or
``":memory:"`` for qdrant-client's in-process local mode); otherwise a
testcontainer is started when Docker is available, pinned to the same image
as compose and CI; otherwise the param skips with the reason.
"""

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from refindery.adapters.vector.lancedb_store import LanceDbVectorStore
from refindery.domain.models import EmbeddingModel, ModelStatus

# Keep in sync with docker-compose.yml and .github/workflows/lint-test.yml.
_QDRANT_IMAGE = "qdrant/qdrant:v1.18.2"

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


@pytest.fixture(scope="session")
def qdrant_url() -> Iterator[str]:
    """Resolve the qdrant target: QDRANT_URL env > testcontainer > skip.

    Session-scoped so at most one container serves the whole run (each test
    uses its own collection); a skip here is cached for the session too.
    """
    if url := os.environ.get("QDRANT_URL"):
        yield url
        return
    try:
        from testcontainers.qdrant import QdrantContainer

        container = QdrantContainer(image=_QDRANT_IMAGE)
        container.start()
    except Exception as exc:  # noqa: BLE001 — no Docker must skip, not error
        pytest.skip(f"qdrant unavailable: set QDRANT_URL or start Docker ({exc})")
    try:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(6333)
        yield f"http://{host}:{port}"
    finally:
        container.stop()


@pytest.fixture(params=["lancedb", pytest.param("qdrant", marks=pytest.mark.qdrant)])
async def vector_store(request, tmp_path):
    """Yield a fresh store of each kind, schema prepared for MODEL_A."""
    if request.param == "lancedb":
        store = LanceDbVectorStore(path=tmp_path / "lance")
        await store.ensure_schema([MODEL_A])
        yield store
        await store.close()
        return

    # Resolved lazily: declaring qdrant_url as a parameter of this fixture
    # would boot Docker for the lancedb param too.
    url = request.getfixturevalue("qdrant_url")
    from refindery.adapters.vector.qdrant_store import QdrantVectorStore

    collection = f"conformance_{uuid.uuid4().hex[:12]}"
    store = QdrantVectorStore(url=url, collection=collection)
    await store.ensure_schema([MODEL_A])
    yield store
    await store.drop_collection()
    await store.close()
