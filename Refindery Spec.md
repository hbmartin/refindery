# Refindery: A Personal Web Content Retrieval Engine

## 1. Summary

A local, single-machine retrieval engine over the web pages you read. Upstream capture systems (browser extensions, history DB readers) extract main-body text and POST it here. This system chunks, embeds, indexes, clusters, and extracts entities from that text, then serves hybrid retrieval over it via a local HTTP API and an MCP server.

**This is a retrieval engine, not a Q&A system.** It returns ranked, grounded passages with provenance. Synthesis is the caller's job — typically an LLM agent (e.g. Claude via MCP) that treats this as a tool. No generation appears on the query path.

### Jobs to be done
1. **Refind** — "I read something about X, take me back to it."
2. **Synthesize** — "What have I learned about Y?" (agent-mediated; we supply the passages)
3. **Resurface** — "What have I been reading a lot about?" (clusters, similarity)

### Non-goals (v1)
- Answer generation or summarization on the query path
- Page versioning / change detection / re-capture (deferred to v2)
- Multi-device sync, multi-user, hosted deployment
- Any UI (TUI deferred to v2)
- Push/spaced-recall (architected for, not built)

---

## 2. Architecture

Hexagonal / ports-and-adapters. Single non-blocking `asyncio` process. `uv`-managed. Docker used only to bundle local-mode dependencies (Ollama, Qdrant).

```
                       ┌─────────────────────────┐
   upstream capture ──▶│  HTTP API (FastAPI)     │
                       │  MCP server (stdio+http)│
                       └───────────┬─────────────┘
                                   │
                       ┌───────────▼─────────────┐
                       │   Application Services  │
                       │  Ingest · Search ·      │
                       │  Cluster · Entity ·     │
                       │  Compare · Forget       │
                       └───────────┬─────────────┘
                                   │  ports
   ┌───────────┬───────────┬───────┴──────┬───────────┬───────────┐
   ▼           ▼           ▼              ▼           ▼           ▼
VectorStore MetadataStore Embedder    Reranker  EntityExtractor ClusterEngine
   │           │           │              │           │           │
Qdrant      SQLite      Voyage        Cohere       LLM (opt)  HDBSCAN
LanceDB     [Postgres]  Cohere        Voyage       GLiNER     KMeans
            DuckDB(obs) OpenAI        bge-reranker spaCy      Leiden
                        Ollama/ST    Jina or local CE  gazetteer
```

Note the the results of all direct system or HTTP calls must be validated with pydantic. (SDK or library calls do not require additional validation.)

### Ports

| Port | Responsibility |
|---|---|
| `VectorStore` | upsert/query dense + sparse vectors, payload filtering, per-model collections |
| `MetadataStore` | pages, chunks, entities, clusters, jobs, blacklist, models, query log |
| `Embedder` | `embed_documents(list[str]) -> list[vec]`, `embed_query(str) -> vec`, exposes `dim`, `max_input_tokens` |
| `Reranker` | `rerank(query, list[chunk]) -> list[score]` |
| `EntityExtractor` | `extract(text) -> list[Mention]` |
| `ClusterEngine` | `fit(vectors) -> labels` |
| `JobQueue` | durable enqueue/lease/ack/retry |
| `Clock` | injectable, for idle detection and tests |

Every adapter is swappable via config. No adapter type leaks into `domain/` or `application/`.

---

## 3. Storage Decisions

### 3.1 Vector store — support both

Both adapters ship. Selected by config; both must pass the same conformance test suite.

| | **Qdrant** (Docker) | **LanceDB (zero-daemon local mode)** |
|---|---|---|
| Deployment | Daemon (Docker, ~200MB RSS) | In-process, single directory |
| Multi-model | Collection per model | Vector column per model |
| Sparse/BM25 | Native sparse vectors, in-store | Tantivy FTS index |
| Hybrid fusion | Server-side RRF (`Query API` prefetch) | Client-side RRF in Python |
| Filter pushdown | Pre-filtered inside HNSW | Post-filter / limited pushdown |
| Concurrent writes | Native | Optimistic concurrency + retry |
| Backfill new model | Create collection → embed → done | Add column → backfill |
| Ops burden | Docker Compose, snapshots | `uv add lancedb` |

