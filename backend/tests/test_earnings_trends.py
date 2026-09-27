"""Trend boundary tests: observed links, exact evidence, period/basis and guidance."""
import asyncio
import json
from datetime import date
from types import SimpleNamespace

import pytest

from backend.app.config import Settings
from backend.app.memory.repository import Repository
from backend.app.research.discovery import FetchedSource
from backend.app.research.earnings_sources import EarningsAcquisition
from backend.app.research.earnings_trends import (
    EarningsTrends, _build_series, _fiscal_period, _guidance_review,
    _metric_windows, _quoted_number, METRICS,
)

NOW = "2026-09-25T12:00:00Z"
EVENT = {"fiscal_period": "Q4 FY2026 / FY2026", "period_end": "2026-08-30", "earnings_date": "2026-09-24"}
COMPANY = {"ticker": "EXM", "name": "Example Membership Inc", "cik": "0000000123"}


def document(identifier="s1", period="Q4 FY2026", published="2026-09-24", text=None):
    return {"source_id": identifier, "url": f"https://example.com/{identifier}", "title": f"Example Membership {period} call", "fiscal_period": period, "period_end": "2026-08-30", "published_at": published, "content": text or "Q4 FY2026. Our US and Canada renewal rate was 92.7%. Worldwide renewal rate was 89.7%. Net sales increased 9.2% this quarter. We ended with 82 million paid household members, up 6.8%. Capital expenditures for FY2026 were $6.2 billion. For FY2027 we expect capital expenditures of $7.5 billion. Capital expenditures increased because we accelerated new locations."}


def observation(metric="renewal_us_canada", value=92.7, **changes):
    return {"source_id": "s1", "metric": metric, "period": "Q4 FY2026", "frequency": "quarterly", "kind": "actual", "value": value, "low": None, "high": None, "quote": "Our US and Canada renewal rate was 92.7%.", "period_quote": "Q4 FY2026", **changes}


def validate(items, docs=None):
    return EarningsTrends(None)._validate_observations({"observations": items, "capex_explanations": []}, docs or [document()], EVENT)


def test_normalizes_composite_fiscal_period_without_accepting_conflicting_years_or_quarters():
    assert _fiscal_period("Q4 FY2026 / FY2026") == (2026, 4)
    assert _fiscal_period("FY2026 / Q4 FY2026") == (2026, 4)
    assert _fiscal_period("Q4 FY2025 / FY2026") is None
    assert _fiscal_period("Q1 FY2026 / Q4 FY2026") is None
    assert _fiscal_period("Q4 2026") == (2026, 4)


def test_accepts_only_exact_source_quote_and_correct_geography():
    points, _, rejected = validate([observation(), observation(metric="renewal_worldwide")])
    assert len(points["renewal_us_canada"]) == 1 and points["renewal_worldwide"] == []
    assert rejected == 1
    points, _, rejected = validate([observation(value=93.2), observation(source_id="invented"), observation(quote="Our US and Canada renewal rate was 93.2%.")])
    assert not any(points.values()) and rejected == 3


def test_rejects_quarter_ytd_annual_mix_unknown_units_and_nonfinite_values():
    points, _, rejected = validate([observation(period="Q3 FY2026"), observation(frequency="annual"), observation(value=float("nan")), observation(value=True)])
    assert not any(points.values()) and rejected == 4
    assert _quoted_number("Capital expenditures were $700 million", .7, "USD billions")
    assert not _quoted_number("Capital expenditures were $700 million", 700, "USD billions")
    assert not _quoted_number("renewal improved 20 basis points", 20, "percent")


@pytest.mark.parametrize("word", ["down", "decreased", "declined", "fell", "dropped", "lower"])
def test_directional_sales_declines_bind_negative_values(word):
    quote = f"For the quarter, revenues were {word} 1 percent on a reported basis."
    doc = document(text="Q4 FY2026. " + quote)
    assert validate([observation(metric="net_sales_growth", value=-1, quote=quote)], [doc])[2] == 0
    assert validate([observation(metric="net_sales_growth", value=1, quote=quote)], [doc])[2] == 1


