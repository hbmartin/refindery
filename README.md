# Refindery

A local, single-machine retrieval engine over the web pages you read.

Upstream capture systems (browser extensions, history readers) extract
main-body text and POST it here. Refindery chunks, embeds, indexes, clusters,
and extracts entities from that text, then serves hybrid retrieval over it via
a local HTTP API and an MCP server.

📖 **Full documentation: <https://hbmartin.github.io/refindery/>**

## Companion projects

- **[Refindery Chrome Extension](https://github.com/hbmartin/refindery-chrome-extension)** — capture pages as you browse.
- **[Browser History Refindery](https://github.com/hbmartin/browser-history-refindery)** — import and search your browser history.

**This is a retrieval engine, not a Q&A system.** It returns ranked, grounded
passages with provenance. Synthesis is the caller's job — typically an LLM
agent (e.g. Claude via MCP) that treats Refindery as a tool. No generation
appears on the query path.

## Jobs to be done

1. **Refind** — "I read something about X, take me back to it."
2. **Synthesize** — "What have I learned about Y?" (agent-mediated; Refindery supplies the passages)
3. **Resurface** — "What have I been reading a lot about?" (clusters, similarity)

## Quickstart

The fastest path on macOS (Homebrew, no Docker) writes a private `.env` with a
generated auth token and the daemon-free LanceDB profile:

```bash
VOYAGE_API_KEY=... ./scripts/setup-macos.sh --start
```

On later runs, start the server with the generated environment:

```bash
uv run --env-file .env refindery serve
```

Then ingest a page and search it:

```bash
curl -s -X POST http://127.0.0.1:8000/v1/pages \
  -H "Authorization: Bearer $REFINDERY_AUTH_TOKEN" -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/article", "title": "An Article",
       "body_extracted": "Plain text main body content...",
       "fetched_at": "2026-07-08T10:00:00Z", "source": "extension"}'

curl -s -X POST http://127.0.0.1:8000/v1/search \
  -H "Authorization: Bearer $REFINDERY_AUTH_TOKEN" -H "Content-Type: application/json" \
  -d '{"query": "main body content"}'
```

Other install paths (Docker/Qdrant, manual, fully containerized) are in the
[Installation guide](https://hbmartin.github.io/refindery/getting-started/installation/).

## Documentation

The [documentation site](https://hbmartin.github.io/refindery/) is the canonical
reference:

- [**Getting started**](https://hbmartin.github.io/refindery/getting-started/) — install, quickstart, and validation.
- [**Guides**](https://hbmartin.github.io/refindery/guides/) — ingest, search, MCP, evaluation, clustering, entities, models, deletion.
- [**Configuration**](https://hbmartin.github.io/refindery/configuration/) — the settings model, deployment profiles, auth, tuning, observability.
- [**Architecture**](https://hbmartin.github.io/refindery/architecture/) — hexagonal ports/adapters, data flow, and the data model.
- [**Operations**](https://hbmartin.github.io/refindery/operations/) — reset, purge, job leases, and accepted risks.
- [**Reference**](https://hbmartin.github.io/refindery/reference/) — HTTP API, upstream ingest API, MCP tools, CLI, and the Python API.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) and the
[Contributing docs](https://hbmartin.github.io/refindery/contributing/).
TL;DR: `uv sync --all-groups --extra ner`, then
`uv run ruff format . && uv run ruff check . && uv run pytest && uv run ty check && uv run pyrefly check`.

Preview the docs locally with `uv run zensical serve`.

## License

MIT — see [LICENSE.txt](LICENSE.txt).
