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

- Watch mode (`/v1/watches`, `WatchService`): a minute periodic (`watch_poll_tick`, prod-only) calls `tick()`, which enqueues one `POLL_WATCH` job per due watch with a time-varying idempotency key (`poll_watch:{id}:{next_run_at}`) and advances `next_run_at` at enqueue time — the handler never touches the schedule (forward progress even if the poll job dies). Handler fans out via `IngestService.ingest()` per discovered URL (canonical-URL revisit = dedup; no separate seen-URLs table). Sources implement `WatchSource.discover()` (`ports/watch_source.py`); RSS = `adapters/feeds/rss_feedparser.py` (feedparser is a core dep). New kinds: add `WatchKind` member + source in the container `sources` map; create route 501s for kinds absent from `WatchService.supported_kinds`.
- SSE job events (`/v1/events`): `JobEventBus` (`application/job_events.py`, plain asyncio, single-loop) is published to ONLY from `HueyJobQueue` on the main event loop (every ledger transition runs there); `recover()` deliberately never publishes (startup precedes any subscriber). Bus closes first in `Container.shutdown` so streams end before the queue stops. Not an MCP tool (a never-returning tool would hang clients). httpx 0.28 `ASGITransport` buffers full response bodies — an endless SSE response deadlocks tests; use `tests/fakes/streaming_transport.py::StreamingASGITransport` (returns at `http.response.start`, streams chunks, sends `http.disconnect` on close).
- `metrics_history.current_gauges` is gauge-only; use `current_counters` for counter families (prometheus_client strips `_total` from family names — pass the stripped name). Counter-derived API fields are process-lifetime, reset on restart; store-derived counts (`count_jobs_by_status`, `count_tombstones_by_status`) are durable truth.
- YouTube (`adapters/youtube/`): `RoutingFetcher` sends *video* URLs (`is_youtube_video_url`) to `YoutubeCaptionFetcher` → captions via yt-dlp (`youtube` extra; manual→auto, json3→vtt) → Whisper fallback (`transcribe`/`transcribe-mlx` extras) → `YoutubeTranscriptEnvelope` under synthetic content type `application/x-youtube-transcript+json` → `YoutubeTranscriptExtractor` (core, always registered) surfaces transcript + video title. One shared `YtDlpBackend` also powers `WatchKind.YOUTUBE` (playlist/channel flat extraction). `canonicalize()` folds `youtu.be/<id>`, `/shorts/<id>`, `/live/<id>` into `youtube.com/watch?v=<id>` via `domain/youtube.py::rewrite_to_watch` — `/watch` paths must stay byte-identical (stored canonicals). YouTube network tests live in the scheduled `youtube-captions.yml` workflow (`slow and external`), never the PR gate.

- Composition root is `application/container.py::build_container`; tests wire fakes via `tests/fakes/container.py::build_test_container` (real SQLite/LanceDB/huey/DuckDB, fake embedder/reranker/fetcher). API tests drive the app with `httpx.ASGITransport` inside `app.router.lifespan_context(app)`.
- `DuckDbSink` executes registered DDL at `start()` — construct `DuckDbQueryLog(sink)` (registers tables) before `sink.start()`.
- duckdb's Python client needs pytz to fetch TIMESTAMPTZ columns; select `epoch_us(ts)` and rebuild UTC datetimes instead (see `DuckDbQueryLogReader`).
- Search ranking only exists after fusion → rerank → rollup → hydrate; pagination/limits must be applied at the final slice in `SearchService.search`, never pushed into the vector-store arms.
- Auth: `require_read`/`require_write` in `api/auth.py`; routers get read globally in `app.py`, mutating routes add `dependencies=[Depends(require_write)]`. MCP tool calls replay over HTTP with the caller's token, so scopes apply on every transport; `enable_mutating_tools` is visibility-only.
- CLI is argparse in `src/refindery/cli.py` (`refindery serve|eval score|eval replay`); no subcommand defaults to serve so `python -m refindery` keeps working.
- Ruff (select=ALL) gotchas seen here: ASYNC110 (no while-await-sleep polling loops with empty bodies), ASYNC240 (no pathlib calls in async defs), S110 (except-pass needs noqa). Tests get per-file-ignores for ANN001/ANN201/ARG001/PLC0415 etc. but NOT ANN202/ANN205/ARG002/A002/EM102/E501 — annotate private helpers and stub methods in tests too.
- lizard threshold in CI: CCN > 15 on `src` only.
- Provider resilience lives in `adapters/resilience/` (CircuitBreaker/BreakerRegistry keyed per provider failure domain, `call_with_retry`, `ResilientEmbedder`/`ResilientReranker` wrapping the ports; `guarded_call` also used inside `OpenAiCompatClient`). Fakes in `tests/fakes/` stay unwrapped. Breaker-open (`ProviderUnavailableError`) job failures requeue WITHOUT incrementing attempts (`huey_queue._execute`); search degrades to fusion-only on rerank failure, `/v1/compare` fails loudly on purpose.
- `_execute` bounds handlers with `asyncio.timeout` (`jobs.handler_timeout_s`, default = lease); `lease_timeout.expired()` distinguishes a lease cancellation from a provider TimeoutError that escaped the handler. The lease watchdog periodic is observe-only — never re-enqueue while the process lives (a zombie thread may still be mid-write; single-writer invariant).
- `# type: ignore[...]` does NOT suppress ty — this repo uses `# ty: ignore[...]` / `# pyrefly: ignore[...]` (see gliner_spacy.py). Faking an optional module in tests: `module = types.ModuleType("x"); module.__dict__["Attr"] = ...` (plain attribute assignment fails ty/pyrefly), then `monkeypatch.setitem(sys.modules, "x", module)`.
- Annotations are evaluated eagerly (py313, no `from __future__ import annotations`): a module-level helper annotated with a class defined later in the file NameErrors at import — define helpers after the dataclasses they reference.
- Docs: Zensical site configured in `zensical.toml` (repo root, `[project]`-scoped TOML, not mkdocs.yml). Content under `docs/`; nav is explicit. Build with `uv run zensical build --clean --strict` (the CI gate in `.github/workflows/docs.yml` → GitHub Pages), preview with `uv run zensical serve`. `--strict` aborts on missing nav targets or broken internal links, and it renders *every* `.md` under `docs/` (not just nav'd ones). The `docs`/`docs = [...]` dependency group has `zensical` + `mkdocstrings[python]`. Python API pages use `::: dotted.path` (mkdocstrings/griffe, static — never imports the heavy extras); `paths = ["src"]` in the plugin config is repo-relative. Zensical plugin config is written raw under `[project.plugins.<name>...]` and wrapped internally. Emoji/material icons need `[project.markdown_extensions.pymdownx.emoji]` with `zensical.extensions.emoji.to_svg`/`twemoji`, or `:material-*:` shortcodes render literally.
