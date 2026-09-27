"""Automatic earnings prerequisites preserve case identity and dispatch gates."""
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.app.config import Settings
from backend.app.db import json_dumps, json_loads, utc_now
from backend.app.memory.repository import Repository
from backend.app.orchestration.workflow import Orchestrator, build_research_tasks
from backend.app.research.earnings_context import build_earnings_context, verified_earnings_package
from backend.app.research.investment_process import ProcessPaused, _verified_workflow, ensure_latest_earnings
from backend.app.research.library_store import LibraryStore
from backend.app.research.workflows import ResearchWorkflows
from backend.app.schemas import RunCreate
from backend.tests.test_research_workflows import Acquisition, Trends


class VerifiedAcquisition(Acquisition):
    async def resolve(self, ticker):
        result = await super().resolve(ticker)
        return result | {"verified_at": utc_now()}

    async def locate(self, company):
        return (await super().locate(company)) | {"verification": "primary_release", "located_at": utc_now()}

    async def acquire(self, company, event):
        result = await super().acquire(company, event)
        if company["ticker"] == "OTHER":
            from backend.app.research.discovery import FetchedSource
            from backend.app.research.source_archive import archive_public_page
            for document in result["documents"].values():
                if document.get("content"):
                    document["content"] += "\nIssuer ticker: OTHER"
                    retained = archive_public_page(self.repo, FetchedSource(document["url"], document["url"], document["content"], document["title"], utc_now()), namespace="real", scope="test-other")
                    document["source_id"] = retained["source_id"]
        sources = {item["id"]: item for item in self.repo.source_packet(self.namespace, [doc["source_id"] for doc in result["documents"].values() if doc.get("source_id")])}
        for document in result["documents"].values():
            if document.get("source_id"):
                document["content_hash"] = sources[document["source_id"]]["content_hash"]
        return result


class IndexedTrends(Trends):
    async def collect(self, company, event, documents):
        # Real descriptive trend indexes intentionally contain no archive hash.
        source = documents["release"]
        return {"version": "earnings-trends.v1", "status": "available", "series": [], "gaps": [],
                "sources": [{key: source[key] for key in ("source_id", "url", "title")} ]}


@pytest.fixture
def setup(tmp_path):
    config = Settings(data_dir=tmp_path, project_root=Path(__file__).resolve().parents[2], enable_market_connectors=False)
    repo = Repository(config=config)
    acquisition = VerifiedAcquisition(repo)
    service = ResearchWorkflows(repo, None, config, acquisition_factory=lambda namespace: acquisition, trends_factory=IndexedTrends)
    return repo, acquisition, service


def new_case(repo, key="case", ticker="ACME"):
    body = RunCreate(question="Would this fit my portfolio over 24 months?", ticker=ticker, horizon="24 months",
                     research_contract="five-questions.v1", idempotency_key="earnings-" + key)
    result, _ = repo.create_run(body, build_research_tasks(body.question, body.horizon, ticker, "real", lean=True), allow_semantic_reuse=False)
    return result["run_id"], repo.tasks_for_run(result["run_id"])[0]["id"]


def collect(service, rid, tid, tickers=None):
    return asyncio.run(ensure_latest_earnings(service, rid, tickers or ["ACME"], task_id=tid))


def test_regular_case_collects_earnings_before_questions_without_replacing_request(setup):
    repo, acquisition, service = setup
    rid, tid = new_case(repo)
    before = json_loads(repo.run_record(rid)["input_snapshot_json"], {})
    ids = collect(service, rid, tid)
    snapshot = json_loads(repo.run_record(rid)["input_snapshot_json"], {})
    assert acquisition.calls == ["resolve", "locate", "acquire"]
    assert ids and set(ids) <= set(snapshot["source_ids"])
    assert snapshot["requested_source_ids"] == []
    for field in ("question", "horizon", "requested_horizon", "origin", "origin_ref"):
        assert snapshot[field] == before[field]
    receipt = snapshot["investment_process"]["earnings"][0]
    assert receipt["status"] == "completed"
    assert _verified_workflow(repo, service.store.get(receipt["workflow_id"]), "real", "ACME")
    with repo.db.operation() as conn:
        assert verified_earnings_package(conn, rid)["workflow_id"] == receipt["workflow_id"]
    context = build_earnings_context(repo, rid, repo.source_packet("real", ids))
    assert context["ticker"] == "ACME" and context["latest_transcript_source_id"] in ids
    assert repo.run_snapshot(rid)["investment_process"]["earnings"] == [receipt]
    with repo.db.operation() as conn:
        entry = dict(conn.execute("SELECT * FROM research_library_entries WHERE ticker='ACME'").fetchone())
        runs, _ = LibraryStore._activity(conn, entry)
    assert next(run for run in runs if run["id"] == rid)["investment_process"]["earnings"] == [receipt]


