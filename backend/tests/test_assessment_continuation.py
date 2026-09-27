"""A valid bounded earnings review keeps gaps without an automatic second pass."""
import asyncio
import json

import pytest

from backend.app.orchestration.workflow import Orchestrator
from backend.app.research.assessment_continuation import execution_only_gap, has_verified_earnings_target
from backend.app.research.case_store import CaseDecisionStore
from backend.app.schemas import MissingGap
from backend.tests.test_assessment_pipeline import answer, case  # noqa: F401 (shared pytest fixture)
from backend.tests.test_discovery_handoff import FakeProvider, FakeRegistry
from backend.tests.test_five_question_commit_path import _DeterministicLaya


MARKET = MissingGap(
    key="dated_market_comparison",
    description="Current entry-price comparison lacks an original-locator price observation and resolved connector identity.",
    relevant_role="A11",
    reopen_when="The supplied archive binds a dated USD-per-share observation to the ACME listing.",
    source_requirements=["Exact original price locator, timestamp, currency and instrument identity"],
)
POSITION = MissingGap(
    key="position_and_risk", description="No verified ACME position or suitable allocation can be established.",
    relevant_role="A11", reopen_when="Dated reconciled holdings, balances and approved owner risk limits are supplied.",
    source_requirements=["Account-level position quantities, costs, market values and observation dates"],
)
FINANCIAL = MissingGap(
    key="financial_binding_and_filings", description="Latest EPS currency and the filing comparison remain unresolved.",
    relevant_role="A11", reopen_when="A dated issuer filing establishes current diluted EPS and cash PP&E.",
)


def run_with_gaps(case, gaps, *, stop_before_repair=False):
    repo, rid, _, _, _, _ = case
    def response(packet):
        payload = answer(packet)
        payload.missing_gaps = gaps
        payload.missing_data = [gap.description for gap in gaps]
        payload.candidate_briefs[0].missing_inputs = [gap.description for gap in gaps]
        return payload
    provider = FakeProvider(response)
    engine = Orchestrator(repo, FakeRegistry(provider), repo.config, laya_runtime=_DeterministicLaya())
    if stop_before_repair:
        # Exercise the real commit and canonical gates, then leave any newly
        # queued research undispatched. No fake follow-up answers are needed.
        execute = engine._execute_task
        async def initial_tasks_only(run_id, task):
            if "continuation" in task["kind"]:
                raise asyncio.CancelledError
            await execute(run_id, task)
        engine._execute_task = initial_tasks_only
    repo.control("run", rid, "run_once")
    if stop_before_repair:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(engine.run(rid))
    else:
        asyncio.run(engine.run(rid))
    return provider


def test_target_publishes_once_with_unresolved_execution_limits(case):
    repo, rid, _, _, _, other = case
    provider = run_with_gaps(case, [MARKET, POSITION])
    assert len(provider.calls) == 1
    assert repo.run_record(rid)["status"] == "completed"
    assert not any("continuation" in t["kind"] for t in repo.tasks_for_run(rid))
    result = CaseDecisionStore(repo).current(rid, "real")
    candidate = result["candidates"][0]
    assert candidate["future_target"]["price"] == "165.00000000"
    assert candidate["valuation"]["status"] == "complete"
    assert len(candidate["key_questions"]) == 5
    rendered = json.dumps(result)
    assert MARKET.description in rendered and POSITION.description in rendered
    assert repo.firm_paused() and repo.run_record(other)["status"] == "queued"
    with repo.db.operation() as conn:
        gaps = conn.execute("SELECT status,repair_round FROM research_gaps WHERE root_run_id=?", (rid,)).fetchall()
        assert len(gaps) == 2 and all(g["status"] != "resolved" and g["repair_round"] == 0 for g in gaps)
        event = conn.execute("SELECT payload_json FROM events WHERE run_id=? AND type='assessment_followup_deferred'", (rid,)).fetchone()
        assert event and len(json.loads(event[0])["gap_ids"]) == 2
        snapshot = json.loads(conn.execute("SELECT input_snapshot_json FROM runs WHERE id=?", (rid,)).fetchone()[0])
        assert not snapshot.get("lean_continuation_used")


def test_complete_target_retains_financial_and_optional_filing_gaps_without_a_second_review(case):
    repo, rid, _, _, _, _ = case
    filing = MissingGap(key="latest_filing_comparison", description="The earnings 8-K and pending 10-K comparison are not fully available.",
                        relevant_role="A11", reopen_when="The issuer publishes the annual report and earnings supplement.")
    provider = run_with_gaps(case, [MARKET, POSITION, FINANCIAL, filing])
    assert len(provider.calls) == 1
    assert repo.run_record(rid)["status"] == "completed"
    assert not any("continuation" in t["kind"] for t in repo.tasks_for_run(rid))
    current = CaseDecisionStore(repo).current(rid, "real")
    assert current["candidates"][0]["future_target"]["price"] == "165.00000000"
    assert current["candidates"][0]["outcome"] != "recommend"
    assert all(gap.description in json.dumps(current) for gap in (MARKET, POSITION, FINANCIAL, filing))
    with repo.db.operation() as conn:
        rows = conn.execute("SELECT normalized_gap,status,repair_round FROM research_gaps WHERE root_run_id=?", (rid,)).fetchall()
        assert len(rows) == 4
        assert all(row["repair_round"] == 0 and row["status"] != "resolved" for row in rows)


