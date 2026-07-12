"""Search/similar/feedback API tests over the full local stack.

Real SQLite + LanceDB + huey; fake embedder and reranker (deterministic).
"""

import asyncio
import time

import duckdb
import httpx
import pytest

from refindery.adapters.observability.query_log_reader import DuckDbQueryLogReader
from refindery.api.app import create_app
from refindery.application.ports.reranker import RerankCandidate, RerankScore
from refindery.domain.errors import NoActiveModelError
from refindery.domain.ids import ClusterId
from refindery.domain.models import Cluster, Mention, PageStatus
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


async def _assign_cluster(
    container, page_id: str, *, cluster_id: str = "cluster-ml"
) -> None:
    now = container.clock.now()
    await container.store.upsert_cluster(
        Cluster(
            id=cluster_id,
            label="ML",
            keywords=["clustering"],
            size=1,
            model_id="fake-model",
            created_at=now,
            updated_at=now,
        )
    )
    await container.store.replace_cluster_members(
        cluster_id=ClusterId(cluster_id), members=[(page_id, 1.0)]
    )


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


class _RaisingReranker:
    """Simulates a reranker provider outage at query time."""

    @property
    def model_name(self) -> str:
        return "raising-reranker"

    async def rerank(
        self, *, query: str, candidates: list[RerankCandidate]
    ) -> list[RerankScore]:
        msg = f"reranker down for {query!r} ({len(candidates)} candidates)"
        raise RuntimeError(msg)


async def test_search_degrades_to_fusion_when_reranker_fails(harness):
    client, container, _ids = harness
    container.search._reranker = _RaisingReranker()  # noqa: SLF001
    response = await client.post(
        "/v1/search", json={"query": "zanzibar authorization"}, headers=AUTH
    )
    assert response.status_code == 200
    data = response.json()
    assert data["results"], "expected fusion-ranked results despite reranker outage"
    assert data["results"][0]["title"] == "Zanzibar Paper"


async def test_search_response_shape_matches_spec(harness):
    client, _container, ids = harness
    response = await client.post(
        "/v1/search", json={"query": "zanzibar authorization"}, headers=AUTH
    )
    assert response.status_code == 200
    data = response.json()

    assert set(data) == {
        "query_id",
        "results",
        "offset",
        "has_more",
        "suggestions",
        "timing_ms",
    }
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


async def test_exact_url_match_respects_filters(harness):
    client, container, ids = harness
    await container.canonicalization.link_mentions(
        page_id=ids[1],
        mentions=[
            Mention(
                surface_form="HDBSCAN",
                type="technology",
                char_start=0,
                char_end=7,
            )
        ],
    )
    await _assign_cluster(container, ids[1])

    cases = [
        {"filters": {"domain": "ml.example"}},
        {"filters": {"after": "2026-06-02T00:00:00Z"}},
        {"filters": {"entity": "HDBSCAN"}},
        {"filters": {"cluster_id": "cluster-ml"}},
    ]
    for body in cases:
        response = await client.post(
            "/v1/search",
            json={
                "query": "https://arch.example/hexagonal",
                "suggest": 0,
                **body,
            },
            headers=AUTH,
        )
        assert response.status_code == 200
        results = response.json()["results"]
        assert all(r["page_id"] != ids[0] for r in results)
        assert all(not r["exact_match"] for r in results)


async def test_exact_domain_match_respects_page_id_filters(harness):
    client, container, ids = harness
    await _assign_cluster(container, ids[1])

    response = await client.post(
        "/v1/search",
        json={
            "query": "arch.example",
            "filters": {"cluster_id": "cluster-ml"},
            "suggest": 0,
        },
        headers=AUTH,
    )

    assert response.status_code == 200
    results = response.json()["results"]
    assert all(r["page_id"] != ids[0] for r in results)
    assert all(not r["exact_match"] for r in results)


async def test_search_cluster_field_serialized(harness):
    client, container, ids = harness
    await _assign_cluster(container, ids[1])

    exact = await client.post(
        "/v1/search",
        json={"query": "https://ml.example/clustering", "suggest": 0},
        headers=AUTH,
    )
    exact_top = exact.json()["results"][0]
    assert exact_top["page_id"] == ids[1]
    assert exact_top["cluster"] == {"id": "cluster-ml", "label": "ML"}

    broad = await client.post(
        "/v1/search",
        json={"query": "HDBSCAN clusters noise", "k": 3, "suggest": 0},
        headers=AUTH,
    )
    ml_result = next(
        result for result in broad.json()["results"] if result["page_id"] == ids[1]
    )
    assert ml_result["cluster"] == {"id": "cluster-ml", "label": "ML"}


async def test_search_cluster_filter_uses_live_membership(harness):
    client, container, ids = harness
    await _assign_cluster(container, ids[1])

    response = await client.post(
        "/v1/search",
        json={
            "query": "architecture clusters zanzibar",
            "filters": {"cluster_id": "cluster-ml"},
            "k": 3,
            "suggest": 0,
        },
        headers=AUTH,
    )

    assert response.status_code == 200
    assert {r["page_id"] for r in response.json()["results"]} == {ids[1]}


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


