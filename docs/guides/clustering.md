# Clustering

Clustering answers *"what have I been reading a lot about?"* It groups pages by
their page vectors into stable, labeled themes that survive re-clustering, so an
agent's reference to a cluster degrades gracefully over time.

## Algorithm

The default is **HDBSCAN over UMAP-reduced page vectors**. A reading corpus is
lumpy — a few obsessions and a long tail of one-offs — and HDBSCAN labels the
one-offs as noise (`−1`) rather than forcing them into a bogus cluster.

| Algorithm | Shape | `k` required | Noise | Stable IDs |
| --- | --- | --- | --- | --- |
| **HDBSCAN** (default) | arbitrary density | no | yes (`−1`) | via matching layer |
| KMeans | spherical, equal size | yes | no | trivial |
| Leiden (kNN graph) | arbitrary | resolution | no | via matching layer |

UMAP/HDBSCAN are CPU-bound, so a run executes in a `ProcessPoolExecutor` off the
event loop. Each run is a full refit. Select the algorithm, reducer, and sizing
with `REFINDERY_CLUSTER__*` — see [Tuning](../configuration/tuning.md).

Each run also computes a separate deterministic two-dimensional PCA projection
from the original page vectors. This display projection does not affect
clustering. Page coordinates and the mean coordinate of every cluster are
persisted with the run, so historical scatter plots remain stable. Read them
through `GET /v1/clusters/runs` and
`GET /v1/clusters/projection?run_id=<id>`; current cluster summaries include
nullable projected centroid coordinates.

## Stable IDs

The clustering algorithm itself gives no stable IDs; a matching layer does. After
a run, Refindery builds a cost matrix from `1 − Jaccard(new, old)` over cluster
memberships, runs Hungarian assignment, and:

- a match with Jaccard ≥ 0.5 → the new cluster **inherits** the old ID (`persisted`);
- an unmatched new cluster → a fresh ID (`created`, or `split` with parents if it
  overlaps an old one);
- an unmatched old cluster → `tombstoned` (rows retained), emitted as `dissolved`
  or `merged`.

Tombstoned clusters are excluded from `list_clusters` but still resolvable by ID,
so stale references from agents keep working.

## Labeling

Each cluster gets keywords from class-based TF-IDF over its members (always
populated), and optionally a short noun-phrase label from an
[LLM](entities.md#llm-extractor) over the nearest-centroid titles and top terms.
LLM labels are cosmetic, cached, and non-blocking; keywords are the reliable
fallback.

## Triggers

A clustering run happens on any of:

- **Manual** — `POST /v1/clusters/recompute`;
- **Cron** — a configurable schedule, e.g. `REFINDERY_CLUSTER__CRON='0 3 * * *'`;
- **Idle** — no ingest for an idle threshold **and** at least `min_new_pages`
  indexed since the last run (default 20), with a minimum corpus size
  (`min_pages`, default 50) before the first run ever.

The idle threshold adapts to how long clustering actually takes, so Refindery
never re-clusters more often than clustering costs.

## Related

- [Tuning](../configuration/tuning.md) — cluster sizing, algorithm, and cron.
- [Searching](search.md) — `cluster` mediation for similarity and the `cluster_id` filter.
- [HTTP API](../reference/http-api.md) — cluster endpoints.
