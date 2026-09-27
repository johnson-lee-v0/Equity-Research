"""Regression coverage for the A11 frozen evidence boundary."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.app.config import Settings
from backend.app.memory.repository import Repository
from backend.app.orchestration.workflow import Orchestrator
from backend.app.schemas import (
    AgentOutputPayload,
    AstraLayaResponse,
    CandidateDecisionBrief,
    FactClaim,
    ModelConfig,
    RunCreate,
)
from backend.app.research.decision_questions import FIVE_QUESTION_KEYS
from backend.tests import test_five_question_commit_path as commit_fixture
from backend.tests.test_five_question_commit_path import create_five_question_case
from backend.tests.test_manager_commit_path import create_manager_case


ROOT = Path(__file__).resolve().parents[2]
MODEL = ModelConfig(provider="codex", model="gpt-6-astra", reasoning_effort="ultra")


class _NoInference:
    def __init__(self) -> None:
        self.calls: list[tuple[object, object]] = []

    def classify(self, state: str, questions: object) -> dict[str, object]:
        self.calls.append((state, questions))
        raise AssertionError("Laya inference must not run for unavailable frozen evidence")


def _raw_payload(repo: Repository, output_id: str) -> tuple[dict[str, object], str]:
    with repo.db.operation() as conn:
        row = conn.execute("SELECT payload_json,attempt_id FROM outputs WHERE id=?", (output_id,)).fetchone()
    assert row is not None
    payload = json.loads(row["payload_json"])
    assert isinstance(payload, dict)
    return payload, str(row["attempt_id"])


def _five_question_rows(fact_id: str) -> list[dict[str, object]]:
    return [
        {
            "key": key,
            "answer": f"Bounded answer for {key}.",
            "decision_implication": f"Bounded implication for {key}.",
            "supporting_claim_ids": [fact_id],
            "contradicting_claim_ids": [],
            "unknowns": [],
        }
        for key in FIVE_QUESTION_KEYS
    ]


def test_pre_receipt_fact_must_be_owned_by_the_a11_frozen_outputs(tmp_path: Path) -> None:
    """An A03 projection cannot smuggle a legacy fact owned by an omitted L output."""

    repo, _legacy_decision, legacy_output = create_manager_case(tmp_path, reference="local")
    _legacy_payload, legacy_attempt_id = _raw_payload(repo, legacy_output["id"])
    versions = repo.attempt_source_versions(legacy_attempt_id)
    legacy_fact = repo.output_with_sources(legacy_output["id"])["output"]["fact_claims"][0]
    fact_id = str(legacy_fact["fact_id"])
    source_ref = str(legacy_fact["source_ref"])
    binding = {
        "fact_id": fact_id,
        "source_ref": source_ref,
        "source_version": str(versions[source_ref]["version"]),
        "source_hash": str(versions[source_ref]["hash"]),
    }

    run, _ = repo.create_run(
        RunCreate(
            namespace="real",
            ticker="ABC",
            question="Synthetic frozen A11 ownership boundary",
            source_ids=list(versions),
            research_contract="five-questions.v1",
            idempotency_key="frozen-a11-ownership-boundary",
        ),
        [
            ("A03", "research_synthesis", "Synthetic A03 projection", []),
            ("A11", "cio_review", "Synthetic A11 review", ["research_synthesis"]),
        ],
        allow_semantic_reuse=False,
    )
    a03_task, a11_task = run["tasks"]
    a03 = repo.create_attempt(a03_task["id"], MODEL, versions)
    # A03 legitimately carries the legacy fact through its own prior allowlist.
    repo.record_attempt_decision_inputs(
        a03["attempt_id"],
        {"prior_output_ids": [legacy_output["id"]], "prior_fact_ids": [fact_id]},
    )
    a03_payload = AgentOutputPayload(
        status="completed",
        research_contract="five-questions.v1",
        title="Synthetic A03 projection",
        summary="A03 carries one selected legacy fact.",
        analysis="The A03 projection is intentionally a carrier for a prior fact.",
        source_refs=list(versions),
        candidate_briefs=[
            CandidateDecisionBrief(
                ticker="ABC",
                stance="watch",
                entry_advice="Review the retained fact.",
                key_questions=_five_question_rows(fact_id),
            )
        ],
    )
    a03_output = repo.commit_output(a03_task["id"], a03["attempt_id"], a03_payload, "real", MODEL)
    pre = repo.record_decision_model_review(
        namespace="real",
        run_id=run["run_id"],
        attempt_id=a03["attempt_id"],
        candidate_key="ABC",
        phase="pre_a11",
        input_hash=hashlib.sha256(b"frozen-pre-owner").hexdigest(),
        model_id="fixture-laya",
        model_revision="fixture-revision",
        choices=["recommend", "watchlist", "decline", "needs_evidence"],
        status="ok",
        result="watchlist",
        fact_bindings=[binding],
    )

    a11 = repo.create_attempt(a11_task["id"], MODEL, versions)
    # Deliberately omit legacy_output: A11 only claims A03 as its prior carrier.
    repo.record_attempt_decision_inputs(
        a11["attempt_id"],
        {
            "prior_output_ids": [a03_output["id"]],
            "prior_fact_ids": [fact_id],
            "decision_receipt_ids": [pre["id"]],
        },
    )
    response = AstraLayaResponse(
        position="agree",
        reason="The selected fact supports the bounded review.",
        fact_claim_ids=[fact_id],
        question_keys=list(FIVE_QUESTION_KEYS),
    )
    a11_payload = AgentOutputPayload(
        status="completed",
        research_contract="five-questions.v1",
        title="Synthetic A11 review",
        summary="A11 review with a deliberately omitted legacy owner.",
        analysis="The omitted owner must block local resolution.",
        source_refs=list(versions),
        laya_response=response,
        candidate_briefs=[
            CandidateDecisionBrief(
                ticker="ABC",
                stance="watch",
                entry_advice="Review the selected fact.",
                key_questions=_five_question_rows(fact_id),
                laya_response=response,
            )
        ],
    )
    a11_output = repo.commit_output(a11_task["id"], a11["attempt_id"], a11_payload, "real", MODEL)
    repo.freeze_decision_review_ids(a11["attempt_id"], [pre["id"]])
    raw_before, _ = _raw_payload(repo, a11_output["id"])

    runtime = _NoInference()
    orchestrator = Orchestrator(repo, object(), repo.config, laya_runtime=runtime)
    task = next(item for item in repo.tasks_for_run(run["run_id"]) if item["id"] == a11_task["id"])
    asyncio.run(orchestrator._run_post_astra_laya(run["run_id"], task, a11_output["id"], a11_payload))

    reviews = repo.decision_model_reviews(run["run_id"], namespace="real", phase="post_astra")
    assert len(reviews) == 1
    assert reviews[0]["status"] == "unavailable"
    assert reviews[0]["fact_bindings"] == []
    assert runtime.calls == []
    raw_after, _ = _raw_payload(repo, a11_output["id"])
    assert raw_after == raw_before


def test_invalid_post_response_evidence_is_unavailable_with_empty_bindings(tmp_path: Path) -> None:
    """A rejected Astra evidence ID cannot reach inference or receipt bindings."""

    repo = Repository(
        config=Settings(
            project_root=ROOT,
            data_dir=tmp_path,
            enable_market_connectors=False,
            enable_reddit_intake=False,
        )
    )

    # Keep a real canonical fact from an older A11 output in the same
    # repository.  The fresh five-question A11 attempt must not inherit that
    # ID through its unchanged prior-output/fact allowlists.
    _old_decision, old_output = create_manager_case(tmp_path, reference="local", repository=repo)[1:]
    old_projection = repo.output_with_sources(old_output["id"])
    assert old_projection is not None
    old_fact_id = str(old_projection["output"]["fact_claims"][0]["fact_id"])

    original_payloads = commit_fixture._provider_payloads

    def invalid_payloads(filing_id: str, market_id: str, *, astra_position: str = "override") -> dict[str, AgentOutputPayload]:
        payloads = original_payloads(filing_id, market_id, astra_position=astra_position)
        invalid_response = AstraLayaResponse(
            position="override",
            reason="The response cites a canonical fact outside this retry's frozen allowlist.",
            fact_claim_ids=[old_fact_id],
            question_keys=list(FIVE_QUESTION_KEYS),
        )
        a11 = payloads["A11"]
        candidate = a11.candidate_briefs[0].model_copy(update={"laya_response": invalid_response})
        payloads["A11"] = a11.model_copy(
            update={
                "candidate_briefs": [candidate],
                "laya_response": invalid_response,
            }
        )
        return payloads

    runtime = commit_fixture._DeterministicLaya()
    with patch.object(commit_fixture, "_provider_payloads", invalid_payloads):
        case = create_five_question_case(tmp_path, repository=repo, laya_runtime=runtime)
    run_id = case.run_id

    reviews = repo.decision_model_reviews(run_id, namespace="real", phase="post_astra")
    assert len(reviews) == 1
    assert reviews[0]["status"] == "unavailable"
    assert reviews[0]["failure_reason"] == "laya_evidence_unavailable"
    assert reviews[0]["fact_bindings"] == []
    assert [item["phase"] for item in runtime.calls] == ["pre_a11"]


def test_advanced_review_as_of_rejects_stale_pre_prices_before_laya(tmp_path: Path) -> None:
    """A fresh Sep-16 price is stale for a refreshed Oct-10 A11 review."""

    repo = Repository(
        config=Settings(
            project_root=ROOT,
            data_dir=tmp_path,
            enable_market_connectors=False,
            enable_reddit_intake=False,
        )
    )

    async def stop_before_post(*_args: object, **_kwargs: object) -> list[dict[str, object]]:
        raise asyncio.CancelledError()

    with patch.object(Orchestrator, "_run_post_astra_laya", stop_before_post):
        with pytest.raises(asyncio.CancelledError):
            create_five_question_case(tmp_path, repository=repo)

    run_id = repo.runs(namespace="real")[0]["id"]
    a11_task = next(item for item in repo.tasks_for_run(run_id) if item["agent_id"] == "A11")
    assert a11_task["output_id"]
    raw_before, _ = _raw_payload(repo, str(a11_task["output_id"]))
    with repo.db.transaction(immediate=True) as conn:
        conn.execute("UPDATE runs SET as_of=? WHERE id=?", ("2026-10-10T00:00:00Z", run_id))

    runtime = _NoInference()
    orchestrator = Orchestrator(repo, object(), repo.config, laya_runtime=runtime)
    payload = AgentOutputPayload.model_validate(raw_before)
    asyncio.run(orchestrator._run_post_astra_laya(run_id, a11_task, str(a11_task["output_id"]), payload))

    pre = repo.decision_model_reviews(run_id, namespace="real", phase="pre_a11")
    assert len(pre) == 1
    sources = {item["id"]: item for item in repo.sources("real")}
    market_quote = next(item["id"] for item in sources.values() if "synthetic market quote" in str(item.get("title") or "").casefold())
    assert sum(1 for item in pre[0]["fact_bindings"] if item.get("source_ref") == market_quote) == 2

    post = repo.decision_model_reviews(run_id, namespace="real", phase="post_astra")
    assert len(post) == 1
    assert post[0]["status"] == "unavailable"
    assert post[0]["fact_bindings"] == []
    assert runtime.calls == []
    raw_after, _ = _raw_payload(repo, str(a11_task["output_id"]))
    assert raw_after == raw_before
