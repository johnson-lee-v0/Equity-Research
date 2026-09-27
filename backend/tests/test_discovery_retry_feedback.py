"""Discovery retries learn the failure without changing bounds or leaking context."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from backend.app.config import Settings
from backend.app.memory.repository import Repository
from backend.app.orchestration.workflow import Orchestrator, _safe_discovery_retry_feedback, build_research_tasks
from backend.app.research.decision_questions import FIVE_QUESTION_CONTRACT
from backend.app.schemas import ModelConfig, RoutingPlan, RunCreate
from backend.tests.test_discovery_handoff import FakeProvider, FakeRegistry, output_payload


@pytest.mark.parametrize("continuation,queries,actions,pages", [(False, 5, 11, 6), (True, 1, 4, 3)])
def test_feedback_uses_existing_stage_limits_and_does_not_echo_raw_error(continuation, queries, actions, pages):
    feedback = _safe_discovery_retry_feedback("Codex exceeded the bounded discovery search-query limit; private-secret must never be echoed", continuation=continuation)
    assert f"{queries} individual search queries" in feedback
    assert f"{actions} total web actions" in feedback
    assert f"{pages} returned source pages" in feedback
    assert "Every query inside a batch counts separately" in feedback
    assert "stop using tools" in feedback
    assert "explicit remaining unknowns" in feedback
    assert "private-secret" not in feedback
    assert _safe_discovery_retry_feedback("An unrelated source validation failure") is None


def test_retried_discovery_receives_feedback_in_sanitized_public_prompt(tmp_path):
    repo = Repository(config=Settings(project_root=Path(__file__).resolve().parents[2], data_dir=tmp_path,
                                      enable_market_connectors=False, enable_reddit_intake=False))
    question = "Assess COST earnings. Private account reference private-secret-account."
    body = RunCreate(question=question, ticker="COST", namespace="real", idempotency_key="discovery-feedback-retry",
                     research_contract=FIVE_QUESTION_CONTRACT)
    run, _ = repo.create_run(body, build_research_tasks(question, None, "COST", "real", lean=True))
    repo.consume_routing_plan(run["run_id"], RoutingPlan(intent="research", horizon="event", tickers=["COST"], selected_analysts=["A03"], research_queries=["COST latest earnings valuation"]))
    task = next(row for row in repo.tasks_for_run(run["run_id"]) if row["agent_id"] == "A01")
    config = ModelConfig(provider="codex", model="gpt-5.6-luna", reasoning_effort="max")
    attempt = repo.create_attempt(task["id"], config, {})
    error = "Codex exceeded the bounded discovery search-query limit (5) at started"
    repo.mark_task_failure(task["id"], attempt["attempt_id"], "blocked", error)
    repo.set_run_status(run["run_id"], "blocked", error=error)
    repo.control("task", task["id"], "retry")
    provider = FakeProvider(lambda packet: output_payload())
    engine = Orchestrator(repo, FakeRegistry(provider), repo.config)
    asyncio.run(engine._execute_task(run["run_id"], repo.task(task["id"])))
    assert len(provider.calls) == 1
    packet = provider.calls[0]["packet"]
    assert "Bounded discovery retry feedback:" in packet["question"]
    assert "5 individual search queries" in packet["question"]
    assert "private-secret-account" not in str(packet)
    assert not packet["source_ids"] and not packet["evidence"]
    assert not packet["portfolio_snapshot"]