@pytest.mark.parametrize("quote,value", [
    ("Quarterly net sales declined by approximately 2.5%.", -2.5),
    ("Quarterly net sales recorded a 2.5% decline.", -2.5),
    ("Quarterly net sales growth was -2.5%.", -2.5),
    ("Quarterly revenues increased by 2.5%.", 2.5),
    ("Quarterly revenues were down -2.5%.", -2.5),
    ("Quarterly revenues were down −2.5%.", -2.5),
])
def test_directional_number_binding_keeps_explicit_consistent_signs(quote, value):
    assert validate([observation(metric="net_sales_growth", value=value, quote=quote)], [document(text="Q4 FY2026. " + quote)])[2] == 0


@pytest.mark.parametrize("quote,value", [
    ("Quarterly revenues increased -1%.", -1),
    ("Quarterly revenues increased -1%.", 1),
    ("Quarterly revenues were down +1%.", -1),
    ("Quarterly revenues were down +1%.", 1),
    ("Quarterly revenues were not down 1%.", -1),
    ("Quarterly revenues increased 2%, while gross margin declined 1%.", -1),
    ("Quarterly revenues increased 2%, while paid household members declined 1%.", -1),
])
def test_direction_cannot_borrow_another_metric_or_conflict_with_explicit_sign(quote, value):
    assert validate([observation(metric="net_sales_growth", value=value, quote=quote)], [document(text="Q4 FY2026. " + quote)])[2] == 1


def test_reported_growth_does_not_borrow_currency_neutral_or_segment_percentage():
    quote = "Fourth quarter revenues were $11.0 billion, down 1 percent on a reported basis and down 4 percent on a currency-neutral basis."
    doc = document(text="Q4 FY2026. " + quote)
    assert validate([observation(metric="net_sales_growth", value=-1, quote=quote)], [doc])[2] == 0
    assert validate([observation(metric="net_sales_growth", value=-4, quote=quote)], [doc])[2] == 1
    for text in ("On a currency-neutral basis, quarterly revenues declined 4%.",
                 "Quarterly NIKE Direct revenues were down 7% on a reported basis.",
                 "Quarterly revenues in Greater China were down 7% on a reported basis.",
                 "Quarterly comparable sales declined 7%."):
        value = -4 if "4%" in text else -7
        assert validate([observation(metric="net_sales_growth", value=value, quote=text)], [document(text="Q4 FY2026. " + text)])[2] == 1


def test_margin_level_after_decline_and_negative_membership_growth_keep_their_measure():
    quote = "Gross margin declined 300 basis points to 40.6% on a reported basis."
    assert validate([observation(metric="gross_margin", value=40.6, quote=quote)], [document(text="Q4 FY2026. " + quote)])[2] == 0
    quote = "Paid household members declined 1%, while cardholders increased 2%."
    doc = document(text="Q4 FY2026. " + quote)
    assert validate([observation(metric="paid_members_growth", value=-1, quote=quote)], [doc])[2] == 0
    assert validate([observation(metric="paid_members_growth", value=2, quote=quote)], [doc])[2] == 1


def test_guidance_range_checks_units_midpoint_and_explicit_target_year():
    doc = document(text="Q4 FY2026. We expect FY2027 capital expenditures of $6.5 to $7 billion.")
    guidance = observation(metric="capex", period="FY2027", frequency="annual", kind="guidance", value=6.75, low=6.5, high=7, quote="We expect FY2027 capital expenditures of $6.5 to $7 billion.", period_quote="FY2027")
    points, _, rejected = validate([guidance], [doc])
    assert rejected == 0 and points["capex"][0]["low"] == 6.5
    for change in ({"value": 6.8}, {"low": 6}, {"period": "FY2028"}, {"kind": "actual"}):
        assert validate([guidance | change], [doc])[2] == 1


