"""The bundled cockpit admin UI mounts at /admin with SPA deep-link fallback."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest

from refindery.api import app as app_module
from refindery.api.app import create_app
from tests.fakes.container import build_test_container, make_test_settings


def _write_bundle(root: Path) -> None:
    """Create a minimal static SPA bundle (shell + one asset)."""
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(
        "<!doctype html><title>Cockpit</title><body>SHELL</body>",
        encoding="utf-8",
    )
    (root / "assets" / "app.js").write_text("console.log('cockpit')", encoding="utf-8")


@asynccontextmanager
async def _client(tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    container = build_test_container(tmp_path)
    app = create_app(make_test_settings(tmp_path), container=container)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as http:
            yield http


async def test_admin_ui_serves_shell_and_spa_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = tmp_path / "admin_ui"
    _write_bundle(bundle)
    monkeypatch.setattr(app_module, "_admin_ui_dir", lambda: bundle)

    async with _client(tmp_path) as client:
        root = await client.get("/admin/")
        deep = await client.get("/admin/search")
        nested = await client.get("/admin/pages/abc123")
        asset = await client.get("/admin/assets/app.js")

    # Mount root and every client-side deep link resolve to the SPA shell so the
    # browser router can boot; real asset requests still serve their own file.
    assert root.status_code == 200
    assert "SHELL" in root.text
    assert deep.status_code == 200
    assert "SHELL" in deep.text
    assert nested.status_code == 200
    assert "SHELL" in nested.text
    assert asset.status_code == 200
    assert "cockpit" in asset.text


async def test_admin_ui_absent_is_graceful(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A source checkout without the injected bundle simply has no /admin mount;
    # the API surface stays fully functional.
    monkeypatch.setattr(app_module, "_admin_ui_dir", lambda: tmp_path / "missing")

    async with _client(tmp_path) as client:
        missing = await client.get("/admin/")
        healthz = await client.get("/healthz")

    assert missing.status_code == 404
    assert healthz.status_code == 200
