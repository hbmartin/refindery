"""Search/similar/feedback API tests over the full local stack.

Real SQLite + LanceDB + huey; fake embedder and reranker (deterministic).
"""

import asyncio
import time

import duckdb
import httpx
import pytest

from refindery.api.app import create_app
from refindery.domain.models import PageStatus
from tests.fakes.container import TEST_TOKEN, build_test_container, make_test_settings

AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}

PAGES = [
    {
        "url": "https://arch.example/hexagonal",
        "title": "Hexagonal Architecture",
        "body_extracted": (
            "Hexagonal architecture keeps domain logic pure. Ports and "
            "adapters isolate infrastructure so the core stays testable."
        ),
        "fetched_at": "2026-06-01T10:00:00Z",
    },
    {
        "url": "https://ml.example/clustering",
        "title": "Density Clustering",
        "body_extracted": (
            "HDBSCAN finds lumpy clusters of arbitrary shape and marks "
            "outliers as noise. UMAP reduces embeddings first."
        ),
        "fetched_at": "2026-06-05T10:00:00Z",
    },
    {
        "url": "https://authz.example/zanzibar",
        "title": "Zanzibar Paper",
        "body_extracted": (
            "The zanzibar authorization system stores relationships as "
            "tuples and evaluates access with zookies."
        ),
        "fetched_at": "2026-06-10T10:00:00Z",
    },
]


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
                assert response.status_code == 202
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


async def test_search_response_shape_matches_spec(harness):
    client, _container, ids = harness
    response = await client.post(
        "/v1/search", json={"query": "zanzibar authorization"}, headers=AUTH
    )
    assert response.status_code == 200
    data = response.json()

    assert set(data) == {"query_id", "results", "suggestions", "timing_ms"}
    assert data["results"], "expected at least one result"
    top = data["results"][0]
    for key in (
        "page_id",
        "canonical_url",
        "title",
        "domain",
        "first_seen_at",
        "visit_count",
        "score",
        "cluster",
        "chunks",
        "exact_match",
    ):
        assert key in top
    assert top["page_id"] == ids[2]
    assert top["chunks"], "whole matched chunks are returned"
    assert "zanzibar" in top["chunks"][0]["text"]
    for stage in ("embed", "dense", "sparse", "fuse", "rerank", "rollup", "total"):
        assert stage in data["timing_ms"]
    # suggestions come from vector similarity, exclude returned pages
    suggested = {s["page_id"] for s in data["suggestions"]}
    assert top["page_id"] not in suggested


async def test_search_rerank_off_skips_stage(harness):
    client, _container, _ids = harness
    response = await client.post(
        "/v1/search",
        json={"query": "clusters noise", "rerank": False},
        headers=AUTH,
    )
    assert response.status_code == 200
    assert "rerank" not in response.json()["timing_ms"]


async def test_search_domain_filter(harness):
    client, _container, ids = harness
    response = await client.post(
        "/v1/search",
        json={
            "query": "architecture clusters zanzibar",
            "filters": {"domain": "ml.example"},
            "suggest": 0,
        },
        headers=AUTH,
    )
    pages = {r["page_id"] for r in response.json()["results"]}
    assert pages == {ids[1]}


async def test_url_query_pins_exact_match(harness):
    client, _container, ids = harness
    response = await client.post(
        "/v1/search",
        json={"query": "https://arch.example/hexagonal?utm_source=x"},
        headers=AUTH,
    )
    top = response.json()["results"][0]
    assert top["page_id"] == ids[0]
    assert top["exact_match"] is True
    assert top["score"] == 1.0


async def test_domain_query_pins_domain_pages(harness):
    client, _container, ids = harness
    response = await client.post(
        "/v1/search", json={"query": "authz.example"}, headers=AUTH
    )
    top = response.json()["results"][0]
    assert top["page_id"] == ids[2]
    assert top["exact_match"] is True