def test_same_year_ytd_capex_cannot_be_presented_as_completed_annual_actual():
    doc = document(period="Q3 FY2026", text="Q3 FY2026. Capital expenditures for FY2026 were $6.2 billion.")
    point = observation(metric="capex", period="FY2026", frequency="annual", value=6.2, quote="Capital expenditures for FY2026 were $6.2 billion.", period_quote="FY2026")
    assert validate([point], [doc])[2] == 1


def test_capex_explanations_remain_verbatim_and_require_spending_cause():
    service = EarningsTrends(None)
    doc = document()
    result = service._validate_observations({"observations": [], "capex_explanations": [
        {"source_id": "s1", "period": "FY2026", "quote": "Capital expenditures increased because we accelerated new locations."},
        {"source_id": "s1", "period": "FY2026", "quote": "Capital expenditures increased because inflation was excessive."},
        {"source_id": "s1", "period": "FY2026", "quote": "Our US and Canada renewal rate was 92.7%."},
    ]}, [doc], EVENT)
    assert len(result[1]) == 1
    assert result[1][0]["text"] == result[1][0]["quote"]
    assert "not independent verification" in result[1][0]["interpretation"]


def capex_point(period, value, kind="actual", published="2026-09-24", low=None, high=None):
    return {"period": period, "value": value, "kind": kind, "published_at": published, "source_id": f"{period}-{kind}-{published}", "url": "https://example.com", "quote": "Verified quote", "low": low, "high": high}


def test_guidance_comparison_uses_earlier_date_range_midpoint_and_retains_revisions():
    items = [capex_point("FY2026", 6.5, "guidance", "2025-09-24", 6, 7), capex_point("FY2026", 6.8, "guidance", "2026-03-05", 6.8, 6.8), capex_point("FY2026", 7.2), capex_point("FY2025", 6.0, "guidance", "2026-09-24", 6, 6), capex_point("FY2025", 5.5, published="2025-09-24")]
    result = _guidance_review(items, [])
    assert len(result["comparisons"]) == 1
    comparison = result["comparisons"][0]
    assert comparison["variance"] == .7 and comparison["variance_pct"] == 10.77
    assert comparison["within_range"] is False and comparison["initiality"] == "earliest_observed"
    assert len(comparison["revisions"]) == 1
    assert "not guaranteed" in result["coverage"]
    assert "above" in result["summary"] and "does not establish" in result["summary"]


def test_series_keeps_missing_periods_null_and_guidance_after_actuals():
    points = {metric: [] for metric in METRICS}
    points["renewal_worldwide"] = [observation(metric="renewal_worldwide") | {"published_at": "2026-09-24"}]
    points["capex"] = [capex_point("FY2026", 6.2), capex_point("FY2027", 7.5, "guidance", low=7.5, high=7.5)]
    gaps = []
    result = _build_series(points, EVENT, gaps)
    renewal = next(item for item in result if item["id"] == "renewal_worldwide")
    assert [point["period"] for point in renewal["points"]] == ["Q3 FY2025", "Q4 FY2025", "Q1 FY2026", "Q2 FY2026", "Q3 FY2026", "Q4 FY2026"]
    assert all(point["value"] is None for point in renewal["points"][:5])
    assert renewal["area_ids"] == ["demand", "outlook"]
    capex = next(item for item in result if item["id"] == "capex")
    assert len(capex["points"]) == 6 and capex["points"][-1]["kind"] == "guidance"
    assert capex["points"][0]["period"] == "FY2022"


def test_metric_windows_include_late_capex_and_renewal_in_large_transcript():
    text = "Quarter results. " * 2000 + "Worldwide renewal was 90.2%. " + "Prepared remarks. " * 3000 + "FY2027 capital expenditures expected $7.5 billion."
    excerpt = _metric_windows(text)
    assert "Worldwide renewal was 90.2%" in excerpt
    assert "FY2027 capital expenditures expected $7.5 billion" in excerpt
    assert len(excerpt) <= 19000


