# Refindery Roadmap — Second-Brain Integration

This roadmap turns refindery into the **memory + retrieval substrate for an
agentic "Second Brain"** — the architecture where Claude (or any model) reads a
persistent, organized context on every task instead of starting from zero.

The framing throughout: refindery is already the *industrial* version of the
"Memory layer" and "retrieval ladder" that Second-Brain builders otherwise
hand-roll in markdown. The retrieval **quality** machinery (hybrid fusion →
rerank → rollup → hydrate, provenance, MCP grounding) is mature and is **not**
being rebuilt. The gaps are: (a) refindery can't yet *store what the agent
learns*, (b) there's no packaged way to *adopt* it as a Second Brain memory, and
(c) a few retrieval/recall ergonomics that agentic memory needs.

## Vision

> A workspace adopts refindery as its **Memory** and a **Connection** by adding
> one MCP server plus one router snippet. From then on, everything the user
> reads *and everything the agent concludes* lands in one indexed, provenance-
> carrying store that the agent searches first, writes back to, and resurfaces
> from — portable across models, private, and local.

## Guiding principles

1. **No generation on the query path.** Synthesis stays with the caller. This
   keeps the memory model-agnostic and portable (you can swap the model without
   losing anything) — the whole point of the Second Brain.
2. **Be the memory, not the router.** The `CLAUDE.md`/router file stays in the
   user's workspace; refindery is the durable, portable store it points to.
3. **Security is structural, not instructional — "keys not prompts."** Read-only
   capability runs autonomously; write capability is gated by an explicit scope
   and (client-side) verification. Refindery already enforces this with
   `read`/`write` token scopes that bind on every transport (HTTP *and* MCP).
4. **Single-user, single-machine, local.** Privacy and portability are features,
   not limitations. No multi-tenant scope creep.
5. **Deterministic before the model.** Retrieval is code, not model turns; the
   agent should be able to score/route from a cheap index before pulling full
   passages.

## Non-goals