async def test_unknown_entity_filter_yields_empty(harness):
    client, _container, _ids = harness
    response = await client.post(
        "/v1/search",
        json={"query": "anything", "filters": {"entity": "python"}, "suggest": 0},
        headers=AUTH,
    )
    assert response.status_code == 200
    assert response.json()["results"] == []


async def test_similar_endpoint(harness):
    client, _container, ids = harness
    response = await client.get(f"/v1/pages/{ids[0]}/similar?k=2", headers=AUTH)
    assert response.status_code == 200
    data = response.json()
    assert data["mediation"] == "vector"
    got = {r["page_id"] for r in data["results"]}
    assert ids[0] not in got
    assert got <= {ids[1], ids[2]}
    assert all(r["reason"] == "vector" for r in data["results"])


async def test_search_exact_suggestions_and_similar_exclude_non_indexed(harness):
    client, container, ids = harness
    await container.store.set_page_status(page_id=ids[2], status=PageStatus.FAILED)

    exact = await client.post(
        "/v1/search",
        json={"query": "https://authz.example/zanzibar", "suggest": 0},
        headers=AUTH,
    )
    assert exact.status_code == 200
    assert all(r["page_id"] != ids[2] for r in exact.json()["results"])

    broad = await client.post(
        "/v1/search",
        json={"query": "architecture clusters zanzibar", "suggest": 5},
        headers=AUTH,
    )
    data = broad.json()
    assert all(r["page_id"] != ids[2] for r in data["results"])
    assert all(s["page_id"] != ids[2] for s in data["suggestions"])

    similar = await client.get(f"/v1/pages/{ids[2]}/similar", headers=AUTH)
    assert similar.status_code == 200
    assert similar.json()["results"] == []


async def test_similar_cluster_mediation_empty_before_first_run(harness):
    client, _container, ids = harness
    response = await client.get(
        f"/v1/pages/{ids[0]}/similar?mediation=cluster", headers=AUTH
    )
    assert response.status_code == 200
    assert response.json()["results"] == []


async def test_similar_unknown_page_404(harness):
    client, _container, _ids = harness
    response = await client.get("/v1/pages/nope/similar", headers=AUTH)
    assert response.status_code == 404


async def test_query_log_lands_in_duckdb(harness, tmp_path):
    client, _container, _ids = harness
    response = await client.post(
        "/v1/search", json={"query": "zanzibar tuples"}, headers=AUTH
    )
    query_id = response.json()["query_id"]

    feedback = await client.post(
        "/v1/feedback",
        json={
            "query_id": query_id,
            "page_id": response.json()["results"][0]["page_id"],
            "relevant": True,
        },
        headers=AUTH,
    )
    assert feedback.status_code == 202

    # wait for the sink writer to flush, then read the file back
    deadline = time.monotonic() + 10
    rows = []
    joined = []
    while time.monotonic() < deadline:
        conn = duckdb.connect(str(tmp_path / "obs.duckdb"))
        try:
            rows = conn.execute(
                "SELECT query_text, active_model, reranker_model, "
                "len(candidate_set), len(dense_hits), len(sparse_hits), "
                "len(final_pages) FROM query_log WHERE query_id = ?",
                [query_id],
            ).fetchall()
            joined = conn.execute(
                "SELECT fb_relevant FROM query_log_with_feedback "
                "WHERE query_id = ? AND fb_page_id IS NOT NULL",
                [query_id],
            ).fetchall()
        finally:
            conn.close()
        if rows and joined:
            break
        await asyncio.sleep(0.2)

    assert rows, "query row never landed in the log"
    query_text, model, reranker, n_cand, n_dense, n_sparse, n_final = rows[0]
    assert query_text == "zanzibar tuples"
    assert model == "fake-model"
    assert reranker == "fake-reranker"
    assert n_cand > 0
    assert n_dense > 0
    assert n_sparse > 0
    assert n_final > 0
    assert joined == [(True,)]
