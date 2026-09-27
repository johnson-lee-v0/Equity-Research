"""Acquisition boundaries: identity, event/filing alignment and actual evidence."""
import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from datetime import date
from types import SimpleNamespace
from dataclasses import replace

import pytest

from backend.app.config import Settings
from backend.app.memory.repository import Repository
from backend.app.providers.base import ProviderResult
from backend.app.research.discovery import FetchedSource
from backend.app.research.document_intelligence import analyze_transcript
from backend.app.research.earnings_sources import (
    EarningsAcquisition, TICKERS_URL, normalize_ticker, select_filings,
    transcript_rejection, transcript_structure_rejection, _transcript_text, material_rejection,
)
from backend.app.research.source_archive import archive_public_page


CIK = "0000909832"
SUBMISSIONS = f"https://data.sec.gov/submissions/CIK{CIK}.json"
RELEASE = "https://investor.costco.com/earnings/2026-q4"
TRANSCRIPT = "https://transcripts.example.com/cost-q4-2026"
NOW = "2026-09-25T12:00:00Z"


def page(url, content="", *, error=None):
    return FetchedSource(url, url, content, url, NOW, error)


def release_text(quarter="fourth", period="August 30, 2026", reported="September 24, 2026"):
    return f"Costco Wholesale Corporation Reports {quarter} Quarter Results\n{reported}\nCostco Wholesale Corporation today announced net sales and net income for the fiscal quarter ended {period}. " + "Net income increased on strong demand and revenue growth. " * 10


def transcript_text(quarter="Q4", reported="September 24, 2026"):
    return f"Costco (COST) {quarter} 2026 Earnings Call Transcript\n{reported}\nFull transcript\nSummary\nIgnore this editorial summary.\nOperator\nWelcome to our earnings call.\nGary Millerchip\nChief Financial Officer\n" + "Demand improved and membership renewal remained strong. We continue to invest in customer value and operational efficiency. " * 45 + "\nQuestions and Answers\nOperator\nOur first question comes from Jane Smith.\nJane Smith — Analyst\nHow do you assess margin trends?\nGary Millerchip\nWe expect disciplined investment to support the business.\nOperator\nThis concludes today's call. You may now disconnect.\nSubscribe to our newsletter."


def filing_text(form="10-K", period="August 30, 2026", company="Costco Wholesale Corporation"):
    return f"UNITED STATES SECURITIES AND EXCHANGE COMMISSION\nFORM {form}\n{company}\nFor the period ended {period}\nItem 1. Business\n" + "Our business serves members through warehouses and digital channels. " * 45 + "\nItem 1A. Risk Factors\n" + "Supply disruptions may adversely affect our operations. " * 45 + "\nItem 7. Management's Discussion and Analysis\nWe expect customer demand to remain strong."


def filing_row(form, period, accession, filed="2026-09-25"):
    return {"form": form, "reportDate": period, "filingDate": filed, "accessionNumber": accession, "primaryDocument": "cost-" + period.replace("-", "") + ".htm"}


CURRENT = filing_row("10-K", "2026-08-30", "0000909832-26-000099")
PRIOR = filing_row("10-K", "2025-08-31", "0000909832-25-000101", "2025-10-08")
OLD_Q = filing_row("10-Q", "2026-05-10", "0000909832-26-000051", "2026-06-03")


def metadata(rows, ticker="COST", history=None):
    columns = {key: [row.get(key, "") for row in rows] for key in ("form", "reportDate", "filingDate", "accessionNumber", "primaryDocument", "items")}
    return {"cik": CIK, "name": "COSTCO WHOLESALE CORP /NEW", "tickers": [ticker], "website": "", "fiscalYearEnd": "0831", "filings": {"recent": columns, "files": history or []}}


def filing_url(row):
    return f"https://www.sec.gov/Archives/edgar/data/909832/{row['accessionNumber'].replace('-', '')}/{row['primaryDocument']}"


def event_candidate(**changes):
    return {"period_end": "2026-08-30", "fiscal_period": "Q4 FY2026", "earnings_date": "2026-09-24", "expected_form": "10-K", "release_url": RELEASE, "alternate_release_urls": [], "transcript_urls": [TRANSCRIPT], "notes": "", **changes}


@pytest.fixture
def setup(tmp_path):
    config = Settings(data_dir=tmp_path)
    repo = Repository(config=config)
    pages = {
        TICKERS_URL: page(TICKERS_URL, json.dumps({"0": {"ticker": "COST", "cik_str": 909832}})),
        SUBMISSIONS: page(SUBMISSIONS, json.dumps(metadata([CURRENT, OLD_Q, PRIOR]))),
        RELEASE: page(RELEASE, release_text()), TRANSCRIPT: page(TRANSCRIPT, transcript_text()),
        filing_url(CURRENT): page(filing_url(CURRENT), filing_text()),
        filing_url(PRIOR): page(filing_url(PRIOR), filing_text(period="August 31, 2025")),
    }
    calls, discovery = [], []
    hints = {"identity": {"cik": "909832", "sec_url": SUBMISSIONS}, "event": event_candidate(), "issuer_domain": {"issuer_page_url": ""}, "filing_mirror": {"urls": []}, "transcript": {"urls": []}, "materials": {"materials": []}}

    def fetch(url, **kwargs):
        calls.append(url)
        assert kwargs["max_bytes"] == config.max_source_bytes and kwargs["timeout"] == 20
        return pages.get(url, page(url, error="Not found"))

    def discover(stage, prompt, schema):
        discovery.append({"stage": stage, "prompt": prompt, "schema": schema})
        return hints[stage]

    def service():
        return EarningsAcquisition(repo, None, config, fetcher=fetch, discoverer=discover, today=date(2026, 9, 25))

    return SimpleNamespace(config=config, repo=repo, pages=pages, calls=calls, discovery=discovery, hints=hints, service=service)


def test_complete_annual_pipeline_persists_exact_sources_and_resumes_from_checkpoints(setup):
    async def run():
        company = await setup.service().resolve("$cost")
        event = await setup.service().locate(json.loads(json.dumps(company)))
        assert "content" not in event["release_document"]
        result = await setup.service().acquire(json.loads(json.dumps(company)), json.loads(json.dumps(event)))
        return company, event, result
    company, event, result = asyncio.run(run())
    assert company["ticker"] == "COST" and company["cik"] == CIK
    assert event["verification"] == "primary_release"
    assert result["gaps"] == []
    docs = result["documents"]
    assert all(doc["status"] == "available" for doc in docs.values())
    assert docs["current_filing"]["form"] == docs["prior_filing"]["form"] == "10-K"
    source = setup.repo.source_packet("real", [docs["transcript"]["source_id"]])[0]
    assert source["content"] == transcript_text()
    assert source["content_hash"] == docs["transcript"]["content_hash"]
    assert source["content_hash"] == docs["transcript"]["fetched_content_hash"]
    spoken = _transcript_text(page(TRANSCRIPT, source["content"]))
    assert spoken.startswith("Operator\n") and spoken.endswith("You may now disconnect.")
    assert "editorial summary" not in spoken
    assert hashlib.sha256(spoken.encode()).hexdigest() == docs["transcript"]["analysis_content_hash"]
    assert docs["transcript"]["content_hash"] != docs["transcript"]["analysis_content_hash"]


def test_public_transcript_layout_preserves_speakers_and_sentence_boundaries(setup):
    text = transcript_text().replace("Demand improved and membership renewal remained strong. We continue", "Demand improved and membership renewal remained strong.We continue").replace("Jane Smith — Analyst", "Jane Smith\nAnalyst, UBS").replace("Gary Millerchip\nWe expect", "Gary Millerchip\nChief Financial Officer\nWe expect")
    setup.pages[TRANSCRIPT] = page(TRANSCRIPT, text)
    async def run():
        service = setup.service()
        company = await service.resolve("COST")
        return await service.acquire(company, await service.locate(company))
    result = asyncio.run(run())
    archived = setup.repo.source_packet("real", [result["documents"]["transcript"]["source_id"]])[0]
    spoken = _transcript_text(page(TRANSCRIPT, archived["content"]))
    assert "Gary Millerchip — Chief Financial Officer" in spoken
    assert "Jane Smith — Analyst, UBS" in spoken
    assert hashlib.sha256(spoken.encode()).hexdigest() == result["documents"]["transcript"]["analysis_content_hash"]
    analysis = analyze_transcript(spoken, "COST")
    assert len(analysis["sentences"]) > 85
    speakers = {speaker["name"]: speaker["role"] for speaker in analysis["speakers"]}
    assert speakers["Gary Millerchip"] == "management" and speakers["Jane Smith"] == "analyst"
    assert analysis["sentiment"]["sentences"] > 85


