-- An explicit user action can run one research case while background work stays
-- paused. The authorization never extends to another run or future follow-up.
CREATE TABLE IF NOT EXISTS run_dispatch_authorizations (
    run_id TEXT PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
    authorized_at TEXT NOT NULL,
    revoked_at TEXT,
    revocation_reason TEXT
);
