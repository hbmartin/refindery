# Implementation Roadmap — COG-inspired features

Engineering roadmap for the ideas in
[01-adopt-cog-ideas.md](01-adopt-cog-ideas.md) (Part A) and the build-heavy
integration work in [02-integration.md](02-integration.md) (Part B). Written for
a Refindery maintainer. Effort is rough: **S** ≈ a day, **M** ≈ a few days,
**L** ≈ a week+.

> This is a proposal, not an approved plan. Nothing here is built yet.

## Invariants every phase must preserve

These come from the codebase and `CLAUDE.md`; violate them and you break the
system's contracts:

1. **No generation on the query path.** Synthesis stays in the client. None of
   these features add an answer-generation route.
2. **Job idempotency keys are frozen.** `domain/job_keys.py` formats are
   persisted dedupe keys locked by golden tests (`tests/unit/test_job_keys.py`).
   A new `JobKind` gets a **new** builder + a new golden test; existing formats
   never change.
3. **One row per canonical URL, never versioned.** `Page` is immutable-identity.
   Authored notes (Part B) are a *different* content path, not a `Page` variant.
4. **Single-writer job execution.** The lease watchdog is observe-only; never
   re-enqueue while the process lives (`adapters/queue/huey_queue.py`).
5. **Corpus-internal entities, no external KB.** Entity work stays
   surface-form/embedding based (`application/services/canonicalization.py`).
6. **Every change runs the full gate:** `ruff format` + `ruff check` (select
   ALL) + `pytest` + `ty check` + `pyrefly check` + `lizard` (CCN ≤ 15 on `src`).
   Adapters take primitives, not `Settings` objects.

New DB tables/columns are SQLite migrations under
`adapters/metadata/migrations/` (next number is **0009**), and the metadata port
`application/ports/metadata_store.py` must stay dialect-neutral (Postgres is a
stated v2 drop-in).

---

## Phase 0 — Enablers (no engine change)

Cheap, unblocks everything else. Mostly docs/config.

| Item | Ref | Effort | What |
| ---- | --- | ------ | ---- |
| MCP wiring guide | B1 | **S** | Document connecting an external agent (COG or any) to `/mcp`. See [integration-guide.md](integration-guide.md). Zero engine code. |
| Citation format | B5 | **S** | Document the stable citation shape `{canonical_url, chunk_id, char_start, char_end, score}`; confirm `search` results already expose deterministic `chunk_id` (`uuid5(page_id:content_hash:ordinal)`) and char offsets. Add fields to the search response schema if any are missing. |

**Acceptance:** an agent configured per the guide can `search` and cite a passage
that round-trips back to the exact chunk.

---

## Phase 1 — Freshness & trust

The "verification-first" backbone. Highest fit; reuses the job/lease infra.

### 1a. Revisit / freshness sweep — A1 · **M**

**Goal.** Periodically re-fetch indexed pages, detect drift, repair the corpus.

**Approach.**
- Add `JobKind.REVISIT_PAGE = "revisit_page"` (`domain/models.py:53`) and a
  `revisit_page_key(*, page_id, run_at)` builder in `job_keys.py` **+ a golden
  test** (time-varying key so each due sweep is distinct work, mirroring
  `poll_watch_key`).
- Add a periodic tick (prod-only, like `watch_poll_tick`) that selects pages
  due for revisit (by `last_verified`/`fetched_at` age + a `RevisitSettings`
  cadence) capped at `max_due_per_tick`, and enqueues one `REVISIT_PAGE` per
  page. Advance the per-page "next revisit" at **enqueue** time so a failing
  fetch can't freeze the schedule (the watch invariant).
- Handler re-fetches via the existing `RoutingFetcher`, computes
  `content_hash` (`domain/content_hash.py`), and:
  - **unchanged** → stamp `last_verified`;
  - **changed** → re-index (re-chunk/embed/extract) and stamp;
  - **gone (404/dead)** → transition `Page.status` to `DEAD`
    (`PageStatus` already has it).

**Reuse.** Ingest already computes drift on revisit (`ingest.py`:
`record_revisit`, `content_hash_differs`) — factor the hash-compare into a
shared helper. Lease/idempotency/backoff come free from the queue.

**Schema.** Migration 0009: add `pages.last_verified TIMESTAMP NULL` (+ index
for the due-selection query).