**Rationale.** Qdrant matches what you described needing — real hybrid fusion in one store, isolated per-model spaces for A/B, filter pushdown. LanceDB matches what you asked for — zero daemon, single process, `uv`-native. Ship both; default to Qdrant; LanceDB is the "I don't want Docker today" path.

**Qdrant / single collection / named vectors**: best default for hybrid retrieval and model comparison. Current Qdrant docs say named vectors can be added and removed from existing collections as of v1.18. Qdrant also says many collections have overhead and recommends single collections with payload partitioning in many cases.

Prefer **single collection with named dense vectors + one sparse vector** for local v1. Use collection-per-model only when you need hard isolation, separate optimizer settings, or simpler drop semantics.

Note: architecture should support options to use pgvector or ChromaDB in V2.

### 3.2 Metadata store — SQLite (WAL) OR Postgres

You asked about Chroma and DuckDB. Both are wrong for this role:

- **Chroma is a vector database, not a metadata store.** It has no relational model, no transactions, no joins. Cluster lineage and entity aliasing need joins.
- **DuckDB has a *worse* concurrent-write story than SQLite** — a single process holds an exclusive lock on the file. It's an OLAP engine. Using it for the job queue would be a regression.

Your original objection to SQLite was concurrent writes. That objection dissolves under this architecture: vectors live in Qdrant/LanceDB, and the only writer to the metadata store is the single in-process queue consumer. SQLite in WAL mode gives many-readers / one-writer, which is precisely the shape of this workload.

**Decision:**
- **SQLite (WAL)** — transactional metadata + durable job queue.
- **DuckDB** — append-only observability sink (query log, trace events, eval artifacts). This is where DuckDB is genuinely the right tool: columnar scans over query history for offline eval.
- **Postgres** — not in v1. The `MetadataStore` port and dialect-neutral DDL make it a drop-in later. No SQLite-specific SQL outside the adapter.

Implement using `huey` rather than rolling a custom SQL queue. https://huey.readthedocs.io/en/1.11.0/sqlite.html

---

## 4. Data Model

