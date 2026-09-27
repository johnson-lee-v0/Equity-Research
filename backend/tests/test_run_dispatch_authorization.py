"""One explicitly requested case can run while the background firm stays paused."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.memory.repository import Repository
from backend.app.orchestration.workflow import Orchestrator
from backend.app.schemas import ModelConfig, RunCreate
from backend.tests.test_independent_acceptance import FakeProvider, FakeRegistry


@pytest.fixture
def repo(tmp_path):
    config = Settings(project_root=Path(__file__).resolve().parents[2], data_dir=tmp_path,
                      enable_market_connectors=False, enable_reddit_intake=False)
    repository = Repository(config=config)
    repository.control("firm", None, "pause")
    return repository


def make_run(repo, key, *, parent=None):
    body = RunCreate(question="Review COST from retained evidence.", ticker="COST",
                     idempotency_key=key, namespace="real", origin="repair" if parent else "user")
    result, _ = repo.create_run(body, [("A03", "fundamental_review", "Review the fixture.", [])],
                                parent_run_id=parent, allow_semantic_reuse=False)
    return result["run_id"]


def attempt(repo, run_id):
    task_id = repo.tasks_for_run(run_id)[0]["id"]
    created = repo.create_attempt(task_id, ModelConfig(provider="codex", model="gpt-5.6-luna", reasoning_effort="max"), {})
    return task_id, created["attempt_id"]


def test_authorized_run_passes_all_dispatch_gates_but_unrelated_run_does_not(repo):
    target, other = make_run(repo, "run-once-target"), make_run(repo, "run-once-unrelated")
    provider = FakeProvider()
    engine = Orchestrator(repo, FakeRegistry(provider), repo.config)
    repo.control("run", target, "run_once")
    assert not engine._pause_requested(target, repo.tasks_for_run(target)[0]["id"])
    assert engine._pause_requested(other, repo.tasks_for_run(other)[0]["id"])
    asyncio.run(engine.run(target))
    assert len(provider.executed_configs) == 1
    assert repo.run_record(target)["status"] == "completed"
    assert repo.run_record(other)["status"] == "queued"
    assert repo.firm_paused()
    # Completed episodes lose the grant before future follow-ups can be queued.
    assert repo.firm_dispatch_paused(target)
    asyncio.run(engine.run(other))
    assert len(provider.executed_configs) == 1
    assert repo.run_record(other)["status"] == "paused"


def test_grant_survives_repository_restart_and_does_not_inherit_to_child(repo):
    target = make_run(repo, "run-once-restart")
    child = make_run(repo, "run-once-child", parent=target)
    repo.control("run", target, "run_once")
    restarted = Repository(config=repo.config)
    assert restarted.firm_paused()
    assert not restarted.firm_dispatch_paused(target)
    assert restarted.firm_dispatch_paused(child)
    target_task, target_attempt = attempt(restarted, target)
    child_task, child_attempt = attempt(restarted, child)
    assert restarted.mark_provider_started(target_task, target_attempt)
    assert restarted.mark_provider_started(child_task, child_attempt) is None


@pytest.mark.parametrize("scope,action", [("firm", "pause"), ("run", "pause"), ("firm", "cancel"), ("run", "cancel")])
def test_explicit_pause_or_cancel_revokes_atomic_provider_authorization(repo, scope, action):
    target = make_run(repo, f"run-once-{scope}-{action}")
    repo.control("run", target, "run_once")
    task_id, attempt_id = attempt(repo, target)
    assert not repo.firm_dispatch_paused(target)
    repo.control(scope, target if scope == "run" else None, action)
    assert repo.firm_dispatch_paused(target)
    assert repo.mark_provider_started(task_id, attempt_id) is None
    if action == "pause":
        repo.control("run", target, "resume")
        assert repo.mark_provider_started(task_id, attempt_id) is None
        repo.control("run", target, "run_once")
        assert repo.mark_provider_started(task_id, attempt_id)


def test_task_pause_still_blocks_authorized_run(repo):
    target = make_run(repo, "run-once-task-pause")
    repo.control("run", target, "run_once")
    task_id, attempt_id = attempt(repo, target)
    repo.control("task", task_id, "pause")
    engine = Orchestrator(repo, FakeRegistry(FakeProvider()), repo.config)
    assert engine._pause_requested(target, task_id)
    assert repo.mark_provider_started(task_id, attempt_id) is None


@pytest.mark.parametrize("terminal", ["completed", "failed", "blocked", "cancelled"])
def test_terminal_episode_cannot_authorize_future_work(repo, terminal):
    target = make_run(repo, "run-once-terminal-" + terminal)
    repo.control("run", target, "run_once")
    repo.set_run_status(target, terminal)
    assert repo.firm_dispatch_paused(target)
    with pytest.raises(ValueError):
        repo.control("run", target, "run_once")


def test_control_api_dispatches_only_selected_case_and_preserves_pause(repo, monkeypatch):
    from backend.app.main import create_app
    target, other = make_run(repo, "run-once-api-target"), make_run(repo, "run-once-api-other")
    scheduled = []
    monkeypatch.setattr(Orchestrator, "schedule", lambda self, run_id: scheduled.append(run_id))
    headers = {"X-Road2M-Client": "local-ui", "Origin": "http://127.0.0.1:8000"}
    with TestClient(create_app(repo.config, repo)) as client:
        response = client.post("/api/control", json={"scope": "run", "id": target, "action": "run_once"}, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["affected"] == 1
        assert scheduled == [target]
        assert repo.run_record(other)["status"] == "queued"
        assert repo.firm_paused()
        rejected = client.post("/api/control", json={"scope": "firm", "action": "run_once"}, headers=headers)
        assert rejected.status_code == 400
        assert scheduled == [target]


def test_interrupted_restart_keeps_existing_explicit_resume_requirement(repo):
    target = make_run(repo, "run-once-interrupted")
    repo.control("run", target, "run_once")
    repo.set_run_status(target, "running")
    task_id, _ = attempt(repo, target)
    restarted = Repository(config=repo.config)
    assert restarted.recover() == 1
    engine = Orchestrator(restarted, FakeRegistry(FakeProvider()), restarted.config)
    assert engine._pause_requested(target, task_id)
    assert restarted.run_record(target)["status"] == "paused"
    restarted.control("run", target, "run_once")
    assert not engine._pause_requested(target, task_id)
