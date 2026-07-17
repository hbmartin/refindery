-- Dialect-neutral DDL: types limited to TEXT/INTEGER/REAL/BLOB/BOOLEAN/TIMESTAMP.
-- Chapter-aware chunking labels each chunk with the podcast chapter (section) it
-- belongs to. Both columns are NULL for ordinary flat chunking; existing rows
-- are unaffected.

ALTER TABLE chunks ADD COLUMN section_title TEXT;
ALTER TABLE chunks ADD COLUMN section_start_s REAL;
