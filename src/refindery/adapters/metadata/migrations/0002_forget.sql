-- Vector tombstones: pages purged from metadata whose vectors may still
-- linger in the vector store until the purge job confirms deletion.
-- page_id is deliberately NOT a foreign key: the pages row is already gone.

CREATE TABLE vector_tombstones (
  page_id     TEXT PRIMARY KEY,
  status      TEXT NOT NULL,
  last_error  TEXT,
  created_at  TIMESTAMP NOT NULL,
  updated_at  TIMESTAMP NOT NULL
);
CREATE INDEX idx_tombstones_status ON vector_tombstones(status, updated_at);
