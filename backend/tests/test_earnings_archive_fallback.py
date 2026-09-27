"""An exhausted supplemental search can leave verified earnings research usable."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.memory.repository import Repository
from backend.app.orchestration.workflow import Orchestrator, build_research_tasks
from backend.app.providers.base import ProviderError
from backend.app.research.decision_questions import FIVE_QUESTION_CONTRACT
from backend.app.research.earnings_fallback import activate_fallback, DISCOVERY_GAP, VALUATION_GAP
from backend.app.research.workflows import WorkflowStore
from backend.app.schemas import ImportRequest, MissingGap, ModelConfig, RunCreate, RoutingPlan
from backend.tests.test_discovery_handoff import FakeProvider, FakeRegistry, output_payload
from backend.tests.test_five_question_commit_path import (
    create_five_question_case, _DeterministicProvider, _DeterministicRegistry,
    _provider_payloads, _routing_payload,
)

ERROR = "Codex exceeded the bounded discovery search-query limit (5) at started"
CONFIG = ModelConfig(provider="codex", model="gpt-5.6-luna", reasoning_effort="max")


def attach_workflow(repo, run_id):
    run = repo.run_record(run_id)
    snapshot = json.loads(run["input_snapshot_json"])
    workflow_id = WorkflowStore(repo).create(run["ticker"])
    package = {"company": {"ticker": run["ticker"], "cik": "0000001234"},
               "source_ids": snapshot["requested_source_ids"], "gaps": ["Latest annual filing is unavailable."]}
    with repo.db.transaction(immediate=True) as conn:
        conn.execute("UPDATE runs SET origin_ref=? WHERE id=?", (f"workflow:{workflow_id}", run_id))
        conn.execute("UPDATE research_workflow_runs SET status='partial',research_run_id=?,result_json=? WHERE id=?", (run_id, json.dumps(package), workflow_id))
        conn.execute("UPDATE research_workflow_steps SET status='completed' WHERE run_id=?", (workflow_id,))
    return workflow_id


@pytest.fixture
def case(tmp_path):
    repo = Repository(config=Settings(project_root=Path(__file__).resolve().parents[2], data_dir=tmp_path,
                                      enable_market_connectors=False, enable_reddit_intake=False))
    source_id = repo.import_evidence(ImportRequest(namespace="real", kind="evidence", title="COST earnings release",
        source_url="https://investor.costco.com/earnings", content="COST archived earnings release, fiscal period ended 2026-08-30.", idempotency_key="archive-source"))["source_id"]
    question = "Assess COST earnings from the archive."
    body = RunCreate(question=question, ticker="COST", namespace="real", source_ids=[source_id],
                     research_contract=FIVE_QUESTION_CONTRACT, idempotency_key="archive-case")
    created, _ = repo.create_run(body, build_research_tasks(question, None, "COST", "real", lean=True))
    run_id = created["run_id"]
    repo.consume_routing_plan(run_id, RoutingPlan(intent="research", horizon="event", tickers=["COST"], selected_analysts=["A03"]))
    with repo.db.transaction(immediate=True) as conn:
        conn.execute("UPDATE tasks SET status='completed' WHERE run_id=? AND agent_id='A00'", (run_id,))
    workflow_id = attach_workflow(repo, run_id)
    task = next(row for row in repo.tasks_for_run(run_id) if row["agent_id"] == "A01")
    attempt_id = repo.create_attempt(task["id"], CONFIG, {})["attempt_id"]
    repo.mark_task_failure(task["id"], attempt_id, "blocked", ERROR)
    repo.set_run_status(run_id, "blocked", error=ERROR)
    return repo, run_id, task["id"], attempt_id, source_id, workflow_id


def test_explicit_recovery_is_durable_honest_idempotent_and_keeps_firm_paused(case):
    repo, run_id, task_id, attempt_id, source_id, _ = case
    repo.control("firm", None, "pause")
    assert repo.control("run", run_id, "use_archived_evidence") == 1
    assert repo.control("run", run_id, "use_archived_evidence") == 1
    restarted = Repository(config=repo.config)
    snapshot = restarted.run_snapshot(run_id)
    receipt = snapshot["earnings_archive_fallbacks"][0]
    assert receipt["status"] == "partial" and receipt["reason"] == ERROR
    assert receipt["attempt_id"] == attempt_id and source_id in receipt["source_versions"]
    assert DISCOVERY_GAP in receipt["gaps"] and VALUATION_GAP in receipt["gaps"]
    assert restarted.task(task_id)["status"] == "blocked" and restarted.task(task_id)["output_id"] is None
    assert restarted.firm_paused() and restarted.firm_dispatch_paused(run_id)
    a03 = next(row for row in restarted.tasks_for_run(run_id) if row["agent_id"] == "A03")
    assert any(dep["id"] == task_id and dep["status"] == "blocked" and dep["satisfied_by_archived_evidence"] for dep in restarted.task_dependency_states(a03["id"]))
    with restarted.db.operation() as conn:
        assert conn.execute("SELECT status,error FROM task_attempts WHERE id=?", (attempt_id,)).fetchone()["error"] == ERROR
        assert conn.execute("SELECT count(*) FROM events WHERE run_id=? AND type='earnings_archive_fallback'", (run_id,)).fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM outputs WHERE task_id=?", (task_id,)).fetchone()[0] == 0


@pytest.mark.parametrize("mutation", ["link", "ticker", "namespace", "hash", "empty", "no_url", "source_set", "failure", "cancel", "continuation"])
def test_invalid_archive_or_failure_cannot_bypass_discovery(case, mutation):
    repo, run_id, task_id, attempt_id, source_id, workflow_id = case
    with repo.db.transaction(immediate=True) as conn:
        if mutation == "link": conn.execute("UPDATE research_workflow_runs SET research_run_id=NULL WHERE id=?", (workflow_id,))
        elif mutation == "ticker": conn.execute("UPDATE research_workflow_runs SET ticker='MSFT' WHERE id=?", (workflow_id,))
        elif mutation == "namespace": conn.execute("UPDATE sources SET namespace='demo' WHERE id=?", (source_id,))
        elif mutation == "hash": conn.execute("UPDATE sources SET content_hash='wrong' WHERE id=?", (source_id,))
        elif mutation == "empty": conn.execute("UPDATE sources SET original_content='' WHERE id=?", (source_id,))
        elif mutation == "no_url": conn.execute("UPDATE sources SET url=NULL WHERE id=?", (source_id,))
        elif mutation == "source_set": conn.execute("UPDATE research_workflow_runs SET result_json=json_set(result_json,'$.source_ids',json('[\"foreign\"]')) WHERE id=?", (workflow_id,))
        elif mutation == "failure": conn.execute("UPDATE task_attempts SET error='Authentication failed' WHERE id=?", (attempt_id,))
        elif mutation == "cancel": conn.execute("UPDATE runs SET cancel_requested=1 WHERE id=?", (run_id,))
        elif mutation == "continuation": conn.execute("UPDATE tasks SET kind='universe_discovery_continuation_1' WHERE id=?", (task_id,))
    assert activate_fallback(repo, run_id) is None
    with pytest.raises(ValueError): repo.control("run", run_id, "use_archived_evidence")


def test_retry_invalidates_receipt_and_atomic_provider_gate(case):
    repo, run_id, task_id, _, _, _ = case
    repo.control("run", run_id, "use_archived_evidence")
    repo.control("task", task_id, "retry")
    assert repo.earnings_archive_fallbacks(run_id) == []
    a03 = next(row for row in repo.tasks_for_run(run_id) if row["agent_id"] == "A03")
    attempt_id = repo.create_attempt(a03["id"], CONFIG, {})["attempt_id"]
    assert repo.mark_provider_started(a03["id"], attempt_id) is None


def test_api_recovery_requires_separate_run_authorization_and_wakes_only_target(case, monkeypatch):
    from backend.app.main import create_app
    repo, run_id, _, _, _, _ = case
    repo.control("firm", None, "pause")
    other, _ = repo.create_run(RunCreate(question="Review MSFT later.", namespace="real", ticker="MSFT", idempotency_key="unrelated-pending"), [("A03", "fundamental_review", "Review later.", [])])
    scheduled = []
    monkeypatch.setattr(Orchestrator, "schedule", lambda self, target: scheduled.append(target))
    headers = {"X-Road2M-Client": "local-ui", "Origin": "http://127.0.0.1:8000"}
    with TestClient(create_app(repo.config, repo)) as client:
        response = client.post("/api/control", json={"scope": "run", "id": run_id, "action": "use_archived_evidence"}, headers=headers)
        assert response.status_code == 200, response.text
        assert scheduled == [] and repo.firm_dispatch_paused(run_id)
        response = client.post("/api/control", json={"scope": "run", "id": run_id, "action": "run_once"}, headers=headers)
        assert response.status_code == 200, response.text
        assert scheduled == [run_id] and repo.firm_paused()
        assert repo.run_record(other["run_id"])["status"] == "queued"


def test_receipt_does_not_override_later_run_pause_or_cancel(case):
    repo, run_id, _, _, _, _ = case
    repo.control("run", run_id, "use_archived_evidence")
    repo.control("run", run_id, "run_once")
    a03 = next(row for row in repo.tasks_for_run(run_id) if row["agent_id"] == "A03")
    attempt_id = repo.create_attempt(a03["id"], CONFIG, {})["attempt_id"]
    repo.control("run", run_id, "pause")
    assert repo.mark_provider_started(a03["id"], attempt_id) is None
    repo.control("run", run_id, "cancel")
    assert repo.mark_provider_started(a03["id"], attempt_id) is None
    assert not repo.earnings_archive_fallbacks(run_id)


def test_recovery_does_not_clear_downstream_provider_failure(case):
    repo, run_id, _, _, _, _ = case
    a03 = next(row for row in repo.tasks_for_run(run_id) if row["agent_id"] == "A03")
    attempt_id = repo.create_attempt(a03["id"], CONFIG, {})["attempt_id"]
    repo.mark_task_failure(a03["id"], attempt_id, "blocked", "Authentication unavailable")
    with pytest.raises(ValueError):
        repo.control("run", run_id, "use_archived_evidence")
    assert repo.task(a03["id"])["status"] == "blocked"
    provider = FakeProvider(lambda packet: output_payload())
    asyncio.run(Orchestrator(repo, FakeRegistry(provider), repo.config).run(run_id))
    assert repo.run_record(run_id)["status"] == "blocked" and provider.calls == []


def test_full_research_loop_keeps_failed_attempt_and_completes_canonical_review(tmp_path):
    repo = Repository(config=Settings(project_root=Path(__file__).resolve().parents[2], data_dir=tmp_path,
                                      enable_market_connectors=False, enable_reddit_intake=False))
    def handler(packet):
        if packet["agent_id"] == "A00":
            attach_workflow(repo, packet["run_id"])
            return _routing_payload()
        if packet["agent_id"] == "A01":
            assert not packet["source_ids"] and not packet["portfolio_snapshot"]
            raise ProviderError("capability", ERROR)
        assert packet["earnings_archive_fallbacks"][0]["status"] == "partial"
        assert "Archive recovery limitation" in packet["question"]
        with repo.db.operation() as conn:
            source_ids = {row["title"]: row["id"] for row in conn.execute("SELECT id,title FROM sources")}
        payload = _provider_payloads(source_ids["ABC synthetic 10-K filing"], source_ids["ABC synthetic market quote"])[packet["agent_id"]]
        if packet["agent_id"] == "A03":
            payload = payload.model_copy(update={"missing_gaps": [MissingGap(
                key="latest_debt_covenant", description="Latest public debt covenant in the issuer filing.",
                relevant_role="A03", reopen_when="A dated issuer filing states the covenant.",
            )]})
        return payload
    provider = _DeterministicProvider(handler)
    case = create_five_question_case(tmp_path, repository=repo, astra_adapter=_DeterministicRegistry(provider))
    assert [item["packet"]["agent_id"] for item in provider.calls] == ["A00", "A01", "A03", "A11"]
    assert repo.run_record(case.run_id)["status"] == "completed"
    assert case.decision is not None
    assert [output["agent_id"] for output in case.outputs] == ["A00", "A03", "A11"]
    for output in case.outputs[1:]:
        assert DISCOVERY_GAP in output["missing_data"] and VALUATION_GAP in output["missing_data"]
    discovery = next(row for row in repo.tasks_for_run(case.run_id) if row["agent_id"] == "A01")
    assert discovery["status"] == "blocked" and discovery["output_id"] is None
    assert any("debt covenant" in gap["description"] and gap["status"] != "resolved" for gap in repo.gaps_for_run(case.run_id))
    assert not any("continuation" in task["kind"] for task in repo.tasks_for_run(case.run_id))
