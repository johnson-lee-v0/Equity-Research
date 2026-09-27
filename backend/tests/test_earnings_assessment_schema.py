"""Verified earnings must request the economic proposal in structured output."""
from __future__ import annotations

import asyncio
import copy

import pytest

from backend.app.orchestration.workflow import Orchestrator
from backend.app.providers.codex import normalize_output_schema
from backend.app.providers.earnings_assessment_schema import constrain_earnings_assessment_schema
from backend.app.research.decision_questions import constrain_five_question_schema
from backend.app.schemas import AgentOutputPayload
from backend.tests.test_assessment_pipeline import answer, case
from backend.tests.test_discovery_handoff import FakeProvider, FakeRegistry
from backend.tests.test_five_question_commit_path import _DeterministicLaya


def schema():
    generic = constrain_five_question_schema(AgentOutputPayload.model_json_schema(), agent_id="A11")
    return normalize_output_schema(constrain_earnings_assessment_schema(generic, ticker="COST"))


BASELINE = {"key": "baseline_eps", "kind": "fact", "value": "18.21", "unit": "USD/share", "currency": "USD",
            "period": "annual period ended 2025-08-31", "scale": None, "basis": "GAAP diluted",
            "statement_type": "SEC US-GAAP diluted EPS", "fact_claim_ids": ["fact_baseline"], "source_refs": ["src_baseline"],
            "rationale": "Historical reported diluted EPS."}


def baseline_schema():
    generic = constrain_five_question_schema(AgentOutputPayload.model_json_schema(), agent_id="A11")
    return normalize_output_schema(constrain_earnings_assessment_schema(generic, ticker="COST", baseline_input=BASELINE))


def financial_schema(*, baseline=True, identifiers=None, normalized=True):
    generic = constrain_five_question_schema(AgentOutputPayload.model_json_schema(), agent_id="A11")
    projected = constrain_earnings_assessment_schema(
        generic, ticker="COST", baseline_input=BASELINE if baseline else None,
        valuation_fact_ids=["fact_baseline", "fact_prior_eps"] if identifiers is None else identifiers)
    return normalize_output_schema(projected) if normalized else projected


def test_empty_candidate_and_null_valuation_are_not_valid_provider_shapes():
    projected = schema()
    candidates = projected["properties"]["candidate_briefs"]
    assert candidates["minItems"] == candidates["maxItems"] == 1
    assert "candidate_briefs" in projected["required"]
    candidate = projected["$defs"]["CandidateDecisionBrief"]
    assert candidate["properties"]["ticker"]["enum"] == ["COST"]
    assert candidate["properties"]["valuation_assumptions"]["$ref"] == "#/$defs/ValuationAssumptions"
    assert "anyOf" not in candidate["properties"]["valuation_assumptions"]
    assert "valuation_assumptions" in candidate["required"]
    assert candidate["properties"]["key_questions"]["minItems"] == candidate["properties"]["key_questions"]["maxItems"] == 5


def test_methods_and_each_scenario_are_required_after_codex_normalization():
    projected = schema()
    definitions = projected["$defs"]
    methods = definitions["ValuationAssumptions"]["properties"]["methods"]
    assert methods["minItems"] == 1
    assert methods["items"] == {"$ref": "#/$defs/ValuationMethodProposal"}
    assert definitions["ValuationMethodProposal"]["properties"]["name"]["minLength"] == 1
    assert definitions["ValuationMethodProposal"]["properties"]["scenarios"]["$ref"] == "#/$defs/ValuationScenarioSet"
    scenarios = definitions["ValuationScenarioSet"]
    assert scenarios["required"] == ["bear", "base", "bull"]
    assert all(scenarios["properties"][name] == {"$ref": "#/$defs/ValuationScenarioProposal"} for name in scenarios["required"])


