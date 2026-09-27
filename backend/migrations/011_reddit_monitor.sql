-- Monitor control and continuation state are distinct from post records.
-- Checkpoints advance only after a retained batch has committed.
CREATE TABLE IF NOT EXISTS intake_monitors (
    namespace TEXT NOT NULL CHECK(namespace IN ('real','demo')),
    community TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
    window_days INTEGER NOT NULL DEFAULT 7 CHECK(window_days BETWEEN 1 AND 30),
    revision INTEGER NOT NULL DEFAULT 0,
    state_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(namespace, community)
);
