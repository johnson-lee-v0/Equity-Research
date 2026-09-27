"""Projection limits must not turn a completed review into a failed decision."""
import json

import pytest

from backend.app.research.decisions import build_case_decision


def watch_payload():
    return {
        "status": "needs_review", "summary": "Await the next issuer disclosure.",
        "candidate_briefs": [{
            "ticker": "BE", "instrument": "Bloom Energy Corporation", "stance": "watch",
            "watch_triggers": [{
                "type": "evidence", "condition": "Issuer publishes guidance update",
                "evidence_condition": "Dated primary guidance materially changes",
                "operator": "changes", "reopen_when": "Reassess the new primary disclosure",
                "source_refs": ["issuer"],
            }],
        }],
    }


def test_final_service_blocker_does_not_overflow_candidate_schema():
    payload = watch_payload()
    payload["candidate_briefs"][0]["missing_inputs"] = [f"Material evidence requirement {i}" for i in range(25)]
    result = build_case_decision("case", payload, {}, {"issuer": {"id": "issuer", "source_type": "primary"}})
    candidate = result.candidates[0]
    assert candidate.outcome == "watchlist"
    assert candidate.execution_state == "awaiting_input"
    assert len(candidate.material_blockers) == 20
    assert len(candidate.watch_triggers) == 1
    assert not any(b.key == "canonical_validation" for b in candidate.material_blockers)


def test_projection_validation_reports_fields_without_private_values():
    payload = watch_payload()
    private_value = "private-provider-input-" * 20
    payload["candidate_briefs"][0]["instrument"] = private_value
    candidate = build_case_decision("case", payload, {}, {}).candidates[0]
    assert candidate.outcome is None and candidate.execution_state == "failed"
    assert "instrument (string_too_long)" in candidate.rationale
    assert private_value not in candidate.rationale
    assert private_value not in candidate.sizing.reason
    assert candidate.material_blockers[0].key == "canonical_validation"
    assert "instrument (string_too_long)" in candidate.material_blockers[0].reason


def source_bound_watch(excerpt, validation_status="validated"):
    payload = watch_payload()
    row = payload["candidate_briefs"][0]
    row.update(ticker="IREN", instrument="IREN Limited")
    row["watch_triggers"] = [{"type": "price", "condition": "Reassess IREN at the observed reference", "operator": "at_or_below", "threshold": "39.60", "currency": "USD", "source_refs": ["market"], "reopen_when": "Reassess fundamentals after the price condition"}]
    payload["fact_claims"] = [{"claim": "The archived September 2 closing price supplies the selected reference-price watch level.", "value": "39.60", "unit": "USD per share", "period": "2026-09-02", "source_ref": "market", "locator": "L3", "validation_status": validation_status, "excerpt": excerpt}]
    return build_case_decision("case", payload, {}, {"market": {"id": "market", "source_type": "market_data"}}).candidates[0]


def test_generic_validated_price_claim_keeps_its_recorded_bar_instrument():
    excerpt = json.dumps({"symbol": "IREN", "timestamp": "2026-09-02T04:00:00Z", "close": "39.60", "complete": True})
    candidate = source_bound_watch(excerpt)
    assert candidate.outcome == "watchlist"
    assert len(candidate.watch_triggers) == 1
    assert candidate.watch_triggers[0].threshold == "39.60"
    assert candidate.watch_triggers[0].source_refs == ["market"]


@pytest.mark.parametrize("excerpt", [
    json.dumps({"symbol": "OTHER", "close": "39.60", "complete": True}),
    json.dumps({"symbol": "IREN", "close": "39.60", "complete": False}),
    json.dumps({"symbol": "IREN", "close": "39.60", "complete": True}) + "\n" + json.dumps({"symbol": "OTHER", "close": "39.60", "complete": True}),
    "IREN appears in a source title without a structured instrument binding.",
])
def test_generic_claim_does_not_relabel_wrong_ambiguous_or_unstructured_excerpt(excerpt):
    assert source_bound_watch(excerpt).watch_triggers == []


def test_unvalidated_excerpt_cannot_authorize_a_price_watch():
    excerpt = json.dumps({"symbol": "IREN", "timestamp": "2026-09-02T04:00:00Z", "close": "39.60", "complete": True})
    assert source_bound_watch(excerpt, "proposed").watch_triggers == []
