"""Ordinary cold-company research uses the same retained financial operands."""
import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from backend.app.db import json_dumps, json_loads
from backend.app.orchestration.workflow import Orchestrator
from backend.app.research.assessment_evidence import compile_earnings_assessment
from backend.app.research.investment_process import receipt_for
from backend.app.research.investment_valuation import compile_financial_preparation, financial_seeds, prepared_context, merge_prepared_facts
from backend.app.schemas import AgentOutputPayload, ImportRequest, RoutingPlan
from backend.tests.test_assessment_pipeline import case as assessment_case
from backend.tests.test_discovery_handoff import FakeProvider, FakeRegistry
from backend.tests.test_five_question_commit_path import _DeterministicLaya


def ordinary(case):
    repo, rid, wf, _, service, _ = case
    snapshot = json_loads(repo.run_record(rid)["input_snapshot_json"], {})
    snapshot.pop("assessment_pipeline")
    snapshot["investment_process"] = {"version": "investment-process.v1", "earnings": [receipt_for("ACME", wf, service.store.get(wf)["result"], reused=True)]}
    with repo.db.transaction(immediate=True) as conn:
        conn.execute("UPDATE runs SET input_snapshot_json=?,horizon='24 months' WHERE id=?", (json_dumps(snapshot), rid))
    repo.consume_routing_plan(rid, RoutingPlan(intent="research", horizon="24 months", tickers=["ACME"], selected_analysts=["A03"]))
    repo.control("run", rid, "run_once")
    return repo, rid


def answer(packet):
    baseline = packet["valuation_readiness"]["baseline_input"]
    identifier = baseline["fact_claim_ids"][0]
    method = {"name": "eps_multiple", "period": "FY2028", "horizon_months": 24,
              "forecast_years": "2", "forecast_span_rationale": "FY2026 reported baseline to FY2028 forecast spans two fiscal years.", "currency": "USD", "inputs": [baseline],
              "scenarios": {name: {"growth_rate": growth, "exit_multiple": multiple,
                  "growth_rationale": "Explicit synthetic annual forecast.", "multiple_rationale": "Explicit synthetic valuation assumption."}
                  for name, growth, multiple in (("bear", "0", "10"), ("base", "0.1", "15"), ("bull", "0.2", "20"))}}
    result = AgentOutputPayload(status="completed", research_contract="five-questions.v1", title="Ordinary synthetic investment review",
        summary="The source-bound earnings support a scenario valuation.", analysis="The review retains the requested 24-month holding horizon.",
        source_refs=baseline["source_refs"], candidate_briefs=[{"ticker": "ACME", "issuer": "ACME", "issuer_name": "ACME Corporation",
            "horizon": "24 months", "stance": "watch", "valuation_assumptions": {"methods": [method]},
            "key_questions": [{"key": key, "answer": "Synthetic reported earnings support a conditional investment assessment.",
                "decision_implication": "Review operating performance before changing the investment decision.",
                "supporting_claim_ids": [identifier] if key == "valuation" else []}
                for key in ("opportunity", "valuation", "catalyst", "downside", "portfolio_action")],
            "laya_response": {"position": "agree", "reason": "Keep the synthetic company on watch.",
                "fact_claim_ids": [identifier], "question_keys": ["valuation"]}}])
    if (packet.get("valuation_preparation") or {}).get("status") == "ready":
        from backend.app.schemas import ValuationReview
        result.candidate_briefs[0].valuation_assumptions = None
        result.candidate_briefs[0].valuation_review = ValuationReview(status="accept", reason="The proposed method fits this synthetic company.")
    return result


