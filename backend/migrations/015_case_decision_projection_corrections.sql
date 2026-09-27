-- A canonical projection may be corrected from the same immutable CIO output.
-- Keep every prior row and constrain idempotency by output plus projection key.
CREATE TABLE case_decision_versions_v15 (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id),
    namespace TEXT NOT NULL,
    output_id TEXT NOT NULL REFERENCES outputs(id),
    projection_key TEXT NOT NULL DEFAULT 'original',
    revision INTEGER NOT NULL,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    calculation_context_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE(output_id, projection_key),
    UNIQUE(run_id, revision)
);

INSERT INTO case_decision_versions_v15(
    id,
    run_id,
    namespace,
    output_id,
    projection_key,
    revision,
    kind,
    payload_json,
    calculation_context_json,
    created_at
)
SELECT
    id,
    run_id,
    namespace,
    output_id,
    'original',
    revision,
    kind,
    payload_json,
    calculation_context_json,
    created_at
FROM case_decision_versions;

DROP TABLE case_decision_versions;
ALTER TABLE case_decision_versions_v15 RENAME TO case_decision_versions;

CREATE INDEX IF NOT EXISTS idx_case_decisions_current ON case_decision_versions(namespace, run_id, revision DESC);
