"""A lossless-on-demand read model for opening a saved research case."""
from __future__ import annotations

from copy import deepcopy
from typing import Any
from urllib.parse import quote


def project_run_reader(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Avoid transferring full archived documents inside claim excerpts.

    Some historical validation associations retained a complete source in
    ``excerpt`` for every claim. The reader already displays ``matched_excerpt``
    when present. Keep that exact display text and every other claim field;
    the full run and output endpoints still return the original excerpt.
    Never shorten the only available excerpt or mutate the repository result.
    """
    result = deepcopy(snapshot)
    previous = result.get("reader_projection")
    deferred = (
        previous.get("deferred_claim_excerpts", [])
        if isinstance(previous, dict) and previous.get("mode") == "reader"
        else []
    )
    namespace = quote(str(snapshot.get("namespace") or "real"), safe="")
    outputs = result.get("outputs")
    for output in outputs if isinstance(outputs, list) else []:
        if not isinstance(output, dict) or not output.get("id"):
            continue
        claims = output.get("fact_claims")
        for index, claim in enumerate(claims if isinstance(claims, list) else []):
            if not isinstance(claim, dict):
                continue
            excerpt = claim.get("excerpt")
            matched = claim.get("matched_excerpt")
            if not (
                isinstance(excerpt, str)
                and len(excerpt) > 4_096
                and isinstance(matched, str)
                and matched.strip()
                and len(matched) < len(excerpt)
            ):
                continue
            claim["excerpt"] = matched
            deferred.append({
                "output_id": output["id"],
                "claim_index": claim.get("claim_index", index),
                "source_ref": claim.get("source_ref"),
                "field": "excerpt",
                "original_chars": len(excerpt),
                "full_output_url": f"/api/outputs/{quote(str(output['id']), safe='')}?namespace={namespace}",
            })
    result["reader_projection"] = {"mode": "reader", "deferred_claim_excerpts": deferred}
    return result