```sql
-- Canonical page. One row per canonical_url. Never versioned.
CREATE TABLE pages (
  id              TEXT PRIMARY KEY,          -- uuid7
  canonical_url   TEXT NOT NULL UNIQUE,
  original_url    TEXT NOT NULL,
  domain          TEXT NOT NULL,
  title           TEXT,
  body_text       TEXT NOT NULL,
  content_hash    TEXT NOT NULL,             -- sha256(body_text)
  source          TEXT,                      -- 'extension' | 'history' | ...
  metadata        TEXT,                      -- JSON passthrough from upstream
  first_seen_at   TIMESTAMP NOT NULL,
  last_seen_at    TIMESTAMP NOT NULL,
  visit_count     INTEGER NOT NULL DEFAULT 1,
  indexed_at      TIMESTAMP,
  status          TEXT NOT NULL              -- queued|indexing|indexed|failed|dead
);

-- Canonical chunking. Model-independent. One chunking, all models embed the same spans.
CREATE TABLE chunks (
  id              TEXT PRIMARY KEY,
  page_id         TEXT NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
  ordinal         INTEGER NOT NULL,
  text            TEXT NOT NULL,
  token_count     INTEGER NOT NULL,
  char_start      INTEGER NOT NULL,
  char_end        INTEGER NOT NULL,
  UNIQUE (page_id, ordinal)
);

CREATE TABLE embedding_models (
  id                TEXT PRIMARY KEY,        -- 'voyage-3', 'bge-m3'
  provider          TEXT NOT NULL,           -- voyage|cohere|openai|ollama|sentence-transformers
  model_name        TEXT NOT NULL,
  dim               INTEGER NOT NULL,
  max_input_tokens  INTEGER NOT NULL,
  is_active         BOOLEAN NOT NULL,        -- exactly one active; used by /search
  status            TEXT NOT NULL,           -- registered|backfilling|ready|retired
  created_at        TIMESTAMP NOT NULL
);

-- Page vector: L2-normalized mean of chunk vectors, per model. Drives clustering + similar_to.
CREATE TABLE page_vectors (
  page_id     TEXT NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
  model_id    TEXT NOT NULL REFERENCES embedding_models(id),
  vector      BLOB NOT NULL,
  PRIMARY KEY (page_id, model_id)
);

-- Corpus-internal canonicalization. No Wikidata, no external service.
CREATE TABLE entities (
  id               TEXT PRIMARY KEY,
  canonical_form   TEXT NOT NULL,            -- most frequent surface form
  type             TEXT NOT NULL,            -- person|org|product|technology|concept|place|work
  mention_count    INTEGER NOT NULL DEFAULT 0,
  page_count       INTEGER NOT NULL DEFAULT 0,
  idf              REAL,                     -- ln(N_pages / page_count), refreshed on cluster run
  UNIQUE (canonical_form, type)
);

CREATE TABLE entity_aliases (
  surface_form  TEXT NOT NULL,
  normalized    TEXT NOT NULL,               -- casefold, punct-stripped, singularized
  entity_id     TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  PRIMARY KEY (surface_form, entity_id)
);

CREATE TABLE entity_mentions (
  entity_id     TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  page_id       TEXT NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
  chunk_id      TEXT REFERENCES chunks(id) ON DELETE CASCADE,
  surface_form  TEXT NOT NULL,
  char_start    INTEGER,
  char_end      INTEGER
);

-- Stable across runs via Jaccard matching.
CREATE TABLE clusters (
  id            TEXT PRIMARY KEY,            -- stable; survives re-clustering
  label         TEXT,                        -- LLM-generated, c-TF-IDF fallback
  keywords      TEXT,                        -- JSON array, c-TF-IDF top terms
  size          INTEGER NOT NULL,
  centroid      BLOB,
  model_id      TEXT NOT NULL,
  created_at    TIMESTAMP NOT NULL,
  updated_at    TIMESTAMP NOT NULL,
  tombstoned_at TIMESTAMP
);

CREATE TABLE cluster_members (
  cluster_id  TEXT NOT NULL REFERENCES clusters(id) ON DELETE CASCADE,
  page_id     TEXT NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
  probability REAL,                          -- HDBSCAN soft membership
  PRIMARY KEY (cluster_id, page_id)
);

CREATE TABLE cluster_runs (
  id             TEXT PRIMARY KEY,
  trigger        TEXT NOT NULL,              -- manual|cron|idle
  algorithm      TEXT NOT NULL,
  params         TEXT NOT NULL,              -- JSON
  started_at     TIMESTAMP NOT NULL,
  finished_at    TIMESTAMP,
  duration_ms    INTEGER,
  n_pages        INTEGER,
  n_clusters     INTEGER,
  n_noise        INTEGER
);

-- created | persisted | split | merged | dissolved
CREATE TABLE cluster_lineage (
  run_id       TEXT NOT NULL REFERENCES cluster_runs(id),
  event        TEXT NOT NULL,
  cluster_id   TEXT NOT NULL,
  parent_ids   TEXT,                         -- JSON array
  jaccard      REAL
);

CREATE TABLE blacklist (
  id          TEXT PRIMARY KEY,
  pattern     TEXT NOT NULL UNIQUE,          -- exact canonical_url or domain glob
  kind        TEXT NOT NULL,                 -- url|domain
  reason      TEXT,
  created_at  TIMESTAMP NOT NULL
);

-- Durable in-process queue.
-- TODO: consider whether this is really necessary if we're using huey
CREATE TABLE jobs (
  id            TEXT PRIMARY KEY,
  kind          TEXT NOT NULL,               -- index_page|fetch_and_index|extract_entities|backfill_model|cluster|canonicalize_entities|purge_vectors
  payload       TEXT NOT NULL,               -- JSON
  status        TEXT NOT NULL,               -- pending|running|done|failed|dead
  attempts      INTEGER NOT NULL DEFAULT 0,
  max_attempts  INTEGER NOT NULL DEFAULT 5,
  lease_until   TIMESTAMP,
  last_error    TEXT,
  created_at    TIMESTAMP NOT NULL,
  updated_at    TIMESTAMP NOT NULL
);
CREATE INDEX idx_jobs_pending ON jobs(status, created_at);
```

