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
offline retrieval evals: `refindery eval score` computes nDCG/MRR/recall from
logged queries + `/v1/feedback` labels, and `refindery eval replay` diffs two
configurations (models or rerank on/off) over the same golden set.

## Quickstart

### macOS one-stop setup (Homebrew, no Docker)

Run the idempotent setup script from the repository root. It installs Homebrew
when needed, then installs Python 3.13 and `uv`, syncs the locked dependencies,
and writes a private `.env` with a generated auth token and the daemon-free
LanceDB profile. The default Voyage embedding model requires an API key; the
script reads it without echoing it and also configures Voyage reranking so one
key covers both services.

```bash
./scripts/setup-macos.sh
```

For a non-interactive setup, pass the key through the environment. Add
`--start` to launch Refindery after setup completes.

```bash
VOYAGE_API_KEY=... ./scripts/setup-macos.sh --start
```

On later runs, start the server with the generated environment:

```bash
uv run --env-file .env refindery serve
```

Use `--skip-api-key` when you only want to prepare the development environment;
indexing will remain unavailable until a provider key is configured.

### macOS one-stop Docker setup

If Docker Desktop is already installed, the Docker setup script configures the
advanced Qdrant profile, builds the app, starts the full stack, and waits for
the readiness check. It writes generated credentials and container-specific
settings to a private `.env.docker`, leaving the daemon-free `.env` untouched.

```bash
./scripts/setup-macos-docker.sh
```

For unattended setup, pass the Voyage key through the environment. Use
`--no-start` to prepare and validate the configuration without launching the
stack, or `--skip-api-key` when indexing does not need to work yet.

```bash
VOYAGE_API_KEY=... ./scripts/setup-macos-docker.sh
```

On later runs, use the dedicated environment file with Compose:

```bash
docker compose --env-file .env.docker up -d --build
```

### Manual minimal profile (no Docker)

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
On macOS, `./scripts/setup-macos-docker.sh` automates this profile and keeps its
settings separate from a host-Python `.env`.

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

## Advanced setup

### Configuration model

Every application setting can be supplied as an environment variable. Use the
`REFINDERY_` prefix and separate nested names with `__`: for example,
`embedder.model` becomes `REFINDERY_EMBEDDER__MODEL`. Tuple and list values are
JSON, kept on one line. Defaults and validation rules live in
[`src/refindery/config.py`](src/refindery/config.py).

Refindery reads `.env` for its own settings, and `.env` is git-ignored. Start it
with `uv run --env-file .env`, however, so native provider variables such as
`VOYAGE_API_KEY` and `COHERE_API_KEY` are also exported to their SDKs:

```bash
uv run --env-file .env refindery serve
```

Use an absolute path for each state file when a service manager or container
may start Refindery from a different working directory. Relative paths resolve
from the process working directory.

### Daemon-free workstation profile

This profile keeps all state under `data/`, uses spaCy for entities, and uses
one Voyage key for embeddings and reranking. Generate the auth token with
`openssl rand -hex 24`, then put the result and provider key in `.env`:

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

Install and launch it with:

```bash
uv sync --locked --extra ner
uv run --env-file .env refindery serve
```

The configured embedding dimension and input limit are authoritative: if they
do not match the provider model, indexing fails instead of storing malformed
vectors. Changing an embedding model also creates a distinct vector space; use
the model registration and backfill API rather than relabeling an existing
model.

### Qdrant profile

For a separate vector daemon, start only Qdrant from Compose and run the Python
process on the host:

```bash
docker compose up -d qdrant
```

Then replace the LanceDB settings in `.env` with:

```dotenv
REFINDERY_VECTOR_STORE=qdrant
REFINDERY_QDRANT__URL=http://127.0.0.1:6333
REFINDERY_QDRANT__COLLECTION=refindery_chunks
```

For a remote authenticated Qdrant deployment, set its HTTPS URL and add
`REFINDERY_QDRANT__API_KEY`. SQLite metadata, Huey jobs, and the DuckDB query
log remain local even when vectors are remote, so back up all four stores as a
unit. The fully containerized Compose profile already points the app at the
`qdrant` service and persists each store in a named volume.

### Entity and content extraction

The entity chain is an ordered fallback, not an ensemble: the first healthy
extractor handles each page, and the next is tried only if it fails. The
standard `ner` extra makes `spacy` available. Other useful profiles are:

```bash
uv sync --extra gliner --extra ner   # GLiNER, then spaCy fallback
uv sync --extra html --extra ner     # accept body_html and fetched HTML
uv sync --extra leiden --extra ner   # enable Leiden clustering
```

Configure GLiNER with spaCy fallback as one-line JSON:

