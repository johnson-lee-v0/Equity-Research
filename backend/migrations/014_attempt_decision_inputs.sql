CREATE TABLE IF NOT EXISTS attempt_decision_inputs (
    attempt_id TEXT PRIMARY KEY REFERENCES task_attempts(id),
    context_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
