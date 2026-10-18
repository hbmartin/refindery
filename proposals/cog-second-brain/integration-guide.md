# Integrating Refindery with your second brain (COG)

A practical guide to using **Refindery** as the semantic-memory backend for an
agentic knowledge system like
[COG-second-brain](https://github.com/huytieu/COG-second-brain) — or any
markdown/agent "second brain."

> **What works today vs. what's planned.** Everything in *Available now* uses
> Refindery's existing HTTP + MCP API — no new code. Sections marked *Planned*
> point at the [implementation roadmap](03-roadmap.md).

## The idea in one picture

```
   You read the web            You think & write
   (browser extension,         (COG skills, notes,
    history import)             braindumps, research)
          │                            │
          ▼                            ▼
   ┌──────────────┐             ┌──────────────┐
   │  REFINDERY   │◀── MCP ────▶│     COG      │
   │  the memory  │  search /   │  the brain   │
   │  hybrid      │  entities / │  synthesis,  │
   │  retrieval   │  clusters   │  workflows,  │
   └──────────────┘             └──────────────┘
```

Refindery **remembers and retrieves** (ranked, grounded passages with
provenance). COG **reasons and writes** (synthesis, briefs, CRM). Refindery
never generates answers — that's the agent's job — which is exactly the boundary
COG's verification-first skills want.

## Prerequisites

- Refindery running locally (see the project README / installation guide).
- An agent host that speaks MCP over HTTP — Claude Code, Cursor, or any COG
  target surface.
- Your Refindery auth token (the setup script writes `REFINDERY_AUTH_TOKEN` into
  `.env`).

---

## Available now

### Step 1 — Start Refindery

```bash
uv run --env-file .env refindery serve
# HTTP API + MCP server on http://127.0.0.1:8000
```

The MCP server is mounted at **`/mcp`**. It authenticates with the same bearer
token as the HTTP API and, by default, exposes **read-only** tools.

### Step 2 — Connect your agent over MCP

Point your agent at Refindery's MCP endpoint with an `Authorization` header.
A representative HTTP-MCP server entry (adapt to your host's config format):

```jsonc
{
  "mcpServers": {
    "refindery": {
      "type": "http",
      "url": "http://127.0.0.1:8000/mcp",
      "headers": { "Authorization": "Bearer ${REFINDERY_AUTH_TOKEN}" }
    }
  }
}
```

For Claude Code specifically you can register it with `claude mcp add` (HTTP
transport, same URL + header) instead of hand-editing config.

### Step 3 — What your agent can now do

Once connected, these **read-only** Refindery tools are available to the agent
(so COG skills call them instead of `grep`-ing your vault):

| Tool | Use it for |
| ---- | ---------- |
| `search` | Hybrid semantic + full-text recall over everything you've read. |
| `similar_to` | "More like this page." |
| `list_clusters` / `cluster_pages` | Resurface themes; browse a topic. |
| `entities` | Look up a person/org/concept and the pages mentioning it. |
| `get_page` / `page_status` | Fetch a specific page / check ingest state. |
| `compare` | A/B two embedding models on one query (eval). |

The results carry **provenance** — canonical URL and stable chunk identifiers —
so any claim your agent writes can cite the exact source. That's the raw
material COG's "sources required, confidence-stamped" skills need.

**Mutating tools** (`add_page`, `create_watch`, `forget`, …) are **off by
default**; enable them only if you want the agent to write, via
`REFINDERY_MCP__ENABLE_MUTATING_TOOLS=true`. Scopes still apply — the token must
carry `write`.

### Step 4 — Feed your notes and reading into Refindery

Anything you want the agent to recall must be ingested. Two paths today:

**a) Web pages** — the companion capture tools do this automatically:
- [Refindery Chrome Extension](https://github.com/hbmartin/refindery-chrome-extension)
- [Browser History Refindery](https://github.com/hbmartin/browser-history-refindery)

**b) Notes / any text** — POST directly. You can ingest a COG note by sending
its text with a stable identifier URL:

```bash
curl -s -X POST http://127.0.0.1:8000/v1/pages \
  -H "Authorization: Bearer $REFINDERY_AUTH_TOKEN" -H "Content-Type: application/json" \
  -d '{"url": "https://vault.local/05-knowledge/ada-lovelace.md",
       "title": "Ada Lovelace — notes",
       "body_extracted": "Full markdown/plain text of the note...",
       "fetched_at": "2026-07-18T10:00:00Z", "source": "cog-vault"}'
```

Refindery chunks, embeds, indexes, clusters, and extracts entities from it, and
it becomes searchable alongside your reading. Use a **stable URL** per note so
re-posting an edited note updates in place (dedup is by canonical URL).

> A small script that walks your vault and POSTs each `.md` file gives you a
> working "vault → memory" sync today. Native, incremental vault sync is
> *Planned* (see below).

---

## Planned — deeper, native integration

These need Refindery code and are scoped in the [roadmap](03-roadmap.md):

- **Native vault ingestion** — a first-class authored-note source so
  `05-knowledge/*.md` indexes incrementally on edit, without the manual POST
  script. (Roadmap [4a](03-roadmap.md).)
- **Export to your vault** — `refindery export` writes entity dossiers, cluster
  summaries, and weekly "resurface" digests as markdown into your vault, so
  Refindery becomes COG's auto-organize engine. (Roadmap [4b](03-roadmap.md).)
- **Freshness / verification loop** — Refindery re-fetches sources, detects
  drift, and tombstones dead links; COG stamps `last_verified` + confidence.
  (Roadmap [1a](03-roadmap.md) + [B4](02-integration.md).)
- **Entity dossiers & related-entity graph** — richer "everything about X"
  recall for the agent. (Roadmap [2a/2b](03-roadmap.md).)
- **One-install skill pack** — a `refindery-memory` pack that registers the MCP
  server and bundles recall+synthesis skills. (Roadmap [4c](03-roadmap.md).)

---

## Who does what

| Concern | Refindery | COG (your agent) |
| ------- | --------- | ---------------- |
| Store & index content | ✅ | — |
| Semantic + full-text recall | ✅ | calls it |
| Clustering / entities / similarity | ✅ | consumes it |
| Provenance & citations | ✅ supplies | ✅ cites |
| Synthesis / briefs / answers | ❌ (by design) | ✅ |
| Workflows, People CRM, PM skills | — | ✅ |
| Re-fetch & drift detection *(planned)* | ✅ mechanical | ✅ semantic re-verify |

## Privacy & boundaries

- **Local-first.** Refindery binds to `127.0.0.1` and needs no external services
  in the LanceDB profile. Your reading and notes stay on your machine.
- **No generation on the query path.** Refindery returns passages, never
  answers; your agent does the reasoning. This keeps a clean, auditable trust
  boundary — retrieved text is *data*, not instructions.
- **Single-user, alpha.** One bearer token, one user. Treat it accordingly.

## Troubleshooting

- **401/403 from `/mcp`** — check the `Authorization: Bearer` header and that the
  token has `read` scope (and `write` if you enabled mutating tools).
- **Agent can't write pages** — mutating tools are off by default; set
  `REFINDERY_MCP__ENABLE_MUTATING_TOOLS=true` and use a `write`-scoped token.
- **A note isn't updating** — you probably posted it under a new URL; reuse the
  same stable URL so revisit dedup applies.
- **Empty search results** — confirm ingestion finished (`page_status`); indexing
  is asynchronous and returns `202` immediately.

---

*This guide currently lives under `proposals/` because it references planned
features. Once Steps 4a/4b land, the "Available now" portion can graduate into
`docs/guides/` and be wired into the docs-site nav.*
