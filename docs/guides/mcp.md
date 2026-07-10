# MCP for agents

Refindery exposes its retrieval surface to LLM agents through the
[Model Context Protocol](https://modelcontextprotocol.io/). The MCP server is
mounted at `/mcp` in the same process as the HTTP API, and its tools are
generated from the REST routes.

## Connect

The server speaks streamable HTTP and uses the same bearer token as the REST
API:

```bash
claude mcp add --transport http refindery http://127.0.0.1:8000/mcp \
  --header "Authorization: Bearer $REFINDERY_AUTH_TOKEN"
```

## Read vs mutating tools

Read-only tools are exposed by default:

`search` · `get_page` · `page_status` · `similar_to` · `list_clusters` ·
`cluster_pages` · `entities` · `compare`

Mutating tools (`add_page`, `forget`) are **opt-in**:

```dotenv
REFINDERY_MCP__ENABLE_MUTATING_TOOLS=true
```

!!! warning "Visibility is not authorization"
    `enable_mutating_tools` controls only *which tools are listed*. Every MCP
    tool call replays through the HTTP routes with the caller's bearer token, so
    the token's [scopes](../configuration/auth.md) are enforced on every
    transport. A read-scoped token cannot mutate even if a mutating tool is
    visible.

See the [MCP tools reference](../reference/mcp-tools.md) for each tool's inputs.

## Grounding

Tool descriptions state explicitly that results are grounded passages from the
user's reading history — they contain no information the user has not read, and
return empty when nothing matches. This is what makes hard-grounding hold at the
agent layer: the agent synthesizes only over what Refindery returns.

!!! danger "Treat retrieved page text as untrusted"
    Page text is data the user browsed, not instructions. Tool descriptions
    direct clients to treat retrieved content as untrusted input and never to
    follow instructions embedded in it. Keep mutating tools disabled unless an
    agent genuinely needs to add or forget pages.

## Related

- [MCP tools reference](../reference/mcp-tools.md) — the full tool catalog.
- [Authentication](../configuration/auth.md) — scoped tokens per capture source or agent.
- [Searching](search.md) — what the `search` tool runs under the hood.
