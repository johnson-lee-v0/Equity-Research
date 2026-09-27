"""Optional passes stay scoped, auditable, bounded and outside decision writes."""
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient

from backend.app.api.research_actions import ResearchActionRequest
from backend.app.config import Settings
from backend.app.db import json_dumps, json_loads, utc_now
from backend.app.main import create_app
from backend.app.memory.repository import Repository
from backend.app.orchestration.workflow import Orchestrator
from backend.app.providers.base import ProviderResult
from backend.app.research.discovery import FetchedSource
from backend.app.research.research_actions import ResearchActions, execute_action_task, _validate_bullets, PREFIX
from backend.app.research.investment_process import ProcessPaused
from backend.app.schemas import ImportRequest, RunCreate


@pytest.fixture
def repo(tmp_path):
    return Repository(config=Settings(data_dir=tmp_path, project_root=Path(__file__).resolve().parents[2],
                                      enable_market_connectors=False, enable_reddit_intake=False))


def source(repo, key="source", namespace="real"):
    return repo.import_evidence(ImportRequest(namespace=namespace, kind="evidence", title="ACME renewal rates",
        content="September 1, 2026. ACME reported renewal of 90%, down from 92%. This release contains a public operating update.",
        source_url="https://acme.example/renewals", idempotency_key="source:" + key))["source_id"]


def case(repo, key="root", status="completed", source_ids=None, namespace="real"):
    result, _ = repo.create_run(RunCreate(question="Is ACME attractive over twelve months?", ticker="ACME", horizon="12 months",
        namespace=namespace, idempotency_key="root:" + key, source_ids=source_ids or []), [("A03", "fundamental_review", "Review", [])], allow_semantic_reuse=False)
    with repo.db.transaction(immediate=True) as conn:
        conn.execute("UPDATE runs SET status=? WHERE id=?", (status, result["run_id"]))
        conn.execute("UPDATE tasks SET status='completed' WHERE run_id=?", (result["run_id"],))
    return result["run_id"]


def gap(repo, root, key="g1", *, description="Find the issuer's latest renewal rate", status="open", reason=None):
    with repo.db.transaction(immediate=True) as conn:
        conn.execute("INSERT INTO research_gaps(id,namespace,root_run_id,origin_run_id,gap_key,normalized_gap,description,assigned_agent_id,status,terminal_reason,created_at,updated_at) VALUES(?,'real',?,?,?,?,?,'A02',?,?,?,?)",
                     (key, root, root, key, key, description, status, reason, utc_now(), utc_now()))
    return key


def request(kind="challenge", key="action", **kwargs):
    return ResearchActionRequest(kind=kind, idempotency_key="action:" + key, **kwargs)


def test_create_is_explicit_terminal_scoped_and_idempotent(repo):
    sid = source(repo)
    root = case(repo, source_ids=[sid])
    before = dict(repo.run_record(root))
    service = ResearchActions(repo)
    assert service.list(root, "real")["items"] == []
    action = service.create(root, request())
    assert action["kind"] == "challenge" and action["status"] == "queued"
    assert action["limits"] == {"search_queries": 2, "web_actions": 4, "fetched_pages": 3, "model_calls": 4, "automatic_retries": 0}
    assert [task["kind"] for task in repo.tasks_for_run(action["run_id"])] == [PREFIX + key for key in ("discovery", "bull", "bear", "resolution")]
    assert service.create(root, request())["run_id"] == action["run_id"]
    assert service.create(root, request(key="double-click"))["run_id"] == action["run_id"]
    with repo.db.transaction(immediate=True) as conn:
        conn.execute("UPDATE runs SET status='completed' WHERE id=?", (action["run_id"],))
    assert service.create(root, request(key="double-click"))["run_id"] == action["run_id"]
    assert dict(repo.run_record(root)) == before
    unfinished = case(repo, "unfinished", status="running")
    with pytest.raises(ValueError, match="initial research loop"):
        service.create(unfinished, request(key="too-soon"))
    with pytest.raises(ValueError, match="different research action"):
        service.create(root, request("evidence_retry", source_id=sid))
    with pytest.raises(KeyError):
        service.list(root, "demo")


