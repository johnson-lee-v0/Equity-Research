"""Focused financial and recommendation-gate invariants for v2 cases."""
from decimal import Decimal

from backend.app.research.calculations import calculate_position_size
from backend.app.research.decisions import build_case_decision
from backend.app.research.fact_validation import validate_fact_claim
from backend.app.research.portfolio_risk import build_opportunity_cost, build_portfolio_context
from backend.app.research.valuation import build_valuation, calculate_dcf, calculate_eps_multiple, calculate_payoff


def _account() -> dict:
    return {
        "id": "nr",
        "account_type": "nonregistered",
        "reconciliation_status": "confirmed",
        "balances": [{"currency": "USD", "amount": "10000", "status": "confirmed", "observed_at": "2026-09-16"}],
    }


def test_short_range_uses_lower_risk_entry_and_upper_notional_price() -> None:
    result = calculate_position_size(
        "SYN",
        entry_price="100",
        entry_lower_price="90",
        entry_upper_price="100",
        currency="USD",
        account_id="nr",
        snapshot={"accounts": [_account()]},
        direction="short",
        borrow_available=True,
        short_permission=True,
        borrow_cost_status="confirmed",
        margin_terms_confirmed=True,
        short_budget="10000",
        budget_currency="USD",
        risk_budget="1000",
        stop_price="110",
        policy_status="approved",
    )
    assert result["risk_entry_price"] == "90.00000000"
    assert result["notional_cap_price"] == "100.00000000"
    assert result["planned_loss_per_share"] == "20.00000000"
    assert result["shares"] == 50
    assert Decimal(result["planned_loss"]) <= Decimal("1000")


def test_short_range_literal_loss_and_notional_caps_never_use_upper_bound_for_risk() -> None:
    # [90, 100], stop 110, loss allowance 100 and notional cap 1000:
    # risk allows floor(100 / (110 - 90)) = 5 shares; using 100 as the
    # adverse entry would incorrectly return 10.
    result = calculate_position_size(
        "SYN",
        entry_price="100",
        entry_lower_price="90",
        entry_upper_price="100",
        currency="USD",
        account_id="nr",
        snapshot={"accounts": [_account()]},
        direction="short",
        borrow_available=True,
        short_permission=True,
        borrow_cost_status="confirmed",
        margin_terms_confirmed=True,
        short_budget="1000",
        budget_currency="USD",
        risk_budget="100",
        stop_price="110",
        policy_status="approved",
    )
    assert result["shares"] == 5
    assert result["planned_loss_per_share"] == "20.00000000"
    assert result["planned_loss"] == "100.00000000"


def test_short_stop_must_clear_upper_bound() -> None:
    result = calculate_position_size(
        "SYN", entry_price="100", entry_lower_price="90", entry_upper_price="100", currency="USD", account_id="nr", snapshot={"accounts": [_account()]}, direction="short", borrow_available=True, short_permission=True, borrow_cost_status="confirmed", margin_terms_confirmed=True, short_budget="10000", budget_currency="USD", risk_budget="1000", stop_price="99", policy_status="approved"
    )
    assert result["shares"] is None
    assert "protective_stop_price" in result["missing_inputs"]


def test_short_sizing_requires_permission_carry_and_margin_terms() -> None:
    result = calculate_position_size(
        "SYN", entry_price="100", entry_lower_price="90", entry_upper_price="100", currency="USD", account_id="nr", snapshot={"accounts": [_account()]}, direction="short", borrow_available=True, short_budget="1000", budget_currency="USD", risk_budget="100", stop_price="110", policy_status="approved"
    )
    assert result["execution_state"] == "awaiting_input"
    assert {"short_sale_permission", "borrow_terms_or_cost_status", "margin_terms_confirmation"}.issubset(result["missing_inputs"])


def test_long_range_uses_upper_adverse_entry_and_lower_valid_stop() -> None:
    result = calculate_position_size(
        "SYN",
        entry_price="100",
        entry_lower_price="90",
        entry_upper_price="100",
        currency="USD",
        account_id="nr",
        snapshot={"accounts": [_account()]},
        direction="long",
        approved_budget="10000",
        budget_currency="USD",
        risk_budget="1000",
        stop_price="80",
        policy_status="approved",
    )
    assert result["risk_entry_price"] == "100.00000000"
    assert result["planned_loss_per_share"] == "20.00000000"
    assert result["shares"] == 50


def test_long_stop_must_clear_lower_bound() -> None:
    result = calculate_position_size(
        "SYN", entry_price="100", entry_lower_price="90", entry_upper_price="100", currency="USD", account_id="nr", snapshot={"accounts": [_account()]}, direction="long", approved_budget="1000", budget_currency="USD", risk_budget="100", stop_price="95", policy_status="approved"
    )
    assert result["shares"] is None
    assert "protective_stop_price" in result["missing_inputs"]


def test_dcf_bridge_rejects_growth_at_discount_rate_and_preserves_signs() -> None:
    invalid = calculate_dcf([100, 100], "0.08", "0.08", excess_cash="0", debt="10", preferred_claims="0", minority_interest="0", diluted_shares="10", source_refs=["filing"])
    assert invalid["status"] == "unavailable"
    assert "Terminal growth" in invalid["reasons"][0]
    valid = calculate_dcf([100], "0.10", "0.02", excess_cash="0", debt="1500", preferred_claims="0", minority_interest="0", diluted_shares="10", source_refs=["filing"])
    assert valid["status"] == "partial"
    assert valid["intermediate_results"]["negative_equity"] is True
    assert valid["output_prices"] == {}