def test_other_research_recipes_keep_their_single_automatic_repair(case):
    repo, rid, _, _, _, _ = case
    provider = run_with_gaps(case, [])
    payload = answer(provider.calls[0]["packet"])
    payload.missing_gaps = [FINANCIAL]
    with repo.db.transaction(immediate=True) as conn:
        task = conn.execute("SELECT * FROM tasks WHERE run_id=? AND agent_id='A11'", (rid,)).fetchone()
        output = conn.execute("SELECT * FROM outputs WHERE id=?", (task["output_id"],)).fetchone()
        snapshot = json.loads(conn.execute("SELECT input_snapshot_json FROM runs WHERE id=?", (rid,)).fetchone()[0])
        snapshot.pop("assessment_pipeline")
        conn.execute("UPDATE runs SET input_snapshot_json=? WHERE id=?", (json.dumps(snapshot), rid))
        repo._queue_gap_repairs_conn(conn, task=task, output_id=output["id"], payload=payload, source_ids=payload.source_refs, allow_child=False)
        queued = repo._queue_lean_continuation_conn(conn, task=task, output_id=output["id"], payload=payload, source_ids=payload.source_refs)
        assert len(queued) == 3


@pytest.mark.parametrize("gap", [
    FINANCIAL,
    MARKET.model_copy(update={"source_requirements": ["Bind latest EPS currency and current stock price"]}),
    MARKET.model_copy(update={"description": "Current quote belongs to a mismatched issuer."}),
    MissingGap(key="complete_call", description="Missing full transcript and management qualifiers."),
    MissingGap(key="company_holdings", description="Company holdings and corporate balances are unknown."),
    MissingGap(key="unclassified", description="Additional context is needed."),
])
def test_mixed_unknown_and_financial_requests_are_never_execution_only(gap):
    assert execution_only_gap(gap) is None


def test_market_and_account_classification_includes_source_requirements():
    assert execution_only_gap(MARKET) == "current_market_comparison"
    assert execution_only_gap(POSITION) == "portfolio_sizing"


@pytest.mark.parametrize("failure", ["missing_target", "wrong_candidate", "unfrozen_facts", "old_pipeline", "changed_archive"])
def test_deferral_requires_verified_package_and_real_frozen_target(case, failure):
    repo, rid, _, source, _, _ = case
    provider = run_with_gaps(case, [])
    proposal = answer(provider.calls[0]["packet"])
    with repo.db.transaction(immediate=True) as conn:
        task = conn.execute("SELECT * FROM tasks WHERE run_id=? AND agent_id='A11'", (rid,)).fetchone()
        output = conn.execute("SELECT * FROM outputs WHERE id=?", (task["output_id"],)).fetchone()
        run = conn.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
        assert has_verified_earnings_target(repo, conn, run=run, task=task, output_id=output["id"], payload=proposal)
        if failure == "missing_target":
            proposal.candidate_briefs[0].valuation_assumptions = None
        elif failure == "wrong_candidate":
            proposal.candidate_briefs[0].ticker = "OTHER"
        elif failure == "unfrozen_facts":
            # Outside-allowlist IDs cannot become facts just because they are
            # in the database and the supplied price calculation is plausible.
            proposal.candidate_briefs[0].valuation_assumptions.methods[0].inputs[0].fact_claim_ids = ["fact_not_supplied"]
        elif failure == "old_pipeline":
            run = dict(run)
            snapshot = json.loads(run["input_snapshot_json"])
            snapshot.pop("assessment_pipeline")
            run["input_snapshot_json"] = json.dumps(snapshot)
        elif failure == "changed_archive":
            conn.execute("UPDATE sources SET original_content=original_content || ' changed' WHERE id=?", (source,))
        assert not has_verified_earnings_target(repo, conn, run=run, task=task, output_id=output["id"], payload=proposal)


def legacy_blocked_continuation(case, monkeypatch):
    """Reproduce a pre-fix continuation behind an already published report."""
    repo, rid, _, _, _, _ = case
    provider = run_with_gaps(case, [MARKET, POSITION, FINANCIAL])
    payload = answer(provider.calls[0]['packet'])
    payload.missing_gaps = [FINANCIAL]
    with monkeypatch.context() as old_policy:
        old_policy.setattr('backend.app.research.assessment_continuation.has_verified_earnings_target', lambda *args, **kwargs: False)
        with repo.db.transaction(immediate=True) as conn:
            task = conn.execute("SELECT * FROM tasks WHERE run_id=? AND agent_id='A11'", (rid,)).fetchone()
            queued = repo._queue_lean_continuation_conn(conn, task=task, output_id=task['output_id'], payload=payload, source_ids=payload.source_refs)
    assert len(queued) == 3
    repo.set_run_status(rid, 'queued')
    repo.authorize_run_once(rid)
    attempt = repo.create_attempt(queued[0], repo.resolve_model('A01')[0], {})
    repo.mark_task_failure(queued[0], attempt['attempt_id'], 'blocked', 'Historical bounded search exceeded one query.')
    repo.set_run_status(rid, 'blocked', error='Historical bounded search exceeded one query.')
    return queued, attempt['attempt_id']