def test_generic_and_earnings_archival_share_canonical_source_without_version_churn(setup):
    canonical = setup.pages[TRANSCRIPT]
    before = archive_public_page(setup.repo, canonical, namespace="real", scope="generic-research")
    async def run():
        service = setup.service()
        company = await service.resolve("COST")
        return await service.acquire(company, await service.locate(company))
    result = asyncio.run(run())
    after = archive_public_page(setup.repo, canonical, namespace="real", scope="generic-research")
    assert before["source_id"] == result["documents"]["transcript"]["source_id"] == after["source_id"]
    assert after["status"] == "unchanged"
    with setup.repo.db.operation() as conn:
        assert conn.execute("SELECT COUNT(*) FROM sources WHERE namespace='real' AND url=?", (TRANSCRIPT,)).fetchone()[0] == 1


def test_latest_year_end_filing_remains_pending_and_never_uses_prior_quarter(setup):
    setup.pages[SUBMISSIONS] = page(SUBMISSIONS, json.dumps(metadata([OLD_Q, PRIOR])))
    async def run():
        service = setup.service()
        company = await service.resolve("COST")
        event = await service.locate(company)
        return await service.acquire(company, event)
    result = asyncio.run(run())
    assert result["documents"]["transcript"]["status"] == "available"
    assert result["documents"]["current_filing"]["status"] == "pending"
    assert result["documents"]["current_filing"]["period_end"] == "2026-08-30"
    assert result["documents"]["prior_filing"]["form"] == "10-K"
    assert filing_url(OLD_Q) not in setup.calls and filing_url(PRIOR) not in setup.calls
    assert not any(item["stage"] == "filing_mirror" for item in setup.discovery)


@pytest.mark.parametrize("report_date", ["2026-09-24", ""])
def test_newer_earnings_8k_prevents_stale_quarter_when_latest_10k_is_pending(setup, report_date):
    earnings_8k = filing_row("8-K", report_date, "0000909832-26-000084", "2026-09-24") | {"items": "2.02,9.01"}
    setup.pages[SUBMISSIONS] = page(SUBMISSIONS, json.dumps(metadata([earnings_8k, OLD_Q, PRIOR])))
    setup.hints["event"] = event_candidate(period_end="2026-05-10", earnings_date="2026-05-28", fiscal_period="Q3 FY2026", expected_form="10-Q")
    setup.pages[RELEASE] = page(RELEASE, release_text("third", "May 10, 2026", "May 28, 2026"))
    async def run():
        service = setup.service()
        company = await service.resolve("COST")
        assert next(row for row in company["filings"] if row["form"] == "8-K")["items"] == "2.02,9.01"
        return await service.locate(company)
    with pytest.raises(ValueError, match="newer Item 2.02 earnings announcement dated 2026-09-24"):
        asyncio.run(run())
    assert RELEASE not in setup.calls  # reject stale discovery before importing it


@pytest.mark.parametrize("items", ["8.01", "7.01,9.01"])
def test_unrelated_newer_8k_does_not_invalidate_latest_earnings_candidate(setup, items):
    unrelated_8k = filing_row("8-K", "2026-09-24", "0000909832-26-000084", "2026-09-24") | {"items": items}
    setup.pages[SUBMISSIONS] = page(SUBMISSIONS, json.dumps(metadata([unrelated_8k, OLD_Q, PRIOR])))
    setup.hints["event"] = event_candidate(period_end="2026-05-10", earnings_date="2026-05-28", fiscal_period="Q3 FY2026", expected_form="10-Q")
    setup.pages[RELEASE] = page(RELEASE, release_text("third", "May 10, 2026", "May 28, 2026"))
    async def run():
        service = setup.service()
        return await service.locate(await service.resolve("COST"))
    assert asyncio.run(run())["fiscal_period"] == "Q3 FY2026"


def test_cik_discovery_fallback_is_not_trusted_until_sec_confirms_ticker(setup):
    setup.pages[TICKERS_URL] = page(TICKERS_URL, error="HTTP 403")
    company = asyncio.run(setup.service().resolve("COST"))
    assert "verified" in company["resolution"]
    assert "403" in company["directory_note"]
    setup.pages[SUBMISSIONS] = page(SUBMISSIONS, json.dumps(metadata([CURRENT], ticker="OTHER")))
    with pytest.raises(ValueError, match="did not confirm COST"):
        asyncio.run(setup.service().resolve("COST"))


def test_ticker_alias_and_input_boundaries(setup):
    setup.pages[TICKERS_URL] = page(TICKERS_URL, json.dumps({"0": {"ticker": "BRK-B", "cik_str": 909832}}))
    setup.pages[SUBMISSIONS] = page(SUBMISSIONS, json.dumps(metadata([CURRENT], ticker="BRK-B")))
    assert asyncio.run(setup.service().resolve("brk.b"))["ticker"] == "BRK.B"
    for invalid in ("COST MSFT", "../../etc", "A; echo x", "A" * 16, ""):
        with pytest.raises(ValueError):
            normalize_ticker(invalid)


@pytest.mark.parametrize("change,expected", [
    ({"earnings_date": "2026-09-26"}, "upcoming"),
    ({"period_end": "2026-05-10"}, "older earnings period"),
    ({"expected_form": "10-Q"}, "expected SEC form"),
    ({"release_url": "https://news.example.com/costco-release"}, "issuer or SEC URL"),
    ({"release_url": "https://costco.evil.com/release"}, "issuer or SEC URL"),
])
def test_locator_rejects_unverified_event_metadata(setup, change, expected):
    setup.hints["event"] = event_candidate(**change)
    async def run():
        service = setup.service()
        return await service.locate(await service.resolve("COST"))
    with pytest.raises(ValueError, match=expected):
        asyncio.run(run())


def test_release_must_contain_actual_company_dates_and_financial_results(setup):
    setup.pages[RELEASE] = page(RELEASE, "Costco Wholesale will report financial results on September 24, 2026. " * 10)
    async def run():
        service = setup.service()
        return await service.locate(await service.resolve("COST"))
    with pytest.raises(ValueError, match="did not verify"):
        asyncio.run(run())


def _separate_ir_domain(setup, *, supplied_hint=True):
    corporate = "https://www.costco.com/leadership"
    ir = "https://investor.different-brand.example/home/default.aspx"
    release = "https://investor.different-brand.example/results/2026-q4"
    setup.pages[corporate] = replace(page(corporate, "Costco Wholesale Corporation leadership. Investor Relations."), links=(ir,))
    setup.pages[ir] = replace(page(ir, "Costco Wholesale Corporation Investor Relations. Financials and investor news."), links=(release,))
    setup.pages[release] = page(release, release_text())
    setup.hints["event"] = event_candidate(release_url=release, issuer_page_url=corporate if supplied_hint else "")
    setup.hints["issuer_domain"] = {"issuer_page_url": corporate}
    return corporate, ir, release


def _save_identity_lead(setup, company, event=None, *, namespace="real"):
    from backend.app.research.workflows import WorkflowStore
    store = WorkflowStore(setup.repo)
    workflow = store.create("COST", namespace=namespace)
    store.step(workflow, "resolve", "completed", output=company)
    if event:
        store.step(workflow, "locate", "completed", output=event)
    store.finish(workflow, "partial", {})
    return workflow


def test_saved_cik_skips_directory_and_model_identity_but_refreshes_sec_metadata(setup):
    old = asyncio.run(setup.service().resolve("COST"))
    workflow = _save_identity_lead(setup, old)
    current_metadata = metadata([CURRENT])
    current_metadata["website"] = "https://www.costco.com/"
    setup.pages[SUBMISSIONS] = page(SUBMISSIONS, json.dumps(current_metadata))
    setup.calls.clear()
    setup.discovery.clear()
    current = asyncio.run(setup.service().resolve("COST"))
    assert setup.calls == [SUBMISSIONS]
    assert setup.discovery == []
    assert len(current["filings"]) == 1
    assert current["website"] == "https://www.costco.com/"
    assert current["verification_hash"] != old["verification_hash"]
    assert current["identity_reuse"]["cik_workflow_id"] == workflow
    assert current["resolution"] == "Retained CIK, freshly verified against SEC submissions"


