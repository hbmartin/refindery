-- Fast per-page job lookups (status endpoint, entity-job reconcile).
-- page_id is denormalized from the JSON payload by the adapter at insert
-- time; existing rows are backfilled in adapter code (dialect-specific).

ALTER TABLE jobs ADD COLUMN page_id TEXT;

CREATE INDEX idx_jobs_page ON jobs(page_id, created_at);