@pytest.fixture
def service(tmp_path):
    config = Settings(data_dir=tmp_path)
    repo = Repository(config=config)
    pages, stages = {}, []

    def fetch(url, **kwargs):
        return pages.get(url, FetchedSource(url, url, "", url, NOW, "Not found"))

    def discover(stage, prompt, schema):
        stages.append(stage)
        return {"sources": []} if stage == "historical_trends" else {"observations": [], "capex_explanations": []}

    acquisition = EarningsAcquisition(repo, None, config, fetcher=fetch, discoverer=discover, today=date(2026, 9, 25))
    return SimpleNamespace(acquisition=acquisition, repo=repo, pages=pages, stages=stages, trends=EarningsTrends(acquisition))


def test_observed_index_links_select_periods_without_inventing_urls(service):
    url = "https://stockanalysis.com/stocks/exm/transcripts/"
    links = tuple(f"https://stockanalysis.com/stocks/exm/transcripts/{year}{quarter}-q{quarter}-{year}/" for year in range(2018, 2028) for quarter in range(1, 5)) + ("https://evil.test/stocks/exm/transcripts/1-q4-2025/",)
    # SimpleNamespace supports the index adapter independently of the shared
    # FetchedSource schema version in installations being upgraded.
    service.pages[url] = SimpleNamespace(title="Example Membership earnings transcripts", content="Example Membership", error=None, links=links)
    result = asyncio.run(service.trends._indexed_candidates(COMPANY, EVENT))
    assert len(result) == 10
    assert {item["fiscal_period"] for item in result} == {"Q3 FY2025", "Q4 FY2025", "Q1 FY2026", "Q2 FY2026", "Q3 FY2026", "Q4 FY2026", "Q4 FY2021", "Q4 FY2022", "Q4 FY2023", "Q4 FY2024"}
    assert all(item["url"] in links and "evil" not in item["url"] for item in result)


def test_historical_source_checks_actual_company_quarter_and_date(service):
    url = "https://example.com/history"
    service.acquisition.discoverer = lambda stage, prompt, schema: {"sources": [{"url": url, "kind": "transcript", "fiscal_period": "Q4 FY2025", "period_end": "2025-08-31", "published_at": "2025-09-25"}]}
    service.pages[url] = FetchedSource(url, url, "Example Membership Q3 FY2025 Earnings Transcript September 25, 2025. " + "Revenue increased. " * 60, "Example Membership Q3 FY2025", NOW)
    gaps = []
    assert asyncio.run(service.trends._historical(COMPANY, EVENT, [], gaps)) == []
    assert "did not verify" in gaps[0]
    service.pages[url] = FetchedSource(url, url, "Example Membership Q4 FY2025 Earnings Transcript September 25, 2025. " + "Revenue increased. " * 60, "Example Membership Q4 FY2025", NOW)
    service.acquisition._pages.clear()
    result = asyncio.run(service.trends._historical(COMPANY, EVENT, [], []))
    assert len(result) == 1 and result[0]["period_end"] is None
    assert service.repo.source_packet("real", [result[0]["source_id"]])[0]["content"] == service.pages[url].content


def test_sec_annual_cash_basis_rejects_ytd_future_and_comparative_fy_mislabel(service):
    url = "https://data.sec.gov/api/xbrl/companyconcept/CIK0000000123/us-gaap/PaymentsToAcquirePropertyPlantAndEquipment.json"
    rows = [
        {"start": "2022-08-29", "end": "2023-09-03", "val": 4300000000, "accn": "a", "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-10-08"},
        {"start": "2025-09-01", "end": "2026-05-10", "val": 3500000000, "accn": "b", "fy": 2026, "fp": "Q3", "form": "10-Q", "filed": "2026-06-03"},
        {"start": "2025-09-01", "end": "2026-08-30", "val": 6800000000, "accn": "c", "fy": 2026, "fp": "FY", "form": "10-K", "filed": "2026-10-08"},
    ]
    content = json.dumps({"cik": 123, "taxonomy": "us-gaap", "tag": "PaymentsToAcquirePropertyPlantAndEquipment", "units": {"USD": rows}})
    service.pages[url] = FetchedSource(url, url, content, url, NOW)
    points, source = asyncio.run(service.trends._sec_capex(COMPANY, EVENT, []))
    assert len(points) == 1 and points[0]["period"] == "FY2023" and points[0]["value"] == 4.3
    assert json.loads(points[0]["quote"]) == rows[0]
    assert source["source_id"] and points[0]["quote"] in content