**Config.** New `RevisitSettings` (BaseModel) — `enabled`, `min_age_hours`,
`max_due_per_tick`, `interval_hours` — nested in root settings like
`WatchSettings`.

**Metrics.** `revisit_pages_total{outcome=unchanged|changed|dead|error}`.

**Tests.** Deterministic queue tests (skip `queue.start()`); a fake fetcher
returning same/changed/404; assert status transitions + `last_verified` stamps;
golden test for the new key.

**Acceptance.** A stale page whose source changed is re-indexed; a dead URL is
tombstoned; the schedule advances even when a fetch throws.

### 1b. Confidence stamps on derived artifacts — A5 · **S–M**

**Goal.** Qualify derived claims so downstream agents can trust/prioritize them.

**Approach.** Add `{confidence, method, computed_at}` to:
- **cluster labels** (`adapters/llm/openai_compat.py` label path +
  `clusters` persistence): LLM-labeled vs keyword-fallback → method + a
  confidence proxy;
- **entity merges** (`canonicalization.py`): the match score already exists
  (exact/edit-distance/embedding) — persist it as merge confidence;
- surface both in `/v1/clusters` and `/v1/entities/*` responses.

**Schema.** Migration 0009/0010: nullable columns on `clusters` and the
canonicalization/merge records.

**Acceptance.** Low-confidence entity merges are queryable (feeds a review
surface / the existing undo path `/v1/entities/merges/{id}/undo`).

---

## Phase 2 — Entity knowledge layer

Turns NER from a filter into a first-class knowledge object.

### 2a. Entity dossiers — A2 · **M**

**Goal.** A People-CRM-style profile per entity.

