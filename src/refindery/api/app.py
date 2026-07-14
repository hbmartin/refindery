"""FastAPI app factory and lifespan wiring."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib import resources
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Lifespan, Scope

from refindery.adapters.observability.logging import configure_logging
from refindery.adapters.observability.otel import configure_tracing
from refindery.api.auth import TokenRegistry, require_read, require_write
from refindery.api.mcp import mount_mcp
from refindery.api.routes import (
    admin,
    clusters,
    compare,
    entities,
    events,
    forget,
    health,
    jobs,
    models,
    pages,
    search,
    watches,
)
from refindery.application.container import Container, build_container
from refindery.config import Settings

_TITLE = "Refindery"
_VERSION = "0.1.0"
_DESCRIPTION = (
    "A local retrieval engine over the web pages you read. Returns ranked, "
    "grounded passages with provenance; synthesis is the caller's job."
)
_AUTHENTICATED_ROUTERS = (
    pages.router,
    jobs.router,
    search.router,
    forget.router,
    clusters.router,
    entities.router,
    models.router,
    compare.router,
    watches.router,
    events.router,
    admin.router,
)
_ADMIN_UI_MOUNT_PATH = "/admin"
_ADMIN_UI_PACKAGE = "refindery.api"
_ADMIN_UI_DIRS = ("static", "admin")
_ADMIN_UI_INDEX = "index.html"

logger = logging.getLogger(__name__)


def _new_app(*, lifespan: Lifespan[FastAPI] | None = None) -> FastAPI:
    """Create an app with shared metadata for runtime and documentation."""
    return FastAPI(
        title=_TITLE,
        version=_VERSION,
        description=_DESCRIPTION,
        lifespan=lifespan,
    )


def _include_http_routes(app: FastAPI) -> None:
    """Mount the canonical HTTP surface and annotate required auth scopes."""
    authed = [Depends(require_read)]
    for router in _AUTHENTICATED_ROUTERS:
        _annotate_scopes(router, default="read")
        app.include_router(router, dependencies=authed)
    _annotate_scopes(admin.identity_router, default="public")
    _annotate_scopes(health.router, default="public")
    app.include_router(admin.identity_router)
    app.include_router(health.router)


def _annotate_scopes(router: APIRouter, *, default: str) -> None:
    """Add the effective Refindery auth scope to each OpenAPI operation."""
    for route in router.routes:
        if not isinstance(route, APIRoute):
            continue
        dependency_calls = {
            dependency.call for dependency in route.dependant.dependencies
        }
        scope = (
            "write"
            if require_write in dependency_calls
            else "read"
            if require_read in dependency_calls
            else default
        )
        route.openapi_extra = {
            **(route.openapi_extra or {}),
            "x-required-scope": scope,
        }


class _SpaStaticFiles(StaticFiles):
    """StaticFiles that falls back to the SPA shell for client-side routes.

    A single ``Mount`` at ``/admin`` captures every ``/admin/*`` request, so a
    deep link like ``/admin/search`` (a client route with no matching file on
    disk) must resolve to ``index.html`` for the browser router to boot. Real
    asset requests still serve normally; the SPA's own router renders its 404
    for genuinely unknown routes. Starlette raises ``HTTPException(404)`` for a
    missing file rather than returning a 404 response, so the fallback is keyed
    on the exception.
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code == 404:
                return await super().get_response(_ADMIN_UI_INDEX, scope)
            raise


def _admin_ui_dir() -> Path:
    """Resolve the bundled admin UI directory from the installed package.

    Mirrors the ``importlib.resources`` pattern used for SQL migrations so the
    path resolves both from a source checkout and an installed wheel.
    """
    package_root = Path(str(resources.files(_ADMIN_UI_PACKAGE)))
    return package_root.joinpath(*_ADMIN_UI_DIRS)


def _mount_admin_ui(app: FastAPI) -> None:
    """Mount the bundled cockpit admin UI at /admin when it is present.

    The static bundle is injected into the wheel at release time
    (``src/refindery/api/static/admin``); a source checkout without it simply
    has no ``/admin`` and the API stays fully functional. It is served
    unauthenticated like ``/healthz`` — the bundle leaks nothing (the bearer
    token is supplied client-side into ``localStorage`` and every ``/v1`` route
    enforces it independently), and it is same-origin with the API so no CORS
    handling is required.
    """
    admin_dir = _admin_ui_dir()
    if not (admin_dir / _ADMIN_UI_INDEX).is_file():
        logger.debug("admin UI bundle not present at %s; skipping mount", admin_dir)
        return
    app.mount(
        _ADMIN_UI_MOUNT_PATH,
        _SpaStaticFiles(directory=admin_dir, html=True),
        name="admin",
    )
    logger.info("mounted admin UI at %s", _ADMIN_UI_MOUNT_PATH)


def create_openapi_app() -> FastAPI:
    """Build the side-effect-free HTTP app used to generate API documentation."""
    app = _new_app()
    _include_http_routes(app)
    return app


def create_app(settings: Settings, *, container: Container | None = None) -> FastAPI:
    """Build the app; tests may inject a pre-wired container of fakes."""
    configure_logging(json_logs=settings.observability.json_logs)
    configure_tracing(settings.observability)
    wired = container or build_container(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await wired.startup()
        try:
            yield
        finally:
            await wired.shutdown()

    app = _new_app(lifespan=lifespan)
    app.state.container = wired
    app.state.token_registry = TokenRegistry.from_settings(settings)

    _include_http_routes(app)
    mount_mcp(app, settings)
    _mount_admin_ui(app)
    return app
