"""Slide tables bind quarterly columns, lease basis and currency independently."""
import asyncio
import hashlib
import json
from types import SimpleNamespace
from dataclasses import replace

import pytest

from backend.app.research.capex_facts import CASH_TAG
from backend.app.research.discovery import FetchedSource
from backend.app.research.earnings_primary import primary_release_points, recover_primary_trends
from backend.app.research.earnings_sources import material_rejection, presentation_redirect
from backend.app.research.earnings_trends import METRICS, _build_series
from backend.app.research.quarterly_capex import quarterly_capex_points

COMPANY = {"ticker": "ABC", "name": "ABC Corporation", "cik": "1234", "website": "https://abc.com", "fiscal_year_end": "1231"}
EVENT = {"fiscal_period": "Q3 FY2025", "period_end": "2025-09-30", "earnings_date": "2025-10-29"}
TEXT = """ResearchCouncil archived PDF
[PDF page 1]
ABC Earnings Presentation
Q3                         2025
[PDF page 9]
Capital Expenditures
In Millions
$50,078 $19,374
YTD Quarterly
Capital expenditures for periods presented were related to purchases of property and equipment and principal payments on finance leases.
[PDF page 15]
Free Cash Flow Reconciliation
In Millions
Q1’25                   Q2’25                     Q3’25
Net cash provided by $ 24,026 $ 25,561 $ 29,999
operating activities
Less: Purchases of 12,941 16,538 18,829
property and equipment
Less: Principal payments 751 474 545
on finance leases
Free Cash Flow $ 10,334 $ 8,549 $ 10,625
Free cash flow (FCF) is a non-GAAP financial measure.
"""


def document(text=TEXT, **changes):
    return {"source_id": "slides", "url": "https://investor.abc.com/slides.pdf", "content": text,
        "content_hash": hashlib.sha256(text.encode()).hexdigest(), "fiscal_period": EVENT["fiscal_period"],
        "period_end": EVENT["period_end"], "published_at": EVENT["earnings_date"], **changes}


ROW = {"start": "2025-01-01", "end": "2025-03-31", "val": 12941000000, "form": "10-Q", "filed": "2025-05-01", "accn": "0000001234-25-000002"}


def source(row=None, unit="USD", **changes):
    content = json.dumps({"cik": 1234, "facts": {"us-gaap": {CASH_TAG: {"units": {unit: [ROW | (row or {})]}}}}})
    return {"id": "sec", "url": "https://data.sec.gov/api/xbrl/companyfacts/CIK0000001234.json", "content": content,
        "content_hash": hashlib.sha256(content.encode()).hexdigest(), **changes}


def test_quarterly_operands_currency_and_page_proof_reproduce_exact_reported_total():
    points = quarterly_capex_points(document(), COMPANY, {"sec": source()})
    cash = [p for p in points if p["metric"] == "capex_cash_ppe_quarterly"]
    totals = [p for p in points if p["metric"] == "capex_quarterly"]
    assert [p["value"] for p in cash] == [12.941, 16.538, 18.829]
    assert [p["value"] for p in totals] == [13.692, 17.012, 19.374]
    assert [p["period"] for p in totals] == ["Q1 FY2025", "Q2 FY2025", "Q3 FY2025"]
    latest = totals[-1]
    assert latest["period_end"] == "2025-09-30" and latest["source_locator"] == {"page": 15, "column": 3, "period": "Q3 FY2025"}
    assert latest["quote"] in TEXT and latest["currency_binding"]["quote"] in source()["content"]
    assert latest["definition_source"]["quote"] in TEXT
    assert [p["value"] for p in latest["calculation"]["inputs"]] == ["18829", "545"]
    assert all(p["quote"] in TEXT for p in latest["calculation"]["inputs"])
    assert all(p["value"] != 50.078 for p in points)  # the YTD chart is never read as Q3


@pytest.mark.parametrize("old,new", [
    ("Q1’25                   Q2’25                     Q3’25", "2023 2024 2025"),
    ("Q1’25                   Q2’25                     Q3’25", "Q1’25 Q1’25 Q3’25"),
    ("Q1’25                   Q2’25                     Q3’25", "Q1’25 Q3’25 Q2’25"),
    ("Q1’25                   Q2’25                     Q3’25", "Q1’25 Q2’25 YTD2025"),
    ("Q3’25", "Q4’25"), ("In Millions", "In Billions"),
    ("18,829", "18,829 42"), ("18,829", "(18,829)"),
    ("12,941", "CAD 12,941"), ("Free Cash Flow Reconciliation", "Capital expenditures forecast"),
])
def test_ambiguous_ytd_forecast_misordered_misaligned_or_foreign_columns_fail_closed(old, new):
    assert quarterly_capex_points(document(TEXT.replace(old, new)), COMPANY, {"sec": source()}) == []


