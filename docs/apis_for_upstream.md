# APIs for Upstream Sources

This document enumerates, in detail, the Refindery HTTP APIs that an **upstream
source** should use. An upstream source is any client that feeds pages into
Refindery — a browser extension recording pages the user reads, a bookmark or
history importer, a crawler, or a manual "add this URL" button. Its job is to
push content in, track that content through indexing, and manage removal.
Retrieval endpoints (`/v1/search`, `/v1/compare`, clustering, entities) are for
*consumer* clients and are out of scope here; this doc covers the *ingest and
lifecycle* surface.

All endpoints are served on loopback by default (`127.0.0.1:8000`) and every
request — even on loopback — requires a bearer token.

---

## Quickstart

Fast path to a working upstream integration.

### 1. Start the server

```bash
# Required: the shared bearer token upstream clients must present.
export REFINDERY_AUTH_TOKEN="$(openssl rand -hex 32)"

uv sync --extra ner          # entity extraction is required for startup
python -m refindery          # serves on http://127.0.0.1:8000
```

Override the bind address with `REFINDERY_BIND_HOST` / `REFINDERY_BIND_PORT` if
needed. See `docs/operations.md` for NER setup and local state paths.

### 2. Confirm the server is ready

```bash
curl -s http://127.0.0.1:8000/readyz        # {"status":"ready"} when usable
```

`readyz` returns `503` until the metadata store is reachable **and** an
embedding model is active. Do not begin ingesting until it reports `ready`.

### 3. Send your first page

The one call that matters. Send the URL plus the text you already extracted:

```bash
curl -s -X POST http://127.0.0.1:8000/v1/pages \
  -H "Authorization: Bearer $REFINDERY_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
        "url": "https://example.com/article",
        "title": "An Example Article",
        "body_extracted": "The full readable text of the page ...",
        "source": "my-extension",
        "fetched_at": "2026-07-09T12:00:00Z"
      }'
```

Response `202 Accepted`:

```json
{ "page_id": "pg_...", "status": "queued" }
```

Indexing is **asynchronous**. The `202` means "accepted and queued", not
"searchable yet".

### 4. Track it to `indexed`

```bash
curl -s http://127.0.0.1:8000/v1/pages/pg_.../status \
  -H "Authorization: Bearer $REFINDERY_AUTH_TOKEN"
```

