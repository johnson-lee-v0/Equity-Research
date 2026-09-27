"""Shared Markdown retrieval must remain a projection, not a source bypass."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.app.config import Settings
from backend.app.memory.repository import Repository
from backend.app.memory.shared import SharedMemoryService
from backend.app.schemas import AgentOutputPayload, ImportRequest, ModelConfig, RunCreate

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def repo(tmp_path: Path) -> Repository:
    return Repository(config=Settings(data_dir=tmp_path, project_root=ROOT))


def setup_company(repo: Repository, ticker: str = "COST", namespace: str = "real") -> dict:
    imported = repo.import_evidence(ImportRequest(namespace=namespace, kind="evidence", title=f"{ticker} earnings release",
        content=f"{ticker} membership renewal rate was 92%.\nRevenue grew 5%.", source_url=f"https://issuer.example/{ticker}",
        publication_at="2026-09-01", observed_at="2026-09-01", idempotency_key=f"source-{ticker}-{namespace}"))
    ref = imported["source_id"]
    created, _ = repo.create_run(RunCreate(namespace=namespace, ticker=ticker, question=f"Review {ticker} renewal growth and valuation",
        horizon="12m", source_ids=[ref], idempotency_key=f"run-{ticker}-{namespace}"),
        [("A03", "fundamental_review", "Review renewal trends", [])], allow_semantic_reuse=False)
    task_id = created["tasks"][0]["id"]
    config = ModelConfig(provider="codex", model="gpt-6-luna", reasoning_effort="medium")
    source = repo.sources(namespace, ref)[0]
    attempt = repo.create_attempt(task_id, config, {ref: {"version": source["version"], "hash": source["content_hash"]}})
    output = repo.commit_output(task_id, attempt["attempt_id"], AgentOutputPayload(status="completed", title=f"{ticker} renewal thesis",
        summary="Renewal trends may support recurring income; this is an interpretation.", analysis="Compare more periods before deciding.", source_refs=[ref]), namespace, config)
    with repo.db.transaction(immediate=True) as conn:
        conn.execute("UPDATE runs SET as_of='2099-01-01T00:00:00Z' WHERE id=?", (created["run_id"],))
        conn.execute("INSERT INTO fact_claims(id,namespace,subject,predicate,value_json,unit,currency,period_start,period_end,source_id,locator,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"fact-{ticker}-{namespace}", namespace, f"{ticker} renewal rate", "reported", '92', "%", None, None, "2026-08-31", ref, "L1", "validated", "2026-09-02T00:00:00Z"))
        conn.execute("INSERT INTO research_gaps(id,namespace,root_run_id,origin_run_id,gap_key,normalized_gap,description,assigned_agent_id,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (f"gap-{ticker}-{namespace}", namespace, created["run_id"], created["run_id"], "renewal-history", "renewal-history", "Find renewal rates for earlier quarters", "A01", "open", "2026-09-02T00:00:00Z", "2026-09-02T00:00:00Z"))
    return {"source": ref, "task": task_id, "run": created["run_id"], "output": output["id"]}


def test_actual_obsidian_files_have_provenance_and_real_graph_links(repo: Repository) -> None:
    fixture = setup_company(repo)
    service = SharedMemoryService(repo)
    summary = service.sync()
    assert summary["written"] == 5
    assert (service.vault_path / ".obsidian/app.json").is_file()
    graph = service.graph()
    assert graph["tickers"] == ["COST"]
    assert set(graph["kinds"]) == {"company", "source", "fact", "opinion", "gap"}
    assert len(graph["edges"]) >= 6
    fact = next(note for note in graph["nodes"] if note["kind"] == "fact")
    detail = service.note(fact["id"])
    assert detail["frontmatter"]["source_refs"] == [fixture["source"]]
    assert detail["frontmatter"]["period"] == "2026-08-31"
    assert detail["frontmatter"]["locator"] == "L1"
    assert detail["frontmatter"]["source_versions"][0]["content_hash"]
    assert "92 %" in detail["markdown"]
    assert service.note(fact["id"], namespace="demo") is None


def test_sync_is_idempotent_and_keeps_prior_generated_revision(repo: Repository) -> None:
    setup_company(repo)
    service = SharedMemoryService(repo)
    first = service.sync()
    mtimes = {p: p.stat().st_mtime_ns for p in service.vault_path.rglob("*.md")}
    second = service.sync()
    assert second["written"] == 0
    assert second["unchanged"] == first["written"]
    assert mtimes == {p: p.stat().st_mtime_ns for p in service.vault_path.rglob("*.md")}
    with repo.db.transaction(immediate=True) as conn:
        conn.execute("UPDATE research_gaps SET status='terminal',terminal_reason='no_new_evidence',updated_at='2026-09-03T00:00:00Z'")
    service.sync()
    assert list((service.vault_path / ".road2m/history").rglob("*.md"))
    gap = next(node for node in service.graph()["nodes"] if node["kind"] == "gap")
    assert "remains unresolved" in service.note(gap["id"])["markdown"]


def test_generated_edits_are_preserved_as_opinions_never_verified_claims(repo: Repository) -> None:
    fixture = setup_company(repo)
    service = SharedMemoryService(repo)
    service.sync()
    fact = next(node for node in service.graph()["nodes"] if node["kind"] == "fact")
    path = service.vault_path / fact["path"]
    altered = path.read_text().replace("92 %", "999 %")
    path.write_text(altered)
    graph = service.graph()
    assert all(node["id"] != fact["id"] for node in graph["nodes"])
    edited = next(node for node in graph["nodes"] if node["kind"] == "user_note")
    assert edited["status"] == "opinion"
    assert edited["source_refs"] == []
    summary = service.sync()
    assert summary["preserved_edits"] == 1
    assert path.read_text() == altered
    detail = service.note(fact["id"])
    assert detail["path"] != fact["path"]
    assert "92 %" in detail["markdown"]
    assert "999 %" not in detail["markdown"]
    company = next(node for node in service.graph()["nodes"] if node["kind"] == "company")
    assert detail["path"][:-3] in service.note(company["id"])["markdown"]
    assert service.sync()["written"] == 0
    user_link = service.vault_path / "User Notes/link-to-my-edit.md"
    user_link.write_text(f"---\nticker: COST\nnamespace: real\ntitle: Follow my edited note\n---\n[[{fact['path'][:-3]}|My original edit]]\n")
    link_node = next(node for node in service.graph()["nodes"] if node["title"] == "Follow my edited note")
    assert service.note(link_node["id"])["links"][0]["target"] == edited["id"]
    packet = service.retrieve(fixture["task"])
    assert all("999 %" not in item["excerpt"] for item in packet["items"] if item["kind"] == "fact")


def test_role_retrieval_boundaries_budget_and_discovery_privacy(repo: Repository) -> None:
    cost = setup_company(repo)
    setup_company(repo, "NKE")
    setup_company(repo, "COST", "demo")
    service = SharedMemoryService(repo)
    service.sync()
    (service.vault_path / "User Notes/thesis.md").write_text("---\nnamespace: real\nticker: COST\ntitle: Private renewal thesis\nkind: fact\nstatus: validated\n---\nMy private opinion about renewal rates. [[COST]]\n")
    graph = service.graph(ticker="COST", kind="user_note")
    assert len(graph["nodes"]) == 1
    note = graph["nodes"][0]
    assert note["status"] == "opinion"
    assert service.note(note["id"])["links"]
    packet = repo.prepare_shared_memory(cost["task"])
    assert all(item["ticker"] == "COST" for item in packet["items"])
    assert all("demo" not in ref for item in packet["items"] for ref in item["source_refs"])
    assert any(item["kind"] == "user_note" and item["use"] == "historical_opinion" for item in packet["items"])
    assert any(item["kind"] == "fact" and item["use"] == "attached_evidence_pointer" for item in packet["items"])
    assert len(json.dumps(packet, ensure_ascii=False)) <= packet["char_budget"]
    small = service.retrieve(cost["task"], max_chars=1500)
    assert len(json.dumps(small, ensure_ascii=False)) <= 1500
    with repo.db.transaction(immediate=True) as conn:
        conn.execute("UPDATE tasks SET agent_id='A01' WHERE id=?", (cost["task"],))
    assert all(item["kind"] not in {"user_note", "opinion", "earnings"} for item in service.retrieve(cost["task"])["items"])


def test_as_of_and_source_amendment_remove_ineligible_context(repo: Repository) -> None:
    fixture = setup_company(repo)
    service = SharedMemoryService(repo)
    assert any(item["kind"] == "fact" for item in service.retrieve(fixture["task"])["items"])
    with repo.db.transaction(immediate=True) as conn:
        conn.execute("UPDATE runs SET as_of='2020-01-01T00:00:00Z' WHERE id=?", (fixture["run"],))
    assert service.retrieve(fixture["task"])["items"] == []
    with repo.db.transaction(immediate=True) as conn:
        conn.execute("UPDATE runs SET as_of='2099-01-01T00:00:00Z' WHERE id=?", (fixture["run"],))
    repo.import_evidence(ImportRequest(namespace="real", kind="evidence", title="Amended renewal observation", content="Renewal rate was corrected to 91%.",
        source_url="https://issuer.example/COST/amended", publication_at="2026-09-03", observed_at="2026-09-03", supersedes_id=fixture["source"], idempotency_key="amend-source"))
    assert all(fixture["source"] not in item["source_refs"] for item in service.retrieve(fixture["task"])["items"])


def test_historical_leads_do_not_expand_task_source_packet(repo: Repository) -> None:
    fixture = setup_company(repo)
    created, _ = repo.create_run(RunCreate(namespace="real", ticker="COST", question="Review renewal history", horizon="12m", idempotency_key="new-research"),
        [("A03", "fundamental_review", "Review history", [])], allow_semantic_reuse=False)
    task_id = created["tasks"][0]["id"]
    with repo.db.transaction(immediate=True) as conn:
        conn.execute("UPDATE runs SET as_of='2099-01-01T00:00:00Z' WHERE id=?", (created["run_id"],))
    packet = repo.prepare_shared_memory(task_id)
    assert any(item["kind"] == "fact" and item["use"] == "historical_lead_requires_attachment" for item in packet["items"])
    with repo.db.operation() as conn:
        assert json.loads(conn.execute("SELECT input_refs_json FROM tasks WHERE id=?", (task_id,)).fetchone()[0]) == []
    assert fixture["source"]


def test_attached_pointer_requires_matching_frozen_version_and_hash(repo: Repository) -> None:
    fixture = setup_company(repo)
    service = SharedMemoryService(repo)
    packet = service.retrieve(fixture["task"], frozen_source_versions={fixture["source"]: {"version": 1, "hash": "wrong-hash"}})
    assert not any(item["use"] == "attached_evidence_pointer" for item in packet["items"])
    assert any(item["kind"] == "fact" and item["use"] == "historical_lead_requires_attachment" for item in packet["items"])


def test_ticker_only_refresh_never_relabels_another_companys_shared_source_fact(repo: Repository) -> None:
    cost = setup_company(repo)
    nike = setup_company(repo, "NKE")
    with repo.db.transaction(immediate=True) as conn:
        conn.execute("UPDATE tasks SET input_refs_json=? WHERE id=?", (json.dumps([nike["source"], cost["source"]]), nike["task"]))
        conn.execute("INSERT INTO fact_claims(id,namespace,subject,predicate,value_json,source_id,locator,status,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            ("unattributed", "real", "Renewal rate", "reported", "92", cost["source"], "L1", "validated", "2026-09-02T00:00:00Z"))
    service = SharedMemoryService(repo)
    service.sync()
    packet = service.retrieve(nike["task"])
    assert not any("COST renewal" in item["title"] for item in packet["items"])
    assert all(node["title"] == "NKE renewal rate" for node in service.graph(ticker="NKE", kind="fact")["nodes"])
    assert not any(node["title"] == "Renewal rate" for node in service.graph(kind="fact")["nodes"])


def test_graph_search_facets_and_actual_user_links(repo: Repository) -> None:
    setup_company(repo)
    setup_company(repo, "NKE")
    service = SharedMemoryService(repo)
    service.sync()
    graph = service.graph(ticker="COST", query="renewal", limit=1)
    assert len(graph["nodes"]) == 1
    assert graph["truncated"] is True
    assert graph["tickers"] == ["COST", "NKE"]
    assert graph["total_nodes"] > 1
    assert service.graph(kind="fact")["total_nodes"] == 2


def test_symlinks_and_path_traversal_cannot_import_or_overwrite_external_files(repo: Repository, tmp_path: Path) -> None:
    setup_company(repo)
    service = SharedMemoryService(repo)
    service.sync()
    outside = tmp_path / "outside.md"
    outside.write_text("---\nticker: COST\nnamespace: real\n---\nprivate outside content")
    (service.vault_path / "User Notes/linked.md").symlink_to(outside)
    assert not any("outside content" in node["excerpt"] for node in service.graph()["nodes"])
    with pytest.raises(ValueError):
        service._write("../outside.md", "bad")
    with pytest.raises(ValueError):
        service._write("User Notes/linked.md", "bad")
    assert outside.read_text().endswith("private outside content")
    with pytest.raises(ValueError):
        service.sync(namespace="../../other")


def test_removed_ledger_record_is_not_retrieved_from_old_markdown(repo: Repository) -> None:
    fixture = setup_company(repo)
    service = SharedMemoryService(repo)
    service.sync()
    fact = next(note for note in service.graph()["nodes"] if note["kind"] == "fact")
    with repo.db.transaction(immediate=True) as conn:
        conn.execute("DELETE FROM fact_claims WHERE id='fact-COST-real'")
    service.sync()
    assert service.note(fact["id"]) is None
    assert (service.vault_path / fact["path"]).exists()
    assert not any(item["kind"] == "fact" for item in service.retrieve(fixture["task"])["items"])


@pytest.mark.parametrize("edited", [b"large user edit\n" * 30_000, b"\xff\xfeuser modified bytes"])
def test_oversize_or_unreadable_user_edit_is_never_overwritten(repo: Repository, edited: bytes) -> None:
    setup_company(repo)
    service = SharedMemoryService(repo)
    service.sync()
    fact = next(note for note in service.graph()["nodes"] if note["kind"] == "fact")
    path = service.vault_path / fact["path"]
    path.write_bytes(edited)
    assert service.sync()["preserved_edits"] == 1
    assert path.read_bytes() == edited
    assert service.note(fact["id"])["path"] != fact["path"]
    assert service.sync()["written"] == 0


def test_large_manifest_retains_ownership_and_invalid_manifest_fails_closed(repo: Repository) -> None:
    setup_company(repo)
    service = SharedMemoryService(repo)
    service.sync()
    manifest_path = service.vault_path / ".road2m/real.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["large_extra_index_metadata"] = "x" * 300_000
    manifest_path.write_text(json.dumps(manifest))
    assert service.sync()["written"] == 0
    files_before = {path: path.read_bytes() for path in service.vault_path.rglob("*.md")}
    manifest_path.write_text("not valid JSON")
    with pytest.raises(ValueError, match="index is unreadable"):
        service.sync()
    assert files_before == {path: path.read_bytes() for path in service.vault_path.rglob("*.md")}


def test_generated_collection_does_not_consume_personal_note_scan_budget(repo: Repository, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_company(repo)
    service = SharedMemoryService(repo)
    service.sync()
    (service.vault_path / "User Notes/personal.md").write_text("---\nticker: COST\nnamespace: real\n---\nMy membership thesis")
    monkeypatch.setattr("backend.app.memory.shared._MAX_USER_NOTES", 2)
    assert len(service.graph(kind="user_note")["nodes"]) == 1
