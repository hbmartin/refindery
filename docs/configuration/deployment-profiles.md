# Deployment profiles

Refindery is one process with pluggable storage. The main decision is the
[vector store](../getting-started/index.md); everything else follows from it.
These profiles are ready-to-copy `.env` fragments.

## Daemon-free workstation (LanceDB)

All state under `data/`, spaCy for entities, one Voyage key for embeddings and
reranking. Generate the auth token with `openssl rand -hex 24`:

```dotenv
REFINDERY_AUTH_TOKEN=replace-with-a-generated-token
REFINDERY_BIND_HOST=127.0.0.1
REFINDERY_BIND_PORT=8000

REFINDERY_VECTOR_STORE=lancedb
REFINDERY_LANCEDB__PATH=data/lancedb
REFINDERY_SQLITE__PATH=data/refindery.db
REFINDERY_HUEY__PATH=data/huey.db
REFINDERY_DUCKDB__PATH=data/observability.duckdb

VOYAGE_API_KEY=replace-with-your-provider-key
REFINDERY_EMBEDDER__PROVIDER=voyage
REFINDERY_EMBEDDER__MODEL=voyage-3.5
REFINDERY_EMBEDDER__DIM=1024
REFINDERY_EMBEDDER__MAX_INPUT_TOKENS=32000
REFINDERY_RERANKER__KIND=api
REFINDERY_RERANKER__PROVIDER=voyage
REFINDERY_RERANKER__MODEL=rerank-2.5

REFINDERY_ENTITY__EXTRACTOR_CHAIN='["spacy"]'
```

Install and launch:

```bash
uv sync --locked --extra ner
uv run --env-file .env refindery serve
```

## Qdrant profile

For a separate vector daemon, start only Qdrant from Compose and run Python on
the host:

```bash
docker compose up -d qdrant
```

Then replace the LanceDB settings in `.env` with:

```dotenv
REFINDERY_VECTOR_STORE=qdrant
REFINDERY_QDRANT__URL=http://127.0.0.1:6333
REFINDERY_QDRANT__COLLECTION=refindery_chunks
```

For a remote authenticated Qdrant, set its HTTPS URL and add
`REFINDERY_QDRANT__API_KEY`. SQLite metadata, Huey jobs, and the DuckDB query log
stay local even when vectors are remote, so **back up all four stores as a
unit**.

## Fully containerized

The Compose stack already points the app at the `qdrant` service and persists
each store in a named volume (`refindery_data`, `refindery_models`,
`qdrant_storage`):

```bash
export REFINDERY_AUTH_TOKEN="$(openssl rand -hex 24)"
export VOYAGE_API_KEY=...
docker compose up -d --build
curl -s http://127.0.0.1:8000/healthz
```

On macOS, `./scripts/setup-macos-docker.sh` automates this and writes
`.env.docker`, keeping it separate from a host-Python `.env`.

## Network access

The default bind address is loopback-only. If another machine must connect, set
`REFINDERY_BIND_HOST=0.0.0.0` **only** behind a firewall and a TLS-terminating
reverse proxy, keep [bearer authentication](auth.md) enabled, and do not expose
the SQLite, DuckDB, Huey, or LanceDB files over a shared filesystem.

## Related

- [Getting started](../getting-started/index.md) — choosing a profile.
- [Embedding models](../guides/models.md) — providers, dimensions, and migration.
- [Operations](../operations/index.md) — resetting local state and vector caveats.