def late_annual_capex(service, *, observed="2026-09-26T10:00:00Z"):
    url = "https://data.sec.gov/api/xbrl/companyconcept/CIK0000000123/us-gaap/PaymentsToAcquirePropertyPlantAndEquipment.json"
    event = {"fiscal_period": "Q4 FY2026", "period_end": "2026-05-31", "earnings_date": "2026-06-30"}
    rows = [
        {"start": "2025-06-01", "end": "2026-05-31", "val": 684000000, "accn": "annual", "fy": 2026, "fp": "FY", "form": "10-K", "filed": "2026-07-15"},
        {"start": "2024-06-01", "end": "2025-05-31", "val": 430000000, "accn": "future", "fy": 2026, "fp": "FY", "form": "10-K", "filed": "2026-10-01"},
    ]
    content = json.dumps({"cik": 123, "taxonomy": "us-gaap", "tag": "PaymentsToAcquirePropertyPlantAndEquipment", "units": {"USD": rows}})
    service.pages[url] = FetchedSource(url, url, content, url, observed)
    return event, rows


def test_sec_capex_uses_research_cutoff_after_call_but_rejects_future_filings(service):
    event, rows = late_annual_capex(service)
    points, source = asyncio.run(service.trends._sec_capex(COMPANY, event, [], research_as_of="2026-09-26T11:00:00Z"))
    assert [(row["period"], row["value"]) for row in points] == [("FY2026", .684)]
    assert json.loads(points[0]["quote"]) == rows[0]
    assert points[0]["published_at"] == source["published_at"] == "2026-07-15"
    assert source["event_as_of"] == "2026-06-30"
    assert source["filing_cutoff"] == "2026-09-26"
    assert source["source_observed_at"] == "2026-09-26T10:00:00Z"


@pytest.mark.parametrize("cutoff,observed", [
    ("2026-06-30", "2026-09-26T10:00:00Z"),
    ("2026-09-26", "2026-07-01T10:00:00Z"),
])
def test_research_and_source_observation_both_bound_the_sec_filing_window(service, cutoff, observed):
    event, _ = late_annual_capex(service, observed=observed)
    assert asyncio.run(service.trends._sec_capex(COMPANY, event, [], research_as_of=cutoff)) == ([], None)


def test_collection_freezes_cutoff_before_long_running_work(service, monkeypatch):
    from backend.app.research import earnings_trends, earnings_primary
    event, _ = late_annual_capex(service, observed="2026-10-02T10:00:00Z")
    clock = iter(["2026-09-26T10:00:00Z", "2026-10-02T12:00:00Z"])
    monkeypatch.setattr(earnings_trends, "_now", lambda: next(clock))
    async def no_history(*args, **kwargs):
        return []
    async def no_primary(*args, **kwargs):
        return {"documents": [], "points": [], "gaps": [], "checks": []}
    monkeypatch.setattr(service.trends, "_historical", no_history)
    monkeypatch.setattr(earnings_primary, "recover_primary_trends", no_primary)
    result = asyncio.run(service.trends.collect(COMPANY, event, {}))
    assert result["as_of"] == "2026-09-26T10:00:00Z" and result["completed_at"] == "2026-10-02T12:00:00Z"
    assert result["event_as_of"] == "2026-06-30"
    actual = [point for series in result["series"] for point in series["points"] if point.get("value") is not None]
    assert [(point["period"], point["value"]) for point in actual] == [("FY2026", .684)]
    assert result["sources"][0]["filing_cutoff"] == "2026-09-26"


