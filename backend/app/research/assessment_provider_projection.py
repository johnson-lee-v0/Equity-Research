"""Provider-only removal of repeated full-line excerpts on validated EPS facts.

A compact SEC concept is one very long source line. Its exact annual EPS row
is already retained as source_quote, but the generic citation read model also
attaches the entire line as excerpt. Copying that row into two fact registers
can dwarf the complete earnings call. This projection replaces only that
redundant excerpt after independently repeating its strict financial binding.
Archive content, line locators, evidence coverage and all fact metadata remain
unchanged. The receipt belongs in attempt diagnostics, outside the prompt.
"""
from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from .earnings_financials import VERSION as FINANCIAL_BINDER_VERSION
from .comparable_financials import VERSION as COMPARABLE_BINDER_VERSION
from .fact_validation import validate_fact_claim

VERSION = "earnings-provider-facts.v1"


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _chars(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False))


def project_assessment_provider_facts(
    context: Mapping[str, Any],
    sources: Sequence[Mapping[str, Any]],
    *,
    source_versions: Mapping[str, Any],
    as_of: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return a copy and audit receipt; never read or write the repository.

    Only A11's deterministic earnings-preparation packet is eligible. A fact
    needs both recorded/current validation and a fresh independent EPS-parser
    proof against unchanged frozen sources. Flags or a quote substring alone
    do not authorize removing a larger source passage.
    """
    projected = copy.deepcopy(dict(context))
    receipt: dict[str, Any] = {"version": VERSION, "context_chars_before": _chars(context),
                               "replacements": [], "validated_fact_ids": []}
    preparation = context.get("assessment_preparation")
    enabled = (context.get("agent_id") == "A11" and isinstance(preparation, Mapping)
               and str(preparation.get("version") or "").startswith("earnings-assessment-evidence."))
    if enabled:
        content: dict[str, str] = {}
        metadata: dict[str, dict[str, Any]] = {}
        versions: dict[str, dict[str, Any]] = {}
        for source in sources:
            sid = str(source.get("id") or "")
            expected = source_versions.get(sid)
            raw = source.get("content")
            if not sid or not isinstance(expected, Mapping) or not isinstance(raw, str):
                continue
            fingerprint = expected.get("hash") or expected.get("content_hash")
            if (not fingerprint or source.get("content_hash") != fingerprint or _hash(raw) != fingerprint
                    or expected.get("version") is None or str(source.get("version")) != str(expected["version"])):
                continue
            content[sid] = raw
            metadata[sid] = {key: value for key, value in source.items() if key != "content"}
            versions[sid] = {"version": expected["version"], "hash": fingerprint}

        seeds = projected.get("financial_seeds")
        seed_ids = {str(fact.get("fact_id")) for fact in seeds if isinstance(fact, Mapping) and fact.get("fact_id")} if isinstance(seeds, list) else set()
        checked: dict[str, bool] = {}

        def project_fact(fact: Any, path: str) -> None:
            if not isinstance(fact, dict) or fact.get("fact_id") not in seed_ids:
                return
            if any(fact.get(field) != "validated" for field in ("validation_status", "recorded_validation_status", "current_validation_status")) or fact.get("semantic_status") != "supported":
                return
            sid = str(fact.get("source_ref") or "")
            quote = fact.get("source_quote")
            excerpt = fact.get("excerpt")
            if (sid not in versions or str(fact.get("source_version")) != str(versions[sid]["version"])
                    or not isinstance(quote, str) or not 1 <= len(quote) <= 2_000
                    or not isinstance(excerpt, str) or len(excerpt) <= len(quote) or quote not in excerpt):
                return
            # Aliases differ between the two registers. Ignore only those
            # display fields in the validation cache; every factual field,
            # including source_quote and source_version, remains part of it.
            fingerprint = _hash(json.dumps({key: value for key, value in fact.items()
                                            if key not in {"claim_id", "excerpt", "matched_excerpt"}},
                                           ensure_ascii=False, sort_keys=True))
            if fingerprint not in checked:
                result = validate_fact_claim(fact, content, source_metadata=metadata,
                                             source_versions=versions, as_of=as_of)
                proof = (result.get("extraction") or {}).get("proof") or {}
                checked[fingerprint] = bool(result.get("validation_status") == "validated"
                                            and result.get("semantic_status") == "supported"
                                            and result.get("matched_excerpt") == quote
                                            and proof.get("parser") in {FINANCIAL_BINDER_VERSION, COMPARABLE_BINDER_VERSION})
            if not checked[fingerprint]:
                return
            # Preserve every other key verbatim, including matched_excerpt,
            # freshness, binding checks, currency and full period metadata.
            fact["excerpt"] = quote
            receipt["replacements"].append({
                "path": path + ".excerpt", "fact_id": fact["fact_id"], "source_ref": sid,
                "source_version": versions[sid]["version"], "source_hash": versions[sid]["hash"],
                "original_excerpt_hash": _hash(excerpt), "source_quote_hash": _hash(quote),
                "excerpt_chars_before": len(excerpt), "excerpt_chars_after": len(quote),
            })
            if fact["fact_id"] not in receipt["validated_fact_ids"]:
                receipt["validated_fact_ids"].append(fact["fact_id"])

        if isinstance(seeds, list):
            for index, fact in enumerate(seeds):
                project_fact(fact, f"financial_seeds[{index}]")
        priors = projected.get("prior_outputs")
        for output_index, output in enumerate(priors if isinstance(priors, list) else []):
            if not isinstance(output, dict) or not isinstance(output.get("fact_claims"), list):
                continue
            for fact_index, fact in enumerate(output["fact_claims"]):
                project_fact(fact, f"prior_outputs[{output_index}].fact_claims[{fact_index}]")

    receipt["context_chars_after"] = _chars(projected)
    receipt["saved_chars"] = receipt["context_chars_before"] - receipt["context_chars_after"]
    receipt["replaced_excerpt_count"] = len(receipt["replacements"])
    return projected, receipt
