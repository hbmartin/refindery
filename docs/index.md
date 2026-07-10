---
title: Refindery
---

# Refindery

**A local, single-machine retrieval engine over the web pages you read.**

Upstream capture systems — browser extensions, history readers — extract the
main-body text of pages you visit and `POST` it to Refindery. Refindery chunks,
embeds, indexes, clusters, and extracts entities from that text, then serves
**hybrid retrieval** over it through a local HTTP API and an
[MCP](guides/mcp.md) server.

!!! quote "A retrieval engine, not a Q&A system"
    Refindery returns ranked, grounded passages with provenance. Synthesis is
    the caller's job — typically an LLM agent (e.g. Claude via MCP) that treats
    Refindery as a tool. **No generation appears on the query path.**

## Jobs to be done

<div class="grid cards" markdown>

-   :material-history: **Refind**

    ---

    "I read something about X, take me back to it." Paste a URL or describe the
    passage; Refindery pins exact matches and ranks the rest.

-   :material-lightbulb-on: **Synthesize**

    ---

    "What have I learned about Y?" Agent-mediated — Refindery supplies the
    grounded passages, the agent writes the synthesis.

-   :material-chart-bubble: **Resurface**

    ---

    "What have I been reading a lot about?" Clusters and similarity surface the
    themes in your reading history.

</div>

## How it fits together

```
   upstream capture ──▶  HTTP API (FastAPI) + MCP server
                                  │
                          Application services
                    Ingest · Search · Cluster · Entity · Compare · Forget
                                  │  ports
        VectorStore · MetadataStore · Embedder · Reranker · EntityExtractor · ClusterEngine
```

A single non-blocking `asyncio` process hosts the FastAPI app, the MCP server,
and the durable job-queue consumer; CPU-bound work (UMAP/HDBSCAN) runs in a
process pool. Everything behind a port is swappable by configuration — see the
[Architecture overview](architecture/index.md).

## Companion projects

- [**Refindery Chrome Extension**](https://github.com/hbmartin/refindery-chrome-extension)
  — capture pages as you browse.
- [**Browser History Refindery**](https://github.com/hbmartin/browser-history-refindery)
  — import and search your existing browser history.

## Where to next

<div class="grid cards" markdown>

- :material-rocket-launch: [**Getting started**](getting-started/index.md) — install and run Refindery in minutes.
- :material-book-open-variant: [**Guides**](guides/index.md) — ingest, search, MCP, eval, clustering, entities.
- :material-tune: [**Configuration**](configuration/index.md) — the settings model, deployment profiles, tuning.
- :material-api: [**Reference**](reference/index.md) — HTTP API, MCP tools, CLI, and the Python API.

</div>

!!! info "Status"
    Refindery is an alpha, single-user system. It keeps two operational risks
    explicit — lease-only job execution and retained raw query text — documented
    in [Operations](operations/index.md).
