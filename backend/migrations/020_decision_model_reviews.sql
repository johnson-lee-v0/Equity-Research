-- Append-only, namespace-scoped receipts for local Laya participation.
-- Provider payloads never write this table; the orchestrator records the
-- verified runtime result and exact frozen evidence binding.
CREATE TABLE IF NOT EXISTS decision_model_reviews (
    id TEXT PRIMARY KEY,
    namespace TEXT NOT NULL CHECK(namespace IN ('real', 'demo', 'simulation')),
    run_id TEXT NOT NULL REFERENCES runs(id),
    attempt_id TEXT NOT NULL REFERENCES task_attempts(id),
    candidate_key TEXT NOT NULL,
    phase TEXT NOT NULL CHECK(phase IN ('reddit_intake', 'pre_a11', 'post_astra')),
    input_hash TEXT NOT NULL,
    proposal_hash TEXT,
    model_id TEXT NOT NULL,
    model_revision TEXT NOT NULL,
    ordered_choices_json TEXT NOT NULL,
    runtime_version TEXT,
    device TEXT,
    token_counts_json TEXT NOT NULL DEFAULT '{}',
    scores_json TEXT NOT NULL DEFAULT '{}',
    result TEXT,
    fact_bindings_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL CHECK(status IN ('ok', 'unavailable', 'timeout', 'overflow', 'invalid_input', 'failed')),
    failure_reason TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(namespace, run_id, attempt_id, candidate_key, phase, input_hash)
);
CREATE INDEX IF NOT EXISTS idx_decision_model_reviews_case
    ON decision_model_reviews(namespace, run_id, candidate_key, phase, created_at);

CREATE TRIGGER IF NOT EXISTS decision_model_reviews_no_update
BEFORE UPDATE ON decision_model_reviews
BEGIN
    SELECT RAISE(ABORT, 'decision_model_reviews is append-only');
END;
CREATE TRIGGER IF NOT EXISTS decision_model_reviews_no_delete
BEFORE DELETE ON decision_model_reviews
BEGIN
    SELECT RAISE(ABORT, 'decision_model_reviews is append-only');
END;

-- Receipt IDs are frozen beside the other attempt-specific decision inputs.
-- Existing rows receive the empty JSON list and remain readable.
ALTER TABLE attempt_decision_inputs
    ADD COLUMN decision_receipt_ids_json TEXT NOT NULL DEFAULT '[]';
