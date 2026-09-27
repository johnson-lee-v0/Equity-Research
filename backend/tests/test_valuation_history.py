from datetime import date, timedelta
import hashlib
import json

from backend.app.research.earnings_forecast import build_earnings_bridge
from backend.app.research.valuation_history import (
    build_historical_pe, build_valuation_research_context, raw_price_history,
    sec_eps_history, trailing_eps_as_of,
)


def eps(start, end, value, available, sid="eps"):
    return {"issuer": "ACME", "basis": "GAAP diluted", "period_start": start, "period_end": end, "value": str(value), "currency": "USD", "unit": "USD/share", "available_at": available, "source_refs": [sid], "duration_days": (date.fromisoformat(end) - date.fromisoformat(start)).days + 1}


def source(content, sid="eps", url="https://data.sec.gov/api/xbrl/companyconcept/CIK0000001234/us-gaap/EarningsPerShareDiluted.json"):
    return {"id": sid, "url": url, "content": content, "content_hash": hashlib.sha256(content.encode()).hexdigest(), "version": 1, "publication_at": "2026-09-24"}


def concept(rows):
    return source(json.dumps({"cik": 1234, "taxonomy": "us-gaap", "tag": "EarningsPerShareDiluted", "units": {"USD/shares": rows}}))


def sec_row(start, end, value, filed, fy=2025, fp="FY", form="10-K"):
    return {"start": start, "end": end, "val": value, "filed": filed, "fy": fy, "fp": fp, "form": form, "accn": "0000001234-25-000001"}


def test_sec_keeps_vintages_and_real_periods_not_filing_fiscal_year():
    archive = concept([sec_row("2023-01-01", "2023-12-31", 8, "2024-02-20", 2024), sec_row("2023-01-01", "2023-12-31", 9, "2025-02-20", 2025)])
    rows = sec_eps_history(archive, ticker="ACME", cik="1234", as_of="2026-01-01")
    assert len(rows) == 2
    assert rows[0]["period_end"] == "2023-12-31"
    assert rows[0]["available_at"] == "2024-02-21"
    assert rows[0]["source_quote"] in archive["content"]
    assert all(row["source_refs"] == ["eps"] for row in rows)
    assert sec_eps_history(archive | {"content_hash": "changed"}, ticker="ACME", cik="1234", as_of="2026-01-01") == []
    assert sec_eps_history(archive, ticker="ACME", cik="9999", as_of="2026-01-01") == []


def test_historical_eps_never_uses_later_restatement_or_same_day_filing():
    archive = concept([sec_row("2024-01-01", "2024-12-31", 10, "2025-02-20"), sec_row("2024-01-01", "2024-12-31", 99, "2026-02-20")])
    rows = sec_eps_history(archive, ticker="ACME", cik="1234", as_of="2026-03-01")
    assert trailing_eps_as_of(rows, point_date="2025-02-20")["status"] == "unavailable"
    assert trailing_eps_as_of(rows, point_date="2025-02-21")["value"] == "10.000000"


def test_ttm_uses_annual_plus_current_ytd_minus_comparable_ytd():
    rows = [eps("2024-01-01", "2024-12-31", 10, "2025-02-01"), eps("2025-01-01", "2025-06-30", 6, "2025-08-01"), eps("2024-01-01", "2024-06-30", 4, "2024-08-01")]
    result = trailing_eps_as_of(rows, point_date="2025-08-31")
    assert result["value"] == "12.000000"
    assert [row["sign"] for row in result["components"]] == [1, 1, -1]
    assert result["period_end"] == "2025-06-30"
    assert result["available_as_of"] == "2025-08-01"
    assert trailing_eps_as_of(rows[:-1], point_date="2025-08-31")["status"] == "unavailable"


def test_conflicting_vintages_negative_eps_and_splits_are_gaps():
    row = eps("2024-01-01", "2024-12-31", 10, "2025-02-01")
    assert trailing_eps_as_of([row, row | {"value": "11"}], point_date="2025-03-01")["status"] == "unavailable"
    assert "zero or negative" in trailing_eps_as_of([row | {"value": "-1"}], point_date="2025-03-01")["reason"]
    assert "split" in trailing_eps_as_of([row], point_date="2025-03-01", split_events=[{"date": "2025-02-15"}])["reason"]
    assert "190 days" in trailing_eps_as_of([row], point_date="2025-09-01")["reason"]


