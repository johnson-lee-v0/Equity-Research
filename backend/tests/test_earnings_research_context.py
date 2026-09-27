"""Earnings synthesis retains late-call Q&A and bound historical observations."""
from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.app.config import Settings
from backend.app.memory.repository import Repository
from backend.app.orchestration.provider_context import prepare_provider_context
from backend.app.orchestration.workflow import Orchestrator, build_research_tasks
from backend.app.providers.base import ProviderError
from backend.app.research.decision_questions import FIVE_QUESTION_CONTRACT
from backend.app.research.earnings_context import build_earnings_context, revision_packet_valid
from backend.app.research.earnings_trends import VALIDATION_VERSION
from backend.app.schemas import ImportRequest, RunCreate, RoutingPlan
from backend.tests.test_discovery_handoff import FakeProvider, FakeRegistry
from backend.tests.test_earnings_archive_fallback import attach_workflow
from backend.tests.test_five_question_commit_path import (
    create_five_question_case, _DeterministicProvider, _DeterministicRegistry,
    _DeterministicLaya, _provider_payloads, FIXTURE_AS_OF,
)


@pytest.fixture
def context_case(tmp_path):
    repo = Repository(config=Settings(project_root=Path(__file__).resolve().parents[2], data_dir=tmp_path,
                                      enable_market_connectors=False, enable_reddit_intake=False))
    quote = "At Q4 end our US and Canada renewal rate was 92.3%."
    capital = "For FY 2027 we are planning approximately $7.5 billion in capital expenditure."
    answer = "Management answer: renewal is normalizing; the present growth rate is more typical."
    content = "COST Q4 FY2026 earnings call\n" + "\n".join(f"Prepared remarks {i}: " + "archived context " * 15 for i in range(100)) + f"\n{quote}\n{capital}\nAnalyst question: is member growth sustainable?\n{answer}"
    source_ids = []
    for index, text in enumerate((content, "COST historical transcript\n" + "Historical retained remarks.\n" * 700)):
        source_ids.append(repo.import_evidence(ImportRequest(namespace="real", kind="evidence", title=f"COST earnings {index}",
            source_url=f"https://example.com/earnings/{index}", content=text, idempotency_key=f"earnings-context-source-{index}"))["source_id"])
    question = "Review COST earnings, historical renewal and capital spending."
    body = RunCreate(question=question, ticker="COST", namespace="real", source_ids=source_ids,
                     research_contract=FIVE_QUESTION_CONTRACT, idempotency_key="earnings-context-case")
    run, _ = repo.create_run(body, build_research_tasks(question, None, "COST", "real", lean=True))
    run_id = run["run_id"]
    repo.consume_routing_plan(run_id, RoutingPlan(intent="research", horizon="event", tickers=["COST"], selected_analysts=["A03"]))
    workflow_id = attach_workflow(repo, run_id)
    sources = repo.source_packet("real", source_ids)
    latest = next(source for source in sources if source["id"] == source_ids[0])
    with repo.db.transaction(immediate=True) as conn:
        package = json.loads(conn.execute("SELECT result_json FROM research_workflow_runs WHERE id=?", (workflow_id,)).fetchone()[0])
        package["documents"] = {"transcript": {"status": "available", "source_id": source_ids[0], "content_hash": latest["content_hash"]}}
        package["trends"] = {"validation_version": VALIDATION_VERSION, "status": "complete", "series": [
            {"id": "renewal_us_canada", "label": "US and Canada renewal", "unit": "percent", "frequency": "quarterly", "basis": "quarter end", "points": [
                {"period": "Q4 FY2026", "value": 92.3, "kind": "actual", "source_id": source_ids[0], "quote": quote}]},
            {"id": "capex", "label": "Capital spending", "unit": "USD billions", "frequency": "annual", "basis": "management plan", "points": [
                {"period": "FY2027", "value": 7.5, "kind": "guidance", "source_id": source_ids[0], "quote": capital}]},
        ]}
        conn.execute("UPDATE research_workflow_runs SET result_json=? WHERE id=?", (json.dumps(package), workflow_id))
    return repo, run_id, workflow_id, sources, source_ids, quote, capital, answer