@pytest.mark.parametrize("failure", ["different_ticker", "different_cik", "unavailable", "redirect"])
def test_failed_fresh_confirmation_of_saved_cik_falls_back_to_normal_resolution(setup, failure):
    old = asyncio.run(setup.service().resolve("COST"))
    stale_cik = "0000000123"
    stale_url = f"https://data.sec.gov/submissions/CIK{stale_cik}.json"
    _save_identity_lead(setup, {**old, "cik": stale_cik, "submissions_url": stale_url})
    data = {**metadata([CURRENT]), "cik": stale_cik}
    if failure == "different_ticker":
        data["tickers"] = ["OTHER"]
    elif failure == "different_cik":
        data["cik"] = CIK
    setup.pages[stale_url] = page(stale_url, json.dumps(data), error="source returned HTTP 403" if failure == "unavailable" else None)
    if failure == "redirect":
        setup.pages[stale_url] = replace(setup.pages[stale_url], final_url="https://untrusted.example/metadata.json")
    setup.calls.clear()
    setup.discovery.clear()
    current = asyncio.run(setup.service().resolve("COST"))
    assert current["cik"] == CIK
    assert current["resolution"] == "SEC ticker directory"
    assert setup.calls == [stale_url, TICKERS_URL, SUBMISSIONS]
    assert "identity_reuse" not in current


@pytest.mark.parametrize("failure", ["namespace", "ticker", "cik", "hash", "submissions_url"])
def test_invalid_or_cross_namespace_saved_identity_is_not_used(setup, failure):
    old = asyncio.run(setup.service().resolve("COST"))
    if failure != "namespace":
        old[{"ticker": "ticker", "cik": "cik", "hash": "verification_hash", "submissions_url": "submissions_url"}[failure]] = "invalid"
    _save_identity_lead(setup, old, namespace="demo" if failure == "namespace" else "real")
    setup.calls.clear()
    current = asyncio.run(setup.service().resolve("COST"))
    assert setup.calls == [TICKERS_URL, SUBMISSIONS]
    assert "identity_reuse" not in current


def test_saved_source_bound_ir_identity_is_reused_but_latest_event_is_discovered_again(setup):
    corporate, ir, release = _separate_ir_domain(setup)
    async def prepare():
        service = setup.service()
        company = await service.resolve("COST")
        return company, await service.locate(company)
    company, event = asyncio.run(prepare())
    workflow = _save_identity_lead(setup, company, event)
    setup.calls.clear()
    setup.discovery.clear()
    async def reuse():
        service = setup.service()
        current_company = await service.resolve("COST")
        current_event = await service.locate(current_company)
        return current_company, current_event
    current_company, current_event = asyncio.run(reuse())
    assert current_company["investor_website"] == ir
    assert current_company["identity_reuse"]["issuer_domain_workflow_id"] == workflow
    assert current_event["issuer_domain_verification"] == event["issuer_domain_verification"]
    assert current_event["verification"] == "primary_release"
    assert setup.calls == [SUBMISSIONS, release]
    assert [call["stage"] for call in setup.discovery] == ["event"]
    assert ir in setup.discovery[0]["prompt"]
    assert corporate not in setup.calls


@pytest.mark.parametrize("invalidate_after_resolve", [False, True])
def test_stale_ir_receipt_cannot_be_reused_for_domain_authority(setup, invalidate_after_resolve):
    _corporate, ir, _release = _separate_ir_domain(setup)
    async def prepare():
        service = setup.service()
        company = await service.resolve("COST")
        return company, await service.locate(company)
    company, event = asyncio.run(prepare())
    _save_identity_lead(setup, company, event)
    current = asyncio.run(setup.service().resolve("COST")) if invalidate_after_resolve else None
    proof_id = event["issuer_domain_verification"]["sources"][1]["source_id"]
    with setup.repo.db.transaction(immediate=True) as conn:
        conn.execute("UPDATE sources SET original_content=original_content || ' changed' WHERE id=?", (proof_id,))
    if current is None:
        current = asyncio.run(setup.service().resolve("COST"))
        assert current["investor_website"] == ""
    else:
        assert current["investor_website"] == ir
    setup.hints["event"]["issuer_page_url"] = ""
    setup.hints["issuer_domain"] = {"issuer_page_url": "", "alternate_issuer_page_urls": []}
    with pytest.raises(ValueError, match="issuer or SEC URL"):
        asyncio.run(setup.service().locate(current))


def test_reused_ir_proof_restores_fresh_sec_identity_when_revoked(setup):
    from backend.app.research.earnings_sources import company_with_verified_issuer_domain
    _separate_ir_domain(setup)
    async def prepare():
        service = setup.service()
        company = await service.resolve("COST")
        company["investor_website"] = "https://previous-ir.example/"
        return company, await service.locate(company)
    company, event = asyncio.run(prepare())
    assert event["issuer_domain_verification"]["original_investor_website"] == "https://previous-ir.example/"
    _save_identity_lead(setup, company, event)
    current = asyncio.run(setup.service().resolve("COST"))
    assert current["issuer_domain_verification"]["original_investor_website"] == ""
    with setup.repo.db.operation() as conn:
        revoked = company_with_verified_issuer_domain(current, {}, conn, "real")
    assert revoked["investor_website"] == ""


@pytest.mark.parametrize("supplied_hint", [True, False])
def test_separate_ir_domain_requires_archived_corporate_link_and_survives_checkpoints(setup, supplied_hint):
    from backend.app.research.earnings_sources import company_with_verified_issuer_domain, _primary_release_url
    corporate, ir, release = _separate_ir_domain(setup, supplied_hint=supplied_hint)
    async def run():
        service = setup.service()
        company = await service.resolve("COST")
        event = await service.locate(company)
        acquired = await setup.service().acquire(json.loads(json.dumps(company)), json.loads(json.dumps(event)))
        return company, event, acquired
    company, event, acquired = asyncio.run(run())
    assert company["investor_website"] == ""
    assert event["verification"] == "primary_release"
    receipt = event["issuer_domain_verification"]
    assert receipt["issuer_page_url"] == corporate
    assert receipt["investor_website"] == ir
    assert receipt["observed_links"] == [ir]
    assert len(receipt["sources"]) == 2
    with setup.repo.db.operation() as conn:
        bound = company_with_verified_issuer_domain(company, event, conn, "real")
        assert bound["investor_website"] == ir
        assert _primary_release_url(release, bound)
        assert not company_with_verified_issuer_domain(company, event, conn, "demo").get("investor_website")
    assert acquired["documents"]["release"]["status"] == "available"
    assert sum(call["stage"] == "issuer_domain" for call in setup.discovery) == (0 if supplied_hint else 1)


@pytest.mark.parametrize("failure", ["no_link", "untrusted_root", "redirect", "wrong_company", "no_investor_identity"])
def test_separate_ir_domain_does_not_accept_unverified_self_claims(setup, failure):
    corporate, ir, release = _separate_ir_domain(setup)
    setup.hints["issuer_domain"] = {"issuer_page_url": "", "alternate_issuer_page_urls": []}
    if failure == "no_link":
        setup.pages[corporate] = replace(setup.pages[corporate], links=())
    elif failure == "untrusted_root":
        setup.hints["event"]["issuer_page_url"] = "https://costco.attacker.example/leadership"
    elif failure == "redirect":
        setup.pages[corporate] = replace(setup.pages[corporate], final_url="https://attacker.example/")
    elif failure == "wrong_company":
        setup.pages[ir] = page(ir, "Unrelated Corporation Investor Relations and financials.")
    else:
        setup.pages[ir] = page(ir, "Costco Wholesale marketing and company jobs.")
    async def run():
        service = setup.service()
        return await service.locate(await service.resolve("COST"))
    with pytest.raises(ValueError, match="issuer or SEC URL"):
        asyncio.run(run())
    assert not any(source["url"] == release for source in setup.repo.sources("real"))


