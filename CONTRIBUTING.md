# Contributing to Refindery

Thanks for your interest in contributing! Refindery is a local, single-machine
retrieval engine over the web pages you read. By participating in this project
you agree to abide by our [Code of Conduct](.github/CODE_OF_CONDUCT.md).

## Requirements

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) for dependency management
- Docker (optional — only needed to run Qdrant locally; the LanceDB profile
  and most of the test suite run without it)

## Getting started

```bash
git clone https://github.com/hbmartin/refindery.git
cd refindery
uv sync --all-groups
```

## Running checks

All of these must pass before a change is merged (CI runs them on every push):

```bash
uv run ruff check .
uv run ruff format --check .
uv run ty check src tests
uv run pyrefly check src tests
uv run pytest
```

Tests that need a running Qdrant are marked `qdrant` and are skipped unless
`QDRANT_URL` is set (or Docker + testcontainers is available):

```bash
docker compose up -d qdrant
QDRANT_URL=http://localhost:6333 uv run pytest
# or, without Docker:
uv run pytest -m "not qdrant"
```

Slow tests (local model downloads, UMAP JIT warmup) are marked `slow`; use
`uv run pytest -m "not slow"` for a fast loop.

## Code style

- Ruff (`select = ["ALL"]`) is the linter and formatter; run `uv run ruff format .`
- Always use type hints; prefer `@dataclass` for domain types
- `list`/`dict`/`|` over `List`/`Dict`/`Union`; `pathlib.Path` over `os.path`
- All external inputs (HTTP requests, fetched responses) are validated with pydantic
- Logging uses `%`-style lazy formatting, never f-strings

## Architecture

Hexagonal (ports and adapters). `domain/` and `application/` never import
adapter types; every adapter is swappable via config. See `README.md` for the
full design.

## Submitting changes

1. Fork and create a feature branch
2. Make your change with tests
3. Ensure all checks above pass
4. Open a pull request with a clear description