Consider whether pydantic should be used to ingest query results into well typed objects.

---

## 5. Ingest

### 5.1 Contract

```
POST /v1/pages
Authorization: Bearer <token>

{
  "url": "https://example.com/article?utm_source=x",
  "title": "Article Title",
  "body_extracted": "Plain text main body content...", // Mutually exclusive with 'body_html', throw an error if both are present. Store this directly in body_text table column
  "body_html"// This system does extraction with trasnforms library and https://huggingface.co/feyninc/pulpie-orange-small/ the stores markdown output results in body_text column
  "fetched_at": "2026-07-08T10:00:00Z",
  "source": "extension",
  "metadata": { "arbitrary": "passthrough" }
}

→ 202 { "page_id": "...", "status": "queued" }
→ 200 { "page_id": "...", "status": "indexed", "revisit": true }   // known canonical_url
→ 403 { "error": "blacklisted", "pattern": "bank.com" }
```

There is **one ingest endpoint**. Manual adds go through it. The caller always supplies body text.

If neither body_extracted nor body_html are provided, attempt to load `url` and extract content using pulpie via huggingface's transforms library (if html) or another extractor for other document types e.g. PDF. Note that this implies an extraction router based on the http response's content-type. Other types may be supported in the future.

Note that some embedders are natively multi-model (e.g. cohere embed-v4.0) and can support image and PDF embeddings natively. 

### 5.2 URL canonicalization

Lowercase scheme + host; strip default port; strip `www.`; strip fragment; strip tracking params (`utm_*`, `fbclid`, `gclid`, `ref`, `si`, configurable); sort remaining query params; strip trailing slash. Per-domain override rules (e.g. YouTube: keep only `v`).

### 5.3 Revisit semantics

On a POST whose `canonical_url` already exists:

- `last_seen_at` ← now, `visit_count` += 1
- `body_text` in the request is **discarded**, even if `content_hash` differs
- response includes `revisit: true` and the differing-hash flag for observability

Two distinct URLs with the same `content_hash` remain two pages. Near-duplicate collapsing is out of scope for v1; the clusterer will co-locate them.

Artchitecture shoud support both recapture and collapsing for v2.

### 5.4 Indexing pipeline (async, queued)

`POST` returns immediately. A durable `index_page` job runs:

1. **Chunk** — canonical, model-independent, sentence-aware. Target **448 tokens, 64 overlap, Configurable hard max (default 512)** (cl100k tokenizer as the canonical counter).
   - **Constraint:** canonical chunk max ≤ `min(max_input_tokens)` across all registered models. Cohere `embed-v3` caps at 512 - but we should not be required to limit to this max in general. Registering a model with a smaller budget is **rejected** — accepting it would force a re-chunk, invalidating every other model's index and destroying A/B comparability.
   - Use Chonkie https://github.com/feyninc/chonkie for chunking
2. **Embed** — for every model with `status ∈ {ready, backfilling}`, embed all chunks, upsert into `chunks__{model_id}` with the shared sparse BM25 vector and payload `{page_id, ordinal, domain, first_seen_at, cluster_id}`.
   1. Use a wrapper library e.g. https://github.com/feyninc/catsu so we can easily support any of e..g. Voyage 4/3.5, OpenAI `text-embedding-3-small/large`, Cohere Embed

3. **Roll up** — `page_vector = normalize(mean(chunk_vectors))` per model. *Tradeoff: mean-pooling washes out topically heterogeneous long pages. Accepted for v1; max-pool variant is a config flag.*
4. `status ← indexed`, then enqueue `extract_entities` keyed by `(page_id, content_hash)`.
5. **Extract entities** — full body. LLM (preferred) or GLiNER / spaCy / gazetteer. This is a durable enrichment job.
6. **Canonicalize entities** — incremental (§7). Entity job failure is visible in `/v1/jobs`, logs, and `/v1/pages/{id}/status` feature warnings but does not change page retrieval status.