def test_cli_strict_subset_has_no_siblings_on_direct_references():
    # Standard JSON Schema allows $ref + description, but the live Codex CLI
    # rejects that shape with invalid_json_schema before generation starts.
    def inspect(node):
        if isinstance(node, dict):
            if "$ref" in node:
                assert set(node) == {"$ref"}, node
            for child in node.values():
                inspect(child)
        elif isinstance(node, list):
            for child in node:
                inspect(child)
    inspect(schema())
    inspect(baseline_schema())
    inspect(financial_schema())
    inspect(financial_schema(baseline=False, identifiers=[]))


def test_baseline_fields_are_fixed_without_a_generic_union_escape():
    projected = baseline_schema()
    definitions = projected["$defs"]
    assert definitions["ValuationInput"]["anyOf"] == [
        {"$ref": "#/$defs/ValuationBaselineInput"}, {"$ref": "#/$defs/ValuationOtherInput"}]
    exact = definitions["ValuationBaselineInput"]
    for field in ("key", "kind", "value", "unit", "currency", "period", "basis", "statement_type"):
        assert exact["properties"][field] == {"type": "string", "enum": [BASELINE[field]]}
    assert exact["properties"]["scale"] == {"type": "null"}
    for field in ("fact_claim_ids", "source_refs"):
        assert exact["properties"][field] == {"type": "array", "items": {"type": "string", "enum": BASELINE[field]}, "minItems": 1, "maxItems": 1}
    other_keys = definitions["ValuationOtherInput"]["properties"]["key"]["enum"]
    assert all(key not in other_keys for key in ("baseline_eps", "Baseline EPS", "baseline eps", "BASELINE_EPS", "baselineeps"))
    assert set(exact["required"]) == set(exact["properties"])
    assert "rationale" in exact["properties"]  # Assumption explanation remains editable.
    assert definitions["ValuationMethodProposal"]["properties"]["baseline_eps"] == {
        "anyOf": [{"type": "string", "enum": [BASELINE["value"]]}, {"type": "null"}]}


@pytest.mark.parametrize("keys", [
    ("forecast_eps", "forecast_diluted_eps", "eps", "forward_diluted_eps", "exit_multiple", "exit_pe", "pe"),
    ("forecast_fcf", "forecast_unlevered_fcf", "fcf", "free_cash_flow", "discount_rate", "wacc", "terminal_growth", "g"),
    ("forecast_metric", "ebitda", "metric", "exit_ev_multiple", "ev_multiple", "multiple", "diluted_shares", "shares"),
    ("nav_per_unit", "underlying_value", "excess_cash", "cash", "debt", "preferred_claims", "preferred", "minority_interest", "minority", "adr_ratio"),
])
def test_baseline_lock_preserves_each_alternative_method_input_family(keys):
    allowed = baseline_schema()["$defs"]["ValuationOtherInput"]["properties"]["key"]["enum"]
    assert set(keys).issubset(allowed)
    financial_allowed = financial_schema()["$defs"]["ValuationOtherInput"]["properties"]["key"]["enum"]
    assert set(keys).issubset(financial_allowed)


@pytest.mark.parametrize("normalized", [False, True])
@pytest.mark.parametrize("baseline", [False, True])
def test_financial_operands_exclude_new_and_operating_claims_without_changing_context(normalized, baseline):
    projected = financial_schema(baseline=baseline, normalized=normalized)
    unbounded = constrain_earnings_assessment_schema(
        constrain_five_question_schema(AgentOutputPayload.model_json_schema(), agent_id="A11"),
        ticker="COST", baseline_input=BASELINE if baseline else None)
    if normalized:
        unbounded = normalize_output_schema(unbounded)
    definitions = projected["$defs"]
    inputs = ["ValuationBaselineInput", "ValuationOtherInput"] if baseline else ["ValuationInput"]
    for name in [*inputs, "ValuationMethodProposal", "ValuationNormalizationAdjustment"]:
        ids = definitions[name]["properties"]["fact_claim_ids"]
        expected = ["fact_baseline"] if name == "ValuationBaselineInput" else ["fact_baseline", "fact_prior_eps"]
        assert ids["items"]["enum"] == expected
        assert "c8" not in ids["items"]["enum"]  # Newly proposed transcript-header price.
        assert "fact_operating" not in ids["items"]["enum"]  # Valid operating fact, not a financial operand.
        assert "Calculation operands only" in ids["description"]
    # The regression must not ban transcript/release context used to explain
    # growth and multiples or remove operating evidence from the five answers.
    for name, before in unbounded["$defs"].items():
        if "source_refs" in before.get("properties", {}):
            assert definitions[name]["properties"]["source_refs"] == before["properties"]["source_refs"]
    assert definitions["KeyQuestionProposal"] == unbounded["$defs"]["KeyQuestionProposal"]
    assert definitions["ValuationScenarioProposal"] == unbounded["$defs"]["ValuationScenarioProposal"]
    reference_input = definitions["ValuationOtherInput" if baseline else "ValuationInput"]
    if baseline:
        assert "reference_price" in reference_input["properties"]["key"]["enum"]
    assert reference_input["properties"]["fact_claim_ids"]["items"]["enum"] == ["fact_baseline", "fact_prior_eps"]


