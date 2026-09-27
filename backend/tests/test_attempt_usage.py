"""Returned provider usage survives rejected output and lifecycle changes."""
import asyncio
import json

import pytest

from backend.app.orchestration.workflow import Orchestrator
from backend.app.research.case_store import CaseDecisionStore
from backend.tests.test_assessment_pipeline import answer, case  # noqa: F401
from backend.tests.test_discovery_handoff import FakeProvider, FakeRegistry
from backend.tests.test_five_question_commit_path import _DeterministicLaya


def test_usage_receipt_does_not_finish_attempt_and_none_does_not_erase_it(case):
    repo, rid, _, _, _, _ = case
    task = repo.tasks_for_run(rid)[0]
    model, _ = repo.resolve_model(task["agent_id"], lean=True)
    attempt = repo.create_attempt(task["id"], model, {})["attempt_id"]
    def read():
        with repo.db.operation() as conn:
            return dict(conn.execute("SELECT * FROM task_attempts WHERE id=?", (attempt,)).fetchone())
    before = read()
    usage = {"input_tokens": 101, "output_tokens": 17}
    repo.record_attempt_usage(attempt, usage)
    after = read()
    assert {k: v for k, v in after.items() if k != "usage_json"} == {k: v for k, v in before.items() if k != "usage_json"}
    assert json.loads(after["usage_json"]) == usage
    repo.record_attempt_usage(attempt, None)
    repo.finish_attempt(attempt, "failed", "Output validation failed.")
    final = read()
    assert final["status"] == "failed" and final["finished_at"]
    assert final["error"] == "Output validation failed."
    assert json.loads(final["usage_json"]) == usage
    repo.finish_attempt(attempt, "failed", usage={"output_tokens": 18})
    assert json.loads(read()["usage_json"]) == {"output_tokens": 18}


@pytest.mark.parametrize("failure,expected_calls", [("missing_target", 2), ("invalid_payload", 1)])
def test_failed_assessment_records_every_returned_provider_call(case, failure, expected_calls):
    repo, rid, _, _, _, _ = case
    def invalid_target(packet):
        payload = answer(packet)
        if failure == "missing_target":
            payload.candidate_briefs = []
        return payload
    class InvalidProvider(FakeProvider):
        async def execute(self, *args, **kwargs):
            result = await super().execute(*args, **kwargs)
            if failure == "invalid_payload":
                result.payload["status"] = "invalid-status"
            return result
    provider = InvalidProvider(invalid_target)
    engine = Orchestrator(repo, FakeRegistry(provider), repo.config, laya_runtime=_DeterministicLaya())
    repo.control("run", rid, "run_once")
    asyncio.run(engine.run(rid))
    assert len(provider.calls) == expected_calls
    assert repo.run_record(rid)["status"] == "failed"
    assert CaseDecisionStore(repo).current(rid, "real") is None
    with repo.db.operation() as conn:
        rows = conn.execute("SELECT a.* FROM task_attempts a JOIN tasks t ON t.id=a.task_id WHERE t.run_id=? AND t.agent_id='A11' ORDER BY a.attempt_no", (rid,)).fetchall()
        assert len(rows) == expected_calls
        assert all(r["status"] == "failed" and r["finished_at"] and r["error"] for r in rows)
        assert [json.loads(r["usage_json"]) for r in rows] == [{"fake_calls": i} for i in range(1, expected_calls + 1)]
