import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app.config import Settings
from backend.app.memory.repository import Repository
from backend.app.research.valuation_corporate_actions import (
    ENDPOINT, TYPES, VERSION, acquire_valuation_corporate_actions,
    fetch_split_actions, split_coverage_from_sources,
)
from backend.app.research.valuation_history import build_valuation_research_context, trailing_eps_as_of
from backend.app.research.earnings_forecast import build_earnings_bridge
from backend.tests.test_valuation_history import concept, eps, market_source, quarters, sec_row, source


def action(kind="forward_splits", **changes):
    return {"corporate_actions": {kind: [{"id": "action-1", "symbol": "ACME", "ex_date": "2025-03-10", "process_date": "2025-03-09", "new_rate": 10, "old_rate": 1, "rate": "0.1", **changes}]}, "next_page_token": None}


def connector(pages, status=200, available=True):
    calls = []

    def http_get(url, params, headers, timeout):
        calls.append((url, params))
        page = pages[min(len(calls) - 1, len(pages) - 1)]
        return SimpleNamespace(status_code=status, payload=page)

    return SimpleNamespace(credentials=SimpleNamespace(alpaca_available=available, alpaca_api_key_id="example-id", alpaca_api_secret_key="example-secret"), http_get=http_get, calls=calls)


def envelope(pages):
    return {"version": VERSION, "provider": "alpaca", "source_type": "corporate_actions", "request": {"endpoint": ENDPOINT, "symbol": "ACME", "start": "2017-01-01", "end": "2026-01-01", "types": TYPES, "data_quality": "complete"}, "retrieved_at": "2025-04-01T00:00:00Z", "status": "complete", "coverage_complete": True, "pages": pages, "issues": []}


def retained(pages):
    return source(json.dumps(envelope(pages)), "actions", ENDPOINT)


def coverage(archive):
    return split_coverage_from_sources([archive], ticker="ACME", start="2018-04-01", end="2025-04-01")


def test_empty_complete_provider_response_establishes_no_reported_actions():
    c = connector([{"corporate_actions": {}, "next_page_token": None}])
    value = fetch_split_actions(c, "ACME", start="2017-01-01", end="2026-01-01")
    assert value["coverage_complete"] is True and value["status"] == "complete"
    assert c.calls[0][0] == ENDPOINT and c.calls[0][1]["types"] == TYPES
    archive = source(json.dumps(value), "actions", ENDPOINT)
    parsed = coverage(archive)
    assert parsed["coverage_complete"] is True and parsed["events"] == []
    assert parsed["source_refs"] == ["actions"]
    assert "example-secret" not in json.dumps(value)


@pytest.mark.parametrize("kind,new,old,ratio", [("forward_splits", 10, 1, "10"), ("reverse_splits", 1, 20, "0.05"), ("stock_dividends", 0, 0, "1.1")])
def test_split_types_use_ex_date_and_retained_ratios(kind, new, old, ratio):
    result = coverage(retained([action(kind, new_rate=new, old_rate=old)]))
    assert result["coverage_complete"] is True
    item = result["events"][0]
    assert item["date"] == "2025-03-10" and item["date"] != item["process_date"]
    assert item["share_ratio"] == ratio


@pytest.mark.parametrize("mutation", [{"symbol": "OTHER"}, {"ex_date": None}, {"new_rate": 0}, {"old_rate": -1}])
def test_malformed_or_mismatched_events_never_claim_complete_coverage(mutation):
    result = coverage(retained([action(**mutation)]))
    assert result["coverage_complete"] is False and result["issues"]
    assert result["events"] == []


def test_pagination_is_bounded_and_incomplete_pages_never_become_empty_success():
    first = action() | {"next_page_token": "next"}
    second = action(id="action-2", ex_date="2025-04-10")
    c = connector([first, second])
    value = fetch_split_actions(c, "ACME", start="2020-01-01", end="2026-01-01")
    assert value["status"] == "complete" and len(value["pages"]) == 2
    assert c.calls[1][1]["page_token"] == "next"
    looping = connector([first])
    partial = fetch_split_actions(looping, "ACME", start="2020-01-01", end="2026-01-01")
    assert partial["status"] == "partial" and partial["coverage_complete"] is False
    assert len(looping.calls) <= 3
    limited = fetch_split_actions(c, "ACME", start="2020-01-01", end="2026-01-01", max_pages=1)
    assert len(limited["pages"]) <= 1


def test_unavailable_provider_and_missing_credentials_remain_unverified():
    c = connector([{}], status=403)
    assert fetch_split_actions(c, "ACME", start="2020-01-01", end="2026-01-01")["status"] == "unavailable"
    c = connector([{}], available=False)
    assert fetch_split_actions(c, "ACME", start="2020-01-01", end="2026-01-01")["status"] == "unavailable"
    assert c.calls == []
    archive = retained([action()])
    assert coverage(archive | {"content_hash": "changed"})["status"] == "unverified"
    assert coverage(archive | {"url": "https://other.example/actions"})["status"] == "unverified"


def test_known_split_excludes_mismatched_eps_then_allows_later_restatement():
    before = eps("2024-01-01", "2024-12-31", 10, "2025-02-01")
    after = eps("2024-01-01", "2024-12-31", 1, "2025-04-01")
    splits = coverage(retained([action()]))["events"]
    assert trailing_eps_as_of([before, after], point_date="2025-02-28", split_events=splits)["value"] == "10.000000"
    assert trailing_eps_as_of([before, after], point_date="2025-03-31", split_events=splits)["status"] == "unavailable"
    assert trailing_eps_as_of([before, after], point_date="2025-04-02", split_events=splits)["value"] == "1.000000"