def test_guidance_after_actual_publication_still_cannot_count_as_a_forecast():
    actual = capex_point("FY2026", .684, published="2026-07-15")
    after = capex_point("FY2026", .684, "guidance", low=.684, high=.684, published="2026-08-01")
    assert _guidance_review([actual, after], [])["comparisons"] == []


def test_unavailable_sources_are_gaps_not_fake_zero_values(service):
    result = asyncio.run(service.trends.collect(COMPANY, EVENT, {}))
    assert result["status"] == "unavailable" and result["series"] == []
    assert result["capex_guidance"]["comparisons"] == []
    assert result["gaps"]


def test_metric_amount_cannot_borrow_another_geography_or_time_basis():
    quote = "Our renewal rate was 92.3% in the U.S. and Canada and 89.8% worldwide."
    doc = document(text="Q4 FY2026. " + quote)
    assert validate([observation(value=92.3, quote=quote)], [doc])[2] == 0
    assert validate([observation(value=89.8, quote=quote)], [doc])[2] == 1
    assert validate([observation(metric="renewal_worldwide", value=89.8, quote=quote)], [doc])[2] == 0
    quote = "Capital expenditure was $2.21 billion in Q4 and $6.4 billion for the full year."
    doc = document(text="Q4 FY2026. " + quote)
    base = observation(metric="capex", period="FY2026", frequency="annual", value=6.4, quote=quote)
    assert validate([base], [doc])[2] == 0
    assert validate([base | {"value": 2.21}], [doc])[2] == 1


def test_rejects_annual_growth_quarter_capex_and_forecast_labeled_actual():
    cases = [
        ("net_sales_growth", "Q4 FY2026", "quarterly", 9.9, "For fiscal 2026, net sales increased 9.9% for the full fiscal year."),
        ("capex", "FY2026", "annual", 1.5, "Capital expenditures for the fourth quarter were $1.5 billion."),
        ("capex", "FY2026", "annual", 6.5, "We expect capital expenditures of $6.5 billion in fiscal 2026."),
    ]
    for metric, period, frequency, value, quote in cases:
        item = observation(metric=metric, period=period, frequency=frequency, value=value, quote=quote)
        assert validate([item], [document(text="Q4 FY2026. " + quote)])[2] == 1


def test_explanations_require_cause_and_matching_period():
    quote = "Capital expenditures for fiscal 2027 will be higher at approximately $7.5 billion."
    result = EarningsTrends(None)._validate_observations({"observations": [], "capex_explanations": [{"source_id": "s1", "period": "FY2022", "quote": quote}]}, [document(text="Q4 FY2026. " + quote)], EVENT)
    assert result[1] == []


def test_sec_fiscal_year_anchor_handles_first_quarter_in_prior_calendar_year():
    from backend.app.research.earnings_trends import _annual_fiscal_year
    assert _annual_fiscal_year("2025-08-31", "2025-11-23", (2026, 1), "0831") == 2025
    assert _annual_fiscal_year("2024-09-01", "2025-11-23", (2026, 1), "0831") == 2024
    assert _annual_fiscal_year("2025-08-31", "2026-05-10", (2026, 3), "0831") == 2025


def test_declared_period_cannot_borrow_guidance_year_from_later_text():
    from backend.app.research.earnings_trends import _declared_period
    assert _declared_period("Example Membership Q4 2025 Earnings Call Transcript. For FY2026, capex will be higher.") == (2025, 4)
    assert _declared_period("Example Membership Q4 FY2025 Earnings Call Transcript") == (2025, 4)


def test_extraction_uses_spoken_body_and_retains_metadata_for_revalidation(service):
    doc = document(text="Example Membership Q4 FY2026. Editorial summary: worldwide renewal 99.5%.\nOperator\nWelcome.\nJane Doe — Chief Financial Officer\nWorldwide renewal rate was 89.8%.\nOperator\nThis concludes today's call. You may now disconnect.\nNewsletter.")
    doc["kind"] = "transcript"
    prompts = []
    service.trends.extractor = lambda prompt, schema: prompts.append(prompt) or {"observations": [], "capex_explanations": []}
    asyncio.run(service.trends._extract([doc], EVENT))
    assert "Editorial summary" not in prompts[0] and "Newsletter" not in prompts[0]
    fake = observation(metric="renewal_worldwide", value=99.5, quote="Editorial summary: worldwide renewal 99.5%.")
    assert validate([fake], [doc])[2] == 1


