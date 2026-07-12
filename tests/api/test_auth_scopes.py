"""Scope enforcement: read tokens can search and give feedback, not mutate."""

import httpx
import pytest

from refindery.api.app import create_app
from tests.fakes.container import (
    TEST_READ_TOKEN,
    TEST_TOKEN,
    build_test_container,
    make_test_settings,
)

FULL = {"Authorization": f"Bearer {TEST_TOKEN}"}
READ = {"Authorization": f"Bearer {TEST_READ_TOKEN}"}

PAGE = {
    "url": "https://arch.example/hexagonal",
    "title": "Hexagonal Architecture",
    "body_extracted": "Hexagonal architecture keeps domain logic pure.",
    "fetched_at": "2026-06-01T10:00:00Z",
}


@pytest.fixture
async def client(tmp_path):
    container = build_test_container(tmp_path)
    app = create_app(make_test_settings(tmp_path), container=container)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as http:
            yield http


async def test_read_token_can_read(client):
    response = await client.post(
        "/v1/search", json={"query": "hexagonal"}, headers=READ
    )
    assert response.status_code == 200
    blacklist = await client.get("/v1/blacklist", headers=READ)
    assert blacklist.status_code == 200
    metrics = await client.get("/metrics", headers=READ)
    assert metrics.status_code == 200


async def test_read_token_can_record_feedback(client):
    search = await client.post("/v1/search", json={"query": "hexagonal"}, headers=READ)
    response = await client.post(
        "/v1/feedback",
        json={
            "query_id": search.json()["query_id"],
            "page_id": "some-page",
            "relevant": True,
        },
        headers=READ,
    )
    assert response.status_code == 202


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/v1/pages", PAGE),
        ("POST", "/v1/forget", {"url": "https://arch.example/hexagonal"}),
        (
            "POST",
            "/v1/models",
            {"id": "m2", "provider": "fake", "model_name": "m2", "dim": 32},
        ),
        ("POST", "/v1/clusters/recompute", None),
        ("POST", "/v1/jobs/some-job/retry", None),
        ("POST", "/v1/jobs/retry", {}),
        ("DELETE", "/v1/blacklist/some-id", None),
        ("POST", "/v1/watches", {"url": "https://blog.example/feed.xml"}),
        ("PATCH", "/v1/watches/some-id", {"enabled": False}),
        ("DELETE", "/v1/watches/some-id", None),
        ("POST", "/v1/watches/some-id/run", None),
    ],
)
async def test_read_token_cannot_mutate(client, method, path, body):
    response = await client.request(method, path, json=body, headers=READ)
    assert response.status_code == 403
    assert "lacks the write scope" in response.json()["detail"]


async def test_full_token_can_mutate(client):
    response = await client.post("/v1/pages", json=PAGE, headers=FULL)
    assert response.status_code == 202


async def test_unknown_token_is_401(client):
    response = await client.post(
        "/v1/search",
        json={"query": "hexagonal"},
        headers={"Authorization": "Bearer wrong"},
    )
    assert response.status_code == 401


async def test_missing_token_is_401(client):
    response = await client.get("/v1/blacklist")
    assert response.status_code == 401
