"""Required structure for the verified, single-company earnings assessment.

The shared historical payload and generic five-question schema remain broad.
This projection is used only after the earnings package has been verified and
the compiler has supplied valuation evidence. The calculator still validates
every fact, assumption, scenario and price independently after generation.
"""
from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any


def _lock_baseline_input(definitions: dict[str, Any], baseline: Mapping[str, Any]) -> None:
    """Reserve baseline_eps for the exact source-bound row, without `not`.

    The generic branch uses the calculator's supported keys and aliases so it
    cannot provide a second path for rewriting baseline metadata. No method
    is forced to use EPS; DCF, EV and NAV keep their ordinary input surface.
    """
    from ..research.valuation import _METHOD_INPUT_ALIASES

    if baseline.get("key") != "baseline_eps" or baseline.get("kind") != "fact":
        raise ValueError("Canonical earnings baseline must be a baseline_eps fact input.")
    identity_fields = ("value", "unit", "currency", "period", "scale", "basis", "statement_type")
    if any(key not in baseline or baseline[key] is not None and not isinstance(baseline[key], str) for key in identity_fields):
        raise ValueError("Canonical earnings baseline has incomplete typed metadata.")
    if any(not isinstance(baseline.get(key), str) or not baseline[key].strip() for key in ("value", "unit", "currency", "period", "basis")):
        raise ValueError("Canonical earnings baseline requires its reported value, unit, currency, period and accounting basis.")
    for key in ("fact_claim_ids", "source_refs"):
        values = baseline.get(key)
        if not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], str) or not values[0]:
            raise ValueError("Canonical earnings baseline requires one saved fact and one source identity.")
    original = definitions.get("ValuationInput")
    if not isinstance(original, dict) or not isinstance(original.get("properties"), dict):
        raise ValueError("Verified earnings assessment schema is missing typed valuation inputs.")
    exact = copy.deepcopy(original)
    exact["title"] = "Canonical historical EPS baseline"
    exact["description"] = "Copy this factual input exactly. Put forecast fiscal periods and assumptions on the method and scenarios."
    for key, value in {"key": "baseline_eps", "kind": "fact", **{key: baseline[key] for key in identity_fields}}.items():
        exact["properties"][key] = {"type": "null"} if value is None else {"type": "string", "enum": [value]}
    for key in ("fact_claim_ids", "source_refs"):
        exact["properties"][key] = {"type": "array", "items": {"type": "string", "enum": baseline[key]}, "minItems": 1, "maxItems": 1}
    exact["required"] = list(exact["properties"])

    generic = copy.deepcopy(original)
    generic["title"] = "Other valuation input"
    # Keep every supported scalar alias, ordinary forecast controls, and
    # operational context keys. Baseline spelling variants cannot bypass the
    # exact row: keys are emitted in their explicit calculator spelling.
    keys = {key for aliases in _METHOD_INPUT_ALIASES.values() for key in aliases}
    for definition_name in ("ValuationMethodProposal", "ValuationScenarioProposal"):
        keys.update(key for key, value in definitions[definition_name]["properties"].items()
                    if value.get("type") != "array" and "$ref" not in value and key not in {"name", "scenarios"})
    keys.update({"annual_eps_growth", "growth_rate", "reference_price", "current_price", "net_sales_growth", "paid_members_growth",
                 "renewal_us_canada", "renewal_worldwide", "gross_margin", "operating_margin", "capex", "capex_cash_ppe"})
    keys.discard("baseline_eps")
    generic["properties"]["key"]["enum"] = sorted(keys)
    definitions["ValuationBaselineInput"] = exact
    definitions["ValuationOtherInput"] = generic
    definitions["ValuationInput"] = {"anyOf": [{"$ref": "#/$defs/ValuationBaselineInput"}, {"$ref": "#/$defs/ValuationOtherInput"}]}
    # The redundant scalar alias may be omitted, but cannot contradict the
    # bound row if the provider chooses to emit it as well.
    definitions["ValuationMethodProposal"]["properties"]["baseline_eps"] = {
        "anyOf": [{"type": "string", "enum": [baseline["value"]]}, {"type": "null"}]}


def _bound_financial_fact_ids(definitions: dict[str, Any], identifiers: list[str]) -> None:
    """Separate numerical operands from operating commentary at dispatch."""
    if not isinstance(identifiers, list) or any(not isinstance(item, str) or not item.startswith("fact_") for item in identifiers):
        raise ValueError("Valuation financial evidence must use saved canonical fact IDs.")
    allowed = list(dict.fromkeys(identifiers))
    baseline = definitions.get("ValuationBaselineInput", {}).get("properties", {}).get("fact_claim_ids")
    if baseline and any(item not in allowed for item in baseline.get("items", {}).get("enum", [])):
        raise ValueError("The canonical EPS baseline is outside the eligible financial fact set.")
    for name in ("ValuationInput", "ValuationBaselineInput", "ValuationOtherInput", "ValuationMethodProposal", "ValuationNormalizationAdjustment"):
        node = definitions.get(name, {}).get("properties", {}).get("fact_claim_ids")
        if not isinstance(node, dict):
            continue
        node["description"] = (
            "Calculation operands only: use these saved financial fact IDs. Operating observations and market-price page headers are not financial operands. "
            "Explain growth and multiple assumptions with source_refs and rationale; keep operating facts in the five research questions."
        )
        if allowed:
            items = node.setdefault("items", {"type": "string"})
            # A canonical baseline remains narrower than the financial pool.
            existing = items.get("enum")
            items["enum"] = [item for item in allowed if existing is None or item in existing]
            if not items["enum"]:
                raise ValueError("A required valuation fact reference is outside the eligible financial fact set.")
        elif name == "ValuationNormalizationAdjustment":
            # An adjustment requires one fact, so the enclosing optional
            # adjustment list is disabled instead of making minItems > maxItems.
            definitions["ValuationMethodProposal"]["properties"]["normalization_adjustments"]["maxItems"] = 0
        else:
            node.pop("minItems", None)
            node["maxItems"] = 0
            node["items"] = {"type": "string"}


