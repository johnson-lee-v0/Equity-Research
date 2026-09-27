"""Explicit ticker acquisition avoids a redundant universe-search model call."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from backend.app.config import Settings
from backend.app.db import json_loads
from backend.app.memory.repository import Repository
from backend.app.orchestration.workflow import Orchestrator, _explicit_ticker_preparation, build_research_tasks
from backend.app.research.earnings_context import verified_earnings_package
from backend.app.research.investment_process import ProcessPaused, ensure_latest_earnings
from backend.app.research.workflows import ResearchWorkflows
from backend.app.schemas import ModelConfig, RoutingPlan, RunCreate
from backend.tests.test_discovery_handoff import FakeProvider, FakeRegistry, output_payload
from backend.tests.test_investment_earnings_process import VerifiedAcquisition, IndexedTrends


@pytest.fixture
def setup(tmp_path):
    config = Settings(project_root=Path(__file__).resolve().parents[2], data_dir=tmp_path,
                      enable_market_connectors=False, enable_reddit_intake=False)
    repo = Repository(config=config)
    acquisition = VerifiedAcquisition(repo)
    service = ResearchWorkflows(repo, None, config, acquisition_factory=lambda namespace: acquisition,
                                trends_factory=IndexedTrends)
    provider = FakeProvider(lambda packet: output_payload())
    engine = Orchestrator(repo, FakeRegistry(provider), config)
    engine.earnings_workflows = service
    return repo, acquisition, service, provider, engine


def case(repo, ticker="NKE", *, route=None, key="case"):
    question = f"Research {ticker or 'companies'} using earnings, five questions, pricing and a decision."
    body = RunCreate(question=question, ticker=ticker, horizon="12 months", namespace="real",
                     research_contract="five-questions.v1", idempotency_key="explicit-ticker-" + key)
    run, _ = repo.create_run(body, build_research_tasks(question, body.horizon, ticker, "real", lean=True),
                             allow_semantic_reuse=False)
    repo.consume_routing_plan(run["run_id"], RoutingPlan(intent="research", horizon="12 months",
        tickers=route if route is not None else [ticker], selected_analysts=["A03"],
        research_queries=["Latest reported earnings and material public updates"]))
    return run["run_id"], next(t for t in repo.tasks_for_run(run["run_id"]) if t["agent_id"] == "A01")


def execute(engine, run_id, task_id):
    return asyncio.run(engine._execute_task(run_id, engine.repository.task(task_id)))


@pytest.mark.parametrize("ticker", ["NKE", "COST", "BRK.B"])
def test_explicit_ticker_prepares_bound_sources_without_universe_search(setup, ticker):
    repo, acquisition, service, provider, engine = setup
    rid, task = case(repo, ticker)
    original = dict(repo.run_record(rid))
    execute(engine, rid, task["id"])
    saved = repo.run_snapshot(rid)
    assert repo.task(task["id"])["status"] == "completed"
    assert provider.calls == []
    assert acquisition.calls == ["resolve", "locate", "acquire"]
    with repo.db.operation() as conn:
        package = verified_earnings_package(conn, rid, ticker=ticker)
        attempt = conn.execute("SELECT * FROM task_attempts WHERE task_id=?", (task["id"],)).fetchone()
        output = conn.execute("SELECT payload_json FROM outputs WHERE task_id=?", (task["id"],)).fetchone()
    assert package and package["ticker"] == ticker
    assert attempt["provider"] == "deterministic" and attempt["status"] == "completed"
    assert set(json_loads(attempt["source_versions_json"])) == set(package["source_bindings"])
    assert {item["ticker"] for item in saved["research_candidates"]} == {ticker}
    assert saved["research_candidates"][0]["evidence_available"]
    assert not saved["research_candidates"][0]["verified"]
    assert set(json_loads(output[0])["source_refs"]) == set(package["source_bindings"])
    for field in ("request", "horizon", "origin", "origin_ref", "ticker"):
        assert repo.run_record(rid)[field] == original[field]
    a03 = next(t for t in repo.tasks_for_run(rid) if t["agent_id"] == "A03")
    assert set(package["source_bindings"]) <= set(json_loads(a03["input_refs_json"]))
    # The ordinary synthesis prerequisite must reuse this exact package.
    prior = saved["investment_process"]["earnings"][0]
    asyncio.run(ensure_latest_earnings(service, rid, [ticker], task_id=a03["id"]))
    assert repo.run_snapshot(rid)["investment_process"]["earnings"][0] == prior
    assert acquisition.calls == ["resolve", "locate", "acquire"]


def test_missing_transcript_and_periodic_filing_are_preserved_gaps(setup):
    repo, acquisition, _, provider, engine = setup
    acquisition.missing = {"transcript", "current_filing"}
    rid, task = case(repo)
    execute(engine, rid, task["id"])
    assert repo.task(task["id"])["status"] == "completed" and not provider.calls
    snapshot = repo.run_snapshot(rid)
    assert snapshot["investment_process"]["earnings"][0]["status"] == "partial"
    with repo.db.operation() as conn:
        payload = json_loads(conn.execute("SELECT payload_json FROM outputs WHERE task_id=?", (task["id"],)).fetchone()[0])
    assert any("transcript" in gap for gap in payload["missing_data"])
    assert any("current_filing" in gap for gap in payload["missing_data"])
    assert not payload["fact_claims"] and not payload["calculations"]


@pytest.mark.parametrize("failure", ["invalid", "wrong_issuer", "unavailable"])
def test_unverified_or_unavailable_issuer_package_honestly_blocks(setup, failure):
    repo, acquisition, _, provider, engine = setup
    if failure == "invalid":
        async def invalid(ticker):
            acquisition.calls.append("resolve")
            raise ValueError("SEC submissions did not confirm the requested ticker.")
        acquisition.resolve = invalid
    elif failure == "wrong_issuer":
        original = acquisition.resolve
        async def wrong(ticker):
            return (await original(ticker)) | {"ticker": "OTHER"}
        acquisition.resolve = wrong
    else:
        acquisition.fail_locate = True
    rid, task = case(repo, "BAD" if failure == "invalid" else "NKE")
    execute(engine, rid, task["id"])
    assert repo.run_record(rid)["status"] == "blocked"
    assert repo.task(task["id"])["status"] == "blocked"
    assert "no current SEC-identified, primary-release-verified earnings package" in repo.task(task["id"])["error"]
    assert not provider.calls
    with repo.db.operation() as conn:
        assert conn.execute("SELECT COUNT(*) FROM outputs WHERE task_id=?", (task["id"],)).fetchone()[0] == 0
    assert not repo.run_snapshot(rid)["research_candidates"]


@pytest.mark.parametrize("ticker,route", [(None, ["NKE"]), ("NKE", ["COST"]), ("NKE", ["NKE", "COST"])])
def test_unrequested_mismatched_and_multiple_routes_keep_bounded_discovery(setup, ticker, route):
    repo, acquisition, service, provider, engine = setup
    rid, task = case(repo, ticker, route=route)
    run = repo.run_record(rid)
    assert _explicit_ticker_preparation(run, task, json_loads(run["input_snapshot_json"])) is None
    execute(engine, rid, task["id"])
    assert len(provider.calls) == 1
    assert acquisition.calls == [] and service.store.history() == []


def test_pause_then_scoped_run_once_prepares_only_selected_case(setup):
    repo, acquisition, service, provider, engine = setup
    rid, task = case(repo)
    other, _ = case(repo, "COST", key="other")
    repo.control("firm", None, "pause")
    with pytest.raises(ProcessPaused):
        execute(engine, rid, task["id"])
    assert not acquisition.calls and not service.store.history()
    repo.control("run", rid, "run_once")
    execute(engine, rid, task["id"])
    assert repo.task(task["id"])["status"] == "completed" and not provider.calls
    assert repo.firm_paused() and repo.run_record(other)["status"] == "paused"


def test_pause_checkpoint_resumes_without_resolving_issuer_again(setup):
    repo, acquisition, service, _, engine = setup
    rid, task = case(repo)
    original = acquisition.resolve
    async def pause(ticker):
        result = await original(ticker)
        repo.control("firm", None, "pause")
        return result
    acquisition.resolve = pause
    with pytest.raises(ProcessPaused):
        execute(engine, rid, task["id"])
    assert acquisition.calls == ["resolve"]
    repo.control("run", rid, "run_once")
    # A new orchestration/service instance reads the same durable checkpoint.
    resumed = ResearchWorkflows(repo, None, repo.config, acquisition_factory=lambda ns: acquisition, trends_factory=IndexedTrends)
    engine.earnings_workflows = resumed
    execute(engine, rid, task["id"])
    assert repo.task(task["id"])["status"] == "completed"
    assert acquisition.calls == ["resolve", "locate", "acquire"]


def test_cancel_during_collection_never_commits_a_late_preparation(setup):
    repo, acquisition, service, _, engine = setup
    rid, task = case(repo)
    original = acquisition.resolve
    async def cancel(ticker):
        result = await original(ticker)
        repo.control("run", rid, "cancel")
        return result
    acquisition.resolve = cancel
    with pytest.raises(ProcessPaused):
        execute(engine, rid, task["id"])
    assert repo.run_record(rid)["status"] == "cancelled"
    assert repo.task(task["id"])["status"] == "cancelled"
    assert acquisition.calls == ["resolve"]
    with repo.db.operation() as conn:
        assert conn.execute("SELECT COUNT(*) FROM outputs WHERE task_id=?", (task["id"],)).fetchone()[0] == 0


def test_explicit_retry_resumes_failed_prerequisite_and_retains_attempt_history(setup):
    repo, acquisition, service, _, engine = setup
    acquisition.fail_locate = True
    rid, task = case(repo)
    execute(engine, rid, task["id"])
    failed_receipt = repo.run_snapshot(rid)["investment_process"]["earnings"][0]
    acquisition.fail_locate = False
    # No retry event means the unavailable receipt cannot create more work.
    assert asyncio.run(ensure_latest_earnings(service, rid, ["NKE"], task_id=task["id"], retry_unavailable=True)) == []
    assert acquisition.calls == ["resolve", "locate"]
    repo.control("task", task["id"], "retry")
    execute(engine, rid, task["id"])
    assert repo.task(task["id"])["status"] == "completed"
    assert acquisition.calls == ["resolve", "locate", "locate", "acquire"]
    receipt = repo.run_snapshot(rid)["investment_process"]["earnings"][0]
    assert receipt["workflow_id"] == failed_receipt["workflow_id"] and receipt["retry_event_id"]
    with repo.db.operation() as conn:
        statuses = [row[0] for row in conn.execute("SELECT status FROM task_attempts WHERE task_id=? ORDER BY attempt_no", (task["id"],))]
        assert statuses == ["blocked", "completed"]
        assert conn.execute("SELECT 1 FROM research_workflow_events WHERE run_id=? AND status='failed'", (receipt["workflow_id"],)).fetchone()


def test_failed_retry_authorization_cannot_be_replayed(setup):
    repo, acquisition, service, _, engine = setup
    acquisition.fail_locate = True
    rid, task = case(repo)
    execute(engine, rid, task["id"])
    repo.control("task", task["id"], "retry")
    execute(engine, rid, task["id"])
    calls = list(acquisition.calls)
    assert asyncio.run(ensure_latest_earnings(service, rid, ["NKE"], task_id=task["id"], retry_unavailable=True)) == []
    assert acquisition.calls == calls


def test_retry_before_first_collection_is_consumed_even_if_collection_fails(setup):
    repo, acquisition, service, _, engine = setup
    acquisition.fail_locate = True
    rid, task = case(repo)
    config = ModelConfig(provider="codex", model="gpt-5.6-luna", reasoning_effort="max")
    attempt = repo.create_attempt(task["id"], config, {})
    repo.mark_task_failure(task["id"], attempt["attempt_id"], "blocked", "Prior bounded discovery failed.")
    repo.set_run_status(rid, "blocked", error="Prior bounded discovery failed.")
    repo.control("task", task["id"], "retry")
    execute(engine, rid, task["id"])
    receipt = repo.run_snapshot(rid)["investment_process"]["earnings"][0]
    assert receipt["status"] == "unavailable" and receipt["retry_event_id"]
    assert acquisition.calls == ["resolve", "locate"]
    assert asyncio.run(ensure_latest_earnings(service, rid, ["NKE"], task_id=task["id"], retry_unavailable=True)) == []
    assert acquisition.calls == ["resolve", "locate"]


def test_sec_verified_fund_uses_ordinary_discovery_without_fabricated_earnings(setup):
    repo, acquisition, _, provider, engine = setup
    original = acquisition.resolve
    async def fund(ticker):
        return (await original(ticker)) | {"earnings_applicability": "not_applicable"}
    acquisition.resolve = fund
    rid, task = case(repo, "SPY")
    execute(engine, rid, task["id"])
    assert acquisition.calls == ["resolve"]
    assert len(provider.calls) == 1
    assert repo.task(task["id"])["status"] == "completed"
    assert repo.run_snapshot(rid)["investment_process"]["earnings"][0]["status"] == "not_applicable"


@pytest.mark.parametrize("ticker,alias", [("BRK.B", "BRK-B"), ("BRK-B", "BRK.B")])
def test_share_class_alias_route_is_one_requested_security(setup, ticker, alias):
    repo, acquisition, _, provider, engine = setup
    rid, task = case(repo, ticker, route=[alias])
    route = json_loads(repo.run_record(rid)["input_snapshot_json"])["routing_plan"]
    assert route["tickers"] == [ticker]
    execute(engine, rid, task["id"])
    assert repo.task(task["id"])["status"] == "completed"
    assert not provider.calls and acquisition.calls == ["resolve", "locate", "acquire"]
    assert [row["ticker"] for row in repo.run_snapshot(rid)["research_candidates"]] == [ticker]


def test_distinct_share_classes_and_issuers_remain_distinct_routes(setup):
    repo, acquisition, _, provider, engine = setup
    rid, task = case(repo, "BRK.B", route=["BRK-B", "BRK.A", "COST"])
    snapshot = json_loads(repo.run_record(rid)["input_snapshot_json"])
    assert snapshot["routing_plan"]["tickers"] == ["BRK.B", "BRK.A", "COST"]
    assert _explicit_ticker_preparation(repo.run_record(rid), task, snapshot) is None
    execute(engine, rid, task["id"])
    assert len(provider.calls) == 1 and not acquisition.calls


def test_completed_package_cannot_be_silently_refreshed_after_source_tampering(setup):
    repo, acquisition, service, _, engine = setup
    rid, task = case(repo)
    execute(engine, rid, task["id"])
    receipt = repo.run_snapshot(rid)["investment_process"]["earnings"][0]
    with repo.db.transaction(immediate=True) as conn:
        sid = next(iter(verified_earnings_package(conn, rid)["source_bindings"]))
        conn.execute("UPDATE sources SET original_content=original_content||' changed' WHERE id=?", (sid,))
    a03 = next(t for t in repo.tasks_for_run(rid) if t["agent_id"] == "A03")
    with pytest.raises(ValueError, match="frozen earnings evidence changed"):
        asyncio.run(ensure_latest_earnings(service, rid, ["NKE"], task_id=a03["id"], retry_unavailable=True))
    assert acquisition.calls == ["resolve", "locate", "acquire"]
    assert repo.run_snapshot(rid)["investment_process"]["earnings"][0] == receipt
