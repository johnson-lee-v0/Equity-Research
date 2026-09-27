"""Independent acceptance tests for durable workflow invariants.

These tests deliberately stop at the provider boundary.  The fake providers
use asyncio events so policy changes, restart recovery, and cancellation are
observed at deterministic points without network access, credentials, or
arbitrary sleeps.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from backend.app.config import Settings
from backend.app.memory.repository import Repository
from backend.app.orchestration.workflow import Orchestrator
from backend.app.providers.base import ProviderResult
from backend.app.schemas import AgentOutputPayload, ModelConfig, RunCreate


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def repository(tmp_path: Path) -> Repository:
    return Repository(config=Settings(data_dir=tmp_path, project_root=ROOT, codex_timeout_seconds=30))


def make_run(
    repository: Repository,
    key: str,
    *,
    namespace: str = "real",
    model_override: ModelConfig | None = None,
    agent_id: str = "A03",
) -> dict[str, Any]:
    body = RunCreate(
        question="Review a sanitized acceptance fixture",
        namespace=namespace,
        horizon="1m",
        ticker="TEST",
        source_ids=[],
        model_override=model_override,
        idempotency_key=key,
    )
    created, reused = repository.create_run(
        body,
        [(agent_id, "fundamental_review", "Review the supplied fixture.", [])],
    )
    assert reused is False
    return created


def valid_payload(title: str = "Fixture output") -> AgentOutputPayload:
    return AgentOutputPayload(
        status="completed",
        title=title,
        summary="A deterministic fake-provider result for acceptance testing.",
        analysis="This result contains no external observation and is used only to test persistence.",
        proposed_action="defer",
    )


class FakeProvider:
    """Provider boundary double with deterministic lifecycle handshakes."""

    def __init__(self, *, payload: AgentOutputPayload | None = None, held: bool = False) -> None:
        self.payload = payload or valid_payload()
        self.held = held
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        if not held:
            self.release.set()
        self.executed_configs: list[ModelConfig] = []
        self.cancel_calls: list[str] = []
        self.preflight_calls: list[tuple[str, bool]] = []

    def is_preflighted(self, _: ModelConfig) -> bool:
        return True

    async def preflight(self, config: ModelConfig, execute: bool) -> dict[str, Any]:
        self.preflight_calls.append((config.model, execute))
        return {
            "available": True,
            "status": "ready",
            "reason": None,
            "model": config.model,
            "reasoning_effort": config.reasoning_effort,
            "actual_execution": execute,
        }

    async def execute(
        self,
        attempt_id: str,
        _prompt: str,
        config: ModelConfig,
        _schema: dict[str, Any],
        _workdir: Path,
        _on_event: Any = None,
    ) -> ProviderResult:
        self.executed_configs.append(config)
        self.started.set()
        await self.release.wait()
        return ProviderResult(payload=self.payload.model_dump(), usage={"fake": 1})

    async def cancel(self, attempt_id: str) -> dict[str, Any]:
        self.cancel_calls.append(attempt_id)
        return {"cancelled": True}


class BlockingProvider(FakeProvider):
    def __init__(self, reason: str) -> None:
        super().__init__()
        self.reason = reason
        self.execute_calls = 0

    async def preflight(self, config: ModelConfig, execute: bool) -> dict[str, Any]:
        self.preflight_calls.append((config.model, execute))
        return {
            "available": False,
            "status": self.reason,
            "reason": f"Synthetic {self.reason} blocker.",
            "model": config.model,
            "reasoning_effort": config.reasoning_effort,
            "actual_execution": False,
        }

    async def execute(self, *args: Any, **kwargs: Any) -> ProviderResult:
        self.execute_calls += 1
        raise AssertionError("blocked provider must not execute")


class FakeRegistry:
    def __init__(self, provider: FakeProvider) -> None:
        self.provider = provider

    def adapter(self, provider: str) -> FakeProvider:
        assert provider == "codex"
        return self.provider

    @asynccontextmanager
    async def generation_slot(self, _provider: str):
        yield

    async def preflight(self, config: ModelConfig, execute: bool) -> dict[str, Any]:
        return await self.provider.preflight(config, execute)


def attempts_for(repository: Repository, task_id: str) -> list[Any]:
    with repository.db.operation() as connection:
        return connection.execute(
            "SELECT * FROM task_attempts WHERE task_id=? ORDER BY attempt_no",
            (task_id,),
        ).fetchall()


def outputs_for(repository: Repository, run_id: str) -> list[Any]:
    with repository.db.operation() as connection:
        return connection.execute(
            "SELECT o.* FROM outputs o JOIN tasks t ON t.id=o.task_id WHERE t.run_id=? ORDER BY o.created_at",
            (run_id,),
        ).fetchall()


def test_policy_changes_apply_to_future_dispatch_but_freeze_held_attempt(repository: Repository) -> None:
    async def scenario() -> None:
        first = ModelConfig(provider="codex", model="gpt-5.6-luna", reasoning_effort="max", profile="gpt-first")
        second = ModelConfig(provider="codex", model="gpt-6-astra", reasoning_effort="ultra", profile="committee")
        repository.set_policy("firm", None, first, None)

        first_run = make_run(repository, "independent-policy-held-1")
        provider = FakeProvider(payload=valid_payload("First model output"), held=True)
        engine = Orchestrator(repository, FakeRegistry(provider), repository.config)
        running = asyncio.create_task(engine.run(first_run["run_id"]))
        await asyncio.wait_for(provider.started.wait(), timeout=5)

        # The attempt is already running and must retain the first resolved
        # configuration even after the firm policy changes.
        repository.set_policy("firm", None, second, None)
        first_task_id = first_run["tasks"][0]["id"]
        held_attempt = attempts_for(repository, first_task_id)[0]
        assert held_attempt["model"] == first.model
        assert json.loads(held_attempt["resolved_config_json"])["model"] == first.model

        provider.release.set()
        await running
        first_output = repository.latest_outputs(first_run["run_id"])[0]
        assert first_output["model"] == first.model
        assert first_output["reasoning_effort"] == first.reasoning_effort

        # A task dispatched after the policy edit resolves the new settings.
        second_run = make_run(repository, "independent-policy-held-2")
        second_task = asyncio.create_task(engine.run(second_run["run_id"]))
        await second_task
        assert provider.executed_configs[-1].model == second.model
        assert provider.executed_configs[-1].reasoning_effort == second.reasoning_effort
        second_output = repository.latest_outputs(second_run["run_id"])[0]
        assert second_output["model"] == second.model
        assert second_output["reasoning_effort"] == second.reasoning_effort

        # Role and one-run overrides remain explicit precedence layers.
        role_config = ModelConfig(provider="codex", model="gpt-5.6-luna", reasoning_effort="high", profile="role")
        repository.set_policy("role", "A03", role_config, None)
        resolved_role, role_source = repository.resolve_model("A03")
        assert resolved_role == role_config
        assert role_source == "role_override"
        one_run_config = ModelConfig(provider="codex", model="gpt-6-astra", reasoning_effort="max", profile="one-run")
        resolved_one_run, one_run_source = repository.resolve_model("A03", one_run_config)
        assert resolved_one_run == one_run_config
        assert one_run_source == "task_override"

    asyncio.run(scenario())


@pytest.mark.parametrize("blocker", ["auth", "quota"])
def test_auth_or_quota_blocks_without_output_or_paid_fallback(repository: Repository, blocker: str) -> None:
    created = make_run(repository, f"independent-blocked-{blocker}")
    provider = BlockingProvider(blocker)
    engine = Orchestrator(repository, FakeRegistry(provider), repository.config)

    asyncio.run(engine.run(created["run_id"]))

    task = repository.task(created["tasks"][0]["id"])
    run = repository.run_record(created["run_id"])
    assert task is not None and task["status"] == "blocked"
    assert run is not None and run["status"] == "blocked"
    assert outputs_for(repository, created["run_id"]) == []
    assert provider.execute_calls == 0
    assert provider.preflight_calls and all(execute is False for _, execute in provider.preflight_calls)
    with repository.db.operation() as connection:
        attempts = connection.execute(
            "SELECT status FROM task_attempts WHERE task_id=?",
            (created["tasks"][0]["id"],),
        ).fetchall()
        paid_fallbacks = connection.execute("SELECT paid_fallback_enabled FROM model_policies").fetchall()
    assert [row[0] for row in attempts] == ["blocked"]
    assert all(row[0] == 0 for row in paid_fallbacks)
    assert any(event["type"] == "blocked" for event in repository.events("real", 0, created["run_id"]))


def test_restart_recovery_preserves_history_and_explicit_retry_creates_attempt(repository: Repository) -> None:
    created = make_run(repository, "independent-recovery-retry", namespace="demo")
    run_id = created["run_id"]
    task_id = created["tasks"][0]["id"]
    config = ModelConfig(provider="codex", model="gpt-5.6-luna", reasoning_effort="max")

    repository.set_run_status(run_id, "running", event_type="started", message="Synthetic attempt started.")
    original_attempt = repository.create_attempt(task_id, config, {})
    repository.emit("demo", "progress", run_id=run_id, task_id=task_id, attempt_id=original_attempt["attempt_id"], payload={"message": "Synthetic provider is held."})
    recovered = repository.recover()

    assert recovered == 1
    assert repository.run_record(run_id)["status"] == "paused"
    assert repository.task(task_id)["status"] == "interrupted"
    history = repository.events("demo", 0, run_id)
    assert [event["type"] for event in history].count("interrupted") == 1
    assert any(event["type"] == "progress" for event in history)
    assert attempts_for(repository, task_id)[0]["status"] == "interrupted"

    # Explicit retry is the user action after restart recovery. It queues a
    # fresh attempt while preserving the interrupted attempt and event trail.
    assert repository.control("run", run_id, "retry") == 1
    engine = Orchestrator(repository, FakeRegistry(FakeProvider()), repository.config)
    asyncio.run(engine.run(run_id))

    attempts = attempts_for(repository, task_id)
    assert len(attempts) == 2
    assert [attempt["status"] for attempt in attempts] == ["interrupted", "completed"]
    assert repository.task(task_id)["status"] == "completed"
    assert repository.run_record(run_id)["status"] == "completed"
    assert len(repository.latest_outputs(run_id)) == 1
    event_types = [event["type"] for event in repository.events("demo", 0, run_id)]
    assert "retry_requested" in event_types
    assert "output_validated" in event_types


def test_late_cancelled_provider_result_cannot_overwrite_terminal_task(repository: Repository) -> None:
    async def scenario() -> None:
        created = make_run(repository, "independent-late-cancel")
        run_id = created["run_id"]
        task_id = created["tasks"][0]["id"]
        provider = FakeProvider(payload=valid_payload("Late result"), held=True)
        engine = Orchestrator(repository, FakeRegistry(provider), repository.config)
        running = asyncio.create_task(engine.run(run_id))
        await asyncio.wait_for(provider.started.wait(), timeout=5)

        assert repository.control("run", run_id, "cancel") == 1
        assert await engine.cancel_active(run_id=run_id) == 1
        provider.release.set()
        await running

        run = repository.run_record(run_id)
        task = repository.task(task_id)
        attempts = attempts_for(repository, task_id)
        assert run is not None and run["status"] == "cancelled"
        assert task is not None and task["status"] == "cancelled"
        assert len(attempts) == 1 and attempts[0]["status"] == "cancelled_late"
        assert outputs_for(repository, run_id) == []
        events = repository.events("real", 0, run_id)
        assert any(event["type"] == "cancelled" for event in events)
        assert not any(event["type"] == "output_validated" for event in events)
        assert len(provider.cancel_calls) == 1

    asyncio.run(scenario())
