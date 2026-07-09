"""Model registry, backfill, and compare endpoints end to end."""

import asyncio

import httpx
import pytest

from refindery.api.app import create_app
from refindery.domain.models import ModelStatus
from tests.fakes.container import TEST_TOKEN, build_test_container, make_test_settings

AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}

PAGES = [
    {
        "url": "https://a.example/one",
        "title": "One",
        "body_extracted": "Hexagonal architecture keeps domain logic pure.",
    },
    {
        "url": "https://b.example/two",
        "title": "Two",
        "body_extracted": "HDBSCAN finds lumpy clusters and marks noise.",
    },
]

NEW_MODEL = {
    "provider": "fake",
    "model_name": "fake-model-b",
    "dim": 32,
    "max_input_tokens": 32_000,
}


@pytest.fixture
async def harness(tmp_path):
    container = build_test_container(tmp_path)
    app = create_app(make_test_settings(tmp_path), container=container)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as http:
            ids = []
            for body in PAGES:
                response = await http.post("/v1/pages", json=body, headers=AUTH)
                ids.append(response.json()["page_id"])
            async with asyncio.timeout(30):
                for page_id in ids:
                    while True:
                        got = await http.get(
                            f"/v1/pages/{page_id}/status", headers=AUTH
                        )
                        if got.json()["status"] == "indexed":
                            break
                        await asyncio.sleep(0.05)
            yield http, container, ids


async def _wait_model_status(container, model_id: str, status: ModelStatus) -> None:
    async with asyncio.timeout(20):
        while True:
            model = await container.store.get_model(model_id)
            if model is not None and model.status is status:
                return model
            await asyncio.sleep(0.05)


async def test_register_validation(harness):
    client, _container, _ids = harness
    too_small = {**NEW_MODEL, "model_name": "tiny", "max_input_tokens": 128}
    response = await client.post("/v1/models", json=too_small, headers=AUTH)
    assert response.status_code == 422
    assert "re-chunk" in response.json()["detail"]

    ok = await client.post("/v1/models", json=NEW_MODEL, headers=AUTH)
    assert ok.status_code == 201
    dup = await client.post("/v1/models", json=NEW_MODEL, headers=AUTH)
    assert dup.status_code == 409


async def test_backfill_activate_retire_compare_flow(harness):
    client, container, ids = harness
    assert (
        await client.post("/v1/models", json=NEW_MODEL, headers=AUTH)
    ).status_code == 201

    # Dry-run estimate is exact from stored chunk stats.
    estimate = (
        await client.post("/v1/models/fake-model-b/backfill", json={}, headers=AUTH)
    ).json()
    n_chunks, total_tokens = await container.store.chunk_stats()
    assert estimate["n_chunks"] == n_chunks > 0
    assert estimate["total_tokens"] == total_tokens > 0
    assert estimate["est_cost_usd"] is None  # no price map configured
    assert estimate["confirm_required"] is True

    # Not ready yet: activation refused.
    early = await client.post("/v1/models/fake-model-b/activate", headers=AUTH)
    assert early.status_code == 409

    # Confirm: durable backfill runs to completion.
    started = await client.post(
        "/v1/models/fake-model-b/backfill", json={"confirm": True}, headers=AUTH
    )
    assert started.json()["status"] == "backfilling"
    await _wait_model_status(container, "fake-model-b", ModelStatus.READY)
    state = await container.store.get_backfill("fake-model-b")
    assert state is not None
    assert state.finished_at is not None
    assert state.embedded_chunks == n_chunks
    assert state.cursor_page_id is not None

    # Both model spaces answer dense queries independently.
    vectors = await container.store.get_page_vectors(model_id="fake-model-b")
    assert len(vectors) == len(ids)

    # Compare across the two ready models.
    response = await client.post(
        "/v1/compare",
        json={"query": "hexagonal clusters", "models": ["fake-model", "fake-model-b"]},
        headers=AUTH,
    )
    assert response.status_code == 200
    data = response.json()
    assert {run["model"] for run in data["runs"]} == {"fake-model", "fake-model-b"}
    assert all(run["results"] for run in data["runs"])
    (pair,) = data["agreement"]
    assert 0.0 <= pair["jaccard_at_k"] <= 1.0
    assert 0.0 <= pair["rbo"] <= 1.0
    assert pair["intersection_size"] >= 0

    # Activate the new model, then the old one can retire.
    activated = await client.post("/v1/models/fake-model-b/activate", headers=AUTH)
    assert activated.json()["is_active"] is True
    retire_active = await client.delete("/v1/models/fake-model-b", headers=AUTH)
    assert retire_active.status_code == 409
    retire_old = await client.delete("/v1/models/fake-model", headers=AUTH)
    assert retire_old.status_code == 204
    listing = (await client.get("/v1/models", headers=AUTH)).json()
    assert [m["id"] for m in listing["models"]] == ["fake-model-b"]


async def test_compare_rejects_unready_model(harness):
    client, _container, _ids = harness
    await client.post("/v1/models", json=NEW_MODEL, headers=AUTH)
    response = await client.post(
        "/v1/compare",
        json={"query": "x", "models": ["fake-model", "fake-model-b"]},
        headers=AUTH,
    )
    assert response.status_code == 422
    assert "registered" in response.json()["detail"]
