"""No repeated synthesis without a new retained source or validated assertion."""
import asyncio
import json

import pytest

from backend.app.db import digest, json_dumps, json_loads
from backend.app.orchestration.workflow import Orchestrator
from backend.app.research.case_store import CaseDecisionStore
from backend.app.research.continuation_evidence_gate import EVENT, LIMITATION, SKIPPED
from backend.app.research.earnings_financials import release_eps_observations
from backend.app.research.research_actions import ResearchActions
from backend.app.schemas import AgentOutputPayload, ImportRequest, MissingGap, ValuationReview
from backend.tests.test_assessment_pipeline import RELEASE, case as assessment_case
from backend.tests.test_discovery_handoff import FakeProvider, FakeRegistry
from backend.tests.test_five_question_commit_path import _DeterministicLaya
from backend.tests.test_investment_valuation import answer, ordinary


@pytest.fixture
def ready(assessment_case, request):
    repo, rid = ordinary(assessment_case)
    options = getattr(request, "param", {})
    if options.get("quarter"):
        sid = repo.import_evidence(ImportRequest(namespace="real", kind="evidence", title="Retained quarterly comparison",
            source_url="https://www.sec.gov/Archives/quarterly-comparison", content=RELEASE.replace("52 Weeks", "13 Weeks"),
            publication_at="2026-08-31T00:00:00Z", idempotency_key="quarter-comparison"))["source_id"]
        repo.append_run_sources(rid, [sid])

    def respond(packet):
        result = answer(packet)
        if packet["agent_id"] == "A11":
            result.candidate_briefs[0].valuation_review = ValuationReview(status="accept",
                reason="The retained baseline and explicit scenario assumptions support this conditional valuation.")
        if repo.task(packet["task_id"])["kind"] == "research_synthesis":
            result.status = options.get("status", "completed")
            result.missing_gaps = [MissingGap(key="latest_issuer_filing", description="The latest issuer filing comparison remains unknown.",
                relevant_role="A03", reopen_when="A dated issuer filing supplies the comparison.")]
        return result

    provider = FakeProvider(respond)
    engine = Orchestrator(repo, FakeRegistry(provider), repo.config, laya_runtime=_DeterministicLaya())
    ids = json_loads(repo.run_record(rid)["input_snapshot_json"], {})["source_ids"]
    versions = {row["id"]: {"version": row["version"], "hash": row["content_hash"]} for row in repo.source_packet("real", ids)}
    for task in repo.tasks_for_run(rid):
        if task["agent_id"] in {"A00", "A01"} and task["status"] == "queued":
            config = repo.resolve_model(task["agent_id"], lean=True)[0]
            attempt = repo.create_attempt(task["id"], config, versions)
            repo.commit_output(task["id"], attempt["attempt_id"], AgentOutputPayload(status="completed", title="Prepared identity",
                summary="The fixture issuer is bound to the archive.", analysis="The identity is retained.", source_refs=ids), "real", config)
    initial = next(task for task in repo.tasks_for_run(rid) if task["kind"] == "research_synthesis")
    asyncio.run(engine._execute_task(rid, initial))
    assert repo.task(initial["id"])["status"] == "completed", repo.task(initial["id"])["error"]
    tasks = {task["kind"]: task["id"] for task in repo.tasks_for_run(rid)}
    return repo, rid, engine, provider, tasks


def complete_discovery(ready, *, status="insufficient_evidence", claims=None):
    repo, rid, _, _, tasks = ready
    ids = json_loads(repo.run_record(rid)["input_snapshot_json"], {})["source_ids"]
    versions = {row["id"]: {"version": row["version"], "hash": row["content_hash"]} for row in repo.source_packet("real", ids)}
    task_id = tasks["universe_discovery_continuation_1"]
    config = repo.resolve_model("A01", lean=True)[0]
    attempt = repo.create_attempt(task_id, config, versions)
    repo.mark_provider_started(task_id, attempt["attempt_id"])
    return repo.commit_output(task_id, attempt["attempt_id"], AgentOutputPayload(status=status, title="Follow-up evidence check",
        summary="The selected evidence gap remains open.", analysis="The archive contains no additional usable source.",
        source_refs=ids, fact_claims=claims or []), "real", config, discovery_candidates=[], discovery_source_ids=[])