def test_short_payoff_reverses_price_return_and_keeps_costs_unknown() -> None:
    payoff = calculate_payoff({"scenarios": {"bear": "40", "base": "60", "bull": "90"}}, "50", direction="short", stop_price="70")
    assert payoff["scenarios"][0]["price_return"] == "0.20000000"
    assert payoff["scenarios"][1]["price_return"] == "-0.20000000"
    assert payoff["scenarios"][0]["net_return"] is None


def test_portfolio_context_consumes_long_cash_but_does_not_credit_short_proceeds() -> None:
    snapshot = {"snapshot_id": "p", "base_currency": "USD", "positions": [], "accounts": [_account()]}
    long_context = build_portfolio_context(snapshot, candidate={"ticker": "LONG", "direction": "long", "notional": "1000", "currency": "USD"})
    short_context = build_portfolio_context(snapshot, candidate={"ticker": "SHORT", "direction": "short", "notional": "1000", "currency": "USD", "borrow_available": True})
    assert long_context["after"]["cash_headroom"] == "9000.00000000"
    assert long_context["after"]["total_value"] == "10000.00000000"
    assert short_context["after"]["cash_headroom"] == "10000.00000000"
    assert short_context["after"]["gross_short"] == "1000.00000000"


def _opportunity_snapshot(**account_updates: object) -> dict:
    account = {
        "id": "nr",
        "base_currency": "USD",
        "account_type": "nonregistered",
        "reconciliation_status": "confirmed",
        "observed_at": "2026-09-16",
        "interest_rate": "0.05",
        "interest_rate_period": "annual/365",
        "interest_compounding": "simple",
        "balances": [{"currency": "USD", "amount": "10000", "status": "confirmed", "observed_at": "2026-09-16"}],
    }
    account.update(account_updates)
    return {"as_of": "2026-09-17", "accounts": [account]}


def test_opportunity_cost_uses_proposed_cash_principal_and_keeps_benchmark_forward_return_unknown() -> None:
    result = build_opportunity_cost(
        _opportunity_snapshot(),
        principal="2500",
        currency="USD",
        account_id="nr",
        horizon="1y",
        as_of="2026-09-17",
        benchmark_ticker="SPY",
        benchmark_rationale="Broad-market alternative.",
    )
    assert result["status"] == "complete"
    assert result["principal"] == "2500.00000000"
    assert result["expected_return"] == "0.05000000"
    assert result["expected_gain"] == "125.00000000"
    assert result["expected_value"] == "2625.00000000"
    assert result["benchmark_ticker"] == "SPY"
    assert result["benchmark_rationale"] == "Broad-market alternative."
    assert result["benchmark_expected_return"] is None
    assert "available_cash" not in result


def test_opportunity_cost_keeps_explicit_percent_rates_and_rejects_malformed_expiry() -> None:
    half_percent = build_opportunity_cost(
        _opportunity_snapshot(interest_rate="0.5%"),
        principal="1000",
        currency="USD",
        account_id="nr",
        horizon="1y",
        as_of="2026-09-17",
    )
    assert half_percent["status"] == "complete"
    assert half_percent["expected_gain"] == "5.00000000"
    malformed_expiry = build_opportunity_cost(
        _opportunity_snapshot(interest_rate_expires_at="not-a-date"),
        principal="1000",
        currency="USD",
        account_id="nr",
        horizon="1y",
        as_of="2026-09-17",
    )
    assert malformed_expiry["status"] == "unavailable"
    assert "interest_rate_term" in malformed_expiry["missing_inputs"]


def test_opportunity_cost_requires_rate_currency_binding_and_does_not_double_compound_apy() -> None:
    multi_currency = _opportunity_snapshot(
        balances=[
            {"currency": "USD", "amount": "10000", "status": "confirmed", "observed_at": "2026-09-16"},
            {"currency": "CAD", "amount": "10000", "status": "confirmed", "observed_at": "2026-09-16"},
        ]
    )
    mismatch = build_opportunity_cost(multi_currency, principal="1000", currency="CAD", account_id="nr", horizon="1y", as_of="2026-09-17")
    assert mismatch["status"] == "unavailable"
    assert "interest_rate_currency" in mismatch["missing_inputs"]
    apy = build_opportunity_cost(
        _opportunity_snapshot(interest_rate_period="apy", interest_compounding="monthly"),
        principal="1000",
        currency="USD",
        account_id="nr",
        horizon="1y",
        as_of="2026-09-17",
    )
    assert apy["status"] == "unavailable"
    assert apy["expected_return"] is None
    assert "interest_compounding" in apy["missing_inputs"]