def test_recent_package_and_same_case_retry_do_not_repeat_collection(setup):
    repo, acquisition, service = setup
    rid, tid = new_case(repo)
    first = collect(service, rid, tid)
    rid2, tid2 = new_case(repo, "second")
    assert collect(service, rid2, tid2) == first
    assert repo.run_snapshot(rid2)["investment_process"]["earnings"][0]["reused"]
    assert collect(service, rid2, tid2) == first
    assert acquisition.calls == ["resolve", "locate", "acquire"]


def test_new_case_rejects_cached_transcript_with_no_attributable_answers(setup, monkeypatch):
    repo, acquisition, service = setup
    from backend.tests import test_research_workflows as fixtures
    with monkeypatch.context() as patch:
        patch.setattr(fixtures, "TRANSCRIPT", "Operator\nWelcome to the call.\n" +
                      "We discussed results and answered questions.\n" * 80)
        identifier = service.store.create("ACME", idempotency_key="old-unsupported-layout")
        asyncio.run(service.execute(identifier))
    prior = service.store.get(identifier)
    assert prior["status"] == "completed"
    assert not _verified_workflow(repo, prior, "real", "ACME")
    rid, tid = new_case(repo, "after-reader-correction")
    assert collect(service, rid, tid)
    receipt = repo.run_snapshot(rid)["investment_process"]["earnings"][0]
    assert receipt["workflow_id"] != identifier and not receipt["reused"]
    assert service.store.get(identifier)["result"] == prior["result"]


def test_reader_quality_change_does_not_rewrite_a_frozen_case(setup, monkeypatch):
    repo, acquisition, service = setup
    rid, tid = new_case(repo)
    ids = collect(service, rid, tid)
    receipt = repo.run_snapshot(rid)["investment_process"]["earnings"][0]
    prior = service.store.get(receipt["workflow_id"])
    monkeypatch.setattr("backend.app.research.earnings_sources.transcript_structure_rejection",
                        lambda content: "A newer reader cannot interpret this layout.")
    assert not _verified_workflow(repo, prior, "real", "ACME")
    assert collect(service, rid, tid) == ids
    assert service.store.get(receipt["workflow_id"])["result"] == prior["result"]


def test_source_only_republication_does_not_make_old_event_check_fresh(setup):
    repo, acquisition, service = setup
    rid, tid = new_case(repo)
    collect(service, rid, tid)
    wf = repo.run_snapshot(rid)["investment_process"]["earnings"][0]["workflow_id"]
    package = service.store.get(wf)["result"]
    package["event"]["located_at"] = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    package["as_of"] = utc_now()
    with repo.db.transaction(immediate=True) as conn:
        conn.execute("UPDATE research_workflow_runs SET result_json=? WHERE id=?", (json_dumps(package), wf))
    rid2, tid2 = new_case(repo, "second")
    collect(service, rid2, tid2)
    assert acquisition.calls.count("locate") == 2
    assert repo.run_snapshot(rid2)["investment_process"]["earnings"][0]["workflow_id"] != wf


def test_frozen_case_rejects_changed_package_instead_of_recollecting(setup):
    repo, acquisition, service = setup
    rid, tid = new_case(repo)
    collect(service, rid, tid)
    wf = repo.run_snapshot(rid)["investment_process"]["earnings"][0]["workflow_id"]
    package = service.store.get(wf)["result"]
    package["event"]["fiscal_period"] = "CHANGED"
    with repo.db.transaction(immediate=True) as conn:
        conn.execute("UPDATE research_workflow_runs SET result_json=? WHERE id=?", (json_dumps(package), wf))
    with pytest.raises(ValueError, match="frozen earnings evidence changed"):
        collect(service, rid, tid)
    assert acquisition.calls.count("locate") == 1


def test_paused_firm_requires_parent_run_grant_and_preserves_other_queue(setup):
    repo, acquisition, service = setup
    rid, tid = new_case(repo)
    other, _ = new_case(repo, "other", "OTHER")
    repo.control("firm", None, "pause")
    with pytest.raises(ProcessPaused):
        collect(service, rid, tid)
    assert acquisition.calls == [] and service.store.history() == []
    repo.control("run", rid, "run_once")
    assert collect(service, rid, tid)
    assert repo.firm_paused() and repo.run_record(other)["status"] == "paused"


