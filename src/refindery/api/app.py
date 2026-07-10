"""FastAPI app factory and lifespan wiring."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from refindery.adapters.observability.logging import configure_logging
from refindery.adapters.observability.otel import configure_tracing
from refindery.api.auth import TokenRegistry, require_read
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
)
from refindery.application.container import Container, build_container
from refindery.config import Settings


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

    app = FastAPI(
        title="Refindery",
        version="0.1.0",
        description=(
            "A local retrieval engine over the web pages you read. Returns "
            "ranked, grounded passages with provenance; synthesis is the "
            "caller's job."
        ),
        lifespan=lifespan,
    )
    app.state.container = wired
    app.state.token_registry = TokenRegistry.from_settings(settings)

    authed = [Depends(require_read)]
    app.include_router(pages.router, dependencies=authed)
    app.include_router(jobs.router, dependencies=authed)
    app.include_router(search.router, dependencies=authed)
    app.include_router(forget.router, dependencies=authed)
    app.include_router(clusters.router, dependencies=authed)
    app.include_router(entities.router, dependencies=authed)
    app.include_router(models.router, dependencies=authed)
    app.include_router(compare.router, dependencies=authed)
    app.include_router(admin.router, dependencies=authed)
    app.include_router(admin.identity_router)
    app.include_router(health.router)
    mount_mcp(app, settings)
    return app