**Idempotency.** Job key = `(page_id, content_hash)`. Re-enqueue is a no-op.

**Failure.** Core indexing failures best-effort delete chunks, page vectors, and vector-store points; then the page is `failed` and, after exhausted attempts, `dead`. Dead jobs are queryable and manually re-enqueueable. Search, exact matches, suggestions, and similarity only use `indexed` pages.

**Recovery.** On startup, `running` jobs past `lease_until` reset to `pending`.

---

## 6. Retrieval

### 6.1 Pipeline

```
query
  ├─ embed_query(active_model) ────▶ dense top-N   ┐
  └─ bm25 sparse ──────────────────▶ sparse top-N  ├─▶ RRF fuse (k=60) ─▶ top-C chunks
                                                    ┘
                                                          │
                                                    rerank (cross-encoder)
                                                          │
                                                    roll up to pages
                                                          │
                                                    top-k pages + suggestions
```

Consider addin exact URL/title/domain boosts and phrase search as first-class features for sparse.

### 6.2 Parameters (all query-configurable; defaults shown)

| Param | Default | Notes |
|---|---|---|
| `k` | 10 | pages returned |
| `candidates` | 100 | chunks retrieved per arm, and post-fusion set size |
| `rerank` | `true` | disable for latency |
| `chunks_per_page` | 2 | matched chunks returned per page, **whole chunk text** |
| `rollup` | `max` | `max` \| `mean_top_m` \| `sum_rrf` |
| `rrf_k` | 60 | |
| `suggest` | 3 | related pages appended to response |
| `mediation` | `vector` | for suggestions & `similar_to` |
| `recency_decay` | `off` | optional half-life in days |

**Rollup formula (default):** `page_score = max(chunk_scores) + 0.15 · ln(1 + n_matching_chunks − 1)`

CAUTION: Need to consider alternatives here. This is a magic number formula. Rerankers are highly calibrated; manipulating their scores with arbitrary logarithmic boosts will likely destroy your relevance ranking. Stick to standard max-pooling or mean-pooling before inventing new math. Also consider using only `max` or `mean_top_m` rollup first, only adding matching-chunk bonuses if eval proves they improve nDCG/MRR.

### 6.3 Filters (first-class, pushed into the store)

`domain`, `after`, `before`, `cluster_id`, `entity_id`. **No `embedding_model` filter** — model selection is global (`is_active`) or via `/compare`.

### 6.4 Response shape

```json
{
  "query_id": "...",
  "results": [{
    "page_id": "...",
    "canonical_url": "...",
    "title": "...",
    "domain": "...",
    "first_seen_at": "...",
    "visit_count": 3,
    "score": 0.87,
    "cluster": { "id": "...", "label": "Hexagonal architecture" },
    "chunks": [
      { "chunk_id": "...", "ordinal": 4, "text": "<whole chunk>", "score": 0.87 }
    ]
  }],
  "suggestions": [{ "page_id": "...", "title": "...", "reason": "vector" }],
  "timing_ms": { "embed": 12, "dense": 8, "sparse": 4, "fuse": 1, "rerank": 140, "rollup": 2 }
}
```

Snippets (whole matched chunks) by default. Full `body_text` only from `GET /v1/pages/{id}`.

### 6.5 Reranker

Local (`bge-reranker-v2-m3`, `mxbai-rerank`) and API (**Cohere Rerank 3.5** and **Voyage rerank-2.5** are first-class) behind one port. Reranks **chunks**, then rolls up. Reranking pages would discard the chunk-level signal that makes long pages findable.

Use https://github.com/answerdotai/rerankers to wrap the API's

### 6.6 Similarity & suggestions

`similar_to(page_id, mediation=vector|cluster|entity, k)`

- **`vector`** (default) — page-vector cosine kNN, excluding source
- **`cluster`** — co-members of the source's cluster, ranked by page-vector cosine to source
- **`entity`** — pages sharing canonical entities, ranked by IDF-weighted Jaccard over entity sets

Same mechanism backs the `suggestions` block on `/search` (seeded from the top result, excluding pages already returned).

