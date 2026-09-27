"""Durable, source-bound calculator preparation before the final prose call."""
import asyncio
from copy import deepcopy

import pytest

from backend.app.db import digest, json_dumps, json_loads
from backend.app.orchestration.workflow import Orchestrator
from backend.app.research import investment_valuation_preparation as prep
from backend.app.schemas import ValuationReview
from backend.tests.test_assessment_pipeline import case as assessment_case
from backend.tests.test_discovery_handoff import FakeProvider, FakeRegistry
from backend.tests.test_five_question_commit_path import _DeterministicLaya
from backend.tests.test_investment_valuation import ordinary, answer
from backend.tests.test_valuation_preparation_review import prepared_case


def events(repo, rid, kind):
    return [row["payload"] for row in repo.events("real", run_id=rid) if row["type"] == kind]


def run_prepare(fixture):
    repo, rid, engine, task, attempt, config, context, sources, versions = fixture
    return asyncio.run(prep.prepare(engine, repo.run_record(rid), task, attempt, config, context, sources, versions))


def correction(packet):
    assert packet["stage"] == "valuation_input_correction"
    assumptions = deepcopy(packet["proposed_assumptions"])
    assumptions["methods"][0]["scenarios"]["base"]["exit_multiple"] = "15"
    return prep.InputCorrection(assumptions=assumptions, explanation="Restore the missing analyst base P/E assumption.")


def test_ready_and_restart_reuse_need_zero_new_model_calls(assessment_case):
    fixture = prepared_case(assessment_case)
    repo, rid, engine, *_ = fixture
    before = len(engine.providers.provider.calls)
    first = run_prepare(fixture)
    second = run_prepare(fixture)
    assert first["calculation"]["scenarios"]["base"] == "181.50000000"
    assert first["model_calls"] == 0 and second["reused"]
    assert len(engine.providers.provider.calls) == before
    assert len(events(repo, rid, prep.EVENT)) == 1


def test_completed_correction_reused_after_crash_before_ready(assessment_case, monkeypatch):
    fixture = prepared_case(assessment_case, broken=True)
    repo, rid, engine, *_ = fixture
    adapter = FakeProvider(correction)
    engine.providers = FakeRegistry(adapter)
    original_emit = prep._emit
    def crash(repo, scope, event, record, **kwargs):
        if event == prep.EVENT and record["status"] == "ready":
            raise RuntimeError("Simulated process exit before ready receipt")
        return original_emit(repo, scope, event, record, **kwargs)
    monkeypatch.setattr(prep, "_emit", crash)
    with pytest.raises(RuntimeError, match="Simulated"):
        run_prepare(fixture)
    assert [row["status"] for row in events(repo, rid, prep.REPAIR_EVENT)] == ["started", "completed"]
    monkeypatch.setattr(prep, "_emit", original_emit)
    receipt = run_prepare(fixture)
    assert receipt["status"] == "ready" and len(adapter.calls) == 1
    assert len(events(repo, rid, prep.REPAIR_EVENT)) == 2


def test_failed_correction_budget_survives_retry_and_never_starts_prose(assessment_case):
    fixture = prepared_case(assessment_case, broken=True)
    repo, rid, engine, task, attempt, config, context, sources, versions = fixture
    adapter = FakeProvider(lambda packet: prep.InputCorrection(assumptions=packet["proposed_assumptions"], explanation="Unchanged invalid inputs."))
    engine.providers = FakeRegistry(adapter)
    with pytest.raises(prep.PreparationError, match="did not pass"):
        run_prepare(fixture)
    # A new parent attempt does not grant a second repair for unchanged inputs.
    new_attempt = repo.create_attempt(task["id"], config, versions)["attempt_id"]
    updated = (repo, rid, engine, task, new_attempt, config, context, sources, versions)
    with pytest.raises(prep.PreparationError, match="did not pass"):
        run_prepare(updated)
    assert len(adapter.calls) == 1
    assert [row["status"] for row in events(repo, rid, prep.REPAIR_EVENT)] == ["started", "completed"]


def test_no_financial_baseline_does_not_spend_repair_budget(assessment_case):
    fixture = prepared_case(assessment_case)
    context = fixture[6]
    context["valuation_readiness"] = {"status": "needs_valuation_evidence", "missing_inputs": ["Annual earnings unavailable."]}
    receipt = run_prepare(fixture)
    assert receipt["status"] == "unavailable" and receipt["model_calls"] == 0
    assert not events(fixture[0], fixture[1], prep.REPAIR_EVENT)


