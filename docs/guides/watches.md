# Watches

Refindery is normally push-only: upstream capture systems submit pages via
`POST /v1/pages`. A **watch** adds a pull mechanism — a durable subscription
that polls a source on its own schedule (default every 24 hours), discovers
the source's current item URLs, and ingests each new one through the normal
pipeline so it becomes searchable.

Watches reuse ingest's dedup: a discovered URL that is already indexed is a
cheap revisit no-op, and blacklisted URLs are skipped. One misbehaving feed
never affects other watches — each poll is its own durable job with retries
and dead-lettering.

## Watch kinds

| Kind | Source | Discovered items |
| ---- | ------ | ---------------- |
| `rss` | An RSS/Atom feed URL | The feed's entry links |
| `youtube` | A YouTube playlist or channel URL | The listing's video URLs (each ingested as a [transcript](ingest.md#youtube-transcripts)) |

`youtube` watches require the `youtube` extra (`uv add 'refindery[youtube]'`);
creating one without it returns `501`. A single video URL is rejected with
`422` — submit those to `POST /v1/pages` directly. Channel URLs
(`/@handle`, `/channel/…`) poll the uploads tab; per-watch `config` may set
`max_entries` to bound how many videos each poll considers (default
`watch.youtube_max_entries`, 100).

## Creating a watch

```bash
curl -X POST http://127.0.0.1:8000/v1/watches \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://blog.example/feed.xml", "interval_hours": 12}'
```

`interval_hours` defaults to `watch.default_interval_hours` (24). The first
poll runs within a minute of creation; after that, one poll per interval.
Creating the same `(kind, url)` twice returns `409`.

## Managing watches

| Action | Endpoint |
| ------ | -------- |
| List (with health) | `GET /v1/watches` |
| Inspect one | `GET /v1/watches/{id}` |
| Pause / resume / retune | `PATCH /v1/watches/{id}` (`enabled`, `interval_hours`, `title`, `config`) |
| Poll immediately | `POST /v1/watches/{id}/run` |
| Delete | `DELETE /v1/watches/{id}` |

The watch URL is immutable — delete and recreate to point a watch somewhere
else. Every response carries the watch's health: `last_status`
(`pending`/`ok`/`error`), `last_error`, `last_item_count`, `last_run_at`, and
`next_run_at`. `POST …/run` returns the poll job id, trackable via the
[jobs API](../reference/http-api.md); it also pushes the next scheduled poll
one full interval out.

All of this is available to agents as MCP tools: `list_watches` and
`get_watch` are always visible; `create_watch`, `update_watch`,
`delete_watch`, and `run_watch` appear when
`REFINDERY_MCP__ENABLE_MUTATING_TOOLS=true` (and always require a
write-scoped token). See [MCP for agents](mcp.md).

## Scheduling semantics

- A minute-level scheduler tick finds due watches (`enabled` and
  `next_run_at <= now`) and enqueues one durable `poll_watch` job per watch.
- The tick advances `next_run_at` at enqueue time — never the poll handler —
  so a permanently failing feed cannot freeze its own schedule.
- Poll jobs carry an idempotency key derived from the scheduled time, so a
  racing duplicate tick cannot double-poll.
- A failed poll records `last_status: error` with the failure detail, then
  retries with backoff and eventually dead-letters — for that watch only.
- Polls returning more than `watch.max_items_per_poll` items keep the newest
  and log how many were dropped.

## Settings

Environment variables use the `REFINDERY_WATCH__` prefix (see the
[settings reference](../configuration/reference.md)):

| Setting | Default | Meaning |
| ------- | ------- | ------- |
| `default_interval_hours` | `24` | Interval for watches created without one |
| `poll_tick_enabled` | `true` | Master switch for the scheduler periodic |
| `max_due_per_tick` | `20` | Due watches processed per scheduler tick |
| `max_items_per_poll` | `200` | Cap on items ingested per poll (newest kept) |
