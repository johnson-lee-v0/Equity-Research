"""An optional timed-out synthesis cannot erase the usable original review."""
import asyncio
import json

import pytest

from backend.app.db import json_loads
from backend.app.orchestration.workflow import Orchestrator
from backend.app.providers.base import ProviderError
from backend.app.research.case_store import CaseDecisionStore
from backend.app.research.synthesis_timeout_fallback import activate_fallback, EVENT, LIMITATION, TIMEOUT
from backend.app.schemas import AgentOutputPayload, ImportRequest, MissingGap
from backend.tests.test_assessment_pipeline import case as assessment_case
from backend.tests.test_discovery_handoff import FakeProvider, FakeRegistry
from backend.tests.test_five_question_commit_path import _DeterministicLaya
from backend.tests.test_investment_valuation import answer, ordinary


@pytest.fixture
def prepared(assessment_case, request):
    repo, rid = ordinary(assessment_case)
    def respond(packet):
        task = repo.task(packet["task_id"])
        if task["kind"] == "research_synthesis_continuation_1":
            raise ProviderError("timeout", TIMEOUT, retryable=True)
        result = answer(packet)
        if task["kind"] == "research_synthesis":
            result.missing_gaps = [MissingGap(key="latest_issuer_filing", description="The latest issuer filing comparison remains unknown.",
                relevant_role="A03", reopen_when="A dated issuer filing supplies the comparison.")]
        return result
    provider = FakeProvider(respond)
    engine = Orchestrator(repo, FakeRegistry(provider), repo.config, laya_runtime=_DeterministicLaya())
    # Commit the already prepared intake/identity fixture so the real run
    # completion barrier can also be exercised after the final CIO review.
    source_ids = json_loads(repo.run_record(rid)["input_snapshot_json"], {})["source_ids"]
    versions = {row["id"]: {"version": row["version"], "hash": row["content_hash"]}
                for row in repo.source_packet("real", source_ids)}
    for task in repo.tasks_for_run(rid):
        if task["agent_id"] in {"A00", "A01"} and task["status"] == "queued":
            config = repo.resolve_model(task["agent_id"], lean=True)[0]
            attempt = repo.create_attempt(task["id"], config, versions)
            repo.commit_output(task["id"], attempt["attempt_id"], AgentOutputPayload(status="completed", title="Prepared identity",
                summary="The synthetic issuer is bound to its source.", analysis="The fixture identity is ready.", source_refs=source_ids), "real", config)
    initial = next(task for task in repo.tasks_for_run(rid) if task["kind"] == "research_synthesis")
    asyncio.run(engine._execute_task(rid, initial))
    assert repo.task(initial["id"])["status"] == "completed", repo.task(initial["id"])["error"]
    followup = next(task for task in repo.tasks_for_run(rid) if task["kind"] == "universe_discovery_continuation_1")
    sid = repo.import_evidence(ImportRequest(namespace="real", kind="evidence", title="Retained follow-up filing comparison",
        source_url="https://www.sec.gov/Archives/followup", content="Additional retained issuer comparison for the final reviewer.",
        publication_at="2026-08-31T00:00:00Z", idempotency_key="timeout-followup"))["source_id"]
    repo.append_run_sources(rid, [sid])
    snapshot = json_loads(repo.run_record(rid)["input_snapshot_json"], {})
    sources = repo.source_packet("real", snapshot["source_ids"])
    versions = {row["id"]: {"version": row["version"], "hash": row["content_hash"]} for row in sources}
    config = repo.resolve_model("A01", lean=True)[0]
    attempt = repo.create_attempt(followup["id"], config, versions)
    repo.mark_provider_started(followup["id"], attempt["attempt_id"])
    repo.commit_output(followup["id"], attempt["attempt_id"], AgentOutputPayload(status=getattr(request, "param", "completed"), title="Follow-up archive",
        summary="The source is retained for review.", analysis="The archived source still requires synthesis.", source_refs=[sid]), "real", config)
    continuation = next(task for task in repo.tasks_for_run(rid) if task["kind"] == "research_synthesis_continuation_1")
    cio = next(task for task in repo.tasks_for_run(rid) if task["kind"] == "cio_review")
    return repo, rid, engine, provider, initial["id"], continuation["id"], cio["id"], sid


def exhaust(prepared, *, old_worker=False, monkeypatch=None):
    repo, rid, engine, _, _, continuation, _, _ = prepared
    if old_worker:
        monkeypatch.setattr("backend.app.orchestration.workflow.activate_synthesis_fallback", lambda *args, **kwargs: None)
    for _ in range(int(repo.task(continuation)["retry_limit"]) + 1):
        asyncio.run(engine._execute_task(rid, repo.task(continuation)))
    assert repo.task(continuation)["status"] == "failed"


