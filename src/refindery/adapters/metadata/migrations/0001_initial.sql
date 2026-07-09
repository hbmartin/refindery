-- Dialect-neutral DDL: types limited to TEXT/INTEGER/REAL/BLOB/BOOLEAN/TIMESTAMP.
-- Timestamps are ISO-8601 UTC TEXT; JSON is TEXT. No SQLite-specific syntax.

CREATE TABLE pages (
  id              TEXT PRIMARY KEY,
  canonical_url   TEXT NOT NULL UNIQUE,
  original_url    TEXT NOT NULL,
  domain          TEXT NOT NULL,
  title           TEXT,
  body_text       TEXT,
  content_hash    TEXT,
  source          TEXT,
  metadata        TEXT,
  first_seen_at   TIMESTAMP NOT NULL,
  last_seen_at    TIMESTAMP NOT NULL,
  visit_count     INTEGER NOT NULL DEFAULT 1,
  indexed_at      TIMESTAMP,
  status          TEXT NOT NULL
);
CREATE INDEX idx_pages_domain ON pages(domain);
CREATE INDEX idx_pages_status ON pages(status);

CREATE TABLE chunks (
  id              TEXT PRIMARY KEY,
  page_id         TEXT NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
  ordinal         INTEGER NOT NULL,
  text            TEXT NOT NULL,
  token_count     INTEGER NOT NULL,
  char_start      INTEGER NOT NULL,
  char_end        INTEGER NOT NULL,
  UNIQUE (page_id, ordinal)
);
CREATE INDEX idx_chunks_page ON chunks(page_id);

CREATE TABLE embedding_models (
  id                TEXT PRIMARY KEY,
  provider          TEXT NOT NULL,
  model_name        TEXT NOT NULL,
  dim               INTEGER NOT NULL,
  max_input_tokens  INTEGER NOT NULL,
  is_active         BOOLEAN NOT NULL,
  status            TEXT NOT NULL,
  created_at        TIMESTAMP NOT NULL
);
CREATE UNIQUE INDEX ux_models_active ON embedding_models(is_active) WHERE is_active;

CREATE TABLE page_vectors (
  page_id     TEXT NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
  model_id    TEXT NOT NULL REFERENCES embedding_models(id),
  vector      BLOB NOT NULL,
  PRIMARY KEY (page_id, model_id)
);

CREATE TABLE jobs (
  id               TEXT PRIMARY KEY,
  kind             TEXT NOT NULL,
  payload          TEXT NOT NULL,
  status           TEXT NOT NULL,
  idempotency_key  TEXT NOT NULL UNIQUE,
  attempts         INTEGER NOT NULL DEFAULT 0,
  max_attempts     INTEGER NOT NULL DEFAULT 5,
  lease_until      TIMESTAMP,
  last_error       TEXT,
  created_at       TIMESTAMP NOT NULL,
  updated_at       TIMESTAMP NOT NULL
);
CREATE INDEX idx_jobs_pending ON jobs(status, created_at);

CREATE TABLE blacklist (
  id          TEXT PRIMARY KEY,
  pattern     TEXT NOT NULL UNIQUE,
  kind        TEXT NOT NULL,
  reason      TEXT,
  created_at  TIMESTAMP NOT NULL
);
