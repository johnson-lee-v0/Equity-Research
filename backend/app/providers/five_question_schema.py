"""Provider-schema projection for the five-question A03/A11 stages.

``AgentOutputPayload`` is shared by every provider stage and by historical
outputs.  The shared model therefore intentionally contains the broad set of
legacy aliases used by routing, discovery, simulation and the PM review.  A
new five-question analyst/CIO call only needs the common evidence parser and
the per-candidate CIO brief.  This module projects that shared JSON schema for
the two stages without changing the Pydantic model or the schema used by any
other stage.
"""
from __future__ import annotations

from collections.abc import Mapping
import copy
from typing import Any


FIVE_QUESTION_PROVIDER_AGENTS = frozenset({"A03", "A11"})
FIVE_QUESTION_FACT_CLAIM_ALIASES = tuple(f"c{index}" for index in range(1, 16))

# These fields are the common parser/evidence contract.  ``missing_gaps`` is
# intentionally retained as the one additional root field: Repository's
# lean continuation gate reads it from the newly committed A03/A11 payload to
# authorize the single bounded public evidence continuation.  The field has a
# compatibility fallback through ``missing_data`` for older runs, but the
# new path must be able to make an explicit continuation request.
FIVE_QUESTION_ROOT_FIELDS = (
    "status",
    "research_contract",
    "allocation_mode",
    "title",
    "summary",
    "analysis",
    "fact_claims",
    "assumptions",
    "calculations",
    "counterarguments",
    "missing_data",
    "proposed_action",
    "invalidation_conditions",
    "next_review_at",
    "source_refs",
    "missing_gaps",
    "decision_disposition",
    "candidate_briefs",
)


def _local_definition_refs(value: Any, refs: set[str], *, skip_root_defs: bool = False) -> None:
    """Collect local ``$defs`` references from a JSON-schema fragment.

    The top-level ``$defs`` mapping itself must be skipped while discovering
    roots; otherwise every definition would appear reachable merely because
    it is present in the schema.  Once a definition is selected, its complete
    fragment is traversed so references made by that definition are retained
    transitively.
    """
    if isinstance(value, Mapping):
        reference = value.get("$ref")
        if isinstance(reference, str) and reference.startswith("#/$defs/"):
            name = reference.removeprefix("#/$defs/")
            if name:
                refs.add(name)
        for key, child in value.items():
            if skip_root_defs and key == "$defs":
                continue
            _local_definition_refs(child, refs)
    elif isinstance(value, list):
        for child in value:
            _local_definition_refs(child, refs)


def _prune_unreferenced_definitions(schema: dict[str, Any]) -> None:
    """Remove local definitions that cannot be reached from the root.

    Pydantic places every model used by ``AgentOutputPayload`` in one shared
    ``$defs`` mapping.  Dropping a root alias is therefore insufficient: its
    definition and any definitions referenced only by it would still be sent
    to the provider.  Reachability is calculated over local ``$ref`` edges,
    including cycles, and the original definition order is retained.
    """
    definitions = schema.get("$defs")
    if not isinstance(definitions, dict):
        return

    reachable: set[str] = set()
    _local_definition_refs(schema, reachable, skip_root_defs=True)
    pending = list(reachable)
    while pending:
        name = pending.pop()
        definition = definitions.get(name)
        if not isinstance(definition, Mapping):
            # Leave an unresolved reference visible.  The provider/consumer
            # can report a malformed source schema instead of this helper
            # silently manufacturing a different reference graph.
            continue
        before = len(reachable)
        _local_definition_refs(definition, reachable)
        if len(reachable) != before:
            pending.extend(item for item in reachable if item not in pending)

    retained = {name: definition for name, definition in definitions.items() if name in reachable}
    if retained:
        schema["$defs"] = retained
    else:
        # Removing an empty mapping saves a little more input and keeps the
        # resulting schema honest about having no local definitions.
        schema.pop("$defs", None)


