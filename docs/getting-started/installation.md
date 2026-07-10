# Installation

Five ways to install and launch Refindery, from a one-command macOS setup to a
fully containerized stack. All of them end with a server listening on
`http://127.0.0.1:8000`. After any of them, continue to the
[Quickstart](quickstart.md).

!!! note "Provider key"
    The default embedding model requires a `VOYAGE_API_KEY`. Without a provider
    key the server starts but indexing stays unavailable. Use `--skip-api-key`
    (setup scripts) to prepare the environment first and add the key later.

## macOS one-stop (Homebrew, no Docker)

Run the idempotent setup script from the repository root. It installs Homebrew
when needed, installs Python 3.13 and `uv`, syncs the locked dependencies, and
writes a private `.env` with a generated auth token and the daemon-free LanceDB
profile. It reads your Voyage key without echoing it and configures Voyage
reranking too, so one key covers both services.

```bash
./scripts/setup-macos.sh
```

For a non-interactive setup, pass the key through the environment. Add `--start`
to launch Refindery after setup completes.

```bash
VOYAGE_API_KEY=... ./scripts/setup-macos.sh --start
```

On later runs, start the server with the generated environment:

```bash
uv run --env-file .env refindery serve
```

Use `--skip-api-key` when you only want to prepare the development environment.

## macOS one-stop Docker

If Docker Desktop is already installed, the Docker setup script configures the
Qdrant profile, builds the app, starts the full stack, and waits for the
readiness check. It writes generated credentials and container-specific settings
to a private `.env.docker`, leaving the daemon-free `.env` untouched.

```bash
./scripts/setup-macos-docker.sh
```

For unattended setup, pass the Voyage key through the environment. Use
`--no-start` to prepare and validate without launching, or `--skip-api-key` when
indexing does not need to work yet.

```bash
VOYAGE_API_KEY=... ./scripts/setup-macos-docker.sh
```

On later runs, use the dedicated environment file with Compose:

```bash
docker compose --env-file .env.docker up -d --build
```

## Manual minimal profile (no Docker)

The daemon-free profile by hand, on any OS:

```bash
uv sync --extra ner
export REFINDERY_AUTH_TOKEN="$(openssl rand -hex 24)"
export REFINDERY_VECTOR_STORE=lancedb
export VOYAGE_API_KEY=...           # or configure another embedding provider
python -m refindery
```

The `ner` extra installs the default spaCy entity-extraction model, which is
[required at startup](../guides/entities.md).

## Docker profile (Qdrant, the default store)

Run only Qdrant in Docker and the Python process on the host:

```bash
uv sync --extra ner
docker compose up -d qdrant
export REFINDERY_AUTH_TOKEN="$(openssl rand -hex 24)"
python -m refindery
```

See [Deployment profiles](../configuration/deployment-profiles.md#qdrant-profile)
for the matching `.env` settings.

## Fully containerized

The multi-stage `Dockerfile` builds a slim image with the `ner` extra (no
torch/gliner; add extras to the sync lines if you need them). Data lives on the
`refindery_data` volume, model caches on `refindery_models`. On macOS,
`./scripts/setup-macos-docker.sh` automates this profile and keeps its settings
separate from a host-Python `.env`.

```bash
export REFINDERY_AUTH_TOKEN="$(openssl rand -hex 24)"
export VOYAGE_API_KEY=...
docker compose up -d --build
curl -s http://127.0.0.1:8000/healthz
```

## Optional extras

| Extra | Enables | Pulls in |
| --- | --- | --- |
| `html` | `body_html` / fetched-HTML extraction (pulpie) | torch (~2 GB) |
| `gliner` | GLiNER zero-shot NER | gliner, onnxruntime |
| `ner` | spaCy NER model | en_core_web_sm |
| `leiden` | Leiden clustering | igraph, leidenalg |

Combine extras as needed, for example `uv sync --extra gliner --extra ner`.
Entity extraction is required at startup — see [Entities](../guides/entities.md).

Next: the [Quickstart](quickstart.md), then [Validate the install](validate.md).
