-- A company remains in the library before, during and after its research.
CREATE TABLE IF NOT EXISTS research_library_entries (
    id TEXT PRIMARY KEY,
    namespace TEXT NOT NULL CHECK(namespace IN ('real','demo','simulation')),
    ticker TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(namespace,ticker)
);
CREATE TABLE IF NOT EXISTS research_library_mentions (
    entry_id TEXT NOT NULL REFERENCES research_library_entries(id),
    origin TEXT NOT NULL,
    origin_ref TEXT NOT NULL,
    run_id TEXT REFERENCES runs(id),
    workflow_id TEXT REFERENCES research_workflow_runs(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(entry_id,origin,origin_ref)
);
CREATE INDEX IF NOT EXISTS research_library_recent ON research_library_entries(namespace,updated_at DESC);
CREATE INDEX IF NOT EXISTS research_library_run ON research_library_mentions(run_id);