@pytest.mark.parametrize("identity_text", [
    "Costco Wholesale Corporation financials from an independent news publisher.",
    "Costco Wholesale Corporation investor news from an independent news publisher.",
])
def test_linked_financial_news_vendor_is_not_ir_authority_on_creation_or_replay(setup, identity_text):
    from backend.app.research.earnings_sources import company_with_verified_issuer_domain
    corporate, ir, release = _separate_ir_domain(setup)
    setup.pages[ir] = page(ir, identity_text)
    async def prepare():
        service = setup.service()
        company = await service.resolve("COST")
        assert await service._verify_issuer_domain_candidate(company, release, corporate) is None
        # Model an older, structurally valid receipt from the permissive gate.
        # All hashes/versions remain valid so replay must reject its identity.
        documents = [service._document(setup.pages[url], title="Archived issuer identity", kind="issuer_identity") for url in (corporate, ir)]
        sources = [setup.repo.sources("real", document["source_id"])[0] for document in documents]
        receipt = {
            "version": "issuer-domain-link.v1", "namespace": "real", "ticker": "COST", "cik": CIK,
            "original_investor_website": "", "issuer_page_url": corporate,
            "linked_investor_url": ir, "investor_website": ir, "observed_links": [ir],
            "sources": [{"source_id": source["id"], "version": source["version"], "content_hash": source["content_hash"]} for source in sources],
        }
        return company, receipt
    company, receipt = asyncio.run(prepare())
    enriched_company = {**company, "investor_website": ir, "issuer_domain_verification": receipt}
    with setup.repo.db.operation() as conn:
        result = company_with_verified_issuer_domain(enriched_company, {"issuer_domain_verification": receipt}, conn, "real")
    assert result["investor_website"] == ""
    assert "issuer_domain_verification" not in result


def test_verified_domain_published_as_source_bound_identity_and_rechecked_by_primary_policy(setup):
    from backend.app.research.workflows import ResearchWorkflows
    from backend.app.research.earnings_primary_policy import verified_earnings_release_policy
    from backend.app.research.earnings_sources import company_with_verified_issuer_domain
    _corporate, ir, _release = _separate_ir_domain(setup)
    async def prepare():
        service = setup.service()
        company = await service.resolve("COST")
        event = await service.locate(company)
        return company, event, await service.acquire(company, event)
    company, event, acquired = asyncio.run(prepare())
    workflows = ResearchWorkflows(setup.repo, None, setup.config)
    workflow = workflows.store.create("COST")
    for stage, payload in (("resolve", company), ("locate", event), ("acquire", acquired)):
        workflows.store.step(workflow, stage, "completed", output=payload)
    run = workflows.store.get(workflow)
    package = workflows._publish(run, {step["id"]: step for step in run["steps"]})
    assert package["company"]["investor_website"] == ir
    assert all(binding["source_id"] in package["source_bindings"] for binding in event["issuer_domain_verification"]["sources"])
    workflows.store.finish(workflow, "partial", package)
    release_id = event["release_source_id"]
    with setup.repo.db.operation() as conn:
        release_row = conn.execute("SELECT * FROM sources WHERE id=?", (release_id,)).fetchone()
        assert verified_earnings_release_policy(conn, release_row)["primary_evidence"] is True
    proof_id = event["issuer_domain_verification"]["sources"][0]["source_id"]
    with setup.repo.db.transaction(immediate=True) as conn:
        conn.execute("UPDATE sources SET original_content=original_content || ' changed' WHERE id=?", (proof_id,))
    with setup.repo.db.operation() as conn:
        assert not company_with_verified_issuer_domain(package["company"], event, conn, "real").get("investor_website")
        assert verified_earnings_release_policy(conn, conn.execute("SELECT * FROM sources WHERE id=?", (release_id,)).fetchone()) is None


def test_unreadable_supplied_identity_hint_uses_one_bounded_corporate_fallback(setup):
    corporate, ir, _release = _separate_ir_domain(setup)
    blocked = f"https://www.sec.gov/Archives/edgar/data/909832/000090983226000099/exhibit991.htm"
    setup.hints["event"]["issuer_page_url"] = blocked
    setup.pages[blocked] = page(blocked, error="source returned HTTP 403")
    missing_link = "https://www.costco.com/company-info"
    setup.pages[missing_link] = page(missing_link, "Costco Wholesale company information without investor link.")
    setup.hints["issuer_domain"] = {"issuer_page_url": missing_link, "alternate_issuer_page_urls": [corporate]}
    async def run():
        service = setup.service()
        return await service.locate(await service.resolve("COST"))
    event = asyncio.run(run())
    assert event["verification"] == "primary_release"
    assert event["issuer_domain_verification"]["investor_website"] == ir
    assert len([call for call in setup.discovery if call["stage"] == "issuer_domain"]) == 1
    assert setup.calls.count(blocked) == 1


def test_observed_corporate_link_chain_avoids_another_identity_model_call(setup):
    corporate, ir, release = _separate_ir_domain(setup)
    blocked = "https://www.sec.gov/Archives/edgar/data/909832/000090983226000099/exhibit991.htm"
    about = "https://www.costco.com/company-info"
    setup.hints["event"]["issuer_page_url"] = blocked
    setup.pages[blocked] = page(blocked, error="source returned HTTP 403")
    setup.pages[release] = replace(setup.pages[release], links=(about,))
    setup.pages[about] = replace(page(about, "Costco Wholesale Corporation company information and leadership."), links=(corporate,))
    setup.hints["issuer_domain"] = {"issuer_page_url": "", "alternate_issuer_page_urls": []}
    async def run():
        service = setup.service()
        return await service.locate(await service.resolve("COST"))
    event = asyncio.run(run())
    assert event["verification"] == "primary_release"
    assert event["issuer_domain_verification"]["issuer_page_url"] == corporate
    assert event["issuer_domain_verification"]["investor_website"] == ir
    assert not any(call["stage"] == "issuer_domain" for call in setup.discovery)
    assert setup.calls.count(about) == setup.calls.count(corporate) == 1


def test_untrusted_ir_page_cannot_self_approve_without_corporate_backlink(setup):
    corporate, ir, release = _separate_ir_domain(setup)
    setup.hints["event"]["issuer_page_url"] = ""
    setup.hints["issuer_domain"] = {"issuer_page_url": "", "alternate_issuer_page_urls": []}
    setup.pages[release] = replace(setup.pages[release], links=(corporate, ir))
    setup.pages[corporate] = replace(setup.pages[corporate], links=())
    async def run():
        service = setup.service()
        return await service.locate(await service.resolve("COST"))
    with pytest.raises(ValueError, match="issuer or SEC URL"):
        asyncio.run(run())
    assert not any(source["url"] == release for source in setup.repo.sources("real"))
    assert ir not in setup.calls


@pytest.mark.parametrize("text,reason", [
    (transcript_text(quarter="Q3"), "quarter"),
    (transcript_text(reported="May 28, 2026"), "date"),
    ("Costco (COST) Q4 2026 September 24, 2026 Earnings webcast", "excerpt"),
    (transcript_text().replace("This concludes today's call. You may now disconnect.", "Subscribe to read the full transcript."), "subscription"),
    (transcript_text().replace("Costco (COST)", "Microsoft (MSFT)"), "company"),
])
def test_transcript_requires_matching_complete_readable_call(text, reason):
    problem = transcript_rejection(page(TRANSCRIPT, text), {"ticker": "COST", "name": "COSTCO WHOLESALE CORP"}, event_candidate())
    assert problem and reason in problem.lower()


def test_transcript_title_cannot_mask_contradictory_quarter_in_document():
    contradictory = FetchedSource(TRANSCRIPT, TRANSCRIPT, transcript_text(quarter="Q3"), "Costco Q4 2026 Transcript", NOW)
    assert "quarter" in transcript_rejection(contradictory, {"ticker": "COST", "name": "Costco"}, event_candidate()).lower()


def test_transcript_rejects_avatar_and_bare_name_layout_without_inventing_roles():
    # This is the archived ROIC page's structural pattern with synthetic prose:
    # avatar initials, bare names, and executive roles mentioned only in speech.
    text = transcript_text().replace("Gary Millerchip\nChief Financial Officer", "G\nGary Millerchip").replace("Jane Smith — Analyst", "J\nJane Smith")
    text = text.replace("Welcome to our earnings call.", "Welcome to our earnings call. Our CFO Gary Millerchip will discuss results.")
    problem = transcript_rejection(page(TRANSCRIPT, text), {"ticker": "COST", "name": "Costco"}, event_candidate())
    assert problem and "attributable management remarks" in problem
    assert analyze_transcript(_transcript_text(page(TRANSCRIPT, text)))["reading_context"]["exchanges"] == []


def test_transcript_rejects_management_only_layout_with_unattributed_analyst():
    text = transcript_text().replace("Jane Smith — Analyst", "J\nJane Smith")
    problem = transcript_structure_rejection(_transcript_text(page(TRANSCRIPT, text)))
    assert problem and "analyst question" in problem


