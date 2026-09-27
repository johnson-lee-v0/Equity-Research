"""Large JSON-line excerpts can shrink without losing financial evidence."""
import copy
import hashlib
import json

import pytest

from backend.app.research.assessment_provider_projection import project_assessment_provider_facts
from backend.app.research.earnings_financials import sec_eps_observations
from backend.app.research.fact_validation import validate_fact_claim
from backend.tests.test_assessment_evidence import RELEASE, sec_source, seed


@pytest.fixture
def packet():
    raw, sec_meta = sec_source()
    concept = json.loads(raw)
    concept["description"] = "Long archived SEC concept description. " * 1500
    raw = json.dumps(concept, separators=(",", ":"))
    contents = {"sec": raw, "release": RELEASE}
    versions = {sid: {"version": 1, "hash": hashlib.sha256(text.encode()).hexdigest()} for sid, text in contents.items()}
    sources = [{"id": sid, "content": text, "content_hash": versions[sid]["hash"], "version": 1,
                "source_type": "document", "namespace": "real",
                "url": sec_meta["url"] if sid == "sec" else "https://issuer.example.test/results"}
               for sid, text in contents.items()]
    facts = []
    for i, row in enumerate(sec_eps_observations(raw, sec_meta, issuer="ABC", cik="1234", as_of="2026-09-25")):
        fact = seed(row, "sec")
        validation = validate_fact_claim(fact, contents, source_metadata={"sec": sec_meta}, source_versions=versions, as_of="2026-09-25")
        assert validation["validation_status"] == "validated"
        facts.append(fact | {"fact_id": f"fact_{i}", "claim_id": f"c{i + 1}", "excerpt": raw,
                             "matched_excerpt": fact["source_quote"], "freshness": "stale" if i else "fresh",
                             "validation_status": "validated", "recorded_validation_status": "validated",
                             "current_validation_status": "validated", "semantic_status": "supported",
                             "binding_checks": validation["binding_checks"], "validation_reason": validation["validation_reason"],
                             "line_start": 1, "line_end": 1, "source_version": "1"})
    call = "L1: Complete management call\nL2: Analyst question\nL3: Complete answer and material qualifier."
    context = {"agent_id": "A11", "assessment_preparation": {"version": "earnings-assessment-evidence.v2"},
               "prior_outputs": [{"id": "output_a03", "agent_id": "A03", "fact_claims": facts}],
               "financial_seeds": [{k: v for k, v in f.items() if k != "claim_id"} for f in facts],
               "evidence": [{"id": "call", "content": call}, {"id": "release", "content": RELEASE}],
               "earnings_context": {"series": [{"period": "FY2025", "value": 10}], "guidance": "Preserve complete guidance qualifiers."},
               "coverage": {"complete_latest_call": True}}
    return context, sources, versions


def apply(packet):
    context, sources, versions = packet
    return project_assessment_provider_facts(context, sources, source_versions=versions, as_of="2026-09-25")


def test_only_redundant_eps_excerpts_change_in_both_registers(packet):
    before = copy.deepcopy(packet)
    projected, receipt = apply(packet)
    assert packet == before  # No archives, records or caller objects changed.
    assert receipt["replaced_excerpt_count"] == 4
    assert receipt["validated_fact_ids"] == ["fact_0", "fact_1"]
    assert receipt["saved_chars"] > 200_000
    assert receipt["context_chars_after"] < 20_000
    expected = copy.deepcopy(before[0])
    for fact in expected["financial_seeds"] + expected["prior_outputs"][0]["fact_claims"]:
        fact["excerpt"] = fact["source_quote"]
    assert projected == expected
    for item in receipt["replacements"]:
        assert item["source_hash"] == before[2]["sec"]["hash"]
        assert item["excerpt_chars_after"] < 200 and item["excerpt_chars_before"] > 50_000
    twice, again = project_assessment_provider_facts(projected, packet[1], source_versions=packet[2], as_of="2026-09-25")
    assert twice == projected and again["saved_chars"] == 0 and not again["replacements"]


@pytest.mark.parametrize("problem", ["proposed", "current_invalid", "wrong_value", "wrong_period", "wrong_basis", "wrong_quote", "source_version", "changed_source", "changed_issuer_source", "missing_issuer_source", "not_financial", "no_preparation", "other_role"])
def test_unproven_or_out_of_scope_facts_keep_their_full_excerpt(packet, problem):
    context, sources, versions = packet
    facts = context["financial_seeds"] + context["prior_outputs"][0]["fact_claims"]
    for fact in facts:
        if problem == "proposed": fact["validation_status"] = "proposed"
        if problem == "current_invalid": fact["current_validation_status"] = "unavailable"
        if problem == "wrong_value": fact["value"] = "999"
        if problem == "wrong_period": fact["period_end"] = "2024-01-01"
        if problem == "wrong_basis": fact["basis"] = "adjusted diluted"
        if problem == "wrong_quote": fact["source_quote"] = "Long archived SEC concept description."
        if problem == "source_version": fact["source_version"] = "2"
        if problem == "not_financial": fact["metric"] = "revenue"
    if problem == "changed_source": sources[0]["content"] += " changed"
    if problem == "changed_issuer_source": sources[1]["content"] += " changed"
    if problem == "missing_issuer_source": sources.pop()
    if problem == "no_preparation": context.pop("assessment_preparation")
    if problem == "other_role": context["agent_id"] = "A03"
    before = copy.deepcopy(context)
    projected, receipt = apply(packet)
    assert projected == before and not receipt["replacements"]


def test_large_numerical_operating_passages_and_other_context_remain_whole(packet):
    context, _, _ = packet
    management = {"fact_id": "fact_operating", "metric": "paid_members_growth", "value": "3.8",
                  "excerpt": "Complete management qualifiers. " * 2000, "source_quote": "Paid members increased 3.8%."}
    context["financial_seeds"].append(copy.deepcopy(management))
    context["prior_outputs"][0]["fact_claims"].append(copy.deepcopy(management))
    projected, _ = apply(packet)
    assert projected["financial_seeds"][-1] == management
    assert projected["prior_outputs"][0]["fact_claims"][-1] == management
    for field in ("evidence", "earnings_context", "coverage"):
        assert projected[field] == context[field]
