# Implementation roadmap: engine features & agent layer

This is a design-and-sequencing roadmap for two groups of proposed work:

- **Engine-level features (B)** — capabilities Refindery grows itself so it is a
  better substrate for an agent-driven "second brain."
- **Agent layer (C)** — the turnkey packaging (a Claude Code plugin and a
  drop-in `CLAUDE.md` block) that makes the interaction real.

Everything here is *proposed*; nothing below is implemented yet. The intent is to
give each item a concrete design, a task list, a test strategy, and a place in a
dependency-ordered plan.

!!! note "The boundary is preserved on purpose"
    Refindery is [a retrieval engine, not a Q&A system](../index.md): it returns
    grounded passages with provenance and **no generation appears on the query
    path**. Every item below keeps that invariant — Refindery supplies substrate
    (digests, graph edges, candidate pairs, storage), and the calling agent does
    the synthesis. Where an item stores agent-authored text (B4), Refindery
    *stores what it is handed*, exactly as it already does for
    caller-supplied `body_extracted`.

## Conventions every item follows

These are house rules; each section assumes them rather than repeating them.

- **Hexagonal.** New capabilities are an application service behind a port, with
  a swappable adapter. `domain/` and `application/` never import adapter types.
  Wiring goes in `application/container.py::build_container`; test wiring goes in
  `tests/fakes/container.py::build_test_container`.
- **Schemas at the trust boundary.** Request/response bodies are pydantic models
  in `src/refindery/api/schemas.py`; domain types stay plain `@dataclass`.
- **New HTTP route → MCP tool** is opt-in by allow-list: add the route's
  `operation_id` to `READ_OPERATIONS` (or `MUTATING_OPERATIONS`) in
  `src/refindery/api/mcp.py`. Read tools ship on by default; mutating tools are
  gated behind `REFINDERY_MCP__ENABLE_MUTATING_TOOLS`. Put the grounding language
  in the route `description` — that string becomes the tool description.
- **Config** is a nested `BaseModel` on `Settings` in `src/refindery/config.py`
  (env prefix `REFINDERY_`, nested delimiter `__`).
- **Migrations** are sequential SQL files in
  `src/refindery/adapters/metadata/migrations/`; the next free number is
  **`0009`**. Most items below are migration-free (they query existing tables).
- **Job idempotency keys** live in `src/refindery/domain/job_keys.py` and are
  locked by golden tests. Adding a new `JobKind` + key format is fine; never
  change an existing format.
- **Checks that must pass:** `ruff format --check`, `ruff check`, `ty check`,
  `pyrefly check`, `pytest`, `lizard src -C 15` (CCN ≤ 15), and
  `zensical build --clean --strict`. See [Contributing](index.md).

## Sequencing

Ordered by dependency and by risk-adjusted leverage. Effort is a rough
single-developer estimate: **S** ≈ ½–1 day, **M** ≈ 2–4 days, **L** ≈ 1–2 weeks.

| Phase | Items | Theme | Unlocks |
|---|---|---|---|
| **1 — Drop-in backend** | B6, B1a, B2, C2 | Make Refindery usable *today* under a Claude vault | Session continuity + citations + a copy-paste `CLAUDE.md` |
| **2 — Graph & hygiene** | B1b, B5 | Materialize relatedness; expose "resurface" hygiene views | Obsidian graph/wikilinks; weekly lint loop |
| **3 — Write-back & discovery** | B4, B3 | Close the loop and let the corpus grow itself | `/save` notes; topic-driven research watches |
| **4 — Packaging** | C1 | Ship the agent UX | `/refind`, `/synthesize`, `/resurface` slash commands |

```
B6 ─┐
B1a ┼─▶ B2 ─▶ C2 ─────────────▶ C1
B1a ────▶ B1b ─▶ B5
              B4
              B3
```

Rationale: B6, B1a, B2 are small, low-risk, and dependency-free; together with the
C2 docs block they turn Refindery into a drop-in second-brain backend. B1b/B5 build
the graph and hygiene surfaces on top. B4/B3 add write-back and discovery. C1
(the plugin) lands last so it can surface `digest` (B2) and rich citations (B6).

