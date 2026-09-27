from copy import deepcopy
from decimal import Decimal

import pytest

from backend.app.research.valuation import build_valuation
from backend.app.schemas import ValuationAssumptions, ValuationBlock


def packet(name="ps_multiple"):
    key, metric, value = {"ps_multiple": ("baseline_revenue", "revenue", "1000"), "ev_ebitda": ("baseline_ebitda", "ebitda", "100"), "nav_multiple": ("baseline_nav", "book equity", "500")}[name]
    specs = [(key, metric, value, "USD"), ("diluted_shares", "shares", "10", "shares")]
    if name == "ev_ebitda":
        specs.extend((key, metric, value, "USD") for key, metric, value in (("excess_cash", "cash", "20"), ("debt", "debt", "40"), ("preferred_claims", "preferred", "0"), ("minority_interest", "minority", "0")))
    facts, inputs = [], []
    for key, metric, value, unit in specs:
        fact = {"fact_id": key, "metric": metric, "subject": "ABC", "value": value, "unit": unit, "currency": None if unit == "shares" else "USD", "period": "2026-08-30", "period_end": "2026-08-30", "basis": "reported", "source_ref": "src", "validation_status": "validated", "semantic_status": "supported"}
        facts.append(fact)
        inputs.append({key: value for key, value in fact.items() if key in {"value", "unit", "currency", "period", "basis"}} | {"key": key, "kind": "fact", "fact_claim_ids": [key], "source_refs": ["src"]})
    method = {"name": name, "period": "FY2027", "horizon_months": 12, "inputs": inputs,
              "scenarios": {case: {"growth_rate": growth, "exit_multiple": multiple, "growth_rationale": "Conditional operating trend.", "multiple_rationale": "Explicit research assumption."} for case, growth, multiple in (("bear", "0", "1"), ("base", "0.1", "2"), ("bull", "0.2", "3"))}}
    if name == "nav_multiple":
        method["nav_basis"] = "book_equity"
    sources = {"src": {"source_type": "filing", "url": "https://www.sec.gov/Archives/abc", "content": "retained financial evidence"}}
    return {"methods": [method]}, facts, sources


def calculate(proposal, facts, sources):
    typed = ValuationAssumptions.model_validate(proposal)
    result = build_valuation(typed, currency="USD", issuer="ABC", as_of="2026-09-25", validated_facts=facts, source_records=sources)
    ValuationBlock.model_validate(result)
    return result


@pytest.mark.parametrize("name,target,today", [("ps_multiple", "220", "200"), ("ev_ebitda", "20", "18"), ("nav_multiple", "110", "100")])
def test_comparable_targets_and_today_use_correct_equity_bridge_and_one_year(name, target, today):
    result = calculate(*packet(name))
    assert result["status"] == "complete", result
    assert Decimal(result["scenarios"]["base"]) == Decimal(target)
    assert Decimal(result["research_context"]["implied_today"]["scenarios"]["base"]) == Decimal(today)
    assert result["horizon"] == "12 months"
    assert result["methods"][0]["intermediate_results"]["comparable_analysis"]["status"] == "unavailable"


def test_ps_does_not_subtract_debt_as_if_it_were_ev_sales():
    proposal, facts, sources = packet()
    proposal["methods"][0].update(debt="999", excess_cash="1")
    assert Decimal(calculate(proposal, facts, sources)["scenarios"]["base"]) == 220


def test_forecast_span_is_distinct_from_holding_horizon():
    proposal, facts, sources = packet()
    proposal["methods"][0].update(forecast_years="2", forecast_span_rationale="FY2025 baseline to FY2027 forecast; 12-month target.")
    result = calculate(proposal, facts, sources)
    assert Decimal(result["scenarios"]["base"]) == 242
    assert result["horizon"] == "12 months"
    proposal["methods"][0]["forecast_span_rationale"] = ""
    assert calculate(proposal, facts, sources)["status"] != "complete"


@pytest.mark.parametrize("change", ["wrong_issuer", "wrong_metric", "unvalidated", "wrong_currency", "missing_source", "wrong_scale", "conflicting_alias"])
def test_material_baseline_cannot_be_certified_by_a_nearby_number(change):
    proposal, facts, sources = packet()
    if change == "wrong_issuer": facts[0]["subject"] = "OTHER"
    if change == "wrong_metric": facts[0]["metric"] = "eps"
    if change == "unvalidated": facts[0]["semantic_status"] = "ambiguous"
    if change == "wrong_currency": facts[0]["currency"] = "CAD"
    if change == "missing_source": sources.clear()
    if change == "wrong_scale": proposal["methods"][0]["inputs"][0]["scale"] = "million"
    if change == "conflicting_alias": proposal["methods"][0]["baseline_revenue"] = "999"
    assert not calculate(proposal, facts, sources)["scenarios"]


