# Multi-stage build: uv resolves into a self-contained venv, the runtime
# stage copies only that venv. Installs core deps + the ner extra (spaCy
# model); the heavy optional extras (html/torch, gliner, leiden) are left
# out — add them to the sync line if you need those adapters.

FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app

# Dependency layer: cached until the lockfile changes.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev --extra ner

# Project layer.
COPY src ./src
COPY README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --extra ner


FROM python:3.13-slim-bookworm

RUN groupadd -r refindery && useradd -r -g refindery refindery \
    && mkdir -p /data /models && chown refindery:refindery /data /models

COPY --from=builder /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH" \
    # loopback is unreachable from outside the container
    REFINDERY_BIND_HOST=0.0.0.0 \
    REFINDERY_SQLITE__PATH=/data/refindery.db \
    REFINDERY_HUEY__PATH=/data/huey.db \
    REFINDERY_LANCEDB__PATH=/data/lancedb \
    REFINDERY_DUCKDB__PATH=/data/observability.duckdb \
    # embedding/reranker model caches live on the /models volume
    HF_HOME=/models

USER refindery
WORKDIR /app
VOLUME ["/data", "/models"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=30s \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"]

CMD ["refindery", "serve"]
