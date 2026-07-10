# Embedding models

Refindery can hold more than one embedding model at a time, with exactly one
**active** model driving search. This is what makes A/B comparison and safe
model migration possible. Each model occupies a distinct vector space, so
switching models is a register-and-backfill operation, never a relabel.

## The lifecycle

```
register ──▶ backfill (dry-run → confirm) ──▶ activate ──▶ (retire)
registered      backfilling                    ready
```

| Step | Endpoint | Effect |
| --- | --- | --- |
| Register | `POST /v1/models` | Adds a model in `registered` state. |
| Backfill | `POST /v1/models/{id}/backfill` | Dry-run estimates cost, then embeds the existing corpus. |
| Activate | `POST /v1/models/{id}/activate` | Makes it the model search uses. |
| Retire | `DELETE /v1/models/{id}` | Drops the model and its vectors. |

Backfill returns a **dry-run estimate first** — re-embedding a large corpus is
real time and real money — so you confirm before it runs. See the
[HTTP API](../reference/http-api.md) for request shapes.

## Distinct vector spaces

Changing an embedding model creates a new vector space; you cannot compare
vectors across models. Refindery enforces this by keeping per-model storage
(collection-per-model in Qdrant, column-per-model in LanceDB) and deriving safe
internal storage names from the public model ID (see
[Operations](../operations/index.md#vector-adapter-caveats)).

!!! warning "Dimension and token limits are authoritative"
    The configured embedding `dim` and `max_input_tokens` are authoritative: if
    they do not match the provider model, indexing **fails** rather than storing
    malformed vectors. Registering a model whose token budget is smaller than
    the canonical chunk size is rejected, because it would force a re-chunk and
    invalidate every other model's index.

## Comparing models

`POST /v1/compare` runs the search pipeline once per model over the same query,
holding the sparse arm and reranker constant so the delta isolates the embedder.
It returns per-model ranked lists and agreement stats (Jaccard@k, RBO, Kendall's
τ) and logs them for [evaluation](eval.md). To compare configurations over a
whole golden set offline, use [`refindery eval replay`](eval.md#refindery-eval-replay).

## Configuring the active model

The default is Voyage `voyage-3.5` at dim 1024. Set the provider and model in
`.env` — see the [daemon-free profile](../configuration/deployment-profiles.md)
and [Configuration overview](../configuration/index.md):

```dotenv
REFINDERY_EMBEDDER__PROVIDER=voyage
REFINDERY_EMBEDDER__MODEL=voyage-3.5
REFINDERY_EMBEDDER__DIM=1024
REFINDERY_EMBEDDER__MAX_INPUT_TOKENS=32000
```

## Related

- [HTTP API](../reference/http-api.md) — model management endpoints.
- [Evaluation](eval.md) — scoring and replay for model comparisons.
- [Deployment profiles](../configuration/deployment-profiles.md) — provider setup.
