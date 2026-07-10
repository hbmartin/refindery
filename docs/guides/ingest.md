# Ingesting pages

There is **one ingest endpoint**, `POST /v1/pages`. Every new page — from a
browser extension, a history importer, or a manual add — goes through it. The
caller supplies the body text; Refindery does the rest asynchronously.

For the exact request/response contract (fields, status codes, examples), see
the [Upstream ingest API](../reference/upstream-ingest-api.md). This guide
explains what happens to a page once it arrives.

## The three body modes

The request carries the page body in one of three ways:

| Mode | Field | What Refindery does |
| --- | --- | --- |
| **Pre-extracted** (preferred) | `body_extracted` | Stores the plain text directly. |
| **HTML** | `body_html` | Extracts main-body markdown (requires the `html` extra). |
| **URL only** | *(neither)* | Fetches the URL and routes by content type (HTML/PDF). |

`body_extracted` and `body_html` are mutually exclusive. URL-only ingestion
enqueues a `FETCH_AND_INDEX` job instead of `INDEX_PAGE`.

## The ingest → index → enrich flow

```
POST /v1/pages ──▶ canonicalize URL ──▶ blacklist check ──▶ upsert page ──▶ enqueue job
       │                                                                        │
    202 Accepted (returned immediately)                                         ▼
                                                              INDEX_PAGE / FETCH_AND_INDEX
                                                                   │
                                       extract (if needed) → chunk → embed → upsert vectors
                                                                   │
                                                          status ← indexed
                                                                   │
                                                          enqueue EXTRACT_ENTITIES
```

1. **Ingest** (`IngestService`) — canonicalize the URL (tracking params stripped,
   per-domain keep rules), reject blacklisted patterns with `403`, upsert the
   page (one row per canonical URL, never versioned), and enqueue a durable job.
   The API returns `202` immediately.
2. **Index** (`IndexingService`, via the queue consumer) — extract content if
   needed, [chunk](../configuration/tuning.md) it (sentence-aware, model
   independent), embed each chunk with the active model, and upsert chunk
   vectors plus a pooled page vector into the [vector store](../architecture/index.md).
   The page becomes `indexed`, and an `EXTRACT_ENTITIES` job is enqueued.
3. **Enrich** ([entities](entities.md)) — run the extractor chain over the page
   and canonicalize mentions into entities. This is an *enrichment* job: its
   failure is visible under `features.entities` but does **not** change the
   page's retrieval status.

## URL canonicalization

Two requests for the "same" page must collapse to one row. Canonicalization
lowercases the scheme and host, strips the default port, `www.`, the fragment,
and tracking parameters (`utm_*`, `fbclid`, `gclid`, `ref`, `si`, …), sorts the
remaining query params, and strips the trailing slash. Per-domain overrides
apply where the query matters (e.g. YouTube keeps only `v`). Configure overrides
with `REFINDERY_CANONICALIZE__*`.

## Revisit semantics

When a `POST` arrives for a `canonical_url` that already exists:

- `last_seen_at` is set to now and `visit_count` is incremented;
- the request's `body_text` is **discarded**, even if its content hash differs;
- the response reports `revisit: true` plus a differing-hash flag for observability.

Two distinct URLs with identical content remain two pages in v1 — the
[clusterer](clustering.md) co-locates them. (Content hashes are stored so that
content-mediated collapse can be added in v2.)

## Page lifecycle

```
queued ──▶ indexing ──▶ indexed
                   │
                   ├──▶ failed ──▶ dead   (after exhausted attempts)
```

Only `indexed` pages are used by search, similarity, and suggestions. Core
indexing failures best-effort delete partial chunks and vectors before marking
the page `failed`; `dead` jobs are queryable and manually re-enqueueable. See
[Operations](../operations/index.md) for the job lease and recovery model, and
the [status reference](../reference/upstream-ingest-api.md#page-lifecycle-status-values).

## Related

- [Upstream ingest API](../reference/upstream-ingest-api.md) — the full contract for capture clients.
- [Tuning](../configuration/tuning.md) — chunking and fetch limits.
- [Deletion & blacklist](deletion.md) — removing content and blocking re-ingestion.
