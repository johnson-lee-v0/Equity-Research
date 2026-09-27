-- Several immutable assessments may use one verified earnings package.
CREATE TABLE earnings_assessment_links (
    run_id TEXT PRIMARY KEY REFERENCES runs(id),
    workflow_id TEXT NOT NULL REFERENCES research_workflow_runs(id),
    package_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX earnings_assessment_workflow ON earnings_assessment_links(workflow_id);