### 6.7 Compare (A/B)

```
POST /v1/compare { "query": "...", "models": ["voyage-3", "bge-m3"], "k": 10 }
```

Runs the full pipeline once per model. The sparse arm and the reranker are **identical across arms**, so the delta isolates the embedder. Returns per-model ranked lists plus agreement stats: **Jaccard@k**, **RBO** (p=0.9), **Kendall's τ** over the intersection. Logged for offline eval.

---

## 7. Entities

**Extraction.**  Primary chain: GLiNER → spaCy NER → gazetteer. Selected by config. Startup fails if the configured chain has no healthy extractor; the default quickstart installs the `ner` extra. User configurable: LLM with structured JSON output, fixed type taxonomy (`person, org, product, technology, concept, place, work`), with character offsets.

**`gliner-spacy`**, wraps the GLiNER zero-shot NER model as a spaCy pipeline component and can be combined with spaCy’s built-in gazetteer-style tools (like the `EntityRuler`). This combination gives the best of both worlds: GLiNER's zero-shot transformer capabilities for dynamic entity recognition, and a strict dictionary-based gazetteer for absolute precision on known terms.

**Canonicalization is corpus-internal.** No Wikidata, no external KB.

*Incremental (per page, at ingest):*
1. Normalize surface form: casefold, strip punctuation/diacritics, singularize.
2. Exact match against `entity_aliases.normalized` → link.
3. Blocking on first-token + type, then match against block members by `cosine(embedding) ≥ 0.85` **or** `normalized_edit_distance ≤ 0.15`.
4. Match → add alias. No match → create entity.

*Periodic (with each cluster run):* full agglomerative re-canonicalization within blocks; merge entities, rewrite aliases, refresh `mention_count`, `page_count`, `idf`.

`canonical_form` = highest-frequency surface form. Merges are logged; they change `entity_id` and therefore invalidate `entity_id` filters held by callers — clients should filter by canonical form or re-resolve.

Corpus-internal canonicalization is safer than Wikidata for personal browsing, but it needs provenance and undo. Corpus-internal canonicalization is safer than Wikidata for personal browsing, but it needs provenance and undo.

---

## 8. Clustering

**Algorithm: HDBSCAN over UMAP-reduced page vectors.** User-selectable; HDBSCAN is default.

| | Shape | k required | Noise | Stable IDs |
|---|---|---|---|---|
| **HDBSCAN** (default) | arbitrary density | no | **yes (−1)** | via matching layer |
| KMeans | spherical, equal-size | yes | no — forces every page in | trivial |
| Leiden (kNN graph) | arbitrary | resolution param | no | via matching layer |

**Why HDBSCAN.** A reading corpus is lumpy: a few obsessions and a long tail of one-offs. KMeans forces the one-offs into a bogus cluster. HDBSCAN labels them noise, which is the correct answer.

**Defaults:** `UMAP(n_components=10, n_neighbors=15, min_dist=0.0, metric='cosine', random_state=42)` → `HDBSCAN(min_cluster_size=5, min_samples=3, metric='euclidean', cluster_selection_method='eom')`. Minimum 50 indexed pages before the first run. Always a **full refit** — at 10k pages this is seconds, and UMAP is the only meaningful cost.

However, Instead of rigid UMAP parameters, the system should dynamically adjust `min_samples` and `min_cluster_size` based on the total corpus size. Instead of rigid UMAP parameters, the system should dynamically adjust `min_samples` and `min_cluster_size` based on the total corpus size.

Keep HDBSCAN default, but compare against “no UMAP + HDBSCAN/Leiden over kNN graph” on your corpus. Make min cluster size dynamic.

### 8.1 Stable IDs (Jaccard + Hungarian)

The algorithm does not give you stable IDs. A matching layer does:

1. After a run, build the cost matrix `C[i][j] = 1 − Jaccard(members(new_i), members(old_j))`.
2. Hungarian assignment (`scipy.optimize.linear_sum_assignment`).
3. Accept a match where `Jaccard ≥ 0.5` → new cluster **inherits** the old `id`. Emit `persisted`.
4. Unmatched new cluster → fresh `id`. Emit `created`; if it overlaps >0.3 with an old cluster, emit `split` with `parent_ids`.
5. Unmatched old cluster → `tombstoned_at` set (rows retained). Emit `dissolved`, or `merged` if its members landed predominantly in one new cluster.

