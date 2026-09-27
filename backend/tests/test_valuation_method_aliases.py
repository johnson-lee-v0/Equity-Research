"""Descriptive labels from one valuation method cannot become another's operands."""
from copy import deepcopy

import pytest

from backend.app.research.fact_validation import validate_fact_claim
from backend.app.research.valuation import build_valuation
from backend.app.schemas import ValuationAssumptions


def eps_packet():
    content = "ABC reported GAAP diluted EPS 2.10 USD/share for the year ended 2026-05-31."
    claim = {"claim": "ABC reported GAAP diluted EPS", "subject": "ABC", "metric": "EPS",
             "value": "2.10", "unit": "USD/share", "currency": "USD", "period": "2026-05-31",
             "basis": "GAAP diluted", "source_ref": "release", "locator": "L1"}
    validation = validate_fact_claim(claim, {"release": content})
    assert validation["semantic_status"] == "supported"
    fact = claim | validation | {"fact_id": "fact_reported_eps", "period_start": "2025-06-01", "period_end": "2026-05-31"}
    sources = {"release": {"id": "release", "source_type": "issuer_release", "primary_evidence": True,
                           "primary_coverage": "primary_issuer", "content": content, "publication_at": "2026-06-30"}}
    method = {
        "name": "eps_multiple", "currency": "USD", "period": "FY2027", "forecast_years": "1", "horizon_months": 12,
        "forecast_span_rationale": "FY2026 reported EPS to FY2027 forecast spans one fiscal year.",
        "baseline_eps": "2.1", "forecast_metric": "GAAP diluted EPS", "metric": "EPS", "metric_name": "GAAP diluted EPS",
        "discount_rate": "0.12", "wacc": "0.10",  # Neither participates in EPS/P-E arithmetic.
        "inputs": [
            {"key": "baseline_eps", "kind": "fact", "value": "2.10", "unit": "USD/share", "currency": "USD",
             "period": "2026-05-31", "basis": "GAAP diluted", "fact_claim_ids": [fact["fact_id"]], "source_refs": ["release"]},
            {"key": "required_return", "kind": "assumption", "value": "0.12", "unit": "fraction", "rationale": "Explicit unused return assumption."},
        ],
        "scenarios": {name: {"growth_rate": growth, "exit_multiple": multiple, "forecast_metric": "GAAP diluted EPS",
            "growth_rationale": "Explicit conditional earnings growth.", "multiple_rationale": "Chosen scenario P/E assumption."}
            for name, growth, multiple in (("bear", "-0.2", "16"), ("base", "-0.05", "20"), ("bull", "0.1", "24"))},
    }
    current = {"current_earnings": {"status": "available", "value": "2.10", "currency": "USD", "period_end": "2026-05-31", "source_refs": ["release"]}}
    return {"methods": [method]}, [fact], sources, current


def calculate(proposal, facts, sources, context):
    return build_valuation(ValuationAssumptions.model_validate(proposal), currency="USD", issuer="ABC",
                           horizon="12 months", as_of="2026-09-26", validated_facts=facts,
                           source_records=sources, research_context=context)


@pytest.mark.parametrize("name", ["eps_multiple", "eps", "pe", "earnings_multiple"])
def test_eps_descriptive_ev_labels_do_not_change_fact_binding_or_arithmetic(name):
    proposal, facts, sources, context = eps_packet()
    proposal["methods"][0]["name"] = name
    before = deepcopy((proposal, facts, sources, context))
    result = calculate(proposal, facts, sources, context)
    assert result["status"] == "complete", result["methods"][0]["reasons"]
    assert result["methods"][0]["supported"]
    assert result["horizon"] == "12 months"
    assert result["scenarios"] == {"bear": "26.88000000", "base": "39.90000000", "bull": "55.44000000"}
    today = result["research_context"]["implied_today"]
    assert today["status"] == "available"
    assert today["scenarios"] == {"bear": "33.60000000", "base": "42.00000000", "bull": "50.40000000"}
    base = result["methods"][0]["scenario_calculations"]["base"]
    assert base["intermediate_results"]["forecast_years"] == "1.00000000"
    assert "fact_reported_eps" in {identifier for row in base["inputs"] for identifier in row.get("fact_claim_ids", [])}
    assert (proposal, facts, sources, context) == before


