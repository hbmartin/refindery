# Architecture

Refindery is a hexagonal (ports and adapters) application: pure domain logic
in `domain/`, orchestration in `application/services/`, `Protocol` contracts
in `application/ports/`, infrastructure in `adapters/`, and two driving
adapters — the FastAPI surface in `api/` and the CLI in `cli.py`.
`application/container.py` is the composition root: `build_container(settings)`
wires every adapter behind its port.

## Process model

One asyncio process hosts everything: the FastAPI app, the MCP server
(mounted at `/mcp`, tools generated from the REST routes), and the embedded
Huey consumer that executes durable jobs. The only exception is clustering:
UMAP/HDBSCAN are CPU-bound, so `ProcessPoolClusterEngine` runs them in a
process pool. Jobs are lease-based and idempotent; `Container.startup()`
connects, migrates, syncs the model registry, recovers leases, reconciles
missing entity jobs, and starts the consumer. The eval CLI uses
`Container.startup_for_eval()` instead — store + vector schema only, no
sink, no queue.

## Ports and adapters

| Port (`application/ports/`) | Default adapter | Alternatives | Selection |
|---|---|---|---|
| `VectorStore` | Qdrant (`adapters/vector/qdrant_store.py`) | LanceDB (in-process) | `REFINDERY_VECTOR_STORE` |
| `MetadataStore` | SQLite WAL (`adapters/metadata/sqlite_store.py`) | Postgres (v2) | — |
| `Embedder` | Voyage via catsu (`adapters/embedding/catsu_embedder.py`) | Cohere, OpenAI, local | `REFINDERY_EMBEDDER__*` |
| `Reranker` | Cohere via rerankers (`adapters/reranking/api.py`) | local cross-encoders, none | `REFINDERY_RERANKER__*` |
| `EntityExtractor` | chain: GLiNER → spaCy → gazetteer (`adapters/extractors/`) | LLM extractor | `REFINDERY_ENTITY__EXTRACTOR_CHAIN` |
| `ClusterEngine` | UMAP + HDBSCAN process pool (`adapters/cluster/`) | KMeans, Leiden (extra) | `REFINDERY_CLUSTER__*` |
| `JobQueue` | Huey/SQLite (`adapters/queue/huey_queue.py`) | — | — |
| `QueryLogSink` / `QueryLogReader` | DuckDB (`adapters/observability/`) | — | `REFINDERY_DUCKDB__PATH` |
| `Chunker` | Chonkie (`adapters/chunking/`) | — | `REFINDERY_CHUNKING__*` |
| `Fetcher` / `ContentExtractor` | httpx fetcher; pypdf, pulpie HTML (extra) | — | — |

Heavy optional adapters import lazily so the extras (`html`, `gliner`,
`leiden`, `ner`) stay optional.

## Ingest → index → cluster data flow

1. **Ingest** (`POST /v1/pages` → `IngestService`): canonicalize the URL
   (tracking params stripped), reject blacklisted patterns, upsert the page,
   and enqueue a durable `INDEX_PAGE` (or `FETCH_AND_INDEX` when no body was
   supplied) job. The API returns 202 immediately.
2. **Index** (`IndexingService` via the Huey consumer): extract content if
   needed (PDF/HTML router), chunk, embed each chunk with the active model,
   upsert chunk vectors + a pooled page vector into the vector store, then
   enqueue `EXTRACT_ENTITIES`.
3. **Entities** (`EntityIngestService`): run the extractor chain over the
   page, canonicalize mentions (exact/edit/embedding matching) into entities.
4. **Cluster** (`ClusterRunService`): triggered by the idle detector, cron,
   or `POST /v1/clusters/recompute`; reduces page vectors (UMAP/PCA) and
   clusters (HDBSCAN/KMeans/Leiden) in the process pool, labels clusters
   (keywords, optional LLM), and tombstones replaced runs.

## Search pipeline

`SearchService.search` (also the shape of each `/v1/compare` arm):

1. embed the query with the active model;
2. hybrid retrieval — dense + sparse arms in parallel
   (`adapters/vector/hybrid.py`), fused client-side with reciprocal-rank
   fusion (`domain/retrieval.py`);
3. optional cross-encoder rerank of the fused candidate set;
4. rollup chunks → pages (max by default);
5. hydrate pages, apply optional recency decay, pin exact URL/domain
   matches;
6. final slice `[offset : offset + k]` — pagination happens only here,
   because every earlier stage reorders the ranking.

Every execution is logged to the DuckDB query log (candidate set, both
arms, final pages, params, timings); `POST /v1/feedback` appends relevance
labels. `refindery eval score` reads that substrate back read-only and
computes nDCG/MRR/recall; `refindery eval replay` re-runs golden queries
under two configurations without logging.

## Auth

Bearer tokens with `read`/`write` scopes (`api/auth.py`); every router
requires `read`, the ten mutating routes additionally require `write`.
Tokens are named and individually revocable via `REFINDERY_AUTH_TOKENS`;
the single `REFINDERY_AUTH_TOKEN` remains a full-access shorthand. MCP
tool calls replay through the HTTP routes with the caller's token, so
scopes bind on every transport; `REFINDERY_MCP__ENABLE_MUTATING_TOOLS`
only controls which tools are listed.
