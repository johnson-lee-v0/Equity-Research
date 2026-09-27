-- Research-process extensions.  Existing runs, tasks, attempts, outputs and
-- source versions stay append-only; these tables add bounded read models and
-- provenance links around them.

ALTER TABLE runs ADD COLUMN origin TEXT NOT NULL DEFAULT 'user';
ALTER TABLE runs ADD COLUMN origin_ref TEXT;
ALTER TABLE runs ADD COLUMN root_run_id TEXT REFERENCES runs(id);

ALTER TABLE tasks ADD COLUMN assignment_reason TEXT;
ALTER TABLE tasks ADD COLUMN memory_context_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE tasks ADD COLUMN origin TEXT NOT NULL DEFAULT 'user';

ALTER TABLE simulations ADD COLUMN run_id TEXT REFERENCES runs(id);
ALTER TABLE simulations ADD COLUMN candidate_ticker TEXT;
ALTER TABLE simulations ADD COLUMN trigger TEXT;

CREATE TABLE IF NOT EXISTS research_gaps (
    id TEXT PRIMARY KEY,
    namespace TEXT NOT NULL CHECK(namespace IN ('real','demo','simulation')),
    root_run_id TEXT NOT NULL REFERENCES runs(id),
    origin_run_id TEXT NOT NULL REFERENCES runs(id),
    origin_task_id TEXT REFERENCES tasks(id),
    origin_output_id TEXT REFERENCES outputs(id),
    gap_key TEXT NOT NULL,
    normalized_gap TEXT NOT NULL,
    description TEXT NOT NULL,
    assigned_agent_id TEXT NOT NULL REFERENCES agents(id),
    reopen_when TEXT,
    source_fingerprint TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','in_progress','resolved','terminal')),
    repair_round INTEGER NOT NULL DEFAULT 0 CHECK(repair_round BETWEEN 0 AND 2),
    repair_run_id TEXT REFERENCES runs(id),
    terminal_reason TEXT CHECK(terminal_reason IS NULL OR terminal_reason IN ('auth','nonpublic','no_new_evidence','budget','unsupported','already_resolved')),
    resolved_by_output_id TEXT REFERENCES outputs(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(root_run_id, normalized_gap, source_fingerprint)
);

CREATE INDEX IF NOT EXISTS idx_research_gaps_root ON research_gaps(namespace, root_run_id, status, updated_at);
CREATE INDEX IF NOT EXISTS idx_research_gaps_repair ON research_gaps(repair_run_id, status);

CREATE TABLE IF NOT EXISTS research_repairs (
    id TEXT PRIMARY KEY,
    namespace TEXT NOT NULL CHECK(namespace IN ('real','demo')),
    root_run_id TEXT NOT NULL REFERENCES runs(id),
    repair_run_id TEXT NOT NULL UNIQUE REFERENCES runs(id),
    source_fingerprint TEXT NOT NULL,
    round_no INTEGER NOT NULL CHECK(round_no BETWEEN 1 AND 2),
    status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','completed','terminal','cancelled')),
    terminal_reason TEXT CHECK(terminal_reason IS NULL OR terminal_reason IN ('auth','nonpublic','no_new_evidence','budget','unsupported','already_resolved')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS research_repair_gaps (
    repair_id TEXT NOT NULL REFERENCES research_repairs(id),
    gap_id TEXT NOT NULL REFERENCES research_gaps(id),
    PRIMARY KEY(repair_id, gap_id)
);

CREATE TABLE IF NOT EXISTS memory_retrievals (
    id TEXT PRIMARY KEY,
    namespace TEXT NOT NULL CHECK(namespace IN ('real','demo','simulation')),
    run_id TEXT NOT NULL REFERENCES runs(id),
    task_id TEXT REFERENCES tasks(id),
    record_id TEXT NOT NULL,
    record_type TEXT NOT NULL,
    source_versions_json TEXT NOT NULL DEFAULT '[]',
    decision TEXT NOT NULL CHECK(decision IN ('reused','fresh')),
    reuse_reason TEXT NOT NULL DEFAULT '',
    observed_at TEXT,
    retrieved_at TEXT,
    stale INTEGER NOT NULL DEFAULT 0 CHECK(stale IN (0,1)),
    excerpt TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(task_id, record_id, record_type, decision)
);

CREATE INDEX IF NOT EXISTS idx_memory_retrievals_task ON memory_retrievals(namespace, run_id, task_id, created_at);

CREATE TABLE IF NOT EXISTS candidate_simulations (
    id TEXT PRIMARY KEY,
    namespace TEXT NOT NULL CHECK(namespace IN ('real','demo')),
    run_id TEXT NOT NULL REFERENCES runs(id),
    task_id TEXT REFERENCES tasks(id),
    simulation_id TEXT NOT NULL REFERENCES simulations(id),
    candidate_ticker TEXT,
    status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','completed','insufficient_evidence','failed')),
    source_fingerprint TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(run_id, simulation_id, candidate_ticker)
);

CREATE INDEX IF NOT EXISTS idx_candidate_simulations_run ON candidate_simulations(namespace, run_id, candidate_ticker);

CREATE TABLE IF NOT EXISTS intake_items (
    id TEXT PRIMARY KEY,
    namespace TEXT NOT NULL CHECK(namespace IN ('real','demo')),
    origin TEXT NOT NULL CHECK(origin IN ('reddit','other')),
    external_id TEXT NOT NULL,
    community TEXT,
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    url TEXT,
    author TEXT,
    published_at TEXT,
    retrieved_at TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','processing','processed','dismissed','failed','blocked')),
    reason TEXT,
    run_id TEXT REFERENCES runs(id),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(namespace, origin, external_id)
);

CREATE INDEX IF NOT EXISTS idx_intake_queue ON intake_items(namespace, origin, status, next_attempt_at, created_at);

CREATE TABLE IF NOT EXISTS intake_cursors (
    id TEXT PRIMARY KEY,
    namespace TEXT NOT NULL CHECK(namespace IN ('real','demo')),
    origin TEXT NOT NULL,
    community TEXT NOT NULL,
    phase TEXT NOT NULL DEFAULT 'backfill' CHECK(phase IN ('backfill','poll')),
    cursor TEXT,
    oldest_published_at TEXT,
    newest_published_at TEXT,
    last_poll_at TEXT,
    backfill_complete INTEGER NOT NULL DEFAULT 0 CHECK(backfill_complete IN (0,1)),
    updated_at TEXT NOT NULL,
    UNIQUE(namespace, origin, community)
);

CREATE TABLE IF NOT EXISTS intake_dispatches (
    id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL REFERENCES intake_items(id),
    run_id TEXT REFERENCES runs(id),
    status TEXT NOT NULL CHECK(status IN ('queued','dispatched','completed','failed','blocked')),
    cost_units INTEGER NOT NULL DEFAULT 1,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(item_id, run_id)
);

CREATE TABLE IF NOT EXISTS intake_runs (
    id TEXT PRIMARY KEY,
    namespace TEXT NOT NULL CHECK(namespace IN ('real','demo')),
    origin TEXT NOT NULL,
    community TEXT NOT NULL,
    phase TEXT NOT NULL CHECK(phase IN ('backfill','poll')),
    status TEXT NOT NULL CHECK(status IN ('queued','running','completed','failed','blocked')),
    fetched_count INTEGER NOT NULL DEFAULT 0,
    queued_count INTEGER NOT NULL DEFAULT 0,
    dispatched_count INTEGER NOT NULL DEFAULT 0,
    dropped_count INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    UNIQUE(namespace, origin, community, phase, started_at)
);

