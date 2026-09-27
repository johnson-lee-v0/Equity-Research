"""Current asset identity must not silently relabel another issuer's prices."""
from copy import deepcopy

import pytest

from backend.app.research.decisions import build_case_decision, evaluate_instrument_identity
from backend.app.research.fact_validation import validate_fact_claim


def asset(name="Gold.com, Inc.", symbol="GOLD", **extra):
    return {"id": "asset-current", "symbol": symbol, "name": name, "status": "active", "exchange": "NYSE", "observed_at": "2026-09-13T20:00:00Z", **extra}


def test_reused_gold_ticker_conflicts_with_barrick_without_hardcoded_issuers():
    result = evaluate_instrument_identity("GOLD", "Barrick Mining Corporation", asset(), source_refs=["asset-source"])
    assert result["status"] == "conflict"
    assert result["provider_name"] == "Gold.com, Inc."
    assert result["claimed_name"] == "Barrick Mining Corporation"
    assert result["source_refs"] == ["asset-source"]
    assert result["asset_id"] == "asset-current"
    other = evaluate_instrument_identity("XYZ", "Northwind Shipping Ltd", asset("Contoso Retail Inc.", "XYZ"))
    assert other["status"] == "conflict"


@pytest.mark.parametrize("ticker,claimed,provider", [
    ("GLD", "SPDR Gold Shares", "SPDR Gold Trust, SPDR Gold Shares"),
    ("WOOF", "Petco Health and Wellness Company, Inc.", "Petco Health & Wellness Company Inc. Class A Common Stock"),
    ("IREN", "IREN Limited", "IREN Limited Ordinary Shares"),
    ("PHYS", "Sprott Physical Gold Trust", "Sprott Physical Gold Trust"),
])
def test_normalized_legal_and_share_class_names_are_consistent_not_verified(ticker, claimed, provider):
    result = evaluate_instrument_identity(ticker, claimed, asset(provider, ticker))
    assert result["status"] == "consistent"
    assert "not independent issuer verification" in result["reason"]


def test_shared_gold_or_fund_words_do_not_verify_different_issuers():
    result = evaluate_instrument_identity("XYZ", "Gold Trust", asset("Gold Fund", "XYZ"))
    assert result["status"] == "unverified"
    result = evaluate_instrument_identity("XYZ", "Northwind Gold Trust", asset("Contoso Gold Trust", "XYZ"))
    assert result["status"] == "conflict"
    # A partial brand overlap still does not verify which fund/share class.
    result = evaluate_instrument_identity("XYZ", "Northwind Gold Trust", asset("Northwind Silver Trust", "XYZ"))
    assert result["status"] == "unverified"


def test_unavailable_asset_lookup_does_not_fabricate_conflict():
    assert evaluate_instrument_identity("GOLD", "Barrick", None)["status"] == "unverified"
    assert evaluate_instrument_identity("GOLD", "Barrick", {"status": "unavailable"})["status"] == "unverified"


@pytest.mark.parametrize("identity", [asset(symbol="OTHER"), asset(status="inactive")])
def test_explicit_symbol_or_inactive_listing_conflict(identity):
    assert evaluate_instrument_identity("GOLD", "Gold.com, Inc.", identity)["status"] == "conflict"


