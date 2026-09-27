-- Immutable source invalidation records.  Historical tasks and outputs keep
-- their original snapshot; a later refresh is a new run linked by these rows.
CREATE TABLE IF NOT EXISTS invalidations (
    id TEXT PRIMARY KEY,
    namespace TEXT NOT NULL CHECK(namespace IN ('real', 'demo', 'simulation')),
    source_id TEXT NOT NULL REFERENCES sources(id),
    supersedes_source_id TEXT REFERENCES sources(id),
    run_id TEXT REFERENCES runs(id),
    task_id TEXT REFERENCES tasks(id),
    output_id TEXT REFERENCES outputs(id),
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(source_id, task_id)
);

CREATE INDEX IF NOT EXISTS idx_invalidations_source ON invalidations(namespace, source_id, created_at);
CREATE INDEX IF NOT EXISTS idx_invalidations_task ON invalidations(task_id, created_at);
