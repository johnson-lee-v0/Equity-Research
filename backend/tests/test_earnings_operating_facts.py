"""Operating facts must bind the right metric inside multi-number remarks."""
from __future__ import annotations

import copy
import hashlib
import json

import pytest

from backend.app.research.assessment_evidence import compile_earnings_assessment
from backend.app.research.earnings_operating_facts import OPERATING_BASES, operating_trend_seeds
from backend.app.research.fact_validation import validate_fact_claim
from backend.app.schemas import FactClaim
from backend.tests.test_assessment_evidence import RELEASE as EPS_RELEASE, _attach_release
from backend.tests.test_earnings_research_context import context_case


RENEWAL = "In terms of renewal rates at Q4 end, our U.S. and Canada renewal rate was 92.3%, up 10 basis points from last quarter, and the worldwide rate came in at 89.8%, also up 10 basis points."
MEMBERS = "We ended the quarter with 84.1 million total paid members, up 3.8% versus last year, and 150.4 million cardholders up 3.6% year-over-year."
MARGIN = "Our reported gross margin rate was lower year-over-year by 11 basis points, coming in at 11.02% compared to 11.13% last year."
SALES = "Net sales for the quarter increased 11.2 percent, to $93.9 billion, from $84.4 billion last year."
TRANSCRIPT = "\n".join([
    "Costco Wholesale Corporation (COST)", "NASDAQ: COST · Real-Time Price · USD",
    "At close: Sep 25, 2026", "Earnings Call: Q4 2026", "Sep 24, 2026",
    "Gary Millerchip", "CFO, Costco Wholesale",
    "In today's press release, we reported operating results for the fourth quarter of fiscal year 2026, the 16 weeks ended August 30th.",
    MEMBERS + RENEWAL, MARGIN,
])
RELEASE = "\n".join([
    "Costco Wholesale Corporation Reports Fourth Quarter and Fiscal Year 2026 Operating Results",
    "Skip to main content",  # Browser title repeats before the dated title.
    "Costco Wholesale Corporation Reports Fourth Quarter and Fiscal Year 2026 Operating Results",
    "09/24/2026",
    "Costco Wholesale Corporation (Nasdaq: COST) today announced its operating results for the 16-week fourth quarter and the 52-week fiscal year ended August 30, 2026.",
    SALES + " Net sales for the fiscal year increased 10.1 percent, to $297.2 billion, from $269.9 billion last year.",
])
EVENT = {"fiscal_period": "Q4 FY2026", "period_end": "2026-08-30", "earnings_date": "2026-09-24"}


def packet():
    sources = [{"id": "call", "content": TRANSCRIPT, "version": 1}, {"id": "release", "content": RELEASE, "version": 1}]
    source_by_id = {source["id"]: source for source in sources}
    series = []
    for metric, quote, value, sid in [("net_sales_growth", SALES, 11.2, "release"),
                                     ("paid_members_growth", MEMBERS, 3.8, "call"),
                                     ("renewal_us_canada", RENEWAL, 92.3, "call"),
                                     ("renewal_worldwide", RENEWAL, 89.8, "call"),
                                     ("gross_margin", MARGIN, 11.02, "call")]:
        content = source_by_id[sid]["content"]
        line = content.count("\n", 0, content.index(quote)) + 1
        series.append({"id": metric, "unit": "percent", "frequency": "quarterly", "points": [
            {"source_id": sid, "source_version": 1, "period": "Q4 FY2026", "period_end": "2026-08-30",
             "kind": "actual", "value": value, "quote": quote, "locator": f"L{line}"}]})
    return {"ticker": "COST", "series": series}, sources


def validate(claim, sources):
    content = {source["id"]: source["content"] for source in sources}
    versions = {source["id"]: {"version": source["version"], "hash": hashlib.sha256(source["content"].encode()).hexdigest()} for source in sources}
    return validate_fact_claim(claim, content, source_versions=versions, as_of="2026-09-25")


def test_five_current_observations_reconstruct_exact_semantics_and_period():
    context, sources = packet()
    facts = operating_trend_seeds(context, sources, ticker="COST", event=EVENT)
    assert [(fact["metric"], fact["value"]) for fact in facts] == [
        ("net_sales_growth", "11.2"), ("paid_members_growth", "3.8"),
        ("renewal_us_canada", "92.3"), ("renewal_worldwide", "89.8"), ("gross_margin", "11.02")]
    for fact in facts:
        parsed = FactClaim.model_validate(fact)
        result = validate(parsed, sources)
        assert result["validation_status"] == "validated" and result["semantic_status"] == "supported"
        assert result["extraction"]["period_end"] == "2026-08-30"
        assert result["corroboration_status"] == "not_assessed" and fact["currency"] is None
        assert "proof" not in fact  # The validator produces its own proof.


