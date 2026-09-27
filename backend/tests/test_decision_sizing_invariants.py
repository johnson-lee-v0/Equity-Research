from decimal import Decimal

import pytest

from backend.app.research.calculations import calculate_multi_candidate_sizing, calculate_position_size
from backend.app.research.decisions import build_case_decision
from backend.app.research.fact_validation import validate_fact_claim


SOURCE = {"quote": {"id": "quote", "source_type": "market_data", "content": "A retained market quote observation on 2026-09-01."}}


def account(account_id="nr", amount="10000", kind="nonregistered"):
    return {"id": account_id, "account_type": kind, "reconciliation_status": "confirmed", "balances": [{"currency": "USD", "amount": amount, "status": "confirmed", "observed_at": "2026-09-01"}]}


def policy(status="approved"):
    return {"status": status, "max_positions": 10, "limits": {"long_term": {"currency": "USD", "initial_notional": "5000", "max_notional": "7500"}, "trade": {"currency": "USD", "initial_notional": "2500", "max_notional": "5000", "planned_loss_limit": "250"}}}


def candidate(ticker, **overrides):
    strategy = overrides.get("strategy", "trade")
    stop = overrides["stop_price"] if "stop_price" in overrides else "10"
    filing_ref = f"filing-{str(ticker).lower()}"
    quote_ref = f"quote-{str(ticker).lower()}"
    invalidation = f"Exit below {stop} USD." if stop is not None else "Reassess if EPS falls below 5 USD/share."
    method_inputs = [
        {"key": "forecast_diluted_eps", "value": "8", "unit": "USD/share", "currency": "USD", "period": "2025-12-31", "kind": "assumption", "fact_claim_ids": [f"fact-{str(ticker).lower()}"], "source_refs": [filing_ref], "rationale": "Forward diluted EPS forecast anchored to the dated GAAP baseline."},
        {"key": "exit_pe", "value": "15", "unit": "multiple", "kind": "assumption", "source_refs": [filing_ref], "rationale": "Bounded peer multiple assumption."},
    ]
    row = {
        "ticker": ticker,
        "instrument": f"{ticker} Inc",
        "issuer": f"{ticker} Inc",
        "direction": "long",
        "strategy": strategy,
        "horizon": "3m" if strategy == "trade" else "12m",
        "stance": "enter",
        "entry_zone": {"lower": "100", "upper": "100", "currency": "USD", "as_of": "2026-09-01", "source_refs": [quote_ref]},
        "stop_price": stop,
        "recommended_shares": 2 if strategy == "trade" else 50,
        "allocation_rationale": "Use a defined whole-share proposal within policy capacity.",
        "thesis": {"variant_view": "The market underestimates operating improvement.", "market_expectation": "The market expects flat fundamentals.", "why_now": "The next review can change expectations.", "supporting_claim_ids": [f"fact-{str(ticker).lower()}"], "strongest_opposing_explanation": "Demand weakness persists.", "disconfirming_evidence": ["Guidance weakens."], "decision_change_conditions": ["EPS below 5 USD/share."], "source_refs": [filing_ref], "status": "complete"},
        "valuation_assumptions": {"methods": [{"name": "eps_multiple", "inputs": method_inputs, "fact_claim_ids": [f"fact-{str(ticker).lower()}"], "source_refs": [filing_ref], "forecast_eps_derivation": "Forward diluted EPS forecast anchored to the dated GAAP baseline."}], "scenarios": {"bear": {"eps": "6", "pe": "12"}, "base": {"eps": "8", "pe": "15"}, "bull": {"eps": "10", "pe": "18"}}},
        "action_plan": {"entry_condition": "Buy at the retained quote.", "exit_condition": "Exit at the valuation target.", "invalidation_condition": invalidation, "max_loss_basis": "stop" if stop is not None else "scenario", "review_at": "2026-10-01T00:00:00Z", "status": "complete"},
    }
    row.update(overrides)
    return row