def test_period_quote_uses_the_same_retained_transcript_text_as_metric_quote():
    period_quote = "Thank you, operator. Hello, everyone, and welcome to our first quarter fiscal 2026 results."
    quote = "This quarter, revenues were up 1% on a reported basis and down 1% on a currency-neutral basis."
    doc = document(period="Q1 FY2026", text=period_quote.replace("operator. Hello", "operator.Hello") + " " + quote)
    doc["extraction_content"] = period_quote + " " + quote
    item = observation(metric="net_sales_growth", period="Q1 FY2026", period_quote=period_quote, value=1, quote=quote)
    points, _, rejected = validate([item], [doc])
    assert rejected == 0 and points["net_sales_growth"][0]["value"] == 1
    assert points["net_sales_growth"][0]["quote"] == quote
    assert validate([item | {"period_quote": period_quote.replace("first", "second")}], [doc])[2] == 1
    assert validate([item | {"period": "Q2 FY2026"}], [doc])[2] == 1


def test_publication_date_uses_call_date_instead_of_live_share_price_timestamp():
    from backend.app.research.earnings_trends import _publication_date
    header = 'Costco Q3 2026 Earnings Call Transcript\nAt close: Sep 25, 2026, 4:00 PM EDT\nEarnings Call: Q3 2026\nMay 28, 2026\nFull Transcript'
    assert _publication_date(header) == '2026-05-28'


def test_guidance_bounds_must_belong_to_one_actual_range_not_old_and_new_estimates():
    from backend.app.research.earnings_trends import _quoted_guidance_range
    quote = 'Prior capex was $5 billion. We expect capital expenditures of $6.5 to $7 billion in FY2027.'
    assert _quoted_guidance_range(quote, 6.5, 7)
    assert not _quoted_guidance_range(quote, 5, 7)
    assert _quoted_guidance_range('We expect $600 million to $750 million of capex.', .6, .75)
    assert not _quoted_guidance_range('We raised capex guidance from $5 to $7 billion.', 5, 7)
    assert not _quoted_guidance_range('We expect C$7 billion capex.', 7, 7)


def test_annual_capex_preserves_comma_linked_period_and_total_year_wording():
    for value, quote in [
        (4.71, 'Capital expenditure in Q4 was approximately $1.58 billion, bringing the total year spend to $4.71 billion.'),
        (4.32, 'CapEx spend in Q4 was approximately $1.56 billion, and for all of fiscal 2023, it totaled $4.32 billion.'),
    ]:
        period = 'FY2023' if value == 4.32 else 'FY2026'
        doc = document(period='Q4 FY2023' if value == 4.32 else 'Q4 FY2026', text='Q4 FY2023. Q4 FY2026. '+quote)
        item = observation(metric='capex', period=period, frequency='annual', value=value, quote=quote, period_quote=doc['fiscal_period'])
        assert validate([item], [doc])[2] == 0
        assert validate([item | {'value': 1.58 if value == 4.71 else 1.56}], [doc])[2] == 1


def test_inequality_actual_is_qualified_and_excluded_from_exact_guidance_variance():
    quote = 'Capital expenditure in Q4 was approximately $1.97 billion, and for the full year was a little under $5.5 billion.'
    item = observation(metric='capex', period='FY2026', frequency='annual', value=5.5, quote=quote)
    points, _, rejected = validate([item], [document(text='Q4 FY2026. '+quote)])
    assert rejected == 0 and points['capex'][0]['qualifier'] == 'less_than'
    guidance = capex_point('FY2026', 5, 'guidance', '2025-09-25', low=5, high=5)
    assert _guidance_review([*points['capex'], guidance], [])['comparisons'] == []


