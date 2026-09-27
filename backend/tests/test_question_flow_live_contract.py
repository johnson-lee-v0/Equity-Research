"""Live orchestration regressions for linked evidence repair and capacity waits."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable

import pytest

import backend.app.orchestration.workflow as workflow_module
from backend.app.config import Settings
from backend.app.memory.repository import Repository
from backend.app.orchestration.workflow import Orchestrator
from backend.app.providers.base import ProviderResult
from backend.app.schemas import AgentOutputPayload, ImportRequest, ModelConfig, ResearchRequest, RoutingPlan, RunCreate


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ModelConfig(provider="codex", model="gpt-5.6-luna", reasoning_effort="max", profile="gpt-first")


def make_repository(tmp_path: Path) -> Repository:
    return Repository(config=Settings(data_dir=tmp_path, project_root=ROOT, codex_timeout_seconds=30))


def packet_from_prompt(prompt: str) -> dict[str, Any]:
    marker = "<untrusted_evidence_packet>"
    assert marker in prompt
    return json.loads(prompt.split(marker, 1)[1].split("</untrusted_evidence_packet>", 1)[0].strip())


def fixture_payload(**updates: Any) -> AgentOutputPayload:
    values: dict[str, Any] = {
        "status": "completed",
        "title": "Synthetic provider output",
        "summary": "A deterministic provider result for orchestration coverage.",
        "analysis": "This fixture contains no live market observation.",
        "proposed_action": "defer",
    }
    values.update(updates)
    return AgentOutputPayload(**values)


class FakeProvider:
    def __init__(self, handler: Callable[[dict[str, Any]], AgentOutputPayload]) -> None:
        self.handler = handler
        self.calls: list[dict[str, Any]] = []
        self.cancel_calls: list[str] = []

    def is_preflighted(self, _config: ModelConfig) -> bool:
        return True

    async def preflight(self, config: ModelConfig, execute: bool) -> dict[str, Any]:
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
        prompt: str,
        config: ModelConfig,
        _schema: dict[str, Any],
        _workdir: Path,
        _on_event: Any = None,
        discovery_stage: bool = False,
    ) -> ProviderResult:
        packet = packet_from_prompt(prompt)
        call = {"attempt_id": attempt_id, "packet": packet, "prompt": prompt, "discovery_stage": discovery_stage}
        self.calls.append(call)
        return ProviderResult(payload=self.handler(packet).model_dump(), usage={"fake_calls": len(self.calls)})

    async def cancel(self, attempt_id: str) -> dict[str, Any]:
        self.cancel_calls.append(attempt_id)
        return {"cancelled": True}


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


def import_source(repository: Repository, key: str) -> str:
    result = repository.import_evidence(
        ImportRequest(
            namespace="real",
            kind="evidence",
            title="Retained parent filing",
            content="The retained filing is available for evidence repair.",
            source_url="https://issuer.example.test/retained-filing",
            publication_at="2026-09-01",
            observed_at="2026-09-01",
            idempotency_key=key,
        )
    )
    return str(result["source_id"])


def test_linked_research_runs_fresh_public_discovery_and_keeps_parent_gaps_local(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repository = make_repository(tmp_path)
    source_id = import_source(repository, "live-research-source")
    parent, reused = repository.create_run(
        RunCreate(
            question="What should I do with my account balance after ABC's filing?",
            namespace="real",
            ticker="ABC",
            source_ids=[source_id],
            idempotency_key="live-research-parent",
        ),
        [("A11", "cio_review", "CIO review", [])],
        allow_semantic_reuse=False,
    )
    assert reused is False
    parent_task = parent["tasks"][0]["id"]
    parent_attempt = repository.create_attempt(parent_task, CONFIG, {source_id: {"version": 1}})
    repository.commit_output(
        parent_task,
        parent_attempt["attempt_id"],
        fixture_payload(
            status="needs_review",
            title="Parent CIO answer",
            summary="The answer needs one dated filing.",
            missing_data=["Need a dated filing for ABC revenue."],
            decision_disposition="defer",
        ),
        "real",
        CONFIG,
    )
    # Missing-data reports now create an automatic repair. Close that worker
    # so this fixture specifically exercises a fresh manual request; active
    # child reuse and round limits have separate focused coverage.
    with repository.db.operation() as connection:
        automatic = connection.execute("SELECT repair_run_id FROM research_repairs WHERE root_run_id=?", (parent["run_id"],)).fetchall()
    assert len(automatic) == 1
    repository.control("run", automatic[0]["repair_run_id"], "cancel")
    child, reused = repository.create_evidence_research(
        parent["run_id"],
        ResearchRequest(
            instruction="Find the dated filing supporting the unresolved ABC revenue gap.",
            idempotency_key="live-research-child",
        ),
    )
    assert reused is False

    # No network is needed for this path.  The route ticker remains a durable
    # candidate even when this bounded public fetch returns no pages.
    monkeypatch.setattr(workflow_module, "fetch_public_pages", lambda *_args, **_kwargs: [])

    def handler(packet: dict[str, Any]) -> AgentOutputPayload:
        agent_id = packet["agent_id"]
        if agent_id == "A00":
            assert packet["parent_research_context"]["missing_data"] == ["Need a dated filing for ABC revenue."]
            assert packet["research_instruction"] == "Find the dated filing supporting the unresolved ABC revenue gap."
            assert "Find the dated filing supporting the unresolved ABC revenue gap." in packet["question"]
            return fixture_payload(
                title="Fresh repair route",
                routing_plan=RoutingPlan(
                    intent="research",
                    horizon="event",
                    tickers=["ABC"],
                    selected_analysts=["A03"],
                    research_queries=["Find a public primary source for ABC revenue."],
                    rationale="The repair requires one public dated filing.",
                ),
            )
        if agent_id == "A01":
            # This packet is the web boundary.  Parent answer text, gap text,
            # account context and the focused local instruction stay out.
            assert "parent_research_context" not in packet
            assert "research_instruction" not in packet
            assert "Need a dated filing for ABC revenue." not in json.dumps(packet, ensure_ascii=False)
            assert "account balance" not in json.dumps(packet, ensure_ascii=False).casefold()
            # A malformed/legacy provider field must not grant A01 CIO
            # authority in the task progress projection.
            return fixture_payload(title="Fresh public discovery", decision_disposition="recommend")
        if agent_id == "A03":
            assert packet["parent_research_context"]["parent_run_id"] == parent["run_id"]
            assert packet["parent_research_context"]["missing_data"] == ["Need a dated filing for ABC revenue."]
            assert packet["research_instruction"] == "Find the dated filing supporting the unresolved ABC revenue gap."
            return fixture_payload(title="Analyst repair review")
        if agent_id == "A04":
            return fixture_payload(title="Technical repair review")
        if agent_id == "A10":
            return fixture_payload(title="PM repair review", review_disposition="accept")
        if agent_id == "A11":
            return fixture_payload(title="CIO repair answer", decision_disposition="recommend")
        raise AssertionError(f"unexpected agent {agent_id}")

    provider = FakeProvider(handler)
    engine = Orchestrator(repository, FakeRegistry(provider), repository.config)
    # Child gap-repair workers are tested separately from this public boundary.
    monkeypatch.setattr(engine, "schedule", lambda _run_id: None)
    asyncio.run(engine.run(child["run_id"]))

    assert [call["packet"]["agent_id"] for call in provider.calls] == ["A00", "A01", "A03", "A04", "A10", "A11"]
    assert provider.calls[1]["discovery_stage"] is True
    assert all(call["discovery_stage"] is False for call in provider.calls[2:])
    child_snapshot = repository.run_snapshot(child["run_id"])
    assert child_snapshot is not None
    assert child_snapshot["status"] == "completed"
    assert child_snapshot["parent_run_id"] == parent["run_id"]
    assert child_snapshot["question"] == "What should I do with my account balance after ABC's filing?"
    assert child_snapshot["routing_plan"]["intent"] == "research"
    assert {task["agent_id"] for task in child_snapshot["tasks"]} >= {"A00", "A01", "A03", "A10", "A11"}
    discovery_task = next(task for task in child_snapshot["tasks"] if task["agent_id"] == "A01")
    assert discovery_task["terminal_summary"] == "Finished — report saved."
    cio_task = next(task for task in child_snapshot["tasks"] if task["agent_id"] == "A11")
    pm_task = next(task for task in child_snapshot["tasks"] if task["agent_id"] == "A10")
    pm_output = next(output for output in child_snapshot["outputs"] if output["task_id"] == pm_task["id"])
    assert cio_task["review_context"] == {
        "output_id": pm_output["id"],
        "title": "PM repair review",
        "relation": "recorded",
        "label": "PM report supplied to CIO before provider start.",
    }


def test_cancellation_while_waiting_for_generation_capacity_keeps_attempt_queued(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    run, reused = repository.create_run(
        RunCreate(question="Wait for provider capacity", namespace="real", idempotency_key="capacity-cancel-run"),
        [("A03", "fundamental_review", "Fundamental review", [])],
        allow_semantic_reuse=False,
    )
    assert reused is False
    task_id = run["tasks"][0]["id"]

    async def exercise() -> tuple[FakeProvider, Any]:
        slot_entered = asyncio.Event()
        release_slot = asyncio.Event()

        class HeldRegistry(FakeRegistry):
            @asynccontextmanager
            async def generation_slot(self, _provider: str):
                slot_entered.set()
                await release_slot.wait()
                yield

        provider = FakeProvider(lambda _packet: fixture_payload(title="Should not execute"))
        engine = Orchestrator(repository, HeldRegistry(provider), repository.config)
        running = asyncio.create_task(engine.run(run["run_id"]))
        await asyncio.wait_for(slot_entered.wait(), timeout=2)
        waiting = repository.task_dict(repository.task(task_id))
        assert waiting["dispatch_state"] == "waiting_capacity"
        assert waiting["attempts"][-1]["started_at"] is None
        assert waiting["attempts"][-1]["queued_at"]
        attempt_id = waiting["attempts"][-1]["id"]

        assert repository.control("run", run["run_id"], "cancel") == 1
        assert await engine.cancel_active(run_id=run["run_id"]) == 1
        release_slot.set()
        await asyncio.wait_for(running, timeout=2)
        return provider, repository.run_snapshot(run["run_id"])

    provider, snapshot = asyncio.run(exercise())
    assert provider.calls == []
    assert provider.cancel_calls
    assert snapshot is not None
    assert snapshot["status"] == "cancelled"
    task = snapshot["tasks"][0]
    assert task["status"] == "cancelled"
    assert task["dispatch_state"] == "finished"
    assert task["allowed_actions"] == []
    assert task["attempts"][0]["status"] == "cancelled"
    assert task["attempts"][0]["started_at"] is None


def test_pause_while_waiting_for_generation_capacity_prevents_provider_start_and_resumes(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    run, reused = repository.create_run(
        RunCreate(question="Pause before provider start", namespace="real", idempotency_key="capacity-pause-run"),
        [("A03", "fundamental_review", "Fundamental review", [])],
        allow_semantic_reuse=False,
    )
    assert reused is False
    task_id = run["tasks"][0]["id"]

    async def exercise() -> tuple[FakeProvider, Any]:
        slot_entered = asyncio.Event()
        release_slot = asyncio.Event()

        class HeldRegistry(FakeRegistry):
            @asynccontextmanager
            async def generation_slot(self, _provider: str):
                slot_entered.set()
                await release_slot.wait()
                yield

        provider = FakeProvider(lambda _packet: fixture_payload(title="Resumed provider output"))
        registry = HeldRegistry(provider)
        engine = Orchestrator(repository, registry, repository.config)
        paused_run = asyncio.create_task(engine.run(run["run_id"]))
        await asyncio.wait_for(slot_entered.wait(), timeout=2)
        assert repository.control("run", run["run_id"], "pause") == 1
        release_slot.set()
        await asyncio.wait_for(paused_run, timeout=2)

        paused_snapshot = repository.run_snapshot(run["run_id"])
        assert paused_snapshot is not None
        assert paused_snapshot["status"] == "paused"
        paused_task = paused_snapshot["tasks"][0]
        assert paused_task["status"] == "queued"
        assert paused_task["dispatch_state"] == "queued"
        assert paused_task["attempts"][0]["status"] == "interrupted"
        assert paused_task["attempts"][0]["started_at"] is None
        assert provider.calls == []

        assert repository.control("run", run["run_id"], "resume") == 1
        resumed = asyncio.create_task(engine.run(run["run_id"]))
        await asyncio.wait_for(resumed, timeout=2)
        return provider, repository.run_snapshot(run["run_id"])

    provider, snapshot = asyncio.run(exercise())
    assert len(provider.calls) == 1
    assert snapshot is not None
    assert snapshot["status"] == "completed"
    task = snapshot["tasks"][0]
    assert task["status"] == "completed"
    assert [attempt["status"] for attempt in task["attempts"]] == ["interrupted", "completed"]


def test_cancelling_final_task_does_not_complete_parent_run(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    run, _ = repository.create_run(
        RunCreate(question="Cancel final committee stage", namespace="real", idempotency_key="live-final-cancel"),
        [("A11", "cio_review", "CIO", [])],
        allow_semantic_reuse=False,
    )
    task_id = run["tasks"][0]["id"]
    assert repository.control("task", task_id, "cancel") == 1

    provider = FakeProvider(lambda _packet: fixture_payload())
    engine = Orchestrator(repository, FakeRegistry(provider), repository.config)
    asyncio.run(engine.run(run["run_id"]))

    snapshot = repository.run_snapshot(run["run_id"])
    assert snapshot is not None
    assert snapshot["status"] == "blocked"
    assert snapshot["tasks"][0]["status"] == "cancelled"
    assert snapshot["latest_output_id"] is None
    assert provider.calls == []