@pytest.mark.parametrize("status", ["completed", "insufficient_evidence"])
def test_unchanged_discovery_skips_only_redundant_synthesis_and_keeps_final_review(ready, status):
    repo, rid, engine, provider, tasks = ready
    original_id = repo.task(tasks["research_synthesis"])["output_id"]
    original = repo.immutable_output_payload(original_id, "real")
    complete_discovery(ready, status=status)
    skipped = repo.task(tasks["research_synthesis_continuation_1"])
    assert skipped["status"] == "cancelled" and skipped["terminal_summary"] == SKIPPED
    assert skipped["current_attempt_id"] is None and skipped["output_id"] is None
    receipt, = repo.no_new_evidence_continuations(rid)
    assert receipt["new_source_count"] == receipt["new_verified_fact_count"] == 0
    assert repo.skip_redundant_continuation(rid, task_id=skipped["id"])
    assert sum(event["type"] == EVENT for event in repo.events("real", run_id=rid)) == 1
    assert repo.immutable_output_payload(original_id, "real") == original
    assert repo.task_dependency_states(tasks["cio_review"])[0]["satisfied_by_archived_evidence"]
    with repo.db.operation() as conn:
        gap = conn.execute("SELECT status,terminal_reason,resolved_by_output_id FROM research_gaps WHERE id=?", (receipt["gap_ids"][0],)).fetchone()
        assert tuple(gap) == ("terminal", "no_new_evidence", None)
        assert not conn.execute("SELECT 1 FROM task_attempts WHERE task_id=?", (skipped["id"],)).fetchone()
    asyncio.run(engine.run(rid))
    assert repo.run_record(rid)["status"] == "completed", [(t["kind"], t["status"], t["error"]) for t in repo.tasks_for_run(rid)]
    assert [call["packet"]["agent_id"] for call in provider.calls] == ["A03", "A11"]
    final = CaseDecisionStore(repo).current(rid, "real")
    assert LIMITATION in json.dumps(final)
    assert final["candidates"][0]["valuation"]["scenarios"]["base"] == "181.50000000"
    actions = ResearchActions(repo).list(rid, "real")
    assert actions["eligibility"]["evidence_retry"]
    assert next(gap for gap in actions["gaps"] if gap["id"] == receipt["gap_ids"][0])["retry_eligible"]
    assert provider.calls[-1]["packet"]["no_new_evidence_continuations"] == [receipt]
    assert LIMITATION in provider.calls[-1]["packet"]["question"]


@pytest.mark.parametrize("ready", [{"status": "needs_review"}], indirect=True)
def test_usable_needs_review_analysis_retains_uncertainty_and_can_skip(ready):
    repo, rid, _, _, tasks = ready
    original_id = repo.task(tasks["research_synthesis"])["output_id"]
    before = repo.immutable_output_payload(original_id, "real")
    assert before["status"] == "needs_review"
    complete_discovery(ready)
    assert repo.no_new_evidence_continuations(rid)
    assert repo.immutable_output_payload(original_id, "real") == before


@pytest.mark.parametrize("problem", ["partial", "missing_questions", "no_candidates", "multiple_candidates", "different_ticker"])
def test_partial_analysis_or_missing_question_contract_never_skips(ready, problem):
    repo, rid, _, _, tasks = ready
    with repo.db.transaction(immediate=True) as conn:
        row = conn.execute("SELECT * FROM outputs WHERE task_id=?", (tasks["research_synthesis"],)).fetchone()
        payload = json_loads(row["payload_json"], {})
        if problem == "partial":
            payload["status"] = "insufficient_evidence"
        elif problem == "missing_questions":
            payload["candidate_briefs"][0]["key_questions"].pop()
        elif problem == "multiple_candidates":
            payload["candidate_briefs"].append(payload["candidate_briefs"][0] | {"ticker": "OTHER"})
        elif problem == "different_ticker":
            payload["candidate_briefs"][0]["ticker"] = "OTHER"
        else:
            payload["candidate_briefs"] = []
        conn.execute("UPDATE outputs SET status=?,payload_json=?,output_hash=? WHERE id=?",
                     (payload["status"], json_dumps(payload), digest(payload), row["id"]))
    complete_discovery(ready)
    assert not repo.no_new_evidence_continuations(rid)
    assert repo.task(tasks["research_synthesis_continuation_1"])["status"] == "queued"


def test_repeated_validated_fact_alias_is_not_new_evidence(ready):
    repo, rid, _, _, tasks = ready
    original = repo.immutable_output_payload(repo.task(tasks["research_synthesis"])["output_id"], "real")
    claim = original["fact_claims"][0] | {"claim_id": "followup_same_fact"}
    output = complete_discovery(ready, claims=[claim])
    with repo.db.operation() as conn:
        assert conn.execute("SELECT validation_status FROM output_claims WHERE output_id=?", (output["id"],)).fetchone()[0] == "validated"
    assert repo.no_new_evidence_continuations(rid)


