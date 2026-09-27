"""Calendar-month EPS tables retain dates, signs, row identity and currency proof."""
import hashlib
import json

import pytest

from backend.app.research.earnings_financials import (
    MONTHLY_LAYOUT_VERSION, VERSION, bound_release_eps_observations,
    bind_release_eps_claim, release_eps_observations,
)
from backend.app.research.fact_validation import validate_fact_claim


def release(*, currency="U.S. dollars", gaap=True, padding=145):
    return "\n".join([
        "Example Corporation (NYSE: EXM) reports results.",
        "The consolidated financial statements are prepared in accordance with U.S. GAAP." if gaap else "Reported results follow.",
        "Example Corporation", "CONSOLIDATED STATEMENTS OF INCOME", "(Unaudited)",
        "THREE MONTHS ENDED", "%", "TWELVE MONTHS ENDED", "%",
        f"({currency} in millions, except per share data)",
        "5/31/2026", "5/31/2025", "Change", "5/31/2026", "5/31/2025", "Change",
        *("Operating statement item" for _ in range(padding)),
        "Earnings per common share:", "Basic", "$", "0.73", "$", "0.15", "387", "%", "$", "2.11", "$", "2.17", "-3", "%",
        "Diluted", "$", "0.72", "$", "0.14", "414", "%", "$", "2.10", "$", "2.16", "-3", "%",
        "Weighted average common shares outstanding:", "Basic", "1482.6", "1476.7", "1479.8", "1484.9",
        "Diluted", "1482.9", "1477.7", "1481.0", "1487.6",
    ])


def claim(row):
    fields = ("value", "unit", "currency", "basis", "period", "period_start", "period_end", "statement_type", "locator", "source_quote", "subject", "metric")
    return {key: row[key] for key in fields} | {"source_ref": "release", "source_version": "1", "claim": "Reported diluted earnings per share."}


def test_vertical_monthly_eps_binds_dates_and_skips_basic_shares_and_percent_columns():
    text = release()
    parsed = release_eps_observations(text)
    assert not parsed["gaps"]
    rows = parsed["observations"]
    assert [row["value"] for row in rows] == ["0.72", "0.14", "2.10", "2.16"]
    assert [row["period_start"] for row in rows] == ["2026-03-01", "2025-03-01", "2025-06-01", "2024-06-01"]
    assert [row["proof"]["table_column"] for row in rows] == [1, 2, 4, 5]
    assert rows[0]["proof"]["metric_line"] - rows[0]["proof"]["table_line"] > 90
    for row in rows:
        assert row["source_quote"] in text and row["proof"]["layout_parser"] == MONTHLY_LAYOUT_VERSION
        assert row["proof"]["parser"] == VERSION
        assert bind_release_eps_claim(claim(row), text)
        assert text.splitlines()[row["proof"]["value_line"] - 1] == row["value"]


@pytest.mark.parametrize("end,prior,months,expected", [
    ("2/29/2024", "2/28/2023", "THREE", "2023-12-01"),
    ("2/28/2025", "2/29/2024", "SIX", "2024-09-01"),
    ("1/31/2026", "1/31/2025", "NINE", "2025-05-01"),
])
def test_month_based_starts_use_calendar_boundaries_and_leap_years(end, prior, months, expected):
    text = release().replace("5/31/2026", end).replace("5/31/2025", prior).replace("THREE MONTHS", months + " MONTHS")
    rows = release_eps_observations(text)["observations"]
    assert len(rows) == 4 and rows[0]["period_start"] == expected


@pytest.mark.parametrize("raw,value", [("(0.72)", "-0.72"), ("-.72", "-0.72"), ("−0.72", "-0.72"), (".72", "0.72")])
def test_losses_and_fractional_cells_keep_their_sign(raw, value):
    parsed = release_eps_observations(release().replace("\n0.72\n", "\n" + raw + "\n"))
    assert parsed["observations"][0]["value"] == value


@pytest.mark.parametrize("old,new", [
    ("5/31/2025\nChange\n5/31/2026", "5/31/2025\nUnexpected\n5/31/2026"),
    ("5/31/2026", "5/30/2026"),
    ("5/31/2025", "5/31/2024"),
    ("5/31/2026", "2/30/2026"),
    ("TWELVE MONTHS ENDED", "No duration header"),
    ("\n414\n%\n", "\n414\n"),
    ("\n0.72\n", "\n0.72\n%\n"),
    ("\n2.16\n", "\n2.16\n999\n"),
    ("\n0.72\n", "\n(0.72\n"),
    ("Weighted average common shares outstanding:", "Unrecognized next section"),
    ("\nDiluted\n$\n0.72", "\nBasic\n$\n0.72"),
])
def test_malformed_or_unaligned_monthly_tables_fail_closed_with_gaps(old, new):
    result = release_eps_observations(release().replace(old, new))
    assert not result["observations"] and result["gaps"]


