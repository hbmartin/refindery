# CLI

Refindery ships a single console entry point, `refindery` (also
`python -m refindery`). With no subcommand it serves the API, so
`python -m refindery` keeps working.

```
refindery serve
refindery eval score  [options]
refindery eval replay [options]
```

Run under the environment file so provider keys reach their SDKs:

```bash
uv run --env-file .env refindery serve
```

## `serve`

Loads settings and starts the API (and, in one process, the MCP server and the
queue consumer). It binds to `REFINDERY_BIND_HOST:REFINDERY_BIND_PORT`
(`127.0.0.1:8000` by default). All behavior is driven by
[configuration](../configuration/index.md).

## `eval score`

Reads the observability DuckDB **read-only** and computes nDCG / MRR / recall /
rerank-lift against `POST /v1/feedback` labels. Needs no container or provider
key.

| Flag | Default | Meaning |
| --- | --- | --- |
| `--db PATH` | `data/observability.duckdb` | Query-log file to read. |
| `--k INT` | `10` | Ranking depth to score at. |
| `--since ISO` | *(all)* | Only score runs at or after this ISO timestamp. |
| `--model NAME` | *(all)* | Only score this model's runs. |
| `--json PATH` | — | Also write the full report as JSON. |

```bash
uv run refindery eval score --k 10 --since 2026-07-01T00:00:00Z
```

## `eval replay`

Re-runs golden queries under **two configurations** and diffs them, without
logging the replay. Boots a trimmed runtime (metadata store + vector schema
only).

| Flag | Default | Meaning |
| --- | --- | --- |
| `--db PATH` | `data/observability.duckdb` | Query-log file to read. |
| `--model-a NAME` | active | Arm A embedding model. |
| `--model-b NAME` | active | Arm B embedding model. |
| `--no-rerank-a` | off | Disable reranking in arm A. |
| `--no-rerank-b` | off | Disable reranking in arm B. |
| `--k INT` | `10` | Ranking depth to score at. |
| `--candidates INT` | `100` | Candidate pool per arm. |
| `--limit INT` | *(all)* | Replay at most this many queries. |
| `--json PATH` | — | Also write the full report as JSON. |

```bash
uv run refindery eval replay --model-a voyage-3.5 --model-b voyage-3-large --no-rerank-b
```

See the [Evaluation guide](../guides/eval.md) for the workflow.
