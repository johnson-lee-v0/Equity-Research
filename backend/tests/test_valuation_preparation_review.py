"""Independent lifecycle and horizon checks for bounded pricing preparation."""
import asyncio
from copy import deepcopy

import pytest

from backend.app.db import json_loads
from backend.app.orchestration.workflow import Orchestrator
from backend.app.research import investment_valuation_preparation as preparation
from backend.app.schemas import AgentOutputPayload
from backend.tests.test_assessment_pipeline import case as assessment_case
from backend.tests.test_discovery_handoff import FakeProvider, FakeRegistry
from backend.tests.test_five_question_commit_path import _DeterministicLaya
from backend.tests.test_investment_valuation import answer, ordinary


def prepared_case(case, *, broken=False):
    repo, rid = ordinary(case)

    def respond(packet):
        payload = answer(packet).model_dump(mode="json")
        if broken:
            payload["candidate_briefs"][0]["valuation_assumptions"]["methods"][0]["scenarios"]["base"].pop("exit_multiple")
        return AgentOutputPayload.model_validate(payload)

    engine = Orchestrator(repo, FakeRegistry(FakeProvider(respond)), repo.config, laya_runtime=_DeterministicLaya())
    analyst = next(task for task in repo.tasks_for_run(rid) if task["kind"] == "research_synthesis")
    asyncio.run(engine._execute_task(rid, analyst))
    assert repo.task(analyst["id"])["status"] == "completed", repo.task(analyst["id"])["error"]
    prior = [row for row in repo.latest_outputs(rid) if row["agent_id"] == "A03"]
    snapshot = json_loads(repo.run_record(rid)["input_snapshot_json"], {})
    sources = repo.source_packet("real", snapshot["source_ids"])
    versions = {row["id"]: {"version": row["version"], "hash": row["content_hash"]} for row in sources}
    task = next(task for task in repo.tasks_for_run(rid) if task["kind"] == "cio_review")
    config = repo.resolve_model("A11", lean=True)[0]
    attempt = repo.create_attempt(task["id"], config, versions)
    context = {"prior_outputs": prior, "valuation_readiness": {"status": "ready", "ready_methods": ["eps_multiple"]},
               "financial_seeds": prior[0]["fact_claims"]}
    return repo, rid, engine, task, attempt["attempt_id"], config, context, sources, versions


@pytest.mark.parametrize("months", [12, 36])
def test_ready_calculation_rejects_a_different_requested_holding_horizon(assessment_case, months):
    repo, rid, _, _, _, _, context, sources, _ = prepared_case(assessment_case)
    prior = context["prior_outputs"][0]
    candidate = prior["candidate_briefs"][0]
    assumptions = preparation.rebind_assumptions(candidate["valuation_assumptions"], prior["fact_claims"])
    run = repo.run_record(rid)
    valid, errors = preparation.calculate(assumptions, candidate, facts=prior["fact_claims"], sources=sources,
        as_of=run["as_of"], horizon=run["horizon"])
    assert not errors and valid["horizon"] == "24 months"
    changed = deepcopy(assumptions)
    changed["methods"][0]["horizon_months"] = months
    _, errors = preparation.calculate(changed, candidate, facts=prior["fact_claims"], sources=sources,
        as_of=run["as_of"], horizon=run["horizon"])
    assert errors and any("horizon" in message.casefold() for message in errors)


def test_task_only_cancel_stops_active_input_correction_and_keeps_receipts(assessment_case):
    repo, rid, engine, task, attempt_id, config, context, sources, versions = prepared_case(assessment_case, broken=True)
    original = deepcopy(repo.latest_outputs(rid))
    config_before = config.model_dump(mode="json")

    class WaitingCorrection(FakeProvider):
        def __init__(self):
            super().__init__(answer)
            self.started = asyncio.Event()
            self.cancelled = []
            self.attempt_ids = []

        async def execute(self, correction_id, *_args, **_kwargs):
            self.attempt_ids.append(correction_id)
            self.started.set()
            await asyncio.Event().wait()

        async def cancel(self, correction_id):
            self.cancelled.append(correction_id)
            return {"cancelled": True}

    async def exercise():
        adapter = WaitingCorrection()
        engine.providers = FakeRegistry(adapter)
        pending = asyncio.create_task(preparation.prepare(engine, repo.run_record(rid), task, attempt_id,
            config, context, sources, versions))
        await asyncio.wait_for(adapter.started.wait(), timeout=2)
        repo.control("task", task["id"], "cancel")
        await engine.cancel_active(task_id=task["id"])
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(pending, timeout=2)
        assert len(adapter.attempt_ids) == 1
        assert adapter.attempt_ids[0] in adapter.cancelled
        assert adapter.attempt_ids[0] not in engine.active

    asyncio.run(exercise())
    records = [event["payload"] for event in repo.events("real", run_id=rid) if event["type"] == preparation.REPAIR_EVENT]
    assert [row["status"] for row in records] == ["started", "cancelled"]
    assert not any(event["type"] == preparation.EVENT and event["payload"].get("status") == "ready"
                   for event in repo.events("real", run_id=rid))
    assert repo.latest_outputs(rid) == original
    assert config.model_dump(mode="json") == config_before


