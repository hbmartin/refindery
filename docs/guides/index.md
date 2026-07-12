# Guides

Task- and concept-oriented walkthroughs of Refindery's capabilities. Each guide
explains *how the feature works* and links to the precise API contract in the
[Reference](../reference/index.md).

<div class="grid cards" markdown>

- :material-tray-arrow-down: [**Ingesting pages**](ingest.md) — capture → index → enrich, the page lifecycle, and revisit semantics.
- :material-rss: [**Watches**](watches.md) — poll RSS/Atom feeds and YouTube playlists/channels on a schedule and auto-ingest new items.
- :material-magnify: [**Searching**](search.md) — the hybrid retrieval pipeline, filters, recency decay, and A/B compare.
- :material-robot: [**MCP for agents**](mcp.md) — connect an LLM agent, read vs mutating tools, and grounding.
- :material-chart-line: [**Evaluation**](eval.md) — score and replay retrieval quality from the query log.
- :material-chart-bubble: [**Clustering**](clustering.md) — resurface reading themes with stable cluster IDs.
- :material-tag-multiple: [**Entities**](entities.md) — extraction chains and corpus-internal canonicalization.
- :material-vector-triangle: [**Embedding models**](models.md) — register, backfill, activate, and compare models.
- :material-delete: [**Deletion & blacklist**](deletion.md) — purge content and block re-ingestion atomically.

</div>

The [Architecture overview](../architecture/index.md) ties these together into
the ingest → index → cluster data flow and the search pipeline.
