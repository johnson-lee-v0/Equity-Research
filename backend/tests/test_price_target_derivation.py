"""Research forecasts have sourced baselines and code-owned arithmetic."""
from decimal import Decimal

import pytest

from backend.app.research.decisions import build_case_decision
from backend.app.research.fact_validation import validate_fact_claim
from backend.app.research.valuation import build_valuation, calculate_dcf, calculate_ev_multiple, calculate_nav
from backend.app.schemas import ValuationAssumptions, ValuationBlock


AS_OF = "2026-09-25T22:00:00Z"


def packet():
    # Synthetic issuer, deliberately one bound observation per source row.
    content = "ABC reported GAAP diluted EPS 20.76 USD/share for 2026-08-30.\nABC reported GAAP diluted EPS 0.15 USD/share for 2026-08-30."
    source = {"id": "release", "source_type": "issuer_release", "primary_evidence": True, "primary_coverage": "primary_issuer", "content": content, "publication_at": "2026-09-24"}
    facts = []
    for identifier, value, period, start, locator in (("eps", "20.76", "2026-08-30", "2025-09-01", "L1"), ("benefit", "0.15", "2026-08-30", "2026-05-11", "L2")):
        claim = {"claim": "ABC diluted EPS", "subject": "ABC", "metric": "EPS", "value": value, "unit": "USD/share", "currency": "USD", "period": period, "basis": "GAAP diluted", "source_ref": "release", "locator": locator}
        validation = validate_fact_claim(claim, {"release": content})
        assert validation["semantic_status"] == "supported"
        facts.append({**claim, **validation, "fact_id": identifier, "period_start": start, "period_end": "2026-08-30"})
    method = {
        "name": "eps_multiple", "currency": "USD", "period": "FY2027",
        "inputs": [{"key": "baseline_eps", "value": "20.76", "unit": "USD/share", "currency": "USD", "period": "2026-08-30", "basis": "GAAP diluted", "kind": "fact", "fact_claim_ids": ["eps"], "source_refs": ["release"]}],
        "normalization_adjustments": [{"fact_claim_ids": ["benefit"], "operation": "subtract", "rationale": "Remove the specified nonrecurring benefit only."}],
        "scenarios": {name: {"growth_rate": growth, "exit_multiple": multiple, "growth_rationale": f"{name}: assumed growth conditional on operating execution.", "multiple_rationale": f"{name}: assumed exit valuation, not observed consensus."} for name, growth, multiple in (("bear", "0.03", "32"), ("base", "0.08", "40"), ("bull", "0.12", "46"))},
        "rationale": "Growth and valuation discipline are explicit research assumptions, not reported facts.",
    }
    return {"methods": [method]}, facts, {"release": source}


def valuation(assumptions=None, facts=None, sources=None):
    default_assumptions, default_facts, default_sources = packet()
    return build_valuation(assumptions or default_assumptions, validated_facts=default_facts if facts is None else facts, source_records=default_sources if sources is None else sources, issuer="ABC", as_of=AS_OF, horizon="event")


def test_known_item_adjustment_forecasts_targets_and_full_audit_are_code_owned():
    assumptions, facts, sources = packet()
    typed = ValuationAssumptions.model_validate(assumptions)
    result = valuation(typed, facts, sources)
    ValuationBlock.model_validate(result)
    assert result["status"] == "complete"
    assert result["horizon"] == "12 months"
    assert result["currency"] == "USD"
    assert result["scenarios"] == {"bear": "679.30560000", "base": "890.35200000", "bull": "1061.82720000"}
    method = result["methods"][0]
    base = method["scenario_calculations"]["base"]
    assert base["intermediate_results"]["adjusted_baseline_eps"] == "20.61000000"
    assert "not a claim" in base["intermediate_results"]["normalization_label"]
    assert base["intermediate_results"]["forecast_diluted_eps"] == "22.25880000"
    assert base["output_price"] == result["scenarios"]["base"]
    assert {row["key"] for row in method["inputs"]} >= {"baseline_eps", "eps_adjustment_1", "annual_eps_growth", "exit_pe"}
    grid = next(row for row in result["sensitivities"] if row["parameter"] == "annual_eps_growth_and_exit_pe")
    assert len(grid["values"]) == 9
    assert grid["probability"] is None
    assert valuation(typed, facts, sources)["input_hash"] == result["input_hash"]


def test_twenty_four_months_compounds_annual_growth_without_changing_it():
    assumptions, facts, sources = packet()
    assumptions["methods"][0]["horizon_months"] = 24
    assumptions["methods"][0]["period"] = "FY2028"
    result = valuation(assumptions, facts, sources)
    assert result["status"] == "complete"
    assert result["horizon"] == "24 months"
    assert Decimal(result["scenarios"]["base"]) == Decimal("20.61") * Decimal("1.08") ** 2 * 40