def decision(rows, snapshot=None, **overrides):
    payload = {"status": "completed", "summary": "Fixture assessment", "candidate_briefs": rows, **overrides}
    effective_snapshot = dict(snapshot or {"accounts": [account()], "portfolio_policy": policy()})
    effective_snapshot["positions"] = [
        {
            **dict(position),
            "issuer": dict(position).get("issuer") or f"{dict(position).get('symbol') or dict(position).get('ticker') or 'HOLD'} Inc",
            "sector": dict(position).get("sector") or "Synthetic",
            "currency": dict(position).get("currency") or "USD",
            "market_value": dict(position).get("market_value") or "100",
            "quantity": dict(position).get("quantity") or "1",
            "price": dict(position).get("price") or "100",
            "price_as_of": dict(position).get("price_as_of") or "2026-09-01",
            "price_source_id": dict(position).get("price_source_id") or "quote",
            "observed_at": dict(position).get("observed_at") or "2026-09-01",
            "status": dict(position).get("status") or "confirmed",
        }
        for position in effective_snapshot.get("positions", [])
        if isinstance(position, dict)
    ]
    market = dict(effective_snapshot.get("deterministic_market") or {})
    market_rows = list(market.get("candidates") or [])
    known = {str(item.get("ticker") or item.get("symbol") or "").upper() for item in market_rows if isinstance(item, dict)}
    claims = list(payload.get("fact_claims") or [])
    source_map = dict(SOURCE)
    for row in rows:
        ticker = str(row.get("ticker") or "").upper()
        if ticker and ticker not in known:
            market_rows.append({"ticker": ticker, "asset_class": row.get("asset_class") or "equity", "instrument_identity": {"status": "consistent", "asset_id": f"asset-{ticker.lower()}", "symbol": ticker, "name": row.get("instrument") or f"{ticker} Inc"}})
        filing_ref = f"filing-{ticker.lower()}"
        quote_ref = f"quote-{ticker.lower()}"
        source_map.setdefault(quote_ref, {"id": quote_ref, "source_type": "market_data", "content": f"{ticker} close 100 USD/share on 2026-09-01."})
        source_map.setdefault(filing_ref, {"id": filing_ref, "source_type": "filing", "primary_evidence": True, "primary_coverage": "primary_issuer", "content": f"{ticker} Inc reported GAAP diluted EPS 8 USD/share for the year ended 2025-12-31.", "publication_at": "2026-01-15"})
        raw_fact = {"claim": f"{ticker} diluted EPS", "value": "8", "unit": "USD/share", "currency": "USD", "period": "2025-12-31", "period_end": "2025-12-31", "basis": "GAAP diluted", "source_ref": filing_ref, "locator": "L1", "subject": f"{ticker} Inc", "metric": "EPS"}
        validation = validate_fact_claim(raw_fact, {filing_ref: source_map[filing_ref]["content"]}, source_metadata={filing_ref: {"source_type": "filing", "publication_at": "2026-01-15"}})
        claims.append({**raw_fact, "fact_id": f"fact-{ticker.lower()}", **{key: validation[key] for key in ("validation_status", "semantic_status", "binding_checks", "matched_excerpt", "freshness", "source_version") if key in validation}})
    effective_snapshot["deterministic_market"] = {**market, "candidates": market_rows}
    payload.setdefault("fact_claims", claims)
    return build_case_decision("fixture", payload, effective_snapshot, source_map, as_of="2026-09-02T00:00:00Z")


@pytest.mark.parametrize("direction", ["long", "short"])
def test_missing_entry_currency_does_not_request_fx_to_unknown_currency(direction):
    result = calculate_position_size("ABC", entry_price=None, currency=None, account_id="nr", approved_budget="2500", short_budget="2500", budget_currency="USD", margin_available="2500", margin_currency="USD", available_cash="5000", available_cash_currency="USD", direction=direction, borrow_available=True)
    assert result["execution_state"] == "awaiting_input"
    assert "entry_currency" in result["missing_inputs"]
    assert not any(item.startswith("fx_rate:") for item in result["missing_inputs"])
    assert result["shares"] is None


def test_known_different_entry_currency_still_requires_explicit_fx():
    result = calculate_position_size("ABC", entry_price="100", currency="CAD", account_id="nr", approved_budget="2500", budget_currency="USD")
    assert "fx_rate:USD_CAD" in result["missing_inputs"]
    assert result["shares"] is None


def test_multiple_candidates_retain_loss_cap_and_protective_stop():
    result = decision([candidate("AAA"), candidate("BBB")])
    assert [item.sizing.shares for item in result.candidates] == [2, 2]
    for item in result.candidates:
        assert item.outcome == "recommend"
        assert item.sizing.risk_budget == "250.00000000"
        assert Decimal(item.sizing.shares) * (Decimal("100") - Decimal(item.sizing.stop_price)) <= Decimal("250")


def test_declined_comparison_does_not_reserve_the_recommendation_budget():
    result = decision([candidate("AAA", stop_price="90"), candidate("BBB", stance="avoid", stop_price="90")])
    assert result.allocation_mode == "combined"
    assert result.candidates[0].sizing.shares == 25
    assert result.candidates[0].sizing.approved_budget == "2500.00000000"
    assert result.candidates[1].outcome == "decline"


