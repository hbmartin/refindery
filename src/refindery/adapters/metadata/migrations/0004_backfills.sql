-- M5: resumable per-model backfill state (page-granular cursor).

CREATE TABLE model_backfills (
  model_id         TEXT PRIMARY KEY,
  cursor_page_id   TEXT,
  total_chunks     INTEGER NOT NULL,
  embedded_chunks  INTEGER NOT NULL DEFAULT 0,
  total_tokens     INTEGER NOT NULL,
  started_at       TIMESTAMP NOT NULL,
  updated_at       TIMESTAMP NOT NULL,
  finished_at      TIMESTAMP,
  last_error       TEXT
);
