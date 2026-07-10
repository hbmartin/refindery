# Observability

Refindery is instrumented from day one: structured logs, Prometheus metrics,
optional OpenTelemetry traces, and the DuckDB query log that doubles as the
[evaluation](../guides/eval.md) substrate. Configure it all under
`REFINDERY_OBSERVABILITY__*`.

## Logs and metrics

JSON logs and authenticated Prometheus metrics are enabled by default. Metrics
are served at `GET /metrics` and require a bearer token like every other
authenticated route. They cover ingest rate, queue depth, job failure rate,
search latency percentiles, rerank latency, embedding-provider error rate, and
cluster-run duration.

The server snapshots its own registry into the observability DuckDB every 15
seconds by default. Configure the positive interval with
`REFINDERY_OBSERVABILITY__METRICS_SNAPSHOT_INTERVAL_S`. The read-scoped
`GET /v1/admin/metrics/timeseries?metric=<family>&since=<timestamp>&step=<seconds>`
endpoint returns one series per label set. Gauge buckets use the last value;
counters and histogram components return per-window deltas. Current gauges are
included separately. History is retained until the DuckDB data is purged.

## Traces

Tracing is off by default. Enable console spans for local diagnosis, or export
OTLP over HTTP:

```dotenv
REFINDERY_OBSERVABILITY__TRACES=otlp
REFINDERY_OBSERVABILITY__OTLP_ENDPOINT=http://127.0.0.1:4318/v1/traces
REFINDERY_OBSERVABILITY__JSON_LOGS=true
```

If no Refindery-specific OTLP endpoint is set, the standard
`OTEL_EXPORTER_OTLP_ENDPOINT` variable is honored. Spans cover the ingest, embed,
vector-upsert, entity, search (per stage), and cluster (per stage) paths.

## The query log

Every search appends a row to the DuckDB query log — query text, params, models,
the full pre-rerank candidate set, both retrieval arms, final pages, and
timings. `POST /v1/feedback` appends relevance labels alongside. This is what
[`refindery eval score` and `eval replay`](../guides/eval.md) read.

!!! warning "Raw query text is retained"
    The query log intentionally keeps raw query text and hit IDs for offline
    evaluation. This is an [accepted operational risk](../operations/index.md#accepted-operational-risks).
    Purge it when you no longer need it — see
    [Operations → Query-log purge](../operations/index.md#query-log-purge).

## Related

- [Evaluation](../guides/eval.md) — using the query log.
- [Operations](../operations/index.md) — purge and retention.
- [Settings reference](reference.md) — the `observability` group.
