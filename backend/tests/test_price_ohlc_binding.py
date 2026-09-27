"""Historical OHLC watches need an explicit field bound to the cited bar."""
import json

import pytest

from backend.app.memory.repository import Repository
from backend.app.schemas import AgentOutputPayload, CandidateDecisionBrief, FactClaim, WatchTrigger


def normalize(claim_text, amount, *, symbol="BE", currency="USD", period="2026-09-10", complete=True):
    source = "Retained market bars\n" + json.dumps({"source_type": "market_bars", "metadata": {"currency": "USD"}}) + "\n" + json.dumps({
        "symbol": symbol, "currency": "USD", "timestamp": "2026-09-10T04:00:00Z", "complete": complete,
        "open": "259.86", "high": "270.98", "low": "257.24", "close": "258.47", "volume": "323093",
    })
    payload = AgentOutputPayload(
        status="completed", title="BE review", summary="Reassess the supported price.", analysis="A historical price is a review threshold, not fair value.",
        fact_claims=[FactClaim(claim=claim_text, value=amount, unit=f"{currency}/share", period=period, source_ref="bars", locator="L3")],
        candidate_briefs=[CandidateDecisionBrief(ticker="BE", stance="watch", watch_triggers=[WatchTrigger(
            type="price", condition="Reassess the historical price", operator="at_or_below", threshold=amount,
            currency=currency, source_refs=["bars"], reopen_when="Reassess fundamentals and price at this threshold",
        )])],
    )
    return Repository._normalize_decision_brief(payload, {"bars"}, source_content={"bars": source}, source_metadata={"bars": {"source_type": "market_bars"}}, run_ticker="BE").candidate_briefs[0]


@pytest.mark.parametrize("claim,amount", [
    ("BE historical daily low selected as a watch threshold", "257.24"),
    ("BE historical daily high", "270.98"),
    ("BE daily open", "259.86"),
    ("BE completed daily close", "258.47"),
    ("BE observed price", "258.47"),
])
def test_explicit_ohlc_field_matches_its_exact_dated_bar(claim, amount):
    candidate = normalize(claim, amount)
    assert len(candidate.watch_triggers) == 1
    assert candidate.watch_triggers[0].source_refs == ["bars"]


@pytest.mark.parametrize("claim,amount", [
    ("BE completed daily close", "270.98"),
    ("BE completed daily close", "323093"),
    ("BE historical daily low", "270.98"),
    ("BE observed price", "257.24"),
    ("BE historical volume high", "270.98"),
    ("BE daily close and low", "257.24"),
])
def test_other_numeric_fields_cannot_be_relabelled_as_the_claimed_price(claim, amount):
    assert normalize(claim, amount).watch_triggers == []


@pytest.mark.parametrize("kwargs", [
    {"symbol": "OTHER"}, {"currency": "CAD"}, {"period": "2026-09-11"}, {"complete": False},
])
def test_low_price_keeps_symbol_currency_date_and_completion_binding(kwargs):
    assert normalize("BE historical daily low", "257.24", **kwargs).watch_triggers == []
