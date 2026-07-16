"""FastAPI app factory and lifespan wiring."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI
from fastapi.routing import APIRoute
from starlette.types import Lifespan

from refindery.adapters.observability.logging import configure_logging
from refindery.adapters.observability.otel import configure_tracing
from refindery.api.auth import TokenRegistry, require_read, require_write
from refindery.api.mcp import mount_mcp
from refindery.api.routes import (
    admin,
    clusters,
    compare,
    entities,
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
    admin.router,
    watches.router,
)


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
    return app