@pytest.mark.parametrize("metric,field,value", [
    ("renewal_us_canada", "value", "89.8"), ("renewal_worldwide", "value", "92.3"),
    ("paid_members_growth", "value", "3.6"), ("paid_members_growth", "value", "84.1"),
    ("gross_margin", "value", "11.13"), ("gross_margin", "value", "11"),
    ("net_sales_growth", "value", "10.1"), ("net_sales_growth", "value", "93.9"),
    ("gross_margin", "subject", "ABC"), ("gross_margin", "period", "Q3 FY2026"),
    ("gross_margin", "period_end", "2025-08-31"), ("gross_margin", "period_start", "2026-06-01"),
    ("gross_margin", "basis", "adjusted margin"), ("gross_margin", "unit", "basis points"),
    ("gross_margin", "currency", "USD"), ("gross_margin", "scale", "millions"),
    ("gross_margin", "source_quote", "11.02%"), ("gross_margin", "locator", "L1-L10"),
    ("gross_margin", "source_version", "2"), ("gross_margin", "statement_type", "management guidance"),
])
def test_claim_cannot_swap_metric_geography_period_basis_or_provenance(metric, field, value):
    context, sources = packet()
    claim = next(fact for fact in operating_trend_seeds(context, sources, ticker="COST", event=EVENT) if fact["metric"] == metric)
    claim[field] = value
    assert validate(claim, sources)["validation_status"] != "validated"


@pytest.mark.parametrize("before,after", [
    ("NASDAQ: COST", "NASDAQ: ABC"), ("NASDAQ: COST", "NASDAQ: COST NYSE: ABC"),
    ("Earnings Call: Q4 2026", "Earnings Call: Q3 2026"),
    ("Sep 24, 2026", "undated"), ("ended August 30th", "ended an unspecified date"),
    ("Sep 24, 2026", "Feb 24, 2026"),
    ("Our reported gross margin", "For the fiscal year, our reported gross margin"),
    (MARGIN, "Analyst, Example Bank\n" + MARGIN),
    (MARGIN, MARGIN + "\n" + MARGIN),
])
def test_mutated_raw_source_cannot_be_rescued_by_claim_or_metadata(before, after):
    context, sources = packet()
    fact = operating_trend_seeds(context, sources, ticker="COST", event=EVENT)[-1]
    sources[0]["content"] = sources[0]["content"].replace(before, after)
    assert validate(fact, sources)["validation_status"] != "validated"


@pytest.mark.parametrize("change", ["value", "period", "date", "guidance", "version", "locator", "duplicate", "unit"])
def test_register_is_only_candidate_selection_and_never_self_certifies(change):
    context, sources = packet()
    series = context["series"][0]
    point = series["points"][0]
    if change == "value": point["value"] = 10.1
    elif change == "period": point["period"] = "Q3 FY2026"
    elif change == "date": point["period_end"] = "2026-08-31"
    elif change == "guidance": point["kind"] = "guidance"
    elif change == "version": point["source_version"] = 2
    elif change == "locator": point["locator"] = "L1-L6"
    elif change == "duplicate": series["points"].append(copy.deepcopy(point))
    elif change == "unit": series["unit"] = "USD billions"
    facts = operating_trend_seeds(context, sources, ticker="COST", event=EVENT)
    assert "net_sales_growth" not in [fact["metric"] for fact in facts]


def test_missing_calendar_year_uses_call_date_not_fiscal_year():
    context, sources = packet()
    sources[0]["content"] = sources[0]["content"].replace("Q4 2026", "Q1 2027").replace("Sep 24, 2026", "Dec 10, 2026").replace("fourth quarter of fiscal year 2026", "first quarter of fiscal year 2027").replace("August 30th", "November 22nd").replace("Q4 end", "Q1 end")
    point = context["series"][1]["points"][0]
    point.update(period="Q1 FY2027", period_end="2026-11-22")
    facts = operating_trend_seeds(context, sources, ticker="COST", event={"fiscal_period": "Q1 FY2027", "period_end": "2026-11-22"})
    assert len(facts) == 1 and facts[0]["period_end"] == "2026-11-22"


@pytest.mark.parametrize("with_eps", [False, True])
def test_compiler_keeps_eps_first_and_operating_only_does_not_unlock_valuation(context_case, with_eps):
    repo, run_id, workflow_id, *_ = context_case
    raw = RELEASE + ("\n" + EPS_RELEASE.replace("ABC", "COST") if with_eps else "")
    sid = _attach_release(repo, run_id, workflow_id, raw)
    context, _ = packet()
    series = context["series"][0]
    series["points"][0]["source_id"] = sid
    with repo.db.transaction(immediate=True) as conn:
        package = json.loads(conn.execute("SELECT result_json FROM research_workflow_runs WHERE id=?", (workflow_id,)).fetchone()[0])
        package["event"] = EVENT
        package["trends"]["series"] = [series]
        conn.execute("UPDATE research_workflow_runs SET result_json=? WHERE id=?", (json.dumps(package), workflow_id))
    snapshot = json.loads(repo.run_record(run_id)["input_snapshot_json"])
    result = compile_earnings_assessment(repo, run_id, repo.source_packet("real", snapshot["source_ids"]))
    facts = result["fact_claims"]
    assert [fact["metric"] for fact in facts] == (["eps", "eps"] if with_eps else []) + ["net_sales_growth"]
    assert [fact["claim_id"] for fact in facts] == [f"c{i + 1}" for i in range(len(facts))]
    assert result["status"] == ("ready" if with_eps else "needs_valuation_evidence")
    assert result["valuation_readiness"]["baseline_claim_id"] == ("c1" if with_eps else None)
