-- Earlier local development copies applied 011 before the settings revision
-- counter was added. Rebuild from the shared columns so either 011 shape can
-- upgrade while preserving settings and every continuation checkpoint.
-- The counter starts a new process epoch; no monitor fetch runs during startup.
BEGIN IMMEDIATE;
CREATE TABLE intake_monitors_revision_upgrade (
    namespace TEXT NOT NULL CHECK(namespace IN ('real','demo')),
    community TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
    window_days INTEGER NOT NULL DEFAULT 7 CHECK(window_days BETWEEN 1 AND 30),
    revision INTEGER NOT NULL DEFAULT 0,
    state_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(namespace, community)
);
INSERT INTO intake_monitors_revision_upgrade(namespace,community,enabled,window_days,state_json,updated_at)
SELECT namespace,community,enabled,window_days,state_json,updated_at FROM intake_monitors;
DROP TABLE intake_monitors;
ALTER TABLE intake_monitors_revision_upgrade RENAME TO intake_monitors;
COMMIT;
