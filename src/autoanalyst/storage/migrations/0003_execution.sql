CREATE TABLE analysis_specs (
    spec_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
    input_version_id TEXT NOT NULL REFERENCES dataset_versions(version_id) ON DELETE RESTRICT,
    module_id TEXT NOT NULL,
    spec_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(project_id, spec_hash)
);

CREATE TABLE analysis_runs (
    run_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
    spec_id TEXT REFERENCES analysis_specs(spec_id) ON DELETE RESTRICT,
    parent_run_id TEXT REFERENCES analysis_runs(run_id) ON DELETE RESTRICT,
    kind TEXT NOT NULL,
    request_key TEXT NOT NULL,
    input_fingerprint TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    error_code TEXT,
    result_id TEXT,
    UNIQUE(project_id, request_key)
);

CREATE INDEX idx_analysis_runs_project_status
ON analysis_runs(project_id, status, created_at);

CREATE TABLE run_events (
    run_id TEXT NOT NULL REFERENCES analysis_runs(run_id) ON DELETE RESTRICT,
    sequence INTEGER NOT NULL CHECK(sequence >= 0),
    event_type TEXT NOT NULL,
    stage TEXT,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(run_id, sequence)
);

CREATE TABLE analysis_results (
    result_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE REFERENCES analysis_runs(run_id) ON DELETE RESTRICT,
    module_id TEXT NOT NULL,
    outcome TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