def test_opportunity_cost_is_unavailable_for_missing_terms_future_rate_or_currency_mismatch() -> None:
    missing_terms = build_opportunity_cost(
        _opportunity_snapshot(interest_rate_period=None),
        principal="2500",
        currency="USD",
        account_id="nr",
        horizon="1y",
        as_of="2026-09-17",
    )
    assert missing_terms["status"] == "unavailable"
    assert missing_terms["expected_return"] is None
    assert "interest_rate_period" in missing_terms["missing_inputs"]
    future_rate = build_opportunity_cost(
        _opportunity_snapshot(observed_at="2026-09-18"),
        principal="2500",
        currency="USD",
        account_id="nr",
        horizon="1y",
        as_of="2026-09-17",
    )
    assert future_rate["status"] == "unavailable"
    assert "interest_rate_observed_at" in future_rate["missing_inputs"]
    mismatch = build_opportunity_cost(
        _opportunity_snapshot(balances=[{"currency": "CAD", "amount": "10000", "status": "confirmed", "observed_at": "2026-09-16"}], base_currency="CAD"),
        principal="2500",
        currency="USD",
        account_id="nr",
        horizon="1y",
        as_of="2026-09-17",
    )
    assert mismatch["status"] == "unavailable"
    assert "cash_currency" in mismatch["missing_inputs"]


def test_opportunity_cost_does_not_use_short_notional_as_cash_principal() -> None:
    result = build_opportunity_cost(_opportunity_snapshot(), principal="2500", currency="USD", direction="short", account_id="nr", horizon="1y", as_of="2026-09-17")
    assert result["status"] == "unavailable"
    assert "short_collateral_capital_basis" in result["missing_inputs"]


def _v2_payload(**updates: object) -> dict:
    candidate = {
        "ticker": "ABC",
        "instrument": "ABC Inc",
        "direction": "long",
        "strategy": "trade",
        "horizon": "3m",
        "stance": "enter",
        "entry_zone": {"lower": "90", "upper": "100", "currency": "USD", "as_of": "2026-09-16", "source_refs": ["market"]},
        "stop_price": "80",
        "recommended_shares": 5,
        "allocation_rationale": "Keep planned loss below the trade risk budget.",
        "thesis": {"variant_view": "The market underestimates margin recovery.", "market_expectation": "Consensus expects flat margins.", "why_now": "The next earnings date can change expectations.", "supporting_claim_ids": ["fact-eps"], "strongest_opposing_explanation": "Demand weakness prevents recovery.", "disconfirming_evidence": ["Guidance lowers margin."], "decision_change_conditions": ["Margin below 20%"], "source_refs": ["filing"], "status": "complete"},
        "valuation_assumptions": {"methods": [{"name": "eps_multiple", "inputs": [{"key": "forecast_diluted_eps", "value": "8", "unit": "USD/share", "currency": "USD", "period": "2025-12-31", "kind": "assumption", "fact_claim_ids": ["fact-eps"], "source_refs": ["filing"], "rationale": "Forecast FY2026 diluted EPS from the dated FY2025 GAAP diluted baseline and the margin recovery thesis."}, {"key": "exit_pe", "value": "15", "unit": "multiple", "kind": "assumption", "source_refs": ["filing"], "rationale": "A conservative peer range for the forecast horizon."}], "fact_claim_ids": ["fact-eps"], "forecast_eps_derivation": "Forecast FY2026 diluted EPS from the dated FY2025 GAAP diluted baseline and the margin recovery thesis.", "basis": "GAAP", "share_basis": "diluted", "source_refs": ["filing"]}], "scenarios": {"bear": {"eps": "6", "pe": "12"}, "base": {"eps": "8", "pe": "15"}, "bull": {"eps": "10", "pe": "18"}}},
        "action_plan": {"entry_condition": "Buy inside the supported range.", "exit_condition": "Exit at valuation target.", "invalidation_condition": "Exit below 80 USD.", "max_loss_basis": "stop", "catalyst_events": [{"description": "Earnings", "event_date": "2026-10-25", "date_kind": "expected"}], "review_at": "2026-10-30T00:00:00Z", "status": "complete"},
    }
    candidate.update(updates)
    raw_fact = {"claim": "ABC diluted EPS", "value": "8", "unit": "USD/share", "currency": "USD", "period": "2025-12-31", "period_end": "2025-12-31", "basis": "GAAP diluted", "source_ref": "filing", "locator": "L1", "subject": "ABC Inc", "metric": "EPS"}
    validation = validate_fact_claim(raw_fact, {"filing": "ABC Inc reported GAAP diluted EPS 8 USD/share for the year ended 2025-12-31."}, source_metadata={"filing": {"source_type": "filing", "publication_at": "2026-01-15"}})
    fact = {**raw_fact, "fact_id": "fact-eps", **{key: validation[key] for key in ("validation_status", "semantic_status", "binding_checks", "matched_excerpt", "freshness", "source_version") if key in validation}}
    return {"status": "completed", "summary": "Complete synthetic case", "analysis": "A differentiated view with a coherent opposing explanation.", "fact_claims": [fact], "candidate_briefs": [candidate]}


def _snapshot() -> dict:
    return {"snapshot_id": "snap", "accounts": [_account()], "portfolio_policy": {"status": "approved", "max_positions": 10, "limits": {"trade": {"currency": "USD", "initial_notional": "2500", "max_notional": "5000", "planned_loss_limit": "250"}}}, "risk_settings": {}, "approved_budget": "2500", "approved_budget_currency": "USD", "deterministic_market": {"candidates": [{"ticker": "ABC", "instrument_identity": {"status": "consistent", "asset_id": "asset-abc", "symbol": "ABC", "name": "ABC Inc"}}]}}


