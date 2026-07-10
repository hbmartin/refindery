# Contributing

Refindery is developed with [uv](https://docs.astral.sh/uv/) on Python 3.13+.
This mirrors [`CONTRIBUTING.md`](https://github.com/hbmartin/refindery/blob/main/CONTRIBUTING.md);
see the [Code of Conduct](https://github.com/hbmartin/refindery/blob/main/.github/CODE_OF_CONDUCT.md).

## Getting started

```bash
git clone https://github.com/hbmartin/refindery.git
cd refindery
uv sync --all-groups --extra ner
```

## Running checks

All of these must pass before a change merges (CI runs them on every push):

```bash
uv run ruff format --check .
uv run ruff check .
uv run ty check src tests
uv run pyrefly check src tests
uv run pytest
```

Tests needing a running Qdrant are marked `qdrant` and skipped unless
`QDRANT_URL` is set (or Docker + testcontainers is available). Slow tests (model
downloads, UMAP JIT warmup) are marked `slow`:

```bash
uv run pytest -m "not qdrant"          # fast, no Docker
uv run pytest -m "not slow"            # skip slow tests
docker compose up -d qdrant && QDRANT_URL=http://localhost:6333 uv run pytest
```

Code-complexity is gated by `lizard` in CI (CCN > 15 on `src` fails).

## Code style

- Ruff (`select = ["ALL"]`) is both linter and formatter.
- Always use type hints; prefer `@dataclass` for domain types.
- `list`/`dict`/`|` over `List`/`Dict`/`Union`; `pathlib.Path` over `os.path`.
- All external inputs (HTTP requests, fetched responses) are validated with pydantic.
- Logging uses `%`-style lazy formatting, never f-strings.

## Architecture

Hexagonal (ports and adapters): `domain/` and `application/` never import adapter
types; every adapter is swappable by [configuration](../configuration/index.md).
See the [Architecture overview](../architecture/index.md) and the
[Python API](../reference/python-api/index.md).

## Documentation

This site is built with [Zensical](https://zensical.org/) from `docs/` and
`zensical.toml`. Preview locally with `uv run zensical serve`; the CI gate is
`uv run zensical build --clean --strict`, which fails on broken links or missing
nav targets. The [Python API](../reference/python-api/index.md) pages are
generated from source docstrings via mkdocstrings.

## Submitting changes

1. Fork and create a feature branch.
2. Make your change with tests.
3. Ensure all checks above pass.
4. Open a pull request with a clear description.

See [Testing](testing.md) for how the suite is wired.