Tombstoned clusters are excluded from `list_clusters` but resolvable by ID, so stale agent references degrade gracefully.

Consdier making merges reversible and expose canonical-form filters, not only unstable IDs.

### 8.2 Labeling

LLM over the 10 nearest-centroid page titles + top c-TF-IDF terms → short noun-phrase label. Fallback: top-5 c-TF-IDF terms joined. `keywords` is always populated regardless of path. Keep c-TF-IDF always populated. LLM labels should be cosmetic, cached, and non-blocking.

### 8.3 Triggers

- **Manual** — `POST /v1/clusters/recompute`
- **Cron** — configurable schedule
- **Idle** — no ingest for `idle_threshold`, **and** ≥ `min_new_pages` (default 20) indexed since the last run.

`idle_threshold = clamp(median(duration_ms of last 5 runs) × 3, 5min, 60min)`

*Interpretation of "record previous cluster timings and use that as threshold": the system should never re-cluster more often than clustering costs. Flag if you meant inter-arrival gaps of ingest instead.*

---

## 9. Deletion & Blacklist

`POST /v1/forget { "url": ... }` or `{ "domain": ... }` — **purges and blacklists atomically.**

Purge: delete `pages` row (cascades chunks, mentions, cluster_members) → delete vectors from **every** model collection → decrement/recompute entity counts, garbage-collect orphaned entities → mark clusters stale.

