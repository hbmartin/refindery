"""Entity extraction end to end: ingest -> extract -> canonicalize -> API."""

import asyncio

import httpx
import pytest

from refindery.api.app import create_app
from tests.fakes.container import TEST_TOKEN, build_test_container, make_test_settings
from tests.fakes.entity_extractor import FakeEntityExtractor

AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}

KEYWORDS = {"Kubernetes": "technology", "Google": "org"}

PAGES = [
    {
        "url": "https://a.example/k8s",
        "title": "K8s Intro",
        "body_extracted": "Kubernetes orchestrates containers at Google scale.",
    },
    {
        "url": "https://b.example/gke",
        "title": "GKE Guide",
        "body_extracted": "Google runs managed Kubernetes clusters in the cloud.",
    },
    {
        "url": "https://c.example/other",
        "title": "Unrelated",
        "body_extracted": "Sourdough bread rises slowly overnight.",
    },
]


@pytest.fixture
async def harness(tmp_path):
    container = build_test_container(tmp_path, extractor=FakeEntityExtractor(KEYWORDS))
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


async def test_page_entities_and_resolution(harness):
    client, _container, ids = harness
    page_entities = (
        await client.get(f"/v1/pages/{ids[0]}/entities", headers=AUTH)
    ).json()
    forms = {e["canonical_form"] for e in page_entities["entities"]}
    assert forms == {"Kubernetes", "Google"}

    # Resolve by canonical form; detail carries aliases and both pages.
    detail = (await client.get("/v1/entities/Kubernetes", headers=AUTH)).json()
    assert detail["entity"]["type"] == "technology"
    assert detail["entity"]["page_count"] == 2
    assert set(detail["page_ids"]) == {ids[0], ids[1]}

    # Resolve by id too.
    by_id = (
        await client.get(f"/v1/entities/{detail['entity']['id']}", headers=AUTH)
    ).json()
    assert by_id["entity"]["id"] == detail["entity"]["id"]

    assert (
        await client.get("/v1/entities/nonexistent", headers=AUTH)
    ).status_code == 404


async def test_entity_search_filter(harness):
    client, _container, ids = harness
    response = await client.post(
        "/v1/search",
        json={
            "query": "clusters cloud bread",
            "filters": {"entity": "Google"},
            "suggest": 0,
        },
        headers=AUTH,
    )
    pages = {r["page_id"] for r in response.json()["results"]}
    assert pages <= {ids[0], ids[1]}
    assert pages


async def test_entity_similar_mediation(harness):
    client, _container, ids = harness
    response = await client.get(
        f"/v1/pages/{ids[0]}/similar?mediation=entity", headers=AUTH
    )
    data = response.json()
    assert [r["page_id"] for r in data["results"]] == [ids[1]]
    assert data["results"][0]["reason"] == "entity"
