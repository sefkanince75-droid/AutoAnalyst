CREATE TABLE holdout_locks (
    lock_id TEXT PRIMARY KEY,
    training_run_id TEXT NOT NULL UNIQUE REFERENCES analysis_runs(run_id) ON DELETE RESTRICT,
    selection_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    final_run_id TEXT NOT NULL REFERENCES analysis_runs(run_id) ON DELETE RESTRICT,
    access_started_at TEXT NOT NULL
);

CREATE INDEX idx_holdout_locks_final_run ON holdout_locks(final_run_id);