def test_historical_baseline_requires_explicit_forecast_span_and_remains_marked_stale():
    assumptions, facts, sources = packet()
    method = assumptions["methods"][0]
    method["normalization_adjustments"] = []
    facts = facts[:1]
    facts[0].update(period="2025-05-31", period_start="2024-06-01", period_end="2025-05-31")
    method["inputs"][0]["period"] = "2025-05-31"
    sources["release"]["publication_at"] = "2025-06-25"
    assert valuation(assumptions, facts, sources)["scenarios"] == {}
    method.update(forecast_years="2", forecast_span_rationale="FY2025 historical EPS to FY2027 forecast covers two annual growth steps; the target horizon is twelve months from now.")
    result = valuation(assumptions, facts, sources)
    assert result["status"] == "complete"
    assert result["horizon"] == "12 months"
    calculation = result["methods"][0]["scenario_calculations"]["base"]
    assert calculation["intermediate_results"]["baseline_freshness"] == "stale"
    assert calculation["intermediate_results"]["historical_baseline_age_days"] == 482
    assert Decimal(result["scenarios"]["base"]) == Decimal("20.76") * Decimal("1.08") ** 2 * 40
    for old_date in ("2023-08-31", "FY2025"):
        facts[0].update(period=old_date, period_end=old_date)
        method["inputs"][0]["period"] = old_date
        assert valuation(assumptions, facts, sources)["scenarios"] == {}


def fiscal_packet(baseline_period="FY2026", target_period="FY2027"):
    assumptions, facts, sources = packet()
    method = assumptions["methods"][0]
    method["normalization_adjustments"] = []
    method["period"] = target_period
    method["inputs"][0]["period"] = baseline_period
    facts = facts[:1]
    facts[0]["period"] = baseline_period
    return assumptions, facts, sources


@pytest.mark.parametrize("baseline_period,target_period", [
    ("FY2026", "FY2027"), ("fiscal year 2026", "fiscal year 2027"),
    ("FY 2026", "FY 2027E"), ("FY2026, annual period ended 2026-08-30", "FY2027 forecast"),
])
def test_annual_fiscal_labels_prevent_two_growth_steps_into_next_year(baseline_period, target_period):
    assumptions, facts, sources = fiscal_packet(baseline_period, target_period)
    assumptions["methods"][0].update(forecast_years="2", forecast_span_rationale="A narrative cannot change the dated annual earnings interval.")
    result = valuation(assumptions, facts, sources)
    assert result["scenarios"] == {}
    assert any("requires 1 annual growth steps" in reason for reason in result["methods"][0]["reasons"])
    assumptions["methods"][0]["forecast_years"] = "1"
    corrected = valuation(assumptions, facts, sources)
    assert corrected["status"] == "complete"
    assert Decimal(corrected["scenarios"]["base"]) == Decimal("20.76") * Decimal("1.08") * 40


def test_older_annual_fiscal_baseline_requires_explicit_matching_span():
    assumptions, facts, sources = fiscal_packet("FY2024", "FY2026")
    result = valuation(assumptions, facts, sources)
    assert result["scenarios"] == {}
    assert "requires 2 annual growth steps" in " ".join(result["methods"][0]["reasons"])
    assumptions["methods"][0].update(forecast_years="2", forecast_span_rationale="FY2024 to FY2026 contains two annual growth steps despite a 12-month holding horizon.")
    corrected = valuation(assumptions, facts, sources)
    assert corrected["horizon"] == "12 months"
    assert Decimal(corrected["scenarios"]["base"]) == Decimal("20.76") * Decimal("1.08") ** 2 * 40


def test_two_year_fiscal_bridge_from_fy2025_to_fy2027_remains_valid():
    assumptions, facts, sources = fiscal_packet("FY2025", "FY2027")
    assumptions["methods"][0].update(forecast_years="2", forecast_span_rationale="Two annual steps from FY2025 reported EPS to FY2027 forecast EPS.")
    result = valuation(assumptions, facts, sources)
    assert result["status"] == "complete"
    assert result["methods"][0]["scenario_calculations"]["base"]["intermediate_results"]["forecast_years"] == "2.00000000"


