# Watch mode

A **watch** turns Refindery from a purely push-based index into one that can
*pull*. Instead of waiting for a browser extension to POST a page, a watch
periodically fetches a source, discovers the article URLs it references, and
ingests each new one — so the content shows up in search without you visiting it.

The first (and currently only) watch kind is **`rss`**: an RSS or Atom feed.
The design is built for *fan-out* sources generally, so future kinds (for
example a sitemap or index-page diff) slot in as just another parser.

## How a poll works

```text
every interval_hours ─▶ POLL_WATCH job ─▶ fetch feed ─▶ parse item URLs ─▶ ingest each new URL
                                                                                  │
                                                            (reuses POST /v1/pages semantics)
                                                                                  ▼
                                                     canonicalize · blacklist · dedup · FETCH_AND_INDEX
```

1. A minute-level scheduler tick finds every watch whose `next_run_at` is due
   and enqueues one durable `POLL_WATCH` job per watch, then advances its
   schedule by `interval_hours`.
2. The `POLL_WATCH` handler fetches the feed and parses out item URLs.
3. Each URL is handed to the same ingest path as a manual add. A watch **never
   fetches article bodies itself** — it enqueues a `FETCH_AND_INDEX` job per new
   URL, which fetches, extracts, chunks, embeds, and indexes it.

Because ingestion is reused wholesale, watches inherit its guarantees for free:

- **Deduplication** — a URL already in the corpus records a cheap revisit and
  enqueues no new work. Re-seeing the same feed items every poll is a no-op.
- **Blacklist** — item URLs matching a [forget](deletion.md) rule are skipped.
- **Isolation** — each watch polls as its own durable job, so one failing feed
  never affects the others; its error is recorded on the watch, not swallowed.

## Scheduling

Each watch stores its own `interval_hours` (default **24**, from
`REFINDERY_WATCH__DEFAULT_INTERVAL_HOURS`). A new watch is due immediately, so
its first poll runs on the next scheduler tick. The schedule is advanced when a
poll is *enqueued*, not when it finishes — a permanently failing feed keeps
retrying at its interval rather than freezing.

A single poll ingests at most `REFINDERY_WATCH__MAX_ITEMS_PER_POLL` items
(default 200, newest first); the rest are dropped with a log line so a large
backfill cannot flood the queue in one run.

## Managing watches

Watches are managed through the HTTP API (and the mirrored MCP tools). All
mutating routes require the `write` scope.

| Method | Path | Tool | What it does |
| --- | --- | --- | --- |
| `POST` | `/v1/watches` | `create_watch` | Register a feed (`url`, optional `kind`, `interval_hours`, `enabled`). |
| `GET` | `/v1/watches` | `list_watches` | List every watch and its last poll outcome. |
| `GET` | `/v1/watches/{id}` | `get_watch` | Inspect one watch. |
| `DELETE` | `/v1/watches/{id}` | `delete_watch` | Stop polling (already-ingested pages are kept). |
| `POST` | `/v1/watches/{id}/run` | `run_watch` | Poll now, out of schedule. |

```bash
# Watch a feed, polling every 6 hours
curl -s -X POST http://127.0.0.1:8000/v1/watches \
  -H "Authorization: Bearer $REFINDERY_AUTH_TOKEN" -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/feed.xml", "interval_hours": 6}'

# Poll it immediately
curl -s -X POST http://127.0.0.1:8000/v1/watches/<id>/run \
  -H "Authorization: Bearer $REFINDERY_AUTH_TOKEN"
```

Each watch reports its most recent poll via `last_status` (`pending` → `ok` /
`error`), `last_error`, `last_run_at`, and `last_item_count`, so you can tell at
a glance whether a feed is healthy.

For the exact request/response contract, see the [HTTP API](../reference/http-api.md)
and [MCP tools](../reference/mcp-tools.md) references; for the settings, the
[Settings reference](../configuration/reference.md).