Poll until `status` is `indexed` (or `failed`/`dead`). See
[Page lifecycle](#page-lifecycle-status-values).

### Minimal integration checklist

- [ ] Hold the bearer token; send it on every request.
- [ ] Gate ingestion on `GET /readyz`.
- [ ] `POST /v1/pages` per page; prefer sending `body_extracted`.
- [ ] Handle the three outcomes: `202` (new), `200` (revisit), `403` (blacklisted).
- [ ] Poll `GET /v1/pages/{id}/status` for terminal state.
- [ ] Wire a "forget this" path to `POST /v1/forget`.

---

## Conventions

### Base URL & versioning

All ingest endpoints live under the `/v1` prefix. The base URL defaults to
`http://127.0.0.1:8000`.

### Authentication

Every endpoint below (except the unauthenticated `/healthz` and `/readyz`)
requires an HTTP Bearer token:

```
Authorization: Bearer <REFINDERY_AUTH_TOKEN>
```

The token is the value of the `REFINDERY_AUTH_TOKEN` environment variable set on
the server. A missing or wrong token returns `401 Unauthorized` with
`{"detail": "missing or invalid bearer token"}`. The comparison is
constant-time; there is a single shared token (no per-client keys).

### Content type

Request bodies are JSON (`Content-Type: application/json`). Responses are JSON
unless noted. Request models reject unknown fields (`extra="forbid`") — a typo'd
field name yields `422`, so keep payloads clean.

### Timestamps

All timestamps are **timezone-aware** ISO-8601 (e.g. `2026-07-09T12:00:00Z`).
Naive datetimes are rejected. Fields typed `AwareDatetime` (like `fetched_at`)
must carry an offset.

---

## The core endpoint: `POST /v1/pages`

The single entry point for adding a page. Everything an upstream source does
centers on this call. It is idempotent per canonical URL: the first time a
canonical URL is seen it is created; subsequent calls record a *revisit*.

### Request body (`IngestPageRequest`)

| Field            | Type                     | Required | Notes |
|------------------|--------------------------|----------|-------|
| `url`            | string                   | **yes**  | The URL the user visited. Must have a scheme and host. Canonicalized server-side. |
| `title`          | string \| null           | no       | Page title. |
| `body_extracted` | string \| null           | no*      | Already-extracted plain readable text. Preferred. |
| `body_html`      | string \| null           | no*      | Raw HTML; the server extracts readable text from it. |
| `fetched_at`     | aware datetime \| null   | no       | When the upstream fetched/observed the page. Defaults to server time. Sets `first_seen_at`/`last_seen_at`. |
| `source`         | string \| null           | no       | Free-form label for the upstream (e.g. `"chrome-extension"`, `"history-import"`). Stored and echoed back; use it to distinguish origins. |
| `metadata`       | object (JSON) \| null    | no       | Arbitrary JSON key/values stored with the page and returned on read. |

\* **`body_extracted` and `body_html` are mutually exclusive.** Sending both is
`422`. Sending neither is valid and triggers server-side fetching — see the
three body modes below.

### The three body modes

The value of `body_extracted` / `body_html` decides how the body is resolved:

1. **`body_extracted` set** — Upstream already has clean readable text (best
   case; the extension ran a readability pass). The text is stored as-is and an
   `index_page` job is enqueued. Fastest and most reliable; **prefer this.**

2. **`body_html` set** — Upstream has raw HTML but not clean text. The server
   runs its extraction router (`text/html`, UTF-8) to derive readable text, then
   enqueues `index_page`. Use when you can capture the DOM but don't want to
   ship a readability engine in the client.

3. **Neither set** — Upstream knows only the URL. The page is created with a
   `null` body and a `fetch_and_index` job is enqueued; the server fetches the
   URL asynchronously and extracts the body itself. Use for
   bookmark/history-style imports where you have URLs but no captured content.
   Note this depends on the server being able to reach the URL.

### Outcomes and status codes

The response depends on whether the canonical URL is new, already known, or
blacklisted.

#### `202 Accepted` — new page queued (`IngestAcceptedResponse`)

A previously-unseen canonical URL. Indexing has been queued.

```json
{ "page_id": "pg_abc123", "status": "queued" }
```

Persist `page_id`; it is how you later read, status-check, or correlate the
page. Indexing runs asynchronously — see [Page lifecycle](#page-lifecycle-status-values).

#### `200 OK` — revisit (`IngestRevisitResponse`)

The canonical URL is already known. Refindery records a revisit (bumps
`visit_count` and `last_seen_at`) rather than creating a duplicate — **one row
per canonical URL, never versioned.**

```json
{
  "page_id": "pg_abc123",
  "status": "indexed",
  "revisit": true,
  "content_hash_differs": false
}
```

- `status` — the existing page's current lifecycle status.
- `content_hash_differs` — `true` when you supplied a body (`body_extracted` or
  `body_html`) whose content hash differs from the stored one, i.e. the page
  **changed since it was first indexed**. Refindery does *not* automatically
  re-index on a revisit; if you care about the new content, treat this flag as a
  signal to `forget` + re-ingest, or to surface a "content changed" state to the
  user. If it is `false` (or you sent no body), the stored version stands.

#### `403 Forbidden` — blacklisted (`BlacklistedResponse`)

The canonical URL or its domain matches a blacklist rule (see
[`/v1/forget`](#post-v1forget--purge--blacklist)). The page is **not** ingested.

```json
{ "error": "blacklisted", "pattern": "example.com" }
```

This is expected, not an error condition — respect it silently (don't retry).
The `pattern` tells you which rule matched.

#### `422 Unprocessable Content`

Validation failures: both bodies supplied (`body_extracted and body_html are
mutually exclusive`), a URL with no scheme/host, a naive `fetched_at`, or
unknown fields. The `detail` string explains which.

#### `501 Not Implemented`

The server has no working extraction path for a supplied `body_html` (extraction
unavailable). Fall back to sending `body_extracted`, or omit the body to defer
to `fetch_and_index`.

### Canonicalization (what "same page" means)

Before anything else, the server canonicalizes `url`: it strips tracking
parameters (`utm_*`, `fbclid`, `gclid`, `ref`, `si` by default), applies any
per-domain keep-param rules, and derives the domain. Two URLs that differ only
by tracking noise collapse to the same canonical URL and thus the same page.

Implication for upstream: you do **not** need to strip query junk yourself, and
you should expect that `?utm_source=...` variants of a URL are treated as
revisits of one page. The `original_url` you sent is preserved on the page
record; the `canonical_url` is what dedupes.

---

## Reading a page back: `GET /v1/pages/{page_id}`

Returns the full stored body and metadata for one page. Useful to verify what
was stored/extracted, or to render the upstream's own history view.

Response (`PageResponse`):

```json
{
  "page_id": "pg_abc123",
  "canonical_url": "https://example.com/article",
  "original_url": "https://example.com/article?utm_source=x",
  "domain": "example.com",
  "title": "An Example Article",
  "body_text": "The full readable text ...",
  "source": "my-extension",
  "metadata": { "any": "json" },
  "first_seen_at": "2026-07-09T12:00:00Z",
  "last_seen_at": "2026-07-09T12:00:00Z",
  "visit_count": 1,
  "indexed_at": "2026-07-09T12:00:03Z",
  "status": "indexed"
}
```

`body_text` and `indexed_at` are `null` while a `fetch_and_index` job is still
resolving the body. `404` if the id is unknown.

---

## Tracking indexing: `GET /v1/pages/{page_id}/status`

Lightweight status probe — poll this after a `202` to know when a page is
searchable, or to detect failures. It does not return the body.

Response (`PageStatusResponse`):

```json
{
  "page_id": "pg_abc123",
  "status": "indexed",
  "last_error": null,
  "features": {
    "entities": { "status": "done", "last_error": null }
  }
}
```

- `status` — the page lifecycle value (below). This is the field to poll on.
- `last_error` — populated only when `status` is `failed` or `dead`; carries the
  message from the latest indexing job.
- `features.entities` — status of the **asynchronous entity-extraction**
  enrichment, which runs as a separate `extract_entities` job *after* core
  indexing succeeds. Its `status` is one of the job statuses or `not_queued`
  (no entity job exists yet). Entity extraction failing does **not** fail the
  page — the page is still `indexed` and searchable; only entity-scoped features
  are affected. Treat this as informational.

### Page lifecycle (`status` values)

| Status     | Meaning | Upstream action |
|------------|---------|-----------------|
| `queued`   | Accepted, waiting for an indexing worker. | Keep polling. |
| `indexing` | A worker is embedding/chunking it now. | Keep polling. |
| `indexed`  | **Terminal success.** Searchable. | Done. |
| `failed`   | An indexing attempt failed; retries may remain. | Poll a bit longer; check `last_error`. |
| `dead`     | **Terminal failure** — retries exhausted. | Surface `last_error`; optionally inspect/retry via the jobs API. |

Recommended polling: short backoff (e.g. 1s → 2s → 5s, cap ~30s) until a
terminal state (`indexed` or `dead`). There is no push/webhook; polling is the
mechanism.

---

## Removing content: `POST /v1/forget` — purge + blacklist

The upstream's "delete this / never index this" control. **Destructive.** It
atomically (a) permanently purges matching pages from the index and (b) adds a
blacklist rule so future `POST /v1/pages` calls for that target return `403`.

Request (`ForgetRequest`) — provide **exactly one** of `url` or `domain`:

```json
{ "url": "https://example.com/private", "reason": "user requested" }
```

or

```json
{ "domain": "example.com", "reason": "sensitive site" }
```

- `url` — forget/blacklist a single canonical URL.
- `domain` — forget/blacklist an entire domain (suffix match).
- `reason` — optional free-form note, stored on the rule.

Providing both or neither is `422` (`provide exactly one of url or domain`).

Response (`ForgetResponse`):

```json
{
  "blacklist_id": "bl_...",
  "pattern": "example.com",
  "kind": "domain",
  "pages_purged": 12,
  "vector_deletes_queued": 12
}
```

Vector deletions are queued (a `purge_vectors` job) and complete asynchronously;
metadata purge is immediate. **This deletes user data and is not reversible** —
removing the blacklist rule later (below) does *not* restore purged content.

### Blacklist management

- **`GET /v1/blacklist`** → `BlacklistResponse` — list all rules, newest first.
  Use to show the user what's currently blocked.

  ```json
  { "entries": [ { "id": "bl_...", "pattern": "example.com",
                   "kind": "domain", "reason": "...", "created_at": "..." } ] }
  ```

- **`DELETE /v1/blacklist/{blacklist_id}`** → `204 No Content` — remove a rule so
  future ingests of that target are allowed again. Purged content stays purged.
  `404` if the id is unknown.

---

## Job administration (optional)

Most upstreams don't need these — page status covers the common case — but they
help when a page reaches `dead` and you want to inspect or re-drive the work.

- **`GET /v1/jobs?status_filter=<status>&limit=<n>`** → `JobListResponse` — the
  job ledger, newest first, optionally filtered by job status (`pending`,
  `running`, `done`, `failed`, `dead`). Each row carries `kind`, `status`,
  `attempts`/`max_attempts`, `last_error`, and timestamps. Use it to find
  dead-lettered indexing jobs.

- **`POST /v1/jobs/{job_id}/retry`** → `JobResponse` — reset a **dead** job to
  pending and re-enqueue it. Returns `409 Conflict` if the job is not `dead`
  (only dead jobs are retryable), `404` if unknown. This is the recovery path
  for a page stuck at `dead` after a transient failure.

---

## Health & readiness (unauthenticated)

For orchestration and pre-flight gating. `/healthz` and `/readyz` require **no**
auth (they leak nothing); `/metrics` does.

- **`GET /healthz`** → `{"status":"ok"}` — liveness only (process is up).
- **`GET /readyz`** → `{"status":"ready"}` (200) when the metadata store is
  reachable and an embedding model is active; otherwise `503` with a reason
  (`metadata store unavailable` / `no active embedding model`). **Gate ingestion
  on this** — a `202` before readiness would queue work that can't drain.
- **`GET /metrics`** — Prometheus exposition (bearer auth required; scrapers
  configure `bearer_token`). `ingest_pages_total{outcome=...}` counts ingest
  outcomes (`queued` / `revisit` / `blacklisted`), useful for upstream dashboards.

---

## End-to-end flow (reference)

```
                      ┌──────────────────────────────────────────┐
  upstream source     │              Refindery                    │
  ────────────────    │                                           │
  GET  /readyz  ───────────►  ready?  ──► 200 ready / 503          │
                      │                                           │
  POST /v1/pages ─────────►  canonicalize ─► blacklist? ─► 403     │
   (body_extracted)  │           │no                              │
                      │           ▼                               │
                      │       known URL? ─yes─► 200 revisit        │
                      │           │no  (content_hash_differs?)     │
                      │           ▼                               │
                      │       insert + enqueue ─► 202 {page_id}    │
                      │                                           │
  GET /v1/pages/{id}/status ─►  queued→indexing→indexed|dead       │
                      │                                           │
  POST /v1/forget ───────────►  purge + blacklist ─► 200           │
                      └──────────────────────────────────────────┘
```

### Recommended upstream loop

1. On startup, poll `GET /readyz` until `ready`.
2. For each page the user reads, `POST /v1/pages` with `body_extracted` (fall
   back to `body_html`, or URL-only for imports).
3. Branch on the status code: `202` store the `page_id`; `200` note the
   `content_hash_differs` flag; `403` respect silently.
4. For pages you care about, poll `GET /v1/pages/{id}/status` to a terminal
   state.
5. Expose a "forget" action wired to `POST /v1/forget`, and a blacklist manager
   over `GET`/`DELETE /v1/blacklist`.
6. If a page reaches `dead`, optionally inspect via `GET /v1/jobs` and re-drive
   with `POST /v1/jobs/{id}/retry`.