@pytest.mark.parametrize("target_period", ["NTM through FY2027", "FY2027 Q2", "next twelve months", "2027-02-28"])
def test_fractional_nonannual_forecast_does_not_infer_fiscal_year_span(target_period):
    assumptions, facts, sources = fiscal_packet("FY2026", target_period)
    method = assumptions["methods"][0]
    method.update(forecast_years="0.5", forecast_span_rationale="Explicit half-year earnings progression, not an annual fiscal-year projection.", horizon_months=6)
    result = valuation(assumptions, facts, sources)
    assert result["status"] == "complete"
    assert result["methods"][0]["scenario_calculations"]["base"]["intermediate_results"]["forecast_years"] == "0.50000000"


def test_implied_today_uses_only_code_context_and_uncompounded_current_eps():
    assumptions, facts, sources = packet()
    forged = {"current_earnings": {"status": "available", "value": "999", "currency": "USD", "source_refs": ["release"]}}
    assumptions["research_context"] = forged
    ignored = build_valuation(assumptions, validated_facts=facts, source_records=sources, issuer="ABC", as_of=AS_OF)
    assert ignored["research_context"] is None
    context = {"current_earnings": {"status": "available", "value": "20.76", "currency": "USD", "period_end": "2026-08-30", "source_refs": ["release"], "formula": "Latest reported annual diluted EPS"}}
    result = build_valuation(assumptions, validated_facts=facts, source_records=sources, issuer="ABC", as_of=AS_OF, research_context=context)
    today = result["research_context"]["implied_today"]
    assert today["status"] == "available"
    assert today["scenarios"] == {"bear": "664.32000000", "base": "830.40000000", "bull": "954.96000000"}
    assert today["scenarios"]["base"] != result["scenarios"]["base"]
    assert today["eps"] == "20.76000000"  # no normalization, growth, or discounting
    assert "implied_today" not in context  # supplied immutable calculation input stays unchanged


def test_implied_today_refuses_currency_mismatch():
    assumptions, facts, sources = packet()
    result = build_valuation(assumptions, validated_facts=facts, source_records=sources, issuer="ABC", as_of=AS_OF, research_context={"current_earnings": {"status": "available", "value": "20.76", "currency": "CAD", "source_refs": ["release"]}})
    assert result["research_context"]["implied_today"]["status"] == "unavailable"
    assert result["research_context"]["implied_today"]["scenarios"] == {}


@pytest.mark.parametrize("mutation", ["proposed", "currency", "basis", "outside_period", "wrong_metric", "duplicate_adjustment", "unsupported_source"])
def test_invalid_baseline_or_adjustment_cannot_produce_a_supported_target(mutation):
    assumptions, facts, sources = packet()
    if mutation == "proposed": facts[0]["validation_status"] = "proposed"
    elif mutation == "currency": facts[1].update(currency="CAD", unit="CAD/share")
    elif mutation == "basis": facts[1]["basis"] = "GAAP basic"
    elif mutation == "outside_period": facts[1]["period_end"] = "2026-11-30"
    elif mutation == "wrong_metric": facts[1]["metric"] = "revenue"
    elif mutation == "duplicate_adjustment": assumptions["methods"][0]["normalization_adjustments"] *= 2
    elif mutation == "unsupported_source": sources["release"]["primary_evidence"] = False
    result = valuation(assumptions, facts, sources)
    assert result["status"] != "complete"
    assert result["scenarios"] == {}


@pytest.mark.parametrize("mutation", ["missing_rationale", "conflicting_forecast", "inverted_scenarios", "invalid_growth", "zero_pe"])
def test_scenarios_require_explicit_defensible_assumptions(mutation):
    assumptions, facts, sources = packet()
    method = assumptions["methods"][0]
    base = method["scenarios"]["base"]
    if mutation == "missing_rationale": base["multiple_rationale"] = ""
    elif mutation == "conflicting_forecast": base["forecast_eps"] = "999"
    elif mutation == "inverted_scenarios": method["scenarios"]["bear"]["exit_multiple"] = "999"
    elif mutation == "invalid_growth": base["growth_rate"] = "-1"
    elif mutation == "zero_pe": base["exit_multiple"] = "0"
    assert valuation(assumptions, facts, sources)["scenarios"] == {}


def test_loss_making_company_rejects_pe_but_can_select_explicit_ev_bridge():
    assumptions, facts, sources = packet()
    facts[0]["value"] = "-2"
    assumptions["methods"][0]["inputs"][0]["value"] = "-2"
    facts.append({**facts[0], "fact_id": "ebitda", "metric": "EBITDA", "unit": "USD", "value": "10", "basis": "GAAP"})
    rows = [{"key": "forecast_metric", "value": "10", "kind": "fact", "unit": "USD", "currency": "USD", "fact_claim_ids": ["ebitda"], "source_refs": ["release"]}]
    rows.extend({"key": key, "value": value, "kind": "assumption", "rationale": "Explicit synthetic bridge assumption."} for key, value in (("exit_ev_multiple", "10"), ("excess_cash", "0"), ("debt", "0"), ("preferred_claims", "0"), ("minority_interest", "0"), ("diluted_shares", "10")))
    assumptions["methods"].append({"name": "ev_multiple", "currency": "USD", "inputs": rows, "source_refs": ["release"], "fact_claim_ids": ["ebitda"]})
    result = valuation(assumptions, facts, sources)
    assert result["methods"][0]["status"] == "unavailable"
    assert result["selected_method"] == "ev_multiple"
    assert result["scenarios"]["base"] == "10.00000000"


