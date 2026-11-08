CREATE TABLE dataset_version_lineage_new (
    version_id TEXT NOT NULL REFERENCES dataset_versions(version_id) ON DELETE RESTRICT,
    input_version_id TEXT NOT NULL REFERENCES dataset_versions(version_id) ON DELETE RESTRICT,
    input_order INTEGER NOT NULL CHECK (input_order >= 0),
    role TEXT NOT NULL CHECK (role IN ('base', 'append', 'join')),
    recipe_id TEXT NOT NULL REFERENCES preparation_recipes(recipe_id) ON DELETE RESTRICT,
    PRIMARY KEY (version_id, input_order),
    UNIQUE (version_id, input_version_id)
);
INSERT INTO dataset_version_lineage_new SELECT * FROM dataset_version_lineage;
DROP TABLE dataset_version_lineage;
ALTER TABLE dataset_version_lineage_new RENAME TO dataset_version_lineage;
CREATE INDEX idx_version_lineage_input ON dataset_version_lineage(input_version_id);
