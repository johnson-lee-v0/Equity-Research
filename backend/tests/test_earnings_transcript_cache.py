"""Prior URLs are bounded source-bound candidates, never cached acceptance."""
import hashlib

import pytest

from backend.app.config import Settings
from backend.app.db import utc_now
from backend.app.memory.repository import Repository
from backend.app.research.discovery import FetchedSource
from backend.app.research.earnings_transcript_cache import prior_transcript_candidates
from backend.app.research.source_archive import archive_public_observation, archive_public_page
from backend.app.research.workflows import WorkflowStore

COMPANY = {"ticker": "EXM", "cik": "0000001234"}
EVENT = {"fiscal_period": "Q4 FY2026", "period_end": "2026-05-31", "earnings_date": "2026-06-30", "verification": "primary_release"}


@pytest.fixture
def repo(tmp_path):
    return Repository(config=Settings(data_dir=tmp_path))


def checkpoint(repo, *, url="https://publisher.example/exm/call", namespace="real", status="cancelled", company=None, event=None, document=None):
    company, event = company or dict(COMPANY), event or dict(EVENT)
    content = "Previously fetched transcript source at " + url
    page = FetchedSource(url, url, content, "Saved earnings call", utc_now())
    saved = archive_public_observation(repo, page, namespace=namespace, scope="test-candidate")
    doc = {"status": "available", "kind": "transcript", "url": url, "source_id": saved["source_id"],
        "content_hash": hashlib.sha256(content.encode()).hexdigest(), "period_end": event["period_end"], "earnings_date": event["earnings_date"]} | (document or {})
    store = WorkflowStore(repo)
    wid = store.create(company["ticker"], namespace=namespace)
    store.step(wid, "resolve", "completed", output=company)
    store.step(wid, "locate", "completed", output=event)
    store.step(wid, "acquire", "completed", output={"documents": {"transcript": doc}})
    store.finish(wid, status, {})
    return wid, doc, page


@pytest.mark.parametrize("status", ["completed", "partial", "cancelled"])
def test_terminal_workflow_can_supply_only_a_url_without_mutating_history(repo, status):
    wid, doc, _ = checkpoint(repo, status=status)
    before = WorkflowStore(repo).get(wid)
    assert prior_transcript_candidates(repo, "real", COMPANY, EVENT) == [doc["url"]]
    assert WorkflowStore(repo).get(wid) == before
    # This deliberately is not readable call text. The helper returns only a
    # candidate; current fetch/event/speaker checks remain mandatory.


@pytest.mark.parametrize("damage", ["cik", "ticker", "fiscal_period", "period_end", "earnings_date", "unverified", "namespace", "resolve_status", "acquire_status", "doc_date", "doc_hash", "doc_url", "http", "credential_url"])
def test_different_issuer_event_namespace_or_unbound_checkpoint_is_not_a_candidate(repo, damage):
    company, event, document, namespace = dict(COMPANY), dict(EVENT), {}, "real"
    if damage == "cik": company["cik"] = "9999"
    if damage == "ticker": company["ticker"] = "OTHER"
    if damage == "fiscal_period": event["fiscal_period"] = "Q3 FY2026"
    if damage == "period_end": event["period_end"] = "2026-02-28"
    if damage == "earnings_date": event["earnings_date"] = "2026-07-01"
    if damage == "unverified": event["verification"] = "discovered"
    if damage == "namespace": namespace = "demo"
    if damage == "doc_date": document["earnings_date"] = "2026-07-01"
    if damage == "doc_hash": document["content_hash"] = "wrong"
    if damage == "doc_url": document["url"] = "https://another.example/call"
    url = "http://publisher.example/call" if damage == "http" else "https://name:secret@publisher.example/call" if damage == "credential_url" else "https://publisher.example/call"
    wid, _, _ = checkpoint(repo, url=url, namespace=namespace, company=company, event=event, document=document)
    if damage in {"resolve_status", "acquire_status"}:
        with repo.db.transaction(immediate=True) as conn:
            conn.execute("UPDATE research_workflow_steps SET status='failed' WHERE run_id=? AND agent_id=?", (wid, damage.removesuffix("_status")))
    assert prior_transcript_candidates(repo, "real", COMPANY, EVENT) == []


@pytest.mark.parametrize("damage", ["source_text", "source_hash", "source_url", "source_namespace", "version_text", "version_hash", "no_version", "superseded"])
def test_archive_and_latest_version_must_be_intact_and_unsuperseded(repo, damage):
    _, doc, page = checkpoint(repo)
    sid = doc["source_id"]
    if damage == "superseded":
        changed = FetchedSource(page.final_url, page.final_url, page.content + " changed", page.title, utc_now())
        assert archive_public_page(repo, changed, namespace="real", scope="test-amendment")["source_id"]
    else:
        with repo.db.transaction(immediate=True) as conn:
            if damage == "source_text": conn.execute("UPDATE sources SET original_content='changed' WHERE id=?", (sid,))
            if damage == "source_hash": conn.execute("UPDATE sources SET content_hash='changed' WHERE id=?", (sid,))
            if damage == "source_url": conn.execute("UPDATE sources SET url='https://changed.example/call' WHERE id=?", (sid,))
            if damage == "source_namespace": conn.execute("UPDATE sources SET namespace='demo' WHERE id=?", (sid,))
            if damage == "version_text": conn.execute("UPDATE source_versions SET content='changed' WHERE source_id=?", (sid,))
            if damage == "version_hash": conn.execute("UPDATE source_versions SET content_hash='changed' WHERE source_id=?", (sid,))
            if damage == "no_version": conn.execute("DELETE FROM source_versions WHERE source_id=?", (sid,))
    assert prior_transcript_candidates(repo, "real", COMPANY, EVENT) == []


def test_candidates_dedupe_and_stop_at_four_recent_observed_urls(repo):
    urls = [f"https://publisher.example/call-{i}" for i in range(6)]
    for url in urls:
        checkpoint(repo, url=url)
    checkpoint(repo, url=urls[-1])
    assert prior_transcript_candidates(repo, "real", COMPANY, EVENT) == list(reversed(urls))[:4]


def test_lookup_never_scans_past_ten_recent_matching_workflows(repo):
    checkpoint(repo)
    for i in range(10):
        checkpoint(repo, url=f"https://publisher.example/invalid-{i}", document={"status": "unavailable"})
    assert prior_transcript_candidates(repo, "real", COMPANY, EVENT) == []


def test_invalid_current_event_cannot_reuse_prior_candidates(repo):
    checkpoint(repo)
    assert prior_transcript_candidates(repo, "real", COMPANY, EVENT | {"verification": "unverified"}) == []
    assert prior_transcript_candidates(repo, "real", COMPANY | {"cik": "missing"}, EVENT) == []
    assert prior_transcript_candidates(repo, "real", COMPANY, EVENT | {"period_end": "not-a-date"}) == []


@pytest.mark.parametrize("label,matched", [("Q4 FY2026 / FY2026", True), ("Fourth quarter fiscal 2026", True), ("Q4 FY2026 / FY2025", False), ("Q4 FY2026 / Q3 FY2026", False), ("FY2026", False)])
def test_fiscal_label_cosmetics_do_not_override_conflicting_year_or_quarter(repo, label, matched):
    _, doc, _ = checkpoint(repo, event=EVENT | {"fiscal_period": label})
    assert prior_transcript_candidates(repo, "real", COMPANY, EVENT) == ([doc["url"]] if matched else [])
