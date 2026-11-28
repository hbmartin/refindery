# Second-Brain Roadmap

A phased plan for aligning Refindery with the
[awesome-second-brain](https://github.com/aristoapp/awesome-second-brain)
lifecycle framework (Collect → Organize → Evolve → Use → Govern) and
interoperating with the tools it catalogs.

> **Scope guardrail.** Refindery is a *retrieval-and-grounding substrate*, not
> an end-to-end brain. Every item below preserves the two core invariants:
> **no generation on the query path** and **grounded passages with provenance**.
> Generation (summaries, labels) happens only at index/cluster time, never during
> `POST /v1/search`.

## Where we are

| Lifecycle stage | Status | Owning code |
| --- | --- | --- |
| **Collect** | Strong | `application/services/ingest.py`, `watch_service.py`, `ports/watch_source.py`, `adapters/feeds/*`, `adapters/youtube/*`, `adapters/transcription/*` |
| **Organize** | Good | `chapter_chunking.py`, `entity_ingest.py`, `adapters/extractors/*`, `clustering_run.py`, `domain/ctfidf.py` |
| **Evolve** | Weak | `canonicalization.py`, `cluster_triggers.py`, `backfill.py`, `feedback_service.py` (eval-only) |
| **Use** | Excellent | `search_service.py`, `api/mcp.py`, `compare_service.py`, `similarity_service.py` |
| **Govern** | Strong (single-user) | `forget_service.py`, `VectorTombstone`, `api/auth.py`, query log |

The framework's clearest lessons: the cheapest high-differentiation wins are
**activation evidence** and **export/portability**; the deepest gap is **Evolve**.

---

## Phasing at a glance

| Phase | Theme | Initiatives | Rough effort |
| --- | --- | --- | --- |
| **0** | Positioning | P0.1 docs page · P0.2 upstream listing | Hours |
| **1** | Close the loop | P1.1 activation evidence · P1.2 export | Small–Medium |
| **2** | Richer Organize | P2.1 summaries · P2.2 timeline/trends | Medium |
| **3** | Real Evolve | P3.1 consolidation · P3.2 feedback→ranking | Medium–Large |
| **4** | Broader Collect | P4.1 Markdown-vault source · P4.2 read-later/newsletter | Medium (optional) |

Phases are ordered by value-to-effort and by dependency: P1.2 (export) and P1.1
(activation) unlock most ecosystem interop; P2/P3 deepen the lifecycle; P4 is
optional breadth.

---

## Phase 0 — Positioning (do first, near-zero cost)

### P0.1 — "Refindery in the second-brain lifecycle" docs page
- **Goal.** Map the three jobs-to-be-done (Refind / Synthesize / Resurface) onto
  Collect / Organize / Use, and state plainly what Refindery deliberately does
  **not** do (agent-authored memory, end-to-end synthesis).
- **Do.** Add `docs/second-brain.md`; wire it into nav in `zensical.toml`.
  Cross-link from `docs/index.md`.
- **Watch out.** `zensical build --strict` renders *every* `.md` under `docs/`
  and fails on broken internal links or missing nav targets — verify with
  `uv run zensical build --clean --strict` before pushing (the `docs.yml` gate).
- **Done when.** Page builds strict-clean and is reachable from the nav.

### P0.2 — Get Refindery listed upstream
- **Goal.** A PR to `aristoapp/awesome-second-brain` adding Refindery under
  *memory infrastructure / substrates*.
- **Positioning.** "Local, single-user retrieval-and-grounding layer over your
  reading history; MCP-native; no generation on the query path."
- **Done when.** PR opened upstream (acceptance is out of our control).

---

## Phase 1 — Close the retrieval loop

### P1.1 — Activation evidence ⭐
The framework's signature metric: *was retrieved memory loaded, cited, refused,
or written back?* We already capture candidate sets, both arms, final pages,
params, and relevance feedback in the DuckDB query log.

- **Goal.** Let an MCP agent report that a retrieved page was actually *used*
  (cited / relied upon / refused) for a given query, and expose that as an eval
  dimension alongside relevance feedback.
- **Do.**
  - Add an `ActivationRecord` to `application/ports/query_log.py` mirroring
    `FeedbackRecord` (fields: `query_id`, `page_id`, `kind` ∈ {cited, used,
    refused}, `ts`).
  - Extend `FeedbackService` (or a sibling `ActivationService`) with a
    `record_activation(...)` that buffers through the existing `QueryLogSink`.
    Reuse the append-only, unknown-`query_id`-tolerated design already in
    `feedback_service.py`.
  - Add `POST /v1/activations` (write scope) + an MCP mutating op mirroring the
    feedback route; join at eval time in the DuckDB reader, never on the write
    path (preserve the single-writer invariant).
  - Surface an activation rate in the eval report (`eval_service.py`).
- **Invariants.** Append-only; no read joins through the DuckDB writer; retained
  raw query text remains a documented operational risk (`operations/index.md`).
- **Done when.** An agent can POST an activation, and `eval score` reports an
  activation/citation rate per query set.
- **Effort.** Small (extends an existing pattern end-to-end).

### P1.2 — Export / portability ⭐
Portability is a governance requirement in the framework and the bridge to
Obsidian / Logseq / Khoj / Mem0. We have `forget` but no export.

- **Goal.** Full, streaming export of the corpus in a portable format.
- **Do.**
  - `GET /v1/export?format=jsonl|markdown` (read scope), streaming (reuse the
    SSE/streaming transport lessons — do not buffer the whole corpus).
  - **JSONL**: one object per page — `canonical_url`, `title`, `body_text`,
    `entities`, `cluster_id`, `first_seen_at`, `last_seen_at`, `visit_count`,
    `metadata`. A superset suitable for re-import.
  - **Markdown**: one document per page with YAML frontmatter (title, url,
    entities, cluster, timestamps) — directly droppable into an Obsidian/Logseq
    vault.
  - Pydantic-validate the export envelope shapes (trust-boundary output).
- **Done when.** A round-trip (export JSONL → re-ingest) reproduces the corpus,
  and Markdown export opens cleanly in Obsidian.
- **Effort.** Medium. Pairs with P4.1 (vault ingest) for a full round-trip.

---

## Phase 2 — Richer Organize

### P2.1 — Summaries (page + cluster)
The framework distinguishes "structured knowledge" from "a pile of embeddings."
We have entities + embeddings but no summaries.

- **Goal.** Optional, off-query-path summaries per page and per cluster.
- **Do.**
  - Reuse the OpenAI-compatible client pattern (`adapters/extractors/llm.py`,
    `adapters/llm/openai_compat.py`) behind a new `Summarizer` port so it stays
    swappable and optional (feature-flagged like the LLM entity extractor).
  - Generate at index time (page) and at cluster-label time
    (`clustering_run.py`, alongside c-TF-IDF keywords); persist page summaries in
    `Page.metadata`, cluster summaries on `Cluster`.
  - Surface summaries in search results and in the Resurface/cluster views —
    **not** generated during the query.
- **Invariants.** Generation stays at index/cluster time; search remains
  generation-free. Provider calls go through `adapters/resilience/` wrappers.
- **Done when.** Search results and cluster listings carry summaries when the
  summarizer is configured, and behave unchanged when it is not.
- **Effort.** Medium.

### P2.2 — Timeline & trends
"What have I been reading a lot about" is already a stated JTBD, and temporal
memory is a headline capability of graph-memory tools in the catalog. We persist
`first_seen_at` / `last_seen_at` / `visit_count` but expose no time view.

- **Goal.** Read-only temporal surfaces over existing data.
- **Do.**
  - `GET /v1/timeline` — reading volume bucketed over time (domain/cluster
    optional facets).
  - `GET /v1/entities/trends` and/or `GET /v1/clusters/trends` — rising/falling
    topics over a window, from mention counts and page timestamps.
  - Add MCP read ops for both.
- **Done when.** Trend endpoints return ranked rising/falling
  entities/clusters for a window; timeline returns per-bucket counts.
- **Effort.** Medium (pure read over persisted data).

---

## Phase 3 — Real Evolve (the deepest gap)

### P3.1 — Consolidation / near-duplicate linking
Dedup today is **canonical-URL only** (`domain/canonical_url.py`); syndicated or
near-duplicate articles under different URLs stay separate.

- **Goal.** Detect and *link* near-duplicate pages (not necessarily merge) so
  search and Resurface stop double-counting them.
- **Do.**
  - New `JobKind.CONSOLIDATE` (register format in `domain/job_keys.py` — never
    mutate an existing kind's key format; it's locked by golden tests).
  - Candidate generation from `content_hash` proximity + page-vector cosine,
    reusing the clustering/embedding infra; persist a "near-duplicate of" link in
    `Page.metadata`.
  - Optional search-time collapse of linked duplicates in rollup
    (`search_service.py` final slice — respect the "pagination only at the final
    slice" rule).
- **Done when.** Two syndicated copies of an article are linked, and search can
  collapse them behind a flag.
- **Effort.** Medium–Large.

### P3.2 — Feedback → ranking
Relevance feedback currently feeds **offline eval only**. The Evolve promise is
"memory improves as context arrives."

- **Goal.** Let accumulated relevance/activation signal influence ranking.
- **Do.** Start conservative: per-query relevance **pins/demotions** applied at
  the final slice; then evaluate a learned reweighting of fusion/rerank scores
  using logged feedback. Gate every change behind the existing eval harness so
  quality is measured, not assumed.
- **Invariants.** Feedback stays append-only and eval-joined; any ranking use
  reads from a materialized view, not through the DuckDB writer.
- **Done when.** A page marked relevant for a recurring query reliably ranks
  higher, with an eval delta to prove it.
- **Effort.** Medium–Large (mostly evaluation rigor).

---

## Phase 4 — Broader Collect (optional breadth)

Follows the existing `WatchKind` + `ports/watch_source.py` pattern: add a
`WatchKind` member and a `WatchSource` in the container `sources` map; kinds
absent from `WatchService.supported_kinds` 501 at the create route.

### P4.1 — Markdown / Obsidian-vault source
- **Goal.** Ingest your own notes back into retrieval; completes the export
  round-trip with P1.2.
- **Do.** `WatchKind.VAULT` + a `WatchSource.discover()` over a local directory
  of Markdown files (mtime-based change detection). Respect
  `ASYNC240` (no pathlib in async defs).
- **Effort.** Medium.

### P4.2 — Read-later / newsletter source
- **Goal.** Pull Pocket/Instapaper exports or IMAP newsletters into the same
  pipeline.
- **Do.** A new `WatchKind` + source; feed URLs/bodies through `IngestService`
  exactly as RSS does.
- **Effort.** Medium.

---

## Cross-cutting engineering checklist

Apply to every initiative (from `CLAUDE.md`):

- Run `ruff`, `pytest`, `ty`, `pyrefly`, and `lizard` (CCN ≤ 15 on `src`) after
  each change.
- New adapters take **primitives**, not `Settings` objects (see `ChonkieChunker`,
  `PdfSettings` unpacking).
- Validate all trust-boundary I/O (HTTP requests/responses, fetched content) with
  **pydantic**; keep internal domain objects as dataclasses.
- New job kinds: register the idempotency-key format in `domain/job_keys.py`;
  never change an existing kind's format.
- New routes get read scope globally; mutating routes add
  `dependencies=[Depends(require_write)]` and a corresponding entry in the MCP
  op lists (`api/mcp.py`).
- Provider calls route through `adapters/resilience/` (breaker + retry).
- Docs for any new surface build strict-clean under `zensical`.

## Suggested sequencing

1. **P0.1 + P0.2** — positioning, hours, clarifies everything downstream.
2. **P1.1** — activation evidence; highest conceptual fit, extends existing code.
3. **P1.2** — export; unlocks Obsidian/Khoj/Mem0 interop.
4. **P2.1 + P2.2** — summaries and timeline; strengthen Synthesize/Resurface.
5. **P3.1 + P3.2** — consolidation and feedback→ranking; the deeper Evolve work.
6. **P4** — optional Collect breadth, ideally after P1.2 for the vault round-trip.
