"""End-to-end preparation, one hosted review and mandatory target publication."""
import asyncio
import json
from pathlib import Path

import pytest

from backend.app.config import Settings
from backend.app.memory.repository import Repository
from backend.app.orchestration.workflow import Orchestrator, build_research_tasks
from backend.app.research.assessment_pipeline import require_price_targets, valuation_operand_fact_ids
from backend.app.research.case_store import CaseDecisionStore
from backend.app.research.earnings_context import verified_earnings_package
from backend.app.research.workflows import ResearchWorkflows, WorkflowStore
from backend.app.schemas import AgentOutputPayload, ImportRequest, RunCreate
from backend.tests.test_discovery_handoff import FakeProvider, FakeRegistry
from backend.tests.test_five_question_commit_path import _DeterministicLaya


RELEASE = """ACME Corporation (Nasdaq: ACME) reports results under U.S. GAAP.
ACME CORPORATION
CONSOLIDATED STATEMENTS OF INCOME
(USD in millions, except per share data)
52 Weeks Ended
August 30, 2026 | August 31, 2025
NET INCOME PER COMMON SHARE:
Basic | 10.02 | 8.02
Diluted | 10.00 | 8.00
Shares used in calculation (000's):
Diluted | 100 | 100
"""


@pytest.fixture
def case(tmp_path, monkeypatch):
    repo = Repository(config=Settings(project_root=Path(__file__).resolve().parents[2], data_dir=tmp_path,
                                      enable_market_connectors=False, enable_reddit_intake=False))
    source = repo.import_evidence(ImportRequest(namespace="real", kind="evidence", title="Synthetic ACME release",
        source_url="https://www.sec.gov/Archives/edgar/data/1234/earnings.htm", content=RELEASE,
        idempotency_key="synthetic-earnings"))["source_id"]
    wf = WorkflowStore(repo).create("ACME")
    package = {"company": {"ticker": "ACME", "name": "ACME Corporation", "cik": "0000001234"},
               "source_ids": [source], "event": {"period_end": "2026-08-30", "fiscal_period": "FY2026"},
               "documents": {"release": {"status": "available", "source_id": source}}}
    with repo.db.transaction(immediate=True) as conn:
        conn.execute("UPDATE research_workflow_runs SET status='completed',result_json=? WHERE id=?", (json.dumps(package), wf))
        conn.execute("UPDATE research_workflow_steps SET status='completed' WHERE run_id=?", (wf,))
    service = ResearchWorkflows(repo, None, repo.config)
    repo.control("firm", None, "pause")
    rid = service.handoff(wf)["run_id"]
    other, _ = repo.create_run(RunCreate(question="Unrelated queued idea", ticker="OTHER", namespace="real", idempotency_key="unrelated-idea"), build_research_tasks("Unrelated queued idea", None, "OTHER", "real", lean=True))
    async def no_network(*_):
        return []
    monkeypatch.setattr("backend.app.orchestration.workflow.acquire_financial_baseline", no_network)
    return repo, rid, wf, source, service, other["run_id"]


def answer(packet):
    assert packet["agent_id"] == "A11"
    fact = packet["financial_seeds"][0]
    assert fact["fact_id"].startswith("fact_") and "claim_id" not in fact
    assert packet["valuation_readiness"]["baseline_fact_id"] == fact["fact_id"]
    baseline = packet["valuation_readiness"]["baseline_input"]
    assert fact["fact_id"] in packet["valuation_readiness"]["valuation_fact_ids"]
    assert baseline["fact_claim_ids"] == [fact["fact_id"]]
    assert baseline["source_refs"] == [fact["source_ref"]]
    assert baseline["period"] == fact["period"]
    assert all(baseline[key] == fact.get(key) for key in ("value", "unit", "currency", "basis", "scale", "statement_type"))
    method = {"name": "eps_multiple", "period": "FY2027", "horizon_months": 12, "currency": "USD",
        "inputs": [{"key": "baseline_eps", "kind": "fact", "value": fact["value"], "period": fact["period"], "basis": fact["basis"],
                    "unit": fact["unit"], "currency": "USD", "fact_claim_ids": [fact["fact_id"]], "source_refs": [fact["source_ref"]]}],
        "scenarios": {name: {"growth_rate": growth, "exit_multiple": multiple, "growth_rationale": "Explicit synthetic operating assumption.",
                              "multiple_rationale": "Explicit synthetic valuation assumption."} for name, growth, multiple in (("bear", "0", "10"), ("base", "0.1", "15"), ("bull", "0.2", "20"))},
        "rationale": "A synthetic forecast for integration testing, not a real investment recommendation."}
    return AgentOutputPayload(status="completed", research_contract="five-questions.v1", title="Synthetic final assessment",
        summary="Synthetic assessment with a sourced baseline and forecast assumptions.", analysis="All five questions were reviewed.",
        source_refs=[fact["source_ref"]], candidate_briefs=[{"ticker": "ACME", "issuer": "ACME", "issuer_name": "ACME Corporation", "horizon": "12 months",
            "stance": "watch", "valuation_assumptions": {"methods": [method]},
            "key_questions": [{"key": key, "answer": "The synthetic source supports an explicit scenario assessment.",
                "decision_implication": "Reassess if operating performance weakens.", "supporting_claim_ids": [fact["fact_id"]] if key == "valuation" else []}
                for key in ("opportunity", "valuation", "catalyst", "downside", "portfolio_action")],
            "laya_response": {"position": "agree", "reason": "Keep the synthetic case on watch pending execution.", "fact_claim_ids": [fact["fact_id"]], "question_keys": ["valuation"]}}])


