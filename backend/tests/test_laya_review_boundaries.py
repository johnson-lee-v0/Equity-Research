"""Persistence-boundary regressions for the local Laya/Astra review.

These cases deliberately use the real Repository and CaseDecisionStore with
small synthetic evidence packets.  The provider payload is never treated as
authority for fact identity, source versions, review receipts, or the case
fact budget.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from backend.app.config import Settings
from backend.app.memory.repository import Repository
from backend.app.research.case_store import CaseDecisionStore
from backend.app.research.decision_questions import candidate_proposal_hash, joint_review_allows_recommendation
from backend.app.schemas import AgentOutputPayload, AstraLayaResponse, CandidateDecisionBrief, FactClaim, ImportRequest, KeyQuestionProposal, ModelConfig, RunCreate
from backend.tests.test_manager_commit_path import create_manager_case


ROOT = Path(__file__).resolve().parents[2]
MODEL = ModelConfig(provider="codex", model="gpt-6-astra", reasoning_effort="ultra")
QUESTION_KEYS = ("opportunity", "valuation", "catalyst", "downside", "portfolio_action")


def _repo(data_dir: Path) -> Repository:
    return Repository(
        config=Settings(
            project_root=ROOT,
            data_dir=data_dir,
            enable_market_connectors=False,
            enable_reddit_intake=False,
        )
    )


def _tasks(repo: Repository, run_id: str) -> list[dict[str, Any]]:
    with repo.db.operation() as conn:
        rows = conn.execute("SELECT * FROM tasks WHERE run_id=? ORDER BY sequence_no", (run_id,)).fetchall()
    return [dict(row) for row in rows]


def _run_namespace(repo: Repository, run_id: str) -> str:
    with repo.db.operation() as conn:
        row = conn.execute("SELECT namespace FROM runs WHERE id=?", (run_id,)).fetchone()
    assert row is not None
    return str(row["namespace"])


def _attempt_fact(repo: Repository, output_id: str, attempt_id: str, *, index: int = 0) -> tuple[dict[str, Any], dict[str, Any]]:
    claims = repo.output_with_sources(output_id)
    assert claims is not None
    fact = claims["output"]["fact_claims"][index]
    versions = repo.attempt_source_versions(attempt_id)
    version = versions[fact["source_ref"]]
    binding = {
        "fact_id": fact["fact_id"],
        "source_ref": fact["source_ref"],
        "source_hash": version["hash"],
        "source_version": str(version["version"]),
    }
    return fact, binding


def _record_review(
    repo: Repository,
    *,
    run_id: str,
    attempt_id: str,
    candidate_key: str = "ABC",
    phase: str = "pre_a11",
    status: str = "ok",
    result: str | None = "recommend",
    proposal_hash: str | None = None,
    fact_bindings: list[dict[str, Any]] | None = None,
    tag: str = "receipt",
) -> dict[str, Any]:
    if phase == "pre_a11":
        choices = ["recommend", "watchlist", "decline", "needs_evidence"]
    else:
        choices = ["accept_resolution", "disagreement_remains", "insufficient_evidence"]
    namespace = _run_namespace(repo, run_id)
    input_hash = hashlib.sha256(f"{run_id}:{attempt_id}:{phase}:{tag}".encode()).hexdigest()
    return repo.record_decision_model_review(
        namespace=namespace,
        run_id=run_id,
        attempt_id=attempt_id,
        candidate_key=candidate_key,
        phase=phase,
        input_hash=input_hash,
        model_id="convaiinnovations/laya",
        model_revision="synthetic-revision",
        choices=choices,
        status=status,
        result=result,
        proposal_hash=(proposal_hash if phase == "post_astra" else None),
        runtime_version="synthetic-runtime",
        device="cpu",
        token_counts={"input": 12, "state": 8},
        scores={"synthetic": 1.0},
        fact_bindings=fact_bindings or [],
        failure_reason="synthetic unavailable" if status == "unavailable" else None,
    )


def _proposal_hash(repo: Repository, output_id: str) -> str:
    with repo.db.operation() as conn:
        row = conn.execute("SELECT payload_json FROM outputs WHERE id=?", (output_id,)).fetchone()
    assert row is not None
    payload = json.loads(row[0])
    return candidate_proposal_hash(payload["candidate_briefs"][0])


def _raw_output_payload(repo: Repository, output_id: str) -> dict[str, Any]:
    with repo.db.operation() as conn:
        row = conn.execute("SELECT payload_json FROM outputs WHERE id=?", (output_id,)).fetchone()
    assert row is not None
    payload = json.loads(row[0])
    assert isinstance(payload, dict)
    return payload


def _review_case(data_dir: Path) -> tuple[Repository, str, str, str, str, str]:
    """Create a prospective five-question case before any output is committed.

    ``create_manager_case`` remains useful as a synthetic source/portfolio
    seed, but its run is intentionally legacy.  The review boundary under test
    must be a new run whose code-owned contract marker is present in the
    immutable input snapshot before either A03 or A11 commits.
    """

    repo, _seed_decision, seed_output = create_manager_case(data_dir, reference="local")
    seed_tasks = _tasks(repo, seed_output["run_id"])
    seed_a11 = next(row for row in seed_tasks if row["agent_id"] == "A11")
    source_versions = repo.attempt_source_versions(str(seed_a11["current_attempt_id"]))
    seed_inputs = repo.attempt_decision_inputs(str(seed_a11["current_attempt_id"])) or {}

    body = RunCreate(
        namespace="real",
        ticker="ABC",
        question="Synthetic prospective five-question review",
        source_ids=list(source_versions),
        research_contract="five-questions.v1",
        idempotency_key="prospective-five-question-" + hashlib.sha256(str(data_dir).encode()).hexdigest()[:16],
    )
    fresh, reused = repo.create_run(
        body,
        [
            ("A03", "research_synthesis", "Synthetic A03 evidence", []),
            ("A11", "cio_review", "Synthetic A11 committee review", ["research_synthesis"]),
        ],
        allow_semantic_reuse=False,
    )
    assert reused is False
    run_id = fresh["run_id"]
    with repo.db.operation() as conn:
        row = conn.execute("SELECT input_snapshot_json FROM runs WHERE id=?", (run_id,)).fetchone()
    assert row is not None
    snapshot = json.loads(row[0])
    assert snapshot["research_contract"] == "five-questions.v1"
    assert snapshot.get("root_run_id") in (None, "")

    task_rows = _tasks(repo, run_id)
    assert [row["agent_id"] for row in task_rows] == ["A03", "A11"]
    a03_task, a11_task = task_rows
    a03_attempt = repo.create_attempt(a03_task["id"], MODEL, source_versions)["attempt_id"]
    repo.record_attempt_decision_inputs(a03_attempt, {})
    a03_payload = AgentOutputPayload(
        status="completed",
        research_contract="five-questions.v1",
        title="Synthetic five-question A03 evidence",
        summary="Synthetic A03 evidence packet.",
        analysis="The prospective contract marker was present before this evidence stage committed.",
        source_refs=list(source_versions),
    )
    a03_output = repo.commit_output(a03_task["id"], a03_attempt, a03_payload, "real", MODEL)
    assert a03_output["id"]

    raw_payload = _raw_output_payload(repo, seed_output["id"])
    claim_ids = [str(claim["claim_id"]) for claim in raw_payload.get("fact_claims", []) if claim.get("claim_id")]
    assert claim_ids
    raw_payload["research_contract"] = "five-questions.v1"
    question_rows = [
        {
            "key": key,
            "answer": f"Synthetic bounded answer for {key}.",
            "decision_implication": f"Synthetic implication for {key}.",
            "supporting_claim_ids": [claim_ids[0]],
            "contradicting_claim_ids": [],
            "unknowns": [],
        }
        for key in QUESTION_KEYS
    ]
    raw_payload["candidate_briefs"][0]["key_questions"] = question_rows
    raw_payload["candidate_briefs"][0]["laya_response"] = {
        "position": "agree",
        "reason": "Synthetic agreement tied to the retained source-backed fact.",
        "fact_claim_ids": [claim_ids[0]],
        "question_keys": list(QUESTION_KEYS),
    }
    if isinstance(raw_payload.get("decision_brief"), dict):
        nested_candidates = raw_payload["decision_brief"].get("candidate_briefs") or []
        if nested_candidates:
            nested_candidates[0]["key_questions"] = question_rows
            nested_candidates[0]["laya_response"] = raw_payload["candidate_briefs"][0]["laya_response"]
    raw_payload["laya_response"] = raw_payload["candidate_briefs"][0]["laya_response"]
    payload = AgentOutputPayload.model_validate(raw_payload)

    a11_attempt = repo.create_attempt(a11_task["id"], MODEL, source_versions)["attempt_id"]
    repo.record_attempt_decision_inputs(a11_attempt, seed_inputs)
    committed = repo.commit_output(a11_task["id"], a11_attempt, payload, "real", MODEL)
    assert committed["id"]
    return repo, run_id, committed["id"], a03_attempt, a11_attempt, _proposal_hash(repo, committed["id"])


def test_unsupplied_same_run_fact_cannot_enter_new_decision(tmp_path: Path) -> None:
    repo, decision, output = create_manager_case(tmp_path, reference="unsupplied")

    assert decision["fact_reference_audit"]["unresolved"]
    candidate = decision["candidates"][0]
    assert candidate["outcome"] != "recommend"
    unresolved_references = {
        str(item["reference"])
        for item in decision["fact_reference_audit"]["unresolved"]
        if item.get("reference")
    }
    assert unresolved_references
    assert not unresolved_references.intersection(
        {
            str(item.get("fact_id"))
            for item in decision["fact_reference_audit"]["resolved"]
            if item.get("fact_id")
        }
    )
    assert any(
        check["key"] == "fact_references" and check["status"] != "pass"
        for check in candidate["recommendation_gate"]["checks"]
    )
    with repo.db.operation() as conn:
        row = conn.execute("SELECT status FROM outputs WHERE id=?", (output["id"],)).fetchone()
    assert row is not None


def test_explicit_prior_fact_may_pass_only_with_the_frozen_source_binding(tmp_path: Path) -> None:
    # Build the source fact in one committed run, then create a linked
    # current run.  The explicit allowlist is the only bridge between them.
    repo, _decision, prior_output = create_manager_case(tmp_path, reference="local")
    prior_run_id = prior_output["run_id"]
    prior_tasks = _tasks(repo, prior_run_id)
    prior_a03_output = next(row["output_id"] for row in prior_tasks if row["agent_id"] == "A03")
    prior_a03_attempt = str(next(row["current_attempt_id"] for row in prior_tasks if row["agent_id"] == "A03"))
    prior_sources = repo.attempt_source_versions(prior_a03_attempt)
    current_body = RunCreate(
        namespace="real",
        ticker="ABC",
        question="Synthetic linked prior-run review",
        source_ids=list(prior_sources),
        idempotency_key="linked-prior-review",
    )
    current_run, _ = repo.create_run(
        current_body,
        [("A11", "cio_review", "Synthetic linked committee review", [])],
        allow_semantic_reuse=False,
        parent_run_id=prior_run_id,
    )
    current_attempt = repo.create_attempt(current_run["tasks"][0]["id"], MODEL, prior_sources)
    fact, binding = _attempt_fact(repo, prior_a03_output, current_attempt["attempt_id"])
    run_id = current_run["run_id"]
    a11_attempt = current_attempt["attempt_id"]
    repo.record_attempt_decision_inputs(
        a11_attempt,
        {"prior_output_ids": [prior_a03_output], "prior_fact_ids": [fact["fact_id"]]},
    )

    accepted = _record_review(
        repo,
        run_id=run_id,
        attempt_id=a11_attempt,
        phase="pre_a11",
        fact_bindings=[binding],
        tag="explicit-prior",
    )
    assert accepted["status"] == "ok"
    assert json.loads(accepted["fact_bindings_json"])[0]["fact_id"] == fact["fact_id"]

    wrong_source = dict(binding, source_ref="not-the-fact-source")
    with pytest.raises(ValueError, match="source does not belong"):
        _record_review(
            repo,
            run_id=run_id,
            attempt_id=a11_attempt,
            phase="pre_a11",
            fact_bindings=[wrong_source],
            tag="wrong-source",
        )

    wrong_version = dict(binding, source_hash="0" * 64)
    with pytest.raises(ValueError, match="immutable source version"):
        _record_review(
            repo,
            run_id=run_id,
            attempt_id=a11_attempt,
            phase="pre_a11",
            fact_bindings=[wrong_version],
            tag="wrong-version",
        )


def test_stale_reused_fact_cannot_become_a_current_review_binding(tmp_path: Path) -> None:
    repo, _decision, output = create_manager_case(tmp_path, reference="prior")
    run_id = output["run_id"]
    task_rows = _tasks(repo, run_id)
    a03_output = next(row["output_id"] for row in task_rows if row["agent_id"] == "A03")
    a11_attempt = str(next(row["current_attempt_id"] for row in task_rows if row["agent_id"] == "A11"))
    _fact, binding = _attempt_fact(repo, a03_output, a11_attempt, index=0)
    with repo.db.transaction(immediate=True) as conn:
        # Move the decision clock beyond the prior annual fact's immutable
        # reporting period.  The source row and version stay untouched; only
        # the current review as-of makes the historical reuse stale.
        conn.execute("UPDATE runs SET as_of=? WHERE id=?", ("2030-01-01T00:00:00Z", run_id))

    with pytest.raises(ValueError, match="current semantic or freshness"):
        _record_review(
            repo,
            run_id=run_id,
            attempt_id=a11_attempt,
            phase="post_astra",
            status="ok",
            result="accept_resolution",
            proposal_hash="a" * 64,
            fact_bindings=[binding],
            tag="stale-reused-fact",
        )


def test_freeze_decision_review_ids_rejects_missing_cross_scope_and_wrong_phase_receipts(tmp_path: Path) -> None:
    repo, _run_decision, output = create_manager_case(tmp_path, reference="local")
    run_id = output["run_id"]
    target_tasks = _tasks(repo, run_id)
    target_a03 = str(next(row["current_attempt_id"] for row in target_tasks if row["agent_id"] == "A03"))
    target_a11 = str(next(row["current_attempt_id"] for row in target_tasks if row["agent_id"] == "A11"))

    with pytest.raises(ValueError, match="missing"):
        repo.freeze_decision_review_ids(target_a11, ["review_does_not_exist"])

    wrong_phase = _record_review(
        repo,
        run_id=run_id,
        attempt_id=target_a11,
        phase="post_astra",
        status="unavailable",
        result=None,
        proposal_hash="b" * 64,
        tag="wrong-phase",
    )
    with pytest.raises(ValueError, match="not a pre-A11"):
        repo.freeze_decision_review_ids(target_a11, [wrong_phase["id"]])

    other_repo_case = create_manager_case(tmp_path, direction="short", reference="local", repository=repo)
    other_output = other_repo_case[2]
    other_tasks = _tasks(repo, other_output["run_id"])
    other_a03 = str(next(row["current_attempt_id"] for row in other_tasks if row["agent_id"] == "A03"))
    cross_run = _record_review(
        repo,
        run_id=other_output["run_id"],
        attempt_id=other_a03,
        candidate_key="SYN",
        phase="pre_a11",
        tag="cross-run",
    )
    with pytest.raises(ValueError, match="cross-run"):
        repo.freeze_decision_review_ids(target_a11, [cross_run["id"]])

    demo_body = RunCreate(namespace="demo", question="Synthetic demo review boundary", ticker="ABC", idempotency_key="freeze-demo-run")
    demo_run, _ = repo.create_run(demo_body, [("A03", "research_synthesis", "Synthetic demo evidence", [])], allow_semantic_reuse=False)
    demo_attempt = repo.create_attempt(demo_run["tasks"][0]["id"], MODEL, {})
    repo.record_attempt_decision_inputs(demo_attempt["attempt_id"], {})
    cross_namespace = _record_review(
        repo,
        run_id=demo_run["run_id"],
        attempt_id=demo_attempt["attempt_id"],
        candidate_key="ABC",
        phase="pre_a11",
        tag="cross-namespace",
    )
    with pytest.raises(ValueError, match="cross-namespace"):
        repo.freeze_decision_review_ids(target_a11, [cross_namespace["id"]])

    valid = _record_review(repo, run_id=run_id, attempt_id=target_a03, phase="pre_a11", tag="valid-freeze")
    frozen = repo.freeze_decision_review_ids(target_a11, [valid["id"]])
    assert frozen["decision_receipt_ids"] == [valid["id"]]


def test_changed_a11_proposal_cannot_reuse_an_old_post_review_receipt(tmp_path: Path) -> None:
    repo, run_id, output_id, a03_attempt, a11_attempt, proposal_hash = _review_case(tmp_path)
    pre = _record_review(repo, run_id=run_id, attempt_id=a03_attempt, phase="pre_a11", tag="changed-proposal-pre")
    repo.freeze_decision_review_ids(a11_attempt, [pre["id"]])
    wrong_hash = "f" * 64 if proposal_hash != "f" * 64 else "e" * 64
    post = _record_review(
        repo,
        run_id=run_id,
        attempt_id=a11_attempt,
        phase="post_astra",
        result="accept_resolution",
        proposal_hash=wrong_hash,
        tag="changed-proposal-post",
    )

    projected = CaseDecisionStore(repo).persist(run_id, output_id)
    assert projected is not None
    review = projected["candidates"][0]["joint_review"]
    assert review["post_receipt_id"] == post["id"]
    assert review["status"] != "agreed"
    assert joint_review_allows_recommendation(review) is False


def test_unavailable_post_review_remains_unavailable(tmp_path: Path) -> None:
    repo, run_id, output_id, a03_attempt, a11_attempt, proposal_hash = _review_case(tmp_path)
    pre = _record_review(repo, run_id=run_id, attempt_id=a03_attempt, phase="pre_a11", tag="unavailable-pre")
    repo.freeze_decision_review_ids(a11_attempt, [pre["id"]])
    _record_review(
        repo,
        run_id=run_id,
        attempt_id=a11_attempt,
        phase="post_astra",
        status="unavailable",
        result=None,
        proposal_hash=proposal_hash,
        tag="unavailable-post",
    )

    projected = CaseDecisionStore(repo).persist(run_id, output_id)
    assert projected is not None
    review = projected["candidates"][0]["joint_review"]
    assert review["status"] == "unavailable"
    assert "unavailable" in review["resolution"].casefold()
    assert joint_review_allows_recommendation(review) is False


def test_older_pre_and_post_receipts_do_not_mask_frozen_current_attempt_results(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, run_id, output_id, a03_attempt, a11_attempt, proposal_hash = _review_case(tmp_path)
    task_rows = _tasks(repo, run_id)
    a03_task = next(row for row in task_rows if row["agent_id"] == "A03")
    a11_task = next(row for row in task_rows if row["agent_id"] == "A11")
    source_versions = repo.attempt_source_versions(a03_attempt)
    current_a11_inputs = repo.attempt_decision_inputs(a11_attempt) or {}

    # Receipt rows are append-only.  Supply a deterministic monotonic clock
    # before creating the retry attempts and receipts rather than rewriting
    # created_at after persistence.  Each old receipt belongs to a failed
    # first attempt; each current receipt belongs to the retry selected by
    # the frozen A11 attempt/output.
    import backend.app.memory.repository as repository_module

    from datetime import datetime, timedelta

    clock = datetime(2026, 9, 20, 0, 0, 0)

    def synthetic_now() -> str:
        nonlocal clock
        value = clock
        clock += timedelta(seconds=1)
        return value.isoformat(timespec="seconds") + "Z"

    monkeypatch.setattr(repository_module, "utc_now", synthetic_now)
    old_a03 = repo.create_attempt(a03_task["id"], MODEL, source_versions)["attempt_id"]
    repo.record_attempt_decision_inputs(old_a03, {})
    old_pre = _record_review(repo, run_id=run_id, attempt_id=old_a03, phase="pre_a11", result="recommend", tag="old-pre")
    repo.mark_task_failure(a03_task["id"], old_a03, "failed", "Synthetic first A03 attempt was superseded by a retry.")

    current_a03 = repo.create_attempt(a03_task["id"], MODEL, source_versions)["attempt_id"]
    repo.record_attempt_decision_inputs(current_a03, {})
    a03_retry_output = repo.commit_output(
        a03_task["id"],
        current_a03,
        AgentOutputPayload(
            status="completed",
            research_contract="five-questions.v1",
            title="Synthetic five-question A03 retry",
            summary="Synthetic A03 retry evidence.",
            analysis="This retry is the current pre-A11 evidence attempt.",
            source_refs=list(source_versions),
        ),
        "real",
        MODEL,
    )
    assert a03_retry_output["id"]
    current_pre = _record_review(repo, run_id=run_id, attempt_id=current_a03, phase="pre_a11", result="needs_evidence", tag="current-pre")

    old_a11 = repo.create_attempt(a11_task["id"], MODEL, repo.attempt_source_versions(a11_attempt))["attempt_id"]
    repo.record_attempt_decision_inputs(old_a11, current_a11_inputs)
    old_post = _record_review(
        repo,
        run_id=run_id,
        attempt_id=old_a11,
        phase="post_astra",
        result="accept_resolution",
        proposal_hash=proposal_hash,
        tag="old-post",
    )
    repo.mark_task_failure(a11_task["id"], old_a11, "failed", "Synthetic first A11 attempt was superseded by a retry.")

    current_a11 = repo.create_attempt(a11_task["id"], MODEL, repo.attempt_source_versions(a11_attempt))["attempt_id"]
    repo.record_attempt_decision_inputs(current_a11, current_a11_inputs)
    repo.freeze_decision_review_ids(current_a11, [current_pre["id"]])
    retry_payload = AgentOutputPayload.model_validate(_raw_output_payload(repo, output_id)).model_copy(
        update={"analysis": "The same bounded proposal was retried from a distinct immutable A11 attempt."}
    )
    current_output = repo.commit_output(a11_task["id"], current_a11, retry_payload, "real", MODEL)
    current_output_id = current_output["id"]
    current_proposal_hash = _proposal_hash(repo, current_output_id)
    current_post = _record_review(
        repo,
        run_id=run_id,
        attempt_id=current_a11,
        phase="post_astra",
        status="unavailable",
        result=None,
        proposal_hash=current_proposal_hash,
        tag="current-post",
    )
    assert old_pre["attempt_id"] != current_pre["attempt_id"]
    assert old_post["attempt_id"] != current_post["attempt_id"]
    projected = CaseDecisionStore(repo).persist(run_id, current_output_id)
    assert projected is not None
    review = projected["candidates"][0]["joint_review"]
    assert review["pre_receipt_id"] == current_pre["id"]
    assert review["post_receipt_id"] == current_post["id"]
    assert review["status"] == "unavailable"


def _five_questions(ids: list[str]) -> list[KeyQuestionProposal]:
    selected = ids[:2]
    return [
        KeyQuestionProposal(
            key=key,
            answer=f"Synthetic bounded answer for {key}.",
            decision_implication=f"Synthetic implication for {key}.",
            supporting_claim_ids=selected,
            contradicting_claim_ids=[],
            unknowns=[],
        )
        for key in QUESTION_KEYS
    ]


def _budget_claims(source_id: str, prefix: str, *, count: int = 15) -> list[FactClaim]:
    return [
        FactClaim(
            claim_id=f"{prefix}_fact_{index}",
            claim=f"SYN revenue {index}",
            subject="SYN",
            metric="revenue",
            value=str(index),
            unit="USD",
            period="2025",
            source_ref=source_id,
            locator=f"L{index}",
        )
        for index in range(1, count + 1)
    ]


def _budget_payload(source_id: str, prefix: str) -> AgentOutputPayload:
    claims = _budget_claims(source_id, prefix)
    ids = [claim.claim_id for claim in claims if claim.claim_id]
    return AgentOutputPayload(
        status="completed",
        research_contract="five-questions.v1",
        title=f"Synthetic {prefix} evidence",
        summary="Synthetic case-budget fixture.",
        analysis="Synthetic evidence used to exercise cumulative persistence limits.",
        fact_claims=claims,
        source_refs=[source_id],
        candidate_briefs=[
            CandidateDecisionBrief(
                ticker="SYN",
                stance="watch",
                entry_advice="Synthetic entry remains bounded.",
                key_questions=_five_questions(ids),
            )
        ],
    )


def _question_stage_payload(
    source_id: str,
    selected_fact_ids: list[str],
    label: str,
    *,
    fact_claims: list[FactClaim] | None = None,
) -> AgentOutputPayload:
    """Build a bounded provider packet whose selected IDs are explicit."""

    response = AstraLayaResponse(
        position="agree",
        reason=f"Synthetic selection for {label}.",
        fact_claim_ids=selected_fact_ids,
        question_keys=list(QUESTION_KEYS),
    )
    return AgentOutputPayload(
        status="completed",
        research_contract="five-questions.v1",
        title=f"Synthetic {label} stage",
        summary=f"Synthetic {label} stage.",
        analysis=f"Bounded synthetic evidence for {label}.",
        fact_claims=fact_claims or [],
        source_refs=[source_id],
        laya_response=response,
        candidate_briefs=[
            CandidateDecisionBrief(
                ticker="SYN",
                stance="watch",
                entry_advice="Synthetic bounded entry remains conditional.",
                key_questions=_five_questions(selected_fact_ids),
                laya_response=response,
            )
        ],
    )


def test_cumulative_a03_and_a11_disjoint_facts_exceed_the_case_budget(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    source = repo.import_evidence(
        ImportRequest(
            namespace="real",
            kind="evidence",
            idempotency_key="budget-synthetic-source",
            title="Synthetic budget evidence",
            source_url="https://example.com/synthetic-budget",
            publication_at="2026-09-20T00:00:00Z",
            content="\n".join(f"SYN revenue {index} USD for 2025." for index in range(1, 16)),
        )
    )["source_id"]
    body = RunCreate(
        namespace="real",
        ticker="SYN",
        question="Synthetic cumulative five-question budget",
        source_ids=[source],
        research_contract="five-questions.v1",
        idempotency_key="cumulative-budget-case",
    )
    run, _ = repo.create_run(
        body,
        [
            ("A03", "research_synthesis", "Synthetic A03 evidence", []),
            ("A11", "cio_review", "Synthetic A11 review", ["research_synthesis"]),
        ],
        allow_semantic_reuse=False,
    )
    versions = {source: {"hash": repo.sources("real", source)[0]["content_hash"], "version": repo.sources("real", source)[0]["version"]}}
    a03 = repo.create_attempt(run["tasks"][0]["id"], MODEL, versions)
    repo.record_attempt_decision_inputs(a03["attempt_id"], {})
    first = repo.commit_output(run["tasks"][0]["id"], a03["attempt_id"], _budget_payload(source, "a03"), "real", MODEL)
    assert first["id"]

    a11 = repo.create_attempt(run["tasks"][1]["id"], MODEL, versions)
    repo.record_attempt_decision_inputs(a11["attempt_id"], {})
    with pytest.raises(ValueError, match="case budget is 15 distinct facts"):
        repo.commit_output(run["tasks"][1]["id"], a11["attempt_id"], _budget_payload(source, "a11"), "real", MODEL)


def test_reused_legacy_memory_is_charged_when_selected_across_stages(tmp_path: Path) -> None:
    """Selected prior memory cannot be split across A03/A11 to evade 15."""

    repo = _repo(tmp_path)
    source = repo.import_evidence(
        ImportRequest(
            namespace="real",
            kind="evidence",
            idempotency_key="legacy-memory-30-source",
            title="Synthetic legacy memory",
            source_url="https://example.com/legacy-memory-30",
            publication_at="2026-09-20T00:00:00Z",
            content="\n".join(f"SYN revenue {index} USD for 2025." for index in range(1, 31)),
        )
    )["source_id"]
    versions = {
        source: {
            "hash": repo.sources("real", source)[0]["content_hash"],
            "version": repo.sources("real", source)[0]["version"],
        }
    }

    legacy_run, _ = repo.create_run(
        RunCreate(
            namespace="real",
            ticker="SYN",
            question="Synthetic legacy memory packet",
            source_ids=[source],
            idempotency_key="legacy-memory-30-run",
        ),
        [("A03", "research_synthesis", "Synthetic legacy memory", [])],
        allow_semantic_reuse=False,
    )
    legacy_attempt = repo.create_attempt(legacy_run["tasks"][0]["id"], MODEL, versions)
    repo.record_attempt_decision_inputs(legacy_attempt["attempt_id"], {})
    legacy_output = repo.commit_output(
        legacy_run["tasks"][0]["id"],
        legacy_attempt["attempt_id"],
        AgentOutputPayload(
            status="completed",
            title="Synthetic legacy memory",
            summary="Thirty retained legacy facts.",
            analysis="The legacy packet is a reusable memory pool until a current stage selects its facts.",
            fact_claims=_budget_claims(source, "legacy", count=30),
            source_refs=[source],
        ),
        "real",
        MODEL,
    )
    canonical_ids = [
        str(claim["fact_id"])
        for claim in repo.output_with_sources(legacy_output["id"])["output"]["fact_claims"]
        if claim.get("fact_id")
    ]
    assert len(canonical_ids) == 30

    fresh, reused = repo.create_run(
        RunCreate(
            namespace="real",
            ticker="SYN",
            question="Synthetic selected legacy memory budget",
            source_ids=[source],
            research_contract="five-questions.v1",
            idempotency_key="selected-legacy-memory-30-case",
        ),
        [
            ("A03", "research_synthesis", "Synthetic A03 memory selection", []),
            ("A11", "cio_review", "Synthetic A11 memory selection", ["research_synthesis"]),
        ],
        allow_semantic_reuse=False,
        parent_run_id=legacy_run["run_id"],
    )
    assert reused is False
    with repo.db.operation() as conn:
        snapshot_row = conn.execute("SELECT input_snapshot_json FROM runs WHERE id=?", (fresh["run_id"],)).fetchone()
    assert snapshot_row is not None
    assert json.loads(snapshot_row[0])["research_contract"] == "five-questions.v1"

    fresh_tasks = _tasks(repo, fresh["run_id"])
    first_fifteen = canonical_ids[:15]
    last_fifteen = canonical_ids[15:]
    a03 = repo.create_attempt(fresh_tasks[0]["id"], MODEL, versions)
    repo.record_attempt_decision_inputs(
        a03["attempt_id"],
        {"prior_output_ids": [legacy_output["id"]], "prior_fact_ids": first_fifteen},
    )
    first = repo.commit_output(
        fresh_tasks[0]["id"],
        a03["attempt_id"],
        _question_stage_payload(source, first_fifteen, "first fifteen selected legacy facts"),
        "real",
        MODEL,
    )
    assert not first["payload"]["fact_claims"]

    a11 = repo.create_attempt(fresh_tasks[1]["id"], MODEL, versions)
    repo.record_attempt_decision_inputs(
        a11["attempt_id"],
        {"prior_output_ids": [legacy_output["id"]], "prior_fact_ids": last_fifteen},
    )
    with pytest.raises(ValueError, match="case budget is 15 distinct facts"):
        repo.commit_output(
            fresh_tasks[1]["id"],
            a11["attempt_id"],
            _question_stage_payload(source, last_fifteen, "last fifteen selected legacy facts"),
            "real",
            MODEL,
        )


def test_eight_emitted_facts_may_be_reused_by_canonical_ids_without_double_charge(tmp_path: Path) -> None:
    """A11 references A03's canonical rows without re-emitting them."""

    repo = _repo(tmp_path)
    source = repo.import_evidence(
        ImportRequest(
            namespace="real",
            kind="evidence",
            idempotency_key="reuse-eight-source",
            title="Synthetic reusable eight facts",
            source_url="https://example.com/reuse-eight",
            publication_at="2026-09-20T00:00:00Z",
            content="\n".join(f"SYN revenue {index} USD for 2025." for index in range(1, 9)),
        )
    )["source_id"]
    body = RunCreate(
        namespace="real",
        ticker="SYN",
        question="Synthetic canonical eight-fact reuse",
        source_ids=[source],
        research_contract="five-questions.v1",
        idempotency_key="canonical-eight-reuse-case",
    )
    run, _ = repo.create_run(
        body,
        [
            ("A03", "research_synthesis", "Synthetic A03 eight facts", []),
            ("A11", "cio_review", "Synthetic A11 canonical reuse", ["research_synthesis"]),
        ],
        allow_semantic_reuse=False,
    )
    versions = {source: {"hash": repo.sources("real", source)[0]["content_hash"], "version": 1}}
    claims = _budget_claims(source, "shared", count=8)
    a03 = repo.create_attempt(run["tasks"][0]["id"], MODEL, versions)
    repo.record_attempt_decision_inputs(a03["attempt_id"], {})
    first = repo.commit_output(
        run["tasks"][0]["id"],
        a03["attempt_id"],
        _question_stage_payload(source, [claim.claim_id for claim in claims], "eight emitted facts", fact_claims=claims),
        "real",
        MODEL,
    )
    canonical_ids = [
        str(claim["fact_id"])
        for claim in repo.output_with_sources(first["id"])["output"]["fact_claims"]
        if claim.get("fact_id")
    ]
    assert len(canonical_ids) == 8

    a11 = repo.create_attempt(run["tasks"][1]["id"], MODEL, versions)
    repo.record_attempt_decision_inputs(
        a11["attempt_id"],
        {"prior_output_ids": [first["id"]], "prior_fact_ids": canonical_ids},
    )
    second = repo.commit_output(
        run["tasks"][1]["id"],
        a11["attempt_id"],
        _question_stage_payload(source, canonical_ids, "eight canonical facts reused"),
        "real",
        MODEL,
    )
    assert not second["payload"]["fact_claims"]


