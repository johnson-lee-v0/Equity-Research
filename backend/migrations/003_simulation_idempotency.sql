-- Added after the initial development snapshot. Fresh databases get this in
-- 001; the compatibility migration handles older local files.
ALTER TABLE simulations ADD COLUMN idempotency_key TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_simulations_idempotency ON simulations(idempotency_key);