@pytest.mark.parametrize("normalized", [False, True])
def test_empty_financial_pool_has_no_invalid_enum_or_impossible_array(normalized):
    projected = financial_schema(baseline=False, identifiers=[], normalized=normalized)
    definitions = projected["$defs"]
    for name in ("ValuationInput", "ValuationMethodProposal"):
        node = definitions[name]["properties"]["fact_claim_ids"]
        assert node["maxItems"] == 0
        assert "enum" not in node["items"]
    assert definitions["ValuationMethodProposal"]["properties"]["normalization_adjustments"]["maxItems"] == 0

    def inspect(node):
        if isinstance(node, dict):
            assert node.get("enum") != []
            if "maxItems" in node:
                assert node.get("minItems", 0) <= node["maxItems"]
            for child in node.values():
                inspect(child)
        elif isinstance(node, list):
            for child in node:
                inspect(child)
    inspect(projected)


@pytest.mark.parametrize("identifiers", [[], ["fact_other"]])
def test_baseline_outside_financial_pool_fails_before_dispatch(identifiers):
    with pytest.raises(ValueError, match="baseline is outside"):
        financial_schema(identifiers=identifiers)


@pytest.mark.parametrize("identifiers", [["c8"], ["fact_baseline", 3], [["fact_baseline"]], "fact_baseline"])
def test_financial_allowlist_requires_canonical_saved_id_list(identifiers):
    with pytest.raises(ValueError, match="saved canonical fact IDs"):
        financial_schema(identifiers=identifiers)


def test_financial_pool_deduplicates_ids_without_widening_canonical_baseline():
    projected = financial_schema(identifiers=["fact_baseline", "fact_prior_eps", "fact_baseline"])
    assert projected["$defs"]["ValuationMethodProposal"]["properties"]["fact_claim_ids"]["items"]["enum"] == ["fact_baseline", "fact_prior_eps"]
    assert projected["$defs"]["ValuationBaselineInput"]["properties"]["fact_claim_ids"]["items"]["enum"] == ["fact_baseline"]


@pytest.mark.parametrize("field,value", [("key", "eps"), ("kind", "assumption"), ("fact_claim_ids", []), ("source_refs", ["a", "b"]), ("value", 18.21), ("value", None), ("currency", "")])
def test_invalid_canonical_baseline_fails_before_dispatch(field, value):
    invalid = BASELINE | {field: value}
    with pytest.raises(ValueError, match="Canonical earnings baseline"):
        constrain_earnings_assessment_schema(AgentOutputPayload.model_json_schema(), ticker="COST", baseline_input=invalid)


def test_duplicate_legacy_target_fields_are_pruned_but_action_contract_remains():
    candidate = schema()["$defs"]["CandidateDecisionBrief"]
    assert not any(name.startswith("target_price") for name in candidate["properties"])
    assert not any(name.startswith("target_price") for name in candidate["required"])
    for name in ("entry_zone", "entry_advice", "entry_plan", "stop_price", "risks", "catalysts", "watch_triggers", "action_plan",
                 "key_questions", "valuation_assumptions", "laya_response", "recommended_shares", "borrow_available", "margin_available"):
        assert name in candidate["properties"]