def test_latest_call_and_verified_trends_reach_actual_provider_packet(context_case):
    repo, run_id, _, sources, ids, quote, capital, answer = context_case
    def capture(packet):
        raise ProviderError("capability", "Test stops after inspecting the prepared packet")
    provider = FakeProvider(capture)
    engine = Orchestrator(repo, FakeRegistry(provider), repo.config)
    a03 = next(task for task in repo.tasks_for_run(run_id) if task["agent_id"] == "A03")
    asyncio.run(engine._execute_task(run_id, a03))
    packet = provider.calls[0]["packet"]
    latest = next(source for source in packet["evidence"] if source["id"] == ids[0])
    assert all(value in latest["content"] for value in (quote, capital, answer))
    assert len(next(source for source in packet["evidence"] if source["id"] == ids[1])["content"]) <= 12000
    assert packet["evidence_projection"]["transcript_coverage"][ids[0]]["status"] == "complete"
    context = packet["earnings_context"]
    assert context["projection"]["omitted_items"] == context["projection"]["unbound_items"] == 0
    assert len(json.dumps(context, ensure_ascii=False)) + sum(len(source["content"]) for source in packet["evidence"]) <= 320000
    point = context["series"][0]["points"][0]
    original = next(source for source in sources if source["id"] == ids[0])
    assert point["quote"] == quote and point["source_id"] == ids[0]
    assert point["content_hash"] == original["content_hash"] and point["source_version"] == original["version"]
    assert original["content"].splitlines()[int(point["locator"][1:])-1] == quote
    assert "one atomic value" in packet["question"]
    receipt = repo.attempt_decision_inputs(provider.calls[0]["attempt_id"])["earnings_context_receipt"]
    assert receipt["source_bindings"][ids[0]]["content_hash"] == original["content_hash"]


@pytest.mark.parametrize("mutation", ["namespace", "version", "hash", "content", "missing_source", "workflow_link"])
def test_context_rejects_foreign_or_changed_source_packets(context_case, mutation):
    repo, run_id, workflow_id, sources, _, *_ = context_case
    changed = copy.deepcopy(sources)
    if mutation == "namespace": changed[0]["namespace"] = "demo"
    elif mutation == "version": changed[0]["version"] += 1
    elif mutation == "hash": changed[0]["content_hash"] = "wrong"
    elif mutation == "content": changed[0]["content"] += " changed"
    elif mutation == "missing_source": changed.pop()
    elif mutation == "workflow_link":
        with repo.db.transaction(immediate=True) as conn:
            conn.execute("UPDATE research_workflow_runs SET research_run_id=NULL WHERE id=?", (workflow_id,))
    assert build_earnings_context(repo, run_id, changed) is None


def test_unbound_trend_quote_is_omitted_and_reported(context_case):
    repo, run_id, workflow_id, sources, _, *_ = context_case
    with repo.db.transaction(immediate=True) as conn:
        package = json.loads(conn.execute("SELECT result_json FROM research_workflow_runs WHERE id=?", (workflow_id,)).fetchone()[0])
        package["trends"]["series"][0]["points"][0]["quote"] = "This fabricated quote is absent from the archive."
        conn.execute("UPDATE research_workflow_runs SET result_json=? WHERE id=?", (json.dumps(package), workflow_id))
    context = build_earnings_context(repo, run_id, sources)
    assert not context["series"][0]["points"]
    assert context["projection"]["unbound_items"] == 1 and context["projection"]["status"] == "partial"


def test_oversized_latest_transcript_reports_real_omissions(context_case):
    _, _, _, sources, ids, *_ = context_case
    changed = copy.deepcopy(sources)
    latest = next(source for source in changed if source["id"] == ids[0])
    latest["content"] += "\n" + "late Q&A " * 20000
    packet = prepare_provider_context({"earnings_context": {"latest_transcript_source_id": ids[0]}}, changed)
    evidence, projection = packet["evidence"], packet["evidence_projection"]
    coverage = projection["transcript_coverage"][ids[0]]
    assert coverage["status"] == "partial"
    assert coverage["omitted_line_ranges"] or coverage["partial_lines"]
    assert sum(len(source["content"]) for source in evidence) <= 320000