def _bound_fact_claim_aliases(schema: dict[str, Any]) -> None:
    """Bound provider-emitted fact aliases without changing reference arrays.

    ``FactClaim.claim_id`` is an output-local alias.  Repository canonical
    IDs are assigned after commit and remain valid in candidate reference
    arrays such as ``supporting_claim_ids`` and ``fact_claim_ids``.  Keeping
    this restriction in the projected A03/A11 JSON schema avoids asking a
    provider to manufacture IDs that only the repository can issue.
    """
    definitions = schema.get("$defs")
    fact_claim = definitions.get("FactClaim") if isinstance(definitions, Mapping) else None
    properties = fact_claim.get("properties") if isinstance(fact_claim, Mapping) else None
    claim_id = properties.get("claim_id") if isinstance(properties, Mapping) else None
    if not isinstance(claim_id, dict):
        return
    branches = claim_id.get("anyOf")
    if not isinstance(branches, list):
        if claim_id.get("type") == "string":
            claim_id["enum"] = list(FIVE_QUESTION_FACT_CLAIM_ALIASES)
        return
    bounded: list[dict[str, Any]] = [{"type": "string", "enum": list(FIVE_QUESTION_FACT_CLAIM_ALIASES)}]
    bounded.extend(
        copy.deepcopy(branch)
        for branch in branches
        if isinstance(branch, Mapping) and branch.get("type") == "null"
    )
    claim_id["anyOf"] = bounded


def simplify_five_question_provider_schema(
    schema: Mapping[str, Any],
    *,
    agent_id: str,
) -> dict[str, Any]:
    """Return an isolated, lean provider schema for A03 or A11.

    The returned mapping is always a deep copy.  For non-five-question agents
    it is otherwise unchanged, preserving the historical provider contract.
    For A03/A11, only the shared root aliases are projected away; the complete
    ``CandidateDecisionBrief`` definition is retained so all entry, target,
    stop, sizing, valuation, thesis, action and feasibility fields keep their
    original limits and validation constraints.

    The caller remains responsible for applying attempt-specific bounds such
    as the 15-fact, three-candidate and five-question limits.  This helper does
    not rewrite any retained node, which keeps those constraints composable
    with the existing contract-boundary function.
    """
    simplified = copy.deepcopy(dict(schema))
    normalized_agent = str(agent_id or "").strip().upper()
    if normalized_agent not in FIVE_QUESTION_PROVIDER_AGENTS:
        return simplified

    properties = simplified.get("properties")
    # Be conservative for a caller that passes a different probe schema.  The
    # projection is defined for AgentOutputPayload and should not turn an
    # arbitrary object schema into an empty object by accident.
    if not isinstance(properties, dict) or "candidate_briefs" not in properties:
        return simplified

    simplified["properties"] = {
        name: properties[name]
        for name in FIVE_QUESTION_ROOT_FIELDS
        if name in properties
    }
    required = simplified.get("required")
    if isinstance(required, list):
        # Preserve the source schema's required/optional semantics while
        # removing required names whose properties were projected out.  The
        # Codex adapter's existing normalization step still makes every
        # declared property required for strict structured output.
        simplified["required"] = [
            name for name in required
            if name in simplified["properties"]
        ]

    _bound_fact_claim_aliases(simplified)
    _prune_unreferenced_definitions(simplified)
    return simplified


def _text_identity(value: Any) -> str:
    """Normalize an identifier/version/hash for exact comparison only."""
    if value is None or isinstance(value, bool):
        return ""
    return str(value).strip()


