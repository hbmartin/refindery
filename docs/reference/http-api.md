# HTTP API

Every endpoint requires a bearer token (see [Authentication](../configuration/auth.md)),
even on loopback. The server binds to `127.0.0.1:8000` by default. Requests and
responses are JSON with `extra="forbid"` — unknown fields are rejected.

This page is the map of the surface. The **ingest** endpoints have their own
in-depth contract in the [Upstream ingest API](upstream-ingest-api.md); this page
focuses on the retrieval and admin surface. `write`-scoped routes are marked 🔒.

## Endpoint map

```
POST   /v1/pages                      ingest (body_extracted XOR body_html; neither → fetch)  🔒
GET    /v1/pages/{id}                 full body_text + metadata
GET    /v1/pages/{id}/status          queued | indexing | indexed | failed | dead
GET    /v1/pages/{id}/similar         ?mediation=vector|cluster|entity&k=
GET    /v1/pages/{id}/entities
POST   /v1/search                     hybrid dense+sparse, RRF, rerank, filters
POST   /v1/compare                    A/B embedding models (Jaccard@k, RBO, Kendall's τ)
GET    /v1/clusters                   ?include_tombstoned=false
GET    /v1/clusters/{id}
POST   /v1/clusters/recompute                                                                  🔒
GET    /v1/entities/{id_or_form}
POST   /v1/forget                     purge + blacklist atomically                             🔒
GET    /v1/blacklist
DELETE /v1/blacklist/{id}                                                                      🔒
POST   /v1/models                     register embedding model                                🔒
POST   /v1/models/{id}/backfill       dry-run estimate, then confirm                          🔒
POST   /v1/models/{id}/activate                                                               🔒
DELETE /v1/models/{id}                                                                        🔒
POST   /v1/feedback                   { query_id, page_id, relevant }
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

## Health

- `GET /healthz` — liveness, unauthenticated.
- `GET /readyz` — readiness, unauthenticated; `503` until the metadata store is
  reachable and an embedding model is active.
- `GET /metrics` — Prometheus, bearer required. See [Observability](../configuration/observability.md).

## Related

- [Upstream ingest API](upstream-ingest-api.md) — full ingest contract and status codes.
- [Python API → Services](python-api/services.md) — the objects behind these routes.
