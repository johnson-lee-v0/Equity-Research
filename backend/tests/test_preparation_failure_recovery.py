"""Pre-provider failures retain their reason and a bounded explicit recovery."""
import asyncio

import pytest

from backend.app.providers.base import ProviderError
from backend.tests.test_explicit_ticker_preparation import setup, case, execute


def prepared_case(repo, engine):
    rid, task = case(repo)
    # The routing fixture stores its plan directly; represent its already
    # completed provider task before exercising the subsequent preparation.
    with repo.db.transaction(immediate=True) as conn:
        conn.execute("UPDATE tasks SET status='completed' WHERE run_id=? AND agent_id='A00'", (rid,))
    execute(engine, rid, task["id"])
    return rid, task


def test_preparation_provider_failure_has_retryable_task_and_reason(setup, monkeypatch):
    repo, acquisition, service, provider, engine = setup
    rid, task = prepared_case(repo, engine)
    saved_output = repo.task(task["id"])["output_id"]
    async def fail(run_id, pending):
        raise ProviderError("capability", "Optional discovery exceeded its search limit.")
    monkeypatch.setattr(engine, "_execute_task", fail)
    asyncio.run(engine.run(rid))
    run = repo.run_snapshot(rid)
    pending = next(t for t in repo.tasks_for_run(rid) if t["agent_id"] == "A03")
    assert run["status"] == "failed" and "search limit" in run["error"]
    assert pending["status"] == "blocked"
    assert "retry" in run["allowed_actions"]
    assert repo.control("run", rid, "retry") == 1
    assert repo.task(task["id"])["output_id"] == saved_output
    assert provider.calls == []


def test_legacy_failed_preparation_retries_only_unstarted_tail(setup):
    repo, acquisition, service, provider, engine = setup
    rid, task = prepared_case(repo, engine)
    saved = dict(repo.task(task["id"]))
    repo.set_run_status(rid, "failed", error="Run stopped before all tasks completed.")
    assert "retry" in repo.run_snapshot(rid)["allowed_actions"]
    assert repo.control("run", rid, "retry") == 1
    assert dict(repo.task(task["id"])) == saved
    pending = next(t for t in repo.tasks_for_run(rid) if t["agent_id"] == "A03")
    assert pending["status"] == "queued" and pending["current_attempt_id"] is None
    with repo.db.operation() as conn:
        events = conn.execute("SELECT task_id FROM events WHERE run_id=? AND type='retry_requested'", (rid,)).fetchall()
    assert [row["task_id"] for row in events] == [pending["id"]]
    assert provider.calls == []


@pytest.mark.parametrize("status,error", [("failed", "A different failure."), ("cancelled", "Run stopped before all tasks completed."), ("completed", "Run stopped before all tasks completed.")])
def test_legacy_recovery_cannot_reopen_other_terminal_conditions(setup, status, error):
    repo, acquisition, service, provider, engine = setup
    rid, task = case(repo)
    repo.set_run_status(rid, status, error=error)
    assert "retry" not in repo.run_snapshot(rid)["allowed_actions"]
    with pytest.raises(ValueError):
        repo.control("run", rid, "retry")
