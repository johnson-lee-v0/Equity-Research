"""Issuer authority requires a saved acquisition proof, not a URL-shaped hint."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from backend.app.config import Settings
from backend.app.memory.repository import Repository
from backend.app.research.earnings_primary_policy import verified_earnings_release_policy
from backend.app.research.workflows import WorkflowStore
from backend.app.schemas import ImportRequest


RELEASE = """ACME Corporation Reports Fourth Quarter and Fiscal Year 2026 Operating Results
September 24, 2026
ACME Corporation (NASDAQ: ACME) today reported net income and diluted earnings
per share for the fourth quarter and fiscal year ended August 30, 2026.
The consolidated statements of income are prepared in accordance with U.S. GAAP.
Net sales increased during the reported year. Diluted earnings per share were
20.76 USD per share for the fiscal year and 6.75 USD per share for the quarter.
"""
URL = "https://investor.acme.com/news/fy2026-results"


@pytest.fixture
def receipt(tmp_path):
    repo = Repository(config=Settings(project_root=Path(__file__).resolve().parents[2], data_dir=tmp_path,
                                     enable_market_connectors=False, enable_reddit_intake=False))
    sid = repo.import_evidence(ImportRequest(namespace="real", kind="evidence", title="ACME earnings release", content=RELEASE,
        source_url=URL, idempotency_key="primary-release"))["source_id"]
    workflow = WorkflowStore(repo).create("ACME")
    document = {"source_id": sid, "status": "available", "kind": "release", "content_hash": hashlib.sha256(RELEASE.encode()).hexdigest(),
                "url": URL, "period_end": "2026-08-30"}
    package = {"company": {"ticker": "ACME", "tickers": ["ACME"], "name": "ACME Corporation", "cik": "0000001234",
        "submissions_url": "https://data.sec.gov/submissions/CIK0000001234.json", "verification_hash": "a" * 64,
        "verified_at": "2026-09-25T00:00:00Z", "resolution": "SEC ticker directory"},
        "event": {"verification": "primary_release", "release_source_id": sid, "release_url": URL, "release_document": copy.deepcopy(document),
                  "period_end": "2026-08-30", "earnings_date": "2026-09-24", "located_at": "2026-09-25T00:00:00Z", "fiscal_period": "Q4 FY2026", "expected_form": "10-K"},
        "documents": {"release": document}, "source_ids": [sid]}
    with repo.db.transaction(immediate=True) as conn:
        conn.execute("UPDATE research_workflow_runs SET status='completed',result_json=? WHERE id=?", (json.dumps(package), workflow))
        conn.execute("UPDATE research_workflow_steps SET status='completed' WHERE run_id=?", (workflow,))
    return repo, sid, workflow, package


def policy(repo, sid):
    with repo.db.operation() as conn:
        row = conn.execute("SELECT s.*,1 AS version FROM sources s WHERE id=?", (sid,)).fetchone()
        return verified_earnings_release_policy(conn, row)


def save_package(repo, workflow, package):
    with repo.db.transaction(immediate=True) as conn:
        conn.execute("UPDATE research_workflow_runs SET result_json=? WHERE id=?", (json.dumps(package), workflow))


def test_exact_verified_issuer_release_is_primary_in_source_and_memory_projection(receipt):
    repo, sid, workflow, _ = receipt
    assert policy(repo, sid)["workflow_id"] == workflow
    projected = repo.sources("real", sid)[0]
    assert projected["primary_evidence"] is True
    assert projected["primary_coverage"] == "primary_issuer"
    assert projected["source_policy"]["workflow_id"] == workflow
    assert projected["source_policy"]["content_hash"] == projected["content_hash"]
    assert projected["source_type"] == "document"  # Authority did not rewrite the stored kind.
    with repo.db.operation() as conn:
        item = repo._memory_source_item_conn(conn, "real", sid, "reuse", "Verified event source.", as_of="2026-09-26T00:00:00Z")
    assert item["primary_evidence"] is True and item["primary_coverage"] == "primary_issuer"


def test_official_looking_domain_or_claimed_source_type_is_not_sufficient(receipt):
    repo, sid, workflow, _ = receipt
    with repo.db.transaction(immediate=True) as conn:
        conn.execute("UPDATE research_workflow_runs SET result_json='{}' WHERE id=?", (workflow,))
        conn.execute("UPDATE sources SET source_type='issuer_release' WHERE id=?", (sid,))
    assert policy(repo, sid) is None
    assert repo.sources("real", sid)[0]["primary_evidence"] is False


@pytest.mark.parametrize("mutation", [
    lambda package: package["event"].update(verification="unverified"),
    lambda package: package["event"].update(release_source_id="src_other"),
    lambda package: package["event"].update(earnings_date="2026-09-23"),
    lambda package: package["event"].update(period_end="2026-08-31"),
    lambda package: package["event"].update(fiscal_period="Q3 FY2026"),
    lambda package: package["event"].update(expected_form="10-Q"),
    lambda package: package["event"].update(release_url="https://example.com/copied-release"),
    lambda package: package["event"]["release_document"].update(content_hash="0" * 64),
    lambda package: package["documents"]["release"].update(source_id="src_other"),
    lambda package: package["documents"]["release"].update(version=2),
    lambda package: package["company"].update(cik="bad-cik"),
    lambda package: package["company"].update(ticker="OTHER"),
    lambda package: package["company"].update(tickers=["OTHER"]),
    lambda package: package["company"].update(verification_hash=None),
    lambda package: package["company"].update(submissions_url="https://example.com/submissions"),
    lambda package: package.update(source_ids=[]),
])
def test_missing_or_mismatched_acquisition_proof_cannot_promote(receipt, mutation):
    repo, sid, workflow, package = receipt
    mutation(package)
    save_package(repo, workflow, package)
    assert policy(repo, sid) is None
    assert repo.sources("real", sid)[0]["primary_evidence"] is False


@pytest.mark.parametrize("target", ["source_content", "source_hash", "archive_content", "archive_hash", "archive_version", "namespace", "step"])
def test_changed_archive_or_namespace_revokes_primary_projection(receipt, target):
    repo, sid, workflow, _ = receipt
    with repo.db.transaction(immediate=True) as conn:
        if target == "source_content": conn.execute("UPDATE sources SET original_content=original_content || ' edited' WHERE id=?", (sid,))
        elif target == "source_hash": conn.execute("UPDATE sources SET content_hash=? WHERE id=?", ("b" * 64, sid))
        elif target == "archive_content": conn.execute("UPDATE source_versions SET content=content || ' edited' WHERE source_id=?", (sid,))
        elif target == "archive_hash": conn.execute("UPDATE source_versions SET content_hash=? WHERE source_id=?", ("b" * 64, sid))
        elif target == "archive_version": conn.execute("UPDATE source_versions SET version_no=2 WHERE source_id=?", (sid,))
        elif target == "namespace": conn.execute("UPDATE research_workflow_runs SET namespace='demo' WHERE id=?", (workflow,))
        else: conn.execute("UPDATE research_workflow_steps SET status='failed' WHERE run_id=? AND agent_id='locate'", (workflow,))
    assert policy(repo, sid) is None


def test_superseded_release_cannot_keep_primary_projection(receipt):
    repo, sid, _, _ = receipt
    repo.import_evidence(ImportRequest(namespace="real", kind="evidence", title="Corrected release", source_url=URL,
        content=RELEASE + "\nAmended earnings data.", supersedes_id=sid, idempotency_key="corrected-release"))
    assert policy(repo, sid) is None


def test_body_must_identify_issuer_even_when_document_title_claims_it(receipt):
    repo, sid, workflow, package = receipt
    content = RELEASE.replace("ACME", "OTHER")
    changed_hash = hashlib.sha256(content.encode()).hexdigest()
    with repo.db.transaction(immediate=True) as conn:
        conn.execute("UPDATE sources SET original_content=?,content_hash=? WHERE id=?", (content, changed_hash, sid))
        conn.execute("UPDATE source_versions SET content=?,content_hash=? WHERE source_id=?", (content, changed_hash, sid))
    for document in (package["event"]["release_document"], package["documents"]["release"]): document["content_hash"] = changed_hash
    save_package(repo, workflow, package)
    assert policy(repo, sid) is None
