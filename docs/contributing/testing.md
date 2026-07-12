# Testing

The test suite favors **real local adapters with fakes only for external I/O**,
so tests exercise the actual SQLite/LanceDB/Huey/DuckDB code paths while staying
deterministic and offline.

## Layout

| Directory | Covers |
| --- | --- |
| `tests/unit/` | Domain logic, config, migrations, CLI, ranking metrics, retrieval, rollup, setup scripts. |
| `tests/integration/` | SQLite store, Huey queue, entities + clusters, the eval harness. |
| `tests/api/` | Auth scopes, pages, search, forget, entities, models + compare, MCP — driven through `create_app`. |
| `tests/conformance/` | One shared `test_vector_store.py` run against **both** the LanceDB and Qdrant adapters. |
| `tests/fakes/` | Deterministic fakes and the test container builder. |

## Container wiring

`tests/fakes/container.py::build_test_container()` uses real local adapters
(SQLite, LanceDB, Huey, Chonkie, DuckDB) with fakes for external services — the
embedder (deterministic hash embedder), fetcher, reranker, entity extractors, and
surface embedder. `make_test_settings(tmp_path)` points every state path at a
temp directory and defines two tokens (a full-access one and a read-only one) so
scope behavior is testable. The clustering engine is replaced by an inline engine
that runs the real worker synchronously (no process pool). `create_app(settings,
container=...)` injects the whole thing.

API tests drive the app with `httpx.ASGITransport` inside
`app.router.lifespan_context(app)`, so startup/shutdown run exactly as in
production.

## Conformance

Both vector-store adapters must pass the same conformance suite. When you touch
either adapter — or the `VectorStore` [port](../reference/python-api/ports.md) —
run the conformance tests against both. The qdrant param resolves its target in
priority order: `QDRANT_URL` (a server URL or `":memory:"` for the in-process
local mode) → an automatic testcontainer when Docker is available (pinned to
the compose/CI image) → skip. `make test-qdrant` and `make test-qdrant-local`
wrap the two common runs; see [Contributing](index.md#running-checks).

## Notes for adapter work

- `DuckDbSink` executes registered DDL at `start()` — construct
  `DuckDbQueryLog(sink)` before `sink.start()`.
- DuckDB's Python client needs pytz for `TIMESTAMPTZ`; select `epoch_us(ts)` and
  rebuild UTC datetimes instead.
- Search ranking exists only after fusion → rerank → rollup → hydrate; apply
  pagination at the final slice in `SearchService.search`, never in a store arm.

Coverage has a floor in CI; keep new code covered.

External-facing adapters use deterministic transport or SDK stubs in the pull
request suite, including timeout, malformed-response, retry, and fallback paths.
The scheduled `Real Extractor Models` workflow installs the optional spaCy and
GLiNER models and treats upstream model regressions as blocking failures.

LanceDB and Qdrant conformance run as separate CI jobs. The LanceDB job has no
daemon configuration; the Qdrant job is selected explicitly against its service
container, so neither adapter can silently stand in for the other.

The `Security` workflow runs dependency auditing, CodeQL, and Gitleaks. Repository
administrators should also enable GitHub secret scanning and push protection in
the repository's code-security settings; those platform controls complement the
versioned workflow gate.