---

# B — Engine-level features

## B1. Materialize a link graph + browse surfaces

**Maps to:** the article's graph view, "cross-links everything," and the
auto-built index of people & ideas.

**Current state.** There is **no page↔page edge table** anywhere (verified across
migrations `0001`–`0008`); relatedness is computed on demand by
`SimilarityService` (`Mediation` ∈ `vector | cluster | entity`), surfaced via
`GET /v1/pages/{id}/similar` and the `suggestions` block on search. Entities are
typed and canonicalized but there is **no "list all entities" endpoint** — you can
only resolve one by ref (`GET /v1/entities/{ref}`). See
[Entities](../guides/entities.md) and [Clustering](../guides/clustering.md).

Split into two shippable pieces.

### B1a — Browse entities (S)

A read endpoint to page through the entity index, the natural companion to the
per-page `GET /v1/pages/{id}/entities`.

- **Store:** add `list_entities(*, sort, limit, offset, type_filter)` to the
  SQLite metadata store, ordering by `page_count`, `mention_count`, or `idf`.
- **Route:** `GET /v1/entities?sort=page_count&type=person&limit=50&offset=0` →
  `EntityListResponse` (reuse the existing entity fields: `id`, `canonical_form`,
  `type`, `mention_count`, `page_count`, `idf`).
- **MCP:** add `list_entities` to `READ_OPERATIONS`.
- **Tests:** store query over a seeded corpus (sort orders, type filter,
  pagination bounds); one ASGITransport route test.
- **No migration.**

### B1b — Graph & backlinks (M)

Expose the relatedness graph as nodes + typed edges — the materialized form of
`SimilarityService` and exactly what an Obsidian graph, a wikilink export (A), or
an in-app view consumes.

- **Service:** a `GraphService` that, for a seed page (or the whole corpus, capped),
  emits edges of kinds `shared_entity` (from `entity_mentions`, IDF-weighted
  Jaccard — reuse `SimilarityService._by_entity`), `same_cluster` (from
  `cluster_members`), and `vector_similar` (cosine, thresholded).
- **Routes:**
    - `GET /v1/pages/{id}/backlinks?edge=shared_entity,same_cluster` → the local
      neighborhood of one page.
    - `GET /v1/graph?seed={id}&depth=1&edge_kinds=…&limit=…` → a bounded node/edge
      set (`GraphResponse { nodes: [{page_id, title, cluster}], edges: [{source,
      target, kind, weight}] }`).
- **MCP:** add `backlinks` (and optionally `graph`) to `READ_OPERATIONS`.
- **Perf note:** start **migration-free** (compute per request; the arms already
  exist). Only if whole-corpus graph latency is a problem, materialize edges into a
  `page_edges` table via a periodic — defer that to a follow-up; do not build it
  speculatively (log any cap you impose so "graph" never silently truncates).
- **Tests:** a fixed small corpus with known shared entities and clusters →
  assert exact edge sets per kind and that weights are ordered; ASGITransport
  tests for both routes.

**Effort:** B1a **S**, B1b **M**. **Risk:** low. Watch `lizard` CCN on the
edge-merging function — keep edge-kind builders as separate helpers.

## B2. Hot cache / digest tool (M)

**Maps to:** the article's `wiki/hot.md` — a recent-context cache refreshed each
session so the next session opens with context, "no recap." This is what makes
"point every project at one brain" (C2) actually work.

**Current state.** The ingredients exist — `pages.first_seen_at`,
`last_seen_at`, `visit_count`, the recency half-life used in search, and clusters
— but there is no "what's new / what am I into lately" surface. An agent opening a
session has nothing to read first except a blind `search`.

**Design.** A read-only aggregation, deliberately compact and cache-friendly:

