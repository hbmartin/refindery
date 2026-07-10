# Data model

Refindery's relational state lives in the [metadata store](index.md) (SQLite in
WAL mode by default). Vectors live in the [vector store](index.md); the query
log lives in DuckDB. This page describes the relational shape; the corresponding
Python types are in the [domain models reference](../reference/python-api/domain.md).

## Core entities

| Table | Grain | Notes |
| --- | --- | --- |
| `pages` | one row per `canonical_url` | Never versioned. Carries `body_text`, `content_hash`, `visit_count`, and `status`. |
| `chunks` | one row per (page, ordinal) | Canonical, **model-independent** spans — all models embed the same chunking. |
| `embedding_models` | one row per model | Exactly one `is_active`; `status ∈ registered\|backfilling\|ready\|retired`. |
| `page_vectors` | (page, model) | L2-normalized pooled page vector; drives clustering and `similar_to`. |

## Entities

| Table | Grain | Notes |
| --- | --- | --- |
| `entities` | canonical entity | `canonical_form`, `type`, `mention_count`, `page_count`, `idf`. |
| `entity_aliases` | (surface_form, entity) | Normalized surface forms linking to an entity. |
| `entity_mentions` | mention | Per-page/chunk mentions with character offsets. |

Canonicalization is corpus-internal — see [Entities](../guides/entities.md).

## Clusters

| Table | Grain | Notes |
| --- | --- | --- |
| `clusters` | stable cluster | `id` survives re-clustering; `label`, `keywords`, `tombstoned_at`. |
| `cluster_members` | (cluster, page) | HDBSCAN soft-membership `probability`. |
| `cluster_runs` | run | Trigger, algorithm, params, counts, timing. |
| `cluster_lineage` | event | `created\|persisted\|split\|merged\|dissolved` with Jaccard and parents. |

Stable IDs come from a Jaccard + Hungarian matching layer — see
[Clustering](../guides/clustering.md).

## Operational tables

| Table | Purpose |
| --- | --- |
| `blacklist` | URL/domain rules; a match makes ingest return `403`. |
| `jobs` | Durable queue ledger: `kind`, `payload`, `status`, `attempts`, `lease_until`. |

Jobs are lease-based and idempotent (job key = `(page_id, content_hash)` for
indexing) — see [Operations](../operations/index.md#job-lease-model).

!!! note "Dialect-neutral DDL"
    The schema uses no SQLite-specific SQL outside the adapter, and the
    `MetadataStore` port is dialect-neutral, so a Postgres adapter is a v2 drop-in.