def test_missing_currency_and_basis_remain_unresolved():
    parsed = release_eps_observations(release(currency="dollars", gaap=False))
    assert len(parsed["observations"]) == 4
    assert all(row["currency"] is None and row["basis"] is None for row in parsed["observations"])
    assert any("accounting basis" in gap for gap in parsed["gaps"])
    assert any("ambiguous dollar" in gap for gap in parsed["gaps"])


def test_monthly_currency_requires_an_exact_frozen_prior_sec_eps_interval():
    text = release(currency="dollars")
    concept = {"cik": 1234, "entityName": "Example Corporation", "taxonomy": "us-gaap", "tag": "EarningsPerShareDiluted",
        "units": {"USD/shares": [{"start": "2024-06-01", "end": "2025-05-31", "val": 2.16, "form": "10-K", "filed": "2025-07-20", "accn": "0000001234-25-000001"}]}}
    metadata = {"sec": {"url": "https://data.sec.gov/api/xbrl/companyconcept/CIK0000001234/us-gaap/EarningsPerShareDiluted.json"}}
    def parsed(value):
        sources = {"release": text, "sec": json.dumps(value)}
        versions = {sid: {"version": 1, "hash": hashlib.sha256(raw.encode()).hexdigest()} for sid, raw in sources.items()}
        return bound_release_eps_observations("release", sources, metadata, versions, as_of="2026-09-26"), sources, versions
    result, sources, versions = parsed(concept)
    assert all(row["currency"] == "USD" for row in result["observations"])
    row = result["observations"][2]
    assert row["proof"]["currency_binding"] == "same_table_exact_comparative_sec_eps"
    assert validate_fact_claim(claim(row), sources, source_metadata=metadata, source_versions=versions, as_of="2026-09-26")["validation_status"] == "validated"
    concept["units"]["USD/shares"][0]["start"] = "2024-06-02"
    changed, _, _ = parsed(concept)
    assert all(row["currency"] is None for row in changed["observations"])


def test_monthly_tables_with_extra_or_duplicate_supported_periods_are_not_merged():
    text = release()
    result = release_eps_observations(text + "\n" + text)
    assert not result["observations"]
    assert any("Multiple diluted-EPS tables" in gap for gap in result["gaps"])


def test_monthly_table_without_percent_change_columns_remains_aligned():
    text = release()
    text = text.replace("\n%\n", "\n").replace("\nChange\n", "\n")
    for value in ("387", "414", "-3"):
        text = text.replace("\n" + value + "\n", "\n")
    assert [row["value"] for row in release_eps_observations(text)["observations"]] == ["0.72", "0.14", "2.10", "2.16"]


@pytest.mark.parametrize("denomination,expected", [
    ("Canadian dollars", "CAD"), ("Australian dollars", "AUD"), ("Hong Kong dollars", "HKD"),
    ("USD and CAD", None), ("Zimbabwean dollars", None), ("XYZ", None), ("euros", None),
    ("C$", None), ("A$", None), ("HK$", None), ("NZ$", None), ("S$", None), ("X$", None),
])
@pytest.mark.parametrize("layout", ["monthly", "weekly"])
def test_explicit_currency_is_never_overridden_by_matching_usd_comparison(denomination, expected, layout):
    from backend.tests.test_assessment_evidence import RELEASE, sec_source
    if layout == "monthly":
        text = release(currency=denomination)
        concept = {"cik": 1234, "entityName": "Example Corporation", "taxonomy": "us-gaap", "tag": "EarningsPerShareDiluted",
            "units": {"USD/shares": [{"start": "2024-06-01", "end": "2025-05-31", "val": 2.16, "form": "10-K", "filed": "2025-07-20", "accn": "0000001234-25-000001"}]}}
    else:
        text = RELEASE.replace("U.S. dollars", denomination)
        concept = json.loads(sec_source()[0])
        concept["units"]["USD/shares"] = [{"start": "2024-01-01", "end": "2024-12-29", "val": 9, "form": "10-K", "filed": "2025-02-20", "accn": "0000001234-25-000001"}]
    sources = {"release": text, "sec": json.dumps(concept)}
    metadata = {"sec": {"url": "https://data.sec.gov/api/xbrl/companyconcept/CIK0000001234/us-gaap/EarningsPerShareDiluted.json"}}
    versions = {sid: {"version": 1, "hash": hashlib.sha256(raw.encode()).hexdigest()} for sid, raw in sources.items()}
    parsed = bound_release_eps_observations("release", sources, metadata, versions, as_of="2026-09-26")
    assert parsed["observations"] and all(row["currency"] == expected for row in parsed["observations"])
    assert all(not row["proof"].get("currency_sources") for row in parsed["observations"])
    assert all(not row["proof"]["currency_inference_allowed"] for row in parsed["observations"])
