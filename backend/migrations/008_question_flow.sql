-- Question-flow read model additions.  Existing runs, attempts and outputs
-- remain append-only; these columns and the claim projection table add
-- relationships and operational state without rewriting historical payloads.

ALTER TABLE runs ADD COLUMN parent_run_id TEXT REFERENCES runs(id);
ALTER TABLE runs ADD COLUMN followup_kind TEXT;
ALTER TABLE runs ADD COLUMN research_instruction TEXT;

ALTER TABLE tasks ADD COLUMN dispatch_state TEXT NOT NULL DEFAULT 'queued';
ALTER TABLE tasks ADD COLUMN wait_reason TEXT;
ALTER TABLE tasks ADD COLUMN provider_started_at TEXT;
ALTER TABLE tasks ADD COLUMN terminal_summary TEXT;

ALTER TABLE task_attempts ADD COLUMN provider_started_at TEXT;

-- SQLite applies the column default to historical rows.  Restore the
-- operational state implied by their durable terminal status so old work is
-- never shown as queued after this read-model migration.
UPDATE tasks SET dispatch_state='finished' WHERE status IN ('completed','cancelled','failed','blocked','interrupted');
UPDATE tasks SET terminal_summary='Finished — report saved.' WHERE status='completed' AND (terminal_summary IS NULL OR terminal_summary='');
UPDATE tasks SET terminal_summary='Cancelled before completion.' WHERE status='cancelled' AND (terminal_summary IS NULL OR terminal_summary='');
UPDATE tasks SET terminal_summary='Blocked before completion.' WHERE status='blocked' AND (terminal_summary IS NULL OR terminal_summary='');
UPDATE tasks SET terminal_summary='Failed before completion.' WHERE status='failed' AND (terminal_summary IS NULL OR terminal_summary='');
UPDATE tasks SET terminal_summary='Interrupted; explicit retry is available.' WHERE status='interrupted' AND (terminal_summary IS NULL OR terminal_summary='');

CREATE TABLE IF NOT EXISTS output_claims (
    output_id TEXT NOT NULL REFERENCES outputs(id),
    claim_index INTEGER NOT NULL,
    fact_id TEXT REFERENCES fact_claims(id),
    validation_status TEXT NOT NULL CHECK(validation_status IN ('validated', 'proposed', 'unavailable')),
    validation_reason TEXT,
    validation_origin TEXT NOT NULL CHECK(validation_origin IN ('recorded', 'archived_source_check')),
    excerpt TEXT,
    line_start INTEGER,
    line_end INTEGER,
    PRIMARY KEY(output_id, claim_index)
);

CREATE INDEX IF NOT EXISTS idx_output_claims_fact ON output_claims(fact_id);
CREATE INDEX IF NOT EXISTS idx_runs_question_group ON runs(namespace, created_at);
