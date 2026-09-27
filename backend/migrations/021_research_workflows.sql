-- Reusable research recipes share the evidence archive and investment engine.
CREATE TABLE research_workflow_runs (
    id TEXT PRIMARY KEY,
    workflow TEXT NOT NULL,
    version TEXT NOT NULL,
    namespace TEXT NOT NULL CHECK(namespace IN ('real','demo')),
    ticker TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('queued','running','completed','partial','failed','cancelled')),
    idempotency_key TEXT,
    result_json TEXT NOT NULL DEFAULT '{}',
    research_run_id TEXT REFERENCES runs(id),
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(namespace,idempotency_key)
);
CREATE UNIQUE INDEX research_workflow_active ON research_workflow_runs(namespace,workflow,ticker)
    WHERE status IN ('queued','running');
CREATE INDEX research_workflow_history ON research_workflow_runs(namespace,created_at DESC);
CREATE TABLE research_workflow_steps (
    run_id TEXT NOT NULL REFERENCES research_workflow_runs(id) ON DELETE CASCADE,
    agent_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    output_json TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    started_at TEXT,
    finished_at TEXT,
    PRIMARY KEY(run_id,agent_id)
);
CREATE TABLE research_workflow_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES research_workflow_runs(id) ON DELETE CASCADE,
    agent_id TEXT,
    status TEXT NOT NULL,
    detail TEXT,
    created_at TEXT NOT NULL
);
