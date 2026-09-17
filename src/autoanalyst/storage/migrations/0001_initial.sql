CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE TABLE projects (
    project_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    format_version TEXT NOT NULL,
    default_language TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 0),
    archived_at TEXT,
    schema_version TEXT NOT NULL
);

CREATE TABLE datasets (
    dataset_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
    name TEXT NOT NULL,
    head_version_id TEXT REFERENCES dataset_versions(version_id) DEFERRABLE INITIALLY DEFERRED,
    head_revision INTEGER NOT NULL CHECK (head_revision >= 0),
    schema_version TEXT NOT NULL
);

CREATE TABLE artifacts (
    artifact_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
    owner_run_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    relative_path TEXT NOT NULL UNIQUE,
    media_type TEXT NOT NULL,
    byte_size INTEGER NOT NULL CHECK (byte_size >= 0),
    sha256 TEXT NOT NULL,
    format_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    schema_version TEXT NOT NULL
);

CREATE TABLE dataset_sources (
    source_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
    original_name TEXT NOT NULL,
    format TEXT NOT NULL,
    raw_artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id) ON DELETE RESTRICT,
    raw_sha256 TEXT NOT NULL,
    byte_size INTEGER NOT NULL CHECK (byte_size >= 0),
    imported_at TEXT NOT NULL,
    schema_version TEXT NOT NULL
);

CREATE TABLE dataset_versions (
    version_id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL REFERENCES datasets(dataset_id) ON DELETE RESTRICT,
    kind TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by_run_id TEXT NOT NULL,
    table_artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id) ON DELETE RESTRICT,
    row_count INTEGER NOT NULL CHECK (row_count >= 0),
    column_count INTEGER NOT NULL CHECK (column_count >= 0),
    schema_hash TEXT NOT NULL,
    content_fingerprint TEXT NOT NULL,
    recipe_id TEXT,
    parse_contract TEXT,
    schema_version TEXT NOT NULL
);

CREATE TABLE version_inputs (
    version_id TEXT NOT NULL REFERENCES dataset_versions(version_id) ON DELETE CASCADE,
    source_id TEXT NOT NULL REFERENCES dataset_sources(source_id) ON DELETE RESTRICT,
    input_order INTEGER NOT NULL CHECK (input_order >= 0),
    PRIMARY KEY (version_id, source_id),
    UNIQUE (version_id, input_order)
);

CREATE TABLE dataset_columns (
    version_id TEXT NOT NULL REFERENCES dataset_versions(version_id) ON DELETE CASCADE,
    column_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    physical_name TEXT NOT NULL,
    physical_type TEXT NOT NULL,
    semantic_hint TEXT,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    is_system INTEGER NOT NULL CHECK (is_system IN (0, 1)),
    PRIMARY KEY (version_id, column_id),
    UNIQUE (version_id, physical_name),
    UNIQUE (version_id, ordinal)
);

CREATE TABLE dataset_head_events (
    event_id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL REFERENCES datasets(dataset_id) ON DELETE RESTRICT,
    from_version_id TEXT REFERENCES dataset_versions(version_id) ON DELETE RESTRICT,
    to_version_id TEXT NOT NULL REFERENCES dataset_versions(version_id) ON DELETE RESTRICT,
    from_revision INTEGER NOT NULL CHECK (from_revision >= 0),
    to_revision INTEGER NOT NULL CHECK (to_revision > from_revision),
    reason TEXT NOT NULL,
    changed_at TEXT NOT NULL
);

CREATE INDEX idx_datasets_project ON datasets(project_id);
CREATE INDEX idx_versions_dataset_created ON dataset_versions(dataset_id, created_at);
CREATE INDEX idx_sources_project ON dataset_sources(project_id);
CREATE INDEX idx_head_events_dataset_revision ON dataset_head_events(dataset_id, to_revision);