def test_ordinary_analyst_commits_prepared_facts_and_reviewer_uses_durable_ids(assessment_case):
    repo, rid = ordinary(assessment_case)
    provider = FakeProvider(answer)
    engine = Orchestrator(repo, FakeRegistry(provider), repo.config, laya_runtime=_DeterministicLaya())
    a03 = next(task for task in repo.tasks_for_run(rid) if task["agent_id"] == "A03")
    asyncio.run(engine._execute_task(rid, a03))
    assert repo.task(a03["id"])["status"] == "completed", repo.task(a03["id"])["error"]
    analyst = provider.calls[0]["packet"]
    assert analyst["valuation_readiness"]["ready_methods"] == ["eps_multiple"]
    assert analyst["financial_seeds"][0]["claim_id"].startswith("prepared_")
    assert "requested holding horizon" in analyst["question"]
    a11 = next(task for task in repo.tasks_for_run(rid) if task["agent_id"] == "A11")
    asyncio.run(engine._execute_task(rid, a11))
    assert repo.task(a11["id"])["status"] == "completed", repo.task(a11["id"])["error"]
    reviewer = provider.calls[-1]["packet"]
    assert reviewer["financial_seeds"][0]["fact_id"].startswith("fact_")
    assert "claim_id" not in reviewer["financial_seeds"][0]
    assert reviewer["valuation_readiness"]["baseline_input"]["value"] == "10.00"
    assert reviewer["horizon"] == "24 months"
    from backend.app.research.case_store import CaseDecisionStore
    candidate = CaseDecisionStore(repo).current(rid, "real")["candidates"][0]
    assert candidate["valuation"]["scenarios"]["base"] == "181.50000000"
    assert len(candidate["key_questions"]) == 5


def test_ordinary_target_correction_is_input_only_before_one_final_review(assessment_case):
    from backend.app.research.investment_valuation_preparation import InputCorrection
    repo, rid = ordinary(assessment_case)
    repair_packets, reviewer_calls = [], []
    def respond(packet):
        if packet.get("stage") == "valuation_input_correction":
            repair_packets.append(packet)
            assert packet["calculator_errors"]
            assert "analysis" not in packet and "evidence" not in packet and "prior_outputs" not in packet
            assumptions = deepcopy(packet["proposed_assumptions"])
            assumptions["methods"][0]["scenarios"]["base"]["exit_multiple"] = "15"
            return InputCorrection(assumptions=assumptions, explanation="Restore the invalid base multiple to the analyst's explicit synthetic assumption.")
        result = answer(packet)
        if packet["agent_id"] == "A03":
            result.candidate_briefs[0].valuation_assumptions.methods[0].scenarios.base.exit_multiple = "-1"
        else:
            reviewer_calls.append(packet)
            assert packet["valuation_preparation"]["calculation"]["scenarios"]["base"] == "181.50000000"
        return result
    provider = FakeProvider(respond)
    engine = Orchestrator(repo, FakeRegistry(provider), repo.config, laya_runtime=_DeterministicLaya())
    analyst = next(task for task in repo.tasks_for_run(rid) if task["agent_id"] == "A03")
    asyncio.run(engine._execute_task(rid, analyst))
    reviewer = next(task for task in repo.tasks_for_run(rid) if task["agent_id"] == "A11")
    asyncio.run(engine._execute_task(rid, reviewer))
    assert repo.task(reviewer["id"])["status"] == "completed", repo.task(reviewer["id"])["error"]
    assert len(repair_packets) == len(reviewer_calls) == 1


