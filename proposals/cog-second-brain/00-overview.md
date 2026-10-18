# Refindery × COG-second-brain — Overview

How Refindery could be improved by ideas from
[COG-second-brain](https://github.com/huytieu/COG-second-brain), and how the two
projects could integrate.

> Status: analysis / proposal only. No code has been changed. This document set
> is the deliverable, not an approved plan.

## The two systems at a glance

|                         | **Refindery**                                                     | **COG-second-brain**                                                       |
| ----------------------- | ----------------------------------------------------------------- | -------------------------------------------------------------------------- |
| Nature                  | DB-backed **retrieval engine**                                    | Markdown + Git **agentic organization layer** ("`.md` files that think")   |
| Content model           | *Captured* web pages — immutable, one row per canonical URL       | *Authored* notes — versioned, editable                                     |
| Core strength           | Hybrid search, embeddings, clustering, NER, provenance, eval      | Synthesis skills, workflows, People CRM, verification-first discipline     |
| Deliberate non-goal     | "No generation on the query path" — synthesis is the caller's job | No database — retrieval is grep over markdown                              |
| Primary interface       | Local HTTP API + **MCP server**                                   | 21 agent skills + 6 worker agents (Claude Code / Cursor / Kiro / Gemini)   |
| Deployment              | Single-user, localhost, alpha                                     | Single-user, local-first, Git-synced                                       |

## Why they are complementary, not competitive

The projects are strongest exactly where the other is weakest:

- **COG has no retrieval substrate.** Its recall is `grep` over markdown files,
  which has no semantic matching and does not scale as a vault grows.
- **Refindery has no synthesis / workflow layer** — by design. It returns
  ranked, grounded passages and leaves generation to the caller.

So the through-line of this proposal is:

> **COG becomes the cognition / workflow layer; Refindery becomes its
> semantic-memory substrate.**

Most of the value comes from *wiring them together* (see
[02-integration.md](02-integration.md)), plus a handful of retrieval-adjacent
ideas Refindery can borrow *without* violating its "retrieval, not Q&A"
principle (see [01-adopt-cog-ideas.md](01-adopt-cog-ideas.md)).

## Document map

1. **[01-adopt-cog-ideas.md](01-adopt-cog-ideas.md)** — Ideas Refindery could
   adopt *from* COG (freshness sweep, entity dossiers, co-occurrence graph,
   resurface digest, confidence stamps, companion skills, ingest-time
   classification).
2. **[02-integration.md](02-integration.md)** — How the two projects could
   interoperate (COG→Refindery over MCP, vault-as-source, export-to-vault,
   shared verification loop, citation format, marketplace skill pack).

## Recommended sequencing

1. **Integration B1** — COG calls Refindery over MCP. Pure config/docs, zero
   Refindery code, immediate value; proves the pairing.
2. **Adopt A1 + A2** — revisit/freshness sweep + entity dossiers. Highest fit
   with existing code; each reuses machinery already present (job/lease infra,
   the content-hash-differs flag, NER + per-page mention counts).
3. **Integration B2 / B3** — vault-in / vault-out. The unified-brain payoff,
   once A2/A4 produce artifacts worth exporting.
4. **Adopt A6 / Integration B6** — companion skill pack. Packages the whole
   thing for COG users.

## Caveats & boundaries to respect

- **Do not turn Refindery into COG.** Adopt the retrieval-adjacent ideas; leave
  authoring, workflow orchestration, and CRM synthesis to COG. Adopt the ideas,
  not the surface area.
- **Keep "no generation on the query path."** Synthesis stays in the client
  (COG / Claude), never inside a Refindery query endpoint. Do not add
  answer-generation routes.
- **The data models differ.** Refindery is "one row per canonical URL, never
  versioned"; COG is file-based and Git-versioned. A COG note is not a captured
  `Page`. Any vault ingestion needs an authored-content path (a distinct source
  with a `file://`/vault-path canonical), not a page masquerade.
- **Philosophical alignment is good.** Both are single-user, local-first,
  privacy-preserving, and free of vendor lock-in, so the pairing is coherent.