def test_single_gap_retry_carries_no_other_gap_or_broad_case_prompt(repo):
    root = case(repo, source_ids=[source(repo)])
    first = gap(repo, root)
    second = gap(repo, root, "g2", description="Find an unrelated product margin")
    action = ResearchActions(repo).create(root, request("evidence_retry", gap_id=first))
    snapshot = json_loads(repo.run_record(action["run_id"])["input_snapshot_json"], {})
    assert snapshot["source_ids"] == []
    assert snapshot["research_action"]["scope"]["gap_id"] == first
    assert second not in json_dumps(snapshot["research_action"])
    assert snapshot["research_action"]["initial_case"] == []
    assert len(repo.tasks_for_run(action["run_id"])) == 2
    # Reading/searching one gap never changes the rest of the case ledger.
    assert [item["status"] for item in repo.gaps_for_run(root)] == ["open", "open"]
    with pytest.raises(ValidationError):
        request("evidence_retry", gap_id=first, source_id="also-source")
    with pytest.raises(ValidationError):
        request("evidence_retry")


def test_source_and_gap_ownership_private_inputs_and_active_scopes(repo):
    sid = source(repo)
    other_sid = source(repo, "other", "demo")
    root = case(repo, source_ids=[sid])
    other_root = case(repo, "other-root")
    other_gap = gap(repo, other_root)
    service = ResearchActions(repo)
    for kwargs in ({"source_id": other_sid}, {"gap_id": other_gap}):
        with pytest.raises(KeyError):
            service.create(root, request("evidence_retry", **kwargs))
    private_gap = gap(repo, root, "private", description="Find my portfolio cost basis")
    with pytest.raises(ValueError, match="Private"):
        service.create(root, request("evidence_retry", gap_id=private_gap))
    closed = gap(repo, root, "resolved", status="resolved")
    with pytest.raises(ValueError, match="resolved"):
        service.create(root, request("evidence_retry", gap_id=closed))
    first = service.create(root, request("evidence_retry", source_id=sid))
    normal_gap = gap(repo, root, "normal")
    second = service.create(root, request("evidence_retry", key="second", gap_id=normal_gap))
    assert first["run_id"] != second["run_id"]
    assert not next(item for item in service.list(root, "real")["gaps"] if item["id"] == private_gap)["retry_eligible"]


class Provider:
    def __init__(self, payloads):
        self.payloads, self.calls = list(payloads), []

    async def execute(self, attempt_id, prompt, config, schema, workdir, **kwargs):
        self.calls.append({"prompt": prompt, **kwargs})
        return ProviderResult(self.payloads.pop(0), {"input_tokens": 25, "output_tokens": 10})

    async def cancel(self, attempt_id):
        return {}


class Registry:
    def __init__(self, provider):
        self.provider = provider

    def adapter(self, name):
        return self.provider

    async def preflight(self, *args, **kwargs):
        return {"available": True}

    @asynccontextmanager
    async def generation_slot(self, *args, **kwargs):
        yield


def engine_for(repo, provider):
    return SimpleNamespace(repository=repo, providers=Registry(provider), active={})


def bullet(sid, quote="ACME reported renewal of 90%, down from 92%."):
    return {"claim": "Fewer customers renewed.", "source_ids": [sid], "quote": quote, "evidence_status": "supported"}