def test_pause_between_stages_keeps_checkpoint_and_resumes_without_identity_retry(setup):
    repo, acquisition, service = setup
    rid, tid = new_case(repo)
    original = acquisition.resolve
    async def pause_after_resolve(ticker):
        result = await original(ticker)
        repo.control("firm", None, "pause")
        return result
    acquisition.resolve = pause_after_resolve
    with pytest.raises(ProcessPaused):
        collect(service, rid, tid)
    assert acquisition.calls == ["resolve"]
    workflow = service.store.get(repo.run_snapshot(rid)["investment_process"]["earnings"][0]["workflow_id"])
    assert workflow["status"] == "queued"
    assert next(step for step in workflow["steps"] if step["id"] == "resolve")["status"] == "completed"
    repo.control("run", rid, "run_once")
    assert collect(service, rid, tid)
    assert acquisition.calls == ["resolve", "locate", "acquire"]


def test_restart_does_not_independently_schedule_parent_owned_earnings(setup, monkeypatch):
    repo, _, service = setup
    rid, _ = new_case(repo)
    owned = service.store.create("ACME", idempotency_key=f"investment-process:{rid}:ACME")
    standalone = service.store.create("OTHER")
    scheduled = []
    monkeypatch.setattr(service, "schedule", scheduled.append)
    service.recover()
    assert scheduled == [standalone] and owned not in scheduled


@pytest.mark.parametrize("fund", [False, True])
def test_missing_or_inapplicable_earnings_remain_explicit_and_bounded(setup, fund):
    repo, acquisition, service = setup
    rid, tid = new_case(repo)
    if fund:
        original = acquisition.resolve
        async def resolve(ticker):
            return (await original(ticker)) | {"earnings_applicability": "not_applicable"}
        acquisition.resolve = resolve
    else:
        acquisition.fail_locate = True
    assert collect(service, rid, tid) == []
    receipt = repo.run_snapshot(rid)["investment_process"]["earnings"][0]
    assert receipt["status"] == ("not_applicable" if fund else "unavailable")
    assert receipt["gaps"]
    calls = list(acquisition.calls)
    assert collect(service, rid, tid) == [] and acquisition.calls == calls
    assert "acquire" not in acquisition.calls


def test_multiple_tickers_have_distinct_frozen_contexts(setup):
    repo, _, service = setup
    rid, tid = new_case(repo, ticker=None)
    ids = collect(service, rid, tid, ["ACME", "OTHER"])
    sources = repo.source_packet("real", ids)
    with repo.db.operation() as conn:
        acme = verified_earnings_package(conn, rid, ticker="ACME")
        other = verified_earnings_package(conn, rid, ticker="OTHER")
        assert verified_earnings_package(conn, rid, ticker="UNREQUESTED") is None
    assert acme["workflow_id"] != other["workflow_id"]
    assert not set(acme["source_bindings"]) & set(other["source_bindings"])
    assert build_earnings_context(repo, rid, sources, ticker="ACME")["ticker"] == "ACME"
    assert build_earnings_context(repo, rid, sources, ticker="OTHER")["ticker"] == "OTHER"


def test_partial_package_used_by_regular_case_cannot_be_retried_in_place(setup):
    repo, acquisition, service = setup
    acquisition.missing = {"transcript"}
    rid, tid = new_case(repo)
    assert collect(service, rid, tid)
    receipt = repo.run_snapshot(rid)["investment_process"]["earnings"][0]
    assert receipt["status"] == "partial"
    prior = service.store.get(receipt["workflow_id"])
    with pytest.raises(ValueError, match="frozen in an investment review"):
        service.store.retry(receipt["workflow_id"])
    assert service.store.get(receipt["workflow_id"])["result"] == prior["result"]


def test_concurrent_cases_share_one_collection_without_duplicate_model_work(setup):
    repo, acquisition, service = setup
    rid, tid = new_case(repo)
    other, other_task = new_case(repo, "second")
    async def concurrent():
        acquisition.block = asyncio.Event()
        first = asyncio.create_task(ensure_latest_earnings(service, rid, ["ACME"], task_id=tid))
        second = asyncio.create_task(ensure_latest_earnings(service, other, ["ACME"], task_id=other_task))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        acquisition.block.set()
        return await asyncio.gather(first, second)
    first, second = asyncio.run(concurrent())
    assert first and first == second
    assert acquisition.calls == ["resolve", "locate", "acquire"]


def test_second_case_cannot_override_paused_workflow_owner_authorization(setup):
    repo, acquisition, service = setup
    owner, _ = new_case(repo)
    other, other_task = new_case(repo, "second")
    wf = service.store.create("ACME", idempotency_key=f"investment-process:{owner}:ACME")
    repo.control("firm", None, "pause")
    repo.control("run", other, "run_once")
    with pytest.raises(ProcessPaused):
        collect(service, other, other_task)
    assert acquisition.calls == [] and service.store.get(wf)["status"] == "queued"