def test_complete_v2_case_passes_and_missing_material_block_blocks() -> None:
    sources = {"market": {"id": "market", "source_type": "market_data", "content": "ABC close 100 USD/share on 2026-09-16."}, "filing": {"id": "filing", "source_type": "filing", "primary_evidence": True, "primary_coverage": "primary_issuer", "content": "ABC Inc reported GAAP diluted EPS 8 USD/share for the year ended 2025-12-31.", "publication_at": "2026-01-15"}}
    complete = build_case_decision("complete", _v2_payload(), _snapshot(), sources, as_of="2026-09-17T00:00:00Z").candidates[0]
    assert complete.outcome == "recommend"
    assert complete.recommendation_gate.status == "pass"
    for key in ("thesis", "valuation_assumptions", "action_plan"):
        payload = _v2_payload(**{key: None})
        blocked = build_case_decision(f"missing-{key}", payload, _snapshot(), sources, as_of="2026-09-17T00:00:00Z").candidates[0]
        assert blocked.recommendation_gate.status == "blocked"
        assert blocked.outcome is None


def test_gate_blocks_zero_allocation_invalid_review_and_negative_base_payoff() -> None:
    sources = {"market": {"id": "market", "source_type": "market_data", "content": "ABC close 100 USD/share on 2026-09-16."}, "filing": {"id": "filing", "source_type": "filing", "primary_evidence": True, "primary_coverage": "primary_issuer", "content": "ABC Inc reported GAAP diluted EPS 8 USD/share for the year ended 2025-12-31.", "publication_at": "2026-01-15"}}
    zero = _v2_payload(recommended_shares=0)
    result = build_case_decision("zero", zero, _snapshot(), sources, as_of="2026-09-17T00:00:00Z").candidates[0]
    assert result.recommendation_gate.status == "blocked"
    assert result.outcome is None
    invalid_review = _v2_payload(action_plan={"entry_condition": "Buy inside the supported range.", "exit_condition": "Exit at valuation target.", "invalidation_condition": "Exit below margin threshold.", "max_loss_basis": "stop", "review_at": "whenever", "status": "complete"})
    result = build_case_decision("invalid-review", invalid_review, _snapshot(), sources, as_of="2026-09-17T00:00:00Z").candidates[0]
    assert "catalyst_or_review" in result.recommendation_gate.missing_inputs
    negative = _v2_payload(valuation_assumptions={"methods": [{"name": "eps_multiple", "forecast_eps": "8", "exit_multiple": "15", "fact_claim_ids": ["fact-eps"], "source_refs": ["filing"]}], "scenarios": {"bear": {"eps": "5", "pe": "15"}, "base": {"eps": "6", "pe": "15"}, "bull": {"eps": "7", "pe": "15"}}})
    result = build_case_decision("negative-base", negative, _snapshot(), sources, as_of="2026-09-17T00:00:00Z").candidates[0]
    assert "economic_objective" in result.recommendation_gate.missing_inputs


def test_build_valuation_rejects_provider_support_and_empty_source() -> None:
    result = build_valuation({"methods": [{"name": "eps_multiple", "forecast_eps": "99999", "exit_multiple": "200", "source_refs": ["filing"], "supported": True}]}, currency="USD", source_records={"filing": {"id": "filing", "content": ""}})
    assert result["status"] == "partial"
    assert result["methods"][0]["supported"] is False
    assert result["scenarios"] == {}


def test_typed_valuation_input_cannot_be_overridden_by_a_conflicting_scalar() -> None:
    sources = {"market": {"id": "market", "source_type": "market_data", "content": "ABC close 100 USD/share on 2026-09-16."}, "filing": {"id": "filing", "source_type": "filing", "primary_evidence": True, "primary_coverage": "primary_issuer", "content": "ABC Inc reported GAAP diluted EPS 8 USD/share for the year ended 2025-12-31.", "publication_at": "2026-01-15"}}
    payload = _v2_payload()
    method = payload["candidate_briefs"][0]["valuation_assumptions"]["methods"][0]
    method["forecast_eps"] = "999"
    payload["candidate_briefs"][0]["valuation_assumptions"]["scenarios"] = {}
    candidate = build_case_decision("typed-scalar-conflict", payload, _snapshot(), sources, as_of="2026-09-17T00:00:00Z").candidates[0]
    assert candidate.outcome is None
    assert candidate.recommendation_gate.status == "blocked"
    assert candidate.valuation.scenarios == {}
    assert any("conflicting" in reason.casefold() for reason in candidate.valuation.methods[0].reasons)


def test_scenario_aliases_ignore_schema_none_and_reject_disagreement() -> None:
    result = calculate_eps_multiple(
        "8",
        "15",
        scenarios={
            "bear": {"forecast_eps": None, "eps": "6", "exit_multiple": None, "pe": "12"},
            "base": {"forecast_eps": None, "eps": "8", "exit_multiple": None, "pe": "15"},
            "bull": {"forecast_eps": None, "eps": "10", "exit_multiple": None, "pe": "18"},
        },
    )
    assert result["output_prices"] == {"bear": "72.00000000", "base": "120.00000000", "bull": "180.00000000"}
    conflict = calculate_eps_multiple("8", "15", scenarios={"base": {"forecast_eps": "8", "eps": "9", "pe": "15"}})
    assert conflict["output_prices"] == {}
    assert any("conflicting aliases" in reason for reason in conflict["reasons"])