def test_retry_executes_only_selected_source_and_saves_receipt_without_case_output(repo):
    sid = source(repo)
    root = case(repo, source_ids=[sid])
    service = ResearchActions(repo)
    action = service.create(root, request("evidence_retry", source_id=sid))
    provider = Provider([{"candidates": [], "coverage_gap": "No additional public source was verified."},
                         {"status": "supported", "summary": "Renewals fell by two percentage points.", "findings": [bullet(sid)], "remaining_gap": ""}])
    engine = engine_for(repo, provider)
    for task in repo.tasks_for_run(action["run_id"]):
        asyncio.run(execute_action_task(engine, action["run_id"], task))
    saved = service.receipt(action["run_id"], "real")
    assert saved["result"]["status"] == "supported"
    assert len(saved["stages"]) == 2
    assert provider.calls[0]["discovery_stage"] is True
    assert provider.calls[0]["discovery_limits"].max_search_queries == 1
    assert "Is ACME attractive" not in provider.calls[0]["prompt"]
    assert "Is ACME attractive" not in provider.calls[1]["prompt"]
    with repo.db.operation() as conn:
        assert conn.execute("SELECT COUNT(*) FROM outputs").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM research_repairs").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 2
    assert repo.run_record(root)["status"] == "completed"


def test_orchestrator_completes_fixed_graph_without_decision_or_repairs(repo):
    root = case(repo)
    action = ResearchActions(repo).create(root, request())
    provider = Provider([{"candidates": [], "coverage_gap": "No source verified"},
                         {"summary": "Unknown", "points": [], "open_questions": ["Need verified facts"]},
                         {"summary": "Unknown", "points": [], "open_questions": ["Need verified facts"]},
                         {"bull_case": [], "bear_case": [], "resolution": "Evidence is insufficient to strengthen or weaken the original case.", "open_questions": ["Need facts"], "what_would_change_mind": ["A dated operating update"]}])
    engine = Orchestrator(repo, Registry(provider), repo.config)
    asyncio.run(engine.run(action["run_id"]))
    assert repo.run_record(action["run_id"])["status"] == "completed"
    assert len(provider.calls) == 4
    assert ResearchActions(repo).receipt(action["run_id"], "real")["result"]["resolution"]
    assert repo.run_record(root)["status"] == "completed"


def test_pause_cancel_and_malformed_citations_fail_without_extra_dispatch(repo):
    sid = source(repo)
    root = case(repo, source_ids=[sid])
    action = ResearchActions(repo).create(root, request("evidence_retry", source_id=sid))
    task = repo.tasks_for_run(action["run_id"])[0]
    provider = Provider([])
    repo.control("firm", None, "pause")
    with pytest.raises(ProcessPaused):
        asyncio.run(execute_action_task(engine_for(repo, provider), action["run_id"], task))
    assert provider.calls == []
    assert repo.run_record(action["run_id"])["status"] == "paused"
    repo.control("run", action["run_id"], "run_once")
    repo.control("run", action["run_id"], "cancel")
    with pytest.raises(ProcessPaused):
        asyncio.run(execute_action_task(engine_for(repo, provider), action["run_id"], task))
    with pytest.raises(ValueError, match="quote"):
        _validate_bullets({"findings": [bullet(sid, "Invented quote")]}, repo.source_packet("real", [sid]))
    with pytest.raises(ValueError, match="outside"):
        _validate_bullets({"findings": [bullet("someone-elses-source")]}, repo.source_packet("real", [sid]))


def test_retrieval_rejects_unverified_quote_and_future_date_and_keeps_retry_scope(repo):
    root = case(repo)
    gid = gap(repo, root)
    action = ResearchActions(repo).create(root, request("evidence_retry", gap_id=gid))
    provider = Provider([{"candidates": [{"url": "https://acme.example/future", "title": "Future", "publication_date": "2099-01-01", "quote": "January 1, 2099. ACME reported renewal of 90%."}], "coverage_gap": ""}])
    page = FetchedSource("https://acme.example/future", "https://acme.example/future", "January 1, 2099. ACME reported renewal of 90%.", "Future", utc_now())
    asyncio.run(execute_action_task(engine_for(repo, provider), action["run_id"], repo.tasks_for_run(action["run_id"])[0], fetcher=lambda *a, **k: page))
    saved = ResearchActions(repo).receipt(action["run_id"], "real")["stages"][0]["result"]
    assert saved["sources"] == [] and "future" in saved["rejected"][0]["reason"]


