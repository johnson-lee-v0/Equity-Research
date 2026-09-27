-- An archive fallback satisfies a dependency without inventing an A01 output.
CREATE TABLE earnings_discovery_fallbacks (
    task_id TEXT PRIMARY KEY REFERENCES tasks(id),
    run_id TEXT NOT NULL REFERENCES runs(id),
    attempt_id TEXT NOT NULL REFERENCES task_attempts(id),
    workflow_id TEXT NOT NULL REFERENCES research_workflow_runs(id),
    source_versions_json TEXT NOT NULL,
    gaps_json TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX earnings_discovery_fallback_run ON earnings_discovery_fallbacks(run_id);