def test_unsupported_transcript_layout_falls_back_to_next_candidate_without_search(setup):
    unsupported = "https://publisher.example.com/cost-q4-2026"
    bad = transcript_text().replace("Gary Millerchip\nChief Financial Officer", "G\nGary Millerchip").replace("Jane Smith — Analyst", "J\nJane Smith")
    setup.pages[unsupported] = page(unsupported, bad)
    setup.hints["event"] = event_candidate(transcript_urls=[unsupported, TRANSCRIPT])
    async def run():
        service = setup.service()
        company = await service.resolve("COST")
        return await service.acquire(company, await service.locate(company))
    result = asyncio.run(run())
    document = result["documents"]["transcript"]
    assert document["status"] == "available" and document["url"] == TRANSCRIPT
    assert document["checks"] == [{"url": unsupported, "reason": "The transcript layout does not identify any attributable management remarks; try another complete transcript source."}]
    assert not any(item["stage"] == "transcript" for item in setup.discovery)


def test_unsupported_transcript_layout_uses_bounded_same_event_rediscovery(setup):
    unsupported = "https://publisher.example.com/cost-q4-2026"
    setup.pages[unsupported] = page(unsupported, transcript_text().replace("Gary Millerchip\nChief Financial Officer", "G\nGary Millerchip"))
    setup.hints["event"] = event_candidate(transcript_urls=[unsupported])
    setup.hints["transcript"] = {"urls": [TRANSCRIPT]}
    async def run():
        service = setup.service()
        company = await service.resolve("COST")
        return await service.acquire(company, await service.locate(company))
    result = asyncio.run(run())
    assert result["documents"]["transcript"]["url"] == TRANSCRIPT
    calls = [item for item in setup.discovery if item["stage"] == "transcript"]
    assert len(calls) == 1 and "attributable management remarks" in calls[0]["prompt"]
    assert "2026-09-24" in calls[0]["prompt"] and "2026-08-30" in calls[0]["prompt"]


def test_exact_event_archive_candidate_is_revalidated_before_fresh_discovery(setup, monkeypatch):
    from backend.app.research import earnings_transcript_cache
    unsupported = "https://publisher.example.com/cost-q4-2026"
    setup.pages[unsupported] = page(unsupported, transcript_text().replace("Gary Millerchip\nChief Financial Officer", "G\nGary Millerchip"))
    setup.hints["event"] = event_candidate(transcript_urls=[unsupported])
    consulted = []
    def candidates(repo, namespace, company, event):
        consulted.append((namespace, company["cik"], event["period_end"], event["earnings_date"]))
        return [TRANSCRIPT]
    monkeypatch.setattr(earnings_transcript_cache, "prior_transcript_candidates", candidates)
    async def run():
        service = setup.service()
        company = await service.resolve("COST")
        return await service.acquire(company, await service.locate(company))
    result = asyncio.run(run())
    assert consulted == [("real", CIK, "2026-08-30", "2026-09-24")]
    assert result["documents"]["transcript"]["url"] == TRANSCRIPT
    assert setup.calls.index(unsupported) < setup.calls.index(TRANSCRIPT)
    assert not any(item["stage"] == "transcript" for item in setup.discovery)


def test_archived_transcript_candidate_does_not_bypass_current_layout_gate(setup, monkeypatch):
    from backend.app.research import earnings_transcript_cache
    unsupported = "https://publisher.example.com/cost-q4-2026"
    setup.pages[unsupported] = page(unsupported, transcript_text().replace("Gary Millerchip\nChief Financial Officer", "G\nGary Millerchip"))
    setup.hints["event"] = event_candidate(transcript_urls=[])
    setup.hints["transcript"] = {"urls": [TRANSCRIPT]}
    monkeypatch.setattr(earnings_transcript_cache, "prior_transcript_candidates", lambda *args: [unsupported])
    async def run():
        service = setup.service()
        company = await service.resolve("COST")
        return await service.acquire(company, await service.locate(company))
    result = asyncio.run(run())
    assert result["documents"]["transcript"]["url"] == TRANSCRIPT
    assert result["documents"]["transcript"]["checks"][0]["url"] == unsupported
    assert "attributable management" in result["documents"]["transcript"]["checks"][0]["reason"]
    assert len([item for item in setup.discovery if item["stage"] == "transcript"]) == 1


def test_acquisition_rediscovers_newly_published_transcript_for_checkpointed_event(setup):
    setup.hints["event"] = event_candidate(transcript_urls=[])
    setup.hints["transcript"] = {"urls": [TRANSCRIPT]}
    async def run():
        service = setup.service()
        company = await service.resolve("COST")
        checkpoint = json.loads(json.dumps(await service.locate(company)))
        return await setup.service().acquire(company, checkpoint)
    result = asyncio.run(run())
    assert result["documents"]["transcript"]["status"] == "available"
    refresh = next(item for item in setup.discovery if item["stage"] == "transcript")
    assert "2026-09-24" in refresh["prompt"] and "2026-08-30" in refresh["prompt"]
    assert "Q4 FY2026" in refresh["prompt"] and "Do not change event" in refresh["prompt"]


def test_verified_release_survives_unavailable_optional_transcript_search(setup):
    setup.hints["event"] = event_candidate(transcript_urls=[])
    async def run():
        service = setup.service()
        company = await service.resolve("COST")
        event = await service.locate(company)
        assert event["verification"] == "primary_release" and not event["transcript_urls"]
        assert not any(item["stage"] == "transcript" for item in setup.discovery)
        assert TRANSCRIPT not in setup.calls
        # A failed optional discovery must not erase a completed locator
        # checkpoint or the independently fetched primary source.
        checkpoint = json.loads(json.dumps(event))
        collector = setup.service()
        discover = collector.discoverer
        def bounded_failure(stage, prompt, schema):
            if stage == "transcript":
                raise ValueError("Codex exceeded bounded discovery search-query limit (6).")
            return discover(stage, prompt, schema)
        collector.discoverer = bounded_failure
        result = await collector.acquire(company, checkpoint)
        assert checkpoint == event
        return event, result
    event, result = asyncio.run(run())
    assert result["documents"]["release"]["status"] == "available"
    assert result["documents"]["release"]["source_id"] == event["release_source_id"]
    assert result["documents"]["transcript"]["status"] == "unavailable"
    assert any("Pinned-period transcript discovery unavailable" in row["reason"] for row in result["documents"]["transcript"]["checks"])
    assert result["gaps"]


@pytest.mark.parametrize("text,reason", [
    (transcript_text(reported="May 28, 2026"), "date"),
    (transcript_text(quarter="Q3"), "quarter"),
])
def test_transcript_recovery_cannot_move_verified_event_to_another_period(setup, text, reason):
    setup.hints["event"] = event_candidate(transcript_urls=[])
    setup.hints["transcript"] = {"urls": [TRANSCRIPT]}
    setup.pages[TRANSCRIPT] = page(TRANSCRIPT, text)
    async def run():
        service = setup.service()
        company = await service.resolve("COST")
        event = await service.locate(company)
        return event, await service.acquire(company, event)
    event, result = asyncio.run(run())
    assert event["earnings_date"] == "2026-09-24" and event["period_end"] == "2026-08-30"
    assert result["documents"]["release"]["source_id"] == event["release_source_id"]
    assert result["documents"]["transcript"]["status"] == "unavailable"
    assert reason in result["documents"]["transcript"]["checks"][0]["reason"].lower()
    assert len([item for item in setup.discovery if item["stage"] == "transcript"]) == 1


def test_existing_or_incidental_transcript_urls_carry_through_without_rediscovery(setup):
    async def run():
        service = setup.service()
        company = await service.resolve("COST")
        checkpoint = json.loads(json.dumps(await service.locate(company)))
        assert checkpoint["transcript_urls"] == [TRANSCRIPT] and checkpoint["transcript_url"] == TRANSCRIPT
        return await setup.service().acquire(company, checkpoint)
    result = asyncio.run(run())
    assert result["documents"]["transcript"]["status"] == "available"
    assert not any(item["stage"] == "transcript" for item in setup.discovery)


