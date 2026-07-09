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

Jobs are lease-only. A worker does not interrupt long-running work when a
lease expires; recovery happens on startup by resetting expired leases and
re-enqueueing pending jobs. Handlers are expected to be idempotent through
stable ids and upserts.

`extract_entities` jobs are enrichment jobs. Their failures appear in
`/v1/jobs`, logs, and `GET /v1/pages/{id}/status` under
`features.entities`, but they do not change page retrieval status.

## Vector Adapter Caveats

Public model ids are preserved in the API. Vector adapters derive internal
storage names from a readable slug plus a stable hash so model ids containing
slashes or provider punctuation remain safe.

LanceDB runs in process and is suitable for zero-daemon local development.
Qdrant is the default daemon-backed store and reconciles payload indexes on
startup.

## Accepted Operational Risks

This alpha keeps two operational risks explicit:

- Job execution is lease-only; active jobs are not force-cancelled at timeout.
- DuckDB query logs retain raw query text until manually purged.