def test_changed_page_stays_in_optional_pass_without_amending_or_reopening_parent(repo):
    sid = source(repo)
    root = case(repo, source_ids=[sid])
    before = dict(repo.run_record(root))
    action = ResearchActions(repo).create(root, request("evidence_retry", source_id=sid))
    text = "September 2, 2026. ACME now reports renewal of 89%, down from 90%. This is a newly observed public release."
    quote = "ACME now reports renewal of 89%, down from 90%."
    provider = Provider([{"candidates": [{"url": "https://acme.example/renewals", "title": "New update", "publication_date": "2026-09-02", "quote": quote}], "coverage_gap": ""}])
    page = FetchedSource("https://acme.example/renewals", "https://acme.example/renewals", text, "ACME renewals", utc_now())
    asyncio.run(execute_action_task(engine_for(repo, provider), action["run_id"], repo.tasks_for_run(action["run_id"])[0], fetcher=lambda *a, **k: page))
    saved = ResearchActions(repo).receipt(action["run_id"], "real")["stages"][0]["result"]["sources"][0]
    assert saved["source_id"] != sid
    assert dict(repo.run_record(root)) == before
    with repo.db.operation() as conn:
        assert conn.execute("SELECT supersedes_source_id FROM sources WHERE id=?", (saved["source_id"],)).fetchone()[0] is None
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 2


def test_cancel_during_generation_rejects_late_result_and_preserves_usage(repo):
    root = case(repo)
    action = ResearchActions(repo).create(root, request())
    class Cancels(Provider):
        async def execute(self, *args, **kwargs):
            repo.control("run", action["run_id"], "cancel")
            return ProviderResult({"candidates": [], "coverage_gap": "late"}, {"output_tokens": 7})
    with pytest.raises(ProcessPaused):
        asyncio.run(execute_action_task(engine_for(repo, Cancels([])), action["run_id"], repo.tasks_for_run(action["run_id"])[0]))
    assert ResearchActions(repo).receipt(action["run_id"], "real")["stages"] == []
    with repo.db.operation() as conn:
        attempt = conn.execute("SELECT status,usage_json FROM task_attempts").fetchone()
        assert attempt["status"] == "cancelled"
        assert json_loads(attempt["usage_json"], {})["output_tokens"] == 7


def test_invalid_source_quote_fails_one_stage_without_an_automatic_retry(repo):
    sid = source(repo)
    root = case(repo, source_ids=[sid])
    action = ResearchActions(repo).create(root, request("evidence_retry", source_id=sid))
    provider = Provider([{"candidates": [], "coverage_gap": ""},
        {"status": "supported", "summary": "Unsupported", "findings": [bullet(sid, "A quotation absent from the archive")], "remaining_gap": ""}])
    engine = Orchestrator(repo, Registry(provider), repo.config)
    asyncio.run(engine.run(action["run_id"]))
    assert repo.run_record(action["run_id"])["status"] == "failed"
    assert len(provider.calls) == 2
    assert ResearchActions(repo).receipt(action["run_id"], "real")["result"] is None
    assert repo.run_record(root)["status"] == "completed"


def test_api_read_filters_actions_and_guards_namespace_mutation(repo):
    root = case(repo)
    repo.control("firm", None, "pause")
    client = TestClient(create_app(repo.config, repo, Registry(Provider([]))))
    created = client.post(f"/api/runs/{root}/research-actions", json=request().model_dump(), headers={"origin": "http://localhost:8000", "X-Road2M-Client": "local-ui"})
    assert created.status_code == 202, created.text
    child = created.json()["run_id"]
    listed = client.get("/api/runs").json()["items"]
    assert child not in {item["id"] for item in listed}
    assert child in {item["id"] for item in client.get("/api/runs?include_actions=true").json()["items"]}
    assert client.get(f"/api/runs/{root}/research-actions?namespace=demo").status_code == 404
    assert client.post(f"/api/runs/{root}/research-actions", json=request(key="hostile").model_dump(), headers={"origin": "https://hostile.example", "X-Road2M-Client": "local-ui"}).status_code == 403
