CREATE TABLE artifact_trust (
    artifact_id TEXT PRIMARY KEY REFERENCES artifacts(artifact_id) ON DELETE CASCADE,
    trust_state TEXT NOT NULL CHECK (trust_state IN ('quarantined')),
    source TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