- A Q&A / generation endpoint on the query path.
- Becoming the workspace router or the user's file tree.
- Multi-tenant / hosted-service features.
- Screenshot/vision capture *inside* refindery (a recorder pushes extracted or
  OCR'd text through the existing ingest API; refindery stays text-over-pages).

---

## Phases at a glance

| Phase | Theme | Items | Rough effort |
|------|-------|-------|--------------|
| **1** | Adopt — usable as a Second Brain memory today | Skill + router glue · Read-only token minting | S |
| **2** | Learn — bidirectional memory | Write-back `add_note` · `source`/channel search filter | M |
| **3** | Retrieve — index-first ergonomics | Catalogue / index-first tier | M |
| **4** | Recall — time & structure | Time/activity recall · Explicit graph traversal | M–L |
| **5** | Proactive — active cadence | Resurface digest routine | M |

Sequencing rationale: Phase 1 unlocks adoption with **near-zero code** using
today's features. Phase 2 is the keystone — it makes the brain *learn*. Phase 3
makes agent retrieval cheap and routable. Phase 4 adds temporal and relational
recall. Phase 5 makes refindery a proactive participant in the workspace's
scheduled routines. Each phase is independently shippable and valuable.

---

## Phase 1 — Adopt: make refindery a drop-in Second Brain memory

Goal: a user can point a Claude Code workspace at refindery and have it behave as
the memory layer **without building anything**, using existing endpoints.

### 1.1 Claude Code skill + `CLAUDE.md` router snippet

- **Problem.** The article's entire "Context" phase is a `claude.md` router that
  teaches the agent *where memory lives* and *"check the index first, open files
  second."* Today a user must hand-write that wiring, and there's no canonical
  guide for using refindery as agent memory.
- **Deliverable.**
  - A packaged **Claude Code skill** (or a documented router paragraph) that
    instructs the agent to: route "have I read / learned about X" queries to the
    refindery MCP `search` tool; use `list_clusters` + `entities` as the
    always-loaded index; write conclusions back via the write path (Phase 2);
    and honor the untrusted-content grounding contract.
  - A short **"Refindery as your Second Brain memory"** guide under
    `docs/guides/`.
- **Touchpoints.** `api/mcp.py` (tool descriptions are already good grounding
  material), `docs/reference/mcp-tools.md`, new `docs/guides/second-brain.md`
  (wire into Zensical nav).
- **Effort.** S. **Risk.** Low — documentation + a skill file, no runtime code.
- **Acceptance.** From a clean Claude Code workspace, adding the refindery MCP
  server + the shipped router snippet yields correct "have I read about X"
  answers grounded in reading history, with no bespoke setup.

### 1.2 Read-only vs write token minting helper ("keys not prompts")

- **Problem.** The security model the article insists on — *read-only runs
  autonomously, write gets verification* — is already expressible via refindery's
  scoped tokens, but minting a correctly-scoped token pair is manual.
- **Deliverable.** A CLI helper (e.g. `refindery token mint --scope read`) that
  emits a scoped token from `REFINDERY_AUTH_TOKENS`, and docs showing the
  recommended split: a **read-only token for autonomous routines**, a separate
  **write token** used only where the client applies visual verification.
- **Touchpoints.** `api/auth.py` (`TokenRegistry`, `TokenSpec`,
  `REFINDERY_AUTH_TOKENS`), `src/refindery/cli.py`, `docs/configuration/auth.md`.
- **Effort.** S. **Risk.** Low. **Dependencies.** None (leans on existing scopes).
- **Acceptance.** A user can mint a read-only token in one command; that token is
  rejected (403) on any mutating route/MCP tool; the write token succeeds.

---

## Phase 2 — Learn: bidirectional memory (the keystone)

Goal: close **Read → Synthesize → Resurface**. The agent can write learnings
back into the same substrate, so refindery becomes "search over what I've read
**and concluded**," not just what I browsed. This is the single biggest lever for
a brain that "actually knows you."

### 2.1 Write-back — a first-class "note / memory" ingest (`add_note`)

- **Problem.** Ingest today is for *captured web pages* from upstream clients.
  There's no home for *agent-authored* knowledge (conclusions, decisions,
  preferences, summaries). An agent *can* technically push arbitrary
  `body_extracted` + a free-form `source` to `POST /v1/pages` today, but it's
  forced through canonical-URL identity, there's no note-vs-page distinction, and
  no MCP write tool for it.
- **Deliverable.**
  - A **note identity** independent of web URLs (e.g. a synthetic `note://…`
    URI or a nullable-URL page kind) so notes aren't shoehorned into
    canonical-URL dedup.
  - A `page_kind` (or reserved `source="note"`) distinction on `Page` so notes
    are queryable and filterable separately from browsed pages.
  - An MCP `add_note` tool + `POST /v1/notes` (alias over ingest), gated by the
    existing `write` scope and surfaced only when mutating tools are enabled.
  - Notes flow through the **same** pipeline: chunk → embed → index → entity
    extraction → clustering, so a learning is retrievable, entity-linked, and
    resurfaceable alongside reading.
- **Touchpoints.** `application/services/ingest.py` (`IngestRequest`,
  `IngestService.ingest`, body-mode handling), `domain/models.py` (`Page`,
  add `page_kind`), `domain/canonical_url.py` (note-URI handling / bypass),
  `api/routes/pages.py` + new notes route, `api/schemas.py` (`IngestPageRequest`),
  `api/mcp.py` (expose `add_note`), a metadata migration under
  `adapters/metadata/migrations/`.
- **Effort.** M. **Risk.** Medium — schema migration + identity model; must not
  regress web-page dedup. **Dependencies.** Pairs with 2.2 (channel filter).
- **Acceptance.** An agent stores a learning via `add_note`; a later `search`
  returns it with provenance; it appears in clustering/entities; web-page
  canonical dedup is unchanged (golden tests green).

### 2.2 `source` / channel filter on search

- **Problem.** `POST /v1/search` **cannot filter by `source`**. A Second Brain
  wiring multiple channels (browsing, notes, podcasts, later a screen recorder)
  needs "search only my notes" / "exclude podcasts" / "only what I browsed."
  This is also what makes 2.1's notes routable as their own channel.
