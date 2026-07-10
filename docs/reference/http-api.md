# HTTP API

Every endpoint requires a bearer token (see [Authentication](../configuration/auth.md)),
even on loopback. The server binds to `127.0.0.1:8000` by default. Requests and
responses are JSON with `extra="forbid"` — unknown fields are rejected.

This page is the map of the surface. The **ingest** endpoints have their own
in-depth contract in the [Upstream ingest API](upstream-ingest-api.md); this page
focuses on the retrieval and admin surface. `write`-scoped routes are marked 🔒.

## Endpoint map

```text
POST   /v1/pages                      ingest (body_extracted XOR body_html; neither → fetch)  🔒
POST   /v1/pages/batch                ingest up to 100 pages with per-item outcomes           🔒
POST   /v1/pages/status/batch         status for up to 500 distinct page IDs
GET    /v1/pages/{id}                 full body_text + metadata
GET    /v1/pages/{id}/chunks          ordered chunk text, token counts, and body offsets
GET    /v1/pages/{id}/status          queued | indexing | indexed | failed | dead
GET    /v1/pages/{id}/similar         ?mediation=vector|cluster|entity&k=
GET    /v1/pages/{id}/entities
POST   /v1/search                     hybrid dense+sparse, RRF, rerank, filters
POST   /v1/compare                    A/B embedding models (Jaccard@k, RBO, Kendall's τ)
GET    /v1/clusters                   ?include_tombstoned=false
GET    /v1/clusters/{id}
GET    /v1/clusters/runs              persisted clustering history
GET    /v1/clusters/projection        ?run_id= — per-page 2-D coordinates
POST   /v1/clusters/recompute                                                                  🔒
GET    /v1/entities/{id_or_form}
POST   /v1/forget                     purge + blacklist atomically                             🔒
GET    /v1/blacklist
DELETE /v1/blacklist/{id}                                                                      🔒
POST   /v1/models                     register embedding model                                🔒
POST   /v1/models/{id}/backfill       dry-run estimate, then confirm                          🔒
GET    /v1/models/{id}/backfill       durable backfill progress
POST   /v1/models/{id}/activate                                                               🔒
DELETE /v1/models/{id}                                                                        🔒
POST   /v1/feedback                   { query_id, page_id, relevant }
GET    /v1/whoami                     authenticated token name and scopes
GET    /v1/jobs                       ?status=&kind=&limit= (status_filter is deprecated)
GET    /v1/admin/query-log            ?since=&limit=&kind=
GET    /v1/admin/query-log/{query_id} full retrieval trace, timing, and feedback
GET    /v1/admin/metrics/timeseries   ?metric=&since=&step=
POST   /v1/admin/eval/score           offline ScoreReport
POST   /v1/admin/eval/replay          enqueue live two-arm replay
GET    /v1/admin/eval/replay/{job_id} poll durable ReplayReport
GET    /v1/admin/config               effective settings with secrets redacted
GET    /v1/admin/mcp                  mounted tool metadata
GET    /healthz  /readyz  /metrics    liveness / readiness (unauth) · metrics (auth)
```

## Retrieval

### `POST /v1/search`

Runs the [hybrid retrieval pipeline](../guides/search.md) and returns ranked
pages with their matched chunks and per-stage timings. Key body parameters:
`query`, `k` (default 10), `candidates` (100), `rerank` (true),
`chunks_per_page` (2), `rollup` (`max`), and the filters `domain`, `after`,
`before`, `cluster_id`, `entity_id`. The response includes a `query_id` — pass
it to `/v1/feedback` to label results for [evaluation](../guides/eval.md).

### `POST /v1/compare`

Runs the pipeline once per model over the same query, holding the sparse arm and
reranker constant, and returns per-model ranked lists plus agreement stats. See
[Embedding models](../guides/models.md).

### Similarity — `GET /v1/pages/{id}/similar`

`mediation=vector|cluster|entity` (default `vector`), `k`. See
[Searching → Similarity](../guides/search.md#similarity-and-suggestions).

## Clusters, entities

`GET /v1/clusters` (optionally `include_tombstoned`), `GET /v1/clusters/{id}`,
and `POST /v1/clusters/recompute` cover [clustering](../guides/clustering.md).
`GET /v1/entities/{id_or_form}` resolves an [entity](../guides/entities.md) by ID
or canonical form.

## Admin

- **Deletion** — `POST /v1/forget`, `GET /v1/blacklist`, `DELETE /v1/blacklist/{id}`.
  See [Deletion & blacklist](../guides/deletion.md).
- **Models** — register, backfill (dry-run then confirm), activate, retire. See
  [Embedding models](../guides/models.md).
- **Web UI bootstrap** — `GET /v1/whoami`, `/v1/admin/config`, and
  `/v1/admin/mcp` expose caller capabilities and safe runtime metadata. Config
  secrets are always returned as `[REDACTED]`; every field is marked
  `boot_only` because there is no runtime configuration mutation API.
- **Search lab** — query-log list/detail returns candidate, dense, sparse, and
  final hit sets plus per-stage timing and latest feedback. The list defaults to
  100 rows and caps `limit` at 1,000.
- **Evaluation** — `/v1/admin/eval/score` is synchronous and read-only. Replay
  returns `202 {job_id,result_url}`; poll the result URL until the job is
  `done`, `failed`, or `dead`. Reports and failures survive process restarts.
- **Jobs** — filter with `status` and/or `kind`; `status_filter` remains a
  deprecated compatibility alias and cannot be combined with `status`.

## Health

- `GET /healthz` — liveness, unauthenticated.
- `GET /readyz` — readiness and additive API capability flags, unauthenticated;
  `503` until the metadata store is reachable and an embedding model is active.
- `GET /metrics` — Prometheus, bearer required. See [Observability](../configuration/observability.md).

## Related

- [Upstream ingest API](upstream-ingest-api.md) — full ingest contract and status codes.
- [Python API → Services](python-api/services.md) — the objects behind these routes.
