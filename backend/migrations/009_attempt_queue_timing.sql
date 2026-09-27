-- Keep provider queue time distinct from provider execution time.  Existing
-- attempts retain their original started_at as historical timing; new
-- attempts write only queued_at until a generation slot is acquired.
ALTER TABLE task_attempts ADD COLUMN queued_at TEXT;
UPDATE task_attempts SET queued_at=started_at WHERE queued_at IS NULL;
-- The pre-read-model schema recorded provider start in started_at.  Copy that
-- historical fact into the new marker so only newly created attempts have a
-- NULL provider_started_at while they wait for capacity.
UPDATE task_attempts SET provider_started_at=started_at WHERE provider_started_at IS NULL;