- **Deliverable.** A `source` (and, optionally, `page_kind`) filter on search.
- **Touchpoints.** Thread `source` into the chunk payload —
  `ChunkPoint`/`StoreFilter` in `application/ports/vector_store.py` currently
  carry only `domain` + `first_seen_at` — plus `SearchFilters`
  (`search_service.py`), `SearchFiltersBody` (`api/schemas.py`), and each vector
  adapter's payload pre-filter (`adapters/vector/*`). May require a vector
  backfill so existing chunks gain the `source` payload field.
- **Effort.** M (touches every vector adapter + a backfill). **Risk.** Medium —
  payload schema change across Qdrant/LanceDB; conformance tests must cover both.
- **Acceptance.** `search` with `source=note` returns only notes; with an
  exclusion returns everything else; both LanceDB and Qdrant behave identically
  in the conformance suite.

---

## Phase 3 — Retrieve: index-first ergonomics

Goal: let the agent (or deterministic code) route from a cheap index before
pulling full passages — the article's retrieval-ladder philosophy, exposed as a
service capability.

### 3.1 Catalogue / index-first tier

- **Problem.** `search` returns full rolled-up passages — ideal for the final
  hop, but agents also want a **cheap first hop**: score/route from a tiny index
  ("where would this answer live?") without hydrating passages. Refindery already
  computes the raw material — cluster keywords (c-TF-IDF), entities, page titles
  — but doesn't expose a compact catalogue.
- **Deliverable.**
  - A **catalogue endpoint** (e.g. `GET /v1/catalogue` or `mode=index` on
    search) returning one-line entries: page/cluster id + title + domain +
    keywords (+ a one-sentence summary if available) — enough for the agent to
    keyword-score locally, then pull only the winning page/chunk.
  - Optionally, a stored **per-page one-line summary** to make catalogue entries
    self-describing (populated at index time; no generation on the *query* path).
- **Touchpoints.** `domain/ctfidf.py` (cluster keywords), `api/routes/clusters.py`
  + `api/routes/entities.py` (index material), new catalogue route + MCP tool,
  `services/indexing.py` (optional summary at index time), `api/mcp.py`.
- **Effort.** M. **Risk.** Low–medium (a summary-at-index-time step adds indexing
  cost if enabled). **Dependencies.** Complements Phase 2 (notes appear in the
  catalogue too).
- **Acceptance.** An agent can retrieve a compact catalogue, choose a target by
  id, then fetch exactly one page/chunk — demonstrably fewer/lighter calls than
  issuing a full `search` for a known-location fact.

---

## Phase 4 — Recall: time and structure

Goal: support "what did I read yesterday" and "what connects to this" — the two
recall modes an agentic brain leans on that refindery only partly serves today.

### 4.1 Time / activity-aware recall

- **Problem.** The article's opening pain is *"the AI has no idea what happened
  yesterday."* Refindery has `after`/`before` (on `Page.first_seen_at`) and
  optional recency decay, but pages are **never versioned** — two visits collapse
  to one row with `visit_count = 2`, so there's no per-visit timeline and no
  "what was I looking at Tuesday afternoon."
