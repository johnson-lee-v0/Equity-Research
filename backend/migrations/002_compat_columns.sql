-- Compatibility additions for databases created by the first development
-- snapshot.  Fresh installs already include these columns in 001.
ALTER TABLE accounts ADD COLUMN interest_rate TEXT;
ALTER TABLE accounts ADD COLUMN interest_rate_period TEXT;
ALTER TABLE accounts ADD COLUMN interest_compounding TEXT;
ALTER TABLE sources ADD COLUMN observed_at TEXT;
ALTER TABLE tasks ADD COLUMN pause_requested INTEGER NOT NULL DEFAULT 0;
ALTER TABLE tasks ADD COLUMN progress_message TEXT;
ALTER TABLE tasks ADD COLUMN input_refs_json TEXT NOT NULL DEFAULT '[]';