def test_thesis_fact_from_another_issuer_cannot_support_this_candidate() -> None:
    sources = {"market": {"id": "market", "source_type": "market_data", "content": "ABC close 100 USD/share on 2026-09-16."}, "filing": {"id": "filing", "source_type": "filing", "primary_evidence": True, "primary_coverage": "primary_issuer", "content": "ABC Inc reported GAAP diluted EPS 8 USD/share for the year ended 2025-12-31.", "publication_at": "2026-01-15"}, "other-filing": {"id": "other-filing", "source_type": "filing", "primary_evidence": True, "primary_coverage": "primary_issuer", "content": "OTHER Inc reported GAAP diluted EPS 8 USD/share for the year ended 2025-12-31.", "publication_at": "2026-01-15"}}
    payload = _v2_payload()
    raw_fact = {"claim": "OTHER diluted EPS", "value": "8", "unit": "USD/share", "currency": "USD", "period": "2025-12-31", "period_end": "2025-12-31", "basis": "GAAP diluted", "source_ref": "other-filing", "locator": "L1", "subject": "OTHER Inc", "metric": "EPS"}
    validation = validate_fact_claim(raw_fact, {"other-filing": sources["other-filing"]["content"]}, source_metadata={"other-filing": {"source_type": "filing", "publication_at": "2026-01-15"}})
    payload["fact_claims"].append({**raw_fact, "fact_id": "other", **{key: validation[key] for key in ("validation_status", "semantic_status", "binding_checks", "matched_excerpt", "freshness", "source_version") if key in validation}})
    payload["candidate_briefs"][0]["thesis"]["supporting_claim_ids"] = ["other"]
    payload["candidate_briefs"][0]["thesis"]["source_refs"] = ["other-filing"]
    candidate = build_case_decision("wrong-issuer-thesis", payload, _snapshot(), sources, as_of="2026-09-17T00:00:00Z").candidates[0]
    assert candidate.outcome is None
    assert candidate.recommendation_gate.status == "blocked"
    assert "thesis" in candidate.recommendation_gate.missing_inputs


def test_eps_metadata_and_typed_multiple_rationale_are_required() -> None:
    sources = {"market": {"id": "market", "source_type": "market_data", "content": "ABC close 100 USD/share on 2026-09-16."}, "filing": {"id": "filing", "source_type": "filing", "primary_evidence": True, "primary_coverage": "primary_issuer", "content": "ABC Inc reported GAAP diluted EPS 8 USD/share for the year ended 2025-12-31.", "publication_at": "2026-01-15"}}
    payload = _v2_payload()
    method = payload["candidate_briefs"][0]["valuation_assumptions"]["methods"][0]
    method.pop("basis", None)
    method.pop("share_basis", None)
    method.pop("forecast_eps_derivation", None)
    method["inputs"][0]["period"] = None
    method["inputs"][0]["rationale"] = ""
    method["inputs"][1]["rationale"] = ""
    candidate = build_case_decision("missing-eps-metadata", payload, _snapshot(), sources, as_of="2026-09-17T00:00:00Z").candidates[0]
    assert candidate.outcome is None
    assert candidate.recommendation_gate.status == "blocked"
    assert candidate.valuation.scenarios == {}
    assert any("EPS input requirements" in reason for reason in candidate.valuation.methods[0].reasons)


def test_nav_quote_is_not_a_supported_fair_value_baseline() -> None:
    result = build_valuation({"methods": [{"name": "nav", "nav_per_unit": "100", "fact_claim_ids": ["price"], "source_refs": ["quote"]}], "scenarios": {"bear": "100", "base": "120", "bull": "140"}}, asset_class="etf", currency="USD", issuer="ABC Fund", as_of="2026-09-17", validated_facts=[{"fact_id": "price", "value": "100", "unit": "USD/share", "currency": "USD", "period": "2026-09-16", "period_end": "2026-09-16", "metric": "Price", "subject": "ABC Fund", "source_ref": "quote", "validation_status": "validated", "semantic_status": "supported"}], source_records={"quote": {"id": "quote", "source_type": "market_data", "content": "ABC Fund closing price 100 USD/share on 2026-09-16.", "observed_at": "2026-09-16"}})
    assert result["status"] == "partial"
    assert result["methods"][0]["supported"] is False
    assert any("metric" in reason.casefold() and "nav" in reason.casefold() for reason in result["methods"][0]["reasons"])


