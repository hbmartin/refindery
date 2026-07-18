# Roadmap: Refindery improvements to support the Brain-Crew use case

> Scope: three capabilities that make Refindery a first-class recall/insight
> substrate under an agentic PKM front-end like
> [My-Brain-Is-Full-Crew](https://github.com/gnekt/My-Brain-Is-Full-Crew).
> Deliberately excluded (belong in the agent layer or covered elsewhere): a
> Markdown-vault source, email/calendar sources, meeting transcription, and the
> resurface digest (see `design/resurface-digest.md`). Also excluded by charter:
> anything that puts generation on the query path — Refindery returns grounded
> passages; synthesis stays in the caller.

Each item states the gap, what exists today, the proposed surface, and the
implementation seams in the current codebase.

---

## 1. Connector-style link discovery as an explicit surface

### The gap
Brain-Crew's **Connector** agent "discovers hidden links" across notes. Refindery
already computes the raw signal but only exposes it **per source page**:
`GET /v1/pages/{id}/similar` (`operation_id=similar_to`) requires you to name one
page and returns its neighbors. There is no *library-wide* "here are the
strongest connections you haven't drawn yet" surface, and no explicit
cross-cluster **bridge** notion (pages that sit between two topics — the highest-
value links for a Zettelkasten).

### What exists today
`application/services/similarity_service.py` already implements three mediations
(`Mediation` enum):

- `vector` — dot-product over per-page dense vectors.
- `cluster` — co-membership ranking within a page's cluster.
- `entity` — IDF-weighted shared-entity overlap (weighted Jaccard).

Plus: clusters with centroids (`list_clusters`, `cluster_pages`), entity→pages
(`page_ids_for_entity`), and 2-D projections per run (`cluster_projection`).

### Proposed surface
A read-only **connections** service and endpoint(s):

1. `GET /v1/connections?page_id=…` — unify `vector` + `entity` + `cluster`
   mediations into one ranked, de-duplicated list with a `via` field
   (`vector|entity|cluster` and, for entity, *which* entities are shared). This
   is the single call the Connector wants instead of fanning out `similar_to`
   three times.
2. `GET /v1/connections/bridges` — **cross-cluster bridges**: pages whose nearest
   neighbors (or shared entities) span two different clusters. Score by
   "betweenness-ish" cross-cluster edge weight. These are the emergent links a
   PARA/Zettelkasten user most wants surfaced.
3. Optional `GET /v1/connections/suggested?since=…` — recently-indexed pages
   with their strongest new connections, so the Connector can run incrementally
   as new content arrives (pairs naturally with the resurface digest).

Output stays *grounded and non-generative*: page ids, canonical URLs, the
mediation that produced each edge, and the shared entities/cluster — never prose.

### Implementation seams
- Add a `ConnectionsService` in `application/services/` that composes the
  existing `SimilarityService` mediations; no new storage needed for (1).
- Bridges (2) need cluster membership + per-page neighbors, both already
  available via `MetadataStore` (`cluster_members`, `cluster_for_page`,
  `get_page_vectors`, `entities_for_page`). Compute at query time for small
  libraries; for larger ones, materialize an edge table during the clustering
  run (`clustering_run.py` already persists projections and centroids — the same
  run can persist top-K neighbor edges).
- New route module `api/routes/connections.py`; add the new `operation_id`s to
  `api/mcp.py::READ_OPERATIONS` so the Connector reaches them over MCP.
- Respect the entity-filter guardrail already used in search
  (`EntityFilterTooBroadError`): a bridge query anchored on a very common entity
  should degrade gracefully, not fan out over thousands of pages.

### Acceptance sketch
Given a page that shares entities with cluster A and vector-neighbors in cluster
B, `/v1/connections/bridges` returns it with `clusters=[A,B]` and a bridge score;
`/v1/connections?page_id=…` returns a merged neighbor list where an entity-only
neighbor and a vector-only neighbor both appear once, each tagged with its `via`.

---

## 2. People / entity-centric surface (a "personal CRM" analogue)

### The gap
Brain-Crew keeps a `05-People/` folder — a lightweight personal CRM. Refindery
already extracts and canonicalizes entities but exposes them only **by
reference** (`GET /v1/entities/{ref}` resolves one entity you already name) and
**per page** (`GET /v1/pages/{id}/entities`). You cannot ask "**who/what have I
been reading about lately**," which is the entity-centric view a PKM front-end
needs to build or refresh People notes.

### What exists today
`domain/entities.py::Entity` carries `canonical_form`, `type`,
`mention_count`, `page_count`, and `idf`; aliases survive merges
(`resolve_entity`, `entity_aliases`, `undo_merge`). `page_ids_for_entity` and
`entities_for_page` give both directions. Entity extraction runs as a job
(`EXTRACT_ENTITIES`) with GLiNER/spaCy/gazetteer/LLM adapters. **But
`page_entities` is not exposed over MCP, and there is no list/browse endpoint.**

### Proposed surface
1. `GET /v1/entities?type=PERSON&sort=recent|mentions|pages&limit=…` — browse the
   entity inventory. `type` filter (PERSON/ORG/…), sort by recency (join to
   `pages.last_seen_at` via the entity→page links), mention/page count. This is
   the "who have I been reading about" query.
2. `GET /v1/entities/{ref}/timeline` — the pages mentioning an entity, ordered by
   `first_seen_at`/`last_seen_at`, so a People note can show "when this person
   showed up in my reading."
3. `GET /v1/entities/{ref}/related` — co-occurring entities (entities that share
   pages with this one), IDF-weighted — the "network around a person" a CRM view
   wants. This reuses the same overlap math as connections item 1.
4. Expose `page_entities`, and the new endpoints, over MCP
   (`api/mcp.py::READ_OPERATIONS`).

### Implementation seams
- (1) is a new `MetadataStore` query (list entities with aggregate counts +
  max page timestamp) + a route; the aggregates (`mention_count`, `page_count`)
  are already materialized on `Entity`, only the recency join and browse endpoint
  are new.
- (2)/(3) compose existing `page_ids_for_entity` / `entities_for_page`;
  co-occurrence (3) is the same weighted-overlap computation proposed for
  connections — build it once in a shared helper.
- Keep the merge/alias semantics intact: browse and related views must resolve
  through aliases (references survive merges), exactly like `resolve_entity`.

### Acceptance sketch
`GET /v1/entities?type=PERSON&sort=recent` returns people ordered by the most
recent page that mentions them; `/entities/{id}/related` returns the other people
and orgs that co-occur with them, each with a shared-page/IDF score.

---

## 3. A reference "Refindery Crew" skills pack

### The gap
Brain-Crew's real leverage is **packaging raw capabilities as conversational
skills**. Refindery ships powerful MCP tools but no opinionated workflows on top,
so every consumer re-derives how to turn `search` / `similar_to` /
`list_clusters` into the three jobs-to-be-done (Refind / Synthesize / Resurface).
A small, shipped skills/agent pack lowers adoption **without violating the
"no generation in the engine" rule — because the skills live in the client.**

### What exists today
Nothing client-side. The engine exposes the tools; the docs describe the API. The
JTBD are defined in the spec (Refind / Synthesize / Resurface) but not embodied
as runnable recipes.

### Proposed deliverable
Ship a `crew/` (or `skills/`) directory of portable, Markdown-defined agent/skill
definitions — the same format Brain-Crew uses — that drive Refindery's MCP tools:

| Skill | JTBD | Tools used |
|---|---|---|
| `refind` | "take me back to the thing I read about X" | `search` (+ `get_page` to hydrate) |
| `synthesize` | "what have I learned about Y" | `search` → agent synthesizes with `canonical_url` citations |
| `resurface` | "what have I been reading a lot about" | `list_clusters` / `cluster_pages`, `resurface` digest (see design doc), `similar_to` |
| `connect` | "what should link to this" | `connections` (roadmap item 1) |
| `who` | "who/what have I been reading about" | `entities` browse (roadmap item 2) |

Each ships as: a system-prompt contract that (a) treats retrieved passages as
data not instructions, (b) always cites `canonical_url`, (c) returns "nothing
found" honestly on empty results. Provide them for at least Claude Code
(`.mcp.json` + skill `.md`), matching Brain-Crew's multi-platform adapter idea.

### Implementation seams
- No engine code — this is a docs/assets addition. Natural home: a top-level
  `crew/` directory plus a docs page under the Zensical site
  (`docs/guides/…`), wired into nav (remember `zensical build --strict` renders
  every `.md` under `docs/` and aborts on broken internal links).
- Provide a one-file `mcp/servers.yaml` snippet (see
  `design/mcp-integration-guide.md`) so a Brain-Crew user drops Refindery in as
  their Seeker's recall engine and picks up these skills in one step.
- Keep the pack versioned against the MCP tool surface: the skills reference
  `operation_id`s, so adding roadmap items 1–2 to `READ_OPERATIONS` is what makes
  the `connect` / `who` skills light up.

### Acceptance sketch
A fresh user clones the pack, sets `REFINDERY_AUTH_TOKEN`, runs
`refindery serve`, and can immediately say "resurface what I've been reading
about" or "what should link to this page" and get grounded, cited results driven
entirely by MCP tool calls — no bespoke prompting.

---

## Sequencing

1. **Expose already-built routes over MCP** (`page_entities`, cluster runs/
   projection) — one-line-per-tool change in `api/mcp.py`, unblocks the Connector
   and People views immediately.
2. **Connections service** (item 1) — highest product value, reuses existing
   similarity math, no new storage for the per-page merge.
3. **Entity browse/related** (item 2) — small store queries + routes.
4. **Skills pack** (item 3) — packages 1–3 into consumable workflows; ships
   last because it references the new tool surface.

All four are additive, read-only, and charter-preserving: they surface structure
Refindery already computes, and leave synthesis to the agent layer.