def test_one_hosted_call_completes_target_without_resuming_other_cases(case):
    repo, rid, wf, source, _, other = case
    provider = FakeProvider(answer)
    engine = Orchestrator(repo, FakeRegistry(provider), repo.config, laya_runtime=_DeterministicLaya())
    repo.control("run", rid, "run_once")
    asyncio.run(engine.run(rid))
    assert repo.run_record(rid)["status"] == "completed", [(t["agent_id"], t["status"], t["error"]) for t in repo.tasks_for_run(rid)]
    assert len(provider.calls) == 1
    decision = CaseDecisionStore(repo).current(rid, "real")
    candidate = decision["candidates"][0]
    assert candidate["future_target"]["price"] == "165.00000000"
    assert candidate["valuation"]["scenarios"] == {"bear": "100.00000000", "base": "165.00000000", "bull": "240.00000000"}
    assert len(candidate["key_questions"]) == 5
    assert repo.firm_paused() and repo.run_record(other)["status"] == "queued"
    with repo.db.operation() as conn:
        attempts = conn.execute("SELECT a.provider FROM task_attempts a JOIN tasks t ON t.id=a.task_id WHERE t.run_id=?", (rid,)).fetchall()
        assert [row[0] for row in attempts].count("deterministic") == 3
        assert verified_earnings_package(conn, rid)
        packet = json.loads(conn.execute("SELECT payload_json FROM events WHERE run_id=? AND type='assessment_provider_packet'", (rid,)).fetchone()[0])
        assert packet["prompt_chars"] > 0 and packet["schema_chars"] > 0
        assert len(packet["prompt_hash"]) == len(packet["schema_hash"]) == 64
        assert set(packet) == {"message", "prompt_chars", "schema_chars", "prompt_hash", "schema_hash", "source_count"}


def test_financial_retrieval_cutoff_prevents_new_source_being_mistaken_for_future_data(case):
    repo, rid, _, _, _, _ = case
    original_creation = repo.run_record(rid)["created_at"]
    with repo.db.transaction(immediate=True) as conn:
        conn.execute("UPDATE runs SET as_of='2026-09-01T00:00:00Z' WHERE id=?", (rid,))
    provider = FakeProvider(answer)
    engine = Orchestrator(repo, FakeRegistry(provider), repo.config, laya_runtime=_DeterministicLaya())
    repo.control("run", rid, "run_once")
    asyncio.run(engine.run(rid))
    assert repo.run_record(rid)["status"] == "completed"
    packet = provider.calls[0]["packet"]
    assert packet["financial_seeds"][0]["freshness"] == "fresh"
    current = repo.run_record(rid)
    assert current["as_of"] > "2026-09-01T00:00:00Z" and current["created_at"] == original_creation
    assert repo.freeze_assessment_clock(rid) == current["as_of"]


def test_missing_target_repairs_once_then_fails_without_publishing(case):
    repo, rid, _, _, _, _ = case
    def invalid(packet):
        payload = answer(packet)
        payload.candidate_briefs[0].valuation_assumptions = None
        return payload
    provider = FakeProvider(invalid)
    engine = Orchestrator(repo, FakeRegistry(provider), repo.config, laya_runtime=_DeterministicLaya())
    repo.control("run", rid, "run_once")
    asyncio.run(engine.run(rid))
    assert len(provider.calls) == 2
    assert repo.run_record(rid)["status"] == "failed"
    assert repo.run_record(rid)["error"].startswith("Price target validation:")
    assert CaseDecisionStore(repo).current(rid, "real") is None
    assert "Price target validation:" in provider.calls[-1]["packet"]["question"]


def test_reassessment_keeps_old_source_link_and_reuses_inflight_run(case):
    repo, rid, wf, _, service, _ = case
    assert service.handoff(wf, reassess=True)["run_id"] == rid
    repo.set_run_status(rid, "completed")
    second = service.handoff(wf, reassess=True)["run_id"]
    assert second != rid
    assert service.handoff(wf, reassess=True)["run_id"] == second
    with repo.db.operation() as conn:
        assert verified_earnings_package(conn, rid)
        assert verified_earnings_package(conn, second)
    assert repo.firm_paused()


def test_valuation_operands_exclude_operating_context_and_changed_financial_facts():
    financial = {"claim_id": "c1", "source_ref": "sec", "source_version": "1", "subject": "ACME",
                 "metric": "eps", "value": "10", "currency": "USD", "unit": "USD/share",
                 "period": "FY2026", "basis": "GAAP diluted"}
    operating = financial | {"claim_id": "c2", "source_ref": "call", "metric": "paid_members_growth",
                             "value": "3.8", "currency": None, "unit": "%"}
    compiled = {"fact_claims": [financial, operating], "seed_proofs": [
        {"claim_id": "c1", "validation": {"proof": {"parser": "earnings-financial-tables.v1"}}},
        {"claim_id": "c2", "validation": {"proof": {"parser": "earnings-operating-facts.v1"}}}]}
    supported = {"validation_status": "validated", "recorded_validation_status": "validated",
                 "current_validation_status": "validated", "semantic_status": "supported"}
    saved = [financial | supported | {"claim_id": "saved_alias", "fact_id": "fact_eps"},
             operating | supported | {"fact_id": "fact_growth"},
             financial | supported | {"fact_id": "fact_changed", "currency": "CAD"},
             financial | supported | {"fact_id": "fact_unavailable", "current_validation_status": "unavailable"}]
    assert valuation_operand_fact_ids(compiled, saved) == ["fact_eps"]
    assert valuation_operand_fact_ids({"fact_claims": [financial], "seed_proofs": []}, saved) == []