def test_chart_alone_does_not_invent_column_associations():
    assert not quarterly_capex_points(document(TEXT.split("[PDF page 15]")[0]), COMPANY, {"sec": source()})


def test_bridge_requires_issuer_definition_not_merely_presence_of_cash_and_lease_rows():
    changed = TEXT.replace("and principal payments on finance leases.", "excluding principal payments on finance leases.")
    points = quarterly_capex_points(document(changed), COMPANY, {"sec": source()})
    assert len(points) == 3 and {p["metric"] for p in points} == {"capex_cash_ppe_quarterly"}


@pytest.mark.parametrize("changed", [source(unit="CAD"), source(row={"val": 12940000000}), source(row={"filed": "2025-10-30"}),
    source(row={"end": "2025-06-30"}), source(row={"accn": "invalid"}), source(url="https://example.com/facts"), source(content_hash="bad")])
def test_dollar_sign_is_not_usd_without_exact_independent_cash_fact(changed):
    assert quarterly_capex_points(document(), COMPANY, {"sec": changed}) == []


@pytest.mark.parametrize("changed", [document(content_hash="bad"), document(fiscal_period="Q3 FY2024"),
    document(url="https://notabc.example/slides.pdf"), document(TEXT.replace("ABC", "XYZ"))])
def test_source_company_hash_host_and_reporting_period_are_bound(changed):
    assert quarterly_capex_points(changed, COMPANY, {"sec": source()}) == []


def test_collector_verified_cdn_deck_requires_unchanged_archived_issuer_link_receipt():
    text = "ABC Corporation quarterly earnings and presentations"
    issuer = {"id": "issuer", "url": "https://investor.abc.com/results", "content": text, "content_hash": hashlib.sha256(text.encode()).hexdigest()}
    cdn = document(url="https://cdn.example/earnings.pdf", provenance="issuer_linked_asset", issuer_link_source_id="issuer",
        issuer_link_url=issuer["url"], issuer_link_content_hash=issuer["content_hash"], issuer_link_target_url="https://cdn.example/earnings.pdf")
    assert len(primary_release_points(cdn, COMPANY, cash_sources={"sec": source()}, issuer_sources={"issuer": issuer})) == 6
    assert not primary_release_points(cdn, COMPANY, cash_sources={"sec": source()})
    for changed in (cdn | {"issuer_link_content_hash": "bad"}, cdn | {"issuer_link_target_url": "https://cdn.example/other.pdf"},
                    cdn | {"issuer_link_url": "https://other.example/results"}, cdn | {"provenance": "model_suggestion"}):
        assert not quarterly_capex_points(changed, COMPANY, {"sec": source()}, issuer_sources={"issuer": issuer})
    assert not quarterly_capex_points(cdn, COMPANY, {"sec": source()}, issuer_sources={"issuer": issuer | {"content": "changed"}})


def test_live_issuer_pdf_redirect_retains_fetch_and_hash_receipt_without_trusting_bare_cdn_url():
    raw = b"%PDF-1.7 fixture"
    page = FetchedSource(document()["url"], "https://cdn.example/slides.pdf", TEXT, "cdn.example", "2026-09-26",
        original_bytes=raw, document_metadata={"mime_type": "application/pdf", "extraction_version": "pdf-layout.v1", "original_hash": hashlib.sha256(raw).hexdigest()})
    receipt = presentation_redirect(page, COMPANY)
    assert receipt and receipt["requested_url"] == document()["url"]
    assert material_rejection(page, COMPANY, EVENT, allow_presentation_cover=True) is None
    assert material_rejection(page, COMPANY, EVENT) is not None
    cdn = document(url=page.final_url, provenance="issuer_redirected_asset", issuer_redirect=receipt)
    assert len(primary_release_points(cdn, COMPANY, cash_sources={"sec": source()})) == 6
    for changed in (replace(page, requested_url="https://unknown.example/slides.pdf"), replace(page, original_bytes=None),
                    replace(page, final_url="http://cdn.example/slides.pdf"), replace(page, document_metadata={}),
                    replace(page, content=TEXT.replace("Q3                         2025", "Company overview"))):
        assert presentation_redirect(changed, COMPANY) is None
    for changed in (receipt | {"requested_url": "https://unknown.example/slides.pdf"}, receipt | {"content_hash": "bad"},
                    receipt | {"final_url": "https://cdn.example/other.pdf"}):
        assert not quarterly_capex_points(cdn | {"issuer_redirect": changed}, COMPANY, {"sec": source()})


