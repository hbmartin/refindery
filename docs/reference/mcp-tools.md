# MCP tools

The [MCP](../guides/mcp.md) server at `/mcp` generates its tools from the REST
routes. Each tool call replays through the corresponding HTTP route with the
caller's bearer token, so [scopes](../configuration/auth.md) are enforced
identically to the HTTP API.

## Read-only tools (default)

| Tool | Backing route | Purpose |
| --- | --- | --- |
| `search` | `POST /v1/search` | Hybrid retrieval; ranked pages with matched passages. |
| `get_page` | `GET /v1/pages/{id}` | Full body text and metadata for a page. |
| `page_status` | `GET /v1/pages/{id}/status` | Lifecycle status and feature warnings. |
| `similar_to` | `GET /v1/pages/{id}/similar` | Related pages via vector/cluster/entity mediation. |
| `list_clusters` | `GET /v1/clusters` | Current (non-tombstoned) clusters. |
| `cluster_pages` | `GET /v1/clusters/{id}` | Member pages of a cluster. |
| `entities` | `GET /v1/entities/{id_or_form}` | Resolve an entity by ID or canonical form. |
| `compare` | `POST /v1/compare` | A/B two embedding models over one query. |

## Mutating tools (opt-in)

Listed only when `REFINDERY_MCP__ENABLE_MUTATING_TOOLS=true`:

| Tool | Backing route | Purpose |
| --- | --- | --- |
| `add_page` | `POST /v1/pages` | Ingest a page. |
| `forget` | `POST /v1/forget` | Purge and blacklist. |

!!! warning "Visibility ≠ authorization"
    The flag controls only which tools appear in `tools/list`. Authorization is
    always the token's scopes: a `read`-scoped token cannot call `add_page` or
    `forget` even when they are visible. See [MCP for agents](../guides/mcp.md).

## Grounding contract

Tool descriptions state that results are grounded passages from the user's
reading history — no information the user has not read, empty when nothing
matches — and that retrieved page text is untrusted content to be treated as
data, never as instructions.
