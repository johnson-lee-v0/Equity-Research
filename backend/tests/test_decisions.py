"""Focused invariants for the canonical decision and numeric services."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import math

import pytest

from backend.app.research.calculations import calculate_multi_candidate_sizing, calculate_position_size
from backend.app.research.decisions import _recommendation_blocker, build_case_decision
from backend.app.research.fact_validation import validate_fact_claim
from backend.app.research.price_scenarios import build_price_scenarios
from backend.app.schemas import AgentOutputPayload, CandidateDecisionBrief, FactClaim, PriceRange, RecommendationCheck, RedditTriage


def _source(source_id: str = "market") -> dict[str, dict[str, str]]:
    return {source_id: {"id": source_id, "source_type": "market_data", "title": "Dated quote", "content": f"{source_id.upper()} close 100 USD/share on 2026-09-01."}}


def _account(account_id: str = "acct") -> dict:
    return {
        "id": account_id,
        "account_type": "nonregistered",
        "reconciliation_status": "confirmed",
        "balances": [{"currency": "USD", "amount": "10000", "status": "confirmed", "observed_at": "2026-09-01"}],
    }


@pytest.mark.parametrize(
    ("key", "kind", "owner", "reopen_when"),
    [
        ("fact_references", "data_quality", "CIO", "Resolve each declared reference to an allowed frozen claim and rerun the gate."),
        ("fresh_material_facts", "evidence", "Researcher", "Provide dated support for each material fact and rerun the gate."),
        ("fresh_material_price", "evidence", "Researcher", "Provide a dated retained price source and rerun the gate."),
        ("instrument_identity", "evidence", "Researcher", "Resolve instrument identity with dated identity support and rerun the gate."),
        ("entry", "evidence", "Researcher", "Provide a dated retained entry price and rerun the gate."),
        ("direction", "unknown", "CIO", "Record an explicit direction and rerun the gate."),
        ("strategy", "unknown", "CIO", "Record an explicit strategy and rerun the gate."),
        ("horizon", "unknown", "CIO", "Record an explicit bounded horizon and rerun the gate."),
        ("economic_objective", "evidence", "CIO", "Resolve the directional payoff or documented hedge objective and rerun the gate."),
        ("thesis", "evidence", "CIO", "Complete the candidate-specific thesis and rerun the gate."),
        ("valuation", "evidence", "CIO", "Complete a supported valuation and rerun the gate."),
        ("payoff", "evidence", "CIO", "Complete a directional payoff and rerun the gate."),
        ("exit", "evidence", "CIO", "Record an executable exit condition and rerun the gate."),
        ("invalidation", "evidence", "CIO", "Record a measurable invalidation condition and rerun the gate."),
        ("catalyst_or_review", "evidence", "CIO", "Record a dated catalyst or review and rerun the gate."),
        ("portfolio_sizing", "sizing", "Portfolio inputs", "Complete the applicable portfolio sizing inputs and rerun the gate."),
        ("recommended_allocation", "sizing", "Portfolio inputs", "Provide a positive recommended allocation and rerun the gate."),
    ],
)
def test_recommendation_blocker_mapping_is_requirement_specific(key, kind, owner, reopen_when) -> None:
    blocker = _recommendation_blocker(RecommendationCheck(key=key, status="fail", reason="Synthetic gate failure."))

    assert (blocker.kind, blocker.owner, blocker.reopen_when) == (kind, owner, reopen_when)


def test_unknown_recommendation_check_defaults_to_cio_unknown_blocker() -> None:
    blocker = _recommendation_blocker(RecommendationCheck(key="future_check", status="fail", reason="Synthetic future requirement."))

    assert blocker.kind == "unknown"
    assert blocker.owner == "CIO"
    assert blocker.reopen_when == "Resolve the future_check requirement and rerun the gate."


def test_position_size_uses_whole_shares_and_risk_cap() -> None:
    result = calculate_position_size(
        "ABC",
        entry_price="100",
        currency="USD",
        account_id="acct",
        approved_budget="5000",
        budget_currency="USD",
        risk_budget="250",
        stop_price="90",
    )
    assert result["execution_state"] == "ready"
    assert result["shares"] == 25
    assert result["notional"] == "2500.00000000"


def test_missing_fx_is_awaiting_input_and_never_converts_by_assumption() -> None:
    result = calculate_position_size(
        "ABC",
        entry_price="100",
        currency="USD",
        account_id="acct",
        approved_budget="5000",
        budget_currency="CAD",
    )
    assert result["execution_state"] == "awaiting_input"
    assert result["shares"] is None
    assert "fx_rate:CAD_USD" in result["missing_inputs"]


def test_multi_candidate_positions_share_one_budget() -> None:
    result = calculate_multi_candidate_sizing(
        [
            {"ticker": "AAA", "entry_price": "100", "currency": "USD"},
            {"ticker": "BBB", "entry_price": "125", "currency": "USD"},
        ],
        budget="1000",
        budget_currency="USD",
        account_id="acct",
    )
    notionals = [Decimal(item["notional"]) for item in result["items"] if item.get("notional")]
    assert result["execution_state"] == "ready"
    assert sum(notionals, Decimal("0")) <= Decimal("1000")
    assert [item["shares"] for item in result["items"]] == [5, 4]


def test_watchlist_preserves_supported_entry_when_sizing_is_missing() -> None:
    payload = AgentOutputPayload(
        status="completed",
        title="Watch ABC",
        summary="Wait for a better entry.",
        analysis="The setup is interesting but account inputs are not ready.",
        ticker="ABC",
        stance="watch",
        entry_zone=PriceRange(lower="90", upper="100", currency="USD", as_of="2026-09-01", source_refs=["market"]),
    )
    result = build_case_decision("run-watch", payload, {"accounts": [], "risk_settings": {}}, _source())
    candidate = result.candidates[0]
    assert candidate.outcome == "watchlist"
    assert candidate.entry and candidate.entry.lower == "90"
    assert candidate.sizing.execution_state == "awaiting_input"
    assert candidate.watch_triggers and candidate.watch_triggers[0].type == "price"
    assert candidate.watch_triggers[0].operator == "between"
    assert candidate.watch_triggers[0].upper_threshold == "100"


def test_real_projection_routes_gate_blockers_to_requirement_owners() -> None:
    payload = {
        "status": "completed",
        "title": "Incomplete gate fixture",
        "summary": "The fixture intentionally omits publishability inputs.",
        "analysis": "The fixture is synthetic.",
        "candidate_briefs": [{"ticker": "ABC", "stance": "enter", "_fact_reference_errors": ["missing"]}],
    }
    result = build_case_decision(
        "run-gate-blocker-routing",
        payload,
        {"accounts": [], "risk_settings": {}},
        _source(),
        as_of="2026-09-01",
    )
    candidate = result.candidates[0]
    blockers = {blocker.key: blocker for blocker in candidate.material_blockers}

    assert candidate.outcome is None
    assert candidate.recommendation_gate.status == "blocked"
    for key, kind, owner, action in (
        ("direction", "unknown", "CIO", "Record an explicit direction and rerun the gate."),
        ("strategy", "unknown", "CIO", "Record an explicit strategy and rerun the gate."),
        ("horizon", "unknown", "CIO", "Record an explicit bounded horizon and rerun the gate."),
        ("fact_references", "data_quality", "CIO", "Resolve each declared reference to an allowed frozen claim and rerun the gate."),
    ):
        blocker = blockers[f"recommendation:{key}"]
        assert (blocker.kind, blocker.owner, blocker.reopen_when) == (kind, owner, action)


def test_generic_missing_input_defaults_to_researcher_owner() -> None:
    payload = {
        "status": "completed",
        "title": "Missing input fixture",
        "summary": "A dated catalyst is absent.",
        "analysis": "Synthetic projection input.",
        "candidate_briefs": [{"ticker": "ABC", "missing_inputs": ["catalyst review date", "account_snapshot"]}],
    }
    result = build_case_decision("run-generic-missing-input", payload, {"accounts": [], "risk_settings": {}}, _source(), as_of="2026-09-01")
    blockers = {blocker.key: blocker for blocker in result.candidates[0].material_blockers}

    assert blockers["input:catalyst review date"].kind == "unknown"
    assert blockers["input:catalyst review date"].owner == "Researcher"
    assert blockers["input:account_snapshot"].kind == "sizing"
    assert blockers["input:account_snapshot"].owner == "Portfolio inputs"


def test_case_builder_reserves_shared_budget_for_multiple_recommendations() -> None:
    def row(ticker: str, quote_ref: str) -> dict:
        filing_ref = f"filing-{ticker.lower()}"
        fact_id = f"fact-{ticker.lower()}"
        return {
            "ticker": ticker,
            "instrument": f"{ticker} Inc",
            "issuer": f"{ticker} Inc",
            "direction": "long",
            "strategy": "trade",
            "horizon": "3m",
            "stance": "enter",
            "entry_zone": {"lower": "100", "upper": "100", "currency": "USD", "as_of": "2026-09-01", "source_refs": [quote_ref]},
            "stop_price": "90",
            "recommended_shares": 5,
            "allocation_rationale": "Use five whole shares within the shared budget.",
            "thesis": {"variant_view": "The market underestimates operating improvement.", "market_expectation": "The market expects flat fundamentals.", "why_now": "The next review can change expectations.", "supporting_claim_ids": [fact_id], "strongest_opposing_explanation": "Demand weakness persists.", "disconfirming_evidence": ["Guidance weakens."], "decision_change_conditions": ["EPS below 5 USD/share."], "source_refs": [filing_ref], "status": "complete"},
            "valuation_assumptions": {"methods": [{"name": "eps_multiple", "inputs": [{"key": "forecast_diluted_eps", "value": "8", "unit": "USD/share", "currency": "USD", "period": "2025-12-31", "kind": "assumption", "fact_claim_ids": [fact_id], "source_refs": [filing_ref], "rationale": "Forward EPS forecast anchored to the dated baseline."}, {"key": "exit_pe", "value": "15", "unit": "multiple", "kind": "assumption", "source_refs": [filing_ref], "rationale": "Peer range assumption."}], "fact_claim_ids": [fact_id], "source_refs": [filing_ref], "forecast_eps_derivation": "Forward EPS forecast anchored to the dated baseline."}], "scenarios": {"bear": {"eps": "6", "pe": "12"}, "base": {"eps": "8", "pe": "15"}, "bull": {"eps": "10", "pe": "18"}}},
            "action_plan": {"entry_condition": "Buy at the retained quote.", "exit_condition": "Exit at the valuation target.", "invalidation_condition": "Exit below 90 USD.", "max_loss_basis": "stop", "review_at": "2026-10-01T00:00:00Z", "status": "complete"},
        }

    candidates = [row("AAA", "a"), row("BBB", "b")]
    facts = []
    sources = {**_source("a"), **_source("b")}
    for candidate in candidates:
        ticker = candidate["ticker"]
        filing_ref = f"filing-{ticker.lower()}"
        filing_content = f"{ticker} Inc reported GAAP diluted EPS 8 USD/share for the year ended 2025-12-31."
        sources[filing_ref] = {"id": filing_ref, "source_type": "filing", "primary_evidence": True, "primary_coverage": "primary_issuer", "content": filing_content, "publication_at": "2026-01-15"}
        raw_fact = {"claim": f"{ticker} diluted EPS", "value": "8", "unit": "USD/share", "currency": "USD", "period": "2025-12-31", "period_end": "2025-12-31", "basis": "GAAP diluted", "source_ref": filing_ref, "locator": "L1", "subject": f"{ticker} Inc", "metric": "EPS"}
        validation = validate_fact_claim(raw_fact, {filing_ref: filing_content}, source_metadata={filing_ref: {"source_type": "filing", "publication_at": "2026-01-15"}})
        facts.append({**raw_fact, "fact_id": f"fact-{ticker.lower()}", **{key: validation[key] for key in ("validation_status", "semantic_status", "binding_checks", "matched_excerpt", "freshness", "source_version") if key in validation}})
    payload = {"status": "completed", "title": "Two candidates", "summary": "Both setups qualify.", "analysis": "The account budget is shared.", "candidate_briefs": candidates, "fact_claims": facts}
    snapshot = {"accounts": [_account()], "risk_settings": {}, "approved_budget": "1000", "approved_budget_currency": "USD"}
    snapshot["deterministic_market"] = {"candidates": [{"ticker": ticker, "instrument_identity": {"status": "consistent", "asset_id": f"asset-{ticker.lower()}", "symbol": ticker, "name": f"{ticker} Inc"}} for ticker in ("AAA", "BBB")]}
    result = build_case_decision("run-multi", payload, snapshot, sources, as_of="2026-09-01T00:00:00Z")
    assert result.outcome == "recommend"
    assert sum(Decimal(item.sizing.notional or "0") for item in result.candidates) <= Decimal("1000")
    assert [item.sizing.shares for item in result.candidates] == [5, 5]


def test_discontinuous_scenario_is_computable_but_rejected_for_forecast_use() -> None:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    price = 1.0
    bars: list[dict[str, object]] = []
    for index in range(90):
        if index == 30:
            price *= 24.12  # approximately the reviewed ASST +2312% jump
        else:
            price *= math.exp(0.001)
        bars.append({"t": (start + timedelta(days=index)).isoformat(), "c": price})
    result = build_price_scenarios("ASST", bars, as_of="2026-01-01T00:00:00Z", path_count=100, horizon_days=21, seed=7)
    assert result["calculation_status"] == "complete"
    assert result["scenarios"]
    assert result["data_quality_status"] == "invalid"
    assert result["model_acceptance_status"] == "rejected"
    assert result["forecast_accepted"] is False
    assert result["calibration"]["discontinuities"]


def test_short_term_long_text_does_not_infer_a_short_position() -> None:
    payload = AgentOutputPayload(
        status="completed",
        title="Short-term ABC trade",
        summary="A short-term long momentum setup.",
        analysis="The holding period is short-term; direction is long.",
        ticker="ABC",
        strategy="trade",
        stance="enter",
        entry_zone=PriceRange(lower="10", upper="10", currency="USD", as_of="2026-09-01", source_refs=["market"]),
    )
    result = build_case_decision("run-direction", payload, {"accounts": [_account()], "risk_settings": {}, "approved_budget": "1000", "approved_budget_currency": "USD"}, _source(), as_of="2026-09-01")
    assert result.candidates[0].direction == "long"
    assert result.candidates[0].sizing.shares == 100


def test_proposed_policy_preview_selects_nonregistered_trade_without_claiming_approval() -> None:
    policy = {
        "status": "proposed",
        "max_positions": 10,
        "tfsa_long_term_only": True,
        "allow_tfsa_outflows": False,
        "allow_chequing_to_nonregistered": True,
        "limits": {
            "long_term": {"currency": "USD", "initial_notional": "5000", "max_notional": "7500"},
            "trade": {"currency": "USD", "initial_notional": "2500", "max_notional": "5000", "planned_loss_limit": "250"},
        },
    }
    payload = AgentOutputPayload(
        status="completed",
        title="Trade preview",
        summary="The trade setup has a defined stop.",
        analysis="The proposed trade still needs funding confirmation.",
        candidate_briefs=[
            CandidateDecisionBrief(
                ticker="ABC",
                strategy="trade",
                stance="enter",
                entry_zone=PriceRange(lower="100", upper="100", currency="USD", as_of="2026-09-01", source_refs=["market"]),
                stop_price="90",
                invalidation_conditions=["Exit below 90 USD."],
            )
        ],
    )
    snapshot = {
        "accounts": [
            {"id": "tfsa", "account_type": "tfsa", "reconciliation_status": "unconfirmed", "balances": []},
            {"id": "nr", "account_type": "nonregistered", "reconciliation_status": "unconfirmed", "balances": []},
        ],
        "portfolio_policy": policy,
        "risk_settings": {},
    }
    decision = build_case_decision("run-policy-preview", payload, snapshot, _source(), as_of="2026-09-01")
    candidate = decision.candidates[0]
    assert candidate.strategy == "trade"
    assert candidate.sizing.account_id == "nr"
    assert candidate.sizing.execution_state == "awaiting_input"
    assert candidate.sizing.shares == 25
    assert candidate.sizing.notional == "2500.00000000"
    assert candidate.sizing.proposed_budget == "2500.00000000"
    assert candidate.sizing.approved_budget is None
    assert candidate.outcome is None


def test_explicit_scenario_assessment_is_required_to_accept_code_forecast() -> None:
    scenario = {
        "code_version": "fixture",
        "method": "fixture",
        "status": "complete",
        "ticker": "ABC",
        "currency": "USD",
        "as_of": "2026-09-01",
        "input_hash": "input",
        "result_hash": "result",
        "calculation_status": "complete",
        "data_quality_status": "valid",
        "model_acceptance_status": "accepted",
        "forecast_accepted": True,
        "source_refs": ["market"],
    }
    payload = AgentOutputPayload(
        status="completed",
        title="Scenario review",
        summary="A bounded setup.",
        analysis="The scenario is illustrative.",
        ticker="ABC",
        stance="watch",
        watch_triggers=[{
            "type": "catalyst",
            "condition": "Guidance confirms demand.",
            "operator": "occurs",
            "catalyst": "Guidance confirms demand",
            "reopen_when": "Review the case after the guidance update.",
        }],
        simulation_snapshot=scenario,
    )
    no_assessment = build_case_decision("run-scenario-unknown", payload, {"accounts": [], "risk_settings": {}}, _source(), as_of="2026-09-01")
    assert no_assessment.candidates[0].forecast is not None
    assert no_assessment.candidates[0].forecast.scenario_assessment == "not_assessed"
    assert no_assessment.candidates[0].forecast.accepted is False

    usable = payload.model_copy(update={"scenario_assessment": "usable"})
    accepted = build_case_decision("run-scenario-usable", usable, {"accounts": [], "risk_settings": {}}, _source(), as_of="2026-09-01")
    assert accepted.candidates[0].forecast is not None
    assert accepted.candidates[0].forecast.scenario_assessment == "usable"
    assert accepted.candidates[0].forecast.accepted is True


def test_reddit_triage_keeps_bounded_issuer_lead_and_author_evidence_type() -> None:
    triage = RedditTriage(classification="yolo_ticker", reason="The issuer is named in the title.", issuer_name="Bloom Energy")
    assert triage.issuer_name == "Bloom Energy"