def test_cancelled_parent_does_not_leave_an_orphan_workflow_in_queue(setup):
    repo, acquisition, service = setup
    rid, tid = new_case(repo)
    original = acquisition.resolve
    async def cancel_after_identity(ticker):
        value = await original(ticker)
        repo.control("run", rid, "cancel")
        return value
    acquisition.resolve = cancel_after_identity
    with pytest.raises(ProcessPaused):
        collect(service, rid, tid)
    assert service.store.history()[0]["status"] == "cancelled"
    assert acquisition.calls == ["resolve"]


def test_documents_cancel_stops_child_and_retains_explicit_gap_for_parent(setup):
    repo, acquisition, service = setup
    rid, tid = new_case(repo)
    async def cancel_collection():
        acquisition.block = asyncio.Event()
        pending = asyncio.create_task(ensure_latest_earnings(service, rid, ["ACME"], task_id=tid))
        for _ in range(5):
            await asyncio.sleep(0)
        identifier = repo.run_snapshot(rid)["investment_process"]["earnings"][0]["workflow_id"]
        assert identifier in service.tasks
        await service.cancel(identifier)
        return await pending, identifier
    ids, identifier = asyncio.run(cancel_collection())
    assert ids == [] and acquisition.calls == ["resolve"]
    assert service.store.get(identifier)["status"] == "cancelled"
    assert repo.run_record(rid)["status"] == "queued"
    assert repo.run_snapshot(rid)["investment_process"]["earnings"][0]["status"] == "unavailable"


def test_frozen_attempt_receipt_selects_original_package_after_run_snapshot_changes(setup):
    repo, _, service = setup
    rid, tid = new_case(repo)
    collect(service, rid, tid)
    snapshot = json_loads(repo.run_record(rid)["input_snapshot_json"], {})
    receipt = dict(snapshot["investment_process"]["earnings"][0])
    snapshot["investment_process"]["earnings"][0]["package_hash"] = "changed-by-later-run-refresh"
    with repo.db.transaction(immediate=True) as conn:
        conn.execute("UPDATE runs SET input_snapshot_json=? WHERE id=?", (json_dumps(snapshot), rid))
        assert verified_earnings_package(conn, rid) is None
        assert verified_earnings_package(conn, rid, ticker="ACME", frozen_receipt=receipt)["workflow_id"] == receipt["workflow_id"]


def test_full_regular_five_question_run_receives_earnings_and_pricing_context(tmp_path, monkeypatch):
    from backend.tests import test_five_question_commit_path as fixture
    original_init = Orchestrator.__init__
    services = []
    def initialize(engine, repository, providers, config, **kwargs):
        original_init(engine, repository, providers, config, **kwargs)
        acquisition = VerifiedAcquisition(repository)
        services.append(ResearchWorkflows(repository, providers, config, engine=engine,
            acquisition_factory=lambda namespace: acquisition, trends_factory=IndexedTrends))
    async def no_network(*args, **kwargs):
        return []
    monkeypatch.setattr(Orchestrator, "__init__", initialize)
    monkeypatch.setattr("backend.app.orchestration.workflow.acquire_financial_baseline", no_network)
    case = fixture.create_five_question_case(tmp_path)
    assert case.repository.run_record(case.run_id)["status"] == "completed"
    packets = [call["packet"] for call in case.provider.calls]
    assert [packet["agent_id"] for packet in packets] == ["A00", "A03", "A11"]
    a01 = next(task for task in case.repository.tasks_for_run(case.run_id) if task["agent_id"] == "A01")
    with case.repository.db.operation() as conn:
        attempt = conn.execute("SELECT * FROM task_attempts WHERE task_id=?", (a01["id"],)).fetchone()
        package = verified_earnings_package(conn, case.run_id, ticker="ABC")
    assert attempt["provider"] == "deterministic" and attempt["status"] == "completed"
    assert set(json_loads(attempt["source_versions_json"])) == set(package["source_bindings"])
    for packet in packets[-2:]:
        assert packet["investment_process"]["earnings"][0]["status"] == "completed"
        assert packet["earnings_reviews"][0]["ticker"] == "ABC"
        assert packet["valuation_research_context"]["ticker"] == "ABC"
        assert "valuation_research_contexts" not in packet
        assert any("Questions and Answers" in source["content"] for source in packet["evidence"])
    assert len(case.decision["candidates"][0]["key_questions"]) == 5
    assert case.decision["candidates"][0]["valuation"]["research_context"]["ticker"] == "ABC"