async def test_pagination_pages_are_disjoint_and_complete(harness):
    client, _container, _ids = harness
    body = {"query": "architecture clustering zanzibar", "suggest": 0}
    full = await client.post("/v1/search", json={**body, "k": 4}, headers=AUTH)
    all_ids = [r["page_id"] for r in full.json()["results"]]
    assert len(all_ids) == 3  # the whole corpus matches something

    first = await client.post(
        "/v1/search", json={**body, "k": 2, "offset": 0}, headers=AUTH
    )
    second = await client.post(
        "/v1/search", json={**body, "k": 2, "offset": 2}, headers=AUTH
    )
    first_ids = [r["page_id"] for r in first.json()["results"]]
    second_ids = [r["page_id"] for r in second.json()["results"]]
    assert first_ids + second_ids == all_ids
    assert first.json()["offset"] == 0
    assert first.json()["has_more"] is True
    assert second.json()["has_more"] is False


async def test_pagination_past_the_end_is_empty(harness):
    client, _container, _ids = harness
    response = await client.post(
        "/v1/search",
        json={"query": "zanzibar", "k": 10, "offset": 50, "candidates": 100},
        headers=AUTH,
    )
    assert response.status_code == 200
    assert response.json()["results"] == []
    assert response.json()["has_more"] is False


async def test_pagination_requires_candidates_to_cover_offset(harness):
    client, _container, _ids = harness
    response = await client.post(
        "/v1/search",
        json={"query": "zanzibar", "k": 50, "offset": 60, "candidates": 100},
        headers=AUTH,
    )
    assert response.status_code == 422
    assert "offset + k" in response.text


async def test_pagination_offset_lands_in_query_log(harness, tmp_path):
    client, _container, _ids = harness
    response = await client.post(
        "/v1/search",
        json={
            "query": "https://arch.example/hexagonal",
            "k": 5,
            "offset": 1,
            "recency_half_life_days": 30.0,
        },
        headers=AUTH,
    )
    query_id = response.json()["query_id"]

    deadline = time.monotonic() + 10
    rows = []
    while time.monotonic() < deadline:
        conn = duckdb.connect(str(tmp_path / "obs.duckdb"))
        try:
            rows = conn.execute(
                "SELECT CAST(params ->> 'offset' AS INTEGER), "
                "CAST(params ->> 'exact_match' AS BOOLEAN), "
                "CAST(params ->> 'recency_half_life_days' AS DOUBLE), "
                "final_pages[1].rank FROM query_log "
                "WHERE query_id = ?",
                [query_id],
            ).fetchall()
        finally:
            conn.close()
        if rows:
            break
        await asyncio.sleep(0.2)

    assert rows, "query row never landed in the log"
    offset, exact_match, recency_half_life_days, first_rank = rows[0]
    assert offset == 1
    assert exact_match is True
    assert recency_half_life_days == pytest.approx(30.0)
    # ranks are absolute: the first logged page of an offset=1 slice is rank 2
    assert first_rank == 2


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


class _FlakyModelSearch:
    """Delegates to the real service, failing a marker query per item."""

    def __init__(self, inner) -> None:
        self._inner = inner

    async def search(self, query) -> object:
        if query.query == "fail-this-one":
            raise NoActiveModelError
        return await self._inner.search(query)


async def test_search_batch_happy_path(harness):
    client, container, _ids = harness
    response = await client.post(
        "/v1/search/batch",
        json={"queries": ["zanzibar authorization", "density clustering"], "k": 5},
        headers=AUTH,
    )
    assert response.status_code == 200
    results = response.json()["results"]
    assert [r["outcome"] for r in results] == ["ok", "ok"]
    assert [r["index"] for r in results] == [0, 1]
    assert results[0]["query"] == "zanzibar authorization"
    query_ids = {r["query_id"] for r in results}
    assert len(query_ids) == 2
    assert all(r["timing_ms"] for r in results)

    # Each item logs its own query-log row (feedback joins per query_id).
    container.sink.close()
    reader = DuckDbQueryLogReader(container.settings.duckdb.path)
    logged = {run.query_id for run in reader.read_runs() if run.kind == "search"}
    assert query_ids <= logged

    feedback = await client.post(
        "/v1/feedback",
        json={
            "query_id": results[0]["query_id"],
            "page_id": results[0]["results"][0]["page_id"],
            "relevant": True,
        },
        headers=AUTH,
    )
    assert feedback.status_code == 202


async def test_search_batch_per_item_error_keeps_envelope_200(harness):
    client, container, _ids = harness
    container.search = _FlakyModelSearch(container.search)
    response = await client.post(
        "/v1/search/batch",
        json={"queries": ["zanzibar authorization", "fail-this-one"]},
        headers=AUTH,
    )
    assert response.status_code == 200
    results = response.json()["results"]
    assert results[0]["outcome"] == "ok"
    assert results[1]["outcome"] == "error"
    assert results[1]["error"] == "no_active_model"
    assert results[1]["query"] == "fail-this-one"


@pytest.mark.parametrize(
    "body",
    [
        {"queries": []},
        {"queries": ["q"] * 21},
        {"queries": ["q"], "k": 50, "offset": 60, "candidates": 100},
        {"queries": ["x" * 4_001]},
        {"queries": ["q"], "unexpected": True},
    ],
)
async def test_search_batch_invalid_bodies_are_422(harness, body):
    client, _container, _ids = harness
    response = await client.post("/v1/search/batch", json=body, headers=AUTH)
    assert response.status_code == 422