```dotenv
REFINDERY_ENTITY__EXTRACTOR_CHAIN='["gliner", "spacy"]'
```

A gazetteer needs no model dependency. Its file is JSONL, with one validated
entity per line:

```jsonl
{"label":"technology","pattern":"Kubernetes"}
{"label":"product","pattern":"Refindery"}
```

```dotenv
REFINDERY_ENTITY__EXTRACTOR_CHAIN='["gazetteer"]'
REFINDERY_ENTITY__GAZETTEER_PATTERNS_PATH=/absolute/path/entities.jsonl
```

Valid labels are `person`, `org`, `product`, `technology`, `concept`, `place`,
and `work`. For an OpenAI-compatible entity endpoint, include `llm` in the
chain and configure `REFINDERY_LLM__BASE_URL`, `REFINDERY_LLM__MODEL`, and,
when required, `REFINDERY_LLM__API_KEY`. The endpoint must accept
`POST <base-url>/chat/completions`.

### Ranking, clustering, and job tuning

The defaults are conservative for a single-user machine. These are the main
knobs to revisit for a larger collection:

| Setting | Default | Purpose |
| ------- | ------- | ------- |
| `REFINDERY_CHUNKING__TARGET_TOKENS` | `448` | Desired chunk size |
| `REFINDERY_CHUNKING__OVERLAP_TOKENS` | `64` | Context repeated across chunks |
| `REFINDERY_CHUNKING__HARD_MAX_TOKENS` | `512` | Maximum canonical chunk size |
| `REFINDERY_FETCH__TIMEOUT_S` | `10.0` | Outbound fetch timeout |
| `REFINDERY_FETCH__MAX_BYTES` | `10000000` | Maximum fetched response size |
| `REFINDERY_JOBS__MAX_ATTEMPTS` | `5` | Attempts before a job becomes dead |
| `REFINDERY_JOBS__LEASE_MINUTES` | `15` | Recovery lease for in-flight work |
| `REFINDERY_CLUSTER__MIN_PAGES` | `50` | Pages required for the first cluster run |
| `REFINDERY_CLUSTER__MIN_NEW_PAGES` | `20` | New pages required for an idle-triggered run |
| `REFINDERY_SEARCH__RECENCY_HALF_LIFE_DAYS` | unset | Optional ranking decay toward recent pages |

Set `REFINDERY_RERANKER__KIND=none` for fusion-only search with no reranking
provider call. To schedule clustering in addition to its idle trigger, set a
one-to-five-field crontab expression, for example:

```dotenv
REFINDERY_CLUSTER__CRON='0 3 * * *'
```

### Observability and network access

JSON logs and authenticated Prometheus metrics are enabled by default. Traces
are off; enable console spans for diagnosis or send OTLP over HTTP:

```dotenv
REFINDERY_OBSERVABILITY__TRACES=otlp
REFINDERY_OBSERVABILITY__OTLP_ENDPOINT=http://127.0.0.1:4318/v1/traces
REFINDERY_OBSERVABILITY__JSON_LOGS=true
```

If no Refindery OTLP endpoint is set, the standard
`OTEL_EXPORTER_OTLP_ENDPOINT` variable is used. Query logs retain raw query
text for offline evaluation; review the purge and backup guidance in
[`docs/operations.md`](docs/operations.md) before retaining them long term.

The default bind address is loopback-only. If another machine must connect,
set `REFINDERY_BIND_HOST=0.0.0.0` only behind a firewall and a TLS-terminating
reverse proxy, keep bearer authentication enabled, and do not expose the
SQLite, DuckDB, Huey, or LanceDB files over a shared filesystem.

### Validate the installation

First validate and print the resolved configuration. Pydantic masks secret
values in this output:

```bash
uv run --env-file .env python - <<'PY'
from refindery.config import load_settings

print(load_settings().model_dump_json(indent=2))
PY
```

After starting the server, check liveness and readiness separately. Readiness
confirms that the metadata store is reachable and an embedding model is active:

```bash
curl -fsS http://127.0.0.1:8000/healthz
curl -fsS http://127.0.0.1:8000/readyz
```

Then use the ingest and search requests above as an end-to-end check of the
queue, embedding provider, vector store, and retrieval path.

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

See [Architecture](docs/architecture.md) for the ports/adapters map, the
ingest→index→cluster data flow, and the search pipeline. See
[Operations](docs/operations.md) for alpha reset commands, query-log
purging, job lease behavior, vector-store caveats, and accepted risks.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md). TL;DR: `uv sync --all-groups --extra ner`,
then `uv run ruff format . && uv run ruff check . && uv run pytest && uv run ty check && uv run pyrefly check`.
