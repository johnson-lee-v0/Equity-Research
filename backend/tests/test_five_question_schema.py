"""Contract tests for the lean A03/A11 provider schema projection."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from backend.app.providers.five_question_schema import (
    FIVE_QUESTION_FACT_CLAIM_ALIASES,
    FIVE_QUESTION_ROOT_FIELDS,
    compact_five_question_context,
    simplify_five_question_provider_schema,
)
from backend.app.research.decision_questions import constrain_five_question_schema
from backend.app.schemas import (
    ActionPlan,
    AgentOutputPayload,
    CandidateDecisionBrief,
    FactClaim,
    KeyQuestionProposal,
    PriceRange,
    ThesisBlock,
    ValuationAssumptions,
    ValuationMethodProposal,
    WatchTrigger,
)


def _local_refs(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        reference = value.get("$ref")
        if isinstance(reference, str) and reference.startswith("#/$defs/"):
            found.append(reference.removeprefix("#/$defs/"))
        for child in value.values():
            found.extend(_local_refs(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_local_refs(child))
    return found


def _five_question_rows() -> list[KeyQuestionProposal]:
    return [
        KeyQuestionProposal(key=key, answer=f"Bounded answer for {key}.")
        for key in (
            "opportunity",
            "valuation",
            "catalyst",
            "downside",
            "portfolio_action",
        )
    ]


def _core_payload() -> AgentOutputPayload:
    facts = [
        FactClaim(
            claim_id=f"c{index}",
            claim=f"ABC fact {index}",
            value=str(index),
            unit="USD",
            period="2026-09-01",
            source_ref="src_fixture",
            locator=f"L{index}",
        )
        for index in range(1, 16)
    ]
    candidate = CandidateDecisionBrief(
        ticker="ABC",
        direction="long",
        strategy="long_term",
        stance="watch",
        entry_zone=PriceRange(lower="90", upper="100", currency="USD", as_of="2026-09-01"),
        target_price="120",
        target_price_currency="USD",
        target_price_as_of="2026-09-01",
        stop_price="80",
        stop_price_currency="USD",
        thesis=ThesisBlock(
            variant_view="The market underestimates the recovery path.",
            market_expectation="The next filing can confirm improving margins.",
            status="complete",
        ),
        valuation_assumptions=ValuationAssumptions(
            methods=[
                ValuationMethodProposal(
                    name="eps_multiple",
                    forecast_eps="8",
                    exit_multiple="15",
                    target_price="120",
                    source_refs=["src_fixture"],
                )
            ],
            rationale="Bounded source-backed valuation proposal.",
        ),
        action_plan=ActionPlan(
            entry_condition="Enter only within the retained range.",
            invalidation_condition="Exit if the retained downside condition occurs.",
            status="complete",
        ),
        watch_triggers=[WatchTrigger(type="price", condition="Review at or below 90")],
        key_questions=_five_question_rows(),
    )
    return AgentOutputPayload(
        status="completed",
        research_contract="five-questions.v1",
        allocation_mode="alternatives",
        title="Five-question fixture",
        summary="A bounded fixture for the lean provider schema.",
        analysis="The fixture retains economics, feasibility, valuation and action fields.",
        fact_claims=facts,
        assumptions=["The retained source is current."],
        calculations=[],
        counterarguments=["The recovery could take longer than expected."],
        missing_data=["A future catalyst remains unknown."],
        proposed_action="Keep the position on the watchlist pending the trigger.",
        invalidation_conditions=["The thesis fails if the downside evidence is confirmed."],
        next_review_at="2026-10-01",
        source_refs=["src_fixture"],
        missing_gaps=[],
        decision_disposition="defer",
        candidate_briefs=[candidate],
    )


@pytest.mark.parametrize("agent_id", ["A03", "A11", "a03", " a11 "])
def test_five_question_projection_keeps_parser_and_complete_candidate_surface(agent_id: str) -> None:
    original = AgentOutputPayload.model_json_schema()
    simplified = simplify_five_question_provider_schema(original, agent_id=agent_id)

    assert set(simplified["properties"]) == set(FIVE_QUESTION_ROOT_FIELDS)
    assert simplified["required"] == ["status", "title", "summary", "analysis"]
    assert simplified["$defs"]["CandidateDecisionBrief"] == original["$defs"]["CandidateDecisionBrief"]

    # Candidate-level economics and short feasibility remain reachable from
    # the one retained candidate array, even though their root aliases do not.
    candidate_fields = simplified["$defs"]["CandidateDecisionBrief"]["properties"]
    for field in (
        "entry_zone",
        "target_price",
        "stop_price",
        "scenario_assessment",
        "thesis",
        "valuation_assumptions",
        "action_plan",
        "recommended_shares",
        "borrow_available",
        "short_permission",
        "margin_available",
        "key_questions",
    ):
        assert field in candidate_fields
        assert candidate_fields[field] == original["$defs"]["CandidateDecisionBrief"]["properties"][field]

    # Every local reference left by the root/candidate graph resolves after
    # transitive $defs pruning.
    assert set(_local_refs({key: value for key, value in simplified.items() if key != "$defs"})) <= set(simplified["$defs"])


def test_projection_prunes_legacy_wrappers_and_reduces_schema_bytes() -> None:
    original = AgentOutputPayload.model_json_schema()
    simplified = simplify_five_question_provider_schema(original, agent_id="A03")

    removed_root = {
        "review_disposition",
        "revision_requests",
        "proposal",
        "decision_brief",
        "laya_response",
        "stance",
        "entry_plan",
        "target_price",
        "target_price_currency",
        "target_price_as_of",
        "target_price_source_refs",
        "target_price_basis",
        "target_price_missing_reason",
        "stop_price",
        "stop_price_currency",
        "scenario_assessment",
        "scenario_reason",
        "entry_zone",
        "ticker",
        "instrument",
        "horizon",
        "strategy",
        "entry_advice",
        "direction",
        "risks",
        "catalysts",
        "missing_inputs",
        "simulation_snapshot",
        "simulation_snapshots",
        "routing_plan",
        "research_candidates",
        "discovery_queries",
        "discovery_urls",
        "watch_triggers",
        "thesis",
        "valuation_assumptions",
        "action_plan",
        "benchmark_ticker",
        "benchmark_rationale",
        "asset_class",
        "recommended_shares",
        "allocation_rationale",
        "issuer",
        "sector",
    }
    assert removed_root.isdisjoint(simplified["properties"])
    for definition in (
        "AllocationProposal",
        "DecisionBrief",
        "PriceScenarioSnapshot",
        "RedditTriage",
        "ResearchCandidate",
        "RoutingPlan",
    ):
        assert definition not in simplified["$defs"]

    before_bytes = len(json.dumps(original, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    after_bytes = len(json.dumps(simplified, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    assert after_bytes < before_bytes
    assert before_bytes - after_bytes > 10_000


def test_projection_composes_with_five_question_bounds() -> None:
    original = AgentOutputPayload.model_json_schema()
    bounded = constrain_five_question_schema(original, agent_id="A11")
    simplified = simplify_five_question_provider_schema(bounded, agent_id="A11")

    assert simplified["properties"]["fact_claims"]["maxItems"] == 15
    assert simplified["properties"]["candidate_briefs"]["maxItems"] == 3
    assert simplified["properties"]["analysis"]["maxLength"] == 4_000
    candidate = simplified["$defs"]["CandidateDecisionBrief"]["properties"]
    assert candidate["key_questions"]["maxItems"] == 5
    assert candidate["key_questions"]["minItems"] == 5
    assert "minItems" not in original["$defs"]["CandidateDecisionBrief"]["properties"]["key_questions"]
    questions = simplified["$defs"]["KeyQuestionProposal"]["properties"]
    assert questions["answer"]["maxLength"] == 800
    assert questions["decision_implication"]["maxLength"] == 400
    assert questions["supporting_claim_ids"]["maxItems"] == 2
    assert questions["contradicting_claim_ids"]["maxItems"] == 1
    assert questions["unknowns"]["maxItems"] == 2


def test_provider_fact_claims_use_local_aliases_while_references_keep_canonical_ids() -> None:
    schema = simplify_five_question_provider_schema(
        AgentOutputPayload.model_json_schema(),
        agent_id="A11",
    )
    claim_id = schema["$defs"]["FactClaim"]["properties"]["claim_id"]
    string_branches = [branch for branch in claim_id["anyOf"] if branch.get("type") == "string"]
    null_branches = [branch for branch in claim_id["anyOf"] if branch.get("type") == "null"]
    assert string_branches == [{"type": "string", "enum": list(FIVE_QUESTION_FACT_CLAIM_ALIASES)}]
    assert null_branches == [{"type": "null"}]
    assert "fact_eps_canonical" not in string_branches[0]["enum"]

    thesis_refs = schema["$defs"]["ThesisBlock"]["properties"]["supporting_claim_ids"]["items"]
    method_refs = schema["$defs"]["ValuationMethodProposal"]["properties"]["fact_claim_ids"]["items"]
    assert thesis_refs == {"type": "string"}
    assert method_refs == {"type": "string"}

    fixture = _core_payload().model_dump(mode="json", exclude_none=False)
    fixture["candidate_briefs"][0]["thesis"]["supporting_claim_ids"] = ["fact_eps_canonical"]
    fixture["candidate_briefs"][0]["valuation_assumptions"]["methods"][0]["fact_claim_ids"] = ["fact_eps_canonical"]
    assert fixture["fact_claims"][0]["claim_id"] == "c1"
    assert fixture["candidate_briefs"][0]["thesis"]["supporting_claim_ids"] == ["fact_eps_canonical"]
    assert fixture["candidate_briefs"][0]["valuation_assumptions"]["methods"][0]["fact_claim_ids"] == ["fact_eps_canonical"]


def test_core_fixture_validates_against_shared_parser_and_retained_schema() -> None:
    original = AgentOutputPayload.model_json_schema()
    simplified = simplify_five_question_provider_schema(original, agent_id="A03")
    payload = _core_payload()
    raw = payload.model_dump(mode="json", exclude_none=False)

    # The fixture contains every declared lean root field and is accepted by
    # the unchanged shared parser.  Removed legacy defaults may still be
    # present in the model's dump, but they are deliberately absent from the
    # provider schema.
    fixture = {name: raw[name] for name in simplified["properties"]}
    parsed = AgentOutputPayload.model_validate(fixture)
    assert parsed.candidate_briefs[0].ticker == "ABC"
    assert len(parsed.fact_claims) == 15
    assert len(parsed.candidate_briefs[0].key_questions) == 5
    assert set(fixture) == set(simplified["properties"])
    assert set(fixture["candidate_briefs"][0]) == set(
        simplified["$defs"]["CandidateDecisionBrief"]["properties"]
    )


def test_non_target_schema_is_deepcopied_and_legacy_schema_is_unchanged() -> None:
    original = AgentOutputPayload.model_json_schema()
    baseline = copy.deepcopy(original)
    legacy = simplify_five_question_provider_schema(original, agent_id="A10")
    assert legacy == baseline
    assert legacy is not original
    assert legacy["properties"] is not original["properties"]

    lean = simplify_five_question_provider_schema(original, agent_id="A03")
    assert original == baseline
    lean["properties"]["fact_claims"]["maxItems"] = 1
    assert original["properties"]["fact_claims"]["maxItems"] == 100


def test_non_agent_probe_schema_is_unchanged_even_for_target_label() -> None:
    probe = {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
        "additionalProperties": False,
    }
    simplified = simplify_five_question_provider_schema(probe, agent_id="A03")
    assert simplified == probe
    assert simplified is not probe


def _context_for_compaction() -> dict[str, Any]:
    candidate = {
        "ticker": "ABC",
        "valuation_assumptions": {"methods": [{"name": "eps_multiple", "forecast_eps": "8"}]},
        "action_plan": {"entry_condition": "Only inside the retained range."},
    }
    evidence = {
        "id": "src_exact",
        "version": 1,
        "content_hash": "hash-exact",
        "content": "L1: exact supplied evidence",
    }
    memory_context = {
        "namespace": "real",
        "run_id": "run_fixture",
        "reused": [
            {
                "record_id": "src_exact",
                "record_type": "source",
                "title": "Exact source",
                "excerpt": evidence["content"],
                "source_refs": ["src_exact"],
                "source_versions": [{"id": "src_exact", "version": 1, "content_hash": "hash-exact"}],
                "retrieved_at": "2026-09-21T00:00:00Z",
            },
            {
                "record_id": "src_mismatch",
                "record_type": "source",
                "title": "Mismatched source",
                "excerpt": "Unique memory evidence must stay.",
                "source_refs": ["src_mismatch"],
                "source_versions": [{"id": "src_mismatch", "version": 1, "content_hash": "hash-old"}],
            },
        ],
        "fresh": [],
        "retrieval_metadata": {"reason": "fixture"},
    }
    duplicate_candidates = [copy.deepcopy(candidate)]
    return {
        "agent_id": "A11",
        "memory_context": memory_context,
        "memory": copy.deepcopy(memory_context),
        "evidence": [evidence],
        "fact_claims": [{"semantic_status": "supported", "binding_checks": [{"key": "metric"}]}],
        "prior_outputs": [
            {
                "agent_id": "A03",
                "candidate_briefs": duplicate_candidates,
                "decision_brief": {
                    "candidate_briefs": copy.deepcopy(duplicate_candidates),
                    "allocation_mode": "alternatives",
                    "valuation_assumptions": {"methods": ["eps_multiple"]},
                },
            },
            {
                "agent_id": "A03",
                "candidate_briefs": [{"ticker": "ABC", "target_price": "120"}],
                "decision_brief": {
                    "candidate_briefs": [{"ticker": "ABC", "target_price": "121"}],
                    "allocation_mode": "alternatives",
                },
            },
        ],
    }


def test_context_compactor_removes_only_exact_aliases_and_preserves_proposals() -> None:
    context = _context_for_compaction()
    original = copy.deepcopy(context)
    compacted = compact_five_question_context(context)

    assert "memory" not in compacted
    assert context == original
    assert compacted["memory_context"]["retrieval_metadata"] == {"reason": "fixture"}
    assert compacted["memory_context"]["reused"][0]["excerpt"].startswith("[Supplied evidence record src_exact@v1")
    assert compacted["memory_context"]["reused"][0]["source_versions"] == original["memory_context"]["reused"][0]["source_versions"]
    assert compacted["memory_context"]["reused"][1]["excerpt"] == "Unique memory evidence must stay."
    assert "candidate_briefs" not in compacted["prior_outputs"][0]["decision_brief"]
    assert compacted["prior_outputs"][0]["decision_brief"]["allocation_mode"] == "alternatives"
    assert compacted["prior_outputs"][0]["decision_brief"]["valuation_assumptions"] == {"methods": ["eps_multiple"]}
    # A nonidentical nested candidate array remains available in full.
    assert "candidate_briefs" in compacted["prior_outputs"][1]["decision_brief"]
    assert compacted["fact_claims"] == original["fact_claims"]


def test_context_compactor_requires_matching_source_identity_and_exact_excerpt() -> None:
    context = _context_for_compaction()
    reused = context["memory_context"]["reused"]
    reused.append({
        "record_id": "src_exact",
        "record_type": "source",
        "excerpt": "A longer excerpt carries unique lines.",
        "source_versions": [{"id": "src_exact", "version": 1, "content_hash": "hash-exact"}],
    })
    reused.append({
        "record_id": "src_exact",
        "record_type": "source",
        "excerpt": "L1: exact supplied evidence",
        "source_versions": [{"id": "src_exact", "version": 2, "content_hash": "hash-exact"}],
    })
    compacted = compact_five_question_context(context, agent_id="A11")
    assert compacted["memory_context"]["reused"][2]["excerpt"] == "A longer excerpt carries unique lines."
    assert compacted["memory_context"]["reused"][3]["excerpt"] == "L1: exact supplied evidence"


def test_context_compactor_keeps_divergent_aliases_and_non_target_contexts() -> None:
    context = _context_for_compaction()
    context["memory"] = {"different": True}
    divergent = compact_five_question_context(context, agent_id="A11")
    assert divergent["memory"] == {"different": True}

    legacy = compact_five_question_context(context, agent_id="A10")
    assert legacy == context
    assert legacy is not context


def test_context_compactor_is_idempotent_and_reduces_captured_packet_when_available() -> None:
    path = Path("runtime/qa/laya-upgrade/real-joint-workflow-3-astra-prompt.txt")
    if not path.exists():
        pytest.skip("captured runtime prompt is not present in this checkout")
    text = path.read_text(encoding="utf-8")
    packet_text = text.split("<untrusted_evidence_packet>\n", 1)[1].split("\n</untrusted_evidence_packet>", 1)[0]
    context = json.loads(packet_text)
    before = len(json.dumps(context, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    compacted = compact_five_question_context(context)
    after = len(json.dumps(compacted, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    assert after < before
    assert before - after > 20_000
    assert compact_five_question_context(compacted) == compacted
