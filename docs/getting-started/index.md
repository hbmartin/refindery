# Getting started

Refindery runs as a single local process. The only hard external dependency is
an **embedding provider** (for indexing and search) and a **vector store**. You
choose the vector store — and therefore how much infrastructure you run — with
one setting:

<div class="grid cards" markdown>

-   :material-laptop: **Daemon-free (LanceDB)**

    ---

    Everything in one process, all state under `data/`. No Docker, no daemon.
    The fastest way to try Refindery and the recommended workstation profile.

    `REFINDERY_VECTOR_STORE=lancedb`

-   :material-docker: **Docker (Qdrant)**

    ---

    Qdrant runs as a daemon (native server-side hybrid fusion and filter
    pushdown). The default for larger collections. Run only Qdrant in Docker, or
    the whole stack.

    `REFINDERY_VECTOR_STORE=qdrant`

</div>

Both stores pass the same conformance suite, so you can start daemon-free and
move to Qdrant later by [registering and backfilling](../guides/models.md) into
the new store.

## Pick your path

| You want to… | Start here |
| --- | --- |
| Try it on macOS with the least friction | [Installation → macOS one-stop (no Docker)](installation.md#macos-one-stop-homebrew-no-docker) |
| Run the full Qdrant stack on macOS | [Installation → macOS one-stop Docker](installation.md#macos-one-stop-docker) |
| Set it up by hand on any OS | [Installation → Manual minimal profile](installation.md#manual-minimal-profile-no-docker) |
| See it actually work end-to-end | [Quickstart](quickstart.md) |
| Confirm the install is healthy | [Validate the install](validate.md) |

## Prerequisites

- **Python 3.13+**
- [**uv**](https://docs.astral.sh/uv/) for dependency management
- An **embedding provider API key** (the default is [Voyage](https://www.voyageai.com/);
  Cohere, OpenAI, and local models are also supported). Indexing and search do
  not work until a provider is configured.
- **Docker** only if you choose the Qdrant profile.

Once installed, head to the [Quickstart](quickstart.md) to ingest your first
page and run a search.