def test_clock_freezes_after_interim_and_market_and_retry_reuses_sources(assessment_case, monkeypatch):
    repo, rid = ordinary(assessment_case)
    clock = ["2026-09-26T12:00:00Z"]
    monkeypatch.setattr("backend.app.memory.repository.utc_now", lambda: clock[0])
    stages, archived = [], []
    async def earnings(*args, **kwargs):
        return []
    async def financial(*args, **kwargs):
        stages.append("financial")
        assert not repo.run_snapshot(rid).get("financial_preparation_as_of")
        return []
    async def interim(*args, **kwargs):
        if "interim" in stages:
            return []
        stages.append("interim")
        assert not json_loads(repo.run_record(rid)["input_snapshot_json"], {}).get("financial_preparation_as_of")
        clock[0] = "2026-09-26T12:00:01Z"
        return []
    async def market(*args, **kwargs):
        stages.append("market")
        assert not json_loads(repo.run_record(rid)["input_snapshot_json"], {}).get("financial_preparation_as_of")
        clock[0] = "2026-09-26T12:00:02Z"
        sid = repo.import_evidence(ImportRequest(namespace="real", kind="evidence", title="Dated market observation",
            source_url="https://www.sec.gov/Archives/market-clock", content="Retained synthetic dated observation.", idempotency_key="clock-market"))["source_id"]
        repo.append_run_sources(rid, [sid])
        archived.append(sid)
        return [sid], []
    monkeypatch.setattr("backend.app.orchestration.workflow.ensure_latest_earnings", earnings)
    monkeypatch.setattr("backend.app.orchestration.workflow.acquire_financial_baseline", financial)
    monkeypatch.setattr("backend.app.research.interim_events.ensure_interim_events", interim)
    from backend.app.providers.base import ProviderError
    def stop(packet):
        raise ProviderError("capability", "Stop after capturing the frozen packet")
    provider = FakeProvider(stop)
    engine = Orchestrator(repo, FakeRegistry(provider), repo.config)
    engine.earnings_workflows = SimpleNamespace(repo=repo)
    monkeypatch.setattr(engine, "_prepare_market_evidence", market)
    task = next(row for row in repo.tasks_for_run(rid) if row["agent_id"] == "A03")
    asyncio.run(engine._execute_task(rid, task))
    assert stages == ["financial", "interim", "market"]
    assert repo.run_record(rid)["as_of"] == "2026-09-26T12:00:02Z"
    clock[0] = "2026-09-26T12:01:00Z"
    repo.control("run", rid, "retry")
    repo.control("run", rid, "run_once")
    asyncio.run(engine._execute_task(rid, repo.task(task["id"])))
    assert stages == ["financial", "interim", "market"]
    assert repo.run_record(rid)["as_of"] == "2026-09-26T12:00:02Z"
    assert archived[0] in [row["id"] for row in provider.calls[-1]["packet"]["evidence"]]


def test_absent_or_nonprimary_financials_remain_explicit_gaps(assessment_case):
    repo, rid = ordinary(assessment_case)
    sources = repo.source_packet("real", json_loads(repo.run_record(rid)["input_snapshot_json"], {})["source_ids"])
    compiled = compile_earnings_assessment(repo, rid, sources)
    for replacement in (compiled | {"fact_claims": [], "seed_proofs": []}, compiled):
        context = prepared_context(replacement, [], [source | {"primary_evidence": False} for source in sources], agent_id="A03", as_of=repo.run_record(rid)["as_of"])
        assert context["valuation_readiness"]["status"] == "needs_valuation_evidence"
        assert context["valuation_readiness"]["missing_inputs"]
        assert context["valuation_readiness"]["baseline_input"] is None


def test_prepared_aliases_cannot_be_rewritten_by_provider(assessment_case):
    repo, rid = ordinary(assessment_case)
    sources = repo.source_packet("real", json_loads(repo.run_record(rid)["input_snapshot_json"], {})["source_ids"])
    seeds = financial_seeds(compile_earnings_assessment(repo, rid, sources))
    payload = AgentOutputPayload(status="completed", title="Fixture", summary="Fixture", analysis="Fixture", fact_claims=[seeds[0]])
    with pytest.raises(ValueError, match="backend-owned"):
        merge_prepared_facts(payload, seeds)


def test_memory_only_source_cannot_disable_or_supply_financial_preparation(assessment_case):
    repo, rid = ordinary(assessment_case)
    sources = repo.source_packet("real", json_loads(repo.run_record(rid)["input_snapshot_json"], {})["source_ids"])
    sid = repo.import_evidence(ImportRequest(namespace="real", kind="evidence", title="Unrelated retained company memory",
        source_url="https://www.sec.gov/Archives/memory", content="Unrelated historical information from a different investigation.", idempotency_key="memory-extra"))["source_id"]
    memory = repo.source_packet("real", [sid])
    assert compile_earnings_assessment(repo, rid, sources + memory) is None
    compiled = compile_financial_preparation(repo, rid, sources + memory)
    assert compiled["status"] == "ready"
    assert sid not in compiled["source_bindings"]
    assert financial_seeds(compiled)[0]["value"] == "10.00"


