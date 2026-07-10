# Operations

## Required NER Setup

Entity extraction is required for startup. Install the default spaCy-backed
chain with:

```bash
uv sync --extra ner
```

If startup reports no healthy extractor, either install the `ner` extra,
configure `REFINDERY_ENTITY__EXTRACTOR_CHAIN`, provide a gazetteer file via
`REFINDERY_ENTITY__GAZETTEER_PATTERNS_PATH`, or configure
`REFINDERY_LLM__BASE_URL` for the `llm` extractor.

Surface-form embeddings are optional. When they fail to load, entity
canonicalization still uses exact and edit-distance matching; cosine matching
is skipped until the embedder can load.

## Alpha Reset

The default local state paths are:

- SQLite metadata: `data/refindery.db`
- Huey queue: `data/huey.db`
- LanceDB vectors: `data/lancedb`
- DuckDB query log: `data/observability.duckdb`

Stop the server, then reset local alpha state with:

```bash
rm -f data/refindery.db data/huey.db data/observability.duckdb
rm -rf data/lancedb
```

For Qdrant, reset the collection configured by
`REFINDERY_QDRANT__COLLECTION` from the Qdrant dashboard or API.

## Query-Log Purge

Query logs intentionally retain raw query text, candidate hits, and final
pages for offline evaluation. To manually purge all logs from the default
DuckDB file:

```sql
DELETE FROM feedback;
DELETE FROM query_log;
CHECKPOINT;
```

Run that SQL with DuckDB after stopping the server or when no writes are in
flight.

## Job Lease Model

Jobs are cooperatively cancelled at lease expiry: the handler coroutine is
cancelled (`asyncio.timeout`), the attempt is recorded as a failure with the
normal retry/backoff/dead-letter path, and the single worker moves on to the
next job. A handler blocked inside native or thread code (a provider SDK
call) cannot be interrupted — the worker is freed but the blocked thread may
leak until the underlying call returns. Startup recovery still resets any
expired leases left by a crashed process and re-enqueues pending jobs.
Handlers are expected to be idempotent through stable ids and upserts. A
once-a-minute watchdog logs RUNNING jobs past their lease and exposes them
via the `refindery_jobs_lease_expired` gauge; it never re-enqueues while the
process is live.

The timeout defaults to `REFINDERY_JOBS__LEASE_MINUTES` and can be overridden
with `REFINDERY_JOBS__HANDLER_TIMEOUT_S`.

## Provider Resilience

Calls to embedding, reranking, and LLM providers run behind a per-provider
circuit breaker with in-call retry and a per-attempt timeout:

- Transient failures (timeouts, connection errors, 408/429/5xx) are retried
  up to `REFINDERY_RESILIENCE__RETRY_ATTEMPTS` times with jittered
  exponential backoff.
- After `REFINDERY_RESILIENCE__BREAKER_FAILURE_THRESHOLD` consecutive
  transient failures the breaker opens and calls fast-fail for
  `REFINDERY_RESILIENCE__BREAKER_COOLDOWN_S` seconds, then a single probe
  call decides whether it closes again.
- A job failing because a breaker is open is requeued **without** burning a
  retry attempt, so a backlog survives an outage of any length; a
  permanently dead provider shows up as an endless deferral loop in logs and
  the `refindery_circuit_breaker_state` / `refindery_circuit_breaker_open_total`
  metrics.
- Search degrades to fusion-only ranking when reranking fails (counted by
  `refindery_rerank_degraded_total`); `/v1/compare` fails loudly instead, so
  a degraded arm can never corrupt agreement statistics.

Per-attempt timeouts: `REFINDERY_RESILIENCE__EMBED_TIMEOUT_S` (60s),
`REFINDERY_RESILIENCE__RERANK_TIMEOUT_S` (15s), `REFINDERY_LLM__TIMEOUT_S`
(30s).

`extract_entities` jobs are enrichment jobs. Their failures appear in
`/v1/jobs`, logs, and `GET /v1/pages/{id}/status` under
`features.entities`, but they do not change page retrieval status.

Startup also reconciles the indexed-to-enqueue crash window: any indexed
page whose current content has no `extract_entities` job gets one enqueued
before the consumer starts, so a `not_queued` feature status heals itself on
the next restart.

## Vector Adapter Caveats

Public model ids are preserved in the API. Vector adapters derive internal
storage names from a readable slug plus a stable hash so model ids containing
slashes or provider punctuation remain safe.

LanceDB runs in process and is suitable for zero-daemon local development.
Qdrant is the default daemon-backed store and reconciles payload indexes on
startup.

## Accepted Operational Risks

This alpha keeps two operational risks explicit:

- Lease-expiry cancellation is cooperative; a handler blocked in native or
  thread code frees the worker but may leak a thread until the call returns.
- DuckDB query logs retain raw query text until manually purged.