def test_bounded_historical_submissions_find_previous_annual_report(setup):
    filename = f"CIK{CIK}-submissions-001.json"
    history_url = "https://data.sec.gov/submissions/" + filename
    setup.pages[SUBMISSIONS] = page(SUBMISSIONS, json.dumps(metadata([CURRENT], history=[{"name": filename}])))
    setup.pages[history_url] = page(history_url, json.dumps(metadata([PRIOR])["filings"]["recent"]))
    async def run():
        service = setup.service()
        company = await service.resolve("COST")
        return await service.acquire(company, await service.locate(company))
    result = asyncio.run(run())
    assert result["documents"]["prior_filing"]["status"] == "available"
    assert setup.calls.count(history_url) == 1


def test_filing_mirror_is_checked_against_sec_identity_and_report_period(setup):
    bad, good = "https://reports.example.com/wrong.pdf", "https://reports.example.com/cost2026.pdf"
    setup.pages[filing_url(CURRENT)] = page(filing_url(CURRENT), error="HTTP 403")
    setup.hints["filing_mirror"] = {"urls": [bad, good]}
    setup.pages[bad] = page(bad, filing_text(period="August 31, 2025") + "\nFinancial comparisons include the period ended August 30, 2026.")
    setup.pages[good] = page(good, filing_text())
    async def run():
        service = setup.service()
        company = await service.resolve("COST")
        return await service.acquire(company, await service.locate(company))
    result = asyncio.run(run())
    current = result["documents"]["current_filing"]
    assert current["status"] == "available" and current["url"] == good
    assert current["provenance"] == "public_mirror"
    assert current["canonical_url"] == filing_url(CURRENT)
    assert "report period" in current["checks"][1]["reason"]


def test_unreachable_current_index_cannot_claim_latest_filing_is_pending(setup):
    async def run():
        service = setup.service()
        setup.pages[SUBMISSIONS] = page(SUBMISSIONS, json.dumps(metadata([OLD_Q, PRIOR])))
        company = await service.resolve("COST")
        event = await service.locate(company)
        setup.pages[SUBMISSIONS] = page(SUBMISSIONS, error="connection failed")
        return await service.acquire(company, event)
    result = asyncio.run(run())
    assert result["documents"]["current_filing"]["status"] == "unavailable"
    assert any("could not be refreshed" in item for item in result["comparison_gaps"])
    assert result["gaps"] == []


def test_quarterly_baseline_uses_prior_same_form_without_amendments():
    rows = [
        {"form": "10-Q", "period_end": "2026-03-31", "filed_at": "2026-04-20"},
        {"form": "10-K", "period_end": "2025-12-31", "filed_at": "2026-02-20"},
        {"form": "10-Q/A", "period_end": "2026-03-31", "filed_at": "2026-04-25"},
        {"form": "10-Q", "period_end": "2025-09-30", "filed_at": "2025-10-20"},
    ]
    current, prior = select_filings(rows, {"expected_form": "10-Q", "period_end": "2026-03-31"})
    assert current["form"] == prior["form"] == "10-Q"
    assert prior["period_end"] == "2025-09-30"


def test_discovery_uses_saved_policy_shared_capacity_and_explicit_bounds(setup):
    calls = []
    @asynccontextmanager
    async def slot(provider, origin):
        calls.append(("slot", provider, origin))
        yield
    async def execute(*args, **kwargs):
        calls.append(("execute", args, kwargs))
        return ProviderResult({"cik": "909832", "sec_url": SUBMISSIONS}, {})
    registry = SimpleNamespace(generation_slot=slot, codex=SimpleNamespace(execute=execute))
    service = EarningsAcquisition(setup.repo, registry, setup.config)
    result = asyncio.run(service._discover("identity", "Public company metadata only", {"type": "object"}))
    assert result["cik"] == "909832"
    assert calls[0] == ("slot", "codex", "earnings")
    args, kwargs = calls[1][1:]
    assert args[2] == setup.repo.resolve_model("A01")[0]
    assert kwargs["discovery_stage"] is True
    assert kwargs["discovery_limits"].max_search_queries == 6
    assert kwargs["discovery_limits"].max_web_actions == 12
    assert args[4].is_dir()
    provenance = json.loads((args[4] / "provenance.json").read_text())
    assert provenance["status"] == "completed"
    assert len(provenance["prompt_hash"]) == 64
    assert provenance["output"]["cik"] == "909832"
    assert result["_discovery"]["model"] == setup.repo.resolve_model("A01")[0].model_dump()


def test_final_locator_prompt_prioritizes_primary_release_with_unchanged_limits(setup):
    calls = []
    @asynccontextmanager
    async def slot(provider, origin):
        yield
    async def execute(*args, **kwargs):
        calls.append((args, kwargs))
        return ProviderResult(event_candidate(transcript_urls=[]), {})
    registry = SimpleNamespace(generation_slot=slot, codex=SimpleNamespace(execute=execute))
    async def run():
        collector = setup.service()
        company = await collector.resolve("COST")
        service = EarningsAcquisition(setup.repo, registry, setup.config, fetcher=collector.fetcher, today=date(2026, 9, 25))
        return await service.locate(company)
    event = asyncio.run(run())
    assert event["verification"] == "primary_release" and event["transcript_urls"] == []
    assert len(calls) == 1
    args, kwargs = calls[0]
    instruction, schema = args[1], args[3]
    assert "6 individual search queries and 12 total web actions" in instruction
    assert "A batched search counts each query separately" in instruction
    assert "This stage only identifies the latest reported event and its primary release" in instruction
    assert "do not spend additional searches or page reads looking for transcripts" in instruction
    assert "Return transcript_urls=[]" in instruction
    assert "Find up to four direct complete earnings-call transcript URLs" not in instruction
    assert "do not search for transcripts in this stage" in schema["properties"]["transcript_urls"]["description"]
    assert kwargs["discovery_limits"].max_search_queries == 6
    assert kwargs["discovery_limits"].max_web_actions == 12


def acquire_packet(setup):
    async def run():
        service = setup.service()
        company = await service.resolve("COST")
        return await service.acquire(company, await service.locate(company))
    return asyncio.run(run())


def test_latest_event_materials_are_useful_while_annual_filing_is_pending(setup):
    supplement = "https://investor.costco.com/earnings/2026-q4-supplement"
    deck = "https://investor.costco.com/earnings/2026-q4-slides.pdf"
    setup.pages[SUBMISSIONS] = page(SUBMISSIONS, json.dumps(metadata([OLD_Q, PRIOR])))
    setup.hints["materials"] = {"materials": [
        {"kind": "financial_supplement", "url": supplement, "issuer_page_url": ""},
        {"kind": "presentation", "url": deck, "issuer_page_url": ""},
        {"kind": "release", "url": RELEASE, "issuer_page_url": ""},
        {"kind": "presentation", "url": deck + "#page=1", "issuer_page_url": ""},
    ]}
    setup.pages[supplement] = page(supplement, release_text() + "\nQuarterly financial supplement\nRenewal rate 92.3%.")
    # A deck can identify its call date and quarter without spelling out the
    # fiscal period end. The same safe fetcher handles extracted PDF text.
    setup.pages[deck] = page(deck, "Costco Wholesale Q4 FY2026 Earnings Presentation\nSeptember 24, 2026\n" + "Net sales increased and membership renewal remained strong. " * 8)
    result = acquire_packet(setup)
    assert result["documents"]["current_filing"]["status"] == "pending"
    assert result["gaps"] == [] and result["comparison_gaps"]
    assert result["material_gaps"] == []
    assert [item["kind"] for item in result["materials"]] == ["release", "transcript", "financial_supplement", "presentation"]
    assert all("content" not in item and item["source_id"] for item in result["materials"])
    assert setup.calls.count(RELEASE) == setup.calls.count(deck) == 1
    retained = setup.repo.source_packet("real", [item["source_id"] for item in result["materials"]])
    assert any(item["content"] == setup.pages[supplement].content for item in retained)
    assert any(item["stage"] == "materials" for item in setup.discovery)


def test_optional_material_failure_does_not_hide_release_or_transcript(setup):
    wrong = "https://investor.costco.com/earnings/old-slides"
    pdf = "https://investor.costco.com/earnings/current-slides.pdf"
    outsider = "https://costco.evil.example/current-slides"
    setup.hints["materials"] = {"materials": [
        {"kind": "presentation", "url": wrong, "issuer_page_url": ""},
        {"kind": "presentation", "url": pdf, "issuer_page_url": ""},
        {"kind": "presentation", "url": outsider, "issuer_page_url": ""},
    ]}
    setup.pages[wrong] = page(wrong, "Costco Q3 FY2026 Earnings\nSeptember 24, 2026\n" + release_text())
    setup.pages[pdf] = page(pdf, error="PDF extraction failed: document is image-only")
    result = acquire_packet(setup)
    assert result["gaps"] == [] and result["material_gaps"]
    assert {item["kind"] for item in result["materials"]} == {"release", "transcript", "current_filing"}
    assert any("different earnings quarter" in item for item in result["material_gaps"])
    assert any("image-only" in item for item in result["material_gaps"])
    assert outsider not in setup.calls