def test_historical_month_samples_and_range_have_no_daily_or_future_leak():
    rows = [eps("2024-01-01", "2024-12-31", 10, "2025-02-01")]
    prices = [{"date": day, "close": close, "source_refs": ["prices"], "currency": "USD"} for day, close in [("2025-02-03", "90"), ("2025-02-28", "100"), ("2025-03-31", "120"), ("2025-04-30", "10000")]]
    result = build_historical_pe(rows, prices, as_of="2025-04-01")
    assert [point["pe"] for point in result["points"]] == ["10.000000", "12.000000"]
    assert result["min"] == "10.000000" and result["median"] == "11.000000" and result["max"] == "12.000000"
    assert result["points"][0]["source_refs"] == ["prices", "eps"]
    assert result["points"][0]["eps_components"][0]["period_end"] == "2024-12-31"


def market_source(*, adjustment="raw", symbol="ACME", currency="USD", close="100", complete=True):
    head = {"provider": "alpaca", "source_type": "market_bars", "metadata": {"timeframe": "1Day", "adjustment": adjustment, "currency": currency, "symbols": [symbol], "feed": "iex"}}
    return source("Road2M canonical research source\n" + json.dumps(head) + "\n" + json.dumps({"symbol": symbol, "timestamp": "2025-02-28T05:00:00Z", "close": close, "complete": complete}), "prices", "https://data.alpaca.markets/v2/stocks/bars")


def test_raw_prices_require_currency_identity_completion_and_raw_adjustments():
    rows, _ = raw_price_history([market_source()], ticker="ACME", as_of="2025-03-01")
    assert len(rows) == 1 and rows[0]["locator"] == "L3"
    for invalid in [market_source(adjustment="split"), market_source(symbol="OTHER"), market_source(currency=None), market_source(complete=False), market_source(close="-1")]:
        assert raw_price_history([invalid], ticker="ACME", as_of="2025-03-01")[0] == []
    assert raw_price_history([market_source(), market_source(close="101")], ticker="ACME", as_of="2025-03-01")[0] == []


def quarters(year, values, *, available_year=None):
    bounds = [("01-01", "03-31"), ("04-01", "06-30"), ("07-01", "09-30"), ("10-01", "12-31")]
    return [eps(f"{year}-{start}", f"{year}-{end}", value, (date.fromisoformat(f"{year}-{end}") + timedelta(days=30)).isoformat()) for (start, end), value in zip(bounds, values)]


def test_q3_bridge_shows_three_actuals_and_only_missing_future_quarter_forecast():
    rows = [eps("2024-01-01", "2024-12-31", 10, "2025-02-01"), *quarters(2024, [1, 2, 3, 4]), *quarters(2025, [1.2, 2.4, 3.6])]
    bridge = build_earnings_bridge(rows, fiscal_period="FY2025 Q3", period_end="2025-09-30", as_of="2025-11-01")
    assert [row["kind"] for row in bridge["quarters"]] == ["reported", "reported", "reported", "projection"]
    assert bridge["quarters"][3]["value"] == "4.800000"
    assert bridge["reported_total"] == "7.200000"
    assert bridge["full_year_total"] == "12.000000"
    assert "not company guidance" in bridge["quarters"][3]["rationale"]


def test_missing_historical_quarter_is_not_replaced_by_ytd_or_projected():
    rows = [eps("2024-01-01", "2024-12-31", 10, "2025-02-01"), *quarters(2024, [1, 2, 3, 4]), *quarters(2025, [1.2, 2.4, 3.6])]
    rows = [row for row in rows if row["period_start"] != "2025-04-01"]
    rows.append(eps("2025-01-01", "2025-06-30", 3.6, "2025-07-30"))
    bridge = build_earnings_bridge(rows, fiscal_period="FY2025 Q3", period_end="2025-09-30", as_of="2025-11-01")
    assert bridge["quarters"][1]["kind"] == "missing"
    assert bridge["quarters"][3]["kind"] == "missing"  # no defensible complete comparable growth
    assert bridge["full_year_total"] is None