def test_large_narrative_packet_preserves_validated_financial_proofs(assessment_case):
    repo, rid = ordinary(assessment_case)
    sources = repo.source_packet("real", json_loads(repo.run_record(rid)["input_snapshot_json"], {})["source_ids"])
    compiled = compile_earnings_assessment(repo, rid, sources, max_context_chars=1)
    assert compiled["status"] == "context_overflow" and compiled["context"] is None
    assert financial_seeds(compiled)[0]["value"] == "10.00"
    context = prepared_context(compiled, [], sources, agent_id="A03", as_of=repo.run_record(rid)["as_of"])
    assert context["valuation_readiness"]["ready_methods"] == ["eps_multiple"]


def test_loss_baseline_does_not_select_an_older_profitable_year(assessment_case):
    repo, rid = ordinary(assessment_case)
    sources = repo.source_packet("real", json_loads(repo.run_record(rid)["input_snapshot_json"], {})["source_ids"])
    compiled = deepcopy(compile_earnings_assessment(repo, rid, sources))
    compiled["fact_claims"][0]["value"] = "-1"
    context = prepared_context(compiled, [], sources, agent_id="A03", as_of=repo.run_record(rid)["as_of"])
    assert context["valuation_readiness"]["baseline_input"] is None
    assert "eps_multiple" not in context["valuation_readiness"]["ready_methods"]


