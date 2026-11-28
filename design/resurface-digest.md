# Design: Resurface digest (periodic spaced-recall)

> Status: design proposal. Delivers the **Resurface** job-to-be-done, which
> `Refindery Spec.md` lists as *"push/spaced-recall — architected for, not
> built."* Brain-Crew's `weekly-agenda` and `deadline-radar` skills validate the
> demand for proactive digests over a personal knowledge base.
>
> Charter constraint: **no generation on the query path.** The digest is a
> *ranked, structured selection* of pages/clusters/entities the user already
> read — not generated prose. Any narrative summary is the calling agent's job
> (e.g. a Brain-Crew skill that reads the digest and writes a weekly note).

## 1. What "resurface" means here

Three signals, all computed from data Refindery already holds:

1. **Trending topics** — clusters that grew or were read most in the window
   ("what have I been reading a lot about").
2. **Resurfacing (spaced recall)** — pages read once, a while ago, not revisited,
   still central to a live cluster — the "you looked at this, worth another
   pass" signal. This is the spaced-repetition-flavored part.
3. **Emerging entities** — people/orgs/topics whose mention/page counts rose in
   the window (pairs with the People surface in the roadmap doc).

The digest is a small, deterministic, cacheable object. It is produced on a
schedule by a background job and read back over HTTP/MCP.

## 2. Non-goals

- No LLM summary text in the digest payload (charter). The payload carries page
  ids, titles, canonical URLs, cluster refs, entity refs, and scores.
- No new notification transport in v1 (no email/push). The digest is *pull*:
  a periodic job materializes it; clients poll or subscribe to the existing SSE
  event stream. Push transport can come later behind a settings flag.
- No change to ranking/search internals.

## 3. Data model

One durable table, one row per produced digest (mirrors how `ClusterRun` records
a run):

```sql
CREATE TABLE resurface_digests (
  id            TEXT PRIMARY KEY,        -- uuid7
  generated_at  TIMESTAMP NOT NULL,
  window_start  TIMESTAMP NOT NULL,
  window_end    TIMESTAMP NOT NULL,
  trigger       TEXT NOT NULL,           -- 'scheduled' | 'manual'
  payload       TEXT NOT NULL,           -- JSON: the structured digest (below)
  n_pages       INTEGER NOT NULL
);
```

`payload` JSON shape (validated with pydantic at the trust boundary — the read
route parses it back into a response model):

```jsonc
{
  "trending_clusters": [
    { "cluster_id": "…", "label": "…", "keywords": ["…"],
      "growth": 7, "size": 23, "page_ids": ["…"] }
  ],
  "resurfacing_pages": [
    { "page_id": "…", "title": "…", "canonical_url": "…",
      "last_seen_at": "…", "days_since": 41, "reason": "central_to_live_cluster",
      "cluster_id": "…" }
  ],
  "emerging_entities": [
    { "entity_id": "…", "canonical_form": "…", "type": "PERSON",
      "delta_pages": 4, "page_count": 9 }
  ]
}
```

Keeping the payload as a materialized snapshot (not recomputed on read) makes the
digest cheap to serve, stable between runs, and joinable to feedback later.

## 4. Scoring (deterministic, no model calls)

All inputs are already in the metadata store; no embedder/reranker calls needed,
so the job is cheap and cannot trip provider circuit breakers.

- **Trending clusters:** for the window `[now - W, now]`, rank live clusters by
  new/updated member pages (join `cluster_members` to `pages.last_seen_at`/
  `first_seen_at`). Uses `list_clusters` + membership already exposed by
  `MetadataStore`.
- **Resurfacing pages:** candidates = indexed pages with
  `last_seen_at < now - min_age` **and** `visit_count` low **and** membership in a
  cluster that is *currently* trending (or has recent activity). Score by
  `recency_decay(days_since) × cluster_centrality`. `visit_count` and
  `last_seen_at` are columns on `Page`; centrality can reuse the cluster
  probability from `cluster_members`. A half-life decay mirrors the existing
  `recency_half_life_days` knob already used in search.
- **Emerging entities:** rank entities by increase in `page_count` within the
  window. Needs a per-window count; simplest v1 = compare entity→page links whose
  page `first_seen_at` falls in the window vs. the prior window.

Ties broken by id for determinism (same convention as `SimilarityService`).

## 5. Wiring into the existing job/periodic machinery

Refindery already has every mechanism this needs — mirror the watch poller.