def test_gold_alternatives_are_independent_possible_positions():
    snapshot = {"accounts": [account("tfsa", kind="tfsa")], "portfolio_policy": policy(), "positions": [{"symbol": f"HOLD{i}"} for i in range(9)]}
    rows = [candidate("GOLD_ETF", strategy="long_term", stop_price=None), candidate("GOLD_MINER", strategy="long_term", stop_price=None)]
    alternatives = decision(rows, snapshot, allocation_mode="alternatives")
    assert alternatives.allocation_mode == "alternatives"
    assert [item.sizing.shares for item in alternatives.candidates] == [50, 50]
    assert [item.outcome for item in alternatives.candidates] == ["recommend", "recommend"]
    assert all(item.sizing.approved_budget == "5000.00000000" for item in alternatives.candidates)
    combined = decision([candidate("GOLD_ETF", strategy="long_term", stop_price=None, recommended_shares=25), candidate("GOLD_MINER", strategy="long_term", stop_price=None, recommended_shares=25)], snapshot, allocation_mode="combined")
    assert combined.allocation_mode == "combined"
    assert [item.sizing.shares for item in combined.candidates] == [25, 25]
    assert [item.outcome for item in combined.candidates] == ["recommend", None]


def test_multiple_trades_missing_stop_do_not_bypass_loss_policy():
    result = decision([candidate("AAA", stop_price=None), candidate("BBB", stop_price=None)])
    assert all(item.outcome is None and item.sizing.shares is None for item in result.candidates)
    assert all("stop_price" in item.sizing.missing_inputs for item in result.candidates)


def test_proposed_unfunded_policy_keeps_illustrative_shares():
    snapshot = {"accounts": [{"id": "nr", "account_type": "nonregistered", "reconciliation_status": "unconfirmed", "balances": []}], "portfolio_policy": policy("proposed")}
    result = decision([candidate("AAA", stop_price="90"), candidate("BBB", stop_price="90")], snapshot)
    assert [item.sizing.shares for item in result.candidates] == [12, 12]
    for item in result.candidates:
        assert item.execution_state == "awaiting_input"
        assert item.outcome is None
        assert item.sizing.proposed_budget == "1250.00000000"
        assert item.sizing.approved_budget is None
        assert item.sizing.available_cash is None
        assert "confirm_proposed_budget_and_funding" in item.sizing.missing_inputs


def test_combined_sizing_caps_the_cash_pool_before_allocating():
    result = calculate_multi_candidate_sizing([{"ticker": symbol, "entry_price": "100", "currency": "USD"} for symbol in ("AAA", "BBB")], approved_budget="5000", budget_currency="USD", account_id="nr", snapshot={"accounts": [account(amount="1000")]})
    assert result["execution_state"] == "ready"
    assert [item["shares"] for item in result["items"]] == [5, 5]
    assert Decimal(result["total_notional"]) <= Decimal("1000")


def test_different_strategy_groups_cannot_spend_same_cash_twice():
    snapshot = {"accounts": [account(amount="1000")], "portfolio_policy": policy()}
    result = decision([candidate("AAA", account_id="nr", strategy="long_term"), candidate("BBB", account_id="nr", stop_price="90")], snapshot)
    assert sum(Decimal(item.sizing.notional or "0") for item in result.candidates) <= Decimal("1000")
    assert result.candidates[1].outcome is None
    assert "unallocated_account_cash" in result.candidates[1].sizing.missing_inputs


def test_explicit_shared_budget_is_reserved_across_accounts_and_currencies():
    cad_account = account("cad")
    cad_account["balances"][0]["currency"] = "CAD"
    snapshot = {"accounts": [account("usd"), cad_account], "portfolio_policy": policy(), "approved_budget": "1000", "approved_budget_currency": "USD", "fx_rates": {"USD/CAD": "1.4"}}
    first = candidate("AAA", account_id="usd", strategy="long_term")
    second = candidate("BBB", account_id="cad", stop_price="90")
    second["entry_zone"]["currency"] = "CAD"
    result = decision([first, second], snapshot)
    total_usd = Decimal(result.candidates[0].sizing.notional or "0") + Decimal(result.candidates[1].sizing.notional or "0") / Decimal("1.4")
    assert total_usd <= Decimal("1000")
    assert result.candidates[1].outcome is None