@pytest.mark.parametrize("change", ["source_version", "analyst_hash", "parent_sources"])
def test_changed_frozen_binding_rejects_ready_reuse(assessment_case, change):
    fixture = prepared_case(assessment_case)
    repo, rid, _, _, attempt, _, context, sources, _ = fixture
    run_prepare(fixture)
    with repo.db.transaction(immediate=True) as conn:
        if change == "source_version":
            conn.execute("UPDATE source_versions SET content_hash='changed' WHERE source_id=?", (sources[0]["id"],))
        elif change == "analyst_hash":
            conn.execute("UPDATE outputs SET output_hash='changed' WHERE id=?", (context["prior_outputs"][0]["id"],))
        else:
            conn.execute("UPDATE task_attempts SET source_versions_json='{}' WHERE id=?", (attempt,))
    with pytest.raises(prep.PreparationError, match="evidence or analyst output changed"):
        run_prepare(fixture)


def test_reviewer_can_reject_method_without_publishing_prices_or_retry(assessment_case):
    repo, rid = ordinary(assessment_case)
    def respond(packet):
        payload = answer(packet)
        if packet["agent_id"] == "A11":
            payload.candidate_briefs[0].valuation_review = ValuationReview(status="reject", reason="The earnings multiple is unsuitable because profitability is structurally unstable.")
        return payload
    adapter = FakeProvider(respond)
    engine = Orchestrator(repo, FakeRegistry(adapter), repo.config, laya_runtime=_DeterministicLaya())
    for agent in ("A03", "A11"):
        task = next(row for row in repo.tasks_for_run(rid) if row["agent_id"] == agent)
        asyncio.run(engine._execute_task(rid, task))
        assert repo.task(task["id"])["status"] == "completed", repo.task(task["id"])["error"]
    assert len(adapter.calls) == 2
    from backend.app.research.case_store import CaseDecisionStore
    candidate = CaseDecisionStore(repo).current(rid, "real")["candidates"][0]
    assert candidate["future_target"] is None
    assert candidate["valuation"]["status"] != "complete" and not candidate["valuation"]["scenarios"]
    assert not any("continuation" in task["kind"] or "gap_repair" in task["kind"] for task in repo.tasks_for_run(rid))
    assert "rejected" in json_dumps(candidate["material_blockers"]).lower()
    assert events(repo, rid, prep.EVENT)[0]["calculation"]["scenarios"]["base"] == "181.50000000"


def test_reviewer_cannot_replace_prepared_numeric_assumptions(assessment_case):
    fixture = prepared_case(assessment_case)
    receipt = run_prepare(fixture)
    baseline = receipt["assumptions"]["methods"][0]["inputs"][0]
    payload = answer({"valuation_readiness": {"baseline_input": baseline}, "valuation_preparation": receipt})
    payload.candidate_briefs[0].valuation_assumptions = prep.ValuationAssumptions.model_validate(receipt["assumptions"])
    payload.candidate_briefs[0].valuation_assumptions.methods[0].scenarios.base.exit_multiple = "99"
    with pytest.raises(prep.PreparationError, match="changed verified"):
        prep.apply_review(payload, receipt, ticker="ACME", horizon="24 months")


def test_interrupted_started_correction_does_not_reset_budget(assessment_case, monkeypatch):
    fixture = prepared_case(assessment_case, broken=True)
    repo, rid, engine, *_ = fixture
    adapter = FakeProvider(correction)
    engine.providers = FakeRegistry(adapter)
    original_emit = prep._emit
    def crash(repo, scope, event, record, **kwargs):
        original_emit(repo, scope, event, record, **kwargs)
        if event == prep.REPAIR_EVENT and record["status"] == "started":
            raise RuntimeError("Simulated process death after durable start")
    monkeypatch.setattr(prep, "_emit", crash)
    with pytest.raises(prep.PreparationError, match="Simulated process death"):
        run_prepare(fixture)
    monkeypatch.setattr(prep, "_emit", original_emit)
    with pytest.raises(prep.PreparationError, match="already attempted or interrupted"):
        run_prepare(fixture)
    assert len(adapter.calls) == 0
    assert [row["status"] for row in events(repo, rid, prep.REPAIR_EVENT)] == ["started"]


@pytest.mark.parametrize("replacement", ["target", "aggregate", "assumptions", "narrative"])
def test_method_rejection_cannot_smuggle_numeric_replacement(assessment_case, replacement):
    fixture = prepared_case(assessment_case)
    receipt = run_prepare(fixture)
    payload = answer({"valuation_readiness": {"baseline_input": receipt["assumptions"]["methods"][0]["inputs"][0]}, "valuation_preparation": receipt})
    candidate = payload.candidate_briefs[0]
    candidate.valuation_review = ValuationReview(status="reject", reason="The method does not fit this issuer.")
    if replacement == "target":
        candidate.target_price = "999"
    elif replacement == "aggregate":
        payload.target_price = "999"
    elif replacement == "assumptions":
        candidate.valuation_assumptions = prep.ValuationAssumptions.model_validate(receipt["assumptions"])
    else:
        payload.summary = "The implied value is $999."
    with pytest.raises(prep.PreparationError, match="must not claim"):
        prep.apply_review(payload, receipt, ticker="ACME", horizon="24 months")