def test_annual_chart_and_guidance_remain_separate_from_quarterly_spending():
    points = {metric: [] for metric in METRICS}
    for point in quarterly_capex_points(document(), COMPANY, {"sec": source()}):
        points[point["metric"]].append(point)
    points["capex"] = [{"period": "FY2025", "kind": "guidance", "source_id": "guidance", "published_at": "2025-10-29", "value": 71, "low": 70, "high": 72}]
    series = _build_series(points, EVENT, [])
    quarterly = next(s for s in series if s["id"] == "capex_quarterly")
    annual = next(s for s in series if s["id"] == "capex")
    assert quarterly["frequency"] == "quarterly" and quarterly["points"][-1]["value"] == 19.374
    assert annual["frequency"] == "annual" and annual["points"][-1]["value"] == 71
    assert all(p.get("value") != 19.374 for p in annual["points"])


def test_cover_identifies_verified_event_only_when_caller_has_event_date_grounding():
    page = FetchedSource(document()["url"], document()["url"], TEXT, "slides", "2026-09-26")
    assert material_rejection(page, COMPANY, EVENT) is not None
    assert material_rejection(page, COMPANY, EVENT, allow_presentation_cover=True) is None
    for changed in (EVENT | {"fiscal_period": "Q4 FY2025"}, EVENT | {"fiscal_period": "Q3 FY2024"}):
        assert material_rejection(page, COMPANY, changed, allow_presentation_cover=True) is not None


def test_historical_recovery_uses_deck_after_event_is_independently_grounded():
    class Repo:
        def source_packet(self, namespace, ids):
            return [source() for sid in ids if sid == "sec"]
    class Acquisition:
        repo, namespace = Repo(), "real"
        async def _fetch(self, url):
            return FetchedSource(url, url, TEXT, "ABC Earnings Presentation Q3 2025", "2026-09-26")
        def _document(self, page, **meta):
            return document() | meta | {"status": "available"}
    points = {metric: [] for metric in METRICS}
    points["capex"] = [{"period": "FY2025", "kind": "guidance", "value": 71}]
    points["capex_cash_ppe"] = [{"period": "FY2024", "kind": "actual", "value": 1, "source_id": "sec"}]
    candidate = {"url": document()["url"], "fiscal_period": EVENT["fiscal_period"], "period_end": EVENT["period_end"], "published_at": EVENT["earnings_date"]}
    no_binding = asyncio.run(recover_primary_trends(Acquisition(), COMPANY, EVENT, [], points, candidates=[candidate]))
    assert no_binding["checks"][0]["status"] == "rejected"
    company = COMPANY | {"filings": [{"form": "8-K", "items": "2.02,9.01", "period_end": "2025-10-29"}]}
    result = asyncio.run(recover_primary_trends(Acquisition(), company, EVENT, [], points, candidates=[candidate]))
    assert result["checks"][0]["status"] == "available"
    assert any(p["period"] == "Q3 FY2025" and p["value"] == 19.374 for p in result["points"])
    assert "sec" in {doc["source_id"] for doc in result["documents"]}
    assert not any("Q3 FY2025 capex_quarterly" in gap for gap in result["gaps"])


def test_historical_recovery_accepts_actual_issuer_to_cdn_redirect():
    raw = b"%PDF-1.7 fixture"
    class Repo:
        def source_packet(self, namespace, ids):
            return [source() for sid in ids if sid == "sec"]
    class Acquisition:
        repo, namespace = Repo(), "real"
        async def _fetch(self, url):
            return FetchedSource(url, "https://cdn.example/slides.pdf", TEXT, "cdn.example", "2026-09-26", original_bytes=raw,
                document_metadata={"mime_type": "application/pdf", "extraction_version": "pdf-layout.v1", "original_hash": hashlib.sha256(raw).hexdigest()})
        def _document(self, page, **meta):
            return document(url=page.final_url) | meta | {"status": "available"}
    points = {metric: [] for metric in METRICS}
    points["capex"] = [{"period": "FY2025", "kind": "guidance", "value": 71}]
    points["capex_cash_ppe"] = [{"period": "FY2024", "kind": "actual", "value": 1, "source_id": "sec"}]
    candidate = {"url": document()["url"], "fiscal_period": EVENT["fiscal_period"], "period_end": EVENT["period_end"], "published_at": EVENT["earnings_date"]}
    company = COMPANY | {"filings": [{"form": "8-K", "items": "2.02", "period_end": "2025-10-29"}]}
    result = asyncio.run(recover_primary_trends(Acquisition(), company, EVENT, [], points, candidates=[candidate]))
    assert result["checks"][0]["status"] == "available"
    assert any(p["period"] == "Q3 FY2025" and p["value"] == 19.374 for p in result["points"])
    assert result["documents"][0]["issuer_redirect"]["requested_url"] == candidate["url"]
