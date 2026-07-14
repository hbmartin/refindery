# Quickstart

This walks through ingesting a page and searching for it end-to-end. It assumes
a running server (see [Installation](installation.md)) and that
`REFINDERY_AUTH_TOKEN` is set in your shell.

## 1. Ingest a page

Upstream capture normally does this, but you can `POST` a page directly. The
call returns `202 Accepted` immediately — indexing happens asynchronously on the
job queue.

```bash
curl -s -X POST http://127.0.0.1:8000/v1/pages \
  -H "Authorization: Bearer $REFINDERY_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/article", "title": "An Article",
       "body_extracted": "Plain text main body content...",
       "fetched_at": "2026-07-08T10:00:00Z", "source": "extension"}'
```

The three body modes — `body_extracted`, `body_html`, or neither (server fetches)
— are covered in the [Ingesting pages guide](../guides/ingest.md) and the
[Upstream ingest API](../reference/upstream-ingest-api.md).

## 2. Watch it reach `indexed`

Ingestion moves the page through `queued → indexing → indexed`. Poll its status
(use the `page_id` from the ingest response):

```bash
curl -s http://127.0.0.1:8000/v1/pages/<page_id>/status \
  -H "Authorization: Bearer $REFINDERY_AUTH_TOKEN"
```

Only `indexed` pages are searchable. See the
[page lifecycle](../reference/upstream-ingest-api.md#page-lifecycle-status-values).

## 3. Search

```bash
curl -s -X POST http://127.0.0.1:8000/v1/search \
  -H "Authorization: Bearer $REFINDERY_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "main body content"}'
```

The response is a ranked list of pages, each with its best matching chunks and a
per-stage timing breakdown. How that ranking is produced — hybrid retrieval,
fusion, reranking, rollup — is explained in the
[Searching guide](../guides/search.md).

## 4. Connect an agent over MCP

The MCP server is served over streamable HTTP at `/mcp` with the same bearer
token:

```bash
claude mcp add --transport http refindery http://127.0.0.1:8000/mcp \
  --header "Authorization: Bearer $REFINDERY_AUTH_TOKEN"
```

Read-only tools (`search`, `get_page`, `similar_to`, `list_clusters`, …) are
exposed by default; mutating tools are opt-in. See the
[MCP guide](../guides/mcp.md).

## 5. Open the admin UI

Published wheels bundle the cockpit admin UI. With the server running, open
<http://127.0.0.1:8000/admin> and paste your `REFINDERY_AUTH_TOKEN` when
prompted — it is held client-side and sent as a bearer token on every API call
from the same origin, so no CORS or login flow is involved. See the
[Admin UI guide](../guides/admin-ui.md).

## Next steps

- [Validate the install](validate.md) — health/readiness and a resolved-config dump.
- [Guides](../guides/index.md) — clustering, entities, evaluation, and more.
- [Configuration](../configuration/index.md) — tune ranking, jobs, and providers.
