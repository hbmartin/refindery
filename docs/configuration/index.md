# Configuration

Every application setting can be supplied as an environment variable. Defaults
and validation rules live in `refindery.config` — see the auto-generated
[Settings reference](reference.md) for the complete, always-current model.

## The environment mapping

Use the `REFINDERY_` prefix and separate nested names with `__` (double
underscore). For example, the `model` field of the `embedder` group becomes:

```dotenv
REFINDERY_EMBEDDER__MODEL=voyage-3.5
```

Tuple and list values are JSON, kept on one line:

```dotenv
REFINDERY_ENTITY__EXTRACTOR_CHAIN='["gliner", "spacy"]'
```

## `.env` and provider keys

Refindery reads `.env` for its own settings, and `.env` is git-ignored. Start it
with `uv run --env-file .env` so that **native provider variables** — such as
`VOYAGE_API_KEY` and `COHERE_API_KEY`, which the provider SDKs read directly —
are also exported to their SDKs:

```bash
uv run --env-file .env refindery serve
```

!!! tip "Use absolute paths for state files"
    Relative paths (LanceDB, SQLite, DuckDB, Huey) resolve from the process
    working directory. Use absolute paths when a service manager or container
    may start Refindery from a different directory.

## What you can configure

| Area | Prefix | Guide |
| --- | --- | --- |
| Vector store & providers | `REFINDERY_VECTOR_STORE`, `REFINDERY_EMBEDDER__*`, `REFINDERY_RERANKER__*` | [Deployment profiles](deployment-profiles.md) |
| Authentication | `REFINDERY_AUTH_TOKEN`, `REFINDERY_AUTH_TOKENS` | [Authentication](auth.md) |
| Ranking, chunking, jobs, clustering | `REFINDERY_CHUNKING__*`, `REFINDERY_JOBS__*`, `REFINDERY_CLUSTER__*`, `REFINDERY_SEARCH__*` | [Tuning](tuning.md) |
| Entities | `REFINDERY_ENTITY__*`, `REFINDERY_LLM__*` | [Entities](../guides/entities.md) |
| Logs, metrics, traces | `REFINDERY_OBSERVABILITY__*` | [Observability](observability.md) |
| State file locations | `REFINDERY_LANCEDB__PATH`, `REFINDERY_SQLITE__PATH`, … | [Deployment profiles](deployment-profiles.md) |

To validate and inspect the resolved configuration (with secrets masked), see
[Validate the install](../getting-started/validate.md).
