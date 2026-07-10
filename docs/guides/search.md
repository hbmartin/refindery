# Searching

`POST /v1/search` runs a hybrid retrieval pipeline and returns ranked pages with
their best matching passages. Ranking only exists after the full pipeline runs,
which is why pagination and limits are applied at the very end.

## The pipeline

`SearchService.search` (also the shape of each `/v1/compare` arm):

```text
query
  ├─ embed_query(active model) ──▶ dense top-N ┐
  └─ bm25 sparse ────────────────▶ sparse top-N ├─▶ RRF fuse ─▶ candidate chunks
                                                 ┘
                                                        │
                                                  rerank (cross-encoder, optional)
                                                        │
                                                  roll up chunks → pages
                                                        │
                                       hydrate · recency decay · pin exact matches
                                                        │
                                             final slice [offset : offset+k]
```

1. **Embed** the query with the active model.
2. **Hybrid retrieval** — dense and sparse (BM25) arms run in parallel, fused
   client-side with **reciprocal-rank fusion** (RRF).
3. **Rerank** (optional) — a cross-encoder rescoring of the fused candidate set.
   Reranking operates on *chunks*, preserving the chunk-level signal that makes
   long pages findable.
4. **Rollup** — collapse chunks to pages (max by default).
5. **Hydrate** pages, apply optional recency decay, and pin exact URL/domain
   matches (the "I'm pasting the URL back" refind case).
6. **Slice** `[offset : offset+k]` — pagination happens *only here*, because
   every earlier stage reorders the ranking.

Every execution is logged to the [DuckDB query log](eval.md) (candidate set,
both arms, final pages, params, timings), which is the substrate for offline
evaluation.

## Key parameters

All are query-configurable; the notable defaults:

| Parameter | Default | Notes |
| --- | --- | --- |
| `k` | 10 | pages returned |
| `candidates` | 100 | chunks retrieved per arm and post-fusion set size |
| `rerank` | `true` | disable for latency, or set the reranker to `none` globally |
| `chunks_per_page` | 2 | matched chunks returned per page (whole chunk text) |
| `rollup` | `max` | rollup strategy |

The response returns whole matched chunks as snippets; full `body_text` comes
only from `GET /v1/pages/{id}`. See the [HTTP API](../reference/http-api.md) for
the complete request and response shapes.

## Filters

Filters are first-class and pushed into the vector store where supported:
`domain`, `after`, `before`, `cluster_id`, `entity_id`. There is deliberately
**no embedding-model filter** — model selection is global (the active model) or
per-arm via [`/compare`](models.md).

## Recency decay

Off by default. Set a half-life to bias ranking toward recently seen pages —
useful for refind on an actively growing corpus:

```dotenv
REFINDERY_SEARCH__RECENCY_HALF_LIFE_DAYS=30
```

## Similarity and suggestions

`GET /v1/pages/{id}/similar?mediation=...` (and the `suggestions` block on
search) surface related pages through one of three mediations:

- **`vector`** (default) — page-vector cosine nearest neighbors, excluding the source.
- **`cluster`** — co-members of the source's [cluster](clustering.md), ranked by
  cosine to the source.
- **`entity`** — pages sharing canonical [entities](entities.md), ranked by
  IDF-weighted Jaccard over entity sets.

## Compare (A/B)

`POST /v1/compare` runs the full pipeline once per embedding model. The sparse
arm and reranker are identical across arms, so the delta isolates the embedder.
It returns per-model ranked lists plus agreement stats — Jaccard@k, RBO, and
Kendall's τ — and logs them for eval. See [Embedding models](models.md).

## Related

- [HTTP API](../reference/http-api.md) — request/response reference.
- [Tuning](../configuration/tuning.md) — reranker selection and recency.
- [Evaluation](eval.md) — measure retrieval quality from logged queries.
