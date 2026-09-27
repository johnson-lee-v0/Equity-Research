"""Code-owned five-question research contract and joint-review helpers.

Provider text is an input to this module.  Question order, evidence status,
fact budgets and model-review status are all derived here so a provider cannot
claim that an unsupported citation was verified or that a local classifier
agreed with Astra.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import copy
import hashlib
import json
import re
from typing import Any

from ..providers.five_question_schema import simplify_five_question_provider_schema
from ..schemas import (
    AstraLayaResponse,
    CandidateDecisionBrief,
    JointDecisionReview,
    KeyQuestion,
    KeyQuestionProposal,
    VerifiedQuestionFact,
)


FIVE_QUESTION_CONTRACT = "five-questions.v1"
FIVE_QUESTION_KEYS = (
    "opportunity",
    "valuation",
    "catalyst",
    "downside",
    "portfolio_action",
)
QUESTION_TEXT: dict[str, str] = {
    "opportunity": "What is the opportunity, and what is the market missing?",
    "valuation": "What is it worth versus the entry price and alternatives?",
    "catalyst": "What can change the outcome, and by when?",
    "downside": "What would prove us wrong, and how could we lose money?",
    "portfolio_action": "What should we do now, and does it fit the portfolio?",
}
MAX_CANDIDATES = 3
MAX_FACTS = 15
MAX_QUERIES = 5
MAX_DISCOVERY_PAGES = 6
MAX_CONTINUATION_QUERIES = 1
MAX_CONTINUATION_PAGES = 3
MAX_ANALYSIS_CHARS = 4_000
MAX_PACKET_CHARS = 24_000
_FACT_REFERENCE_KEYS = frozenset({"supporting_claim_ids", "contradicting_claim_ids", "fact_claim_ids"})


class QuestionContractError(ValueError):
    """A bounded corrective validation error for a five-question attempt."""


class LayaPacketEvidenceError(QuestionContractError):
    """A response fact cannot be represented by the frozen Laya packet."""


def _schema_definition(schema: Mapping[str, Any], name: str) -> dict[str, Any] | None:
    definitions = schema.get("$defs") if isinstance(schema.get("$defs"), Mapping) else {}
    value = definitions.get(name)
    return value if isinstance(value, dict) else None


def _schema_ref_definition(schema: Mapping[str, Any], value: Any) -> dict[str, Any] | None:
    """Resolve one local Pydantic ``$ref`` used by the provider schema."""
    if not isinstance(value, Mapping):
        return None
    reference = value.get("$ref")
    if isinstance(reference, str) and reference.startswith("#/$defs/"):
        return _schema_definition(schema, reference.removeprefix("#/$defs/"))
    for branch_key in ("anyOf", "oneOf", "allOf"):
        branches = value.get(branch_key)
        if isinstance(branches, Sequence) and not isinstance(branches, (str, bytes, bytearray)):
            for branch in branches:
                resolved = _schema_ref_definition(schema, branch)
                if resolved is not None:
                    return resolved
    return None


def _schema_property(schema: Mapping[str, Any], owner: Mapping[str, Any], name: str) -> dict[str, Any] | None:
    properties = owner.get("properties") if isinstance(owner.get("properties"), Mapping) else {}
    value = properties.get(name)
    if isinstance(value, dict):
        return value
    return None


def constrain_five_question_schema(
    schema: Mapping[str, Any],
    *,
    agent_id: str,
    task_kind: str = "",
) -> dict[str, Any]:
    """Apply contract-specific provider bounds to a copy of the output schema.

    The shared ``AgentOutputPayload`` schema must remain compatible with older
    runs.  New five-question attempts receive this stricter copy at dispatch,
    so a provider is rejected at the structured-output boundary instead of
    sending an over-limit payload that a later normalizer might trim.
    """
    constrained = simplify_five_question_provider_schema(schema, agent_id=agent_id)
    root = constrained
    root_properties = root.get("properties") if isinstance(root.get("properties"), Mapping) else {}

    def limit(owner: Mapping[str, Any], field: str, maximum: int) -> None:
        node = owner.get("properties", {}).get(field) if isinstance(owner.get("properties"), Mapping) else None
        if isinstance(node, dict):
            node["maxItems"] = maximum
            return
        if isinstance(node, list):
            for candidate in node:
                if isinstance(candidate, dict):
                    candidate["maxItems"] = maximum

    def max_length(owner: Mapping[str, Any], field: str, maximum: int) -> None:
        node = owner.get("properties", {}).get(field) if isinstance(owner.get("properties"), Mapping) else None
        if isinstance(node, dict):
            node["maxLength"] = maximum
            for candidate in node.get("anyOf", []) if isinstance(node.get("anyOf"), list) else []:
                if isinstance(candidate, dict) and candidate.get("type") == "string":
                    candidate["maxLength"] = maximum

    normalized_agent = str(agent_id or "").strip().upper()
    continuation = str(task_kind or "").startswith("universe_discovery_continuation_")
    if normalized_agent == "A00":
        routing = _schema_ref_definition(constrained, root_properties.get("routing_plan"))
        if routing:
            limit(routing, "research_queries", MAX_QUERIES)
            # Candidate scope is code-owned for the new route as well as for
            # A01/A03/A11.  A00 may still explain why fewer alternatives were
            # selected in its rationale.
            limit(routing, "tickers", MAX_CANDIDATES)
    elif normalized_agent == "A01":
        limit(root, "research_candidates", MAX_CANDIDATES)
        limit(root, "discovery_queries", MAX_CONTINUATION_QUERIES if continuation else MAX_QUERIES)
        limit(root, "discovery_urls", MAX_CONTINUATION_PAGES if continuation else MAX_DISCOVERY_PAGES)
        candidate_definition = _schema_definition(constrained, "ResearchCandidate")
        if candidate_definition:
            limit(candidate_definition, "source_urls", MAX_CONTINUATION_PAGES if continuation else MAX_DISCOVERY_PAGES)
    elif normalized_agent in {"A03", "A11"}:
        # Both aggregate and per-candidate aliases are accepted by the
        # repository, so constrain each independently.
        limit(root, "candidate_briefs", MAX_CANDIDATES)
        decision_brief = _schema_ref_definition(constrained, root_properties.get("decision_brief"))
        if decision_brief:
            limit(decision_brief, "candidate_briefs", MAX_CANDIDATES)
        limit(root, "fact_claims", MAX_FACTS)
        max_length(root, "analysis", MAX_ANALYSIS_CHARS)
        # The nested candidate definition already carries the per-question
        # array limits, but setting them here keeps this function resilient if
        # a future schema widens the shared historical model.
        candidate_definition = _schema_definition(constrained, "CandidateDecisionBrief")
        if candidate_definition:
            limit(candidate_definition, "key_questions", len(FIVE_QUESTION_KEYS))
            candidate_definition["properties"]["key_questions"]["minItems"] = len(FIVE_QUESTION_KEYS)
        decision_definition = _schema_definition(constrained, "DecisionBrief")
        if decision_definition:
            limit(decision_definition, "key_questions", len(FIVE_QUESTION_KEYS))
    return constrained


def _dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="python", exclude_none=False)
    return dict(value) if isinstance(value, Mapping) else {}


def is_five_question_contract(value: Any) -> bool:
    if isinstance(value, Mapping):
        value = value.get("research_contract")
    return str(value or "").strip() == FIVE_QUESTION_CONTRACT


def question_definitions() -> list[dict[str, str]]:
    return [{"key": key, "question": QUESTION_TEXT[key]} for key in FIVE_QUESTION_KEYS]


def normalize_question_key(value: Any) -> str:
    text = re.sub(r"[\s-]+", "_", str(value or "").strip().casefold())
    return text


def _proposal_values(raw: Any) -> list[Any]:
    if isinstance(raw, Mapping):
        # A mapping is convenient for tests and internal callers but a list is
        # retained as the public provider shape so duplicate keys are visible.
        return [dict(item, key=key) if isinstance(item, Mapping) else {"key": key, "answer": item} for key, item in raw.items()]
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
        return list(raw)
    return []


def normalize_question_proposals(raw: Any, *, require_exact: bool = True) -> tuple[list[dict[str, Any]], list[str]]:
    """Normalize proposal keys without dropping duplicate or unknown entries."""
    values = _proposal_values(raw)
    errors: list[str] = []
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(values):
        row = _dict(item)
        key = normalize_question_key(row.get("key"))
        if key not in FIVE_QUESTION_KEYS:
            errors.append(f"key_questions[{index}] has unsupported key {key or '<empty>'!r}")
            continue
        if key in seen:
            errors.append(f"key_questions contains duplicate key {key}")
            continue
        seen.add(key)
        row["key"] = key
        rows.append(row)
    if require_exact:
        missing = [key for key in FIVE_QUESTION_KEYS if key not in seen]
        if missing:
            errors.append("key_questions is missing required keys: " + ", ".join(missing))
        if len(values) != len(FIVE_QUESTION_KEYS):
            errors.append("key_questions must contain exactly five entries")
    return rows, errors


def _candidate_rows(payload: Any) -> list[dict[str, Any]]:
    value = _dict(payload)
    nested = _dict(value.get("decision_brief"))
    raw = []
    raw.extend(value.get("candidate_briefs") or [])
    raw.extend(nested.get("candidate_briefs") or [])
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_row in raw:
        row = _dict(raw_row)
        ticker = str(row.get("ticker") or row.get("symbol") or row.get("instrument") or "").strip().upper()
        key = ticker or f"<candidate-{len(rows) + 1}>"
        if key in seen:
            # Compatible aggregate paths often repeat exactly the same row.
            # Keep the richer row while still surfacing conflicting question
            # payloads through validation below.
            current = next(item for item in rows if item.get("_candidate_key") == key)
            if not current.get("key_questions") and row.get("key_questions"):
                current["key_questions"] = row["key_questions"]
            continue
        row["_candidate_key"] = key
        rows.append(row)
        seen.add(key)
    if not rows:
        brief_questions = nested.get("key_questions")
        if brief_questions:
            ticker = str(value.get("ticker") or "").strip().upper() or "<candidate-1>"
            rows.append({"ticker": ticker, "_candidate_key": ticker, "key_questions": brief_questions})
    return rows


def _question_rows_for_candidate(candidate: Mapping[str, Any], payload: Mapping[str, Any]) -> Any:
    raw = candidate.get("key_questions")
    if raw:
        return raw
    brief = _dict(payload.get("decision_brief"))
    if len(_candidate_rows(payload)) == 1:
        return brief.get("key_questions") or []
    return []


def _claim_id_set(payload: Mapping[str, Any]) -> set[str]:
    ids: set[str] = set()
    for raw in payload.get("fact_claims") or []:
        row = _dict(raw)
        for key in ("claim_id", "fact_id"):
            value = str(row.get(key) or "").strip()
            if value:
                ids.add(value)
    return ids


def _reference_ids(value: Any) -> set[str]:
    """Collect only declared fact references, never source/ticker identifiers."""
    result: set[str] = set()
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key in _FACT_REFERENCE_KEYS and isinstance(child, Sequence) and not isinstance(child, (str, bytes, bytearray)):
                result.update(str(item).strip() for item in child if str(item).strip())
            elif key not in {"unknowns", "answer", "decision_implication", "source_refs", "source_ref", "locator", "claim_id", "fact_id"}:
                result.update(_reference_ids(child))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for child in value:
            if isinstance(child, (Mapping, Sequence)) and not isinstance(child, (str, bytes, bytearray)):
                result.update(_reference_ids(child))
    return result


def _claim_identity_map(payload: Mapping[str, Any]) -> dict[str, str]:
    """Map local aliases and canonical IDs to one identity when both exist."""
    identities: dict[str, str] = {}
    for raw in payload.get("fact_claims") or []:
        row = _dict(raw)
        aliases = [str(row.get(key) or "").strip() for key in ("fact_id", "claim_id")]
        aliases = [item for item in aliases if item]
        if not aliases:
            continue
        canonical = next((item for item in aliases if item.startswith("fact_")), aliases[0])
        for alias in aliases:
            identities[alias] = canonical
    return identities


def count_distinct_research_facts(payload: Any, *, prior_fact_ids: Sequence[str] = ()) -> int:
    """Count selected facts globally; unused historical memory is free."""
    return len(distinct_research_fact_ids(payload))


def distinct_research_fact_ids(payload: Any) -> set[str]:
    """Return the canonical identities selected/emitted by one payload."""
    value = _dict(payload)
    identities = _claim_identity_map(value)
    selected = {identities.get(identifier, identifier) for identifier in _reference_ids(value)}
    # Every saved claim is a durable fact row.  Counting all claims keeps an
    # unreferenced narrative fact from evading the hard case budget.
    selected.update(identities.get(identifier, identifier) for identifier in _claim_id_set(value))
    for index, raw in enumerate(value.get("fact_claims") or []):
        if not _claim_identity_map({"fact_claims": [raw]}):
            selected.add(f"__claim_index_{index}")
    for index, raw in enumerate(value.get("calculations") or []):
        row = _dict(raw)
        for fact_index in row.get("input_fact_indices") or []:
            try:
                claim = _dict((value.get("fact_claims") or [])[int(fact_index)])
            except (IndexError, TypeError, ValueError):
                continue
            selected.update(identities.get(str(claim.get(key) or "").strip(), str(claim.get(key) or "").strip()) for key in ("claim_id", "fact_id") if str(claim.get(key) or "").strip())
    return selected


def validate_five_question_payload(
    payload: Any,
    *,
    prior_fact_ids: Sequence[str] = (),
    candidate_limit: int = MAX_CANDIDATES,
    fact_limit: int = MAX_FACTS,
    require_questions: bool = True,
) -> list[str]:
    """Return bounded contract errors; no list is truncated here."""
    value = _dict(payload)
    errors: list[str] = []
    rows = _candidate_rows(value)
    if len(rows) > candidate_limit:
        errors.append(f"The five-question contract permits at most {candidate_limit} candidates; received {len(rows)}.")
    question_ids = _claim_id_set(value)
    known_references = question_ids | {str(item).strip() for item in prior_fact_ids if str(item).strip()}
    for candidate_index, row in enumerate(rows):
        proposals, proposal_errors = normalize_question_proposals(_question_rows_for_candidate(row, value), require_exact=require_questions)
        errors.extend(f"candidate {candidate_index + 1}: {error}" for error in proposal_errors)
        for proposal in proposals:
            answer = str(proposal.get("answer") or "")
            implication = str(proposal.get("decision_implication") or "")
            if len(answer) > 800:
                errors.append(f"candidate {candidate_index + 1} {proposal['key']}: answer exceeds 800 characters")
            if len(implication) > 400:
                errors.append(f"candidate {candidate_index + 1} {proposal['key']}: implication exceeds 400 characters")
            supports = proposal.get("supporting_claim_ids") or []
            contradictions = proposal.get("contradicting_claim_ids") or []
            unknowns = proposal.get("unknowns") or []
            if len(supports) > 2:
                errors.append(f"candidate {candidate_index + 1} {proposal['key']}: at most two supporting facts")
            if len(contradictions) > 1:
                errors.append(f"candidate {candidate_index + 1} {proposal['key']}: at most one contradicting fact")
            if len(unknowns) > 2:
                errors.append(f"candidate {candidate_index + 1} {proposal['key']}: at most two unknowns")
            for identifier in [*supports, *contradictions]:
                identifier = str(identifier).strip()
                if identifier and identifier not in known_references:
                    errors.append(f"candidate {candidate_index + 1} {proposal['key']}: unknown fact reference {identifier}")
    count = count_distinct_research_facts(value, prior_fact_ids=prior_fact_ids)
    if count > fact_limit:
        errors.append(f"The five-question case budget is {fact_limit} distinct facts; the proposal selects {count}.")
    analysis = str(value.get("analysis") or "")
    if len(analysis) > MAX_ANALYSIS_CHARS:
        errors.append(f"Five-question analysis must be at most {MAX_ANALYSIS_CHARS} characters.")
    route = _dict(value.get("routing_plan"))
    queries = route.get("research_queries") or []
    if len(queries) > MAX_QUERIES:
        errors.append(f"Initial five-question discovery permits at most {MAX_QUERIES} targeted queries.")
    return errors


def assert_valid_five_question_payload(payload: Any, **kwargs: Any) -> None:
    errors = validate_five_question_payload(payload, **kwargs)
    if errors:
        raise QuestionContractError("Five-question contract validation failed: " + " ".join(errors[:8]))


def _fact_is_verified(fact: Mapping[str, Any]) -> bool:
    semantic = str(fact.get("semantic_status") or "").casefold()
    validation = str(fact.get("validation_status") or "").casefold()
    freshness = str(fact.get("freshness") or fact.get("freshness_status") or "").casefold()
    # Both semantic binding and repository validation are required.  A stale
    # historical validation must never become a current verified question
    # fact merely because its recorded status says validated.
    if semantic != "supported" or validation != "validated":
        return False
    return freshness == "fresh"


def project_key_questions(raw: Any, fact_lookup: Mapping[str, Any] | Sequence[Any] | None = None) -> list[dict[str, Any]]:
    """Project exactly five code-owned questions and verified facts."""
    proposals, _ = normalize_question_proposals(raw, require_exact=False)
    lookup: dict[str, dict[str, Any]] = {}
    if isinstance(fact_lookup, Mapping):
        lookup = {str(key): _dict(item) for key, item in fact_lookup.items()}
    elif isinstance(fact_lookup, Sequence) and not isinstance(fact_lookup, (str, bytes, bytearray)):
        lookup = {str(_dict(item).get("fact_id")): _dict(item) for item in fact_lookup if _dict(item).get("fact_id")}
    by_key = {row.get("key"): row for row in proposals}
    result: list[dict[str, Any]] = []
    for key in FIVE_QUESTION_KEYS:
        row = by_key.get(key) or {}
        verified: list[dict[str, Any]] = []
        unresolved: list[str] = []
        for role, field in (("support", "supporting_claim_ids"), ("contradiction", "contradicting_claim_ids")):
            for identifier in row.get(field) or []:
                identifier = str(identifier).strip()
                fact = lookup.get(identifier)
                if not fact or not _fact_is_verified(fact):
                    if identifier:
                        unresolved.append(identifier)
                    continue
                verified.append(
                    {
                        "fact_id": str(fact.get("fact_id") or identifier),
                        "role": role,
                        "claim": str(fact.get("claim") or "")[:2_000],
                        "value": fact.get("value"),
                        "unit": fact.get("unit"),
                        "period": fact.get("period") or fact.get("period_end"),
                        "source_ref": str(fact.get("source_ref") or ""),
                        "locator": str(fact.get("locator") or ""),
                        "semantic_status": str(fact.get("semantic_status") or "supported"),
                        "freshness": str(fact.get("freshness") or fact.get("freshness_status") or "unknown"),
                    }
                )
        provider_unknowns = list(dict.fromkeys(str(item).strip() for item in row.get("unknowns") or [] if str(item).strip()))
        if unresolved:
            # Preserve every provider unknown and every unresolved reference
            # in one explicit bounded blocker instead of silently dropping a
            # third item to satisfy the display cardinality.
            unknowns = [
                "Unresolved evidence: "
                + "; ".join(provider_unknowns + [f"reference {identifier} is unresolved or stale" for identifier in unresolved])
            ]
        else:
            unknowns = list(dict.fromkeys(provider_unknowns))
        if not row:
            status = "unavailable"
        elif not verified:
            status = "unavailable"
        elif unresolved or provider_unknowns:
            status = "partial"
        else:
            status = "complete"
        result.append(
            {
                "key": key,
                "question": QUESTION_TEXT[key],
                "answer": str(row.get("answer") or ""),
                "decision_implication": str(row.get("decision_implication") or ""),
                "evidence_status": status,
                "verified_facts": verified[:3],
                "unknowns": unknowns,
            }
        )
    return result


def compact_fact(fact: Mapping[str, Any], *, role: str) -> dict[str, Any]:
    """Return the exact bounded fields used by a classifier packet."""
    return {
        "fact_id": str(fact.get("fact_id") or ""),
        "role": role,
        "claim": str(fact.get("claim") or ""),
        "value": fact.get("value"),
        "unit": fact.get("unit"),
        "period": fact.get("period") or fact.get("period_end"),
        "source_ref": str(fact.get("source_ref") or ""),
        "locator": str(fact.get("locator") or ""),
        "semantic_status": str(fact.get("semantic_status") or fact.get("validation_status") or "unavailable"),
        "freshness": str(fact.get("freshness") or fact.get("freshness_status") or "unknown"),
    }


_FACT_OBSERVATION_FIELDS = (
    "source_ref",
    "source_version",
    "source_hash",
    "locator",
    "subject",
    "metric",
    "claim",
    "value",
    "unit",
    "period",
    "period_start",
    "period_end",
    "currency",
    "scale",
    "basis",
    "share_basis",
    "statement_type",
)


def _observation_value(fact: Mapping[str, Any], field: str) -> str | None:
    aliases = {
        "source_version": ("source_version", "version"),
        "source_hash": ("source_hash", "content_hash"),
        "period": ("period", "period_end"),
        "period_end": ("period_end", "period"),
    }.get(field, (field,))
    for key in aliases:
        value = fact.get(key)
        if value not in (None, ""):
            text = str(value).strip()
            if field in {"unit", "currency"}:
                text = text.casefold()
            return text or None
    return None


def _fact_observation_key(fact: Mapping[str, Any]) -> tuple[str | None, ...]:
    """Identify one immutable observation without treating equal values as aliases."""
    return tuple(_observation_value(fact, field) for field in _FACT_OBSERVATION_FIELDS)


def _compact_packet_fact(fact: Mapping[str, Any], label: str) -> dict[str, Any]:
    """Expose only the bounded value fields needed by local Laya."""
    compact = {
        "id": label,
        "subject": str(fact.get("subject") or ""),
        "metric": str(fact.get("metric") or fact.get("claim") or ""),
        "value": fact.get("value"),
        "unit": fact.get("unit"),
        "period": fact.get("period") or fact.get("period_end"),
    }
    period_start = fact.get("period_start")
    if period_start not in (None, ""):
        compact["period_start"] = period_start
    period_end = fact.get("period_end")
    if period_end not in (None, "") and str(period_end).strip() != str(compact["period"]).strip():
        compact["period_end"] = period_end
    return compact


def _packet_json(value: Mapping[str, Any], *, max_chars: int = MAX_PACKET_CHARS) -> str:
    packet = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(packet) > max_chars:
        raise QuestionContractError(f"Laya packet exceeds the {max_chars}-character bound; no content was truncated.")
    return packet


def build_laya_disposition_packet(
    candidate: Mapping[str, Any] | CandidateDecisionBrief,
    questions: Sequence[Mapping[str, Any]],
    facts: Mapping[str, Any] | Sequence[Any],
    *,
    deterministic_summary: Mapping[str, Any] | None = None,
    max_chars: int = MAX_PACKET_CHARS,
) -> tuple[str, dict[str, dict[str, Any]]]:
    """Build one compact pre-A11 disposition packet for one candidate."""
    row = _dict(candidate)
    lookup = facts if isinstance(facts, Mapping) else {str(_dict(item).get("fact_id")): _dict(item) for item in facts if _dict(item).get("fact_id")}
    packet_questions: list[dict[str, Any]] = []
    fact_rows: list[dict[str, Any]] = []
    fact_labels: dict[str, str] = {}
    for question in questions:
        q = _dict(question)
        support_labels: list[str] = []
        contradiction_labels: list[str] = []
        for fact in q.get("verified_facts") or []:
            fact_value = _dict(fact)
            fact_id = str(fact_value.get("fact_id") or "").strip()
            fact_row = lookup.get(fact_id)
            if not fact_row or not fact_id:
                continue
            label = fact_labels.get(fact_id)
            if label is None:
                label = f"f{len(fact_rows) + 1}"
                fact_labels[fact_id] = label
                # Source IDs/locators remain in the durable receipt binding;
                # the classifier only needs exact selected values and units.
                fact_rows.append(_compact_packet_fact(fact_row, label))
            if str(fact_value.get("role") or "support") == "contradiction":
                contradiction_labels.append(label)
            else:
                support_labels.append(label)
        packet_question = {
            "key": str(q.get("key") or ""),
            # The full provider answer remains in the immutable output and
            # the UI projection.  The local classifier receives only the
            # short action implication so the five-question packet stays
            # within the runtime's 512-token input budget.
            "implication": str(q.get("decision_implication") or q.get("answer") or ""),
        }
        # Empty coverage arrays are redundant with the compact fact table;
        # retain every non-empty contradiction and unknown verbatim.
        if support_labels:
            packet_question["support"] = support_labels
        if contradiction_labels:
            packet_question["contradiction"] = contradiction_labels
        unknowns = [str(item) for item in q.get("unknowns") or []]
        if unknowns:
            packet_question["unknowns"] = unknowns
        if str(q.get("evidence_status") or "complete") != "complete":
            packet_question["evidence_status"] = str(q.get("evidence_status") or "unavailable")
        packet_questions.append(packet_question)
    body = {
        "version": FIVE_QUESTION_CONTRACT,
        "candidate": {key: row.get(key) for key in ("ticker", "direction") if row.get(key) not in (None, "")},
        "questions": packet_questions,
        "facts": fact_rows,
    }
    gates = _dict(deterministic_summary)
    if gates:
        body["gates"] = gates
    state = _packet_json(body, max_chars=max_chars)
    choices = {"disposition": {"type": "choice", "instructions": "Choose the bounded evidence disposition; probabilities are classifier scores, not investment odds.", "criteria": {
        "recommend": "Evidence and all entry and risk gates support entry.",
        "watchlist": "The thesis is plausible; wait for a named price or catalyst.",
        "decline": "The thesis is refuted or risk is unacceptable.",
        "needs_evidence": "Material evidence is missing or unresolved.",
    }}}
    return state, choices


def build_laya_resolution_packet(
    candidate: Mapping[str, Any] | CandidateDecisionBrief,
    questions: Sequence[Mapping[str, Any]],
    facts: Mapping[str, Any] | Sequence[Any],
    *,
    deterministic_summary: Mapping[str, Any] | None = None,
    astra_response: Mapping[str, Any] | AstraLayaResponse | None,
    laya_outcome: str | None,
    astra_outcome: str | None,
    max_chars: int = MAX_PACKET_CHARS,
) -> tuple[str, dict[str, dict[str, Any]]]:
    """Build the bounded post-Astra resolution packet."""
    state, _ = build_laya_disposition_packet(
        candidate,
        questions,
        facts,
        deterministic_summary=deterministic_summary,
        max_chars=max_chars,
    )
    selected = json.loads(state)
    # Post resolution reuses the exact selected evidence rows but removes the
    # contract/version wrapper and sends only the Astra fields Laya needs.
    response = _dict(astra_response)
    fact_lookup = (
        {str(key): _dict(value) for key, value in facts.items()}
        if isinstance(facts, Mapping)
        else {
            str(_dict(item).get("fact_id")): _dict(item)
            for item in facts
            if _dict(item).get("fact_id")
        }
    )
    # Astra's durable response keeps canonical fact IDs.  The local packet
    # already has the same facts under short labels, so send those labels to
    # Laya and retain the full IDs only in the append-only receipt binding.
    # A11 may emit a distinct canonical ID for the same immutable observation
    # selected by A03; map it to the existing short label after comparing the
    # complete observation identity. Never put an unbound canonical ID in the
    # classifier packet as if it were represented evidence.
    fact_label_by_id: dict[str, str] = {}
    labeled_facts: dict[str, dict[str, Any]] = {}
    for question in questions:
        for fact in _dict(question).get("verified_facts") or []:
            fact_id = str(_dict(fact).get("fact_id") or "").strip()
            fact_row = fact_lookup.get(fact_id)
            if fact_id and fact_row and fact_id not in fact_label_by_id:
                label = f"f{len(fact_label_by_id) + 1}"
                fact_label_by_id[fact_id] = label
                labeled_facts[fact_id] = fact_row
    compact_response = {
        key: response.get(key)
        for key in ("position", "reason", "fact_claim_ids", "question_keys")
        if response.get(key) not in (None, "", [], {})
    }
    if compact_response.get("fact_claim_ids"):
        response_labels: list[str] = []
        for raw_id in compact_response["fact_claim_ids"]:
            fact_id = str(raw_id or "").strip()
            if not fact_id:
                raise LayaPacketEvidenceError("The Astra response referenced unavailable evidence.")
            label = fact_label_by_id.get(fact_id)
            fact_row = fact_lookup.get(fact_id)
            if label is None:
                if not fact_row or not _fact_is_verified(fact_row):
                    raise LayaPacketEvidenceError("The Astra response referenced unavailable evidence.")
                observation = _fact_observation_key(fact_row)
                label = next(
                    (
                        existing_label
                        for existing_id, existing_fact in labeled_facts.items()
                        if _fact_observation_key(existing_fact) == observation
                        for existing_label in (fact_label_by_id[existing_id],)
                    ),
                    None,
                )
                if label is None:
                    if len(selected.get("facts") or []) >= MAX_FACTS:
                        raise LayaPacketEvidenceError("The Astra response exceeded the bounded evidence budget.")
                    label = f"f{len(selected.get('facts') or []) + 1}"
                    selected.setdefault("facts", []).append(_compact_packet_fact(fact_row, label))
                    labeled_facts[fact_id] = fact_row
                    fact_label_by_id[fact_id] = label
            response_labels.append(label)
        compact_response["fact_claim_ids"] = response_labels
    body = {
        "candidate": selected.get("candidate", {}),
        "questions": selected.get("questions", []),
        "facts": selected.get("facts", []),
        "laya_outcome": laya_outcome,
        "astra_outcome": astra_outcome,
        "astra_response": compact_response,
    }
    if selected.get("gates"):
        body["gates"] = selected["gates"]
    return _packet_json(body, max_chars=max_chars), {
        "resolution": {"type": "choice", "instructions": "Assess Astra's response.", "criteria": {
            "accept_resolution": "Reasoned resolution supported by evidence.",
            "disagreement_remains": "Unresolved material disagreement.",
            "insufficient_evidence": "Insufficient evidence.",
        }}
    }


def proposal_input_hash(state: str, choices: Mapping[str, Any]) -> str:
    return hashlib.sha256((state + "\n" + json.dumps(choices, sort_keys=True, separators=(",", ":"))).encode("utf-8")).hexdigest()


def candidate_proposal_hash(candidate: Mapping[str, Any]) -> str:
    """Hash the code-visible Astra candidate proposal before receipt review."""
    return hashlib.sha256(json.dumps(dict(candidate), sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")).hexdigest()


def _safe_review_failure(receipt: Mapping[str, Any] | None) -> str | None:
    """Translate a local receipt failure into a safe UI diagnostic."""
    if not receipt:
        return None
    status = str(receipt.get("status") or "").casefold()
    reason = str(receipt.get("failure_reason") or receipt.get("reason") or "").casefold()
    counts = receipt.get("token_counts") if isinstance(receipt.get("token_counts"), Mapping) else {}
    try:
        limit = int(counts.get("limit") or 512)
        input_tokens = int(counts.get("input") or counts.get("state") or 0)
    except (TypeError, ValueError):
        limit, input_tokens = 512, 0
    if "overflow" in reason or bool(counts.get("overflow")) or input_tokens > limit:
        return f"The selected evidence packet exceeded Laya's {limit}-token input limit."
    if any(marker in reason for marker in ("timeout", "timed out")):
        return "The local Laya review timed out before producing a receipt."
    if any(marker in reason for marker in ("missing", "not_installed", "not installed", "model_not", "weights")):
        return "The pinned local Laya model is unavailable on this worker."
    if "pause" in reason:
        return "The local Laya review was paused before completion."
    if "cancel" in reason:
        return "The local Laya review was cancelled before completion."
    if status in {"failed", "unavailable", "timeout", "invalid_input", "overflow"}:
        return "The local Laya review did not produce a usable receipt."
    return None


def joint_review_from_receipts(
    *,
    candidate: Mapping[str, Any],
    receipts: Sequence[Mapping[str, Any]],
    reviewed_at: str | None = None,
) -> dict[str, Any]:
    """Project a receipt-bound review; provider fields cannot forge this."""
    def latest_phase(phases: set[str]) -> dict[str, Any] | None:
        matching = [
            (index, dict(item))
            for index, item in enumerate(receipts)
            if str(item.get("phase") or "") in phases
        ]
        if not matching:
            return None
        # Repository reads are ordered by immutable creation time.  Prefer
        # the latest receipt when a direct caller supplies multiple rows; the
        # index tie-break preserves that order for synthetic rows without a
        # timestamp and keeps an older receipt from masking a current one.
        return max(matching, key=lambda pair: (str(pair[1].get("created_at") or ""), pair[0]))[1]

    pre = latest_phase({"pre_a11", "pre_a11_disposition", "disposition"})
    post = latest_phase({"post_astra", "post_astra_resolution", "resolution"})
    astra = _dict(candidate.get("laya_response"))
    astra_outcome = str(candidate.get("outcome") or candidate.get("stance") or "").strip().lower()
    if astra_outcome == "enter":
        astra_outcome = "recommend"
    if astra_outcome == "watch":
        astra_outcome = "watchlist"
    if astra_outcome == "defer":
        astra_outcome = "needs_evidence"
    if astra_outcome == "avoid":
        astra_outcome = "decline"
    if astra_outcome not in {"recommend", "watchlist", "decline", "needs_evidence"}:
        astra_outcome = None
    laya_outcome = None
    laya_model = None
    laya_revision = None
    disagreements: list[dict[str, Any]] = []
    verified_fact_ids: list[str] = []
    astra_reasoned = False
    failure_reason: str | None = None
    post_receipt_id = str(post.get("id") or "") or None if post else None
    pre_receipt_id = str(pre.get("id") or "") or None if pre else None
    post_proposal_hash = str(post.get("proposal_hash") or "") or None if post else None
    if post:
        verified_fact_ids = list(dict.fromkeys(
            str(item.get("fact_id") or "").strip()
            for item in post.get("fact_bindings") or []
            if isinstance(item, Mapping) and str(item.get("fact_id") or "").strip()
        ))[:15]
        response_ids = {str(item).strip() for item in astra.get("fact_claim_ids") or [] if str(item).strip()}
        astra_reasoned = (
            str(astra.get("position") or "") in {"agree", "override"}
            and bool(str(astra.get("reason") or "").strip())
            and bool(response_ids)
            and bool(response_ids & set(verified_fact_ids))
            and bool(post_proposal_hash)
        )
    if pre:
        result = pre.get("result") or pre.get("result_value") or pre.get("scores")
        laya_outcome = str(result).strip().lower() if isinstance(result, str) else str(pre.get("outcome") or "").strip().lower() or None
        if laya_outcome == "needs evidence":
            laya_outcome = "needs_evidence"
        laya_model = str(pre.get("model_id") or pre.get("model") or "") or None
        laya_revision = str(pre.get("model_revision") or pre.get("revision") or "") or None
    status = "unavailable"
    resolution = "Local Laya participation is unavailable; recommendation remains blocked."
    if pre and str(pre.get("status") or "").casefold() not in {"ok", "complete", "completed"}:
        failure_reason = _safe_review_failure(pre)
        if failure_reason:
            resolution = f"Local Laya participation is unavailable: {failure_reason} Recommendation remains blocked."
    if pre and str(pre.get("status") or "").casefold() in {"ok", "complete", "completed"}:
        status = "pending"
        resolution = "Astra and local Laya require a bounded post-review resolution."
        if post and str(post.get("status") or "").casefold() in {"ok", "complete", "completed"}:
            result = str(post.get("result") or post.get("result_value") or "").strip().lower()
            if result == "accept_resolution" and astra_reasoned:
                status = "agreed" if laya_outcome == astra_outcome else "resolved"
                resolution = "The bounded local resolution accepted Astra's reasoned assessment."
            elif result == "disagreement_remains":
                status = "pending"
                resolution = "A material Laya/Astra disagreement remains pending review."
                disagreements.append({"question_key": None, "reason": str(astra.get("reason") or "The classifiers did not resolve the proposed outcome.")[:1_000]})
            elif result == "accept_resolution":
                status = "pending"
                resolution = "The post-Astra receipt did not include a reasoned, source-bound Astra response."
            else:
                status = "pending"
                resolution = "The selected evidence was insufficient to resolve the joint review."
        elif post:
            status = "unavailable"
            failure_reason = _safe_review_failure(post)
            resolution = (
                f"The post-Astra local resolution is unavailable: {failure_reason}"
                if failure_reason
                else "The post-Astra local resolution is unavailable."
            )
    if astra.get("position") == "unable":
        status = "pending"
        resolution = "Astra could not address the local assessment from the selected evidence."
    if laya_outcome and astra_outcome and laya_outcome != astra_outcome and status != "resolved":
        disagreements.append({"question_key": None, "reason": f"Laya selected {laya_outcome}; Astra selected {astra_outcome}."})
    return JointDecisionReview(
        status=status,
        laya_model=laya_model,
        laya_revision=laya_revision,
        laya_outcome=laya_outcome if laya_outcome in {"recommend", "watchlist", "decline", "needs_evidence"} else None,
        astra_outcome=astra_outcome,
        resolution=resolution,
        failure_reason=failure_reason,
        disagreements=disagreements,
        reviewed_at=reviewed_at,
        pre_receipt_id=pre_receipt_id,
        post_receipt_id=post_receipt_id,
        post_proposal_hash=post_proposal_hash,
        verified_fact_ids=verified_fact_ids,
        astra_reasoned=astra_reasoned,
    ).model_dump(mode="json")


def joint_review_allows_recommendation(review: Mapping[str, Any]) -> bool:
    # A reasoned Astra override is valid.  The local vote is advisory and may
    # be watchlist/decline while Astra resolves it to Recommend, provided the
    # verified post-Astra receipt accepted that resolution.
    return (
        str(review.get("status") or "") in {"agreed", "resolved"}
        and str(review.get("astra_outcome") or "") == "recommend"
        and "accepted" in str(review.get("resolution") or "").casefold()
        and bool(review.get("post_receipt_id"))
        and bool(review.get("post_proposal_hash"))
        and bool(review.get("verified_fact_ids"))
        and bool(review.get("astra_reasoned"))
    )


# Friendly aliases used by tests and call sites during the contract rollout.
build_disposition_packet = build_laya_disposition_packet
build_resolution_packet = build_laya_resolution_packet
validate_key_questions = validate_five_question_payload
project_verified_questions = project_key_questions


__all__ = [
    "FIVE_QUESTION_CONTRACT",
    "FIVE_QUESTION_KEYS",
    "QUESTION_TEXT",
    "MAX_CANDIDATES",
    "MAX_FACTS",
    "QuestionContractError",
    "LayaPacketEvidenceError",
    "assert_valid_five_question_payload",
    "build_laya_disposition_packet",
    "build_laya_resolution_packet",
    "candidate_proposal_hash",
    "count_distinct_research_facts",
    "constrain_five_question_schema",
    "distinct_research_fact_ids",
    "is_five_question_contract",
    "joint_review_allows_recommendation",
    "joint_review_from_receipts",
    "normalize_question_key",
    "normalize_question_proposals",
    "project_key_questions",
    "validate_five_question_payload",
]
