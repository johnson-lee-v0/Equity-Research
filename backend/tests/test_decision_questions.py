"""Semantic regression tests for the five-question research contract.

These tests exercise the code-owned boundaries around provider proposals,
repository evidence projection, and receipt-backed Laya/Astra review.  They
intentionally use plain mappings at the helper boundary and Pydantic models at
the provider boundary so a provider cannot manufacture canonical evidence.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from backend.app.research.decision_questions import (
    FIVE_QUESTION_KEYS,
    LayaPacketEvidenceError,
    build_laya_resolution_packet,
    candidate_proposal_hash,
    count_distinct_research_facts,
    joint_review_allows_recommendation,
    joint_review_from_receipts,
    normalize_question_proposals,
    project_key_questions,
    validate_five_question_payload,
)
from backend.app.schemas import AgentOutputPayload


def _proposal(
    key: str,
    *,
    supporting: tuple[str, ...] = (),
    contradicting: tuple[str, ...] = (),
    unknowns: tuple[str, ...] = (),
) -> dict[str, object]:
    return {
        "key": key,
        "answer": f"Bounded answer for {key}.",
        "decision_implication": f"Decision implication for {key}.",
        "supporting_claim_ids": list(supporting),
        "contradicting_claim_ids": list(contradicting),
        "unknowns": list(unknowns),
    }


def _five_questions(
    *,
    supports: dict[str, tuple[str, ...]] | None = None,
    contradictions: dict[str, tuple[str, ...]] | None = None,
    unknowns: dict[str, tuple[str, ...]] | None = None,
) -> list[dict[str, object]]:
    supports = supports or {}
    contradictions = contradictions or {}
    unknowns = unknowns or {}
    return [
        _proposal(
            key,
            supporting=supports.get(key, ()),
            contradicting=contradictions.get(key, ()),
            unknowns=unknowns.get(key, ()),
        )
        for key in FIVE_QUESTION_KEYS
    ]


def _fact(
    fact_id: str,
    *,
    claim_id: str | None = None,
    semantic_status: str = "supported",
    validation_status: str = "validated",
    freshness: str = "fresh",
) -> dict[str, object]:
    return {
        "fact_id": fact_id,
        "claim_id": claim_id,
        "claim": f"{fact_id} is a bounded repository fact",
        "subject": "ABC Inc.",
        "metric": "bounded metric",
        "value": "10",
        "unit": "USD",
        "period": "2026-09-17",
        "source_ref": f"source-{fact_id}",
        "locator": "L1",
        "semantic_status": semantic_status,
        "validation_status": validation_status,
        "freshness": freshness,
    }


def _payload(*, questions: list[dict[str, object]], facts: list[dict[str, object]]) -> dict[str, object]:
    return {
        "research_contract": "five-questions.v1",
        "fact_claims": facts,
        "candidate_briefs": [{"ticker": "ABC", "key_questions": questions}],
    }


def test_question_proposals_are_exactly_five_unique_code_owned_keys() -> None:
    raw = [
        _proposal("Opportunity"),
        _proposal("VALUATION"),
        _proposal("catalyst"),
        _proposal("downside"),
        _proposal("Portfolio-Action"),
    ]

    normalized, errors = normalize_question_proposals(raw, require_exact=True)

    assert errors == []
    assert [row["key"] for row in normalized] == list(FIVE_QUESTION_KEYS)

    duplicate, duplicate_errors = normalize_question_proposals(raw + [_proposal("opportunity")], require_exact=True)
    assert len(duplicate) == 5
    assert any("duplicate key opportunity" in error for error in duplicate_errors)
    assert any("exactly five entries" in error for error in duplicate_errors)


def test_candidate_alias_paths_are_bounded_at_three_without_silent_drop() -> None:
    payload = {
        "candidate_briefs": [{"ticker": "AAA"}, {"ticker": "BBB"}],
        "decision_brief": {"candidate_briefs": [{"ticker": "CCC"}, {"ticker": "DDD"}]},
    }

    errors = validate_five_question_payload(payload, require_questions=False)

    assert any("at most 3 candidates" in error for error in errors)
    assert len(payload["candidate_briefs"]) == 2
    assert len(payload["decision_brief"]["candidate_briefs"]) == 2


def test_fifteen_selected_facts_are_alias_normalized_and_unused_memory_is_free() -> None:
    facts = [_fact(f"fact_{index}", claim_id="local-1" if index == 1 else None) for index in range(1, 16)]
    questions = _five_questions(
        supports={
            "opportunity": ("local-1", "fact_2"),
            "valuation": ("fact_1", "fact_4"),
            "catalyst": ("fact_5", "fact_6"),
            "downside": ("fact_7", "fact_8"),
            "portfolio_action": ("fact_9", "fact_10"),
        },
        contradictions={
            "opportunity": ("fact_11",),
            "valuation": ("fact_12",),
            "catalyst": ("fact_13",),
            "downside": ("fact_14",),
            "portfolio_action": ("fact_15",),
        },
    )
    payload = _payload(questions=questions, facts=facts)

    unused_prior_facts = tuple(f"prior-unused-{index}" for index in range(1, 21))
    assert count_distinct_research_facts(payload, prior_fact_ids=unused_prior_facts) == 15
    errors = validate_five_question_payload(payload, prior_fact_ids=unused_prior_facts, fact_limit=15)
    assert not any("case budget" in error for error in errors)


def test_sixteenth_new_unreferenced_claim_still_counts_toward_case_budget() -> None:
    facts = [_fact(f"fact_{index}", claim_id="local-1" if index == 1 else None) for index in range(1, 16)]
    facts.append(_fact("new-unreferenced-16"))
    questions = _five_questions(
        supports={
            "opportunity": ("local-1", "fact_2"),
            "valuation": ("fact_1", "fact_4"),
            "catalyst": ("fact_5", "fact_6"),
            "downside": ("fact_7", "fact_8"),
            "portfolio_action": ("fact_9", "fact_10"),
        },
        contradictions={
            "opportunity": ("fact_11",),
            "valuation": ("fact_12",),
            "catalyst": ("fact_13",),
            "downside": ("fact_14",),
            "portfolio_action": ("fact_15",),
        },
    )
    payload = _payload(questions=questions, facts=facts)

    assert count_distinct_research_facts(payload, prior_fact_ids=("prior-unused",)) == 16
    errors = validate_five_question_payload(payload, fact_limit=15)
    assert any("case budget" in error for error in errors)


def test_selected_sixteenth_prior_fact_dependency_counts_but_other_prior_memory_is_free() -> None:
    facts = [_fact(f"fact_{index}", claim_id="local-1" if index == 1 else None) for index in range(1, 16)]
    questions = _five_questions(
        supports={
            "opportunity": ("local-1", "fact_2"),
            "valuation": ("fact_1", "fact_4"),
            "catalyst": ("fact_5", "fact_6"),
            "downside": ("fact_7", "fact_8"),
            "portfolio_action": ("fact_9", "fact_10"),
        },
        contradictions={
            "opportunity": ("fact_11",),
            "valuation": ("fact_12",),
            "catalyst": ("fact_13",),
            "downside": ("fact_14",),
            "portfolio_action": ("fact_15",),
        },
    )
    payload = _payload(questions=questions, facts=facts)
    payload["candidate_briefs"][0]["valuation_assumptions"] = {"methods": [{"fact_claim_ids": ["prior-selected-16"]}]}
    prior_facts = tuple(["prior-selected-16", *[f"prior-unused-{index}" for index in range(1, 21)]])

    assert count_distinct_research_facts(payload, prior_fact_ids=prior_facts) == 16
    errors = validate_five_question_payload(payload, prior_fact_ids=prior_facts, fact_limit=15)
    assert any("case budget" in error for error in errors)


def test_each_question_allows_two_supporting_and_one_contradicting_fact() -> None:
    facts = [_fact(f"fact_{index}") for index in range(1, 6)]
    questions = _five_questions()
    questions[0]["supporting_claim_ids"] = ["fact_1", "fact_2", "fact_3"]
    questions[0]["contradicting_claim_ids"] = ["fact_4", "fact_5"]

    errors = validate_five_question_payload(_payload(questions=questions, facts=facts))

    assert any("at most two supporting facts" in error for error in errors)
    assert any("at most one contradicting fact" in error for error in errors)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("semantic_status", "mismatch"),
        ("freshness", "stale"),
        ("freshness", "unknown"),
        ("validation_status", "rejected"),
    ],
)
def test_semantic_mismatch_stale_unknown_or_unvalidated_facts_cannot_be_verified(field: str, value: str) -> None:
    fact = _fact("fact-bad", **{field: value})
    questions = _five_questions(supports={"opportunity": ("fact-bad",)})

    projected = project_key_questions(questions, {"fact-bad": fact})
    opportunity = projected[0]

    assert opportunity["verified_facts"] == []
    assert opportunity["evidence_status"] == "unavailable"
    assert "reference fact-bad is unresolved or stale" in opportunity["unknowns"][0]


def test_supported_validated_fresh_fact_is_projected_as_complete() -> None:
    questions = _five_questions(supports={"opportunity": ("fact-good",)})

    projected = project_key_questions(questions, {"fact-good": _fact("fact-good")})
    opportunity = projected[0]

    assert opportunity["evidence_status"] == "complete"
    assert [fact["fact_id"] for fact in opportunity["verified_facts"]] == ["fact-good"]
    assert opportunity["unknowns"] == []


def test_material_unknown_changes_complete_question_to_partial() -> None:
    questions = _five_questions(
        supports={"opportunity": ("fact-good",)},
        unknowns={"opportunity": ("No confirmed catalyst date is retained.",)},
    )

    opportunity = project_key_questions(questions, {"fact-good": _fact("fact-good")})[0]

    assert opportunity["evidence_status"] == "partial"
    assert opportunity["unknowns"] == ["No confirmed catalyst date is retained."]


def test_unresolved_reference_remains_visible_alongside_two_provider_unknowns() -> None:
    questions = _five_questions(
        supports={"opportunity": ("fact-good", "missing-fact")},
        unknowns={"opportunity": ("Unknown one.", "Unknown two.")},
    )

    opportunity = project_key_questions(questions, {"fact-good": _fact("fact-good")})[0]
    unknown_text = " ".join(opportunity["unknowns"])

    assert opportunity["evidence_status"] == "partial"
    assert [fact["fact_id"] for fact in opportunity["verified_facts"]] == ["fact-good"]
    assert "Unknown one." in unknown_text
    assert "Unknown two." in unknown_text
    assert "missing-fact" in unknown_text


def _provider_payload(*, candidate_briefs: list[dict[str, object]] | None = None, **extra: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "status": "completed",
        "title": "Five-question provider output",
        "summary": "Bounded summary.",
        "analysis": "Bounded analysis.",
        "candidate_briefs": candidate_briefs or [{"ticker": "ABC", "key_questions": _five_questions()}],
    }
    payload.update(extra)
    return payload


def test_provider_cannot_forge_joint_review_or_verified_fact_projection() -> None:
    with pytest.raises(ValidationError):
        AgentOutputPayload.model_validate(_provider_payload(joint_review={"status": "agreed"}))

    forged_facts = _five_questions()
    forged_facts[0]["verified_facts"] = [{"fact_id": "fact-forged"}]
    with pytest.raises(ValidationError):
        AgentOutputPayload.model_validate(_provider_payload(candidate_briefs=[{"ticker": "ABC", "key_questions": forged_facts}]))

    forged_candidate_review = {"ticker": "ABC", "key_questions": _five_questions(), "joint_review": {"status": "agreed"}}
    with pytest.raises(ValidationError):
        AgentOutputPayload.model_validate(_provider_payload(candidate_briefs=[forged_candidate_review]))


def _review_candidate() -> dict[str, object]:
    return {
        "ticker": "ABC",
        "outcome": "recommend",
        "laya_response": {
            "position": "override",
            "reason": "Astra addresses the valuation contradiction and accepts the supported evidence.",
            "fact_claim_ids": ["fact-good"],
            "question_keys": ["valuation"],
        },
    }


def _review_receipts(candidate: dict[str, object], *, post: dict[str, object] | None = None) -> list[dict[str, object]]:
    receipts: list[dict[str, object]] = [
        {
            "id": "receipt-pre",
            "phase": "pre_a11",
            "status": "ok",
            "result": "watchlist",
            "model_id": "laya",
            "model_revision": "rev-1",
        }
    ]
    if post is not None:
        receipts.append(post)
    return receipts


def test_only_receipt_backed_reasoned_resolution_allows_watch_to_recommend_override() -> None:
    candidate = _review_candidate()
    post = {
        "id": "receipt-post",
        "phase": "post_astra",
        "status": "ok",
        "result": "accept_resolution",
        "proposal_hash": candidate_proposal_hash(candidate),
        "fact_bindings": [{"fact_id": "fact-good"}],
    }

    review = joint_review_from_receipts(candidate=candidate, receipts=_review_receipts(candidate, post=post), reviewed_at="2026-09-21T00:00:00Z")

    assert review["status"] == "resolved"
    assert review["laya_outcome"] == "watchlist"
    assert review["astra_outcome"] == "recommend"
    assert review["astra_reasoned"] is True
    assert joint_review_allows_recommendation(review) is True


@pytest.mark.parametrize(
    "post",
    [
        None,
        {
            "id": "receipt-post",
            "phase": "post_astra",
            "status": "ok",
            "result": "accept_resolution",
            "proposal_hash": "hash-without-reasoned-binding",
            "fact_bindings": [{"fact_id": "unrelated-fact"}],
        },
    ],
)
def test_missing_post_receipt_or_source_bound_reason_cannot_allow_recommendation(post: dict[str, object] | None) -> None:
    candidate = _review_candidate()
    review = joint_review_from_receipts(candidate=candidate, receipts=_review_receipts(candidate, post=post))

    assert joint_review_allows_recommendation(review) is False


def test_unresolved_joint_disagreement_cannot_allow_recommendation() -> None:
    candidate = _review_candidate()
    post = {
        "id": "receipt-post",
        "phase": "post_astra",
        "status": "ok",
        "result": "disagreement_remains",
        "proposal_hash": candidate_proposal_hash(candidate),
        "fact_bindings": [{"fact_id": "fact-good"}],
    }

    review = joint_review_from_receipts(candidate=candidate, receipts=_review_receipts(candidate, post=post))

    assert review["status"] == "pending"
    assert joint_review_allows_recommendation(review) is False


def test_resolution_maps_distinct_canonical_ids_for_the_same_observation() -> None:
    primary = _fact("fact-primary")
    primary.update(
        {
            "claim": "ABC diluted EPS",
            "metric": "EPS",
            "value": "8",
            "unit": "USD/share",
            "period": "2025-12-31",
            "source_ref": "filing-abc",
            "locator": "L1",
            "currency": "USD",
            "basis": "GAAP diluted",
        }
    )
    alias = dict(primary, fact_id="fact-alias")
    facts = {primary["fact_id"]: primary, alias["fact_id"]: alias}
    questions = project_key_questions(_five_questions(supports={"opportunity": ("fact-primary",)}), facts)

    state, _ = build_laya_resolution_packet(
        {"ticker": "ABC"},
        questions,
        facts,
        astra_response={"position": "override", "reason": "Bounded evidence.", "fact_claim_ids": ["fact-alias"], "question_keys": ["opportunity"]},
        laya_outcome="watchlist",
        astra_outcome="recommend",
    )
    body = json.loads(state)

    assert body["astra_response"]["fact_claim_ids"] == ["f1"]
    assert [fact["id"] for fact in body["facts"]] == ["f1"]


def test_resolution_does_not_alias_different_observation_and_represents_response_fact() -> None:
    primary = _fact("fact-primary")
    primary.update({"claim": "ABC diluted EPS", "metric": "EPS", "value": "8", "source_ref": "filing-abc", "locator": "L1", "period": "2025-12-31"})
    different = dict(primary, fact_id="fact-different", source_ref="filing-other", subject="Other Issuer", period="2026-12-31")
    facts = {primary["fact_id"]: primary, different["fact_id"]: different}
    questions = project_key_questions(_five_questions(supports={"opportunity": ("fact-primary",)}), facts)

    state, _ = build_laya_resolution_packet(
        {"ticker": "ABC"},
        questions,
        facts,
        astra_response={"position": "override", "reason": "Bounded evidence.", "fact_claim_ids": ["fact-different"], "question_keys": ["opportunity"]},
        laya_outcome="watchlist",
        astra_outcome="recommend",
    )
    body = json.loads(state)

    assert body["astra_response"]["fact_claim_ids"] == ["f2"]
    assert [fact["id"] for fact in body["facts"]] == ["f1", "f2"]
    assert body["facts"][1]["subject"] == "Other Issuer"


@pytest.mark.parametrize(
    ("field", "different_value"),
    [("period_start", "2026-04-01"), ("period_end", "2026-09-30")],
)
def test_resolution_keeps_period_boundaries_distinct(
    field: str,
    different_value: str,
) -> None:
    primary = _fact("fact-primary")
    primary.update(
        {
            "claim": "ABC quarterly revenue",
            "metric": "Revenue",
            "period": "Q2 FY2026",
            "period_start": "2026-01-01",
            "period_end": "2026-06-30",
            "source_ref": "filing-abc",
            "locator": "L1",
        }
    )
    different = dict(primary, fact_id="fact-different", **{field: different_value})
    facts = {primary["fact_id"]: primary, different["fact_id"]: different}
    questions = project_key_questions(_five_questions(supports={"opportunity": ("fact-primary",)}), facts)

    state, _ = build_laya_resolution_packet(
        {"ticker": "ABC"},
        questions,
        facts,
        astra_response={"position": "override", "reason": "Bounded evidence.", "fact_claim_ids": ["fact-different"], "question_keys": ["opportunity"]},
        laya_outcome="watchlist",
        astra_outcome="recommend",
    )
    body = json.loads(state)

    assert body["astra_response"]["fact_claim_ids"] == ["f2"]
    assert body["facts"][1][field] == different_value


def test_resolution_rejects_unbound_response_fact_without_emitting_raw_id() -> None:
    primary = _fact("fact-primary")
    stale = _fact("stale-response", freshness="stale")
    facts = {"fact-primary": primary, "stale-response": stale}
    questions = project_key_questions(_five_questions(supports={"opportunity": ("fact-primary",)}), facts)

    for fact_id in ("unbound-raw-id", "stale-response"):
        with pytest.raises(LayaPacketEvidenceError):
            build_laya_resolution_packet(
                {"ticker": "ABC"},
                questions,
                facts,
                astra_response={"position": "override", "reason": "Bounded evidence.", "fact_claim_ids": [fact_id], "question_keys": ["opportunity"]},
                laya_outcome="watchlist",
                astra_outcome="recommend",
            )