def test_material_discovery_is_bounded_and_optional(setup):
    setup.hints["materials"] = {"materials": [{"kind": "presentation", "url": f"https://investor.costco.com/material-{i}", "issuer_page_url": ""} for i in range(20)]}
    result = acquire_packet(setup)
    assert len(result["material_checks"]) == 8
    assert result["gaps"] == []
    del setup.hints["materials"]
    result = acquire_packet(setup)
    assert result["material_discovery_status"] == "unavailable"
    assert result["gaps"] == [] and "discovery unavailable" in result["material_gaps"][0]


def test_sec_earnings_8k_uses_verified_event_date_not_general_8k_activity(setup):
    earnings_8k = filing_row("8-K", "2026-09-24", "0000909832-26-000084", "2026-09-25") | {"items": "2.02,9.01"}
    other = filing_row("8-K", "2026-09-24", "0000909832-26-000085", "2026-09-25") | {"items": "8.01"}
    setup.pages[SUBMISSIONS] = page(SUBMISSIONS, json.dumps(metadata([earnings_8k, other, OLD_Q, PRIOR])))
    url = filing_url(earnings_8k)
    setup.pages[url] = page(url, "Costco Wholesale Corporation\nFORM 8-K\nDate of report September 24, 2026\nItem 2.02 Results of Operations and Financial Condition\n" + "Our earnings release is furnished as exhibit 99.1. " * 8)
    result = acquire_packet(setup)
    doc = next(item for item in result["materials"] if item["kind"] == "earnings_8k")
    assert doc["accession"] == earnings_8k["accessionNumber"]
    assert "SEC Item 2.02" in doc["verification"]
    assert filing_url(other) not in setup.calls
    assert doc["period_end"] == "2026-08-30"  # fiscal period, not 8-K event date


def test_cdn_material_requires_fetched_direct_issuer_link_and_archives_lineage(setup):
    cdn = "https://s201.q4cdn.com/financials/2026/q4/slides.pdf"
    issuer_page = "https://investor.costco.com/financials/quarterly-results"
    setup.pages[issuer_page] = replace(page(issuer_page, "Costco Wholesale financial results\n" + "Quarterly earnings documents are available for investors. " * 8), links=(cdn,))
    setup.pages[cdn] = page(cdn, release_text() + "\nEarnings slides and capital expenditure guidance.")
    setup.hints["materials"] = {"materials": [{"kind": "presentation", "url": cdn, "issuer_page_url": issuer_page}]}
    result = acquire_packet(setup)
    doc = next(item for item in result["materials"] if item["kind"] == "presentation")
    assert doc["provenance"] == "issuer_linked_asset"
    assert doc["issuer_link_url"] == issuer_page and doc["issuer_link_target_url"] == cdn
    linking = setup.repo.source_packet("real", [doc["issuer_link_source_id"]])[0]
    assert linking["content_hash"] == doc["issuer_link_content_hash"]
    assert linking["url"] == issuer_page
    # A discovery assertion cannot replace the actual link from the issuer.
    setup.pages[issuer_page] = replace(setup.pages[issuer_page], links=())
    result = acquire_packet(setup)
    assert not any(item["kind"] == "presentation" for item in result["materials"])
    assert any("direct link" in item for item in result["material_gaps"])


def test_cdn_link_does_not_exempt_issuer_event_checks_or_external_redirects(setup):
    cdn = "https://cdn.example.com/earnings.pdf"
    setup.hints["materials"] = {"materials": [{"kind": "presentation", "url": cdn, "issuer_page_url": RELEASE}]}
    setup.pages[RELEASE] = replace(setup.pages[RELEASE], links=(cdn,))
    setup.pages[cdn] = page(cdn, release_text(quarter="third", period="May 10, 2026", reported="May 28, 2026"))
    result = acquire_packet(setup)
    assert any("different earnings quarter" in item for item in result["material_gaps"])
    setup.pages[cdn] = replace(page(cdn, release_text()), final_url="https://unrelated.example.com/redirected.pdf")
    result = acquire_packet(setup)
    assert any("redirected away" in item for item in result["material_gaps"])


def test_material_period_checks_reject_older_year_and_unrelated_company():
    company = {"ticker": "COST", "name": "Costco Wholesale Corporation", "cik": CIK}
    event = event_candidate()
    wrong_year = page(RELEASE, "Costco Q4 FY2025 Earnings\n" + release_text())
    assert "different fiscal year" in material_rejection(wrong_year, company, event)
    wrong_company = page(RELEASE, release_text().replace("Costco", "Microsoft"))
    assert "requested issuer" in material_rejection(wrong_company, company, event)
    undated = page(RELEASE, "Costco Q4 FY2026 earnings slides\n" + "Membership revenue and sales increased. " * 10)
    assert "verified earnings date" in material_rejection(undated, company, event)


def test_malformed_optional_material_url_is_a_gap_not_a_workflow_failure(setup):
    setup.hints["materials"] = {"materials": [{"kind": "presentation", "url": "https://[invalid", "issuer_page_url": ""}]}
    result = acquire_packet(setup)
    assert result["gaps"] == []
    assert result["material_checks"][0]["status"] == "rejected"
    assert "malformed" in result["material_gaps"][0]


def test_material_validation_works_with_another_issuer_domain_and_fiscal_calendar():
    company = {"ticker": "MSFT", "name": "Microsoft Corporation", "cik": "0000789019", "investor_website": "https://www.microsoft.com/en-us/Investor"}
    event = {"period_end": "2026-06-30", "earnings_date": "2026-07-29", "fiscal_period": "Q4 FY2026"}
    source = page("https://www.microsoft.com/investor/2026-q4-supplement", "Microsoft Q4 FY2026 Financial Supplement\nJuly 29, 2026\n" + "Revenue, cash flow and capital expenditures increased during the quarter. " * 8)
    assert material_rejection(source, company, event) is None


def test_issuer_link_traversal_preserves_material_when_locator_budget_fails(setup):
    section = "https://investor.costco.com/events-and-presentations/default.aspx"
    event_page = "https://investor.costco.com/events/2026/Q4-FY2026/default.aspx"
    deck = "https://cdn.example.com/2026/q4-earnings-presentation.pdf"
    setup.pages[RELEASE] = replace(setup.pages[RELEASE], links=(section,))
    setup.pages[section] = replace(page(section, "Costco Wholesale Events and Presentations " * 10), links=(event_page,))
    setup.pages[event_page] = replace(page(event_page, release_text()), links=(deck,))
    setup.pages[deck] = page(deck, release_text() + " Earnings presentation and financial supplement.")
    del setup.hints["materials"]
    result = acquire_packet(setup)
    presentation = next(item for item in result["materials"] if item["kind"] == "presentation")
    assert presentation["url"] == deck and presentation["issuer_link_url"] == event_page
    assert result["material_discovery_status"] == "unavailable"
    assert result["gaps"] == []
    assert len([item for item in setup.discovery if item["stage"] == "materials_recovery"]) == 1
    assert all(setup.calls.count(url) == 1 for url in (section, event_page, deck))


def test_one_narrow_recovery_returns_candidates_after_initial_discovery_failure(setup):
    deck = "https://investor.costco.com/2026-q4-slides.pdf"
    setup.pages[deck] = page(deck, release_text() + "\nQuarterly earnings slides and warehouse expansion detail.")
    del setup.hints["materials"]
    setup.hints["materials_recovery"] = {"materials": [{"kind": "presentation", "url": deck, "issuer_page_url": ""}]}
    result = acquire_packet(setup)
    assert result["material_discovery_status"] == "recovered"
    assert any(item["url"] == deck for item in result["materials"])
    assert result["material_gaps"] == []
    prompt = next(item["prompt"] for item in setup.discovery if item["stage"] == "materials_recovery")
    assert "ONE search query" in prompt and "THREE total web actions" in prompt


