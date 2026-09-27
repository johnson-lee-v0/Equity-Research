"""Acceptance coverage for the durable A01 discovery handoff."""

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
from backend.app.orchestration.workflow import Orchestrator, build_research_tasks
from backend.app.providers.base import ProviderResult
from backend.app.research.discovery import FetchedSource
from backend.app.schemas import AgentOutputPayload, ImportRequest, ModelConfig, ResearchCandidate, RoutingPlan, RunCreate


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ModelConfig(provider="codex", model="gpt-5.6-luna", reasoning_effort="max", profile="gpt-first")


@pytest.fixture()
def repository(tmp_path: Path) -> Repository:
    return Repository(config=Settings(data_dir=tmp_path, project_root=ROOT, codex_timeout_seconds=30))


def output_payload(**updates: Any) -> AgentOutputPayload:
    values: dict[str, Any] = {
        "status": "completed",
        "title": "Synthetic discovery output",
        "summary": "A deterministic output used by the discovery handoff test.",
        "analysis": "This fixture contains no live market observation.",
        "proposed_action": "defer",
    }
    values.update(updates)
    return AgentOutputPayload(**values)


def packet_from_prompt(prompt: str) -> dict[str, Any]:
    marker = "<untrusted_evidence_packet>"
    assert marker in prompt
    return json.loads(prompt.split(marker, 1)[1].split("</untrusted_evidence_packet>", 1)[0].strip())


class FakeProvider:
    def __init__(self, handler: Callable[[dict[str, Any]], AgentOutputPayload]) -> None:
        self.handler = handler
        self.calls: list[dict[str, Any]] = []

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
        self.calls.append({"attempt_id": attempt_id, "packet": packet, "discovery_stage": discovery_stage})
        return ProviderResult(payload=self.handler(packet).model_dump(), usage={"fake_calls": len(self.calls)})

    async def cancel(self, _attempt_id: str) -> dict[str, Any]:
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