def test_completed_context_refresh_is_append_only_idempotent_and_does_not_dispatch(tmp_path, monkeypatch):
    case = create_five_question_case(tmp_path)
    repo, run_id = case.repository, case.run_id
    attach_workflow(repo, run_id)
    before_tasks = [dict(task) for task in repo.tasks_for_run(run_id)]
    before_outputs = [output["id"] for output in repo.latest_outputs(run_id)]
    repo.control("firm", None, "pause")
    assert repo.control("run", run_id, "refresh_earnings_context") == 2
    assert repo.control("run", run_id, "refresh_earnings_context") == 0
    tasks = repo.tasks_for_run(run_id)
    assert [dict(task) for task in tasks[:len(before_tasks)]] == before_tasks
    assert [output["id"] for output in repo.latest_outputs(run_id)] == before_outputs
    appended = tasks[len(before_tasks):]
    assert [task["agent_id"] for task in appended] == ["A03", "A11"]
    assert repo.task_dependency_states(appended[1]["id"]) == [{"id": appended[0]["id"], "status": "queued"}]
    assert repo.firm_paused() and repo.firm_dispatch_paused(run_id)
    assert repo.run_record(run_id)["status"] == "queued"
    with repo.db.operation() as conn:
        assert all(revision_packet_valid(conn, task["id"]) for task in appended)
        source_ids = {row["title"]: row["id"] for row in conn.execute("SELECT id,title FROM sources")}
    def forbidden(*args, **kwargs):
        raise AssertionError("A context-only reassessment must not fetch, replace heads, or add memory sources")
    monkeypatch.setattr(repo, "source_head_ids", forbidden)
    monkeypatch.setattr(repo, "prepare_task_memory", forbidden)
    prior_facts = next(output for output in repo.latest_outputs(run_id) if output["agent_id"] == "A03")["fact_claims"]
    aliases = {claim["claim_id"]: claim["fact_id"] for claim in prior_facts}
    def reuse_facts(value):
        if isinstance(value, dict): return {key: reuse_facts(item) for key, item in value.items()}
        if isinstance(value, list): return [reuse_facts(item) for item in value]
        return aliases.get(value, value) if isinstance(value, str) else value
    def revised_payload(packet):
        payload = _provider_payloads(source_ids["ABC synthetic 10-K filing"], source_ids["ABC synthetic market quote"])[packet["agent_id"]]
        raw = payload.model_dump()
        raw["fact_claims"] = []
        return type(payload).model_validate(reuse_facts(raw))
    provider = _DeterministicProvider(revised_payload)
    engine = Orchestrator(repo, _DeterministicRegistry(provider), repo.config, laya_runtime=_DeterministicLaya())
    monkeypatch.setattr(engine, "_prepare_market_evidence", forbidden)
    repo.control("run", run_id, "run_once")
    with patch("backend.app.memory.repository.utc_now", return_value=FIXTURE_AS_OF):
        asyncio.run(engine.run(run_id))
    assert [call["packet"]["agent_id"] for call in provider.calls] == ["A03", "A11"]
    assert repo.run_record(run_id)["status"] == "completed"
    assert len(repo.latest_outputs(run_id)) == len(before_outputs) + 2
    assert repo.firm_paused()


@pytest.mark.parametrize("mutation", ["task_packet", "workflow_link"])
def test_reassessment_rejects_changed_non_workflow_packet(tmp_path, mutation):
    case = create_five_question_case(tmp_path)
    repo, run_id = case.repository, case.run_id
    workflow_id = attach_workflow(repo, run_id)
    repo.control("run", run_id, "refresh_earnings_context")
    revision = next(task for task in repo.tasks_for_run(run_id) if task["kind"].startswith("research_synthesis_earnings_revision_"))
    with repo.db.transaction(immediate=True) as conn:
        if mutation == "task_packet":
            ids = json.loads(revision["input_refs_json"])
            conn.execute("UPDATE tasks SET input_refs_json=? WHERE id=?", (json.dumps(ids[:-1]), revision["id"]))
        else:
            conn.execute("UPDATE research_workflow_runs SET research_run_id=NULL WHERE id=?", (workflow_id,))
        assert not revision_packet_valid(conn, revision["id"])


def test_active_case_cannot_start_context_refresh(context_case):
    repo, run_id, *_ = context_case
    with pytest.raises(ValueError, match="finish"):
        repo.control("run", run_id, "refresh_earnings_context")