def constrain_earnings_assessment_schema(schema: Mapping[str, Any], *, ticker: str, baseline_input: Mapping[str, Any] | None = None,
                                        valuation_fact_ids: list[str] | None = None) -> dict[str, Any]:
    """Require one company and an explicit three-scenario valuation proposal.

    Method-level scenarios are the unambiguous form for this focused request.
    All EPS, DCF, enterprise-value and NAV driver fields remain available; no
    multiple, growth rate, target price or valuation formula is manufactured.
    """
    constrained = copy.deepcopy(dict(schema))
    ticker = str(ticker or "").strip()
    if not ticker:
        raise ValueError("Verified earnings assessment schema requires its company ticker.")
    definitions = constrained.get("$defs") or {}
    needed = ("CandidateDecisionBrief", "ValuationAssumptions", "ValuationMethodProposal", "ValuationScenarioSet", "ValuationScenarioProposal")
    if "candidate_briefs" not in constrained.get("properties", {}) or any(name not in definitions for name in needed):
        raise ValueError("Verified earnings assessment schema is missing its candidate or valuation definitions.")

    def prioritize(owner: dict[str, Any], names: tuple[str, ...]) -> None:
        properties = owner["properties"]
        owner["properties"] = {name: properties[name] for name in names if name in properties} | {
            name: value for name, value in properties.items() if name not in names}

    def require(owner: dict[str, Any], *names: str) -> None:
        owner["required"] = list(dict.fromkeys([*owner.get("required", []), *names]))

    candidate_array = constrained["properties"]["candidate_briefs"]
    candidate_array.update(minItems=1, maxItems=1,
        description="Exactly one assessment for the verified company. Complete its valuation and five key questions; prose-only prices are insufficient.")
    require(constrained, "candidate_briefs")
    candidate = definitions["CandidateDecisionBrief"]
    candidate["properties"]["ticker"]["enum"] = [ticker]
    # The CLI's strict schema subset forbids siblings on a direct $ref,
    # although standard JSON Schema permits them. Describe the definition.
    candidate["properties"]["valuation_assumptions"] = {"$ref": "#/$defs/ValuationAssumptions"}
    require(candidate, "valuation_assumptions", "key_questions")
    valuation = definitions["ValuationAssumptions"]
    valuation["description"] = "Required structured assumptions from sourced facts, with a reasoned bear/base/bull forecast. Code computes the prices."
    valuation["properties"]["methods"].update(minItems=1, items={"$ref": "#/$defs/ValuationMethodProposal"},
        description="At least one structured valuation method with source-bound inputs and explicit bear/base/bull scenario drivers; a method name alone is insufficient.")
    require(valuation, "methods")
    method = definitions["ValuationMethodProposal"]
    method["properties"]["name"]["minLength"] = 1
    method["properties"]["scenarios"] = {"$ref": "#/$defs/ValuationScenarioSet"}
    require(method, "name", "scenarios")
    scenarios = definitions["ValuationScenarioSet"]
    scenarios["description"] = "All three scenario objects are required here. Supply the drivers appropriate to this method and explain the assumptions; narrative prices are not calculation inputs."
    for name in ("bear", "base", "bull"):
        scenarios["properties"][name] = {"$ref": "#/$defs/ValuationScenarioProposal"}
    require(scenarios, "bear", "base", "bull")
    if baseline_input is not None:
        _lock_baseline_input(definitions, baseline_input)
    if valuation_fact_ids is not None:
        _bound_financial_fact_ids(definitions, valuation_fact_ids)

    # Canonical prices are calculated from the method above. Asking for a
    # second legacy target repeats the proposal and can contradict that math.
    for name in ("target_price", "target_price_currency", "target_price_as_of", "target_price_source_refs", "target_price_basis", "target_price_missing_reason"):
        candidate["properties"].pop(name, None)
    candidate["required"] = [name for name in candidate["required"] if name in candidate["properties"]]

    # Structured output follows schema property order. Put the reviewable
    # economic proposal ahead of optional execution fields and bounded prose,
    # so spending the narrative budget cannot substitute for the proposal.
    prioritize(constrained, ("candidate_briefs", "status", "research_contract", "title", "summary", "analysis"))
    prioritize(candidate, ("ticker", "valuation_assumptions", "key_questions", "laya_response", "horizon", "stance"))
    prioritize(method, ("name", "inputs", "scenarios", "rationale", "horizon_months", "forecast_years", "forecast_span_rationale"))
    for name in ("summary", "analysis"):
        if name in constrained["properties"]:
            constrained["properties"][name]["description"] = "Concise reader-facing explanation of the completed candidate assessment. Keep all required valuation drivers in candidate_briefs, rather than only in this prose."
    if "analysis" in constrained["properties"]:
        constrained["properties"]["analysis"]["maxLength"] = min(900, constrained["properties"]["analysis"].get("maxLength", 900))
    return constrained
