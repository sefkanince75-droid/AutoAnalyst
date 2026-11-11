CREATE TABLE holdout_source_locks (
    source_identity TEXT PRIMARY KEY,
    lock_id TEXT NOT NULL REFERENCES holdout_locks(lock_id) ON DELETE RESTRICT
);

CREATE INDEX idx_holdout_source_locks_lock ON holdout_source_locks(lock_id);