def test_a01_commit_interruption_recovers_archived_handoff_without_repeating_discovery(
    repository: Repository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    public_url = "https://issuer.example.test/candidate"
    fetched = FetchedSource(
        requested_url=public_url,
        final_url=public_url,
        content="CAND issuer page, archived by the local discovery double.",
        title="Synthetic candidate page",
        retrieved_at="2026-09-12T00:00:00Z",
    )
    fetch_calls: list[list[str]] = []

    def fake_fetch(urls: Any, **_kwargs: Any) -> list[FetchedSource]:
        requested = list(urls)
        fetch_calls.append(requested)
        return [fetched] if public_url in requested else []

    monkeypatch.setattr(workflow_module, "fetch_public_pages", fake_fetch)
    candidate = ResearchCandidate(
        ticker="CAND",
        name="Candidate issuer",
        rationale="A bounded public discovery lead.",
        source_urls=[public_url],
        # The provider's flag is deliberately affirmative; the backend must
        # derive the saved value from archived evidence instead.
        verified=True,
    )
    resumed_packets: list[dict[str, Any]] = []

    def provider_handler(packet: dict[str, Any]) -> AgentOutputPayload:
        agent_id = packet["agent_id"]
        if agent_id == "A00":
            return output_payload(
                title="Typed research route",
                routing_plan=RoutingPlan(
                    intent="research",
                    horizon="event",
                    tickers=[],
                    selected_analysts=["A03"],
                    research_queries=["Find a public primary source for CAND."],
                    rationale="PRIVATE_ACCOUNT_SENTINEL A01 performs one bounded discovery pass.",
                ),
            )
        if agent_id == "A01":
            assert "PRIVATE_ACCOUNT_SENTINEL" not in json.dumps(packet, ensure_ascii=False)
            assert packet["routing_plan"].get("rationale") is None
            assert packet["research_candidates"] == []
            return output_payload(title="Provider discovery packet", research_candidates=[candidate])
        if agent_id == "A03":
            # This assertion runs only after the fresh orchestrator resumes;
            # it proves the analyst did not receive a source-less handoff.
            resumed_packets.append(packet)
            candidates = packet.get("research_candidates") or []
            assert candidates and candidates[0]["ticker"] == "CAND"
            assert candidates[0]["source_ids"]
            assert packet["source_ids"] == candidates[0]["source_ids"]
            assert packet["evidence"] and packet["evidence"][0]["content"].startswith("L1:")
            return output_payload(title="Analyst received archived candidate", research_candidates=[candidate])
        return output_payload(title=f"{agent_id} completed")

    first_provider = FakeProvider(provider_handler)
    first_engine = Orchestrator(repository, FakeRegistry(first_provider), repository.config)
    body = RunCreate(
        question="Find and review a candidate issuer.",
        namespace="real",
        horizon=None,
        ticker=None,
        idempotency_key="discovery-handoff-1",
    )
    created, reused = repository.create_run(
        body,
        build_research_tasks(body.question, body.horizon, body.ticker, body.namespace),
    )
    assert reused is False

    real_commit_output = repository.commit_output
    interrupted = False

    def commit_then_interrupt(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal interrupted
        result = real_commit_output(*args, **kwargs)
        task_id = args[0] if args else kwargs["task_id"]
        task = repository.task(task_id)
        if not interrupted and task is not None and task["kind"] == "universe_discovery":
            interrupted = True
            raise RuntimeError("simulated process interruption after A01 output commit")
        return result

    monkeypatch.setattr(repository, "commit_output", commit_then_interrupt)
    asyncio.run(first_engine.run(created["run_id"]))
    assert interrupted is True
    assert [call["packet"]["agent_id"] for call in first_provider.calls] == ["A00", "A01"]
    first_snapshot = repository.run_snapshot(created["run_id"])
    assert first_snapshot is not None
    assert first_snapshot["status"] == "failed"
    first_a01 = next(task for task in repository.tasks_for_run(created["run_id"]) if task["kind"] == "universe_discovery")
    assert first_a01["status"] == "completed"
    with repository.db.operation() as connection:
        first_output_row = connection.execute("SELECT id,payload_json FROM outputs WHERE task_id=?", (first_a01["id"],)).fetchone()
    assert first_output_row is not None
    original_output_id = first_output_row["id"]
    original_payload = json.loads(first_output_row["payload_json"])

    # The second orchestrator opens the same temporary database, as a process
    # restart would.  The completed A01 task must be resumed from its durable
    # handoff rather than sent through the provider again.
    monkeypatch.setattr(repository, "commit_output", real_commit_output)
    restarted = Repository(config=repository.config)
    resumed_provider = FakeProvider(provider_handler)
    resumed_engine = Orchestrator(restarted, FakeRegistry(resumed_provider), restarted.config)
    # Repair dispatch has its own lifecycle tests; isolate this original handoff.
    monkeypatch.setattr(resumed_engine, "schedule", lambda _run_id: None)
    asyncio.run(resumed_engine.run(created["run_id"]))

    assert [call["packet"]["agent_id"] for call in resumed_provider.calls] == ["A03", "A04", "A10", "A11"]
    assert not any(call["packet"]["agent_id"] == "A01" for call in resumed_provider.calls)
    assert len(fetch_calls) == 1
    assert len(resumed_packets) == 1

    snapshot = restarted.run_snapshot(created["run_id"])
    assert snapshot is not None
    assert snapshot["status"] == "completed"
    saved_candidate = next(item for item in snapshot["research_candidates"] if item["ticker"] == "CAND")
    assert saved_candidate["source_ids"]
    assert saved_candidate["verified"] is False
    assert saved_candidate["evidence_available"] is True
    assert snapshot["source_ids"] == saved_candidate["source_ids"]

    with restarted.db.operation() as connection:
        final_a01 = connection.execute("SELECT id,payload_json FROM outputs WHERE task_id=?", (first_a01["id"],)).fetchall()
        sources = connection.execute("SELECT id,url FROM sources WHERE namespace='real' AND url=?", (public_url,)).fetchall()
        versions = connection.execute(
            "SELECT rv.version_no FROM research_versions rv JOIN research_items ri ON ri.id=rv.research_item_id WHERE ri.namespace='real' AND ri.ticker='CAND' ORDER BY rv.version_no"
        ).fetchall()
        handoff_events = connection.execute(
            "SELECT COUNT(*) FROM events WHERE run_id=? AND task_id=? AND type='discovery_archived'",
            (created["run_id"], first_a01["id"]),
        ).fetchone()[0]
    assert len(final_a01) == 1
    assert final_a01[0]["id"] == original_output_id
    assert json.loads(final_a01[0]["payload_json"]) == original_payload
    assert len(sources) == 1
    assert [row[0] for row in versions] == [1]
    assert handoff_events == 1


def test_discovery_does_not_overwrite_existing_research_disposition(repository: Repository) -> None:
    body = RunCreate(
        question="Review a previously held candidate.",
        namespace="real",
        horizon=None,
        ticker=None,
        idempotency_key="discovery-disposition-1",
    )
    created, reused = repository.create_run(
        body,
        build_research_tasks(body.question, body.horizon, body.ticker, body.namespace),
    )
    assert reused is False
    repository.consume_routing_plan(
        created["run_id"],
        RoutingPlan(
            intent="research",
            horizon="event",
            tickers=[],
            selected_analysts=["A03"],
            research_queries=["Review the held candidate."],
            rationale="A03 will review the existing candidate.",
        ),
    )
    with repository.db.transaction(immediate=True) as connection:
        connection.execute(
            "INSERT INTO research_items(id,namespace,ticker,issuer,status,reason,reopen_trigger,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                "research:real:HOLD",
                "real",
                "HOLD",
                "Existing issuer",
                "rejected",
                "User held this candidate pending a new filing.",
                "Reopen only after a dated filing changes the thesis.",
                "2026-09-01T00:00:00Z",
                "2026-09-01T00:00:00Z",
            ),
        )
    discovery_task = next(task for task in repository.tasks_for_run(created["run_id"]) if task["kind"] == "universe_discovery")
    source_id = repository.import_evidence(
        ImportRequest(
            namespace="real",
            kind="evidence",
            title="Held candidate filing",
            content="HOLD public filing.",
            source_url="https://issuer.example.test/held",
            publication_at="2026-09-01",
            observed_at="2026-09-01",
            idempotency_key="discovery-disposition-source",
        )
    )["source_id"]

    repository.record_discovery(
        created["run_id"],
        discovery_task["id"],
        [{"ticker": "HOLD", "rationale": "Fresh discovery rationale.", "source_ids": [source_id]}],
        [source_id],
    )
    with repository.db.operation() as connection:
        row = connection.execute("SELECT status,reason FROM research_items WHERE id='research:real:HOLD'").fetchone()
    assert row["status"] == "rejected"
    assert row["reason"] == "User held this candidate pending a new filing."
