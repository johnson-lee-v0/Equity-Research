CREATE TABLE IF NOT EXISTS idea_baselines (
    id TEXT PRIMARY KEY,
    namespace TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES runs(id),
    decision_revision INTEGER NOT NULL,
    candidate_key TEXT NOT NULL,
    ticker TEXT NOT NULL,
    direction TEXT NOT NULL,
    outcome TEXT,
    baseline_json TEXT NOT NULL,
    frozen_at TEXT NOT NULL,
    UNIQUE(run_id, decision_revision, candidate_key)
);
CREATE INDEX IF NOT EXISTS idx_idea_baselines_namespace ON idea_baselines(namespace, frozen_at DESC);

CREATE TABLE IF NOT EXISTS idea_outcomes (
    id TEXT PRIMARY KEY,
    baseline_id TEXT NOT NULL REFERENCES idea_baselines(id),
    idempotency_key TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    UNIQUE(baseline_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS idea_lifecycle_events (
    id TEXT PRIMARY KEY,
    namespace TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES runs(id),
    candidate_key TEXT NOT NULL,
    state TEXT NOT NULL,
    decision_revision INTEGER NOT NULL,
    reason TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(namespace, idempotency_key)
);