**Approach.** Extend the entities surface (`api/routes/entities.py` already has
`GET /v1/entities/{ref}` returning summary + aliases + page_ids). Add
`GET /v1/entities/{ref}/profile` (+ MCP tool) returning:
- mention count, `page_count`, `idf` (already on `EntitySummary`);
- **first/last-seen** dates (min/max over mention → page `fetched_at`);
- **representative chunks** (top mentions by IDF/recency, with provenance);
- **cluster membership** (via existing page→cluster refs);
- **top co-occurring entities** (from [2b](#2b-entity-co-occurrence-graph--a3--m));
- an evidence **tier** derived from mention count (mirrors COG's stub/moderate/
  full CRM tiers — thresholds in config).

**Reuse.** `entity_mentions`, `entity_aliases`, per-page IDF counts, and
`page_ids_for_entity` all exist. This is an aggregation read model + one route +
one MCP tool; no new extraction.

**Acceptance.** `GET /v1/entities/Ada%20Lovelace/profile` returns a dossier;
alias/merged references resolve to the same profile.

### 2b. Entity co-occurrence graph — A3 · **M**

**Goal.** "What's connected to X" without relation extraction.

**Approach.** Compute co-occurrence from `entity_mentions` sharing a chunk (char
offsets already stored). Persist a weighted, IDF-dampened edge table
(migration); refresh incrementally on `EXTRACT_ENTITIES` completion or as a
periodic rebuild. Expose `GET /v1/entities/{ref}/related` (+ MCP) and feed
[2a](#2a-entity-dossiers--a2--m).

**Risk.** Edge count can blow up on dense pages — cap per-page pair generation
and prune below a weight threshold (`log()` what's pruned; no silent caps).

**Acceptance.** Related-entities list is stable across re-runs and IDF-sane
(common entities don't dominate).

---

## Phase 3 — Resurface & classification

### 3a. Resurface digest — A4 · **M**

**Goal.** Proactive "what have you been reading about lately."

**Approach.** A read model over existing **cluster lineage** events
(`cluster_lineage`, `domain/clustering.py`) + projection points: top clusters by
recent member additions, newly-created/split clusters in the window, trending
entities (mention deltas). Expose `GET /v1/resurface?window=7d` (+ MCP), and/or
emit as markdown (feeds [B3](02-integration.md)).

**Acceptance.** Digest reflects the last N days and names emerging themes with
representative pages.

### 3b. Ingest-time classification — A7 · **M** (optional)

**Goal.** Fast per-page topical label at ingest (complements periodic
clustering).

**Approach.** Optional step in `indexing.py` — zero-shot classifier or reuse of
keyword/entity signal — assigning a stable, user-defined domain tag. New optional
extra + port + adapter (keep it behind a flag; adapters take primitives). Maps
onto COG's PARA folders for [B2/B3](02-integration.md).

**Acceptance.** Pages carry a domain tag usable as a search filter; disabled by
default, no gate regressions when off.

---

## Phase 4 — Vault interoperability

The unified-brain payoff. Do after Phase 2 (so there are artifacts worth
exporting).

### 4a. Ingest the COG vault — B2 · **L**

**Goal.** Index authored markdown notes alongside captured pages.

**Design decision (important).** The watch port **cannot** be reused verbatim:
`WatchItem` validates **http(s)-only** URLs (`ports/watch_source.py`
`_absolute_http`), and notes are authored + Git-versioned, not immutable pages.
Two viable shapes:
1. **New authored-content path** — a `Note`-like source with a `file://`/vault
   path identity, its own ingest entrypoint, re-index-on-edit semantics
   (pairs with the [1a](#1a-revisit--freshness-sweep--a1--m) drift machinery).
   *Preferred* — keeps the `Page` invariant intact.
2. **Relax the watch source** to accept a `vault` kind with non-http identity —
   simpler but bends `WatchItem`/`Page` assumptions.

**Recommendation.** Option 1. Add a `WatchKind.VAULT`/source only if you first
lift the http(s) constraint deliberately and give notes a distinct source label
+ canonical scheme.

**Acceptance.** COG `05-knowledge/*.md` is searchable via `/v1/search`, tagged
with a `cog-vault` source, and a note edit re-indexes rather than duplicating.

### 4b. Export to the COG vault — B3 · **M**

**Goal.** Refindery becomes COG's "auto-organize" engine.

**Approach.** `refindery export` CLI subcommand (`cli.py`) and/or an MCP tool
that writes markdown into a target vault dir: entity dossiers
([2a](#2a-entity-dossiers--a2--m)), cluster summaries, resurface digests
([3a](#3a-resurface-digest--a4--m)). Deterministic filenames (stable IDs) so
re-export updates in place rather than duplicating; front-matter carries
provenance + `last_verified`.

**Acceptance.** Running export twice is idempotent; generated notes cite
canonical URLs + chunk IDs ([B5](02-integration.md)).

### 4c. Companion skills + marketplace pack — A6 / B6 · **M**

**Goal.** One-install semantic memory for COG users.

**Approach.** Ship client-side Claude Code skills that orchestrate Refindery's
MCP tools ("what have I learned about X", "resurface this week", "entity
dossier") and a `marketplace-entry.json`-style pack that registers the MCP
server. No engine code — pure client packaging.

**Acceptance.** `npx skills add …` (or equivalent) yields a working
recall+synthesis workflow against a running Refindery.

---

## Sequencing & dependencies

```
Phase 0 (B1 docs, B5 citation)      ── enables everything
        │
Phase 1 ├─ 1a Revisit sweep ────────── enables 4a re-index-on-edit, 4b freshness
        └─ 1b Confidence stamps
        │
Phase 2 ├─ 2b Co-occurrence graph ──── feeds 2a
        └─ 2a Entity dossiers ──────── feeds 3a, 4b
        │
Phase 3 ├─ 3a Resurface digest ─────── feeds 4b
        └─ 3b Classification (opt) ─── feeds 4a/4b domain mapping
        │
Phase 4 ├─ 4a Vault ingest (L)
        ├─ 4b Vault export
        └─ 4c Skill pack / marketplace
```

Recommended first cut: **Phase 0 → 1a → 2a**. Those three deliver the
verification backbone and the entity knowledge object with the least new surface,
each reusing machinery already present, and together they make the
[integration guide](integration-guide.md) substantially more powerful.

## Cross-cutting checklist (every item)

- Migration numbered from **0009**, port stays dialect-neutral.
- New config as a nested `*Settings` BaseModel; adapters receive primitives.
- New job kind → new `job_keys.py` builder **+ golden test**; never touch
  existing formats.
- Prometheus counters/gauges for new outcomes (`current_counters` for `_total`
  families).
- Docs page under `docs/guides/` (wire into `zensical.toml` nav; `--strict`
  build must pass).
- Full gate green: ruff · pytest · ty · pyrefly · lizard (CCN ≤ 15).