def test_missing_ev_liability_is_not_silently_zero_and_explicit_assumption_is_labelled():
    proposal, facts, sources = packet("ev_ebitda")
    proposal["methods"][0]["inputs"] = [row for row in proposal["methods"][0]["inputs"] if row["key"] != "debt"]
    assert not calculate(proposal, facts, sources)["scenarios"]
    proposal["methods"][0]["inputs"].append({"key": "debt", "value": "0", "unit": "USD", "currency": "USD", "kind": "assumption", "rationale": "Explicit conditional repayment by the forecast date."})
    result = calculate(proposal, facts, sources)
    assert result["status"] == "complete"
    assert Decimal(result["scenarios"]["base"]) == 24
    today = result["methods"][0]["intermediate_results"]["implied_today"]
    assert today["status"] == "unavailable"
    assert not today["scenarios"]
    assert "Reported current debt" in today["missing_inputs"][0]


@pytest.mark.parametrize("name", ["ps_multiple", "ev_ebitda", "nav_multiple"])
def test_forecast_share_count_does_not_change_implied_today(name):
    proposal, facts, sources = packet(name)
    shares = next(row for row in proposal["methods"][0]["inputs"] if row["key"] == "diluted_shares")
    shares.update(kind="assumption", value="8", rationale="Conditional buyback by the forecast date.", fact_claim_ids=[], source_refs=[])
    result = calculate(proposal, facts, sources)
    assert result["status"] == "complete"
    today = result["methods"][0]["intermediate_results"]["implied_today"]
    assert today["status"] == "unavailable" and not today["scenarios"]
    assert "Reported current diluted shares" in today["missing_inputs"][0]


def test_book_equity_must_not_be_relabelled_appraised_nav():
    proposal, facts, sources = packet("nav_multiple")
    proposal["methods"][0]["nav_basis"] = "appraised_nav"
    assert not calculate(proposal, facts, sources)["scenarios"]
    facts[0]["metric"] = "asset value"
    assert not calculate(proposal, facts, sources)["scenarios"]


def test_peer_ratio_is_calculated_from_its_own_facts_and_dates_not_provider_label():
    proposal, facts, sources = packet()
    method = proposal["methods"][0]
    rows = []
    for key, metric, value in (("numerator", "market capitalization", "3000"), ("denominator", "revenue", "1000")):
        fact = facts[0] | {"fact_id": key, "metric": metric, "subject": "PEER", "value": value}
        facts.append(fact)
        rows.append(method["inputs"][0] | {"value": value, "fact_claim_ids": [key]})
    method["comparables"] = [{"ticker": "PEER", "metric": "P/S", "basis": "trailing", "as_of": "2026-09-25", "numerator": rows[0], "denominator": rows[1], "rationale": "Similar margins and business mix."}]
    receipt = calculate(proposal, facts, sources)["methods"][0]["intermediate_results"]["comparable_analysis"]
    assert receipt["medians"] == {"trailing": "3.00000000"}
    method["comparables"][0]["as_of"] = "2026-09-26"
    receipt = calculate(proposal, facts, sources)["methods"][0]["intermediate_results"]["comparable_analysis"]
    assert not receipt["peers"] and receipt["rejected"]


def test_noncorporate_fund_cannot_use_ps():
    proposal, facts, sources = packet()
    result = build_valuation(proposal, asset_class="ETF", currency="USD", issuer="ABC", as_of="2026-09-25", validated_facts=facts, source_records=sources)
    assert not result["scenarios"]


def test_operating_ebitda_proxy_requires_two_sourced_matching_periods_and_is_labelled():
    proposal, facts, sources = packet("ev_ebitda")
    method = proposal["methods"][0]
    baseline = method["inputs"].pop(0)
    template = facts.pop(0)
    for key, metric, value in (("baseline_operating_income", "operating income", "80"), ("baseline_da", "depreciation and amortization", "20")):
        facts.append(template | {"fact_id": key, "metric": metric, "value": value, "period_start": "2025-08-31"})
        method["inputs"].append(baseline | {"key": key, "value": value, "fact_claim_ids": [key]})
    result = calculate(proposal, facts, sources)
    assert result["status"] == "complete"
    assert Decimal(result["scenarios"]["base"]) == 20
    assert "proxy" in result["methods"][0]["steps"][0]
    facts[-1]["period_start"] = "2026-01-01"
    assert not calculate(proposal, facts, sources)["scenarios"]


def test_unused_or_unrepresentable_inputs_do_not_certify_or_crash_a_target():
    proposal, facts, sources = packet()
    proposal["methods"][0]["inputs"].append({"key": "fake_financial_fact", "kind": "fact", "value": "9", "fact_claim_ids": ["fake"]})
    assert not calculate(proposal, facts, sources)["scenarios"]
    proposal, facts, sources = packet()
    proposal["methods"][0]["scenarios"]["base"]["exit_multiple"] = "1e10000"
    assert not calculate(proposal, facts, sources)["scenarios"]