def test_dcf_preserves_relative_year_offsets_and_explicit_scenarios() -> None:
    irregular = calculate_dcf({"1": "100", "3": "100"}, "0.10", "0.02", excess_cash="0", debt="0", preferred_claims="0", minority_interest="0", diluted_shares="10")
    assert [row["year"] for row in irregular["intermediate_results"]["discounted_fcf"]] == ["1", "3"]
    scenarios = calculate_dcf([100, 100], "0.10", "0.02", excess_cash="0", debt="0", preferred_claims="0", minority_interest="0", diluted_shares="10", scenarios={"bear": {"forecast_fcf": [80, 80], "discount_rate": "0.11", "terminal_growth": "0.01"}, "bull": {"forecast_fcf": [120, 120], "discount_rate": "0.09", "terminal_growth": "0.03"}})
    assert set(scenarios["output_prices"]) == {"bear", "base", "bull"}
    prices = {key: Decimal(value) for key, value in scenarios["output_prices"].items()}
    assert prices["bear"] < prices["base"] < prices["bull"]
    incomplete = calculate_dcf(
        [100, 100],
        "0.10",
        "0.02",
        excess_cash="0",
        debt="0",
        preferred_claims="0",
        minority_interest="0",
        diluted_shares="10",
        scenarios={"base": {"forecast_fcf": [100, 100]}},
    )
    assert incomplete["status"] == "partial"
    assert any("scenario set is incomplete" in reason for reason in incomplete["reasons"])


def test_supported_valuation_emits_bounded_sensitivity_grids() -> None:
    sources = {"filing": {"id": "filing", "source_type": "filing", "primary_evidence": True, "primary_coverage": "primary_issuer", "content": "ABC Inc reported free cash flow 100 USD for 2025 and GAAP diluted EPS 8 USD/share for the year ended 2025-12-31.", "publication_at": "2026-01-15"}}
    fact = {"fact_id": "fcf", "value": "100", "unit": "USD", "currency": "USD", "period": "2025-12-31", "period_end": "2025-12-31", "metric": "Free Cash Flow", "subject": "ABC Inc", "source_ref": "filing", "validation_status": "validated", "semantic_status": "supported"}
    dcf = build_valuation({"methods": [{"name": "dcf", "inputs": [{"key": "forecast_unlevered_fcf", "value": {"1": "100", "2": "100"}, "kind": "assumption", "rationale": "Forward FCF schedule anchored to the retained filing."}, {"key": "discount_rate", "value": "0.10", "kind": "assumption", "rationale": "Capital cost assumption."}, {"key": "terminal_growth", "value": "0.02", "kind": "assumption", "rationale": "Long-run growth assumption."}, {"key": "excess_cash", "value": "0", "kind": "assumption", "rationale": "No excess cash adjustment."}, {"key": "debt", "value": "0", "kind": "assumption", "rationale": "No debt adjustment."}, {"key": "preferred_claims", "value": "0", "kind": "assumption", "rationale": "No preferred claims."}, {"key": "minority_interest", "value": "0", "kind": "assumption", "rationale": "No minority interest."}, {"key": "diluted_shares", "value": "10", "kind": "assumption", "rationale": "Diluted share assumption."}], "fact_claim_ids": ["fcf"], "source_refs": ["filing"]}]}, currency="USD", issuer="ABC Inc", as_of="2026-09-17", validated_facts=[fact], source_records=sources)
    assert dcf["status"] == "complete"
    assert {item["parameter"] for item in dcf["sensitivities"]} == {"discount_rate", "terminal_growth"}
    assert all(len(item["values"]) == 3 for item in dcf["sensitivities"])


def test_material_valuation_rejects_unverified_external_source_classification() -> None:
    result = build_valuation(
        {"methods": [{"name": "eps_multiple", "forecast_eps": "8", "exit_multiple": "15", "fact_claim_ids": ["fact"], "source_refs": ["blog"]}]},
        currency="USD",
        issuer="ABC Inc",
        as_of="2026-09-17",
        validated_facts=[{"fact_id": "fact", "value": "8", "unit": "USD/share", "currency": "USD", "period": "2025-12-31", "period_end": "2025-12-31", "metric": "EPS", "subject": "ABC Inc", "source_ref": "blog", "validation_status": "validated", "semantic_status": "supported"}],
        source_records={"blog": {"id": "blog", "source_type": "filing", "title": "ABC 10-K summary", "content": "ABC Inc reported diluted EPS 8 USD/share for the year ended 2025-12-31.", "publication_at": "2026-01-15", "url": "https://example.com/abc"}},
    )
    assert result["methods"][0]["supported"] is False
    assert any("primary coverage" in reason.casefold() for reason in result["methods"][0]["reasons"])