Blacklist: insert rule. Subsequent `POST /v1/pages` matching it → **403** (not a silent 202 — upstream should know it's wasting work).

Transaction plan: metadata tombstone, vector delete retry queue, and verification job.

`GET /v1/blacklist`, `DELETE /v1/blacklist/{id}` (un-blacklist; does not restore purged content).

---

## 10. Interfaces

All external inputs must be validated with pydantic.

### 10.1 HTTP (FastAPI)

Bearer token **always required**, even on loopback. Bound to `127.0.0.1` by default.

```
POST   /v1/pages                      ingest (single endpoint, body_extracted XOR body_html)
GET    /v1/pages/{id}                 full body_text + metadata
GET    /v1/pages/{id}/status          queued|indexing|indexed|failed|dead + features.entities
GET    /v1/pages/{id}/similar         ?mediation=vector|cluster|entity&k=
GET    /v1/pages/{id}/entities
POST   /v1/search
POST   /v1/compare
GET    /v1/clusters                   ?include_tombstoned=false
GET    /v1/clusters/{id}              label, keywords, member pages
POST   /v1/clusters/recompute
GET    /v1/entities/{id}              canonical form, aliases, pages
POST   /v1/forget                     purge + blacklist
GET    /v1/blacklist
DELETE /v1/blacklist/{id}
POST   /v1/models                     register embedding model
POST   /v1/models/{id}/backfill
POST   /v1/models/{id}/activate
DELETE /v1/models/{id}                retire (drop collection)
POST   /v1/feedback                   { query_id, page_id, relevant }
GET    /healthz  /readyz  /metrics
```

### 10.2 MCP (stdio + streamable HTTP)

Tools mirror the REST surface, shaped for an agent:

`search` · `get_page` · `page_status` · `similar_to` · `list_clusters` · `cluster_pages` · `entities` · `add_page` · `forget` · `compare`

Tool descriptions state explicitly: *returns grounded passages from the user's reading history; contains no information the user has not read; returns empty when nothing matches.* This is what makes hard-grounding hold at the agent layer.

IMPORTANT: Make mutating MCP tools opt-in. Treat retrieved page text as untrusted content.

---

## 11. Deployment Profiles

Two options:

- Minimal SQLite + LanceDB + local embeddings, installable with `uv` 
- Docker option: `docker compose` also bundles Qdrant + Ollama

Single non-blocking process — API and queue workers are asyncio tasks in one runtime, not separate services. CPU-bound work (UMAP, HDBSCAN, local reranker, local embeddings) runs in a `ProcessPoolExecutor` so the event loop never blocks.

---

## 12. Observability

Maximum instrumentation from day one. Evals are v2, but the data they need is captured now.

**OpenTelemetry traces.** Spans: `ingest`, `chunk`, `embed` (per model), `vector.upsert`, `entity.extract`, `entity.canonicalize`, `search`, `search.dense`, `search.sparse`, `search.fuse`, `search.rerank`, `search.rollup`, `cluster.umap`, `cluster.hdbscan`, `cluster.match`, `cluster.label`.

**Structured JSON logs** with trace correlation.

**`query_log` (DuckDB), the eval substrate:**

| Column | Why |
|---|---|
| `query_id`, `ts`, `query_text`, `params` | reconstruct the run |
| `active_model`, `reranker_model` | attribute regressions |
| `candidate_set` | **full pre-rerank chunk IDs + fusion scores** — lets you measure reranker lift offline without re-running retrieval |
| `dense_hits`, `sparse_hits` | measure which arm is carrying |
| `final_pages` | ranked page IDs + scores |
| `timing_ms` | per-stage breakdown |
| `feedback` | joined from `POST /v1/feedback` |

**Prometheus `/metrics`:** ingest rate, queue depth, job failure rate, search p50/p95/p99, rerank latency, embedding API error rate, cluster run duration.

**v2 eval harness.** Golden set assembled retroactively from `query_log` + feedback. Metrics: nDCG@10, Recall@100 (candidate-set quality, isolates retrieval from reranking), MRR, reranker lift (nDCG post-rerank − nDCG pre-rerank), and per-model deltas from `/compare` runs.

---

## 13. Milestones

**M1 — Skeleton.** Domain model, ports, SQLite adapter, job queue, `POST /v1/pages` → chunk → embed (one model) → Qdrant. `GET /v1/pages/{id}`, `/status`. Token auth.

**M2 — Retrieval.** Hybrid dense+sparse, RRF, reranker port (Cohere + local), rollup, filters, `POST /v1/search`. `query_log` from the first query.

**M3 — Agent surface.** MCP server (stdio + HTTP), all tools. LanceDB adapter + conformance suite. `forget` / blacklist.

**M4 — Structure.** Entity extraction + corpus-internal canonicalization. HDBSCAN clustering + Jaccard/Hungarian stable IDs + lineage. `similar_to` with three mediations. Cluster labeling.

**M5 — Multi-model.** Model registry, backfill jobs, `POST /v1/compare` with Jaccard/RBO/τ. Full OTel. DuckDB observability sink.

**v2 (deferred):** TUI, eval harness, push/spaced-recall, Postgres adapter.

---

## 14. Open Questions

1. **Local-mode LLM.** Require user configuration e.g. Ollama? Degrade (with warning?)  to GLiNER + c-TF-IDF?
2. **Idle threshold.** Confirm the interpretation in §8.3 — run-duration-derived, not ingest-inter-arrival.
3. **Page vector pooling.** Mean-pool washes out heterogeneous long pages. Worth a max-pool or top-k-chunk-centroid variant, or ship mean?
   1. Answer: Mean-pooled page vectors are cheap but can wash out long heterogeneous pages. Entity overlap and cluster co-membership are useful secondary signals. Store page mean vector, but also store representative chunk vectors or top-k centroid chunks. Use suggestions as an explainable blend: vector + shared entities + cluster.
4. **`entity_id` instability.** Periodic merges change entity IDs. Should `/search?entity=` accept canonical form strings rather than IDs, so agent-held references survive? Probably yes.
5. **Near-duplicate URLs.** Same article on two domains stays two pages in v1. Ever want content-hash-mediated collapse? Support hashing now so that this can be implemented in v2
6. **Recency.** `recency_decay` is off by default. Confirm — a reading corpus arguably wants mild recency bias on refind.
7. **Backfill cost.** Re-embedding 10k pages / 200k chunks against Voyage is real money and real minutes. Rate-limit and cost-estimate the backfill job before running it?\
