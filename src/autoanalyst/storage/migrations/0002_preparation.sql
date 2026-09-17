CREATE TABLE preparation_recipes (
    recipe_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
    base_version_id TEXT NOT NULL REFERENCES dataset_versions(version_id) ON DELETE RESTRICT,
    recipe_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    schema_version TEXT NOT NULL
);

CREATE TABLE preparation_steps (
    recipe_id TEXT NOT NULL REFERENCES preparation_recipes(recipe_id) ON DELETE CASCADE,
    position INTEGER NOT NULL CHECK (position >= 0),
    step_id TEXT NOT NULL UNIQUE,
    operation TEXT NOT NULL,
    operation_version TEXT NOT NULL,
    parameters_json TEXT NOT NULL,
    affected_column_ids_json TEXT NOT NULL,
    learning_scope TEXT NOT NULL CHECK (learning_scope IN ('none', 'dataset', 'train_only')),
    schema_version TEXT NOT NULL,
    PRIMARY KEY (recipe_id, position)
);

CREATE TABLE preparation_previews (
    preview_id TEXT PRIMARY KEY,
    recipe_id TEXT NOT NULL REFERENCES preparation_recipes(recipe_id) ON DELETE RESTRICT,
    recipe_hash TEXT NOT NULL,
    base_version_id TEXT NOT NULL REFERENCES dataset_versions(version_id) ON DELETE RESTRICT,
    expected_head_revision INTEGER NOT NULL CHECK (expected_head_revision >= 0),
    status TEXT NOT NULL CHECK (status IN ('ready', 'applied')),
    candidate_artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id) ON DELETE RESTRICT,
    before_row_count INTEGER NOT NULL CHECK (before_row_count >= 0),
    after_row_count INTEGER NOT NULL CHECK (after_row_count >= 0),
    before_column_count INTEGER NOT NULL CHECK (before_column_count >= 0),
    after_column_count INTEGER NOT NULL CHECK (after_column_count >= 0),
    step_results_json TEXT NOT NULL,
    warnings_json TEXT NOT NULL,
    sample_changes_json TEXT NOT NULL,
    candidate_columns_json TEXT NOT NULL,
    append_input_version_ids_json TEXT NOT NULL,
    content_fingerprint TEXT NOT NULL,
    schema_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    applied_version_id TEXT REFERENCES dataset_versions(version_id) ON DELETE RESTRICT,
    schema_version TEXT NOT NULL
);

CREATE TABLE dataset_version_lineage (
    version_id TEXT NOT NULL REFERENCES dataset_versions(version_id) ON DELETE CASCADE,
    input_version_id TEXT NOT NULL REFERENCES dataset_versions(version_id) ON DELETE RESTRICT,
    input_order INTEGER NOT NULL CHECK (input_order >= 0),
    role TEXT NOT NULL CHECK (role IN ('base', 'append')),
    recipe_id TEXT NOT NULL REFERENCES preparation_recipes(recipe_id) ON DELETE RESTRICT,
    PRIMARY KEY (version_id, input_order),
    UNIQUE (version_id, input_version_id)
);

CREATE INDEX idx_preparation_recipes_base ON preparation_recipes(base_version_id);
CREATE INDEX idx_preparation_previews_recipe ON preparation_previews(recipe_id, created_at);
CREATE INDEX idx_version_lineage_input ON dataset_version_lineage(input_version_id);
