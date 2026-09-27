"""The web-capable A01 receives public leads without notebook authority."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from backend.app.config import Settings
from backend.app.memory.repository import Repository
from backend.app.memory.shared import SharedMemoryService
from backend.app.orchestration.workflow import Orchestrator
from backend.app.research.discovery_memory import prepare_discovery_memory, public_lead_url
from backend.app.schemas import RunCreate
from backend.tests.test_discovery_handoff import FakeProvider, FakeRegistry, output_payload
from backend.tests.test_shared_memory_vault import setup_company

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def repo(tmp_path: Path) -> Repository:
    return Repository(config=Settings(data_dir=tmp_path, project_root=ROOT, codex_timeout_seconds=30))


def public_company(repo: Repository, ticker: str = "COST", namespace: str = "real") -> dict:
    fixture = setup_company(repo, ticker, namespace)
    # The fixture represents a retained regulator document. Unknown generic
    # URLs have no public-authority proof and intentionally stay local.
    with repo.db.transaction(immediate=True) as conn:
        conn.execute("UPDATE sources SET url=? WHERE id=?", (f"https://www.sec.gov/Archives/{ticker}.htm", fixture["source"]))
    return fixture


def discovery_task(repo: Repository, *, key: str = "new-discovery") -> tuple[str, str]:
    created, _ = repo.create_run(RunCreate(namespace="real", ticker="COST", question="Review public COST earnings and renewal history",
        horizon="12m", idempotency_key=key), [("A01", "universe_discovery", "Find current issuer materials", [])], allow_semantic_reuse=False)
    with repo.db.transaction(immediate=True) as conn:
        row = conn.execute("SELECT input_snapshot_json FROM runs WHERE id=?", (created["run_id"],)).fetchone()
        snapshot = json.loads(row[0])
        snapshot["routing_plan"] = {"intent": "research", "horizon": "12m", "tickers": ["COST"], "research_queries": ["COST latest earnings and renewal trends"]}
        conn.execute("UPDATE runs SET as_of='2099-01-01T00:00:00Z',input_snapshot_json=? WHERE id=?", (json.dumps(snapshot), created["run_id"]))
    return created["run_id"], created["tasks"][0]["id"]


def test_initial_discovery_reuses_company_sources_and_public_gap_topics_without_private_notes(repo: Repository) -> None:
    old = public_company(repo)
    public_company(repo, "NKE")
    public_company(repo, "COST", "demo")
    service = SharedMemoryService(repo)
    service.sync()
    (service.vault_path / "User Notes/private.md").write_text("---\nnamespace: real\nticker: COST\ntitle: PRIVATE_OPINION_SENTINEL\n---\nMy private bank balance is 123456. [[COST]]\n")
    _, task_id = discovery_task(repo)
    before = repo.task(task_id)["input_refs_json"]
    public, receipt = prepare_discovery_memory(repo, task_id, public_tickers=["COST"], frozen_source_versions={})
    assert public["companies"] == [{"ticker": "COST"}]
    assert public["sources"][0]["url"] == "https://www.sec.gov/Archives/COST.htm"
    assert public["open_topics"][0]["topic"] == "membership growth and renewal trends"
    wire = json.dumps(public)
    for forbidden in ("PRIVATE_OPINION_SENTINEL", "123456", "earlier quarters", "Renewal trends may support", "source_refs", "note_id", "Generated/", "NKE", old["source"]):
        assert forbidden not in wire
    assert public["sources"][0]["use"].startswith("historical URL lead")
    assert receipt["source_versions"][old["source"]]["content_hash"]
    assert receipt["note_ids"]
    assert receipt["citation_authority"] is False
    assert repo.task(task_id)["input_refs_json"] == before == "[]"


def test_superseded_or_future_sources_and_private_or_closed_gaps_do_not_become_public_leads(repo: Repository) -> None:
    old = public_company(repo)
    _, task_id = discovery_task(repo)
    with repo.db.transaction(immediate=True) as conn:
        conn.execute("UPDATE research_gaps SET description='My portfolio account needs COST revenue to fund my balance' WHERE id=?", ("gap-COST-real",))
        conn.execute("UPDATE runs SET as_of='2020-01-01T00:00:00Z' WHERE id=(SELECT run_id FROM tasks WHERE id=?)", (task_id,))
    public, _ = prepare_discovery_memory(repo, task_id, public_tickers=["COST"], frozen_source_versions={})
    assert public["sources"] == public["open_topics"] == []
    with repo.db.transaction(immediate=True) as conn:
        conn.execute("UPDATE runs SET as_of='2099-01-01T00:00:00Z' WHERE id=(SELECT run_id FROM tasks WHERE id=?)", (task_id,))
    public, _ = prepare_discovery_memory(repo, task_id, public_tickers=["COST"], frozen_source_versions={})
    assert public["open_topics"] == []
    # Even if a newer head appears between retrieval and projection, stale
    # versions cannot be passed along as source leads.
    original = repo.prepare_shared_memory
    def stale(*args, **kwargs):
        packet = original(*args, **kwargs)
        with repo.db.transaction(immediate=True) as conn:
            conn.execute("UPDATE sources SET content_hash='changed' WHERE id=?", (old["source"],))
        return packet
    repo.prepare_shared_memory = stale
    public, _ = prepare_discovery_memory(repo, task_id, public_tickers=["COST"], frozen_source_versions={})
    assert public["sources"] == []


def test_continuation_leads_cannot_add_a_different_gap_scope_or_ticker(repo: Repository) -> None:
    public_company(repo)
    _, task_id = discovery_task(repo)
    public, _ = prepare_discovery_memory(repo, task_id, public_tickers=["COST"], frozen_source_versions={}, include_gaps=False)
    assert public["sources"]
    assert public["open_topics"] == []
    unrelated, _ = prepare_discovery_memory(repo, task_id, public_tickers=["NKE"], frozen_source_versions={})
    assert unrelated["companies"] == unrelated["sources"] == unrelated["open_topics"] == []


def test_public_url_leads_never_invent_queryless_documents_or_expose_private_queries() -> None:
    assert public_lead_url("https://investor.example.com/results#section") == "https://investor.example.com/results"
    assert public_lead_url("https://investor.example.com/download?id=quarterly-report") is None
    assert public_lead_url("https://investor.example.com/results?token=SECRET#private") is None
    for value in ("javascript:alert(1)", "http://issuer.example.com", "https://user:password@example.com", "https://127.0.0.1/private", "https://10.1.2.3/results", "https://corp.local/reports", "https://localhost/", "https://issuer.example.com/my%20account/123", "https://issuer.example.com:8443/", "https://issuer.example.com/\nresults"):
        assert public_lead_url(value) is None


def test_workflow_supplies_public_memory_to_initial_a01_and_freezes_local_receipt(repo: Repository, monkeypatch: pytest.MonkeyPatch) -> None:
    old = public_company(repo)
    run_id, task_id = discovery_task(repo)
    # No outbound network: this response emits no new URLs to acquire.
    import backend.app.orchestration.workflow as workflow_module
    monkeypatch.setattr(workflow_module, "fetch_public_pages", lambda *_args, **_kwargs: [])
    provider = FakeProvider(lambda _packet: output_payload(title="Discovery used historical public leads"))
    engine = Orchestrator(repo, FakeRegistry(provider), repo.config)
    asyncio.run(engine._execute_task(run_id, repo.task(task_id)))
    assert len(provider.calls) == 1, repo.run_snapshot(run_id)
    packet = provider.calls[0]["packet"]
    assert provider.calls[0]["discovery_stage"] is True
    assert packet["shared_memory"]["sources"][0]["url"] == "https://www.sec.gov/Archives/COST.htm"
    assert packet["evidence"] == packet["source_ids"] == []
    assert packet["portfolio_snapshot"] == {}
    assert old["source"] not in json.dumps(packet)
    with repo.db.operation() as conn:
        row = conn.execute("SELECT context_json FROM attempt_decision_inputs WHERE attempt_id=?", (provider.calls[0]["attempt_id"],)).fetchone()
        assert json.loads(row[0])["shared_memory"]["source_versions"][old["source"]]
    assert repo.task(task_id)["status"] == "completed"


@pytest.mark.parametrize("source_type,url", [
    ("user_provided", "https://docs.google.com/document/d/SECRET_DOCUMENT_ID/edit"),
    ("document", "https://docs.google.com/document/d/SECRET_DOCUMENT_ID/edit"),
    ("document", "https://personal.example.com/SECRET_DOCUMENT_ID"),
    ("user_provided", "https://www.sec.gov/Archives/COST.htm"),
])
def test_private_or_unproven_source_urls_never_leave_in_discovery_memory(repo: Repository, source_type: str, url: str) -> None:
    old = setup_company(repo)
    with repo.db.transaction(immediate=True) as conn:
        conn.execute("UPDATE sources SET source_type=?,url=? WHERE id=?", (source_type, url, old["source"]))
    _, task_id = discovery_task(repo)
    public, receipt = prepare_discovery_memory(repo, task_id, public_tickers=["COST"], frozen_source_versions={})
    assert public["sources"] == []
    assert "SECRET_DOCUMENT_ID" not in json.dumps(public)
    assert old["source"] not in receipt["source_versions"]
