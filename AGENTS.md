## Workflow

Always run ruff and pytest and ty and pyrefly and lizard (with uv) after making any changes

## Development Notes

- The project supports Python 3.13+
- Uses uv for dependency management instead of traditional pip/setuptools
- Code style uses Ruff formatter and linter

## Python Practices
- Always use or add type hints
- Prefer @dataclasses where applicable
- Always use f-string over string formatting or concatentation (except in logging strings)
- Use async generators and comprehensions when they might provide benefits
- Use underscores in large numeric literals
- Use walrus assignment := where applicable
- Prefer to use named arguments when calling a method with more than one argument
- Use "list" instead of "List" and "dict" instead of "Dict" and "|" instead of "Union" for types
- Use "Self" for applicable types
- Use Structural Pattern Matching (match...case) where applicable
- Always use pathlib.Path for file operations, never use os.path
- Inputs (e.g. HTTP requests) and call results (e.g. HTTP requests not already wrapped in a library) must be validated and made type safe with pydantic.

# Update AGENTS.md
Update AGENTS.md with notes, learnings, findings, or other useful patterns you have learned

# Notes

- Deduplicate identifiers while preserving order before batching dynamic SQLite
  `IN` query parameters to at most 999 variables. This avoids redundant queries
  and remains compatible with older SQLite builds.
- Normalize timezone-naive CLI datetimes to UTC before binding them to DuckDB
  `TIMESTAMPTZ` queries; otherwise DuckDB interprets them in the session timezone.
- Keep `DuckDbQueryLogReader` paths typed as `Path` because argparse normalizes
  `--db` at the CLI boundary. The `query_log.params` column is `JSON NOT NULL`, so
  readers should surface invalid rows instead of silently replacing `NULL`.
- Preserve `final_pages.rank` when reading paginated query-log rows and pass those
  absolute positions into nDCG/MRR/recall; enumerating a returned slice from one
  inflates offline metrics.
- Compute reranker lift only when the logged ranking isolates reranking: max
  rollup, no exact-match pin, and explicitly no recency decay. Log effective
  search settings, including defaults, so eligibility is auditable.
- Reject blank or duplicate auth token secrets during settings validation, and
  make Compose require its token variable instead of substituting an empty value.
- Define nDCG at non-positive cutoff depths as `0.0` when relevance labels exist,
  before computing or dividing by ideal gain.
- Force a non-UTC DuckDB session in naive-`TIMESTAMPTZ` regression tests so the
  timezone boundary remains observable on UTC CI hosts.
- Exclude paginated rows (`offset > 0`) from rerank-lift eligibility: an offset
  slice cannot see earlier pages, so final-vs-pool nDCG no longer isolates
  reranking and lift skews negative.
- Keep macOS local setup daemon-free with LanceDB, install Python and `uv`
  through Homebrew, and launch with `uv run --env-file .env` so native provider
  keys in the ignored `.env` reach their SDKs.
- Treat entity extractor chains as ordered fallbacks rather than ensembles; the
  first healthy extractor handles a page, with later extractors used only after
  a per-call failure.
- Keep fully containerized macOS settings in `.env.docker` and always pass it
  with `docker compose --env-file .env.docker`; this avoids overwriting the
  daemon-free host profile in `.env`.
- Document `forget` as an immediate blacklist and metadata purge followed by
  eventual vector deletion; content refresh must remove the returned blacklist
  rule before re-ingesting the same URL.
- When cancellation interrupts a circuit breaker's half-open probe, reopen the
  breaker with a fresh cooldown before propagating `CancelledError`; otherwise
  the in-flight probe flag permanently rejects future calls.
- Derive the persisted job lease from the effective handler timeout, capped by
  `lease_minutes`, so the watchdog and cooperative cancellation share one deadline.
- Log a reranker model only when reranking actually completed; fusion-only
  degradation must not be counted as reranker output in offline evaluation.
- Retry DuckDB read-only connection opens across the sink's brief write/checkpoint
  window; DuckDB rejects concurrent connections with different configurations.
- Keep cluster display projection separate from the clustering reduction and
  derive overview centroids from projected member points so both views align.
- Persist asynchronous eval reports by job id rather than in process memory, and
  redact settings recursively from typed values before exposing effective config.
- Validate batch-ingest items inside the route rather than as a typed envelope
  list so one malformed page becomes a per-item rejection instead of a batch-wide
  `422`; process items sequentially to preserve deterministic revisit semantics.
- Validate untyped embedder and reranker SDK results with pydantic before mapping
  them into domain values; exercise malformed responses and resilience timeouts.
- Keep deterministic adapter failure tests in the pull-request suite and run real
  spaCy/GLiNER model checks in a blocking scheduled workflow.
- Run LanceDB and Qdrant conformance in isolated CI jobs selected by pytest marks;
  the LanceDB job must not expose `QDRANT_URL`.
- Gate releases on Python 3.13 tests, `uv build`, and `twine check`; retain
  dependency audit, CodeQL, Gitleaks, and GitHub push protection as separate controls.
- The PR-triage storage helper accepts its SQLite path through `--db-path`;
  the older positional-path invocation is no longer supported.
- Check cluster-run existence through a primary-key lookup; projection rows can
  legitimately be empty while a run is in progress, so emptiness is not a 404 signal.
- Use finite-number constraints when validating embedder vectors and reranker
  scores; Pydantic's plain `float` accepts NaN and infinity.
- Enforce the target numeric representation's range before narrowing provider
  values; a finite Python float can overflow to infinity when cast to `float32`.
- Generate Python API inventories from source modules and HTTP/MCP reference
  details from the canonical OpenAPI document; keep conceptual guidance manual,
  but never duplicate endpoint, schema, service, or port inventories in Markdown.
- Write Prometheus bearer-token files to a path writable by the image's non-root
  user, create them under `umask 077`, and keep the scrape credentials path aligned.
- Escape Markdown table delimiters inside structural renderers such as schema-type
  unions; escaping only prose cells does not protect generated type columns.
- Bound persisted scheduling intervals at every external configuration boundary;
  values that fit SQLite integers can still overflow Python datetime arithmetic
  and leave due rows permanently consuming scheduler capacity.
- Preserve omitted-versus-null PATCH semantics with an explicit unset sentinel for
  nullable fields, and fail a durable fan-out job when every child operation fails
  so the queue can retry instead of recording a false success.