def _matching_evidence_record(
    memory_record: Mapping[str, Any],
    evidence_rows: list[Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    """Find one exact source identity shared by memory and provider evidence.

    A record ID alone is insufficient because a source can be amended.  The
    source version and content hash must also match.  Output-memory records
    may cite several source versions; only a row whose own ``record_id`` is
    the evidence ID can be compacted.
    """
    record_id = _text_identity(memory_record.get("record_id") or memory_record.get("id"))
    if not record_id:
        return None
    versions = memory_record.get("source_versions")
    version_rows: list[Mapping[str, Any]] = []
    if isinstance(versions, list):
        version_rows.extend(item for item in versions if isinstance(item, Mapping))
    direct_version = {
        "id": memory_record.get("record_id") or memory_record.get("id"),
        "version": memory_record.get("version"),
        "content_hash": memory_record.get("content_hash") or memory_record.get("source_hash"),
    }
    if direct_version.get("version") is not None or direct_version.get("content_hash") is not None:
        version_rows.append(direct_version)

    for evidence in evidence_rows:
        evidence_id = _text_identity(evidence.get("id") or evidence.get("record_id"))
        if evidence_id != record_id:
            continue
        evidence_version = _text_identity(evidence.get("version"))
        evidence_hash = _text_identity(evidence.get("content_hash") or evidence.get("source_hash"))
        if not evidence_version or not evidence_hash:
            continue
        for version_row in version_rows:
            version_id = _text_identity(version_row.get("id") or version_row.get("source_id"))
            if (
                version_id == evidence_id
                and _text_identity(version_row.get("version")) == evidence_version
                and _text_identity(version_row.get("content_hash") or version_row.get("source_hash")) == evidence_hash
            ):
                return evidence
    return None


def _evidence_reference(evidence: Mapping[str, Any]) -> str:
    """Build the small, deterministic replacement for an identical excerpt."""
    evidence_id = _text_identity(evidence.get("id") or evidence.get("record_id"))
    version = _text_identity(evidence.get("version"))
    content_hash = _text_identity(evidence.get("content_hash") or evidence.get("source_hash"))
    return (
        "[Supplied evidence record "
        f"{evidence_id}@v{version} hash {content_hash}; use its bounded content above.]"
    )


def compact_five_question_context(
    context: Mapping[str, Any],
    *,
    agent_id: str | None = None,
) -> dict[str, Any]:
    """Return a safe compact copy of a new A03/A11 provider context.

    The caller invokes this after frozen inputs and projections are prepared.
    If ``agent_id`` is supplied (or is present in the context), only A03/A11
    are changed; a context without an agent marker is accepted for small
    standalone callers and tests.  The operation is idempotent.

    Only exact aliases are removed:

    * ``memory`` is dropped when it is structurally equal to
      ``memory_context``; a divergent alias remains untouched.
    * A prior output's nested ``decision_brief.candidate_briefs`` is dropped
      only when it is structurally equal to that output's top-level
      ``candidate_briefs``.  Other aggregate decision-brief fields remain.
    * A reused memory source excerpt is replaced only when the matching
      evidence record has the same source ID, version and content hash *and*
      the excerpt text is exactly identical.  A bounded or otherwise
      different excerpt remains, because it may contain unique evidence.

    No fact claims, validation metadata, unmatched memory rows, portfolio
    values, or financial proposal fields are pruned.
    """
    compacted = copy.deepcopy(dict(context))
    normalized_agent = str(
        agent_id if agent_id is not None else compacted.get("agent_id") or ""
    ).strip().upper()
    if normalized_agent and normalized_agent not in FIVE_QUESTION_PROVIDER_AGENTS:
        return compacted

    memory_context = compacted.get("memory_context")
    if "memory" in compacted and compacted.get("memory") == memory_context:
        compacted.pop("memory", None)

    prior_outputs = compacted.get("prior_outputs")
    if isinstance(prior_outputs, list):
        for prior_output in prior_outputs:
            if not isinstance(prior_output, dict):
                continue
            decision_brief = prior_output.get("decision_brief")
            candidate_briefs = prior_output.get("candidate_briefs")
            if (
                isinstance(decision_brief, dict)
                and isinstance(candidate_briefs, list)
                and isinstance(decision_brief.get("candidate_briefs"), list)
                and decision_brief["candidate_briefs"] == candidate_briefs
            ):
                # Keep aggregate fields such as stance, valuation and action
                # data; only the byte-for-byte duplicate candidate array goes.
                decision_brief.pop("candidate_briefs", None)

    evidence = compacted.get("evidence")
    reused = memory_context.get("reused") if isinstance(memory_context, dict) else None
    if isinstance(evidence, list) and isinstance(reused, list):
        evidence_rows = [item for item in evidence if isinstance(item, Mapping)]
        for memory_record in reused:
            if not isinstance(memory_record, dict) or "excerpt" not in memory_record:
                continue
            matching_evidence = _matching_evidence_record(memory_record, evidence_rows)
            if matching_evidence is None:
                continue
            # Keep any memory excerpt whose text differs from the bounded
            # evidence projection: it may contain lines the projection omits.
            if memory_record.get("excerpt") == matching_evidence.get("content"):
                memory_record["excerpt"] = _evidence_reference(matching_evidence)

    return compacted


__all__ = [
    "FIVE_QUESTION_PROVIDER_AGENTS",
    "FIVE_QUESTION_FACT_CLAIM_ALIASES",
    "FIVE_QUESTION_ROOT_FIELDS",
    "compact_five_question_context",
    "simplify_five_question_provider_schema",
]
