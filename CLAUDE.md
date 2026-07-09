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

# Update CLAUDE.md
Update CLAUDE.md with notes, learnings, findings, or other useful patterns you have learned

# Notes

- Composition root is `application/container.py::build_container`; tests wire fakes via `tests/fakes/container.py::build_test_container` (real SQLite/LanceDB/huey/DuckDB, fake embedder/reranker/fetcher). API tests drive the app with `httpx.ASGITransport` inside `app.router.lifespan_context(app)`.
- `DuckDbSink` executes registered DDL at `start()` — construct `DuckDbQueryLog(sink)` (registers tables) before `sink.start()`.
- duckdb's Python client needs pytz to fetch TIMESTAMPTZ columns; select `epoch_us(ts)` and rebuild UTC datetimes instead (see `DuckDbQueryLogReader`).
- Search ranking only exists after fusion → rerank → rollup → hydrate; pagination/limits must be applied at the final slice in `SearchService.search`, never pushed into the vector-store arms.
- Auth: `require_read`/`require_write` in `api/auth.py`; routers get read globally in `app.py`, mutating routes add `dependencies=[Depends(require_write)]`. MCP tool calls replay over HTTP with the caller's token, so scopes apply on every transport; `enable_mutating_tools` is visibility-only.
- CLI is argparse in `src/refindery/cli.py` (`refindery serve|eval score|eval replay`); no subcommand defaults to serve so `python -m refindery` keeps working.
- Ruff (select=ALL) gotchas seen here: ASYNC110 (no while-await-sleep polling loops with empty bodies), ASYNC240 (no pathlib calls in async defs), S110 (except-pass needs noqa).
- lizard threshold in CI: CCN > 15 on `src` only.