def test_fx_observation_does_not_claim_currency_exchange_already_happened():
    cad_account = account()
    cad_account["balances"][0]["currency"] = "CAD"
    sized = calculate_position_size("AAA", entry_price="100", currency="USD", account_id="nr", approved_budget="1000", snapshot={"accounts": [cad_account]}, fx_rates={"CAD/USD": "0.7"})
    assert sized["shares"] == 10
    assert sized["available_cash"] is None
    assert sized["execution_state"] == "awaiting_input"
    assert "confirm_account_currency_funding" in sized["missing_inputs"]


def test_candidates_keep_account_eligibility_in_shared_sizing():
    snapshot = {"accounts": [account("tfsa", kind="tfsa")], "portfolio_policy": policy()}
    result = decision([candidate("AAA", account_id="tfsa"), candidate("BBB", account_id="tfsa")], snapshot)
    assert all(item.outcome is None for item in result.candidates)
    assert all("tfsa_trade_restriction" in item.sizing.recommend_requires for item in result.candidates)


def test_combined_candidates_reserve_distinct_holding_slots():
    snapshot = {"accounts": [account()], "portfolio_policy": policy(), "positions": [{"symbol": f"HOLD{i}"} for i in range(9)]}
    result = decision([candidate("AAA"), candidate("BBB")], snapshot)
    assert [item.outcome for item in result.candidates] == ["recommend", None]
    assert "available_holding_slot" in result.candidates[1].sizing.missing_inputs


@pytest.mark.parametrize("overrides", [{"status": "needs_review", "decision_disposition": "defer"}, {"missing_data": ["The key earnings claim did not validate."]}, {"decision_disposition": "defer"}])
def test_materially_unvalidated_or_deferred_output_cannot_recommend(overrides):
    result = decision([candidate("AAA")], **overrides)
    assert result.outcome is None
    assert result.execution_state == "awaiting_input"
    assert result.candidates[0].sizing.shares == 2


@pytest.mark.parametrize("threshold,upper", [("NaN", None), ("-1", None), ("0", None), ("100", "99"), ("100", "Infinity"), ("999", None)])
def test_watch_price_must_be_valid_and_supported(threshold, upper):
    trigger = {"type": "price", "operator": "between" if upper else "at_or_below", "threshold": threshold, "upper_threshold": upper, "currency": "USD", "condition": "Wait for the entry condition", "reopen_when": "Reassess", "source_refs": ["quote"]}
    result = decision([candidate("AAA", stance="watch", entry_zone=None, watch_triggers=[trigger])])
    assert result.outcome is None
    assert not result.candidates[0].watch_triggers


def test_watch_price_cannot_borrow_another_currency_or_author_quote():
    trigger = {"type": "price", "operator": "at_or_below", "threshold": "100", "currency": "CAD", "condition": "Wait", "reopen_when": "Reassess", "source_refs": ["quote"]}
    result = decision([candidate("AAA", stance="watch", watch_triggers=[trigger])])
    assert all(item.currency != "CAD" for item in result.candidates[0].watch_triggers)
    payload = {"status": "completed", "candidate_briefs": [candidate("AAA", stance="watch", entry_zone=None, watch_triggers=[{**trigger, "currency": "USD"}])]}
    result = build_case_decision("fixture", payload, {}, {"quote": {"id": "quote", "provider": "reddit"}})
    assert result.outcome is None


def test_user_provided_source_type_remains_author_evidence():
    payload = {"status": "completed", "candidate_briefs": [candidate("AAA")], "fact_claims": [{"claim": "AAA price", "value": "100", "source_ref": "quote", "validation_status": "validated"}]}
    result = build_case_decision("fixture", payload, {}, {"quote": {"id": "quote", "source_type": "user_provided"}})
    assert result.candidates[0].entry is None
    assert result.candidates[0].evidence[0].kind == "opinion"


def test_risk_budget_conversion_and_account_requirement_are_enforced():
    sized = calculate_position_size("AAA", entry_price="100", currency="CAD", account_id="nr", approved_budget="5000", risk_budget="250", risk_budget_currency="USD", stop_price="90", fx_rates={"USD/CAD": "1.4"})
    assert sized["shares"] == 35
    assert sized["risk_budget"] == "350.00000000"
    restricted = calculate_position_size("AAA", entry_price="100", currency="USD", account_id="nr", approved_budget="5000", recommend_requires=["funding_transfer_confirmation"])
    assert restricted["shares"] == 50
    assert restricted["execution_state"] == "awaiting_input"


def test_a_stop_on_the_profit_side_is_not_a_loss_limit():
    sized = calculate_position_size("AAA", entry_price="100", currency="USD", account_id="nr", approved_budget="5000", risk_budget="250", stop_price="110")
    assert sized["shares"] is None
    assert "protective_stop_price" in sized["missing_inputs"]
