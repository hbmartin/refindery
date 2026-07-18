# Ideas Refindery could adopt from COG-second-brain

Retrieval-adjacent ideas Refindery can borrow from
[COG](https://github.com/huytieu/COG-second-brain) *without* breaking its
"retrieval, not Q&A" principle. Ranked by **value × fit with the existing
architecture**. See [00-overview.md](00-overview.md) for framing and
[02-integration.md](02-integration.md) for interop.

---

## A1. A "revisit / freshness sweep" — highest fit

**COG source.** The `memory-hygiene` skill runs a "trust sweep of persistent
memory": it re-verifies stored facts against the live environment and stamps
`last_verified` + confidence. COG's whole ethos is "verification-first —
sources required, 7-day freshness, confidence levels on all analysis."

**Refindery status.** Refindery has **watches that discover *new* URLs**
(`application/services/watch_service.py`) but **nothing re-verifies pages
already ingested**. The only scheduling that touches existing content is model
backfill.

**Proposal.** Add a periodic **revisit sweep** — a new `JobKind`
(e.g. `REVISIT_PAGE`) that re-fetches the canonical URLs of indexed pages on a
cadence, detects drift, and repairs the corpus:

- Re-fetch → compare content hash → on change, re-index; on `404`/dead, move
  `Page.status` to `DEAD` / tombstone.
- Optionally stamp `last_verified` on the page row.

**Why it fits.** Most of the backbone already exists:

- Lease-based, idempotent job infra (`adapters/queue/huey_queue.py`,
  `domain/job_keys.py`, the `jobs` ledger) — the same pattern watches use.
- Refindery **already records a "content-hash-differs" flag on revisit**
  (`application/services/ingest.py`, `domain/content_hash.py`), so drift
  detection is half-built.
- `Page.status` already has a `DEAD` state.

**Value.** Kills dead links, re-indexes updated articles, and gives the corpus
a recency/trust signal — the mechanical backbone of a verification-first system,
and the natural counterpart to COG's `memory-hygiene`.

---

## A2. Entity dossiers — People-CRM-style progressive profiles

**COG source.** COG's People CRM builds progressive profiles that auto-escalate
through tiers by mention frequency (stub → moderate → full; e.g. 1 mention → 3+
→ 8+ or a direct meeting), each observation source-cited.

**Refindery status.** Refindery already extracts and canonicalizes entities
(`domain/entities.py`, `application/services/canonicalization.py`,
`application/services/entity_ingest.py`) and counts per-page mentions with IDF —
but exposes them **only as a search filter and a similarity mediation**, never
as a first-class object. It is one step away from COG's CRM.

**Proposal.** Add an **entity profile / dossier**
(`GET /v1/entities/{id}/profile` + a matching MCP tool) aggregating:

- every passage mentioning the entity, with provenance;
- mention count, first/last-seen dates;
- top co-occurring entities (see [A3](#a3-lightweight-entity-to-entity-graph-co-occurrence-edges));
- representative chunks and cluster membership;
- an evidence **tier** derived from mention count, mirroring COG's CRM tiers.

**Why it fits.** The data (`entities`, `entity_aliases`, `entity_mentions`
tables; per-page IDF counts) already exists — this is an aggregation view plus
one endpoint, not new extraction.

**Value.** Turns NER from a filter into a knowledge object and delivers the
"Resurface" job-to-be-done for people / orgs / concepts, not just topics.

---

## A3. Lightweight entity-to-entity graph (co-occurrence edges)

**COG source.** COG "builds frameworks from scattered notes" and auto-maintains
cross-references between entities.

**Refindery status.** Confirmed gap: entities are canonicalized but **isolated
nodes** — there is no entity-to-entity edge model, relation extraction, or graph
traversal.

**Proposal.** Compute **co-occurrence edges** — entities that share a
chunk/page, weighted and IDF-dampened. No relation-extraction model needed; it
reuses the existing `entity_mentions` rows (which already carry char offsets, so
same-chunk co-occurrence is cheap to compute).

**Value.** Gives the calling agent a traversable "what's connected to X" graph
and composes directly with the [A2](#a2-entity-dossiers--people-crm-style-progressive-profiles)
dossier (which surfaces "top related entities"). A modest, bounded step toward a
knowledge graph that stays inside Refindery's corpus-internal, no-external-KB
stance.

---

## A4. A proactive "Resurface" digest

**COG source.** COG ships `weekly-checkin` (cross-domain pattern analysis) and
`daily-brief` (verified news intelligence) as *scheduled outputs*, not queries.

**Refindery status.** Refindery lists "Resurface" as a core job-to-be-done but
only serves it *reactively* — cluster listing (`/v1/clusters`) and similarity
(`/v1/pages/{id}/similar`). The user has to ask.

**Proposal.** A periodic **resurface digest**: top clusters by recent activity,
newly-emerged clusters, and trending entities over a window. Expose as an
endpoint / MCP tool the agent can call, or as a generated markdown export
(see [02-integration.md#b3](02-integration.md)).

**Why it fits.** The raw material exists: **cluster lineage events**
(created/split/merged/dissolved — `domain/clustering.py`, `cluster_lineage`
table) and 2D projection points already track how themes evolve over time.

**Value.** Makes "Resurface" a product, not a query — the proactive
"what have you been reading about lately" surface.

---

## A5. Confidence stamps on *derived* artifacts

**COG source.** COG stamps a confidence level on all analysis and requires
sources.

**Refindery status.** Refindery produces several **derived claims** that are
currently unqualified:

- **LLM cluster labels** (`adapters/llm/openai_compat.py`);
- **entity-canonicalization merges** (edit-distance / embedding matches in
  `application/services/canonicalization.py`);
- **cluster assignments** (HDBSCAN membership).

**Proposal.** Attach `{confidence, method, computed_at}` to these artifacts and
surface them in the API. Low-confidence entity merges become natural candidates
for review (the undo path already exists:
`POST /v1/entities/merges/{id}/undo`).

**Value.** Lets a downstream COG agent cite Refindery's derived facts honestly
and prioritize re-verification — the data-quality complement to
[A1](#a1-a-revisit--freshness-sweep--highest-fit).

---

## A6. Companion skills / MCP prompts

**COG source.** COG's entire value is *packaged agentic skills* (21 of them:
`braindump`, `knowledge-consolidation`, `auto-research`, `weekly-checkin`, …).

**Refindery status.** Refindery ships **no** client-side skills. It delegates
synthesis to the caller but leaves the caller to invent the orchestration, so
the "Synthesize" job is a DIY exercise.

**Proposal.** Ship a small set of **client-side Claude Code skills / MCP
prompts** that orchestrate Refindery's *own* tools, e.g.:

- *"What have I learned about X"* — `search` → `list_clusters` → entity dossier
  → synthesize (in the client);
- *"Resurface this week"* — drive the [A4](#a4-a-proactive-resurface-digest)
  digest;
- *"Entity dossier for X"* — drive [A2](#a2-entity-dossiers--people-crm-style-progressive-profiles).

**Why it respects the core principle.** Generation lives in the *client*, never
in a Refindery query endpoint — so "no generation on the query path" holds. The
"Synthesize" job becomes real out of the box. This is the softest, lowest-risk
bridge to COG and pairs with [02-integration.md#b6](02-integration.md).

---

## A7. Domain classification at ingest time

**COG source.** COG's `braindump` auto-classifies raw input into PARA domains
(`02-personal`, `03-professional`, `04-projects`, `05-knowledge`) on capture.

**Refindery status.** Refindery clusters only *post-hoc* (a full refit) and has
no fast per-page topical label at ingest time.

**Proposal.** A lightweight zero-shot classifier (or a reuse of the existing
entity/keyword signal) that tags each page into stable, user-defined topics at
ingest. Improves filtering, resurfacing, and provides a clean mapping onto COG's
domain folders for [02-integration.md#b2](02-integration.md).

**Value.** Cheap per-page structure that complements — rather than replaces —
the expensive periodic clustering.

---

## Fit summary

| Idea                                   | Reuses existing machinery                              | New surface           | Priority |
| -------------------------------------- | ----------------------------------------------------- | --------------------- | -------- |
| A1 Revisit / freshness sweep           | Job/lease infra, content-hash-differs flag, `DEAD`    | 1 `JobKind` + cadence | **High** |
| A2 Entity dossiers                     | `entities`/`entity_mentions`, IDF mention counts      | 1 endpoint + MCP tool | **High** |
| A3 Co-occurrence graph                 | `entity_mentions` (char offsets)                      | edge table + view     | Medium   |
| A4 Resurface digest                    | cluster lineage, projection points                    | endpoint / export     | Medium   |
| A5 Confidence stamps                   | cluster labels, canonicalization, undo path           | schema fields         | Medium   |
| A6 Companion skills                    | existing MCP tools                                     | client skills only    | Medium   |
| A7 Ingest-time classification          | entity/keyword signal                                  | ingest step           | Low      |