def test_issuer_traversal_is_bounded_and_skips_old_quarters_and_duplicate_release_pdf(setup):
    links = tuple(f"https://investor.costco.com/events/{i}/default.aspx" for i in range(10))
    old = "https://cdn.example.com/2025/q4-slides.pdf"
    release_pdf = "https://cdn.example.com/2026/Reports-Fourth-Quarter-Results.pdf"
    module = "https://investor.costco.com/events-and-presentations/CustomModules/"
    setup.pages[RELEASE] = replace(setup.pages[RELEASE], links=(*links, old, release_pdf, module))
    for url in links:
        setup.pages[url] = page(url, "Costco Wholesale Events " * 20)
    async def run():
        service = setup.service()
        company = await service.resolve("COST")
        return await service._issuer_material_candidates(company, await service.locate(company))
    candidates, visited = asyncio.run(run())
    assert candidates == [] and len(visited) == 4
    assert old not in setup.calls and release_pdf not in setup.calls
    assert module not in setup.calls


def test_earnings_8k_traversal_archives_actual_same_accession_exhibits(setup):
    earnings = filing_row('8-K', '2026-09-24', '0000909832-26-000084', '2026-09-24') | {'items': '2.02,9.01'}
    setup.pages[SUBMISSIONS] = page(SUBMISSIONS, json.dumps(metadata([earnings, OLD_Q, PRIOR])))
    wrapper = filing_url(earnings)
    exhibit = wrapper.rsplit('/', 1)[0] + '/financial-supplement.htm'
    unrelated = wrapper.replace('000090983226000084', '000090983226000083').rsplit('/', 1)[0] + '/financial-supplement.htm'
    setup.pages[wrapper] = replace(page(wrapper, 'Costco Wholesale Corporation\nFORM8-K\nSeptember 24, 2026\nItem2.02 Results of Operations\n' + 'Financial results are furnished in the attached earnings exhibits. ' * 8), links=(exhibit, unrelated))
    setup.pages[exhibit] = page(exhibit, 'Fourth Quarter FY2026 Supplemental Information\nGross margin11.02%. Net sales growth11.2%.\n' + 'These earnings results present operating highlights for shareholders. ' * 8)
    async def run():
        service = setup.service()
        company = await service.resolve('COST')
        return await service.acquire(company, await service.locate(company))
    result = asyncio.run(run())
    retained = next(item for item in result['materials'] if item['url'] == exhibit)
    assert retained['kind'] == 'earnings_exhibit'
    assert retained['issuer_link_url'] == wrapper
    assert retained['issuer_link_source_id']
    assert unrelated not in setup.calls
    assert 'same-accession' in retained['verification']


def test_acquisition_passes_workspace_sec_identity_to_shared_http_boundary(setup, monkeypatch):
    from backend.app.research import earnings_sources

    observed = []

    def http_boundary(url, **options):
        observed.append(options)
        return page(url, '{}')

    monkeypatch.setattr(earnings_sources, 'fetch_public_page', http_boundary)
    setup.config.sec_user_agent = 'Example Research contact@example.com'
    acquisition = EarningsAcquisition(setup.repo, None, setup.config)
    asyncio.run(acquisition._fetch(SUBMISSIONS))
    assert observed == [{'max_bytes': setup.config.max_source_bytes, 'timeout': 20.0,
                         'sec_user_agent': 'Example Research contact@example.com'}]


def test_refresh_missing_filings_recovers_sources_without_models_or_mutating_prior(setup):
    readable = setup.pages[filing_url(CURRENT)]
    setup.pages[filing_url(CURRENT)] = page(filing_url(CURRENT), error="source returned HTTP 403")
    async def initial():
        service = setup.service()
        company = await service.resolve("COST")
        event = await service.locate(company)
        return company, event, await service.acquire(company, event)
    company, event, prior = asyncio.run(initial())
    snapshot = json.dumps(prior, sort_keys=True)
    setup.pages[filing_url(CURRENT)] = readable
    setup.calls.clear()
    setup.discovery.clear()
    revised = asyncio.run(setup.service().refresh_missing_filings(company, event, prior, research_as_of="2026-09-25T12:00:00Z"))
    assert revised["documents"]["current_filing"]["status"] == "available"
    assert revised["documents"]["prior_filing"]["status"] == "available"
    assert revised["comparison_gaps"] == []
    assert revised["source_refresh"]["changed_filings"] == ["current_filing", "prior_filing"]
    assert revised["documents"]["release"] == prior["documents"]["release"]
    assert revised["documents"]["transcript"] == prior["documents"]["transcript"]
    assert setup.discovery == []
    assert RELEASE not in setup.calls and TRANSCRIPT not in setup.calls
    assert json.dumps(prior, sort_keys=True) == snapshot


def test_refresh_keeps_available_filings_and_respects_research_cutoff(setup):
    async def initial():
        service = setup.service()
        company = await service.resolve("COST")
        event = await service.locate(company)
        return company, event, await service.acquire(company, event)
    company, event, prior = asyncio.run(initial())
    setup.calls.clear()
    revised = asyncio.run(setup.service().refresh_missing_filings(company, event, prior))
    assert revised["documents"] == prior["documents"]
    assert revised["source_refresh"]["changed_filings"] == []
    assert setup.calls == [SUBMISSIONS]

    missing = json.loads(json.dumps(prior))
    missing["documents"]["current_filing"] = {"status": "unavailable"}
    missing["documents"]["prior_filing"] = {"status": "pending"}
    setup.calls.clear()
    revised = asyncio.run(setup.service().refresh_missing_filings(company, event, missing, research_as_of="2026-09-24T12:00:00Z"))
    assert revised["documents"]["current_filing"]["status"] == "pending"
    assert filing_url(CURRENT) not in setup.calls
    assert revised["source_refresh"]["changed_filings"] == []


def test_refresh_rejects_wrong_cover_and_never_launches_mirror_discovery(setup):
    async def initial():
        service = setup.service()
        company = await service.resolve("COST")
        event = await service.locate(company)
        return company, event, await service.acquire(company, event)
    company, event, prior = asyncio.run(initial())
    prior["documents"]["current_filing"] = {"status": "unavailable"}
    setup.pages[filing_url(CURRENT)] = page(filing_url(CURRENT), filing_text(period="August 31, 2025"))
    setup.discovery.clear()
    revised = asyncio.run(setup.service().refresh_missing_filings(company, event, prior))
    assert revised["documents"]["current_filing"]["status"] == "unavailable"
    assert "cover" in revised["documents"]["current_filing"]["checks"][0]["reason"]
    assert revised["comparison_gaps"]
    assert revised["documents"]["prior_filing"] == prior["documents"]["prior_filing"]
    assert setup.discovery == []


def test_refresh_failed_8k_follows_only_observed_same_accession_exhibits(setup):
    earnings = filing_row('8-K', '2026-09-24', '0000909832-26-000084', '2026-09-24') | {'items': '2.02,9.01'}
    setup.pages[SUBMISSIONS] = page(SUBMISSIONS, json.dumps(metadata([earnings, CURRENT, OLD_Q, PRIOR])))
    wrapper = filing_url(earnings)
    exhibit = wrapper.rsplit('/', 1)[0] + '/financial-supplement.htm'
    unrelated = wrapper.replace('000090983226000084', '000090983226000083').rsplit('/', 1)[0] + '/financial-supplement.htm'
    setup.pages[wrapper] = page(wrapper, error="source returned HTTP 403")
    async def initial():
        service = setup.service()
        company = await service.resolve("COST")
        event = await service.locate(company)
        return company, event, await service.acquire(company, event)
    company, event, prior = asyncio.run(initial())
    setup.pages[wrapper] = replace(page(wrapper, 'Costco Wholesale Corporation\nFORM8-K\nSeptember 24, 2026\nItem2.02 Results of Operations\n' + 'Financial results are furnished in the attached earnings exhibits. ' * 8), links=(exhibit, unrelated))
    setup.pages[exhibit] = page(exhibit, 'Fourth Quarter FY2026 Supplemental Information\nGross margin11.02%. Net sales growth11.2%.\n' + 'These earnings results present operating highlights for shareholders. ' * 8)
    setup.calls.clear()
    setup.discovery.clear()
    revised = asyncio.run(setup.service().refresh_missing_filings(company, event, prior))
    assert {item["url"] for item in revised["materials"]} >= {wrapper, exhibit}
    assert not any("403" in gap for gap in revised["material_gaps"])
    assert len(revised["source_refresh"]["added_material_source_ids"]) == 2
    assert revised["documents"] == prior["documents"]
    assert any("403" in gap for gap in prior["material_gaps"])
    assert unrelated not in setup.calls
    assert setup.discovery == []
