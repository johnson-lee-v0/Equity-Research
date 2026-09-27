-- Road2M durable local schema.  All timestamps are UTC ISO-8601 strings.
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    mandate TEXT NOT NULL,
    authority TEXT NOT NULL,
    office TEXT NOT NULL,
    location_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model_policies (
    id TEXT PRIMARY KEY,
    scope TEXT NOT NULL CHECK(scope IN ('firm', 'agent', 'run')),
    scope_id TEXT,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    reasoning_mode TEXT,
    profile TEXT NOT NULL DEFAULT 'gpt-first',
    allowed_fallback_json TEXT NOT NULL DEFAULT '[]',
    paid_fallback_enabled INTEGER NOT NULL DEFAULT 0 CHECK(paid_fallback_enabled = 0),
    enabled INTEGER NOT NULL DEFAULT 1,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(scope, scope_id)
);

CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    account_type TEXT NOT NULL,
    base_currency TEXT NOT NULL DEFAULT 'USD',
    namespace TEXT NOT NULL DEFAULT 'real' CHECK(namespace IN ('real', 'demo', 'simulation')),
    reconciliation_status TEXT NOT NULL DEFAULT 'unconfirmed' CHECK(reconciliation_status IN ('unconfirmed', 'pending', 'reconciled', 'rejected')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS balance_observations (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    amount TEXT,
    currency TEXT NOT NULL,
    observed_at TEXT,
    published_at TEXT,
    source_id TEXT,
    status TEXT NOT NULL DEFAULT 'unconfirmed' CHECK(status IN ('unconfirmed', 'proposed', 'confirmed', 'contested')),
    unknown_reason TEXT,
    namespace TEXT NOT NULL DEFAULT 'real',
    import_id TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transactions (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    dedupe_key TEXT NOT NULL UNIQUE,
    symbol TEXT,
    side TEXT CHECK(side IN ('buy', 'sell', 'deposit', 'withdrawal', 'fee', 'dividend', 'transfer', 'unknown')),
    quantity TEXT,
    price TEXT,
    amount TEXT,
    currency TEXT NOT NULL DEFAULT 'USD',
    occurred_at TEXT,
    source_id TEXT,
    status TEXT NOT NULL DEFAULT 'unconfirmed' CHECK(status IN ('unconfirmed', 'proposed', 'confirmed', 'contested')),
    namespace TEXT NOT NULL DEFAULT 'real',
    import_id TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS positions (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    symbol TEXT NOT NULL,
    quantity TEXT,
    cost_basis TEXT,
    currency TEXT NOT NULL DEFAULT 'USD',
    observed_at TEXT,
    source_id TEXT,
    status TEXT NOT NULL DEFAULT 'unconfirmed',
    unknown_reason TEXT,
    namespace TEXT NOT NULL DEFAULT 'real',
    created_at TEXT NOT NULL,
    UNIQUE(account_id, symbol, observed_at, namespace)
);

CREATE TABLE IF NOT EXISTS imports (
    id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    namespace TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'staged' CHECK(status IN ('staged', 'applied', 'rejected')),
    error TEXT,
    created_at TEXT NOT NULL,
    applied_at TEXT
);

CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    namespace TEXT NOT NULL CHECK(namespace IN ('real', 'demo', 'simulation')),
    source_type TEXT NOT NULL,
    url TEXT,
    title TEXT,
    publisher TEXT,
    publication_at TEXT,
    retrieval_at TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    locator TEXT,
    original_content TEXT,
    is_untrusted INTEGER NOT NULL DEFAULT 1,
    supersedes_source_id TEXT REFERENCES sources(id),
    created_at TEXT NOT NULL,
    UNIQUE(namespace, content_hash)
);

CREATE TABLE IF NOT EXISTS source_versions (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(id),
    version_no INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    content TEXT,
    retrieved_at TEXT NOT NULL,
    amendment_type TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(source_id, version_no)
);

CREATE TABLE IF NOT EXISTS fact_claims (
    id TEXT PRIMARY KEY,
    namespace TEXT NOT NULL,
    subject TEXT NOT NULL,
    predicate TEXT NOT NULL,
    value_json TEXT,
    unit TEXT,
    currency TEXT,
    period_start TEXT,
    period_end TEXT,
    source_id TEXT REFERENCES sources(id),
    locator TEXT,
    status TEXT NOT NULL DEFAULT 'proposed' CHECK(status IN ('proposed', 'validated', 'contested', 'unresolved')),
    unknown_reason TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS research_items (
    id TEXT PRIMARY KEY,
    namespace TEXT NOT NULL,
    ticker TEXT NOT NULL,
    issuer TEXT,
    status TEXT NOT NULL DEFAULT 'considered' CHECK(status IN ('considered', 'watch', 'active_research', 'rejected', 'held')),
    reason TEXT,
    reopen_trigger TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS research_versions (
    id TEXT PRIMARY KEY,
    research_item_id TEXT NOT NULL REFERENCES research_items(id),
    version_no INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    source_versions_json TEXT NOT NULL DEFAULT '{}',
    provenance TEXT NOT NULL DEFAULT 'real',
    created_at TEXT NOT NULL,
    UNIQUE(research_item_id, version_no)
);

CREATE TABLE IF NOT EXISTS decisions (
    id TEXT PRIMARY KEY,
    namespace TEXT NOT NULL,
    run_id TEXT,
    ticker TEXT,
    decision_type TEXT NOT NULL CHECK(decision_type IN ('pm', 'cio', 'user', 'system')),
    disposition TEXT NOT NULL,
    rationale TEXT NOT NULL,
    dissent_json TEXT NOT NULL DEFAULT '[]',
    constraints_json TEXT NOT NULL DEFAULT '[]',
    invalidation_json TEXT NOT NULL DEFAULT '[]',
    source_ids_json TEXT NOT NULL DEFAULT '[]',
    supersedes_id TEXT REFERENCES decisions(id),
    immutable_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    namespace TEXT NOT NULL CHECK(namespace IN ('real', 'demo', 'simulation')),
    request TEXT NOT NULL,
    ticker TEXT,
    horizon TEXT,
    as_of TEXT,
    account_snapshot_id TEXT,
    status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued', 'running', 'waiting_review', 'waiting_evidence', 'paused', 'blocked', 'failed', 'cancelled', 'completed')),
    pause_requested INTEGER NOT NULL DEFAULT 0,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    mode TEXT NOT NULL DEFAULT 'research',
    model_override_json TEXT NOT NULL DEFAULT '{}',
    input_snapshot_json TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id),
    agent_id TEXT NOT NULL REFERENCES agents(id),
    kind TEXT NOT NULL,
    instruction TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued', 'running', 'waiting_evidence', 'waiting_review', 'blocked', 'failed', 'cancelled', 'completed', 'interrupted')),
    sequence_no INTEGER NOT NULL,
    dependency_json TEXT NOT NULL DEFAULT '[]',
    resolved_config_json TEXT NOT NULL DEFAULT '{}',
    input_snapshot_hash TEXT,
    retry_limit INTEGER NOT NULL DEFAULT 1,
    retry_count INTEGER NOT NULL DEFAULT 0,
    timeout_seconds INTEGER NOT NULL DEFAULT 900,
    current_attempt_id TEXT,
    output_id TEXT,
    blocked_reason TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE(run_id, agent_id, kind)
);

CREATE TABLE IF NOT EXISTS task_dependencies (
    task_id TEXT NOT NULL REFERENCES tasks(id),
    depends_on_task_id TEXT NOT NULL REFERENCES tasks(id),
    PRIMARY KEY(task_id, depends_on_task_id)
);

CREATE TABLE IF NOT EXISTS task_attempts (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    attempt_no INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'running' CHECK(status IN ('running', 'interrupted', 'completed', 'failed', 'blocked', 'cancelled', 'cancelled_late')),
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    reasoning_mode TEXT,
    profile TEXT,
    prompt_version TEXT NOT NULL,
    output_schema_version TEXT NOT NULL,
    source_versions_json TEXT NOT NULL DEFAULT '{}',
    resolved_config_json TEXT NOT NULL DEFAULT '{}',
    usage_json TEXT,
    error TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS outputs (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    attempt_id TEXT NOT NULL REFERENCES task_attempts(id),
    agent_id TEXT NOT NULL REFERENCES agents(id),
    version INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('completed', 'insufficient_evidence', 'needs_review', 'partial', 'cancelled_late')),
    conclusion TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    provenance TEXT NOT NULL CHECK(provenance IN ('real', 'demo', 'simulation')),
    output_hash TEXT NOT NULL,
    supersedes_id TEXT REFERENCES outputs(id),
    created_at TEXT NOT NULL,
    UNIQUE(task_id, version),
    UNIQUE(task_id, output_hash)
);

CREATE TABLE IF NOT EXISTS events (
    sequence_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    namespace TEXT NOT NULL,
    run_id TEXT REFERENCES runs(id),
    task_id TEXT REFERENCES tasks(id),
    attempt_id TEXT REFERENCES task_attempts(id),
    type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    emitted_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS search_records (
    record_id TEXT NOT NULL,
    record_type TEXT NOT NULL,
    namespace TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    provenance TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(record_id, record_type)
);

CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
    record_id UNINDEXED,
    record_type UNINDEXED,
    namespace UNINDEXED,
    title,
    body,
    provenance UNINDEXED,
    tokenize = 'unicode61'
);

CREATE TABLE IF NOT EXISTS simulations (
    id TEXT PRIMARY KEY,
    namespace TEXT NOT NULL DEFAULT 'simulation' CHECK(namespace = 'simulation'),
    name TEXT NOT NULL,
    horizon TEXT NOT NULL,
    participants_json TEXT NOT NULL,
    initial_state_json TEXT NOT NULL,
    constraints_json TEXT NOT NULL,
    shocks_json TEXT NOT NULL,
    rounds INTEGER NOT NULL,
    seed INTEGER NOT NULL,
    provider TEXT NOT NULL DEFAULT 'rules',
    model TEXT,
    status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued', 'running', 'completed', 'failed', 'cancelled')),
    result_json TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS simulation_events (
    id TEXT PRIMARY KEY,
    simulation_id TEXT NOT NULL REFERENCES simulations(id),
    round_no INTEGER NOT NULL,
    participant_id TEXT,
    event_type TEXT NOT NULL,
    action_json TEXT,
    state_json TEXT NOT NULL,
    repair_attempt_json TEXT,
    recorded_response_json TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schedules (
    id TEXT PRIMARY KEY,
    namespace TEXT NOT NULL DEFAULT 'real',
    name TEXT NOT NULL,
    request TEXT NOT NULL,
    interval_seconds INTEGER NOT NULL,
    timezone TEXT NOT NULL,
    catch_up_policy TEXT NOT NULL DEFAULT 'skip' CHECK(catch_up_policy IN ('skip', 'one')),
    enabled INTEGER NOT NULL DEFAULT 0,
    last_execution_at TEXT,
    next_execution_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id TEXT PRIMARY KEY,
    action TEXT NOT NULL,
    subject_type TEXT NOT NULL,
    subject_id TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_sequence ON events(sequence_id);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id, sequence_id);
CREATE INDEX IF NOT EXISTS idx_tasks_run ON tasks(run_id, sequence_no);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status, updated_at);
CREATE INDEX IF NOT EXISTS idx_attempts_task ON task_attempts(task_id, attempt_no);
CREATE INDEX IF NOT EXISTS idx_sources_hash ON sources(namespace, content_hash);
CREATE INDEX IF NOT EXISTS idx_facts_subject ON fact_claims(namespace, subject);
CREATE INDEX IF NOT EXISTS idx_search_namespace ON search_records(namespace, record_type);
CREATE INDEX IF NOT EXISTS idx_sim_events ON simulation_events(simulation_id, round_no, created_at);
