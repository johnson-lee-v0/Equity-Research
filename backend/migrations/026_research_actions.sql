-- Optional user-requested research passes never replace case decisions.
CREATE TABLE IF NOT EXISTS research_action_receipts (
    task_id TEXT PRIMARY KEY REFERENCES tasks(id),
    run_id TEXT NOT NULL REFERENCES runs(id),
    namespace TEXT NOT NULL,
    attempt_id TEXT NOT NULL REFERENCES task_attempts(id),
    stage TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    source_bindings_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS research_action_receipts_run ON research_action_receipts(run_id, created_at);

CREATE TABLE IF NOT EXISTS research_action_requests (
    idempotency_key TEXT PRIMARY KEY,
    namespace TEXT NOT NULL,
    parent_run_id TEXT NOT NULL REFERENCES runs(id),
    request_hash TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES runs(id),
    created_at TEXT NOT NULL
);