def test_q4_seasonality_proxy_is_a_forecast_assumption_not_reported_actual():
    rows = [eps("2024-01-01", "2024-12-31", 10, "2025-02-01"), *quarters(2024, [1, 2, 3]), *quarters(2025, [1.2, 2.4, 3.6])]
    bridge = build_earnings_bridge(rows, fiscal_period="FY2025 Q3", period_end="2025-09-30", as_of="2026-01-10")
    assert bridge["quarters"][3]["kind"] == "projection"
    assert bridge["quarters"][3]["value"] == "4.800000"
    assert bridge["quarters"][3]["prior_year_kind"] == "modeled_seasonal_residual"
    assert "not reported Q4 EPS" in bridge["quarters"][3]["rationale"]


def test_complete_fiscal_year_keeps_reported_annual_and_four_actual_quarters():
    rows = [eps("2024-01-01", "2024-12-31", 10, "2025-02-01"), eps("2025-01-01", "2025-12-31", 12.01, "2026-02-01"), *quarters(2025, [1.2, 2.4, 3.6, 4.8])]
    bridge = build_earnings_bridge(rows, fiscal_period="FY2025 Q4", period_end="2025-12-31", as_of="2026-02-15")
    assert bridge["reported_quarters"] == 4 and bridge["projected_quarters"] == 0
    assert bridge["reported_total"] == "12.000000"
    assert bridge["annual_reported_value"] == bridge["full_year_total"] == "12.010000"
    assert "different diluted share counts" in bridge["coverage_note"]


def test_retail_fiscal_calendar_uses_exact_12_and_16_week_quarter_dates():
    starts = ["2025-09-01", "2025-11-24", "2026-02-16", "2026-05-11"]
    ends = ["2025-11-23", "2026-02-15", "2026-05-10", "2026-08-30"]
    rows = [eps("2024-09-02", "2025-08-31", 18.21, "2025-10-09"), eps("2025-09-01", "2026-08-30", 20.76, "2026-09-25")]
    rows += [eps(start, end, value, (date.fromisoformat(end) + timedelta(days=25)).isoformat()) for start, end, value in zip(starts, ends, [4.5, 4.58, 4.93, 6.75])]
    bridge = build_earnings_bridge(rows, fiscal_period="Q4 FY2026", period_end="2026-08-30", as_of="2026-09-26")
    assert bridge["reported_quarters"] == 4
    assert [point["period_end"] for point in bridge["quarters"]] == ends
    assert bridge["full_year_total"] == "20.760000"


def test_context_rejects_changed_archives_and_has_clickable_source_projection():
    archive = concept([sec_row("2024-01-01", "2024-12-31", 10, "2025-02-20")])
    context = build_valuation_research_context([archive, market_source()], ticker="ACME", cik="1234", as_of="2025-03-01", fiscal_period="FY2024 Q4", period_end="2024-12-31")
    assert len(context["historical_pe"]["points"]) == 1
    assert {item["source_id"] for item in context["sources"]} == {"eps", "prices"}
    assert context["current_earnings"]["value"] == "10.000000"
    unavailable = build_valuation_research_context([archive | {"content_hash": "changed"}, market_source()], ticker="ACME", cik="1234", as_of="2025-03-01", fiscal_period="FY2024 Q4", period_end="2024-12-31")
    assert unavailable["historical_pe"]["points"] == []


def test_latest_bound_release_updates_current_earnings_only_after_publication():
    archive = concept([sec_row("2024-01-01", "2024-12-31", 10, "2025-02-20")])
    release = source("An independently bound issuer financial table.", "release", "https://investor.acme.com/results") | {"publication_at": None}
    extra = eps("2025-01-01", "2025-12-31", 12, "2026-02-02", "release")
    arguments = dict(ticker="ACME", cik="1234", fiscal_period="FY2025 Q4", period_end="2025-12-31", extra_observations=[extra])
    current = build_valuation_research_context([archive, release], as_of="2026-02-02", **arguments)
    assert current["current_earnings"]["value"] == "12.000000"
    assert current["current_earnings"]["source_refs"] == ["release"]
    before = build_valuation_research_context([archive, release], as_of="2026-02-01", **arguments)
    assert before["current_earnings"]["status"] == "unavailable"