def test_canonical_target_survives_missing_portfolio_and_legacy_empty_target():
    assumptions, facts, sources = packet()
    payload = {"summary": "Research case", "fact_claims": facts, "candidate_briefs": [{"ticker": "ABC", "issuer": "ABC", "direction": "long", "horizon": "event", "stance": "watch", "target_price": None, "valuation_assumptions": assumptions}]}
    result = build_case_decision("research", payload, {}, sources, as_of=AS_OF).candidates[0]
    assert result.future_target is not None
    assert result.future_target.price == "890.35200000"
    assert result.future_target.horizon == "12 months"
    assert result.future_target.source_refs == ["release"]
    assert result.sizing.recommended_shares is None
    assert result.recommendation_gate.status != "pass"
    assert result.valuation.methods[0].scenario_calculations["base"]["output_price"] == result.future_target.price


def test_conflicted_instrument_identity_keeps_computed_target_unpublished():
    assumptions, facts, sources = packet()
    payload = {"fact_claims": facts, "candidate_briefs": [{"ticker": "ABC", "issuer": "ABC", "valuation_assumptions": assumptions}]}
    snapshot = {"deterministic_market": {"candidates": [{"ticker": "ABC", "instrument_identity": {"status": "conflict", "reason": "Ticker now belongs to another issuer."}}]}}
    result = build_case_decision("conflict", payload, snapshot, sources, as_of=AS_OF).candidates[0]
    assert result.future_target is None


def test_dcf_scenarios_survive_typed_schema_and_explain_each_discount_bridge():
    typed = ValuationAssumptions.model_validate({"methods": [{"name": "dcf", "scenarios": {
        "bear": {"forecast_fcf": [80, 85], "discount_rate": "0.12", "terminal_growth": "0.01", "rationale": "Higher capital costs and weak cash conversion."},
        "base": {"forecast_fcf": [100, 110], "discount_rate": "0.10", "terminal_growth": "0.02", "rationale": "Moderate growth and capital costs."},
        "bull": {"forecast_fcf": [120, 135], "discount_rate": "0.09", "terminal_growth": "0.03", "rationale": "Stronger cash conversion."},
    }}]})
    scenarios = typed.methods[0].scenarios.model_dump()
    result = calculate_dcf([100, 110], "0.10", "0.02", excess_cash="20", debt="30", preferred_claims="0", minority_interest="0", diluted_shares="10", currency="USD", scenarios=scenarios)
    assert result["status"] == "complete"
    prices = result["output_prices"]
    assert Decimal(prices["bear"]) < Decimal(prices["base"]) < Decimal(prices["bull"])
    for name, price in prices.items():
        explanation = result["scenario_calculations"][name]
        assert explanation["output_price"] == price
        assert len(explanation["intermediate_results"]["discounted_fcf"]) == 2
        assert any(row["key"] == "discount_rate" and row["value"] == f"{Decimal(scenarios[name]['discount_rate']):.8f}" for row in explanation["inputs"])


def test_ev_and_nav_scenario_audits_keep_actual_scenario_inputs():
    ev = calculate_ev_multiple("100", "10", excess_cash="20", debt="30", preferred_claims="0", minority_interest="0", diluted_shares="10", currency="USD", scenarios={"bear": {"forecast_metric": "80", "exit_multiple": "8"}, "base": {"forecast_metric": "100", "exit_multiple": "10"}, "bull": {"forecast_metric": "120", "exit_multiple": "12"}})
    assert ev["scenario_calculations"]["bear"]["intermediate_results"]["enterprise_value"] == "640.00000000"
    assert ev["scenario_calculations"]["bull"]["output_price"] == "143.00000000"
    nav = calculate_nav("100", currency="USD", scenarios={"bear": {"nav_per_unit": "80", "rationale": "Asset value falls."}, "base": {"nav_per_unit": "100"}, "bull": {"nav_per_unit": "120", "rationale": "Asset value rises."}})
    assert nav["scenario_calculations"]["base"]["inputs"][0]["kind"] == "fact"
    assert nav["scenario_calculations"]["bull"]["inputs"][0]["kind"] == "assumption"