@pytest.mark.parametrize("conflict", ["baseline_scalar", "forecast_alias", "forecast_typed", "multiple_alias", "multiple_typed", "scenario_eps", "unknown_fact"])
def test_real_eps_conflicts_and_unbound_rows_still_reject(conflict):
    proposal, facts, sources, context = eps_packet()
    method = proposal["methods"][0]
    if conflict == "baseline_scalar":
        method["baseline_eps"] = "999"
    elif conflict == "forecast_alias":
        method.update(forecast_eps="1.995", eps="999")
    elif conflict == "forecast_typed":
        method["forecast_eps"] = "1.995"
        method["inputs"].append({"key": "forecast_diluted_eps", "kind": "assumption", "value": "999", "rationale": "Conflicting forward earnings."})
    elif conflict == "multiple_alias":
        method.update(exit_multiple="20", pe="999")
    elif conflict == "multiple_typed":
        method["exit_multiple"] = "20"
        method["inputs"].append({"key": "exit_pe", "kind": "assumption", "value": "999", "rationale": "Conflicting P/E."})
    elif conflict == "scenario_eps":
        method["scenarios"]["base"].update(forecast_eps="1.995", eps="999")
    elif conflict == "unknown_fact":
        method["inputs"].append({"key": "unrelated_observation", "kind": "fact", "value": "999", "fact_claim_ids": ["fact_unknown"]})
    result = calculate(proposal, facts, sources, context)
    assert result["status"] != "complete"
    assert result["scenarios"] == {}


@pytest.mark.parametrize("conflict", ["metric_alias", "metric_typed", "multiple_alias"])
def test_ev_operand_alias_conflicts_remain_material(conflict):
    proposal, facts, sources, context = eps_packet()
    content = "ABC reported GAAP EBITDA 100 USD for the year ended 2026-05-31."
    claim = {"claim": "ABC reported EBITDA", "subject": "ABC", "metric": "EBITDA", "unit": "USD", "currency": "USD",
             "value": "100", "basis": "GAAP", "period": "2026-05-31", "source_ref": "release", "locator": "L1"}
    validation = validate_fact_claim(claim, {"release": content})
    assert validation["semantic_status"] == "supported"
    facts = [claim | validation | {"fact_id": "fact_ebitda", "period_start": "2025-06-01", "period_end": "2026-05-31"}]
    sources["release"]["content"] = content
    rows = [{"key": "forecast_metric", "kind": "fact", "value": "100", "unit": "USD", "currency": "USD",
             "fact_claim_ids": ["fact_ebitda"], "source_refs": ["release"]}]
    rows.extend({"key": key, "kind": "assumption", "value": value, "rationale": "Explicit synthetic bridge assumption."}
                for key, value in (("exit_ev_multiple", "10"), ("excess_cash", "0"), ("debt", "0"), ("preferred_claims", "0"), ("minority_interest", "0"), ("diluted_shares", "10")))
    method = {"name": "ev_multiple", "metric_name": "EBITDA", "currency": "USD", "forecast_metric": "100", "adr_ratio": "1", "inputs": rows}
    proposal = {"methods": [method]}
    assert calculate(proposal, facts, sources, context)["status"] == "complete"
    if conflict == "metric_alias":
        method["metric"] = "999"
    elif conflict == "metric_typed":
        rows[0]["value"] = "999"
    else:
        method.update(exit_multiple="10", multiple="999")
    result = calculate(proposal, facts, sources, context)
    assert result["status"] != "complete"
    assert result["scenarios"] == {}
    assert any("Conflicting raw, alias and typed inputs" in reason for reason in result["methods"][0]["reasons"])