@pytest.mark.parametrize("method_name,expected", [("ps_multiple", "242.00000000"), ("ev_ebitda", "22.20000000"), ("nav_multiple", "121.00000000")])
def test_ordinary_alternative_methods_from_archived_sec_financials(assessment_case, method_name, expected):
    """SEC parsing -> prepared aliases -> durable facts -> five-question target."""
    from backend.tests.test_assessment_pipeline import RELEASE
    repo, rid, wf, _, service, _ = assessment_case
    loss_release = repo.import_evidence(ImportRequest(namespace="real", kind="evidence", title="Synthetic loss-year results",
        source_url="https://www.sec.gov/Archives/edgar/data/1234/loss-results.htm", content=RELEASE.replace("10.00", "-1.00"), idempotency_key="loss-release"))["source_id"]
    specs = {"Revenues": (1000, True), "CommonStockSharesOutstanding": (10, False)}
    if method_name == "ev_ebitda":
        specs.update({"EarningsBeforeInterestTaxesDepreciationAndAmortization": (100, True), "CashAndCashEquivalentsAtCarryingValue": (20, False),
                      "LongTermDebtAndShortTermBorrowings": (40, False), "PreferredStockValue": (0, False), "MinorityInterest": (0, False)})
    if method_name == "nav_multiple":
        specs["StockholdersEquity"] = (500, False)
    gaap = {}
    for tag, (value, duration) in specs.items():
        row = {"val": value, "end": "2026-08-30", "filed": "2026-09-01", "form": "10-K", "accn": "0000001234-26-000001"}
        if duration:
            row["start"] = "2025-08-31"
        gaap[tag] = {"units": {"shares" if tag == "CommonStockSharesOutstanding" else "USD": [row]}}
    sid = repo.import_evidence(ImportRequest(namespace="real", kind="evidence", title="Synthetic SEC company facts",
        source_url="https://data.sec.gov/api/xbrl/companyfacts/CIK0000001234.json",
        content=json.dumps({"cik": 1234, "entityName": "ACME Corporation", "facts": {"us-gaap": gaap}}), idempotency_key="company-facts"))["source_id"]
    repo.append_run_sources(rid, [loss_release, sid])
    package = service.store.get(wf)["result"]
    package["source_ids"] = [loss_release]
    package["documents"]["release"] = {"status": "available", "source_id": loss_release}
    with repo.db.transaction(immediate=True) as conn:
        conn.execute("UPDATE research_workflow_runs SET result_json=? WHERE id=?", (json_dumps(package), wf))
    repo, rid = ordinary(assessment_case)

    def comparable_answer(packet):
        assert method_name in packet["valuation_readiness"]["ready_methods"]
        assert "eps_multiple" not in packet["valuation_readiness"]["ready_methods"]
        facts = {fact["metric"]: fact for fact in packet["financial_seeds"]}
        operands = {"ps_multiple": {"baseline_revenue": "revenue"}, "ev_ebitda": {"baseline_ebitda": "ebitda", "excess_cash": "cash", "debt": "debt", "preferred_claims": "preferred", "minority_interest": "minority"}, "nav_multiple": {"baseline_nav": "book equity"}}[method_name] | {"diluted_shares": "shares"}
        inputs = []
        for key, metric in operands.items():
            fact = facts[metric]
            inputs.append({"key": key, "kind": "fact", **{field: fact.get(field) for field in ("value", "unit", "currency", "basis", "period")},
                "fact_claim_ids": [fact.get("fact_id") or fact["claim_id"]], "source_refs": [fact["source_ref"]]})
        method = {"name": method_name, "period": "FY2028", "horizon_months": 24, "forecast_years": "2",
            "forecast_span_rationale": "FY2026 reported baseline to FY2028 forecast.", "currency": "USD", "inputs": inputs,
            "scenarios": {name: {"growth_rate": "0.1", "exit_multiple": "2", "growth_rationale": "Conditional synthetic operating growth.",
                "multiple_rationale": "Explicit synthetic multiple assumption."} for name in ("bear", "base", "bull")}}
        if method_name == "nav_multiple":
            method["nav_basis"] = "book_equity"
        identifier = inputs[0]["fact_claim_ids"][0]
        result = AgentOutputPayload(status="completed", research_contract="five-questions.v1", title="Alternative valuation fixture",
            summary="A loss-making company's reported operating or asset baseline supports the selected method.", analysis="Five synthetic investment questions were reviewed.",
            source_refs=[sid], candidate_briefs=[{"ticker": "ACME", "issuer": "ACME", "issuer_name": "ACME Corporation", "horizon": "24 months", "stance": "watch",
                "valuation_assumptions": {"methods": [method]},
                "key_questions": [{"key": key, "answer": "The source-bound financial record supports a conditional investment scenario.",
                    "decision_implication": "Reassess if operating performance or asset quality deteriorates.", "supporting_claim_ids": [identifier] if key == "valuation" else []}
                    for key in ("opportunity", "valuation", "catalyst", "downside", "portfolio_action")],
                "laya_response": {"position": "agree", "reason": "The supplied synthetic operands support a watch assessment.", "fact_claim_ids": [identifier], "question_keys": ["valuation"]}}])
        if (packet.get("valuation_preparation") or {}).get("status") == "ready":
            from backend.app.schemas import ValuationReview
            result.candidate_briefs[0].valuation_assumptions = None
            result.candidate_briefs[0].valuation_review = ValuationReview(status="accept", reason="The operating or asset method fits the synthetic baseline.")
        return result

    provider = FakeProvider(comparable_answer)
    engine = Orchestrator(repo, FakeRegistry(provider), repo.config, laya_runtime=_DeterministicLaya())
    for agent in ("A03", "A11"):
        task = next(task for task in repo.tasks_for_run(rid) if task["agent_id"] == agent)
        asyncio.run(engine._execute_task(rid, task))
        assert repo.task(task["id"])["status"] == "completed", repo.task(task["id"])["error"]
    from backend.app.research.case_store import CaseDecisionStore
    candidate = CaseDecisionStore(repo).current(rid, "real")["candidates"][0]
    assert candidate["valuation"]["selected_method"] == method_name
    assert candidate["valuation"]["scenarios"]["base"] == expected
    assert len(candidate["key_questions"]) == 5