def cache_artifact(service, *, instruction='Source old-id text', model=None, namespace='real'):
    import hashlib
    from backend.app.research.earnings_trends import _EXTRACTION_SCHEMA, VERSION
    folder = service.acquisition.config.evidence_dir / 'research-workflows/trend-extraction/completed-attempt'
    folder.mkdir(parents=True)
    provenance = {'attempt_id':'completed-attempt','version':VERSION,'status':'completed','namespace':namespace,'model':model or {'provider':'codex','model':'configured'},'source_documents':[{'url':'https://example.com/call','source_id':'old-id'}],'prompt_hash':hashlib.sha256(instruction.encode()).hexdigest()}
    (folder/'provenance.json').write_text(json.dumps(provenance))
    (folder/'output-schema.json').write_text(json.dumps(_EXTRACTION_SCHEMA))
    (folder/'result.json').write_text(json.dumps({'observations':[observation(source_id='old-id')],'capex_explanations':[]}))
    return folder


def test_extraction_retry_reuses_exact_prompt_after_source_id_rename(service):
    folder = cache_artifact(service)
    result = service.trends._reuse_extraction('Source new-id text', {'provider':'codex','model':'configured'}, [{'url':'https://example.com/call','source_id':'new-id'}])
    assert result and result[1] == 'completed-attempt'
    assert result[0]['observations'][0]['source_id'] == 'new-id'
    assert json.loads((folder/'result.json').read_text())['observations'][0]['source_id'] == 'old-id'


@pytest.mark.parametrize('change', ['text', 'model', 'namespace', 'schema', 'url'])
def test_extraction_retry_never_reuses_changed_evidence_model_namespace_schema_or_urls(service, change):
    folder = cache_artifact(service, namespace='other' if change=='namespace' else 'real')
    if change == 'schema':
        (folder/'output-schema.json').write_text('{}')
    result = service.trends._reuse_extraction('Source new-id changed text' if change=='text' else 'Source new-id text', {'provider':'codex','model':'other' if change=='model' else 'configured'}, [{'url':'https://example.com/other' if change=='url' else 'https://example.com/call','source_id':'new-id'}])
    assert result is None


def test_word_hyphen_spacing_is_typographic_not_a_fabricated_quote():
    quote = 'We ended Q3 with 79.6 million paid household members, up 6.8% versus last year, and 142.8 million cardholders, up 6.6% year-over-year.'
    doc = document(text='Q4 FY2026. '+quote.replace('year-over-year', 'year- over- year'))
    item = observation(metric='paid_members_growth', value=6.8, quote=quote)
    assert validate([item], [doc])[2] == 0


def test_omitted_full_year_capex_bound_is_recovered_as_bound_not_exact_actual():
    quote = 'Capital expenditure in Q4 was approximately $1.97 billion, and for the full year was a little under $5.5 billion.'
    doc = document(text='Q4 FY2026. '+quote)
    points, _, rejected = validate([], [doc])
    assert rejected == 0 and points['capex'][0]['value'] == 5.5
    assert points['capex'][0]['qualifier'] == 'less_than'
    doc['fiscal_period'] = 'Q3 FY2026'
    assert not validate([], [doc])[0]['capex']


def test_capex_followup_can_select_first_quarter_guidance_sources(service):
    url = 'https://stockanalysis.com/stocks/exm/transcripts/'
    links = tuple(f'https://stockanalysis.com/stocks/exm/transcripts/{year}1-q1-{year}/' for year in (2022,2023,2024,2025,2026))
    service.pages[url] = SimpleNamespace(title='Example Membership',content='Example Membership',error=None,links=links)
    result = asyncio.run(service.trends._indexed_candidates(COMPANY, EVENT, only_periods={(2022,1),(2024,1),(2025,1)}))
    assert [item['fiscal_period'] for item in result] == ['Q1 FY2022','Q1 FY2024','Q1 FY2025']
