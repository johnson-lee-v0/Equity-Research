import hashlib
import json

import pytest

from backend.app.research.comparable_financials import observations, latest_seeds, bind_claim, VERSION
from backend.app.research.comparable_history import build_comparable_histories
from backend.app.research.fact_validation import validate_fact_claim
from backend.app.research.valuation_history import multiple_statistics
from backend.app.research.assessment_provider_projection import project_assessment_provider_facts


def source():
    def observation(value, end="2025-12-31", filed="2026-02-20", start=None):
        return {"val": value, "end": end, "filed": filed, "form": "10-K", "accn": "0000001234-26-000001", **({"start": start} if start else {})}
    data = {"cik": 1234, "entityName": "ABC Corporation", "facts": {"us-gaap": {
        "Revenues": {"units": {"USD": [observation(1000, start="2025-01-01"), observation(9999, start="2025-01-01", filed="2026-04-20")]}},
        "CommonStockSharesOutstanding": {"units": {"shares": [observation(10)]}},
        "StockholdersEquity": {"units": {"USD": [observation(500)]}},
        "Assets": {"units": {"USD": [observation(9000)]}},
        "OperatingIncomeLoss": {"units": {"USD": [observation(100, start="2025-01-01")]}},
    }}}
    content = json.dumps(data)
    return {"id": "sec", "content": content, "content_hash": hashlib.sha256(content.encode()).hexdigest(), "version": 1, "source_type": "filing", "url": "https://data.sec.gov/api/xbrl/companyfacts/CIK0000001234.json"}


def bound():
    archive = source()
    release = "ABC Corporation (Nasdaq: ABC)\nABC Corporation\nCONSOLIDATED STATEMENTS OF INCOME\nAnnual results"
    contents = {"sec": archive["content"], "release": release}
    versions = {key: {"hash": hashlib.sha256(value.encode()).hexdigest(), "version": 1} for key, value in contents.items()}
    rows = latest_seeds([archive], ticker="ABC", cik="1234", as_of="2026-03-01")
    row = next(row for row in rows if row["metric"] == "revenue")
    claim = {key: row.get(key) for key in ("metric", "subject", "value", "unit", "currency", "basis", "period", "period_start", "period_end", "statement_type", "locator", "source_quote", "source_ref")}
    claim.update(claim="ABC reported revenue", source_version="1")
    return archive, contents, versions, claim


def test_exact_company_facts_are_bound_to_a_separate_frozen_issuer_identity():
    archive, contents, versions, claim = bound()
    result = validate_fact_claim(claim, contents, source_metadata={"sec": archive}, source_versions=versions, as_of="2026-03-01")
    assert result["validation_status"] == "validated", result
    assert result["extraction"]["proof"]["parser"] == VERSION
    for changed in (claim | {"subject": "OTHER"}, claim | {"metric": "assets"}, claim | {"currency": "CAD"}, claim | {"value": "9999"}):
        assert bind_claim(changed, contents, {"sec": archive}, versions, as_of="2026-03-01") is None
    assert bind_claim(claim, contents | {"release": "changed"}, {"sec": archive}, versions, as_of="2026-03-01") is None


def test_assets_are_not_nav_and_operating_income_is_not_ebitda():
    rows = observations(source()["content"], source(), ticker="ABC", cik="1234", as_of="2026-03-01")
    assert {row["metric"] for row in rows} == {"revenue", "shares", "book equity", "operating income"}
    assert not observations(source()["content"], source(), ticker="ABC", cik="9999", as_of="2026-03-01")


def test_historical_ps_and_book_never_leak_future_restatement_or_same_day_filing():
    prices = [{"date": day, "close": "100", "currency": "USD", "source_refs": ["market"]} for day in ("2026-02-20", "2026-03-31")]
    result = build_comparable_histories([source()], prices, ticker="ABC", cik="1234", as_of="2026-05-01")
    assert result["P/S"]["gaps"][0]["date"] == "2026-02-20"
    assert result["P/S"]["points"][0]["multiple"] == "1.000000"
    assert result["P/book"]["points"][0]["multiple"] == "2.000000"
    assert result["EV/EBITDA"]["status"] == "unavailable"
    assert "appraised NAV" in result["P/book"]["basis"]
    result = build_comparable_histories([source()], prices, ticker="ABC", cik="1234", as_of="2026-05-01", split_events=[{"date": "2026-03-15"}])
    assert not result["P/S"]["points"]


def test_population_bands_use_available_samples_without_zero_filling():
    result = multiple_statistics([10, None, "", 12, "-1"])
    assert result["sample_count"] == 2
    assert result["mean"] == "11.000000"
    assert result["standard_deviation"] == "1.000000"
    assert result["bands"][0] == {"sigma": -3, "value": "8.000000"}
    assert multiple_statistics([10])["standard_deviation"] is None


def test_comparable_source_does_not_copy_a_megabyte_json_line_into_each_prompt_fact():
    archive, contents, versions, claim = bound()
    fact = claim | {"fact_id": "fact_sales", "validation_status": "validated", "recorded_validation_status": "validated", "current_validation_status": "validated", "semantic_status": "supported", "excerpt": archive["content"]}
    context = {"agent_id": "A11", "assessment_preparation": {"version": "earnings-assessment-evidence.v2"}, "financial_seeds": [fact]}
    sources = [archive | {"namespace": "real"}, {"id": "release", "namespace": "real", "content": contents["release"], "content_hash": versions["release"]["hash"], "version": 1}]
    projected, receipt = project_assessment_provider_facts(context, sources, source_versions=versions, as_of="2026-03-01")
    assert receipt["replaced_excerpt_count"] == 1
    assert projected["financial_seeds"][0]["excerpt"] == claim["source_quote"]


@pytest.mark.parametrize("indent", [None, 2])
def test_large_archive_locators_preserve_exact_source_without_repeated_prefix_scans(indent):
    class CountedSource(str):
        scanned_characters = 0

        def count(self, sub, start=0, end=None):
            self.scanned_characters += max(0, (len(self) if end is None else min(end, len(self))) - start)
            return super().count(sub, start, len(self) if end is None else end)

    archive = source()
    data = json.loads(archive["content"])
    template = data["facts"]["us-gaap"]["Revenues"]["units"]["USD"][0]
    data["facts"]["us-gaap"] = {
        "Revenues": {"units": {"USD": [template | {"val": index + 1} for index in range(160)]}},
    }
    # Leading whitespace, CRLF and multiline records exercise exact source
    # coordinates as well as the SEC's usual compact, single-line layout.
    plain = "\r\n  " + json.dumps(data, indent=indent, ensure_ascii=False).replace("\n", "\r\n")
    content = CountedSource(plain)
    rows = observations(content, archive, ticker="ABC", cik="1234", as_of="2026-03-01")
    assert len(rows) == 160
    assert [row["value"] for row in rows] == [f"{index + 1}.000000" for index in range(160)]
    for row in rows:
        quote = row["source_quote"]
        offset = plain.index(quote)
        first = plain.count("\n", 0, offset) + 1
        last = plain.count("\n", 0, offset + len(quote) - 1) + 1
        assert row["locator"] == (f"L{first}" if first == last else f"L{first}-L{last}")
        assert plain[offset:offset + len(quote)] == quote
    # Bound work deterministically; elapsed-time assertions vary by machine.
    assert content.scanned_characters <= 2 * len(content)