def test_valuation_rejects_currency_and_metric_misbinding() -> None:
    jpy_claim = {"claim": "ABC diluted EPS", "value": "5", "unit": "JPY/share", "currency": "JPY", "period": "2025-12-31", "period_end": "2025-12-31", "source_ref": "jpy-filing", "locator": "L1", "subject": "ABC Inc", "metric": "EPS"}
    jpy_source = "ABC Inc reported diluted EPS 5 JPY/share for the year ended 2025-12-31."
    jpy_validation = validate_fact_claim(jpy_claim, {"jpy-filing": jpy_source}, source_metadata={"jpy-filing": {"source_type": "filing", "publication_at": "2026-01-15"}})
    jpy_fact = {**jpy_claim, "fact_id": "jpy-eps", **{key: jpy_validation[key] for key in ("validation_status", "semantic_status", "binding_checks", "matched_excerpt")}}
    jpy_result = build_valuation({"methods": [{"name": "eps_multiple", "inputs": [{"key": "forecast_eps", "value": "5", "unit": "JPY/share", "currency": "JPY", "period": "2025-12-31", "kind": "fact", "fact_claim_ids": ["jpy-eps"]}, {"key": "exit_multiple", "value": "20", "unit": "multiple", "kind": "assumption"}], "fact_claim_ids": ["jpy-eps"], "source_refs": ["jpy-filing"]}]}, currency="USD", issuer="ABC Inc", as_of="2026-09-17", validated_facts=[jpy_fact], source_records={"jpy-filing": {"id": "jpy-filing", "source_type": "filing", "primary_evidence": True, "primary_coverage": "primary_issuer", "content": jpy_source, "publication_at": "2026-01-15"}})
    assert jpy_result["methods"][0]["supported"] is False
    assert any("JPY" in reason for reason in jpy_result["methods"][0]["reasons"])

    revenue_claim = {"claim": "ABC revenue", "value": "5", "unit": "USD", "currency": "USD", "period": "2025-12-31", "period_end": "2025-12-31", "source_ref": "revenue-filing", "locator": "L1", "subject": "ABC Inc", "metric": "Revenue"}
    revenue_source = "ABC Inc reported revenue 5 USD for the year ended 2025-12-31."
    revenue_validation = validate_fact_claim(revenue_claim, {"revenue-filing": revenue_source}, source_metadata={"revenue-filing": {"source_type": "filing", "publication_at": "2026-01-15"}})
    revenue_fact = {**revenue_claim, "fact_id": "revenue", **{key: revenue_validation[key] for key in ("validation_status", "semantic_status", "binding_checks", "matched_excerpt")}}
    revenue_result = build_valuation({"methods": [{"name": "eps_multiple", "inputs": [{"key": "forecast_eps", "value": "5", "unit": "USD/share", "currency": "USD", "period": "2025-12-31", "kind": "fact", "fact_claim_ids": ["revenue"]}, {"key": "exit_multiple", "value": "20", "unit": "multiple", "kind": "assumption"}], "fact_claim_ids": ["revenue"], "source_refs": ["revenue-filing"]}]}, currency="USD", issuer="ABC Inc", as_of="2026-09-17", validated_facts=[revenue_fact], source_records={"revenue-filing": {"id": "revenue-filing", "source_type": "filing", "primary_evidence": True, "primary_coverage": "primary_issuer", "content": revenue_source, "publication_at": "2026-01-15"}})
    assert revenue_result["methods"][0]["supported"] is False
    assert any("metric" in reason.casefold() for reason in revenue_result["methods"][0]["reasons"])


def test_dcf_normalizes_calendar_periods_and_declares_discount_convention() -> None:
    year_end = calculate_dcf({"2026": "100", "2027": "100"}, "0.10", "0.02", excess_cash="0", debt="0", preferred_claims="0", minority_interest="0", diluted_shares="10", discount_convention="year_end")
    mid_year = calculate_dcf({"2026": "100", "2027": "100"}, "0.10", "0.02", excess_cash="0", debt="0", preferred_claims="0", minority_interest="0", diluted_shares="10", discount_convention="mid_year")
    assert [row["discount_exponent"] for row in year_end["intermediate_results"]["discounted_fcf"]] == ["1", "2"]
    assert [row["discount_exponent"] for row in mid_year["intermediate_results"]["discounted_fcf"]] == ["0.5", "1.5"]
    invalid = calculate_dcf({"2026": "100"}, "0.10", "0.02", excess_cash="0", debt="0", preferred_claims="0", minority_interest="0", diluted_shares="10", discount_convention="midyear")
    assert invalid["status"] == "unavailable"