def test_candidate_and_valuation_precede_narrative_without_narrowing_method_drivers():
    projected = schema()
    assert next(iter(projected["properties"])) == "candidate_briefs"
    candidate = projected["$defs"]["CandidateDecisionBrief"]["properties"]
    assert list(candidate)[:3] == ["ticker", "valuation_assumptions", "key_questions"]
    assert list(projected["$defs"]["ValuationMethodProposal"]["properties"])[:3] == ["name", "inputs", "scenarios"]
    assert projected["properties"]["analysis"]["maxLength"] == 900
    original = normalize_output_schema(AgentOutputPayload.model_json_schema())
    # EPS, DCF, EV and NAV plus their historical aliases remain usable. Only
    # the required structured container changes; no formula or price is set.
    for definition in ("ValuationMethodProposal", "ValuationScenarioProposal", "ValuationInput"):
        before = original["$defs"][definition]["properties"]
        after = projected["$defs"][definition]["properties"]
        assert before.keys() == after.keys()
        for field in before:
            if definition == "ValuationMethodProposal" and field in {"name", "scenarios"}:
                continue
            assert before[field] == after[field], (definition, field)


def test_generic_and_historical_schema_remains_unchanged():
    generic = constrain_five_question_schema(AgentOutputPayload.model_json_schema(), agent_id="A11")
    before = copy.deepcopy(generic)
    constrain_earnings_assessment_schema(generic, ticker="COST")
    assert generic == before
    assert generic["properties"]["candidate_briefs"]["maxItems"] == 3
    assert "minItems" not in generic["properties"]["candidate_briefs"]
    assert generic["properties"]["analysis"]["maxLength"] == 4000
    assert any(branch.get("type") == "null" for branch in generic["$defs"]["CandidateDecisionBrief"]["properties"]["valuation_assumptions"]["anyOf"])


@pytest.mark.parametrize("ticker,document", [("", None), ("COST", {"type": "object", "properties": {}})])
def test_missing_verified_ticker_or_schema_fails_before_generation(ticker, document):
    with pytest.raises(ValueError, match="Verified earnings assessment schema"):
        constrain_earnings_assessment_schema(document or AgentOutputPayload.model_json_schema(), ticker=ticker)


def test_verified_earnings_dispatch_uses_required_schema_and_preserves_evidence(case):
    repo, run_id, _, source, _, _ = case
    class InspectingProvider(FakeProvider):
        async def execute(self, attempt_id, prompt, config, output_schema, workdir, on_event=None, **kwargs):
            effective = normalize_output_schema(output_schema)
            assert next(iter(effective["properties"])) == "candidate_briefs"
            assert effective["properties"]["candidate_briefs"]["minItems"] == 1
            assert effective["$defs"]["CandidateDecisionBrief"]["properties"]["ticker"]["enum"] == ["ACME"]
            assert effective["$defs"]["ValuationMethodProposal"]["properties"]["scenarios"]["$ref"] == "#/$defs/ValuationScenarioSet"
            fixed = effective["$defs"]["ValuationBaselineInput"]["properties"]
            assert fixed["value"]["enum"] == ["10.00"]
            assert fixed["period"]["enum"] == ["52 weeks ended 2026-08-30"]
            assert "baseline_eps" not in effective["$defs"]["ValuationOtherInput"]["properties"]["key"]["enum"]
            return await super().execute(attempt_id, prompt, config, output_schema, workdir, on_event, **kwargs)
    provider = InspectingProvider(answer)
    engine = Orchestrator(repo, FakeRegistry(provider), repo.config, laya_runtime=_DeterministicLaya())
    repo.control("run", run_id, "run_once")
    asyncio.run(engine.run(run_id))
    assert repo.run_record(run_id)["status"] == "completed"
    assert len(provider.calls) == 1
    retained = next(item for item in provider.calls[0]["packet"]["evidence"] if item["id"] == source)
    assert "Diluted | 10.00 | 8.00" in retained["content"]
