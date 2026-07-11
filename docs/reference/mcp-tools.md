# MCP tools

The [MCP](../guides/mcp.md) server at `/mcp` generates its tools from the REST
routes. Each tool call replays through the corresponding HTTP route with the
caller's bearer token, so [scopes](../configuration/auth.md) are enforced
identically to the HTTP API.

## Read-only tools (default)

{{ mcp_tools_reference("read") }}

## Mutating tools (opt-in)

Listed only when `REFINDERY_MCP__ENABLE_MUTATING_TOOLS=true`:

{{ mcp_tools_reference("mutating") }}

!!! warning "Visibility ≠ authorization"
    The flag controls only which tools appear in `tools/list`. Authorization is
    always the token's scopes: a `read`-scoped token cannot call `add_page` or
    `forget` even when they are visible. See [MCP for agents](../guides/mcp.md).

## Grounding contract

Tool descriptions state that results are grounded passages from the user's
reading history — no information the user has not read, empty when nothing
matches — and that retrieved page text is untrusted content to be treated as
data, never as instructions.