def setup_case(identity_status="conflict"):
    identity = evaluate_instrument_identity("GOLD", "Barrick Mining Corporation", asset(), source_refs=["asset-source"])
    if identity_status == "unverified":
        identity = evaluate_instrument_identity("GOLD", "Barrick Mining Corporation", None)
    entry = {"lower": "100", "upper": "100", "currency": "USD", "as_of": "2026-09-16", "source_refs": ["quote"]}
    fact_id = "fact-gold"
    filing_ref = "filing-gold"
    v2 = {
        "direction": "long", "horizon": "12m", "issuer": "Barrick Mining Corporation", "asset_class": "equity",
        "recommended_shares": 50, "allocation_rationale": "Use the whole-share long-term allocation within the policy ceiling.",
        "thesis": {"variant_view": "The market underestimates operating improvement.", "market_expectation": "The market expects flat fundamentals.", "why_now": "The next review can change expectations.", "supporting_claim_ids": [fact_id], "strongest_opposing_explanation": "Commodity weakness persists.", "disconfirming_evidence": ["Guidance weakens."], "decision_change_conditions": ["EPS below 5 USD/share."], "source_refs": [filing_ref], "status": "complete"},
        "valuation_assumptions": {"methods": [{"name": "eps_multiple", "inputs": [{"key": "forecast_diluted_eps", "value": "8", "unit": "USD/share", "currency": "USD", "period": "2025-12-31", "kind": "assumption", "fact_claim_ids": [fact_id], "source_refs": [filing_ref], "rationale": "Forward EPS forecast anchored to the dated baseline."}, {"key": "exit_pe", "value": "15", "unit": "multiple", "kind": "assumption", "source_refs": [filing_ref], "rationale": "Bounded peer range assumption."}], "fact_claim_ids": [fact_id], "source_refs": [filing_ref], "forecast_eps_derivation": "Forward EPS forecast anchored to the dated baseline."}], "scenarios": {"bear": {"eps": "6", "pe": "12"}, "base": {"eps": "8", "pe": "15"}, "bull": {"eps": "10", "pe": "18"}}},
        "action_plan": {"entry_condition": "Buy at the retained quote.", "exit_condition": "Exit at the valuation target.", "invalidation_condition": "Exit below 90 USD.", "max_loss_basis": "scenario", "review_at": "2026-10-01T00:00:00Z", "status": "complete"},
    }
    candidate = {"ticker": "GOLD", "instrument": "Barrick Mining Corporation", "strategy": "long_term", "stance": "enter", "entry_zone": entry, "target_price": "120", "target_price_currency": "USD", "target_price_as_of": "2026-09-16", "target_price_basis": "Valuation assumption", "target_price_source_refs": ["quote"], "scenario_assessment": "usable", "watch_triggers": [{"type": "price", "condition": "Price reaches 100", "reopen_when": "Reassess at supported price", "operator": "at_or_below", "threshold": "100", "currency": "USD", "source_refs": ["quote"]}], **v2}
    payload = {
        "status": "completed", "summary": "Review the issuer thesis.", "allocation_mode": "combined", "candidate_briefs": [candidate],
    }
    snapshot = {
        "accounts": [{"id": "tfsa", "account_type": "tfsa", "reconciliation_status": "confirmed", "balances": [{"currency": "USD", "amount": "10000", "status": "confirmed", "observed_at": "2026-09-11"}]}],
        "portfolio_policy": {"status": "approved", "max_positions": 10, "limits": {"long_term": {"currency": "USD", "initial_notional": "5000", "max_notional": "7500"}}},
        "deterministic_market": {"candidates": [{"ticker": "GOLD", "instrument_identity": identity, "source_refs": ["quote"], "technical": {"frequencies": {"daily": {"sma20": "90", "log_returns": [1, 2, 3]}}}, "scenario": {"status": "complete", "calculation_status": "complete", "data_quality_status": "valid", "model_acceptance_status": "accepted", "forecast_accepted": True, "source_refs": ["quote"]}}]},
    }
    sources = {"quote": {"id": "quote", "source_type": "market_data", "content": "GOLD close 100 USD/share on 2026-09-16."}, "asset-source": {"id": "asset-source", "source_type": "asset_identity", "provider": "alpaca", "content": "Current GOLD listing identity observed on 2026-09-16."}, filing_ref: {"id": filing_ref, "source_type": "filing", "primary_evidence": True, "primary_coverage": "primary_issuer", "content": "Barrick Mining Corporation reported GAAP diluted EPS 8 USD/share for the year ended 2025-12-31.", "publication_at": "2026-01-15"}}
    raw_fact = {"claim": "Barrick Mining Corporation diluted EPS", "value": "8", "unit": "USD/share", "currency": "USD", "period": "2025-12-31", "period_end": "2025-12-31", "basis": "GAAP diluted", "source_ref": filing_ref, "locator": "L1", "subject": "Barrick Mining Corporation", "metric": "EPS"}
    validation = validate_fact_claim(raw_fact, {filing_ref: sources[filing_ref]["content"]}, source_metadata={filing_ref: {"source_type": "filing", "publication_at": "2026-01-15"}})
    payload["fact_claims"] = [{**raw_fact, "fact_id": fact_id, **{key: validation[key] for key in ("validation_status", "semantic_status", "binding_checks", "matched_excerpt", "freshness", "source_version") if key in validation}}]
    return payload, snapshot, sources


def test_identity_conflict_suppresses_all_numerical_decision_use_without_rewriting_history():
    payload, snapshot, sources = setup_case()
    before_payload, before_snapshot = deepcopy(payload), deepcopy(snapshot)
    result = build_case_decision("case", payload, snapshot, sources).candidates[0]
    assert result.outcome is None
    assert result.execution_state == "awaiting_input"
    assert result.entry is None and result.future_target is None
    assert result.sizing.shares is None and result.sizing.notional is None
    assert result.sizing.entry_price is None and result.sizing.stop_price is None
    assert result.watch_triggers == []
    assert result.technical_indicators == {}
    assert result.forecast.accepted is False
    assert result.forecast.data_quality_status == "invalid"
    assert result.instrument_identity["status"] == "conflict"
    assert any(b.key == "instrument_identity" and "asset-source" in b.source_refs for b in result.material_blockers)
    assert payload == before_payload and snapshot == before_snapshot


