"""Between-filing coverage excludes future/foreign evidence and keeps gaps."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app.config import Settings
from backend.app.db import json_loads, utc_now
from backend.app.memory.repository import Repository
from backend.app.research.discovery import FetchedSource
from backend.app.research.earnings_sources import EarningsAcquisition
from backend.app.research.interim_events import collect_interim_events, ensure_interim_events
from backend.app.research.investment_process import ProcessPaused
from backend.app.schemas import RunCreate


@pytest.fixture
def repo(tmp_path):
    return Repository(config=Settings(data_dir=tmp_path, project_root=Path(__file__).resolve().parents[2], enable_market_connectors=False))


def company():
    return {"ticker": "ACME", "name": "Acme Incorporated", "cik": "0000000123", "website": "https://acme.example", "investor_website": "https://investor.acme.example",
            "filings": [{"form": "10-Q", "period_end": "2026-06-30", "filed_at": "2026-08-01", "accession": "000123-26-0001", "primary_document": "quarter.htm"},
                        {"form": "8-K", "period_end": "2026-09-03", "filed_at": "2026-09-04", "accession": "000123-26-0002", "primary_document": "event.htm", "items": "5.02"}]}


def release(url="https://investor.acme.example/leadership", published="2026-09-02", quote="Acme appointed a new chief executive following the retirement of its previous leader."):
    return {"url": url, "title": "Acme leadership update", "published_at": published, "event_date": "2026-09-01", "kind": "press_release", "quote": quote}


def fixture_acquisition(repo, candidates=None, *, fail_search=False):
    sources = {
        "https://investor.acme.example/leadership": "September 2, 2026\nAcme appointed a new chief executive following the retirement of its previous leader. The appointment became effective September 1, 2026. The company will provide its next business update at its investor meeting.",
        "https://www.sec.gov/Archives/edgar/data/123/000123260002/event.htm": "Acme Incorporated Form 8-K. September 3, 2026. Item 5.02: The chief financial officer resigned to pursue another opportunity. The board appointed an interim financial officer. This announcement is unrelated to quarterly earnings.",
    }
    def fetch(url, **kwargs):
        return FetchedSource(url, url, sources.get(url, "unavailable"), "Acme announcement", utc_now())
    def discover(stage, prompt, schema):
        assert stage == "interim_events"
        assert "not only the latest earnings" in prompt
        if fail_search:
            raise ValueError("Issuer page unavailable")
        return {"events": candidates if candidates is not None else [release()], "coverage_gap": ""}
    acquisition = EarningsAcquisition(repo, None, repo.config, namespace="real", fetcher=fetch, discoverer=discover)
    return acquisition


def test_company_releases_and_non_earnings_sec_events_are_bound_to_window(repo):
    acquisition = fixture_acquisition(repo)
    # The collector's archive defer only matters for amendment versions; this
    # fixture uses fresh pages and no parent job or model execution.
    result = asyncio.run(collect_interim_events(acquisition, company(), cutoff="2026-09-26", run_id="fixture"))
    assert result["window_start"] == "2026-06-30"
    assert result["baseline_filing"]["filed_at"] == "2026-08-01"
    assert result["cutoff"] == "2026-09-26"
    assert len(result["events"]) == 2
    sec = next(item for item in result["events"] if item["kind"] == "current_report")
    assert sec["published_at"] == "2026-09-04" and sec["event_date"] == "2026-09-03"
    assert sec["sec_items"] == "5.02"
    issuer = next(item for item in result["events"] if item["kind"] == "press_release")
    assert issuer["event_date"] == "2026-09-01" and issuer["published_at"] == "2026-09-02"
    sources = {source["id"]: source for source in repo.source_packet("real", result["source_ids"])}
    assert issuer["quote"] in sources[issuer["source_id"]]["content"]
    assert issuer["content_hash"] == sources[issuer["source_id"]]["content_hash"]


@pytest.mark.parametrize("candidate,reason", [
    (release(published="2026-10-01"), "outside"),
    (release(url="https://foreign.example/update"), "verified issuer"),
    (release(published="2026-09-20"), "Publication date"),
    (release(quote="An invented announcement that does not exist"), "excerpt"),
])
def test_invalid_candidate_is_gap_not_evidence(repo, candidate, reason):
    result = asyncio.run(collect_interim_events(fixture_acquisition(repo, [candidate]), company(), cutoff="2026-09-26", run_id="fixture"))
    assert len(result["events"]) == 1
    assert reason in result["checks"][0]["reason"]
    assert result["status"] == "partial" and result["gaps"]


def test_failed_search_retains_sec_events_and_explicit_coverage_gap(repo):
    result = asyncio.run(collect_interim_events(fixture_acquisition(repo, fail_search=True), company(), cutoff="2026-09-26", run_id="fixture"))
    assert len(result["events"]) == 1
    assert result["discovery_status"] == "unavailable"
    assert "discovery was unavailable" in result["gaps"][0]


def test_empty_search_never_asserts_no_events(repo):
    issuer = company() | {"filings": []}
    result = asyncio.run(collect_interim_events(fixture_acquisition(repo, []), issuer, cutoff="2026-09-26", run_id="fixture"))
    assert result["status"] == "partial" and result["source_ids"] == []
    assert "does not establish" in result["gaps"][-1]
    assert "180 days" in result["gaps"][0]


def test_saved_receipt_reuses_evidence_without_new_search_and_obeys_pause(repo):
    result, _ = repo.create_run(RunCreate(question="Research ACME", ticker="ACME", idempotency_key="interim-case"), [("A03", "fundamental_review", "Review", [])])
    run_id, task_id = result["run_id"], result["tasks"][0]["id"]
    acquisition = fixture_acquisition(repo)
    calls = []
    async def resolve(ticker):
        calls.append(ticker)
        return company()
    acquisition.resolve = resolve
    service = SimpleNamespace(repo=repo, config=repo.config, registry=None, acquisition_factory=lambda namespace: acquisition)
    before = dict(repo.run_record(run_id))
    first = asyncio.run(ensure_interim_events(service, run_id, ["ACME"], task_id=task_id))
    second = asyncio.run(ensure_interim_events(service, run_id, ["ACME"], task_id=task_id))
    assert first == second and len(first) == 2 and calls == ["ACME"]
    snapshot = json_loads(repo.run_record(run_id)["input_snapshot_json"], {})
    assert snapshot["investment_process"]["interim_events"][0]["source_ids"] == first
    assert all(sid in snapshot["source_ids"] for sid in first)
    assert repo.run_record(run_id)["request"] == before["request"]
    repo.control("firm", None, "pause")
    with pytest.raises(ProcessPaused):
        asyncio.run(ensure_interim_events(service, run_id, ["ACME"], task_id=task_id))
    assert calls == ["ACME"]


def test_sec_refresh_retains_same_case_verified_ir_domain_and_rechecks_proof_on_replay(repo):
    from backend.app.research.workflows import WorkflowStore
    from dataclasses import replace
    root_url = "https://acme.example/leadership"
    ir_url = "https://investor.other-brand.example/home"
    event_url = "https://investor.other-brand.example/update"
    quote = "Acme appointed a new chief executive following the retirement of its previous leader."
    pages = {
        root_url: FetchedSource(root_url, root_url, "Acme Incorporated leadership and Investor Relations.", "Acme", utc_now(), links=(ir_url,)),
        ir_url: FetchedSource(ir_url, ir_url, "Acme Incorporated Investor Relations. Financials.", "Acme", utc_now()),
        event_url: FetchedSource(event_url, event_url, "September 2, 2026\n" + quote + " The appointment became effective September 1, 2026. The company will provide more information at its next investor meeting.", "Acme update", utc_now()),
    }
    calls = []
    def fetch(url, **kwargs):
        return pages.get(url, FetchedSource(url, url, "", "", utc_now(), "unavailable"))
    def discover(stage, prompt, schema):
        calls.append(stage)
        assert stage == "interim_events" and ir_url in prompt
        return {"events": [release(url=event_url, quote=quote)], "coverage_gap": ""}
    acquisition = EarningsAcquisition(repo, None, repo.config, namespace="real", fetcher=fetch, discoverer=discover)
    fresh_company = company() | {"investor_website": ""}
    proof = asyncio.run(acquisition._verify_issuer_domain_candidate(fresh_company, event_url, root_url))
    assert proof
    store = WorkflowStore(repo)
    workflow = store.create("ACME")
    store.finish(workflow, "partial", {"company": fresh_company | {"filings": []}, "event": {"issuer_domain_verification": proof}})
    result, _ = repo.create_run(RunCreate(question="Research ACME", ticker="ACME", idempotency_key="interim-domain-case"), [("A03", "fundamental_review", "Review", [])])
    run_id, task_id = result["run_id"], result["tasks"][0]["id"]
    with repo.db.transaction(immediate=True) as conn:
        snapshot = json_loads(conn.execute("SELECT input_snapshot_json FROM runs WHERE id=?", (run_id,)).fetchone()[0], {})
        snapshot["investment_process"] = {"earnings": [{"ticker": "ACME", "workflow_id": workflow}]}
        conn.execute("UPDATE runs SET input_snapshot_json=? WHERE id=?", (json.dumps(snapshot), run_id))
    async def resolve(ticker):
        return fresh_company
    acquisition.resolve = resolve
    service = SimpleNamespace(repo=repo, config=repo.config, registry=None, acquisition_factory=lambda namespace: acquisition)
    first = asyncio.run(ensure_interim_events(service, run_id, ["ACME"], task_id=task_id))
    assert len(first) == 1
    snapshot = json_loads(repo.run_record(run_id)["input_snapshot_json"], {})
    receipt = snapshot["investment_process"]["interim_events"][0]
    assert receipt["issuer_verification"]["investor_website"] == ir_url
    assert receipt["baseline_filing"]["filed_at"] == "2026-08-01"
    assert receipt["issuer_domain_verification"] == proof
    assert asyncio.run(ensure_interim_events(service, run_id, ["ACME"], task_id=task_id)) == first
    assert calls == ["interim_events"]
    with repo.db.transaction(immediate=True) as conn:
        conn.execute("UPDATE sources SET original_content=original_content || ' changed' WHERE id=?", (proof["sources"][0]["source_id"],))
    with pytest.raises(ValueError, match="issuer identity changed"):
        asyncio.run(ensure_interim_events(service, run_id, ["ACME"], task_id=task_id))
