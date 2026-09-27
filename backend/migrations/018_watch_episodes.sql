CREATE TABLE IF NOT EXISTS watch_review_episodes (
    id TEXT PRIMARY KEY,
    namespace TEXT NOT NULL,
    trigger_key TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES runs(id),
    decision_revision INTEGER,
    candidate_key TEXT NOT NULL,
    source_fingerprint TEXT NOT NULL,
    episode INTEGER NOT NULL,
    first_task_id TEXT NOT NULL REFERENCES tasks(id),
    final_task_id TEXT NOT NULL REFERENCES tasks(id),
    condition_json TEXT NOT NULL,
    observation_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(trigger_key, episode)
);
CREATE INDEX IF NOT EXISTS idx_watch_review_episodes_namespace ON watch_review_episodes(namespace, created_at);