@pytest.mark.parametrize("ready", [{"quarter": True}], indirect=True)
def test_new_validated_fact_from_unchanged_retained_source_keeps_analysis(ready):
    repo, rid, _, _, tasks = ready
    sources = repo.source_packet("real", json_loads(repo.run_record(rid)["input_snapshot_json"], {})["source_ids"])
    source = next(row for row in sources if row["url"].endswith("quarterly-comparison"))
    observation = release_eps_observations(source["content"])["observations"][0]
    fields = ("subject", "metric", "value", "unit", "currency", "basis", "period", "period_start", "period_end", "statement_type", "locator", "source_quote")
    claim = {key: observation[key] for key in fields} | {"claim_id": "quarter_fact", "claim": "ACME reported quarterly diluted EPS.",
        "source_ref": source["id"], "source_version": str(source["version"])}
    output = complete_discovery(ready, claims=[claim])
    with repo.db.operation() as conn:
        assert conn.execute("SELECT validation_status FROM output_claims WHERE output_id=?", (output["id"],)).fetchone()[0] == "validated"
    assert not repo.no_new_evidence_continuations(rid)
    assert repo.task(tasks["research_synthesis_continuation_1"])["status"] == "queued"


def test_new_archived_content_keeps_the_continuation(ready):
    repo, rid, engine, _, tasks = ready
    sid = repo.import_evidence(ImportRequest(namespace="real", kind="evidence", title="New issuer evidence",
        source_url="https://www.sec.gov/Archives/new-issuer-filing", content="The issuer published new primary financial evidence.",
        publication_at="2026-08-31T00:00:00Z", idempotency_key="new-issuer-evidence"))["source_id"]
    repo.append_run_sources(rid, [sid])
    complete_discovery(ready)
    assert not repo.no_new_evidence_continuations(rid)
    assert repo.task(tasks["research_synthesis_continuation_1"])["status"] == "queued"
    asyncio.run(engine._execute_task(rid, repo.task(tasks["research_synthesis_continuation_1"])))
    assert repo.task(tasks["research_synthesis_continuation_1"])["status"] == "completed"


def duplicate_source(repo, rid):
    source_id = json_loads(repo.run_record(rid)["input_snapshot_json"], {})["source_ids"][0]
    with repo.db.operation() as conn:
        source = dict(conn.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone())
    result = repo.import_evidence(ImportRequest(namespace="real", kind="evidence", title="Repeated discovery of the same archive",
        content=source["original_content"], source_url=source["url"], idempotency_key="duplicate-discovery"))
    assert result["source_id"] == source_id
    repo.append_run_sources(rid, [source_id])
    return source_id


@pytest.mark.parametrize("change", [None, {"publication_at": "2026-09-01T00:00:00Z"}, {"url": "https://www.sec.gov/Archives/different-source"}])
def test_same_bytes_are_duplicate_only_when_source_identity_and_dates_match(ready, change):
    repo, rid, _, _, _ = ready
    sid = duplicate_source(repo, rid)
    if change:
        with repo.db.transaction(immediate=True) as conn:
            key, value = next(iter(change.items()))
            conn.execute(f"UPDATE sources SET {key}=? WHERE id=?", (value, sid))
    complete_discovery(ready)
    assert bool(repo.no_new_evidence_continuations(rid)) == (change is None)


@pytest.mark.parametrize("problem", ["initial_incomplete", "initial_output_changed", "source_changed", "source_version_changed", "cancelled", "task_cancelled", "synthesis_attempted", "cio_attempted"])
def test_incomplete_cancelled_or_changed_history_cannot_be_skipped(ready, problem):
    repo, rid, _, _, tasks = ready
    with repo.db.transaction(immediate=True) as conn:
        if problem == "initial_incomplete":
            conn.execute("UPDATE tasks SET status='failed' WHERE id=?", (tasks["research_synthesis"],))
        elif problem == "initial_output_changed":
            conn.execute("UPDATE outputs SET payload_json='{}' WHERE task_id=?", (tasks["research_synthesis"],))
        elif problem == "source_changed":
            conn.execute("UPDATE sources SET original_content=original_content || ' changed' WHERE id=(SELECT json_extract(input_refs_json,'$[0]') FROM tasks WHERE id=?)", (tasks["research_synthesis"],))
        elif problem == "source_version_changed":
            conn.execute("UPDATE source_versions SET content=content || ' changed' WHERE source_id=(SELECT json_extract(input_refs_json,'$[0]') FROM tasks WHERE id=?)", (tasks["research_synthesis"],))
        elif problem == "cancelled":
            conn.execute("UPDATE runs SET cancel_requested=1 WHERE id=?", (rid,))
        elif problem == "task_cancelled":
            conn.execute("UPDATE tasks SET status='cancelled' WHERE id=?", (tasks["research_synthesis_continuation_1"],))
        elif problem in {"synthesis_attempted", "cio_attempted"}:
            key = "cio_review" if problem == "cio_attempted" else "research_synthesis_continuation_1"
            conn.execute("UPDATE tasks SET current_attempt_id='attempted' WHERE id=?", (tasks[key],))
    if problem != "cancelled":
        complete_discovery(ready)
    assert not repo.skip_redundant_continuation(rid, task_id=tasks["research_synthesis_continuation_1"])
    assert not repo.no_new_evidence_continuations(rid)


