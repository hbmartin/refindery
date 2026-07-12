"""MCP surface tests.

Tool listing respects the mutating-tools flag, descriptions carry the
grounding language, and auth is enforced.
"""

import json

import httpx
import pytest

from refindery.api.app import create_app
from refindery.config import McpSettings
from tests.fakes.container import (
    TEST_READ_TOKEN,
    TEST_TOKEN,
    build_test_container,
    make_test_settings,
)

AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}
READ_AUTH = {"Authorization": f"Bearer {TEST_READ_TOKEN}"}
GROUNDING = "grounded passages from the user's own reading history"


def _rpc(method: str, request_id: int = 1, **params) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def _parse(response: httpx.Response) -> dict:
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        for line in response.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line.removeprefix("data:").strip())
        msg = f"no data frame in SSE body: {response.text[:200]}"
        raise AssertionError(msg)
    return response.json()


async def _mcp_session(client: httpx.AsyncClient, auth: dict) -> dict:
    headers = {
        **auth,
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    init = await client.post(
        "/mcp",
        json=_rpc(
            "initialize",
            protocolVersion="2025-03-26",
            capabilities={},
            clientInfo={"name": "test", "version": "0"},
        ),
        headers=headers,
    )
    assert init.status_code == 200, init.text
    session = init.headers.get("mcp-session-id")
    if session:
        headers["mcp-session-id"] = session
    notified = await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers=headers,
    )
    assert notified.status_code in (200, 202), notified.text
    return headers


async def _list_tools(client: httpx.AsyncClient) -> list[dict]:
    headers = await _mcp_session(client, AUTH)
    listed = await client.post(
        "/mcp", json=_rpc("tools/list", request_id=2), headers=headers
    )
    assert listed.status_code == 200, listed.text
    return _parse(listed)["result"]["tools"]


async def _call_add_page(client: httpx.AsyncClient, auth: dict) -> dict:
    headers = await _mcp_session(client, auth)
    called = await client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            request_id=3,
            name="add_page",
            arguments={
                "url": "https://arch.example/hexagonal",
                "title": "Hexagonal Architecture",
                "body_extracted": "Ports and adapters isolate infrastructure.",
                "fetched_at": "2026-06-01T10:00:00Z",
            },
        ),
        headers=headers,
    )
    assert called.status_code == 200, called.text
    return _parse(called)["result"]


@pytest.fixture
async def make_client(tmp_path):
    async def _make(*, enable_mutating: bool) -> tuple:
        settings = make_test_settings(tmp_path)
        settings = settings.model_copy(
            update={"mcp": McpSettings(enable_mutating_tools=enable_mutating)}
        )
        container = build_test_container(tmp_path)
        app = create_app(settings, container=container)
        return app, container

    return _make


async def test_read_only_tools_by_default(make_client):
    app, _container = await make_client(enable_mutating=False)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            tools = await _list_tools(client)
    names = {t["name"] for t in tools}
    assert names == {
        "search",
        "get_page",
        "page_status",
        "similar_to",
        "list_clusters",
        "cluster_pages",
        "entities",
        "compare",
        "list_watches",
        "get_watch",
    }
    search_tool = next(t for t in tools if t["name"] == "search")
    assert GROUNDING in search_tool["description"].lower()


async def test_mutating_tools_opt_in(make_client):
    app, _container = await make_client(enable_mutating=True)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            tools = await _list_tools(client)
    names = {t["name"] for t in tools}
    assert {
        "add_page",
        "forget",
        "create_watch",
        "update_watch",
        "delete_watch",
        "run_watch",
    } <= names


async def test_mutating_tool_call_respects_token_scope(make_client):
    """Visibility comes from the flag; authorization comes from the token."""
    app, _container = await make_client(enable_mutating=True)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            denied = await _call_add_page(client, READ_AUTH)
            assert denied.get("isError"), denied
            assert "write scope" in json.dumps(denied)

            allowed = await _call_add_page(client, AUTH)
            assert not allowed.get("isError"), allowed


async def test_mcp_requires_auth(make_client):
    app, _container = await make_client(enable_mutating=False)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            response = await client.post(
                "/mcp",
                json=_rpc("initialize", protocolVersion="2025-03-26"),
                headers={"Accept": "application/json, text/event-stream"},
            )
    assert response.status_code == 401