- **Deliverable.**
  - A **time-window browse** endpoint ("pages seen in \[range], most-recent
    first") independent of a query.
  - Optionally, a **per-visit event log** so repeated views over time are
    recoverable (enables true "Tuesday afternoon" recall). Decide event-log vs.
    keeping page-level timestamps as an explicit design step.
  - Consider making recency decay a Second-Brain-friendly default.
- **Touchpoints.** `SearchFilters.after/before` + `_page_matches_filter`
  (`search_service.py`), `apply_recency_decay` (`domain/retrieval.py`),
  `record_revisit` / `visit_count` / `last_seen_at` (`ingest.py`,
  `adapters/metadata/sqlite_store.py`), `MetadataStore.list_page_ids_by_domain`;
  a new `page_visits` table + migration if the event-log route is chosen.
- **Effort.** M (browse) → L (per-visit event log). **Risk.** Medium — an event
  log is a real data-model addition; the browse endpoint alone is small.
- **Acceptance.** "Pages seen this week" returns a correct, time-ordered list; if
  the event log ships, repeated visits to the same URL are individually
  recoverable by time window.

### 4.2 Explicit relationship-graph traversal (Graphify-style)

- **Problem.** Refindery already has a *latent* graph — entities, clusters, and
  `similar_to` with VECTOR/CLUSTER/ENTITY mediation — but the agent can only
  vector-search, not *navigate* ("what else connects to this entity/cluster?").
  The article's retrieval ladder wants to "follow one pointer."
- **Deliverable.** A first-class **neighborhood traversal** tool: given a page,
  entity, or cluster, return its connected neighbors (co-occurring entities,
  sibling cluster members, similar pages) with the mediation reason.
- **Touchpoints.** `services/similarity_service.py` (`SimilarPage`, `Mediation`),
  `GET /v1/pages/{id}/similar`, `api/routes/entities.py`,
  `api/routes/clusters.py`, new graph/neighborhood route + MCP tool.
- **Effort.** M. **Risk.** Low (mostly composition of existing signals).
- **Acceptance.** From an entity or cluster, the agent retrieves a ranked,
  reasoned neighborhood and can traverse one hop to related memory without a new
  vector query.

---

## Phase 5 — Proactive: active cadence

Goal: refindery participates in the workspace's scheduled routines instead of
being a passive store — feeding the article's "morning briefing" / weekly-summary
Cadence layer.

### 5.1 Resurface digest routine

- **Problem.** Clustering *is* the "Resurface" job ("what have I been reading a
  lot about"), and refindery already has watch periodics + SSE job events — but
  there's no consumable digest for a scheduled Claude routine.
- **Deliverable.** A **digest** ("top clusters this week / trending entities /
  what you've been reading a lot about") available as a `GET /v1/digest` endpoint
  and/or emitted over the existing SSE/periodic plumbing, so a scheduled routine
  can pull it into a briefing. No generation on refindery's side — it returns
  structured resurface data; the routine's agent writes the prose.
- **Touchpoints.** `services/clustering_run.py`, `domain/ctfidf.py`,
  `api/routes/clusters.py`, watch periodics registration in
  `application/container.py` (`_register_periodics`), SSE `GET /v1/events`,
  new digest route + MCP tool.
- **Effort.** M. **Risk.** Low–medium (defining "trending" windows/thresholds).
- **Acceptance.** A scheduled routine retrieves a weekly digest of top
  clusters/entities with counts and deltas; the payload is stable enough to drive
  a briefing without post-processing.

---

## Dependencies & sequencing summary

```
Phase 1  Adopt        (skill/router, read-only tokens)      ── independent, ship first
Phase 2  Learn        (add_note ─ pairs with ─ source filter) ── keystone
Phase 3  Retrieve     (catalogue tier)                        ── benefits from Phase 2 notes
Phase 4  Recall       (time recall · graph traversal)         ── independent of 2/3
Phase 5  Proactive    (resurface digest)                      ── benefits from all above
```

- **1** is pure enablement; ship it first to make refindery adoptable today.
- **2.1 + 2.2** ship together (notes need a channel to be filtered by).
- **3, 4, 5** are independent of each other and can be parallelized or reordered
  by demand.

## Cross-cutting quality gates

Every item ships with, per `CLAUDE.md`: ruff, pytest, ty, pyrefly, lizard
(CCN ≤ 15 on `src`); type hints throughout; pydantic validation on all new
request/response models; new endpoints mirrored as MCP tools with grounding
descriptions; docs updated (Zensical `--strict` build stays green); and — for
vector-payload changes (2.2) — conformance coverage on **both** LanceDB and
Qdrant, plus any required backfill.

## Open questions

- **Note identity:** synthetic `note://…` URI vs. a nullable-URL `page_kind`?
  (Affects canonical-URL invariants and dedup.)
- **Time model:** is a per-visit event log worth the data-model cost, or is
  page-level `first_seen_at`/`last_seen_at` + a browse endpoint enough for the
  target recall queries?
- **Catalogue summaries:** generate a one-line summary at index time (extra
  indexing cost, better catalogue) or derive entries purely from existing
  titles/keywords/entities (zero added cost)?
- **Digest windows:** what defines "trending" — raw counts, week-over-week
  deltas, or cluster growth — and over what window?