@pytest.mark.parametrize("scope", ["run", "task", "firm"])
def test_pause_defers_gate_until_permitted_resume(ready, scope):
    repo, rid, _, _, tasks = ready
    subject = tasks["research_synthesis_continuation_1"] if scope == "task" else rid if scope == "run" else None
    repo.control(scope, subject, "pause")
    complete_discovery(ready)
    assert not repo.no_new_evidence_continuations(rid)
    assert repo.task(tasks["research_synthesis_continuation_1"])["status"] == "queued"
    if scope == "firm":
        repo.control("run", rid, "run_once")
    else:
        repo.control(scope, subject, "resume")
        if scope == "run":
            repo.control("run", rid, "run_once")
    assert repo.skip_redundant_continuation(rid, task_id=tasks["research_synthesis_continuation_1"])


def test_source_drift_revokes_receipt_and_prevents_final_provider_start(ready):
    repo, rid, _, _, tasks = ready
    complete_discovery(ready)
    receipt, = repo.no_new_evidence_continuations(rid)
    with repo.db.transaction(immediate=True) as conn:
        conn.execute("UPDATE sources SET original_content=original_content || ' changed' WHERE id=?", (next(iter(receipt["source_versions"])),))
    assert not repo.no_new_evidence_continuations(rid)
    assert not repo.synthesis_timeout_review_allowed(tasks["cio_review"])
    assert not repo.task_dependency_states(tasks["cio_review"])[0].get("satisfied_by_archived_evidence")
    attempt = repo.create_attempt(tasks["cio_review"], repo.resolve_model("A11", lean=True)[0], {})
    assert repo.mark_provider_started(tasks["cio_review"], attempt["attempt_id"]) is None


@pytest.mark.parametrize("control", ["pause", "cancel"])
def test_parent_lifecycle_still_blocks_review_after_skip(ready, control):
    repo, rid, _, _, tasks = ready
    complete_discovery(ready)
    assert repo.no_new_evidence_continuations(rid)
    repo.control("run", rid, control)
    assert not repo.no_new_evidence_continuations(rid)
    assert not repo.synthesis_timeout_review_allowed(tasks["cio_review"])
    assert not repo.task_dependency_states(tasks["cio_review"])[0].get("satisfied_by_archived_evidence")


def test_source_drift_during_review_blocks_commit(ready):
    repo, rid, engine, provider, tasks = ready
    complete_discovery(ready)
    receipt, = repo.no_new_evidence_continuations(rid)
    original_handler = provider.handler
    def changed(packet):
        result = original_handler(packet)
        with repo.db.transaction(immediate=True) as conn:
            conn.execute("UPDATE sources SET original_content=original_content || ' changed' WHERE id=?", (next(iter(receipt["source_versions"])),))
        return result
    provider.handler = changed
    asyncio.run(engine._execute_task(rid, repo.task(tasks["cio_review"])))
    assert repo.task(tasks["cio_review"])["status"] != "completed"
    assert repo.task(tasks["cio_review"])["output_id"] is None
    assert CaseDecisionStore(repo).current(rid, "real") is None


@pytest.mark.parametrize("drift", [False, True])
def test_recovery_revalidates_skip_without_rerunning_analysis(ready, monkeypatch, drift):
    repo, rid, engine, provider, tasks = ready
    complete_discovery(ready)
    original_review = engine._run_post_astra_laya
    async def interrupted(*args, **kwargs):
        raise asyncio.CancelledError
    monkeypatch.setattr(engine, "_run_post_astra_laya", interrupted)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(engine._execute_task(rid, repo.task(tasks["cio_review"])))
    pending = repo.task(tasks["cio_review"])
    assert pending["output_id"] and pending["status"] == "waiting_review"
    calls = len(provider.calls)
    if drift:
        with repo.db.transaction(immediate=True) as conn:
            conn.execute("UPDATE outputs SET payload_json='{}' WHERE task_id=?", (tasks["research_synthesis"],))
    monkeypatch.setattr(engine, "_run_post_astra_laya", original_review)
    recovered = asyncio.run(engine._recover_contract_cio_review(rid, pending, repo.run_record(rid)))
    assert recovered is not drift
    if drift:
        assert not repo.finalize_recovered_output(pending["id"], pending["current_attempt_id"], pending["output_id"])
        assert CaseDecisionStore(repo).current(rid, "real") is None
    else:
        assert repo.task(tasks["cio_review"])["status"] == "completed"
        assert LIMITATION in json.dumps(CaseDecisionStore(repo).current(rid, "real"))
    assert len(provider.calls) == calls
