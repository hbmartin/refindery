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