def test_conflicted_candidate_does_not_drain_valid_candidate_shared_budget():
    payload, snapshot, sources = setup_case()
    valid = deepcopy(payload["candidate_briefs"][0])
    valid.update(ticker="GLD", instrument="SPDR Gold Shares", issuer="SPDR Gold Shares")
    valid["action_plan"]["invalidation_condition"] = "EPS falls below 5 USD/share."
    valid["thesis"]["supporting_claim_ids"] = ["fact-gld"]
    valid["thesis"]["source_refs"] = ["filing-gld"]
    valid["valuation_assumptions"]["methods"][0]["fact_claim_ids"] = ["fact-gld"]
    valid["valuation_assumptions"]["methods"][0]["source_refs"] = ["filing-gld"]
    valid["valuation_assumptions"]["methods"][0]["inputs"][0]["fact_claim_ids"] = ["fact-gld"]
    valid["valuation_assumptions"]["methods"][0]["inputs"][0]["source_refs"] = ["filing-gld"]
    valid["valuation_assumptions"]["methods"][0]["inputs"][1]["source_refs"] = ["filing-gld"]
    payload["fact_claims"].append({**payload["fact_claims"][0], "fact_id": "fact-gld", "claim": "SPDR Gold Shares diluted EPS", "subject": "SPDR Gold Shares", "source_ref": "filing-gld"})
    sources["filing-gld"] = {"id": "filing-gld", "source_type": "filing", "primary_evidence": True, "primary_coverage": "primary_issuer", "content": "SPDR Gold Shares reported GAAP diluted EPS 8 USD/share for the year ended 2025-12-31.", "publication_at": "2026-01-15"}
    snapshot["deterministic_market"]["candidates"].append({"ticker": "GLD", "instrument_identity": {"status": "consistent", "asset_id": "asset-gld", "symbol": "GLD", "name": "SPDR Gold Shares"}, "source_refs": ["quote"]})
    payload["candidate_briefs"].append(valid)
    # The fixture quote is dated 2026-09-16; keep this regression test on its
    # intended evaluation clock instead of making it age with wall time.
    results = build_case_decision("case", payload, snapshot, sources, as_of="2026-09-17T00:00:00Z").candidates
    assert results[0].sizing.shares is None
    assert results[1].outcome == "recommend"
    assert results[1].sizing.shares == 50


def test_model_cannot_clear_code_owned_conflict_by_asserting_another_identity():
    payload, snapshot, sources = setup_case()
    payload["candidate_briefs"][0]["instrument_identity"] = {"status": "consistent"}
    payload["candidate_briefs"][0]["instrument"] = "Gold.com, Inc."
    result = build_case_decision("case", payload, snapshot, sources).candidates[0]
    assert result.instrument_identity["status"] == "conflict"
    assert result.sizing.shares is None


def test_quote_match_does_not_remain_a_verified_issuer_fact_after_identity_conflict():
    payload, snapshot, sources = setup_case()
    payload["fact_claims"] = [{"claim": "Claimed issuer closing price", "value": "100", "unit": "USD/share", "period": "2026-09-11", "source_ref": "quote", "locator": "L1", "validation_status": "validated"}]
    result = build_case_decision("case", payload, snapshot, sources).candidates[0]
    assert result.evidence[0].kind == "unknown"
    assert result.evidence[0].validation_status == "unavailable"
    assert payload["fact_claims"][0]["validation_status"] == "validated"


def test_unverified_lookup_preserves_other_supported_research():
    payload, snapshot, sources = setup_case("unverified")
    result = build_case_decision("case", payload, snapshot, sources).candidates[0]
    assert result.instrument_identity["status"] == "unverified"
    assert result.outcome == "watchlist"
    assert result.sizing.shares == 50
    assert result.recommendation_gate.status == "blocked"
    assert "instrument_identity" in result.recommendation_gate.missing_inputs


def test_identity_conflict_preserves_independent_asset_identity_fact():
    payload, snapshot, sources = setup_case()
    snapshot["deterministic_market"]["candidates"][0]["source_refs"].append("asset-source")
    payload["fact_claims"] = [
        {"claim": "Claimed issuer closing price", "value": "100", "unit": "USD/share", "period": "2026-09-11", "source_ref": "quote", "locator": "L1", "validation_status": "validated"},
        {"claim": "Current asset issuer is Gold.com, Inc.", "value": "Gold.com, Inc.", "unit": "issuer", "period": "2026-09-13", "source_ref": "asset-source", "locator": "L3", "validation_status": "validated"},
    ]
    result = build_case_decision("case", payload, snapshot, sources).candidates[0]
    price_fact, identity_fact = result.evidence
    assert price_fact.kind == "unknown" and price_fact.validation_status == "unavailable"
    assert identity_fact.kind == "fact"
    assert identity_fact.validation_status == "validated"


def test_identity_conflict_can_keep_an_evidence_reopen_condition():
    payload, snapshot, sources = setup_case()
    row = payload["candidate_briefs"][0]
    row["stance"] = "watch"
    row["watch_triggers"].append({"type": "evidence", "condition": "Resolve issuer and listing identity", "operator": "changes", "evidence_condition": "Dated issuer listing resolves name conflict", "reopen_when": "Review after issuer is verified", "source_refs": ["asset-source"]})
    result = build_case_decision("case", payload, snapshot, sources).candidates[0]
    assert result.outcome == "watchlist"
    assert result.execution_state == "awaiting_input"
    assert [t.type for t in result.watch_triggers] == ["evidence"]
    assert result.sizing.shares is None
