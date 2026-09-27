"""Acceptance coverage for typed open-question routing.

All provider responses and public discovery pages in this module are local
deterministic doubles.  The tests exercise durable routing decisions and the
provider boundary without making network or hosted-model calls.
"""

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
from backend.app.schemas import (
    AgentOutputPayload,
    AllocationProposal,
    ImportRequest,
    ModelConfig,
    ResearchCandidate,
    RoutingPlan,
    RunCreate,
)


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ModelConfig(provider="codex", model="gpt-5.6-luna", reasoning_effort="max", profile="gpt-first")


@pytest.fixture()
def repository(tmp_path: Path) -> Repository:
    return Repository(config=Settings(data_dir=tmp_path, project_root=ROOT, codex_timeout_seconds=30))


def output_payload(**updates: Any) -> AgentOutputPayload:
    values: dict[str, Any] = {
        "status": "completed",
        "title": "Synthetic routing output",
        "summary": "A deterministic output used by routing acceptance tests.",
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
    """Provider double that returns schema-valid structured output."""

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
        self.calls.append(
            {
                "attempt_id": attempt_id,
                "config": config,
                "packet": packet,
                "prompt": prompt,
                "schema": _schema,
                "discovery_stage": discovery_stage,
            }
        )
        payload = self.handler(packet)
        return ProviderResult(payload=payload.model_dump(), usage={"fake_calls": len(self.calls)})

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


def import_source(repository: Repository, key: str, content: str = "Synthetic public page.") -> str:
    result = repository.import_evidence(
        ImportRequest(
            namespace="real",
            kind="evidence",
            title="Synthetic public page",
            content=content,
            source_url="https://issuer.example.test/page",
            publication_at="2026-09-01",
            observed_at="2026-09-01",
            idempotency_key=key,
        )
    )
    assert result["duplicate"] is False
    return str(result["source_id"])


def test_free_form_run_starts_with_only_a00_and_does_not_force_one_month(repository: Repository) -> None:
    body = RunCreate(
        question="Compare the price trend and filing risk for a new candidate.",
        namespace="real",
        horizon=None,
        ticker=None,
        idempotency_key="routing-free-form-1",
    )
    task_plan = build_research_tasks(body.question, body.horizon, body.ticker, body.namespace)

    assert len(task_plan) == 1
    assert task_plan[0][0:2] == ("A00", "routing")

    created, reused = repository.create_run(body, task_plan)
    assert reused is False
    assert len(created["tasks"]) == 1
    assert created["tasks"][0]["agent_id"] == "A00"
    assert repository.tasks_for_run(created["run_id"])[0]["kind"] == "routing"

    row = repository.run_record(created["run_id"])
    assert row is not None
    assert row["horizon"] is None
    snapshot = json.loads(row["input_snapshot_json"])
    assert snapshot["question"] == body.question
    assert snapshot["ticker"] is None
    assert snapshot["horizon"] is None


def test_retry_uses_effective_source_enum_and_safe_citation_feedback(repository: Repository) -> None:
    source_id = import_source(repository, "routing-source-enum-retry-1", "A locally supplied public source.")
    body = RunCreate(
        question="Explain the supplied source in plain language.",
        namespace="real",
        source_ids=[source_id],
        idempotency_key="routing-source-enum-retry-1",
    )
    created, reused = repository.create_run(
        body,
        build_research_tasks(body.question, body.horizon, body.ticker, body.namespace),
    )
    assert reused is False
    task = created["tasks"][0]
    first_attempt = repository.create_attempt(task["id"], CONFIG, {})
    repository.mark_task_failure(
        task["id"],
        first_attempt["attempt_id"],
        "failed",
        "Provider output failed schema or source validation.",
    )
    failed_task = repository.task_dict(repository.task(task["id"]))
    assert failed_task["blocking_reason"] == "Provider output failed schema or source validation."
    assert repository.request_retry("task", task["id"]) == 1

    def handler(packet: dict[str, Any]) -> AgentOutputPayload:
        assert packet["agent_id"] == "A00"
        return output_payload(
            title="Safe retry route",
            routing_plan=RoutingPlan(
                intent="direct_answer",
                horizon=None,
                tickers=[],
                selected_analysts=[],
                research_queries=[],
                rationale="The supplied source can be explained locally.",
            ),
        )

    provider = FakeProvider(handler)
    asyncio.run(Orchestrator(repository, FakeRegistry(provider), repository.config).run(created["run_id"]))

    assert len(provider.calls) == 1
    call = provider.calls[0]
    assert call["schema"]["$defs"]["FactClaim"]["properties"]["source_ref"]["enum"] == [source_id]
    assert call["schema"]["properties"]["source_refs"]["items"]["enum"] == [source_id]
    assert "Bounded retry feedback" in call["prompt"]
    assert source_id in call["prompt"]
    assert "Provider output failed schema or source validation." not in call["prompt"]
    with repository.db.operation() as connection:
        attempts = connection.execute(
            "SELECT attempt_no,status,error FROM task_attempts WHERE task_id=? ORDER BY attempt_no",
            (task["id"],),
        ).fetchall()
    assert len(attempts) == 2
    assert attempts[0]["status"] == "failed"
    assert attempts[1]["status"] == "completed"


def test_structured_a00_route_controls_dispatch_and_direct_answer_is_a00_only(repository: Repository, monkeypatch: pytest.MonkeyPatch) -> None:
    route_question = "Please review the price trend even though the route selects filing work."

    def handler(packet: dict[str, Any]) -> AgentOutputPayload:
        agent_id = packet["agent_id"]
        if agent_id == "A00":
            return output_payload(
                title="Typed route",
                routing_plan=RoutingPlan(
                    intent="research",
                    horizon="2 years",
                    tickers=["ROUTE"],
                    selected_analysts=["A02"],
                    research_queries=["Find the latest issuer filing."],
                    rationale="The structured route requests filing review.",
                ),
            )
        assert packet["horizon"] == "2 years"
        if agent_id == "A10":
            return output_payload(title="PM accepts", review_disposition="accept")
        if agent_id == "A11":
            return output_payload(
                title="CIO proposal",
                decision_disposition="recommend",
                proposal=AllocationProposal(
                    symbol="ROUTE",
                    target_position_weight="0.05",
                    sector_weight_after="0.05",
                    cash_weight_after="0.95",
                ),
            )
        return output_payload(title=f"{agent_id} completed")

    provider = FakeProvider(handler)
    engine = Orchestrator(repository, FakeRegistry(provider), repository.config)
    # Test the root graph independently of the bounded repair worker.
    monkeypatch.setattr(engine, "schedule", lambda _run_id: None)
    body = RunCreate(question=route_question, namespace="real", horizon=None, idempotency_key="routing-typed-1")
    created, reused = repository.create_run(body, build_research_tasks(body.question, body.horizon, body.ticker, body.namespace))
    assert reused is False

    asyncio.run(engine.run(created["run_id"]))

    assert [call["packet"]["agent_id"] for call in provider.calls] == ["A00", "A01", "A02", "A04", "A10", "A11"]
    # Required technical/scenario checks augment the saved discretionary route.
    assert "A07" not in [call["packet"]["agent_id"] for call in provider.calls]
    snapshot = repository.run_snapshot(created["run_id"])
    assert snapshot is not None
    assert snapshot["status"] == "completed"
    assert [task["kind"] for task in repository.tasks_for_run(created["run_id"])] == [
        "routing",
        "universe_discovery",
        "filing_review",
        "technical_review",
        "simulation_review",
        "pm_review",
        "cio_review",
    ]
    assert snapshot["routing_plan"]["selected_analysts"] == ["A02"]
    assert snapshot["routing_plan"]["horizon"] == "2 years"
    assert snapshot["horizon"] == "2 years"

    direct_body = RunCreate(
        question="How should I explain a local event loop to a colleague?",
        namespace="real",
        horizon=None,
        idempotency_key="routing-direct-1",
    )

    def direct_handler(packet: dict[str, Any]) -> AgentOutputPayload:
        assert packet["agent_id"] == "A00"
        return output_payload(
            title="Direct answer route",
            routing_plan=RoutingPlan(
                intent="direct_answer",
                horizon=None,
                tickers=[],
                selected_analysts=[],
                research_queries=[],
                rationale="The question is a general explanation request.",
            ),
        )

    direct_provider = FakeProvider(direct_handler)
    direct_engine = Orchestrator(repository, FakeRegistry(direct_provider), repository.config)
    direct_created, reused = repository.create_run(
        direct_body,
        build_research_tasks(direct_body.question, direct_body.horizon, direct_body.ticker, direct_body.namespace),
    )
    assert reused is False

    asyncio.run(direct_engine.run(direct_created["run_id"]))

    assert [call["packet"]["agent_id"] for call in direct_provider.calls] == ["A00"]
    direct_snapshot = repository.run_snapshot(direct_created["run_id"])
    assert direct_snapshot is not None
    assert direct_snapshot["status"] == "completed"
    assert direct_snapshot["routing_plan"]["intent"] == "direct_answer"
    assert [task["agent_id"] for task in direct_snapshot["tasks"]] == ["A00"]


def test_routing_expansion_is_restart_safe_and_original_request_replays_after_discovery(repository: Repository) -> None:
    body = RunCreate(
        question="Build a bounded review for two possible candidates.",
        namespace="real",
        horizon=None,
        ticker=None,
        idempotency_key="routing-restart-replay-1",
    )
    created, reused = repository.create_run(body, build_research_tasks(body.question, body.horizon, body.ticker, body.namespace))
    assert reused is False
    routing_task = created["tasks"][0]
    route = RoutingPlan(
        intent="research",
        horizon="3m",
        tickers=["ALFA", "BETA"],
        selected_analysts=["A02"],
        research_queries=["Find dated issuer evidence."],
        rationale="Two bounded candidates require filing review.",
    )
    attempt = repository.create_attempt(routing_task["id"], CONFIG, {})
    committed = repository.commit_output(
        routing_task["id"],
        attempt["attempt_id"],
        output_payload(title="Committed A00 plan", routing_plan=route),
        "real",
        CONFIG,
    )

    first_expansion = repository.ensure_routing_graph(created["run_id"])
    assert first_expansion is not None
    assert first_expansion["reused"] is False
    task_count = len(repository.tasks_for_run(created["run_id"]))
    assert task_count == 7

    repeated = repository.consume_routing_plan(created["run_id"], route, routing_output_id=committed["id"])
    assert repeated["reused"] is True
    assert len(repository.tasks_for_run(created["run_id"])) == task_count

    restarted = Repository(config=repository.config)
    replayed_expansion = restarted.ensure_routing_graph(created["run_id"])
    assert replayed_expansion is not None
    assert replayed_expansion["reused"] is True
    assert len(restarted.tasks_for_run(created["run_id"])) == task_count

    source_id = import_source(repository, "routing-discovery-replay-source", "ALFA public filing page.")
    discovery_task = next(task for task in repository.tasks_for_run(created["run_id"]) if task["kind"] == "universe_discovery")
    repository.record_discovery(
        created["run_id"],
        discovery_task["id"],
        [{"ticker": "ALFA", "rationale": "A bounded discovery lead.", "source_ids": [source_id]}],
        [source_id],
    )

    row = repository.run_record(created["run_id"])
    assert row is not None
    snapshot = json.loads(row["input_snapshot_json"])
    # Dynamic discovery attachments are allowed to grow, while the user's
    # original request fields remain available for idempotency and audit.
    assert snapshot["question"] == body.question
    assert snapshot["ticker"] is None
    assert snapshot["horizon"] is None
    assert snapshot["source_ids"] == [source_id]
    assert row["request"] == body.question

    replayed, reused = restarted.create_run(
        body,
        build_research_tasks(body.question, body.horizon, body.ticker, body.namespace),
    )
    assert reused is True
    assert replayed["run_id"] == created["run_id"]

    with repository.db.operation() as connection:
        output_rows = connection.execute("SELECT id,payload_json FROM outputs WHERE task_id=?", (routing_task["id"],)).fetchall()
    assert len(output_rows) == 1
    assert json.loads(output_rows[0]["payload_json"])["routing_plan"]["tickers"] == ["ALFA", "BETA"]


def test_semantic_cache_rejects_prior_dynamic_discovery_and_stale_workflow_version(repository: Repository) -> None:
    dynamic_body = RunCreate(
        question="Find promising candidates for a new research pass.",
        namespace="real",
        horizon=None,
        ticker=None,
        idempotency_key="routing-cache-dynamic-1",
    )
    dynamic_created, reused = repository.create_run(
        dynamic_body,
        build_research_tasks(dynamic_body.question, dynamic_body.horizon, dynamic_body.ticker, dynamic_body.namespace),
    )
    assert reused is False
    repository.consume_routing_plan(
        dynamic_created["run_id"],
        RoutingPlan(
            intent="research",
            horizon="event",
            tickers=[],
            selected_analysts=["A03"],
            research_queries=["Find public primary sources."],
            rationale="The request needs a fresh bounded discovery pass.",
        ),
    )
    repository.set_run_status(dynamic_created["run_id"], "completed")

    dynamic_replay, dynamic_reused = repository.create_run(
        dynamic_body.model_copy(update={"idempotency_key": "routing-cache-dynamic-2"}),
        build_research_tasks(dynamic_body.question, dynamic_body.horizon, dynamic_body.ticker, dynamic_body.namespace),
    )
    assert dynamic_reused is False
    assert dynamic_replay["run_id"] != dynamic_created["run_id"]

    source_id = import_source(repository, "routing-cache-version-source", "A dated source for cache identity.")
    discovery_task = next(task for task in repository.tasks_for_run(dynamic_created["run_id"]) if task["kind"] == "universe_discovery")
    repository.record_discovery(
        dynamic_created["run_id"],
        discovery_task["id"],
        [{"ticker": "LIVE", "rationale": "A fetched discovery lead.", "source_ids": [source_id]}],
        [source_id],
    )
    live_discovery_replay, live_discovery_reused = repository.create_run(
        dynamic_body.model_copy(update={"idempotency_key": "routing-cache-dynamic-3"}),
        build_research_tasks(dynamic_body.question, dynamic_body.horizon, dynamic_body.ticker, dynamic_body.namespace),
    )
    assert live_discovery_reused is False
    assert live_discovery_replay["run_id"] != dynamic_created["run_id"]

    versioned_body = RunCreate(
        question="Review this dated source.",
        namespace="real",
        horizon="1m",
        ticker="CACHE",
        source_ids=[source_id],
        idempotency_key="routing-cache-version-1",
    )
    versioned_created, reused = repository.create_run(
        versioned_body,
        build_research_tasks(versioned_body.question, versioned_body.horizon, versioned_body.ticker, versioned_body.namespace),
    )
    assert reused is False
    repository.set_run_status(versioned_created["run_id"], "completed")
    with repository.db.transaction(immediate=True) as connection:
        row = connection.execute("SELECT input_snapshot_json FROM runs WHERE id=?", (versioned_created["run_id"],)).fetchone()
        snapshot = json.loads(row["input_snapshot_json"])
        snapshot["workflow_version"] = "routing-v0-stale"
        connection.execute("UPDATE runs SET input_snapshot_json=? WHERE id=?", (json.dumps(snapshot), versioned_created["run_id"]))

    versioned_replay, versioned_reused = repository.create_run(
        versioned_body.model_copy(update={"idempotency_key": "routing-cache-version-2"}),
        build_research_tasks(versioned_body.question, versioned_body.horizon, versioned_body.ticker, versioned_body.namespace),
    )
    assert versioned_reused is False
    assert versioned_replay["run_id"] != versioned_created["run_id"]


def test_discovered_candidates_reach_analysts_and_survive_cio_risk_defer(repository: Repository, monkeypatch: pytest.MonkeyPatch) -> None:
    public_url = "https://issuer.example.test/candidate"
    fetched = FetchedSource(
        requested_url=public_url,
        final_url=public_url,
        content="Candidate issuer page, archived for acceptance testing.",
        title="Synthetic candidate page",
        retrieved_at="2026-09-12T00:00:00Z",
    )
    monkeypatch.setattr(workflow_module, "fetch_public_pages", lambda urls, **_kwargs: [fetched] if public_url in list(urls) else [])
    candidate = ResearchCandidate(
        ticker="CAND",
        name="Candidate issuer",
        rationale="A lead returned by bounded discovery.",
        source_urls=[public_url],
    )
    seen_packets: dict[str, dict[str, Any]] = {}

    def handler(packet: dict[str, Any]) -> AgentOutputPayload:
        agent_id = packet["agent_id"]
        if agent_id == "A00":
            return output_payload(
                title="Candidate route",
                routing_plan=RoutingPlan(
                    intent="research",
                    horizon="event",
                    tickers=[],
                    selected_analysts=["A03"],
                    research_queries=["Find a public primary source for the candidate."],
                    rationale="A01 should discover and preserve candidate leads.",
                ),
            )
        if agent_id == "A01":
            return output_payload(title="Discovery lead", research_candidates=[candidate])
        if agent_id in {"A03", "A10", "A11"}:
            seen_packets[agent_id] = packet
            candidates = packet.get("research_candidates")
            assert candidates and candidates[0]["ticker"] == "CAND"
            source_ids = packet.get("source_ids") or []
            assert source_ids
            archived = next(source for source in repository.sources("real") if source["url"] == public_url)
            assert archived["id"] in source_ids
            if agent_id == "A10":
                return output_payload(title="Candidate accepted by PM", review_disposition="accept", research_candidates=[candidate])
            if agent_id == "A11":
                return output_payload(
                    title="Candidate CIO proposal",
                    decision_disposition="recommend",
                    research_candidates=[candidate],
                    proposal=AllocationProposal(
                        symbol="CAND",
                        target_position_weight="0.05",
                        sector_weight_after="0.05",
                        cash_weight_after="0.95",
                    ),
                )
            return output_payload(title="Candidate analyst review", research_candidates=[candidate])
        return output_payload(title=f"{agent_id} completed")

    provider = FakeProvider(handler)
    engine = Orchestrator(repository, FakeRegistry(provider), repository.config)
    body = RunCreate(
        question="Find and review a candidate issuer.",
        namespace="real",
        horizon=None,
        ticker=None,
        idempotency_key="routing-candidate-propagation-1",
    )
    created, reused = repository.create_run(body, build_research_tasks(body.question, body.horizon, body.ticker, body.namespace))
    assert reused is False

    asyncio.run(engine.run(created["run_id"]))

    assert list(seen_packets) == ["A03", "A10", "A11"]
    with repository.db.operation() as connection:
        raw_artifacts = {
            row["agent_id"]: (row["payload_json"], row["output_hash"])
            for row in connection.execute(
                "SELECT o.agent_id,o.payload_json,o.output_hash FROM outputs o JOIN tasks t ON t.id=o.task_id WHERE t.run_id=?",
                (created["run_id"],),
            ).fetchall()
        }
    snapshot = repository.run_snapshot(created["run_id"])
    assert snapshot is not None
    assert snapshot["status"] == "completed"
    saved_candidate = next(item for item in snapshot["research_candidates"] if item["ticker"] == "CAND")
    assert saved_candidate["evidence_available"] is True
    assert saved_candidate["verified"] is False
    assert saved_candidate["source_ids"]
    assert saved_candidate["unverified_reason"] == "Source archived; candidate thesis remains unverified."

    for agent_id in ("A01", "A03", "A10", "A11"):
        saved = next(item for item in snapshot["outputs"] if item["agent_id"] == agent_id)
        display_candidate = saved["research_candidates"][0]
        assert display_candidate["ticker"] == "CAND"
        assert display_candidate["source_ids"] == saved_candidate["source_ids"]
        assert display_candidate["evidence_available"] is True
        assert display_candidate["verified"] is False
        assert agent_id in raw_artifacts
        with repository.db.operation() as connection:
            stored = connection.execute(
                "SELECT payload_json,output_hash FROM outputs o JOIN tasks t ON t.id=o.task_id WHERE t.run_id=? AND o.agent_id=?",
                (created["run_id"], agent_id),
            ).fetchone()
        assert (stored["payload_json"], stored["output_hash"]) == raw_artifacts[agent_id]
    cio = next(item for item in snapshot["outputs"] if item["agent_id"] == "A11")
    assert cio["decision_disposition"] == "defer"
    decisions = [item for item in repository.decisions("real") if item["run_id"] == created["run_id"] and item["agent_id"] == "A11"]
    assert decisions and decisions[-1]["disposition"] == "defer"


def test_discovery_replaces_pending_reason_with_archive_or_fetch_failure(repository: Repository, monkeypatch: pytest.MonkeyPatch) -> None:
    success_url = "https://issuer.example.test/results"
    failed_url = "https://issuer.example.test/results.pdf"
    success = FetchedSource(
        requested_url=success_url,
        final_url=success_url,
        content="Issuer results HTML page.",
        title="Issuer results",
        retrieved_at="2026-09-12T00:00:00Z",
    )
    failed = FetchedSource(
        requested_url=failed_url,
        final_url=failed_url,
        content="",
        title=failed_url,
        retrieved_at="2026-09-12T00:00:00Z",
        error="source returned PDF or binary content",
    )
    monkeypatch.setattr(workflow_module, "fetch_public_pages", lambda _urls, **_kwargs: [success, failed])

    body = RunCreate(
        question="Review two public candidate sources.",
        namespace="real",
        horizon=None,
        ticker=None,
        idempotency_key="routing-archive-reasons-1",
    )
    created, reused = repository.create_run(body, build_research_tasks(body.question, body.horizon, body.ticker, body.namespace))
    assert reused is False
    repository.consume_routing_plan(
        created["run_id"],
        RoutingPlan(
            intent="research",
            horizon="unspecified",
            tickers=[],
            selected_analysts=["A03"],
            research_queries=["Find two public candidate sources."],
            rationale="Bounded source archival test.",
        ),
    )
    discovery_task = next(task for task in repository.tasks_for_run(created["run_id"]) if task["kind"] == "universe_discovery")
    payload = output_payload(
        research_candidates=[
            ResearchCandidate(
                ticker="GOOD",
                name="Readable issuer",
                rationale="Issuer page was suggested.",
                source_urls=[success_url],
                unverified_reason="Source archival pending.",
            ),
            ResearchCandidate(
                ticker="PDFX",
                name="PDF issuer",
                rationale="PDF page was suggested.",
                source_urls=[failed_url],
                unverified_reason="Source archival pending.",
            ),
        ]
    )

    archived, records, source_ids = asyncio.run(
        Orchestrator(repository, None, repository.config)._archive_discovery(
            created["run_id"], discovery_task, payload
        )
    )

    assert source_ids
    saved_good = next(item for item in records if item["ticker"] == "GOOD")
    assert saved_good["unverified_reason"] == "Source archived; candidate thesis remains unverified."
    saved_pdf = next(item for item in records if item["ticker"] == "PDFX")
    assert failed_url in saved_pdf["unverified_reason"]
    assert "source returned PDF or binary content" in saved_pdf["unverified_reason"]
    assert "archival pending" not in saved_pdf["unverified_reason"].casefold()
    assert len(saved_pdf["unverified_reason"]) <= 1000
    assert archived.research_candidates[0].unverified_reason == "Source archived; candidate thesis remains unverified."
    assert failed_url in (archived.research_candidates[1].unverified_reason or "")


def test_revision_requests_ignore_invalid_targets_and_are_idempotent(repository: Repository) -> None:
    body = RunCreate(
        question="Review a bounded analyst thesis.",
        namespace="real",
        horizon=None,
        ticker=None,
        idempotency_key="routing-revision-1",
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
            research_queries=["Review the bounded thesis."],
            rationale="A03 is the named specialist for the initial packet.",
        ),
    )
    base_tasks = repository.tasks_for_run(created["run_id"])
    assert len([task for task in base_tasks if task["kind"] == "universe_discovery"]) == 1

    invalid = repository.add_revision_task(
        created["run_id"],
        1,
        [
            "Please look again without a target.",
            "A07: run a simulation",
            "A10: review yourself",
            "A11: bypass the PM",
            "A99: invent a new specialist",
        ],
    )
    assert invalid is None
    assert not any(task["kind"].startswith("pm_revision_") for task in repository.tasks_for_run(created["run_id"]))

    # A00 may receive a targeted context or horizon clarification.  It must
    # remain a bounded revision and cannot create a second discovery pass.
    first = repository.add_revision_task(created["run_id"], 1, ["A00: Clarify the planning horizon and context."])
    assert first is not None
    after_first = repository.tasks_for_run(created["run_id"])
    assert len([task for task in after_first if task["kind"] == "pm_revision_1_A00"]) == 1
    assert len([task for task in after_first if task["kind"] == "universe_discovery"]) == 1
    second = repository.add_revision_task(created["run_id"], 1, ["A00: Clarify the planning horizon and context."])
    assert second is None
    after_second = repository.tasks_for_run(created["run_id"])
    assert len(after_second) == len(after_first)
    assert len([task for task in after_second if task["kind"] == "pm_revision_1_A00"]) == 1
    assert len([task for task in after_second if task["kind"] == "pm_revision_1"]) == 1

    next_round = repository.add_revision_task(created["run_id"], 2, ["A03: Recheck the dated thesis evidence."])
    assert next_round is not None
    assert len([task for task in repository.tasks_for_run(created["run_id"]) if task["kind"] == "universe_discovery"]) == 1
    assert len([task for task in repository.tasks_for_run(created["run_id"]) if task["kind"] == "pm_revision_2_A03"]) == 1
    assert repository.add_revision_task(created["run_id"], 3, ["A03: Never dispatch a third round."]) is None