@pytest.mark.parametrize("prepared", ["completed", "insufficient_evidence"], indirect=True)
def test_exhausted_optional_timeout_keeps_history_and_final_checks(prepared):
    repo, rid, engine, provider, initial, continuation, cio, sid = prepared
    original = repo.latest_outputs(rid)
    exhaust(prepared)
    receipts = repo.synthesis_timeout_fallbacks(rid)
    assert len(receipts) == 1
    assert receipts[0]["original_output_id"] == repo.task(initial)["output_id"]
    assert sid in receipts[0]["source_versions"]
    assert repo.task_dependency_states(cio) == [{"id": continuation, "status": "failed", "satisfied_by_archived_evidence": True}]
    assert repo.latest_outputs(rid) == original
    with repo.db.operation() as conn:
        attempts_before = [dict(row) for row in conn.execute("SELECT * FROM task_attempts WHERE task_id=?", (continuation,))]
    assert all(row["status"] == "failed" and row["error"] == TIMEOUT for row in attempts_before)
    asyncio.run(engine._execute_task(rid, repo.task(cio)))
    assert repo.task(cio)["status"] == "completed", repo.task(cio)["error"]
    packet = provider.calls[-1]["packet"]
    assert packet["agent_id"] == "A11" and packet["synthesis_timeout_fallbacks"] == receipts
    assert LIMITATION in packet["question"]
    assert sid in {source["id"] for source in packet["evidence"]}
    assert repo.task(initial)["output_id"] in {out["id"] for out in packet["prior_outputs"]}
    result = CaseDecisionStore(repo).current(rid, "real")
    assert result["candidates"][0]["valuation"]["scenarios"]["base"] == "181.50000000"
    assert LIMITATION in json.dumps(result)
    assert repo.task(continuation)["status"] == "failed"
    with repo.db.operation() as conn:
        assert [dict(row) for row in conn.execute("SELECT * FROM task_attempts WHERE task_id=?", (continuation,))] == attempts_before
        assert conn.execute("SELECT status FROM research_gaps WHERE id=?", (receipts[0]["gap_ids"][0],)).fetchone()[0] != "resolved"
    asyncio.run(engine.run(rid))
    assert repo.run_record(rid)["status"] == "completed", [(task["kind"], task["status"], task["error"]) for task in repo.tasks_for_run(rid)]


@pytest.mark.parametrize("problem", ["initial_timeout", "auth", "schema", "cancelled", "paused", "task_paused", "attempt_cancelled", "source_changed", "source_version_changed", "output_changed", "not_exhausted", "cio_attempted", "required_task_failed"])
def test_recovery_rejects_nonoptional_or_untrusted_state(prepared, monkeypatch, problem):
    repo, rid, _, _, initial, continuation, cio, sid = prepared
    exhaust(prepared, old_worker=True, monkeypatch=monkeypatch)
    with repo.db.transaction(immediate=True) as conn:
        if problem == "initial_timeout":
            continuation = initial
        elif problem == "auth":
            conn.execute("UPDATE task_attempts SET error='Authentication required' WHERE task_id=?", (continuation,))
        elif problem == "schema":
            conn.execute("UPDATE task_attempts SET error='Output schema validation failed' WHERE task_id=? AND attempt_no=1", (continuation,))
        elif problem == "cancelled":
            conn.execute("UPDATE runs SET cancel_requested=1 WHERE id=?", (rid,))
        elif problem == "paused":
            conn.execute("UPDATE runs SET pause_requested=1 WHERE id=?", (rid,))
        elif problem == "task_paused":
            conn.execute("UPDATE tasks SET pause_requested=1 WHERE id=?", (continuation,))
        elif problem == "attempt_cancelled":
            conn.execute("UPDATE task_attempts SET status='cancelled' WHERE task_id=? AND attempt_no=1", (continuation,))
        elif problem == "source_changed":
            conn.execute("UPDATE sources SET original_content=original_content || ' changed' WHERE id=?", (sid,))
        elif problem == "source_version_changed":
            conn.execute("UPDATE source_versions SET content=content || ' changed' WHERE source_id=?", (sid,))
        elif problem == "output_changed":
            conn.execute("UPDATE outputs SET payload_json='{}' WHERE task_id=?", (initial,))
        elif problem == "not_exhausted":
            conn.execute("UPDATE tasks SET retry_limit=retry_limit+1 WHERE id=?", (continuation,))
        elif problem == "cio_attempted":
            conn.execute("UPDATE tasks SET current_attempt_id='already-attempted' WHERE id=?", (cio,))
        elif problem == "required_task_failed":
            conn.execute("UPDATE tasks SET status='failed' WHERE run_id=? AND kind='universe_discovery'", (rid,))
    assert activate_fallback(repo, rid, task_id=continuation, queue_run=True) is None
    assert not repo.synthesis_timeout_fallbacks(rid)
    assert not any(event["type"] == EVENT for event in repo.events("real", run_id=rid))


