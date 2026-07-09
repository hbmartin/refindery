-- M4: corpus-internal entities (with reversible merges) and stable clusters.

CREATE TABLE entities (
  id               TEXT PRIMARY KEY,
  canonical_form   TEXT NOT NULL,
  type             TEXT NOT NULL,
  mention_count    INTEGER NOT NULL DEFAULT 0,
  page_count       INTEGER NOT NULL DEFAULT 0,
  idf              REAL,
  UNIQUE (canonical_form, type)
);

CREATE TABLE entity_aliases (
  surface_form  TEXT NOT NULL,
  normalized    TEXT NOT NULL,
  block_key     TEXT NOT NULL,
  entity_id     TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  PRIMARY KEY (surface_form, entity_id)
);
CREATE INDEX idx_aliases_normalized ON entity_aliases(normalized);
CREATE INDEX idx_aliases_block ON entity_aliases(block_key);

CREATE TABLE entity_mentions (
  entity_id     TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  page_id       TEXT NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
  chunk_id      TEXT,
  surface_form  TEXT NOT NULL,
  char_start    INTEGER,
  char_end      INTEGER,
  UNIQUE (entity_id, page_id, surface_form, char_start)
);
CREATE INDEX idx_mentions_page ON entity_mentions(page_id);
CREATE INDEX idx_mentions_entity ON entity_mentions(entity_id);

-- Reversible merge log: snapshot written BEFORE mutation.
CREATE TABLE entity_merges (
  id                      TEXT PRIMARY KEY,
  source_entity_snapshot  TEXT NOT NULL,
  target_entity_id        TEXT NOT NULL,
  moved_aliases           TEXT NOT NULL,
  method                  TEXT NOT NULL,
  similarity              REAL,
  merged_at               TIMESTAMP NOT NULL,
  undone_at               TIMESTAMP
);

-- Cache for surface-form vectors (active-embedder canonicalization mode).
CREATE TABLE surface_vectors (
  normalized   TEXT NOT NULL,
  embedder_id  TEXT NOT NULL,
  vector       BLOB NOT NULL,
  PRIMARY KEY (normalized, embedder_id)
);

CREATE TABLE clusters (
  id            TEXT PRIMARY KEY,
  label         TEXT,
  keywords      TEXT,
  size          INTEGER NOT NULL,
  centroid      BLOB,
  model_id      TEXT NOT NULL,
  created_at    TIMESTAMP NOT NULL,
  updated_at    TIMESTAMP NOT NULL,
  tombstoned_at TIMESTAMP
);

CREATE TABLE cluster_members (
  cluster_id  TEXT NOT NULL REFERENCES clusters(id) ON DELETE CASCADE,
  page_id     TEXT NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
  probability REAL,
  PRIMARY KEY (cluster_id, page_id)
);
CREATE INDEX idx_cluster_members_page ON cluster_members(page_id);

CREATE TABLE cluster_runs (
  id             TEXT PRIMARY KEY,
  trigger_kind   TEXT NOT NULL,
  algorithm      TEXT NOT NULL,
  params         TEXT NOT NULL,
  started_at     TIMESTAMP NOT NULL,
  finished_at    TIMESTAMP,
  duration_ms    INTEGER,
  n_pages        INTEGER,
  n_clusters     INTEGER,
  n_noise        INTEGER
);

CREATE TABLE cluster_lineage (
  run_id       TEXT NOT NULL REFERENCES cluster_runs(id),
  event        TEXT NOT NULL,
  cluster_id   TEXT NOT NULL,
  parent_ids   TEXT,
  jaccard      REAL
);
