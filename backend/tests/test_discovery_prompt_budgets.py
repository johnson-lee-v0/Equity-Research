"""The actual discovery prompt must agree with its provider-enforced budget."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from backend.app.agents.roles import ROLE_BY_ID, role_prompt
from backend.app.config import Settings
from backend.app.memory.repository import Repository
from backend.app.orchestration.workflow import Orchestrator, build_research_tasks
from backend.app.research.decision_questions import FIVE_QUESTION_CONTRACT
from backend.app.schemas import RoutingPlan, RunCreate
from backend.tests.test_discovery_handoff import FakeProvider, FakeRegistry, output_payload


def test_initial_five_question_final_prompt_matches_enforced_limits(tmp_path, monkeypatch):
    import backend.app.orchestration.workflow as workflow_module

    class CapturingProvider(FakeProvider):
        async def execute(self, attempt_id, prompt, config, schema, workdir, on_event=None,
                          discovery_stage=False, discovery_limits=None):
            self.prompt = prompt
            self.limits = discovery_limits
            return await super().execute(attempt_id, prompt, config, schema, workdir,
                                         on_event, discovery_stage=discovery_stage)

    def no_source_fetch(urls, **kwargs):
        assert not list(urls), "The fixture must not request a real network fetch."
        return []

    monkeypatch.setattr(workflow_module, "fetch_public_pages", no_source_fetch)
    repo = Repository(config=Settings(project_root=Path(__file__).resolve().parents[2], data_dir=tmp_path,
                                      enable_market_connectors=False, enable_reddit_intake=False))
    question = "Research NKE using the regular investment process."
    run, _ = repo.create_run(
        RunCreate(question=question, ticker="NKE", namespace="real", research_contract=FIVE_QUESTION_CONTRACT,
                  idempotency_key="discovery-final-prompt-budget"),
        build_research_tasks(question, "12 months", "NKE", "real", lean=True),
    )
    repo.consume_routing_plan(run["run_id"], RoutingPlan(
        intent="research", horizon="12 months", tickers=["NKE"], selected_analysts=["A03"],
        research_queries=["NKE latest earnings and valuation"],
    ))
    task = next(row for row in repo.tasks_for_run(run["run_id"]) if row["agent_id"] == "A01")
    # The execution-time prompt must also correct a task created under the old
    # default role wording; no stored instruction or existing case is rewritten.
    assert "six search operations" in task["instruction"]
    provider = CapturingProvider(lambda packet: output_payload())
    engine = Orchestrator(repo, FakeRegistry(provider), repo.config)
    asyncio.run(engine._execute_task(run["run_id"], task))

    assert len(provider.calls) == 1
    assert provider.limits == {"max_search_queries": 5, "max_web_actions": 11}
    assert "5 individual search queries and 11 total web actions" in provider.prompt
    assert "up to six public primary-source URLs" in provider.prompt
    assert "Every query inside a batch counts separately" in provider.prompt
    assert "opening or finding within a page also consumes a web action" in provider.prompt
    assert "stop using tools" in provider.prompt and "explicit remaining unknowns" in provider.prompt
    assert "six search operations" not in provider.prompt
    assert "twelve total web operations" not in provider.prompt
    assert "up to twelve public" not in provider.prompt


@pytest.mark.parametrize("lean", [False, True])
def test_legacy_discovery_retains_default_budget(lean):
    prompt = role_prompt(ROLE_BY_ID["A01"], "Find public candidates.", "12 months", "real",
                         discovery_stage=True, lean_stage=lean)
    assert "up to five candidate tickers" in prompt
    assert "up to twelve public primary-source URLs" in prompt
    assert "at most six search operations and twelve total web operations" in prompt
    assert "5 individual search queries" not in prompt


def test_five_question_continuation_states_its_smaller_budget_and_batch_counting():
    prompt = role_prompt(ROLE_BY_ID["A01"], "Check the retained public gap.", "12 months", "real",
                         discovery_stage=True, lean_stage=True, evidence_gap_stage=True,
                         research_contract=FIVE_QUESTION_CONTRACT)
    assert "at most 1 individual search query, 4 total web actions, and 3 returned source pages" in prompt
    assert "Every query inside a batch counts separately" in prompt
    assert "Do not identify, add, replace or rank instruments" in prompt
    assert "six search operations" not in prompt
    assert "twelve total web operations" not in prompt
