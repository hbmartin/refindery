# Refindery

A local, single-machine retrieval engine over the web pages you read.

Upstream capture systems (browser extensions, history readers) extract
main-body text and POST it here. Refindery chunks, embeds, indexes, clusters,
and extracts entities from that text, then serves hybrid retrieval over it via
a local HTTP API and an MCP server.

**This is a retrieval engine, not a Q&A system.** It returns ranked, grounded
passages with provenance. Synthesis is the caller's job — typically an LLM
agent (e.g. Claude via MCP) that treats Refindery as a tool. No generation
appears on the query path.

## Jobs to be done

1. **Refind** — "I read something about X, take me back to it."
2. **Synthesize** — "What have I learned about Y?" (agent-mediated; Refindery supplies the passages)
3. **Resurface** — "What have I been reading a lot about?" (clusters, similarity)

## Architecture

Hexagonal / ports-and-adapters. A single non-blocking asyncio process hosts
the FastAPI app, the MCP server, and the job queue consumer; CPU-bound work
runs in a process pool.

| Port              | Default adapter                | Alternatives                       |
| ----------------- | ------------------------------ | ---------------------------------- |
| `VectorStore`     | Qdrant (Docker)                | LanceDB (in-process, zero daemon)  |
| `MetadataStore`   | SQLite (WAL)                   | Postgres (v2)                      |
| `Embedder`        | Voyage (via catsu)             | Cohere, OpenAI, local              |
| `Reranker`        | Cohere / Voyage (via rerankers)| local cross-encoders               |
| `EntityExtractor` | spaCy + gazetteer              | GLiNER (extra), LLM                |
| `ClusterEngine`   | UMAP + HDBSCAN                 | KMeans, Leiden (extra)             |

Observability: OpenTelemetry traces (off by default), structured JSON logs,
Prometheus `/metrics`, and a DuckDB append-only query log — the substrate for
offline retrieval evals.

## Quickstart

### Minimal profile (no Docker)

```bash
uv sync --extra ner
export REFINDERY_AUTH_TOKEN="$(openssl rand -hex 24)"
export REFINDERY_VECTOR_STORE=lancedb
export VOYAGE_API_KEY=...           # or configure another embedding provider
python -m refindery
```

### Docker profile (Qdrant, the default store)

```bash
uv sync --extra ner
docker compose up -d qdrant
export REFINDERY_AUTH_TOKEN="$(openssl rand -hex 24)"
python -m refindery
```

### Fully containerized

The multi-stage `Dockerfile` builds a slim image with the `ner` extra
(no torch/gliner; add extras to the sync lines if you need them). Data
lives on the `refindery_data` volume, model caches on `refindery_models`.

```bash
export REFINDERY_AUTH_TOKEN="$(openssl rand -hex 24)"
export VOYAGE_API_KEY=...
docker compose up -d --build
curl -s http://127.0.0.1:8000/healthz
```

### Ingest and search

```bash
curl -s -X POST http://127.0.0.1:8000/v1/pages \
  -H "Authorization: Bearer $REFINDERY_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/article", "title": "An Article",
       "body_extracted": "Plain text main body content...",
       "fetched_at": "2026-07-08T10:00:00Z", "source": "extension"}'

curl -s -X POST http://127.0.0.1:8000/v1/search \
  -H "Authorization: Bearer $REFINDERY_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "main body content"}'
```

### MCP

The MCP server is served over streamable HTTP at `/mcp` (same bearer token):

```bash
claude mcp add --transport http refindery http://127.0.0.1:8000/mcp \
  --header "Authorization: Bearer $REFINDERY_AUTH_TOKEN"
```

Read-only tools are exposed by default (`search`, `get_page`, `similar_to`,
`list_clusters`, …). Mutating tools (`add_page`, `forget`) are opt-in via
`REFINDERY_MCP__ENABLE_MUTATING_TOOLS=true`. That flag controls visibility
only — authorization comes from the token's scopes on every transport.

### Auth tokens and scopes

`REFINDERY_AUTH_TOKEN` is a single full-access token. To hand each capture
source or agent its own revocable token, configure named tokens with `read`
or `write` scopes (write implies read; both forms can coexist):

```bash
export REFINDERY_AUTH_TOKENS='[
  {"name": "chrome-capture", "token": "...", "scopes": ["write"]},
  {"name": "agent",          "token": "...", "scopes": ["read"]}
]'
```

Read-scoped tokens can search, browse, compare, and record feedback;
mutating endpoints (`add_page`, `forget`, model management, …) return 403
without the `write` scope.

## HTTP API

```
POST   /v1/pages                      ingest (body_extracted XOR body_html; neither → fetch)
GET    /v1/pages/{id}                 full body_text + metadata
GET    /v1/pages/{id}/status          queued|indexing|indexed|failed|dead
GET    /v1/pages/{id}/similar         ?mediation=vector|cluster|entity&k=
GET    /v1/pages/{id}/entities
POST   /v1/search                     hybrid dense+sparse, RRF, rerank, filters
POST   /v1/compare                    A/B embedding models (Jaccard@k, RBO, Kendall's τ)
GET    /v1/clusters                   ?include_tombstoned=false
GET    /v1/clusters/{id}
POST   /v1/clusters/recompute
GET    /v1/entities/{id_or_form}
POST   /v1/forget                     purge + blacklist atomically
GET    /v1/blacklist
DELETE /v1/blacklist/{id}
POST   /v1/models                     register embedding model
POST   /v1/models/{id}/backfill       dry-run estimate, then confirm
POST   /v1/models/{id}/activate
DELETE /v1/models/{id}
POST   /v1/feedback                   { query_id, page_id, relevant }
GET    /healthz  /readyz  /metrics
```

Bearer token is always required, even on loopback. The server binds to
`127.0.0.1` by default.

## Optional extras

| Extra       | Enables                                        | Pulls in            |
| ----------- | ---------------------------------------------- | ------------------- |
| `html`      | `body_html` / fetched-HTML extraction (pulpie) | torch (~2 GB)       |
| `gliner`    | GLiNER zero-shot NER                           | gliner, onnxruntime |
| `ner`       | spaCy NER model                                | en_core_web_sm      |
| `leiden`    | Leiden clustering                              | igraph, leidenalg   |

Entity extraction is required at startup. The default extractor chain needs
the `ner` extra unless you configure a healthy gazetteer, GLiNER, or LLM
extractor.

See [Operations](docs/operations.md) for alpha reset commands, query-log
purging, job lease behavior, vector-store caveats, and accepted risks.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md). TL;DR: `uv sync --all-groups --extra ner`,
then `uv run ruff format . && uv run ruff check . && uv run pytest && uv run ty check && uv run pyrefly check`.
