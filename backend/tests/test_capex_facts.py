"""Annual cash facts and an issuer-defined bridge have separate proof duties."""
import asyncio
import copy
import hashlib
import json
from types import SimpleNamespace

import pytest

from backend.app.research.capex_facts import CASH_TAG, LEASE_TAG, LEASE_BASIS, project_capex
from backend.app.research.earnings_trends import EarningsTrends, _guidance_review

COMPANY = {"ticker": "ABC", "name": "ABC Corporation", "cik": "0000001234", "fiscal_year_end": "1231"}
EVENT = {"fiscal_period": "Q2 FY2026", "period_end": "2026-06-30", "earnings_date": "2026-07-29"}
AS_OF = "2026-09-26T10:00:00Z"
ROW = {"start": "2025-01-01", "end": "2025-12-31", "val": 69691000000, "accn": "0000001234-26-000001", "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2026-01-29"}


def source(cash=None, leases=None, **changes):
    content = json.dumps({"cik": 1234, "facts": {"us-gaap": {
        CASH_TAG: {"units": {"USD": cash if cash is not None else [ROW]}},
        LEASE_TAG: {"units": {"USD": leases if leases is not None else [ROW | {"val": 2524000000}]}},
    }}})
    return {"id": "sec", "content": content, "content_hash": hashlib.sha256(content.encode()).hexdigest(),
        "url": "https://data.sec.gov/api/xbrl/companyfacts/CIK0000001234.json", "retrieved_at": AS_OF, **changes}


def definition(**changes):
    text = "ABC Q4 FY2025. Capital expenditures, including principal payments on finance leases, were $22.14 billion."
    return {"source_id": "call", "content": text, "content_hash": hashlib.sha256(text.encode()).hexdigest(),
        "fiscal_period": "Q4 FY2025", "published_at": "2026-01-28", "url": "https://abc.com/call", **changes}


def projection(sources=None, documents=None, **changes):
    return project_capex(sources or [source()], COMPANY, EVENT, [definition()] if documents is None else documents, research_as_of=changes.get("as_of", AS_OF))


def test_annual_bridge_preserves_cash_and_both_exact_operands_plus_definition():
    result = projection()
    cash, bridge = result["points"]
    assert cash["metric"] == "capex_cash_ppe" and cash["value"] == 69.691
    assert bridge["metric"] == "capex" and bridge["value"] == 72.215
    assert bridge["measure_basis"] == LEASE_BASIS
    assert [x["value"] for x in bridge["calculation"]["inputs"]] == ["69691000000", "2524000000"]
    for operand in bridge["calculation"]["inputs"]:
        assert operand["quote"] in source()["content"] and operand["accession"] == ROW["accn"]
    assert bridge["definition_source"]["quote"] in definition()["content"]
    assert bridge["quote"] == cash["quote"]  # explicitly calculated, never fake prose


@pytest.mark.parametrize("change", [
    {"accn": "0000001234-26-000002"}, {"filed": "2026-01-30"},
    {"start": "2025-02-01"}, {"end": "2025-12-30"}, {"form": "10-K/A"},
])
def test_bridge_cannot_mix_filing_vintages_periods_or_amendments(change):
    result = projection([source(leases=[ROW | {"val": 2524000000} | change])])
    assert [point["metric"] for point in result["points"]] == ["capex_cash_ppe"]


@pytest.mark.parametrize("changed", [
    definition(fiscal_period="Q4 FY2024"), definition(published_at="2026-10-01"),
    definition(content="Capital expenditures, including principal payments on finance leases, were $2 billion."),
    definition(content_hash="wrong"),
])
def test_bridge_needs_same_year_available_unchanged_issuer_definition(changed):
    assert [point["metric"] for point in projection(documents=[changed])["points"]] == ["capex_cash_ppe"]


def test_guidance_or_a_different_lease_definition_cannot_authorize_bridge():
    for text in ["We expect capital expenditures, including principal payments on finance leases, to be $2 billion.",
                 "Capital expenditures, excluding principal payments on finance leases, were $2 billion."]:
        doc = definition(content=text, content_hash=hashlib.sha256(text.encode()).hexdigest())
        assert [point["metric"] for point in projection(documents=[doc])["points"]] == ["capex_cash_ppe"]


@pytest.mark.parametrize("change", [{"filed": "2026-10-01"}, {"start": "2025-07-01"}, {"form": "10-Q"}, {"fp": "Q4"}, {"accn": "invalid"}])
def test_rejects_future_ytd_nonannual_and_unbound_accession(change):
    assert projection([source(cash=[ROW | change])])["points"] == []


