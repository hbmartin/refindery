# Tuning

The defaults are conservative for a single-user machine. These are the main
knobs to revisit as a collection grows. All are environment variables — see the
[Configuration overview](index.md) and the full
[Settings reference](reference.md).

## Ranking, chunking, jobs, and clustering

| Setting | Default | Purpose |
| --- | --- | --- |
| `REFINDERY_CHUNKING__TARGET_TOKENS` | `448` | Desired chunk size. |
| `REFINDERY_CHUNKING__OVERLAP_TOKENS` | `64` | Context repeated across chunks. |
| `REFINDERY_CHUNKING__HARD_MAX_TOKENS` | `512` | Maximum canonical chunk size. |
| `REFINDERY_FETCH__TIMEOUT_S` | `10.0` | Outbound fetch timeout. |
| `REFINDERY_FETCH__MAX_BYTES` | `10000000` | Maximum fetched response size. |
| `REFINDERY_JOBS__MAX_ATTEMPTS` | `5` | Attempts before a job becomes dead. |
| `REFINDERY_JOBS__LEASE_MINUTES` | `15` | Recovery lease for in-flight work. |
| `REFINDERY_CLUSTER__MIN_PAGES` | `50` | Pages required for the first cluster run. |
| `REFINDERY_CLUSTER__MIN_NEW_PAGES` | `20` | New pages required for an idle-triggered run. |
| `REFINDERY_SEARCH__RECENCY_HALF_LIFE_DAYS` | *(unset)* | Optional ranking decay toward recent pages. |

!!! warning "Chunking is model-independent"
    Chunk sizes are canonical and shared across all embedding models, so all
    models embed the same spans. Changing them re-chunks the corpus. The hard
    max must not exceed the smallest registered model's token budget — see
    [Embedding models](../guides/models.md).

## Reranking

Set the reranker to `none` for fusion-only search with no reranking provider
call:

```dotenv
REFINDERY_RERANKER__KIND=none
```

Otherwise choose an API reranker (Cohere, Voyage) or a local cross-encoder via
`REFINDERY_RERANKER__*`. See [Searching](../guides/search.md#the-pipeline).

## Clustering schedule

Clustering runs on an idle trigger by default. To add a cron schedule, set a
one-to-five-field crontab expression:

```dotenv
REFINDERY_CLUSTER__CRON='0 3 * * *'
```

Algorithm, reducer, and sizing live under `REFINDERY_CLUSTER__*` — see
[Clustering](../guides/clustering.md).

## Related

- [Searching](../guides/search.md) — where these knobs take effect.
- [Clustering](../guides/clustering.md) — sizing and triggers.
- [Operations](../operations/index.md) — job lease behavior.