@pytest.mark.parametrize("changed", ["analyst_output", "source"])
def test_committed_review_recovery_rechecks_prepared_input_bindings(assessment_case, monkeypatch, changed):
    repo, rid = ordinary(assessment_case)
    engine = Orchestrator(repo, FakeRegistry(FakeProvider(answer)), repo.config, laya_runtime=_DeterministicLaya())
    analyst = next(task for task in repo.tasks_for_run(rid) if task["kind"] == "research_synthesis")
    asyncio.run(engine._execute_task(rid, analyst))
    reviewer = next(task for task in repo.tasks_for_run(rid) if task["kind"] == "cio_review")

    async def crash_after_commit(*_args, **_kwargs):
        raise RuntimeError("Simulated exit after review commit")

    monkeypatch.setattr(engine, "_run_post_astra_laya", crash_after_commit)
    try:
        asyncio.run(engine._execute_task(rid, reviewer))
    except RuntimeError as exc:
        assert "Simulated exit" in str(exc)
    else:
        pytest.fail(str(repo.task(reviewer["id"])["error"]))
    retained = repo.task(reviewer["id"])
    assert retained["output_id"] and retained["status"] == "waiting_review"
    with repo.db.transaction(immediate=True) as conn:
        if changed == "analyst_output":
            conn.execute("UPDATE outputs SET payload_json='{}' WHERE id=?", (repo.task(analyst["id"])["output_id"],))
        else:
            source_id = next(iter(repo.attempt_source_versions(retained["current_attempt_id"])))
            conn.execute("UPDATE sources SET original_content=original_content || ' changed' WHERE id=?", (source_id,))

    local_calls = []

    async def must_not_review(*_args, **_kwargs):
        local_calls.append(True)
        raise AssertionError("Invalid preparation must stop before local review")

    monkeypatch.setattr(engine, "_run_post_astra_laya", must_not_review)
    assert not asyncio.run(engine._recover_contract_cio_review(rid, retained, repo.run_record(rid)))
    assert not local_calls
    from backend.app.research.case_store import CaseDecisionStore
    assert CaseDecisionStore(repo).current(rid, "real") is None


def test_correction_timeout_cancels_and_reaps_provider_without_a_second_call(assessment_case, monkeypatch):
    repo, rid, engine, task, attempt_id, config, context, sources, versions = prepared_case(assessment_case, broken=True)
    monkeypatch.setattr(preparation, "REPAIR_TIMEOUT_SECONDS", 0.05)

    class NeverReturns(FakeProvider):
        def __init__(self):
            super().__init__(answer)
            self.attempt_ids = []
            self.cancelled = []
            self.reaped = []

        async def execute(self, correction_id, *_args, **_kwargs):
            self.attempt_ids.append(correction_id)
            try:
                await asyncio.Event().wait()
            finally:
                self.reaped.append(correction_id)

        async def cancel(self, correction_id):
            self.cancelled.append(correction_id)
            return {"cancelled": True}

    adapter = NeverReturns()
    engine.providers = FakeRegistry(adapter)
    with pytest.raises(preparation.PreparationError, match="exceeded"):
        asyncio.run(preparation.prepare(engine, repo.run_record(rid), task, attempt_id, config, context, sources, versions))
    assert len(adapter.attempt_ids) == 1
    assert adapter.cancelled == adapter.reaped == adapter.attempt_ids
    records = [event["payload"] for event in repo.events("real", run_id=rid) if event["type"] == preparation.REPAIR_EVENT]
    assert [row["status"] for row in records] == ["started", "failed"]
    replacement = repo.create_attempt(task["id"], config, versions)["attempt_id"]
    with pytest.raises(preparation.PreparationError, match="already attempted"):
        asyncio.run(preparation.prepare(engine, repo.run_record(rid), task, replacement, config, context, sources, versions))
    assert len(adapter.attempt_ids) == 1
    assert not engine.active