def test_observation_date_and_source_identity_hash_are_required():
    for changed in [source(retrieved_at="2026-01-01"), source(content_hash="bad"), source(url="https://example.com/facts")]:
        assert projection([changed])["points"] == []
    assert projection(as_of="2026-01-01")["points"] == []


def test_same_filing_conflicting_cash_or_lease_values_are_not_arbitrarily_selected():
    assert projection([source(cash=[ROW, ROW | {"val": 1}])])["points"] == []
    assert [p["metric"] for p in projection([source(leases=[ROW | {"val": 1}, ROW | {"val": 2}])])["points"]] == ["capex_cash_ppe"]
    assert projection([source(), source(cash=[ROW | {"val": 1}], id="other")])["points"] == []
    later = ROW | {"accn": "0000001234-26-000002", "filed": "2026-02-01", "val": 68000000000}
    assert projection([source(cash=[ROW, ROW | {"val": 1}, later])])["points"] == []


@pytest.mark.parametrize("reported,conflict", [("50.00", True), ("72.22", False), ("72.2", False)])
def test_explicit_same_measure_annual_actual_must_reconcile_at_reported_precision(reported, conflict):
    text = f"For the full year 2025, capital expenditures, including principal payments on finance leases, were ${reported} billion."
    doc = definition(content=text, content_hash=hashlib.sha256(text.encode()).hexdigest())
    result = projection(documents=[doc])
    assert any(point['metric'] == 'capex' for point in result['points']) is not conflict
    assert bool(result['gaps']) is conflict


def test_first_filing_is_retained_and_its_missing_lease_is_not_borrowed_from_later_filing():
    revised = ROW | {"accn": "0000001234-26-000002", "filed": "2026-02-01", "val": 68000000000}
    result = projection([source(cash=[revised, ROW], leases=[revised | {"val": 2000000000}])])
    assert [(p["metric"], p["value"]) for p in result["points"]] == [("capex_cash_ppe", 69.691)]


def test_guidance_comparison_requires_same_explicit_definition_and_keeps_bridge_proof():
    actual = projection()["points"][1]
    guidance = actual | {"kind": "guidance", "published_at": "2025-01-01", "low": 60, "high": 65,
        "quote": "We expect capital expenditures of $60-65 billion."}
    assert _guidance_review([actual, guidance], [])["comparisons"] == []
    guidance["quote"] = "We expect capital expenditures, including principal payments on finance leases, of $60-65 billion."
    result = _guidance_review([actual, guidance], [])["comparisons"][0]
    assert result["actual"] == 72.215
    assert result["actual_source"]["calculation"] == actual["calculation"]


def test_provider_context_keeps_calculated_status_formula_and_operand_identities():
    from backend.app.research.assessment_evidence import _compact_trends
    actual = projection()["points"][1]
    packet = _compact_trends({"series": [{"id": "capex", "points": [actual]}]})
    calculated = packet["series"][0]["calculated_observations"][0]
    assert calculated["status"] == "calculated_actual"
    assert [row["tag"] for row in calculated["inputs"]] == [CASH_TAG, LEASE_TAG]
    assert calculated["definition_source"]["source_id"] == "call"


def test_archived_refresh_repairs_post_call_filing_without_network_model_or_mutating_parent():
    # No database/network/model methods exist: refresh is a pure archive read.
    raw = source(cash=[ROW | {"start": "2025-06-01", "end": "2026-05-31", "val": 684000000, "filed": "2026-07-15"}], leases=[])
    raw["source_id"] = raw["id"]
    class Repo:
        def source_packet(self, ns, ids):
            assert ns == "real"
            return [raw for sid in ids if sid == raw["id"]]
    acquisition = SimpleNamespace(repo=Repo(), namespace="real")
    prior = {"as_of": AS_OF, "series": [{"id": "capex_cash_ppe", "points": []}],
        "sources": [raw], "gaps": ["Cash purchases of property, plant & equipment: verified 0 of 5 requested completed periods; missing bars remain gaps."]}
    before = copy.deepcopy(prior)
    event = {"fiscal_period": "Q4 FY2026", "period_end": "2026-05-31", "earnings_date": "2026-06-30"}
    result = asyncio.run(EarningsTrends(acquisition).refresh_primary(COMPANY | {"fiscal_year_end": "0531"}, event, prior, {}, candidates=[]))
    actual = next(p for s in result["series"] for p in s["points"] if p["value"] is not None)
    assert actual["period"] == "FY2026" and actual["value"] == .684
    assert result["as_of"] == AS_OF and prior == before