def test_context_uses_retained_action_source_and_preserves_complete_empty_coverage():
    financial = concept([sec_row("2024-01-01", "2024-12-31", 10, "2025-02-20")])
    archive = retained([{"corporate_actions": {}, "next_page_token": None}])
    result = build_valuation_research_context([financial, market_source(), archive], ticker="ACME", cik="1234", as_of="2025-04-01", fiscal_period="FY2024 Q4", period_end="2024-12-31")
    assert result["historical_pe"]["split_coverage"]["coverage_complete"] is True
    assert len(result["historical_pe"]["points"]) == 1
    assert "actions" in {row["id"] for row in result["sources"]}


def test_unverified_discontinuity_is_excluded_not_invented_as_split_or_adjustment():
    financial = concept([sec_row("2024-01-01", "2024-12-31", 10, "2025-02-20")])
    price = market_source()
    lines = price["content"].splitlines()
    before = json.loads(lines[2]) | {"timestamp": "2025-03-10T04:00:00Z", "close": "100"}
    after = before | {"timestamp": "2025-03-11T04:00:00Z", "close": "10"}
    price = source("\n".join([*lines[:2], json.dumps(before), json.dumps(after)]), "prices", price["url"])
    result = build_valuation_research_context([financial, price], ticker="ACME", cik="1234", as_of="2025-04-01", fiscal_period="FY2024 Q4", period_end="2024-12-31")
    history = result["historical_pe"]
    assert history["points"] == []
    assert history["split_coverage"]["status"] == "unverified"
    assert "no split ratio was inferred" in history["gaps"][0]["reason"]
    event = history["share_basis_review_events"][0]
    assert event["type"] == "unexplained_price_discontinuity" and "share_ratio" not in event
    assert after["close"] == "10"


def test_acquisition_archives_and_reuses_complete_empty_coverage(tmp_path, monkeypatch):
    config = Settings(project_root=Path(__file__).resolve().parents[2], data_dir=tmp_path, enable_market_connectors=True)
    repo = Repository(config=config)
    c = connector([{"corporate_actions": {}, "next_page_token": None}])
    monkeypatch.setattr("backend.app.research.valuation_corporate_actions.AlpacaConnector", lambda **_: c)
    ids = asyncio.run(acquire_valuation_corporate_actions(repo, config, "ACME", namespace="real"))
    assert len(ids) == 1
    assert asyncio.run(acquire_valuation_corporate_actions(repo, config, "ACME", namespace="real")) == ids
    assert len(c.calls) == 1
    request = c.calls[0][1]
    assert (datetime.fromisoformat(request["end"]) - datetime.fromisoformat(request["start"])).days > 7 * 365
    assert len(repo.sources("real")) == 1


def split_quarter_packet():
    # Synthetic NVDA-like 10-for-1 split: Q1 was published before the split,
    # while Q2/Q3 use the new share basis and the prior year is not restated.
    rows = [eps("2023-01-01", "2023-12-31", 4, "2024-02-01"), *quarters(2023, [0.5, 0.8, 1, 1.7]), *quarters(2024, [1, 0.12, 0.15])]
    actions = [{"date": "2024-06-10", "type": "forward_split", "share_ratio": "10", "source_refs": ["actions"]}]
    return rows, actions


def test_quarter_bridge_preserves_actual_eps_but_withholds_mixed_basis_totals_and_forecast():
    rows, actions = split_quarter_packet()
    result = build_earnings_bridge(rows, fiscal_period="FY2024 Q3", period_end="2024-09-30", as_of="2024-11-01", split_events=actions)
    assert [row["value"] for row in result["quarters"][:3]] == ["1.000000", "0.120000", "0.150000"]
    assert all(row["kind"] == "reported" for row in result["quarters"][:3])
    assert result["quarters"][0]["share_basis_status"] == "unreconciled"
    assert "original share basis" in result["quarters"][0]["rationale"]
    assert result["quarters"][3]["kind"] == "missing"
    assert "Projection withheld" in result["quarters"][3]["rationale"]
    assert result["reported_total"] is None and result["projected_total"] is None and result["full_year_total"] is None
    assert result["share_basis_status"] == "unreconciled"
    assert "actions" in result["source_refs"]


def test_later_restated_quarters_restore_comparable_forecasting_without_code_adjustments():
    rows, actions = split_quarter_packet()
    revised = [row | {"value": str(float(row["value"]) / 10), "available_at": "2024-07-01"} for row in rows if row["available_at"] < "2024-06-10"]
    result = build_earnings_bridge([*rows, *revised], fiscal_period="FY2024 Q3", period_end="2024-09-30", as_of="2024-11-01", split_events=actions)
    assert result["quarters"][0]["value"] == "0.100000"
    assert result["reported_total"] == "0.370000"
    assert result["quarters"][3]["kind"] == "projection"
    assert result["full_year_total"] is not None
    assert result.get("share_basis_status") != "unreconciled"


def test_reported_post_split_annual_eps_can_supply_total_without_summing_old_quarters():
    rows, actions = split_quarter_packet()
    rows.extend([*quarters(2024, [1, 0.12, 0.15, 0.2]), eps("2024-01-01", "2024-12-31", 0.57, "2025-02-01")])
    result = build_earnings_bridge(rows, fiscal_period="FY2024 Q4", period_end="2024-12-31", as_of="2025-03-01", split_events=actions)
    assert result["reported_total"] is None
    assert result["annual_reported_value"] == result["full_year_total"] == "0.570000"
    assert result["quarters"][0]["value"] == "1.000000"
    assert result["quarters"][0]["share_basis_status"] == "unreconciled"
