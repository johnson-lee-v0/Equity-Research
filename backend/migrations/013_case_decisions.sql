CREATE TABLE IF NOT EXISTS case_decision_versions (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id),
    namespace TEXT NOT NULL,
    output_id TEXT NOT NULL UNIQUE REFERENCES outputs(id),
    revision INTEGER NOT NULL,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    calculation_context_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE(run_id, revision)
);
CREATE INDEX IF NOT EXISTS idx_case_decisions_current ON case_decision_versions(namespace, run_id, revision DESC);

CREATE TABLE IF NOT EXISTS watch_checks (
    trigger_key TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id),
    ticker TEXT NOT NULL,
    trigger_json TEXT NOT NULL,
    status TEXT NOT NULL,
    result_json TEXT NOT NULL DEFAULT '{}',
    source_fingerprint TEXT,
    followup_task_id TEXT REFERENCES tasks(id),
    checked_at TEXT NOT NULL
);