### 5.1 Settings section
Add a `ResurfaceSettings` `BaseModel` in `config.py`, composed onto `Settings`
(alongside `WatchSettings`, `ClusterSettings`, …). Follow `ClusterSettings`,
which already carries a **cron** field with 1–5-field validation:

```python
class ResurfaceSettings(BaseModel):
    enabled: bool = False
    cron: str = "0 8 * * 1"          # Monday 08:00, like a weekly agenda
    window_days: int = 7
    min_age_days: int = 14           # a page must be this old to "resurface"
    max_items_per_section: int = 10
```

Env override: `REFINDERY_RESURFACE__ENABLED=true`, `REFINDERY_RESURFACE__CRON=…`.

### 5.2 JobKind + idempotency key
- Add `RESURFACE_DIGEST = "resurface_digest"` to `domain/models.py::JobKind`.
- Add its key format to `domain/job_keys.py` — **new kind only; never change an
  existing kind's format** (they are persisted dedupe keys locked by golden
  tests). Use a time-bucketed key so a duplicate tick is a no-op:
  `resurface_digest:{window_end_iso}` (same pattern as
  `poll_watch:{id}:{next_run_at}`).

### 5.3 Periodic tick
Register a periodic with the existing helper
(`adapters/queue/huey_queue.py::register_periodic(name, schedule, body)` →
huey `periodic_task` + `crontab`). Follow the `watch_poll_tick` precedent:

- Prod-only, guarded by `settings.resurface.enabled`.
- The **tick only enqueues** one `RESURFACE_DIGEST` job with the bucketed
  idempotency key; the **handler computes and persists** the digest. This keeps
  forward progress if the compute job dies (same invariant the watch scheduler
  relies on) and keeps the tick side-effect-free.

### 5.4 Handler
A `ResurfaceService` in `application/services/` composes existing stores/services
(clusters, similarity, entities, query log) to build the payload, then writes one
`resurface_digests` row. It performs **no** embedder/reranker calls (§4), so it is
immune to `ProviderUnavailableError` requeue churn.

### 5.5 Events (optional, reuses existing bus)
On successful persist, the handler can publish a `resurface.ready` event to the
existing `JobEventBus` (`/v1/events` SSE) so a subscribed agent wakes and reads
the digest. No new transport needed. (Per the SSE note in `CLAUDE.md`, publish
only from the main-loop queue path; `recover()` must not publish.)

## 6. Read surface (HTTP + MCP)

```
GET /v1/resurface/latest            operation_id=resurface_latest
GET /v1/resurface/{id}              operation_id=get_resurface_digest
POST /v1/resurface/recompute        operation_id=recompute_resurface  (require_write, 202)
```

- `latest` returns the most recent digest (or `204`/empty when none produced
  yet). Read-scoped; add `resurface_latest` and `get_resurface_digest` to
  `api/mcp.py::READ_OPERATIONS` so agents can pull it as an MCP tool.
- `recompute` mirrors `clusters/recompute`: enqueues a manual run, `409` if one
  is in flight, `202` accepted.
- Response is the structured payload from §3, re-validated through pydantic
  response models — grounded ids/urls/scores only, no prose.

## 7. How Brain-Crew consumes it

A Crew skill (`resurface`, per the skills-pack roadmap item) calls
`resurface_latest`, then *writes the narrative* — a weekly note in `07-Daily/`
or a MOC refresh in `MOC/` — citing each page's `canonical_url`. That is the
correct division of labor: **Refindery selects and grounds; the agent
synthesizes.** The digest is also a natural trigger for the Connector (run
`connections` over the resurfacing pages) and the People view (surface emerging
entities into `05-People/`).

## 8. Testing

- **Deterministic queue tests:** enqueue/handle without `queue.start()` — the
  digest job runs without a consumer and nothing races (per the huey testing
  note in `CLAUDE.md`).
- **Scoring unit tests:** pure functions over synthetic pages/clusters/entities
  (fixed clock via the injectable `Clock` port) assert ranking and recency decay
  without any store.
- **Idempotency golden test:** two ticks in the same window bucket enqueue one
  job (bucketed key), matching the watch-poller golden-test convention.
- **Charter test:** assert the digest payload contains no free-text summary
  field — only structured, grounded references.

## 9. Rollout

Ship `enabled=false` by default (like watches' prod-only poller). Land the table
+ handler + read routes first with `recompute` for manual invocation and eval;
enable the periodic once scoring is validated on a real reading history.