def test_explicit_old_worker_recovery_is_idempotent_and_preserves_attempts(prepared, monkeypatch):
    repo, rid, _, _, _, continuation, cio, _ = prepared
    exhaust(prepared, old_worker=True, monkeypatch=monkeypatch)
    assert repo.run_record(rid)["status"] == "failed"
    repo.mark_task_blocked(cio, "A required task did not produce a usable completed output.")
    receipt = activate_fallback(repo, rid, task_id=continuation, queue_run=True)
    assert receipt and repo.run_record(rid)["status"] == "queued"
    assert repo.task(cio)["status"] == "queued" and repo.task(continuation)["status"] == "failed"
    assert activate_fallback(repo, rid, task_id=continuation, queue_run=True) == receipt
    assert sum(event["type"] == EVENT for event in repo.events("real", run_id=rid)) == 1


def test_source_drift_revokes_dependency_and_blocks_provider_start(prepared):
    repo, rid, _, _, _, continuation, cio, sid = prepared
    exhaust(prepared)
    assert repo.synthesis_timeout_fallbacks(rid)
    with repo.db.transaction(immediate=True) as conn:
        conn.execute("UPDATE sources SET original_content=original_content || ' changed' WHERE id=?", (sid,))
    assert repo.task_dependency_states(cio) == [{"id": continuation, "status": "failed"}]
    attempt = repo.create_attempt(cio, repo.resolve_model("A11", lean=True)[0], {})
    assert repo.mark_provider_started(cio, attempt["attempt_id"]) is None


def test_final_calculator_rejects_changed_prepared_target_without_full_retry(prepared):
    repo, rid, engine, provider, _, _, cio, _ = prepared
    exhaust(prepared)
    def incomplete(packet):
        result = answer(packet)
        result.candidate_briefs[0].target_price = "999"
        return result
    provider.handler = incomplete
    count = len(provider.calls)
    asyncio.run(engine._execute_task(rid, repo.task(cio)))
    assert len(provider.calls) == count + 1
    assert repo.task(cio)["status"] == "failed"
    assert "differs from the prepared calculator" in repo.task(cio)["error"]
    assert CaseDecisionStore(repo).current(rid, "real") is None


def test_source_drift_during_generation_rejects_final_commit(prepared):
    repo, rid, engine, provider, _, _, cio, sid = prepared
    exhaust(prepared)
    def changed(packet):
        result = answer(packet)
        with repo.db.transaction(immediate=True) as conn:
            conn.execute("UPDATE sources SET original_content=original_content || ' changed' WHERE id=?", (sid,))
        return result
    provider.handler = changed
    asyncio.run(engine._execute_task(rid, repo.task(cio)))
    assert repo.task(cio)["status"] != "completed"
    assert repo.task(cio)["output_id"] is None
    assert CaseDecisionStore(repo).current(rid, "real") is None


@pytest.mark.parametrize("drift", ["source", "original_output"])
def test_recovery_revalidates_receipt_after_committed_proposal(prepared, monkeypatch, drift):
    repo, rid, engine, provider, initial, _, cio, sid = prepared
    exhaust(prepared)
    async def interrupted(*args, **kwargs):
        raise asyncio.CancelledError
    monkeypatch.setattr(engine, "_run_post_astra_laya", interrupted)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(engine._execute_task(rid, repo.task(cio)))
    pending = repo.task(cio)
    assert pending["output_id"] and pending["status"] == "waiting_review"
    call_count = len(provider.calls)
    with repo.db.transaction(immediate=True) as conn:
        if drift == "source":
            conn.execute("UPDATE sources SET original_content=original_content || ' changed' WHERE id=?", (sid,))
        else:
            conn.execute("UPDATE outputs SET payload_json='{}' WHERE task_id=?", (initial,))
    assert not asyncio.run(engine._recover_contract_cio_review(rid, pending, repo.run_record(rid)))
    assert not repo.finalize_recovered_output(cio, pending["current_attempt_id"], pending["output_id"])
    assert repo.task(cio)["status"] == "waiting_review"
    assert len(provider.calls) == call_count
    assert CaseDecisionStore(repo).current(rid, "real") is None


def test_drift_during_local_review_cannot_publish_final_decision(prepared, monkeypatch):
    repo, rid, engine, _, _, _, cio, sid = prepared
    exhaust(prepared)
    original = engine._run_post_astra_laya
    async def drift(*args, **kwargs):
        result = await original(*args, **kwargs)
        with repo.db.transaction(immediate=True) as conn:
            conn.execute("UPDATE sources SET original_content=original_content || ' changed' WHERE id=?", (sid,))
        return result
    monkeypatch.setattr(engine, "_run_post_astra_laya", drift)
    asyncio.run(engine._execute_task(rid, repo.task(cio)))
    assert repo.task(cio)["status"] != "completed"
    assert CaseDecisionStore(repo).current(rid, "real") is None