def test_reconcile_obsolete_followup_preserves_report_failed_attempt_and_unresolved_gaps(case, monkeypatch):
    repo, rid, _, _, _, other = case
    queued, failed_attempt = legacy_blocked_continuation(case, monkeypatch)
    canonical = CaseDecisionStore(repo).current(rid, 'real')
    unrelated = dict(repo.run_record(other))
    with repo.db.operation() as conn:
        attempt_before = dict(conn.execute('SELECT * FROM task_attempts WHERE id=?', (failed_attempt,)).fetchone())
        original_outputs = [tuple(row) for row in conn.execute('SELECT o.* FROM outputs o JOIN tasks t ON t.id=o.task_id WHERE t.run_id=?', (rid,))]
    result = repo.reconcile_earnings_assessment_followup(rid)
    assert result['changed'] and set(result['cancelled_task_ids']) == set(queued)
    assert repo.run_record(rid)['status'] == 'completed'
    assert CaseDecisionStore(repo).current(rid, 'real') == canonical
    assert dict(repo.run_record(other)) == unrelated and repo.firm_paused()
    with repo.db.operation() as conn:
        assert dict(conn.execute('SELECT * FROM task_attempts WHERE id=?', (failed_attempt,)).fetchone()) == attempt_before
        assert [tuple(row) for row in conn.execute('SELECT o.* FROM outputs o JOIN tasks t ON t.id=o.task_id WHERE t.run_id=?', (rid,))] == original_outputs
        tasks = conn.execute('SELECT id,status,error FROM tasks WHERE run_id=?', (rid,)).fetchall()
        assert all(row['status'] == 'cancelled' for row in tasks if row['id'] in queued)
        assert next(row for row in tasks if row['id'] == queued[0])['error'] == 'Historical bounded search exceeded one query.'
        gaps = conn.execute('SELECT status,repair_round FROM research_gaps WHERE root_run_id=?', (rid,)).fetchall()
        assert all(row['status'] == 'open' for row in gaps) and any(row['repair_round'] == 1 for row in gaps)
        assert conn.execute("SELECT COUNT(*) FROM events WHERE run_id=? AND type='assessment_continuation_cancelled'", (rid,)).fetchone()[0] == 3
        assert not conn.execute('SELECT 1 FROM run_dispatch_authorizations WHERE run_id=? AND revoked_at IS NULL', (rid,)).fetchone()
    assert not repo.reconcile_earnings_assessment_followup(rid)['changed']
    repo.set_run_status(rid, 'completed')  # Later generic reconciliation must not falsely close these gaps.
    with repo.db.operation() as conn:
        assert all(row[0] == 'open' for row in conn.execute('SELECT status FROM research_gaps WHERE root_run_id=?', (rid,)))
        assert conn.execute("SELECT COUNT(*) FROM events WHERE run_id=? AND type='assessment_followup_reconciled'", (rid,)).fetchone()[0] == 1


@pytest.mark.parametrize('failure', ['running_attempt', 'invalid_target', 'changed_archive'])
def test_reconcile_refuses_active_or_unverified_assessment(case, monkeypatch, failure):
    repo, rid, _, source, _, _ = case
    queued, _ = legacy_blocked_continuation(case, monkeypatch)
    if failure == 'running_attempt':
        repo.create_attempt(queued[1], repo.resolve_model('A03')[0], {})
    elif failure == 'invalid_target':
        with repo.db.transaction(immediate=True) as conn:
            output = conn.execute("SELECT o.id,o.payload_json FROM outputs o JOIN tasks t ON t.id=o.task_id WHERE t.run_id=? AND t.kind='cio_review'", (rid,)).fetchone()
            payload = json.loads(output['payload_json'])
            payload['candidate_briefs'][0]['valuation_assumptions'] = None
            conn.execute('UPDATE outputs SET payload_json=? WHERE id=?', (json.dumps(payload), output['id']))
    else:
        with repo.db.transaction(immediate=True) as conn:
            conn.execute("UPDATE sources SET original_content=original_content||'changed' WHERE id=?", (source,))
    before = [dict(task) for task in repo.tasks_for_run(rid)]
    with pytest.raises(ValueError, match='running|recalculation'):
        repo.reconcile_earnings_assessment_followup(rid)
    assert [dict(task) for task in repo.tasks_for_run(rid)] == before
    assert repo.run_record(rid)['status'] == 'blocked'
