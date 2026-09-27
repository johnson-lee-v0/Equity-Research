"""Ordinary provider packets retain one exact single-company valuation context."""
import asyncio
import json
from copy import deepcopy

import pytest

from backend.app.orchestration.workflow import Orchestrator
from backend.app.research.valuation_context import compact_valuation_context
from backend.tests.test_assessment_pipeline import case as assessment_case
from backend.tests.test_discovery_handoff import FakeProvider, FakeRegistry
from backend.tests.test_five_question_commit_path import _DeterministicLaya
from backend.tests.test_investment_valuation import answer, ordinary


def packet():
    valuation = {
        "ticker": "ACME", "as_of": "2026-09-01", "sources": [{"source_id": "source_1"}],
        "historical_pe": {"points": [{"date": "2026-08-30", "multiple": "19.2"}]},
        "historical_multiples": {"ps": {"points": [{"date": "2026-08-30", "multiple": "3.1"}]}},
        "earnings_bridge": {"actual_quarters": [1, 2, 3], "projection": "4.2"},
    }
    return {
        "ticker": "ACME", "valuation_research_context": valuation,
        "valuation_research_contexts": {"ACME": deepcopy(valuation)},
        "evidence": [{"id": "source_1", "content": "Keep the original source."}],
        "unrelated": {"preserve": ["all", "fields"]},
    }


def test_only_exact_single_ticker_alias_is_removed_without_mutating_inputs():
    original = packet()
    frozen = deepcopy(original)
    projected = Orchestrator._provider_single_ticker_valuation_projection(original)
    expected = deepcopy(original)
    expected.pop("valuation_research_contexts")
    assert projected == expected
    assert original == frozen
    assert Orchestrator._provider_single_ticker_valuation_projection(projected) == projected
    saved_chars = len(json.dumps(original)) - len(json.dumps(projected))
    assert saved_chars > len(json.dumps(original["valuation_research_context"]))


@pytest.mark.parametrize("case", [
    "multiple_companies", "different_value", "extra_map_field", "wrong_ticker",
    "missing_ticker", "missing_alias", "missing_map", "null_alias", "nonmapping_alias",
])
def test_distinct_or_incomplete_contexts_are_preserved(case):
    context = packet()
    contexts = context["valuation_research_contexts"]
    if case == "multiple_companies":
        contexts["OTHER"] = {"ticker": "OTHER", "unique_history": [1, 2, 3]}
    elif case == "different_value":
        contexts["ACME"]["historical_pe"]["points"][0]["multiple"] = "20.1"
    elif case == "extra_map_field":
        contexts["ACME"]["additional_evidence"] = "Must remain visible."
    elif case == "wrong_ticker":
        context["ticker"] = "OTHER"
    elif case == "missing_ticker":
        context.pop("ticker")
    elif case == "missing_alias":
        context.pop("valuation_research_context")
    elif case == "missing_map":
        context.pop("valuation_research_contexts")
    elif case == "null_alias":
        context["valuation_research_context"] = contexts["ACME"] = None
    elif case == "nonmapping_alias":
        context["valuation_research_context"] = contexts["ACME"] = ["history"]
    frozen = deepcopy(context)
    assert Orchestrator._provider_single_ticker_valuation_projection(context) == frozen
    assert context == frozen


def test_ordinary_analyst_and_reviewer_keep_original_budget_and_emit_counts(assessment_case, monkeypatch):
    repo, rid = ordinary(assessment_case)
    valuation = packet()["valuation_research_context"]
    valuation["unique_marker"] = "PRIVATE_TEST_CONTEXT_" * 150
    valuation_before = deepcopy(valuation)
    monkeypatch.setattr("backend.app.research.valuation_context.compile_valuation_context", lambda *args, **kwargs: valuation)
    expected_valuation = compact_valuation_context(valuation)
    provider = FakeProvider(answer)
    engine = Orchestrator(repo, FakeRegistry(provider), repo.config, laya_runtime=_DeterministicLaya())
    frozen_inputs = []
    original_projection = engine._provider_single_ticker_valuation_projection

    def project(context):
        # This boundary is after durable decision inputs are frozen. The
        # provider-only change must not rewrite those receipts or the run.
        run_before = dict(repo.run_record(rid))
        with repo.db.operation() as conn:
            attempts_before = [dict(row) for row in conn.execute("SELECT * FROM attempt_decision_inputs")]
        result = original_projection(context)
        assert dict(repo.run_record(rid)) == run_before
        with repo.db.operation() as conn:
            assert [dict(row) for row in conn.execute("SELECT * FROM attempt_decision_inputs")] == attempts_before
        frozen_inputs.append(deepcopy(context))
        return result

    monkeypatch.setattr(engine, "_provider_single_ticker_valuation_projection", project)
    for agent in ("A03", "A11"):
        task = next(task for task in repo.tasks_for_run(rid) if task["agent_id"] == agent)
        asyncio.run(engine._execute_task(rid, task))
        assert repo.task(task["id"])["status"] == "completed", repo.task(task["id"])["error"]
        supplied = provider.calls[-1]["packet"]
        assert supplied["valuation_research_context"] == expected_valuation
        assert "valuation_research_contexts" not in supplied
        # Preserve the previous allowance: reviews + exactly one map copy.
        reserved = len(json.dumps(supplied["earnings_reviews"], ensure_ascii=False))
        reserved += len(json.dumps({"ACME": expected_valuation}, ensure_ascii=False))
        assert supplied["evidence_projection"]["max_chars"] == max(16_000, 320_000 - reserved)
        assert supplied["evidence"] == frozen_inputs[-1]["evidence"]
        event = next(event for event in repo.events("real", run_id=rid)
                     if event["type"] == "investment_provider_packet" and event["task_id"] == task["id"])
        counts = event["payload"]
        assert set(counts) == {"message", "agent_id", "prompt_chars", "schema_chars", "context_chars", "block_chars", "source_count"}
        assert counts["agent_id"] == agent
        assert counts["context_chars"] == len(json.dumps(supplied, ensure_ascii=False))
        assert counts["prompt_chars"] > counts["context_chars"] > 0
        assert counts["schema_chars"] > 0
        assert 0 < len(counts["block_chars"]) <= 13
        assert "valuation_research_contexts" not in counts["block_chars"]
        for key, size in counts["block_chars"].items():
            assert isinstance(size, int) and size == len(json.dumps(supplied[key], ensure_ascii=False))
        assert "PRIVATE_TEST_CONTEXT" not in json.dumps(counts)
    assert len(frozen_inputs) == 2
    assert valuation == valuation_before
