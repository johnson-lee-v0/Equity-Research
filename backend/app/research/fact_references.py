"""Resolve fact references only within an immutable decision input packet."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import json
import re
from typing import Any


def _version(value: Any) -> tuple[str, str] | None:
    if not isinstance(value, dict) or value.get("version") is None:
        return None
    fingerprint = value.get("hash") or value.get("content_hash")
    return (str(value["version"]),str(fingerprint)) if fingerprint else None


def resolve_fact_references(repo: Any, output: Any, payload: dict[str, Any], current_facts: list[dict[str, Any]], inputs: dict[str, Any], sources: list[dict[str, Any]]) -> tuple[dict[str, Any],dict[str, Any]]:
    """Return a projection copy; never repair the immutable provider artifact.

    Aliases belong to this output. Prior IDs must have been supplied to this
    attempt, including their original output and exact retained source version.
    Missing historical allowlists stay missing; no current-state lookup fills them.
    """
    namespace = output["provenance"]
    with repo.db.operation() as conn:
        attempt = conn.execute("SELECT source_versions_json,started_at FROM task_attempts WHERE id=?",(output["attempt_id"],)).fetchone()
        aliases = conn.execute("SELECT claim_id,fact_id FROM output_claim_aliases WHERE output_id=? AND namespace=?",(output["id"],namespace)).fetchall()
    versions = json.loads(attempt["source_versions_json"] or "{}") if attempt else {}
    versions = versions if isinstance(versions,dict) else {}
    supplied = {source["id"]:_version({"version":source.get("version"),"hash":source.get("content_hash")}) for source in sources}
    def source_allowed(fact: dict[str, Any], original_versions: dict[str, Any]) -> bool:
        ref = fact.get("source_ref")
        frozen = _version(versions.get(ref))
        return bool(frozen and frozen == supplied.get(ref) and frozen == _version(original_versions.get(ref)))

    available = {fact["fact_id"]:fact for fact in current_facts if fact.get("fact_id") and source_allowed(fact,versions)}
    alias_counts = Counter(row["claim_id"] for row in aliases)
    alias_map = {row["claim_id"]:row["fact_id"] for row in aliases if alias_counts[row["claim_id"]] == 1 and row["fact_id"] in available}
    allowed_outputs = set(inputs.get("prior_output_ids") or [])
    allowed_facts = set(inputs.get("prior_fact_ids") or [])
    prior_facts: dict[str,dict[str,Any]] = {}
    # Query only IDs in the frozen allowlist. Each row also verifies its output
    # and namespace before its archived payload can be exposed to the projection.
    for identifier in allowed_facts:
        with repo.db.operation() as conn:
            row = conn.execute("SELECT o.*,a.source_versions_json AS original_versions FROM output_claims c JOIN outputs o ON o.id=c.output_id JOIN task_attempts a ON a.id=o.attempt_id JOIN tasks t ON t.id=o.task_id JOIN runs r ON r.id=t.run_id WHERE c.fact_id=? AND o.provenance=? AND r.namespace=?",(identifier,namespace,namespace)).fetchone()
        if not row or row["id"] not in allowed_outputs or not attempt or row["created_at"] > attempt["started_at"]:
            continue
        original = json.loads(row["original_versions"] or "{}")
        if not isinstance(original,dict):
            continue
        fact = next((item for item in repo.output_dict(row)["fact_claims"] if item.get("fact_id") == identifier),None)
        if fact and source_allowed(fact,original):
            prior_facts[identifier] = fact
    available.update(prior_facts)
    projected = deepcopy(payload)
    audit: dict[str,Any] = {"version":"fact-references.v1","resolved":[],"unresolved":[]}
    used_prior = set()
    def walk(value: Any, path: str = "") -> None:
        if isinstance(value,list):
            for index,item in enumerate(value):
                walk(item,f"{path}[{index}]")
        elif isinstance(value,dict):
            for key,item in value.items():
                location = f"{path}.{key}" if path else key
                if key in {"supporting_claim_ids","contradicting_claim_ids","fact_claim_ids"} and isinstance(item,list):
                    resolved = []
                    for reference in item:
                        identifier = alias_map.get(reference,reference) if isinstance(reference,str) else None
                        if identifier in available and alias_counts.get(reference,0) < 2:
                            resolved.append(identifier)
                            audit["resolved"].append({"path":location,"reference":reference,"fact_id":identifier})
                            if identifier in prior_facts:
                                used_prior.add(identifier)
                        else:
                            # Keep the unresolvable identifier explicit so the
                            # recommendation gate cannot silently drop a requirement.
                            resolved.append(reference)
                            audit["unresolved"].append({"path":location,"reference":reference,"reason":"Unknown, duplicate or outside the frozen output/source packet."})
                    value[key] = resolved
                else:
                    walk(item,location)
    walk(projected)
    global_errors = []
    candidate_errors: dict[int,list[dict[str,Any]]] = {}
    for error in audit["unresolved"]:
        match = re.match(r"^(?:decision_brief\.)?(?:candidate_briefs|candidates)\[(\d+)\]",error["path"])
        if match:
            candidate_errors.setdefault(int(match.group(1)),[]).append(error)
        else:
            global_errors.append(error)
    for container in (projected,projected.get("decision_brief") or {}):
        for field in ("candidate_briefs","candidates"):
            for index,candidate in enumerate(container.get(field) or []):
                errors = global_errors+candidate_errors.get(index,[])
                if errors:
                    candidate["_fact_reference_errors"] = errors
                    candidate["missing_inputs"] = list(candidate.get("missing_inputs") or [])+["Material fact references could not be resolved within the frozen research packet. Supply a unique local claim or a prior fact actually provided to this attempt."]
        if global_errors:
            container["_fact_reference_errors"] = global_errors
    current = [fact if fact.get("fact_id") in available else dict(fact,validation_status="unavailable",semantic_status="unavailable",validation_reason="Fact source is not bound to the immutable attempt source version.") for fact in current_facts]
    projected["fact_claims"] = current+[prior_facts[key] for key in sorted(used_prior) if key not in {fact.get("fact_id") for fact in current_facts}]
    return projected,audit