- **Service:** `DigestService.build(since: datetime | None, limits)` returns
    - `recent_pages` — newest N pages since `since` (title, canonical_url,
      cluster, first_seen_at);
    - `active_clusters` — top clusters by recent membership growth;
    - `trending_entities` — entities whose mention/page counts rose most in the
      window (reuses B1a's store query with a time filter).
- **Route:** `GET /v1/digest?since=2026-07-01T00:00:00Z&pages=20&clusters=8` →
  `DigestResponse`. Default window = last 14 days.
- **MCP:** add `digest` to `READ_OPERATIONS`, with a description that tells the
  agent *"call this first at session start to load recent reading context."*
- **Config:** `DigestSettings` (default window days, default limits).
- **Tests:** seed pages/clusters with controlled timestamps → assert ordering,
  window filtering, and limit clamping; one route test.
- **No migration.**

**Effort:** **M**. **Risk:** low. This is pure read aggregation — it must not
trigger clustering or any write.

## B3. Topic-driven research watch (M)

**Maps to:** the article's `/autoresearch` — autonomous rounds of search → fetch →
file. (The article's "Web Clipper → `.raw/` → ingest all of these" batch flow is
**already** served by `POST /v1/pages/batch`; document that as the "inbox" flow in
[Ingesting pages](../guides/ingest.md) rather than building anything.)

**Current state.** Watch mode polls **only known feeds** —
`WatchKind ∈ {RSS, YOUTUBE, PODCAST}` — via `WatchSource.discover()`
(`ports/watch_source.py`). There is no query-driven discovery anywhere in `src/`.
The `WatchService` tick/handler and canonical-URL dedup are the exact seam to
extend. See [Watches](../guides/watches.md).

**Design.** A new watch *kind* whose discovery runs a saved query against a
pluggable web-search adapter; results flow through the existing
`IngestService.ingest` fan-out and dedup unchanged. **No new job kind** — it
reuses `POLL_WATCH`.

- **Port + adapter:** `WebSearchPort.search(query, limit) -> list[SearchHit]`
  (validate provider responses with pydantic per house rules). Ship one adapter
  behind an optional extra; keep a fake in `tests/fakes/`.
- **Watch kind:** add `WatchKind.SEARCH`; register a `SearchWatchSource` in the
  container `sources` map and in `WatchService.supported_kinds`. The saved query +
  provider live in the existing watch `config` JSON — **no migration** (the
  `watches` table from `0007` already stores arbitrary config). Per the existing
  pattern, `create_watch` 501s for kinds absent from `supported_kinds`, so the
  route degrades gracefully when the extra isn't installed.
- **Config:** `WebSearchSettings` (provider, API key via env, per-poll result cap;
  reuse `WatchSettings.max_items_per_poll`).
- **Boundary:** discovery returns **URLs only**; Refindery fetches + indexes them
  through the normal pipeline. No synthesis, no summarization on this path.
- **Tests:** fake `WebSearchPort` returning fixed hits → assert the poll handler
  fans out to `ingest`, respects the per-poll cap, and dedups repeat hits by
  canonical URL. Deterministic queue tests skip `queue.start()`.

**Effort:** **M**. **Risk:** medium — mostly the external provider surface; the
watch plumbing is a well-trodden extension point.

## B4. `/save` — a first-class "note" source (M)

**Maps to:** the article's `/save`, which turns a conversation into a permanent,
linkable note — "the brain builds itself." This closes the loop so agent output
becomes future retrieval.

**Current state.** Ingest is URL-centric: `IngestService.ingest` canonicalizes a
URL and dedups on the unique `pages.canonical_url`. An agent's synthesized answer
has no home. But the pipeline is content-agnostic once past URL resolution — a
caller-supplied `body_extracted` is stored verbatim and chunked/embedded/
entity-extracted like anything else.

**Design.** Accept a note ingest that mints a synthetic canonical URL.

- **Ingest:** allow `source: "note"` (or a dedicated `POST /v1/notes`) carrying
  `title` + Markdown `body`. Refindery mints `refindery://note/{uuid}` as the
  canonical URL so each save is its own page and the uniqueness/dedup invariant is
  untouched. Everything downstream (chunking, embedding, entity extraction,
  clustering) runs unchanged.
- **Canonicalization:** teach `domain/canonical_url.py` to pass the
  `refindery://` scheme through untouched (no host/tracking-param rules apply).
  Add unit tests pinning that these URLs round-trip byte-identically.
- **MCP:** a `save_note` mutating tool (`MUTATING_OPERATIONS`), so an agent can
  write a synthesized note back with the user's write-scoped token.
- **Retrieval:** notes are ordinary pages — they appear in `search`, `similar_to`,
  clusters, and the graph, and can be filtered by `domain = refindery` (or a
  `source` filter) to separate "my notes" from "pages I read."
- **Boundary:** Refindery still does not generate; it stores handed-in Markdown.
- **Tests:** save a note → assert it is retrievable via `get_page`, searchable,
  and entity-linked; assert the synthetic URL canonicalizes to itself; assert a
  read-scoped token is 403'd.
- **No migration** (`pages` already has `source` and `metadata`).

**Effort:** **M**. **Risk:** low-medium — the main care is the synthetic-URL
canonicalization edge cases.

## B5. Wiki-lint / "resurface" review endpoints (M–L)

**Maps to:** the article's `lint the wiki` — orphans, dead links, stale claims,
and `[!contradiction]` callouts with both sources. Refindery has all the raw
material and exposes none of it. Every endpoint here is a **candidate generator the
agent adjudicates** — Refindery flags, the agent decides.

A `/v1/review/*` family (all read, all added to `READ_OPERATIONS`):

- **Orphans (S).** `GET /v1/review/orphans` — pages that are cluster noise
  (member of no non-noise cluster) **and** share no canonical entities with any
  other page. Pure SQL over `cluster_members` + `entity_mentions`. No migration.
- **Stale / review queue (S).** `GET /v1/review/stale` — old `first_seen_at`, low
  `visit_count`, never revisited; the "you read this once and forgot it" queue.
  Pure SQL. No migration.
- **Contradiction candidates (M).** `GET /v1/review/contradictions` — pairs of
  chunks with high semantic similarity (same topic) across **different** pages,
  returned as `{a: chunk_ref, b: chunk_ref, similarity}`. A direct extension of
  `SimilarityService` (chunk-level cosine, thresholded, cross-page). Refindery
  returns the pairs; the agent renders the `[!contradiction]` callout. No
  migration.
- **Dead links (M).** `GET /v1/review/dead-links` — ingested URLs that no longer
  resolve. This one needs a re-validation mechanism: a periodic that enqueues a
  lightweight `VALIDATE_LINK` job (new `JobKind` + `job_keys.py` format) doing a
  `HEAD`/ranged `GET` and recording the last status. Store `last_checked_at` +
  `http_status` in `pages.metadata` to stay migration-free, **or** add columns in
  migration `0009` if you want them indexable. Reuse the existing fetcher and its
  SSRF pin loop.

**Effort:** orphans/stale **S** each; contradictions **M**; dead-links **M** (the
job + periodic). Ship the two **S** items first. **Risk:** low except dead-links
(new job kind, external I/O — keep it observe-only and rate-limited).

## B6. First-class citations in search results (S)

**Maps to:** the article's "cites the exact pages." This is the smallest, highest
value-per-line item.

**Current state.** Search `ChunkResult` returns `ordinal` + `text` but **not**
`char_start`/`char_end` — those offsets live only on
`GET /v1/pages/{id}/chunks`. So an agent that wants to cite a precise span must
make a second call. The offsets already exist on the `Chunk` domain type
(`char_start`, `char_end`); they are simply dropped from the search projection.

**Design.**

- Add `char_start: int` and `char_end: int` to `ChunkResult` in
  `api/schemas.py`, populated from the chunk already in hand (no store change).
- Add a stable citation anchor — e.g. a `cite` string like
  `{canonical_url}#refindery-chunk-{ordinal}` (and/or `?char={start}-{end}`) — so
  an agent can drop an exact-span link straight into a Markdown note.
- Update the `search` tool description and [Searching](../guides/search.md) /
  [MCP tools reference](../reference/mcp-tools.md) to show the new fields.
- **Tests:** extend existing search response assertions to check offsets match the
  chunk table and that the anchor is well-formed. No migration.

**Effort:** **S**. **Risk:** minimal (additive fields; existing clients ignore
unknown fields).

---

# C — Agent layer

## C1. Refindery Claude Code plugin (M)

**Maps to:** the article's distribution model — a Claude Code plugin + marketplace
entry + slash commands. Refindery ships a raw MCP server but no plugin and no
slash commands. This fits the existing companion-projects strategy (the Chrome
extension and history importer).

**Current state.** No `.claude/` plugin scaffold exists in the repo; C1 is
net-new. The MCP tools it wraps already exist (and Phases 1–3 add `digest`,
`list_entities`, `backlinks`, `save_note`, `review_*`).

**Design.** A small plugin (its own repo, e.g. `refindery-claude-plugin`, or a
`plugin/` dir here) providing curated slash commands mapped to the three
[jobs to be done](../index.md):

| Command | Wraps | Behavior |
|---|---|---|
| `/refind <desc>` | `search` (exact-match bias) | "Take me back to what I read about X," cites provenance |
| `/synthesize <topic>` | `digest` → `search` → agent | Load recent context, gather grounded passages, agent writes the synthesis |
| `/resurface` | `list_clusters` + `review_*` | "What have I been reading about?" + orphans/stale/contradictions |
| `/ingest <url…>` | `add_page` / `batch` | Capture a page or a pile (write scope) |
| `/save` | `save_note` (B4) | Persist the current conversation as a linkable note |
| `/forget <url\|domain>` | `forget` | Purge + blacklist (write scope) |

- Each command is a thin prompt template that calls the MCP tools and formats
  citations from B6 anchors. No new server code.
- Document connection (bearer token, `claude mcp add`) exactly as
  [MCP for agents](../guides/mcp.md) already does; the plugin just packages it.
- **Tests:** the plugin is prompt/config, so validate the manifest and do a
  smoke run against a local server; no `pytest` surface.

**Effort:** **M**. **Risk:** low; depends on Phases 1–3 for the richer commands
(ship a minimal `/refind` + `/ingest` version first).

## C2. Drop-in `CLAUDE.md` block + "one brain, every project" docs (S)

**Maps to:** the article's Part 5 — pointing every project's Claude at the same
knowledge base with a copy-paste `CLAUDE.md` section. Near-zero effort, highest
leverage of anything in C, and it operationalizes B2's `digest`.

**Design.** Add a copy-paste block to [MCP for agents](../guides/mcp.md) and a
short "one brain, every project" section, e.g.:

```markdown
## Refindery knowledge base

Refindery holds everything I've read. When you need prior context that
isn't already in this project:
1. Call the `digest` tool first (recent reading context).
2. If that isn't enough, call `search` with a focused query.
3. Cite sources by their `canonical_url` and chunk anchor.
Do NOT query Refindery for general coding questions.
```

- Pairs with the `digest` tool description (B2) so an agent's *first* move in any
  project is cheap and grounded.
- Also document scoped tokens per project (read-only where write isn't needed) —
  the [Authentication](../configuration/auth.md) guide already supports this.
- **Docs only.** No code, no migration. Ship it the moment `digest` lands (or
  immediately with a `search`-only variant).

**Effort:** **S**. **Risk:** none.

---

## What this roadmap deliberately does *not* do

- **No synthesis on the query path.** `/autoresearch`, `/think`, and note *writing*
  live in the agent (C). Refindery supplies substrate only.
- **No migration of the database of record to plain Markdown.** The portable-vault
  ownership story is served by *projecting* to Markdown (a separate export/bridge
  effort), not by abandoning hybrid retrieval — which is the capability that lets
  the corpus scale past the point where "the agent greps the vault" stops working.