def test_local_claim_alias_is_scoped_per_output_for_a_new_fact_budget(tmp_path: Path) -> None:
    """Reusing a local alias for a new value must add a distinct fact."""

    repo = _repo(tmp_path)
    source = repo.import_evidence(
        ImportRequest(
            namespace="real",
            kind="evidence",
            idempotency_key="scoped-alias-source",
            title="Synthetic scoped alias facts",
            source_url="https://example.com/scoped-alias",
            publication_at="2026-09-20T00:00:00Z",
            content="\n".join(
                [*(f"SYN revenue {index} USD for 2025." for index in range(1, 16)), "SYN revenue 99 USD for 2025."]
            ),
        )
    )["source_id"]
    body = RunCreate(
        namespace="real",
        ticker="SYN",
        question="Synthetic output-scoped fact aliases",
        source_ids=[source],
        research_contract="five-questions.v1",
        idempotency_key="scoped-alias-budget-case",
    )
    run, _ = repo.create_run(
        body,
        [
            ("A03", "research_synthesis", "Synthetic A03 fifteen facts", []),
            ("A11", "cio_review", "Synthetic A11 new aliased fact", ["research_synthesis"]),
        ],
        allow_semantic_reuse=False,
    )
    versions = {source: {"hash": repo.sources("real", source)[0]["content_hash"], "version": 1}}
    first_claims = _budget_claims(source, "shared", count=15)
    a03 = repo.create_attempt(run["tasks"][0]["id"], MODEL, versions)
    repo.record_attempt_decision_inputs(a03["attempt_id"], {})
    first = repo.commit_output(
        run["tasks"][0]["id"],
        a03["attempt_id"],
        _question_stage_payload(source, [claim.claim_id for claim in first_claims], "fifteen aliased facts", fact_claims=first_claims),
        "real",
        MODEL,
    )
    assert first["id"]

    new_claim = FactClaim(
        claim_id="shared_fact_1",
        claim="SYN revenue 99",
        subject="SYN",
        metric="revenue",
        value="99",
        unit="USD",
        period="2025",
        source_ref=source,
        locator="L16",
    )
    a11 = repo.create_attempt(run["tasks"][1]["id"], MODEL, versions)
    repo.record_attempt_decision_inputs(a11["attempt_id"], {"prior_output_ids": [first["id"]], "prior_fact_ids": []})
    with pytest.raises(ValueError, match="case budget is 15 distinct facts"):
        repo.commit_output(
            run["tasks"][1]["id"],
            a11["attempt_id"],
            _question_stage_payload(source, [new_claim.claim_id], "new fact reusing a local alias", fact_claims=[new_claim]),
            "real",
            MODEL,
        )
