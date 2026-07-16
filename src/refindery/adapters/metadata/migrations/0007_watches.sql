-- Dialect-neutral DDL: types limited to TEXT/INTEGER/REAL/BLOB/BOOLEAN/TIMESTAMP.
-- Timestamps are ISO-8601 UTC TEXT; JSON is TEXT. No SQLite-specific syntax.

CREATE TABLE watches (
  id              TEXT PRIMARY KEY,
  kind            TEXT NOT NULL,
  url             TEXT NOT NULL,
  interval_hours  INTEGER NOT NULL,
  enabled         BOOLEAN NOT NULL DEFAULT 1,
  config          TEXT,
  next_run_at     TIMESTAMP NOT NULL,
  last_run_at     TIMESTAMP,
  last_status     TEXT NOT NULL,
  last_error      TEXT,
  last_item_count INTEGER,
  created_at      TIMESTAMP NOT NULL,
  UNIQUE (kind, url)
);
CREATE INDEX idx_watches_due ON watches(enabled, next_run_at);
