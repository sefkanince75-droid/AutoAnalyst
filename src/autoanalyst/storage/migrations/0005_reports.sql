CREATE TABLE reports (
    report_id TEXT PRIMARY KEY,
    root_result_id TEXT NOT NULL REFERENCES analysis_results(result_id) ON DELETE RESTRICT,
    format TEXT NOT NULL,
    language TEXT NOT NULL,
    template_version TEXT NOT NULL,
    render_options_hash TEXT NOT NULL,
    artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    UNIQUE(root_result_id, format, language, template_version, render_options_hash)
);

CREATE INDEX idx_reports_result ON reports(root_result_id);
