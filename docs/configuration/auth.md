# Authentication

A bearer token is **always required**, even on loopback. The server binds to
`127.0.0.1` by default. Tokens carry scopes that decide what they may do, and
scopes are enforced on every transport — HTTP and [MCP](../guides/mcp.md) alike.

## Single token

`REFINDERY_AUTH_TOKEN` is a single full-access token — the simplest setup:

```bash
export REFINDERY_AUTH_TOKEN="$(openssl rand -hex 24)"
```

## Named scoped tokens

To hand each capture source or agent its own revocable token, configure named
tokens with `read` or `write` scopes (`write` implies `read`; both forms can
coexist):

```bash
export REFINDERY_AUTH_TOKENS='[
  {"name": "chrome-capture", "token": "...", "scopes": ["write"]},
  {"name": "agent",          "token": "...", "scopes": ["read"]}
]'
```

Revoke a token by removing its entry and restarting.

## Scopes

| Scope | Grants |
| --- | --- |
| `read` | search, browse, compare, similarity, record feedback. |
| `write` | everything `read` grants, plus mutating routes: `add_page`, `forget`, un-blacklist, cluster recompute, model management, entity-merge undo, job retry. |

A valid token missing the required scope gets `403`; an absent or unknown token
gets `401`.

## MCP and scopes

MCP tool calls replay through the HTTP routes with the caller's token, so scopes
bind identically on that transport. The
`REFINDERY_MCP__ENABLE_MUTATING_TOOLS` flag only controls which tools are
*listed* — it is not authorization. See the [MCP guide](../guides/mcp.md).

## Related

- [MCP for agents](../guides/mcp.md) — per-agent tokens and grounding.
- [Deletion & blacklist](../guides/deletion.md) — a `write`-scoped operation.
- [Settings reference](reference.md) — `auth_token` / `auth_tokens` fields.
