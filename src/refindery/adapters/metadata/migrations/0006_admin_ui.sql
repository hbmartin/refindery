CREATE TABLE cluster_projection_points (
  run_id       TEXT NOT NULL REFERENCES cluster_runs(id) ON DELETE CASCADE,
  page_id      TEXT NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
  x            REAL NOT NULL,
  y            REAL NOT NULL,
  cluster_id   TEXT REFERENCES clusters(id),
  PRIMARY KEY (run_id, page_id)
);

CREATE TABLE cluster_projection_centroids (
  run_id       TEXT NOT NULL REFERENCES cluster_runs(id) ON DELETE CASCADE,
  cluster_id   TEXT NOT NULL REFERENCES clusters(id),
  x            REAL NOT NULL,
  y            REAL NOT NULL,
  PRIMARY KEY (run_id, cluster_id)
);

CREATE TABLE eval_replay_results (
  job_id       TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
  report       TEXT,
  error        TEXT,
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL
);
