# Evaluation

Refindery logs every search to an append-only DuckDB query log, and every
relevance label to a feedback table. Together they are the substrate for
**offline retrieval evaluation** — you can measure and compare ranking quality
without re-running live traffic.

## The substrate

Each search writes a row capturing everything needed to reconstruct and re-score
the run: the query text and params, the active and reranker models, the **full
pre-rerank candidate set** (chunk IDs + fusion scores), the dense and sparse
hits, the final ranked pages, and per-stage timings. Relevance labels arrive
separately:

```bash
curl -s -X POST http://127.0.0.1:8000/v1/feedback \
  -H "Authorization: Bearer $REFINDERY_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query_id": "<from a search response>", "page_id": "<page>", "relevant": true}'
```

Because the candidate set is stored, reranker lift can be measured offline
without re-running retrieval.

## `refindery eval score`

Reads the query log **read-only** and computes metrics against the feedback
labels — nDCG, MRR, recall, and rerank-lift:

```bash
uv run refindery eval score --k 10
```

Useful flags: `--db <path>` (query-log file), `--k` (cutoff), `--since` (time
window), `--model` (filter by active model), `--json` (machine-readable output).
This command needs no container or provider key — it only reads the log.

## `refindery eval replay`

Re-runs a golden set of logged queries under **two configurations** and diffs
them — for example two embedding models, or rerank on vs off — over the same
queries, without logging the replay:

```bash
uv run refindery eval replay --model-a voyage-3.5 --model-b voyage-3-large --k 10
```

Useful flags: `--model-a/-b`, `--no-rerank-a/-b`, `--k`, `--candidates`,
`--limit`, `--json`. Replay boots a trimmed runtime (metadata store + vector
schema only — no sink, no queue), so it is safe to run alongside evaluation
without perturbing production state.

## Retention

Query logs intentionally retain raw query text and hit IDs for evaluation. This
is one of Refindery's [accepted operational risks](../operations/index.md#accepted-operational-risks);
purge them when you no longer need them — see
[Observability](../configuration/observability.md) and
[Operations](../operations/index.md#query-log-purge).

## Related

- [CLI reference](../reference/cli.md) — every `eval` flag.
- [Searching](search.md) — the pipeline whose output is logged.
- [Embedding models](models.md) — the A/B comparisons replay evaluates.