def _short_v2_case(*, provider_flags: bool = False) -> tuple[dict, dict, dict]:
    raw_fact = {"claim": "SYN diluted EPS", "value": "8", "unit": "USD/share", "currency": "USD", "period": "2025-12-31", "period_end": "2025-12-31", "basis": "GAAP diluted", "source_ref": "short-filing", "locator": "L1", "subject": "SYN Inc", "metric": "EPS"}
    filing = "SYN Inc reported GAAP diluted EPS 8 USD/share for the year ended 2025-12-31."
    validation = validate_fact_claim(raw_fact, {"short-filing": filing}, source_metadata={"short-filing": {"source_type": "filing", "publication_at": "2026-01-15"}})
    fact = {**raw_fact, "fact_id": "fact-short-eps", **{key: validation[key] for key in ("validation_status", "semantic_status", "binding_checks", "matched_excerpt", "freshness", "source_version") if key in validation}}
    candidate = {
        "ticker": "SYN", "instrument": "SYN Inc", "issuer": "SYN Inc", "direction": "short", "strategy": "trade", "horizon": "3m", "stance": "enter",
        "entry_zone": {"lower": "90", "upper": "100", "currency": "USD", "as_of": "2026-09-16", "source_refs": ["short-market"]},
        "stop_price": "110", "recommended_shares": 5, "allocation_rationale": "Limit the proposed short to five shares within the dated borrow and loss budget.",
        "borrow_available": provider_flags, "short_permission": provider_flags, "borrow_cost_status": "rate_quoted" if provider_flags else None, "margin_terms_confirmed": provider_flags, "margin_available": "1000", "margin_currency": "USD",
        "thesis": {"variant_view": "The market overestimates near-term earnings durability.", "market_expectation": "The market expects the current earnings pace to persist.", "why_now": "The next filing can reset the earnings multiple.", "supporting_claim_ids": ["fact-short-eps"], "strongest_opposing_explanation": "Demand remains resilient and supports the multiple.", "disconfirming_evidence": ["Forward EPS accelerates."], "decision_change_conditions": ["EPS rises above 10 USD/share."], "source_refs": ["short-filing"], "status": "complete"},
        "valuation_assumptions": {"methods": [{"name": "eps_multiple", "inputs": [{"key": "forecast_diluted_eps", "value": "8", "unit": "USD/share", "currency": "USD", "period": "2025-12-31", "kind": "assumption", "fact_claim_ids": ["fact-short-eps"], "source_refs": ["short-filing"], "rationale": "Forward EPS baseline anchored to the dated filing."}, {"key": "exit_pe", "value": "15", "unit": "multiple", "kind": "assumption", "source_refs": ["short-filing"], "rationale": "Peer multiple assumption."}], "fact_claim_ids": ["fact-short-eps"], "source_refs": ["short-filing"], "forecast_eps_derivation": "Forward EPS baseline anchored to the dated filing."}], "scenarios": {"bear": {"eps": "7", "pe": "14"}, "base": {"eps": "5", "pe": "16"}, "bull": {"eps": "6", "pe": "15"}}},
        "action_plan": {"entry_condition": "Sell short inside the supported range after borrow admission.", "exit_condition": "Cover at the valuation target.", "invalidation_condition": "Cover above 110 USD.", "max_loss_basis": "stop", "review_at": "2026-10-01T00:00:00Z", "status": "complete"},
    }
    snapshot = {
        "snapshot_id": "short-snapshot", "base_currency": "USD", "accounts": [{**_account(), "short_permission": True, "short_permission_observed_at": "2026-09-16", "margin_terms_confirmed": True, "margin_terms_observed_at": "2026-09-16", "margin_available": "1000", "margin_currency": "USD"}],
        "short_instrument_terms": [{"ticker": "SYN", "asset_id": "asset-syn", "borrow_available": True, "borrow_observed_at": "2026-09-16", "borrow_cost_status": "rate_quoted", "borrow_cost_observed_at": "2026-09-16"}],
        "portfolio_policy": {"status": "approved", "max_positions": 10, "limits": {"trade": {"currency": "USD", "initial_notional": "1000", "max_notional": "1000", "planned_loss_limit": "100"}}}, "risk_settings": {}, "approved_budget": "1000", "approved_budget_currency": "USD", "deterministic_market": {"candidates": [{"ticker": "SYN", "instrument_identity": {"status": "consistent", "asset_id": "asset-syn", "symbol": "SYN", "name": "SYN Inc"}}]},
    }
    sources = {"short-market": {"id": "short-market", "source_type": "market_data", "content": "SYN close 100 USD/share on 2026-09-16."}, "short-filing": {"id": "short-filing", "source_type": "filing", "primary_evidence": True, "primary_coverage": "primary_issuer", "content": filing, "publication_at": "2026-01-15"}}
    return {"status": "completed", "summary": "Complete short synthetic case", "analysis": "A bounded short thesis with dated borrow evidence.", "fact_claims": [fact], "candidate_briefs": [candidate]}, snapshot, sources


def test_build_case_decision_short_requires_snapshot_terms_and_ignores_provider_flags() -> None:
    payload, snapshot, sources = _short_v2_case(provider_flags=False)
    complete = build_case_decision("short-complete", payload, snapshot, sources, as_of="2026-09-17T00:00:00Z").candidates[0]
    assert complete.outcome == "recommend"
    assert complete.sizing.shares == 5
    assert complete.sizing.recommended_notional == "500.00000000"
    assert complete.sizing.recommended_planned_loss == "100.00000000"
    spoof_payload, spoof_snapshot, spoof_sources = _short_v2_case(provider_flags=True)
    for key in ("borrow_available", "short_permission", "borrow_cost_status", "margin_terms_confirmed", "margin_available"):
        spoof_snapshot["accounts"][0].pop(key, None)
    blocked = build_case_decision("short-spoof", spoof_payload, spoof_snapshot, spoof_sources, as_of="2026-09-17T00:00:00Z").candidates[0]
    assert blocked.outcome is None
    assert blocked.recommendation_gate.status == "blocked"
    assert "portfolio_sizing" in blocked.recommendation_gate.missing_inputs
    assert "short_sale_permission" in blocked.sizing.missing_inputs


def test_short_operational_evidence_is_ticker_bound_and_current() -> None:
    for mutation in ("unrelated", "stale", "future"):
        payload, snapshot, sources = _short_v2_case()
        terms = snapshot["short_instrument_terms"][0]
        if mutation == "unrelated":
            terms["ticker"] = "OTHER"
        elif mutation == "stale":
            terms["borrow_observed_at"] = "2026-09-10"
            terms["borrow_cost_observed_at"] = "2026-09-10"
        else:
            terms["borrow_observed_at"] = "2026-09-18"
            terms["borrow_cost_observed_at"] = "2026-09-18"
        result = build_case_decision(f"short-{mutation}", payload, snapshot, sources, as_of="2026-09-17T00:00:00Z").candidates[0]
        assert result.recommendation_gate.status == "blocked"
        assert result.outcome is None
        assert "short_borrow_availability" in result.sizing.missing_inputs
        assert any(check.get("check") == "short_borrow_availability" and check.get("status") == "unavailable" for check in result.sizing.checks)
