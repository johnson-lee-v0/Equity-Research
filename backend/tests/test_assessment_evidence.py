"""A fast compiler preserves evidence and cannot invent valuation anchors."""
from __future__ import annotations

import copy
import hashlib
import json

import pytest

from backend.app.research.assessment_evidence import compile_earnings_assessment
from backend.app.research.earnings_financials import release_eps_observations, sec_eps_observations
from backend.app.research.fact_validation import validate_fact_claim
from backend.app.schemas import ImportRequest
from backend.tests.test_earnings_research_context import context_case


def test_complete_call_is_kept_including_unmatched_late_text(context_case):
    repo, run_id, _, sources, source_ids, *_ = context_case
    result = compile_earnings_assessment(repo, run_id, sources)
    call = next(item for item in result["context"]["evidence"] if item["id"] == source_ids[0])
    original = next(item for item in sources if item["id"] == source_ids[0])
    assert call["kind"] == "complete_latest_transcript"
    assert call["content"].endswith(original["content"].splitlines()[-1])
    assert result["context"]["coverage"]["omitted_spans"] == []
    # An explicit low bound must request a split, not silently omit Q&A.
    overflow = compile_earnings_assessment(repo, run_id, sources, max_context_chars=1000)
    assert overflow["status"] == "context_overflow" and overflow["context"] is None


RELEASE = """ABC Corporation (Nasdaq: ABC) reports operating results.
The consolidated financial statements are prepared in accordance with U.S. GAAP.
ABC CORPORATION
CONSOLIDATED STATEMENTS OF INCOME
(U.S. dollars in millions, except per share data) (unaudited)
13 Weeks Ended  52 Weeks Ended
December 28, 2025  December 29, 2024  December 28, 2025  December 29, 2024
Net income 100 90 390 350
NET INCOME PER COMMON SHARE:
Basic $ 2.52 $ 2.32 $ 10.01 $ 9.04
Diluted $ 2.50 $ 2.30 $ 10.00 $ 9.00
Shares used in calculation (000's):
Diluted 40 39 39 38
"""


def seed(observation, source_id="release"):
    fields = ("value", "unit", "currency", "basis", "period", "period_start", "period_end", "statement_type", "locator", "source_quote", "subject", "metric")
    return {key: observation[key] for key in fields} | {"source_ref": source_id, "claim": "Reported diluted EPS.", "source_version": "1"}


def test_comparative_table_binds_metric_duration_and_current_vs_prior():
    result = release_eps_observations(RELEASE)
    assert not result["gaps"]
    assert [(item["value"], item["duration_weeks"], item["comparison"]) for item in result["observations"]] == [("2.50", 13, "current"), ("2.30", 13, "prior"), ("10.00", 52, "current"), ("9.00", 52, "prior")]
    observation = result["observations"][2]
    assert observation["period_start"] == "2024-12-30" and observation["period_end"] == "2025-12-28"
    assert observation["value"] != "39"  # Later diluted share-count row is not EPS.
    validation = validate_fact_claim(seed(observation), {"release": RELEASE}, source_versions={"release": {"version": 1, "hash": hashlib.sha256(RELEASE.encode()).hexdigest()}})
    assert validation["validation_status"] == "validated" and validation["semantic_status"] == "supported"


@pytest.mark.parametrize("field,value", [("value", "9.00"), ("unit", "USD"), ("currency", "CAD"), ("basis", "adjusted diluted"), ("period_end", "2024-12-29"), ("subject", "XYZ"), ("locator", "L11"), ("source_version", "2"), ("metric", "revenue")])
def test_table_claim_cannot_swap_basis_issuer_period_or_column(field, value):
    claim = seed(release_eps_observations(RELEASE)["observations"][2])
    claim[field] = value
    result = validate_fact_claim(claim, {"release": RELEASE}, source_versions={"release": {"version": 1, "hash": hashlib.sha256(RELEASE.encode()).hexdigest()}})
    assert result["validation_status"] != "validated"


def test_bare_dollar_is_not_a_currency_claim_and_misaligned_columns_rejected():
    ambiguous = RELEASE.replace("U.S. dollars", "dollars")
    result = release_eps_observations(ambiguous)
    assert result["observations"] and all(item["currency"] is None for item in result["observations"])
    assert any("ambiguous dollar" in gap for gap in result["gaps"])
    assert not release_eps_observations(RELEASE.replace("13 Weeks Ended  52 Weeks Ended", "52 Weeks Ended"))["observations"]


def sec_source(ticker="ABC"):
    data = {"cik": 1234, "taxonomy": "us-gaap", "tag": "EarningsPerShareDiluted", "entityName": "ABC CORP /NEW", "units": {"USD/shares": [
        {"start": "2024-12-30", "end": "2025-12-28", "val": 10, "form": "10-K", "filed": "2026-02-01", "accn": "0000001234-26-000001", "fy": 2025, "fp": "FY"},
        {"start": "2023-12-31", "end": "2024-12-29", "val": 9, "form": "10-K", "filed": "2026-02-01", "accn": "0000001234-26-000001", "fy": 2025, "fp": "FY"},
        {"start": "2025-09-29", "end": "2025-12-28", "val": 2.5, "form": "10-K", "filed": "2026-02-01", "accn": "0000001234-26-000001", "fy": 2025, "fp": "FY"},
    ]}}
    return json.dumps(data, separators=(",", ":")), {"url": "https://data.sec.gov/api/xbrl/companyconcept/CIK0000001234/us-gaap/EarningsPerShareDiluted.json"}


def test_sec_eps_requires_primary_url_exact_units_and_hash_bound_issuer():
    content, metadata = sec_source()
    observations = sec_eps_observations(content, metadata, issuer="ABC", cik="0000001234", as_of="2026-09-25")
    assert len(observations) == 2 and observations[0]["value"] == "10"
    claim = seed(observations[0], "sec")
    sources = {"sec": content, "release": RELEASE.replace("U.S. dollars", "dollars")}
    versions = {sid: {"version": 1, "hash": hashlib.sha256(raw.encode()).hexdigest()} for sid, raw in sources.items()}
    result = validate_fact_claim(claim, sources, source_metadata={"sec": metadata}, source_versions=versions, as_of="2026-09-25")
    assert result["validation_status"] == "validated"
    assert result["extraction"]["proof"]["issuer_source_id"] == "release"
    for change in ("wrong_issuer", "wrong_hash", "wrong_host", "quarter", "future"):
        changed_claim, changed_versions, changed_meta = copy.deepcopy(claim), copy.deepcopy(versions), {"sec": dict(metadata)}
        as_of = "2026-09-25"
        if change == "wrong_issuer": changed_claim["subject"] = "XYZ"
        if change == "wrong_hash": changed_versions["release"]["hash"] = "changed"
        if change == "wrong_host": changed_meta["sec"]["url"] = metadata["url"].replace("data.sec.gov", "example.com")
        if change == "quarter": changed_claim["period_start"] = "2025-09-29"
        if change == "future": as_of = "2026-01-01"
        result = validate_fact_claim(changed_claim, sources, source_metadata=changed_meta, source_versions=changed_versions, as_of=as_of)
        assert result["validation_status"] != "validated", change


def _attach_release(repo, run_id, workflow_id, content):
    source_id = repo.import_evidence(ImportRequest(namespace="real", kind="evidence", title="COST results", source_url="https://investor.costco.com/results", content=content, idempotency_key="compiler-release"))["source_id"]
    repo.append_run_sources(run_id, [source_id])
    # A test workflow originally containing only calls can add its release
    # before handoff; preserve that reciprocal frozen-source requirement.
    with repo.db.transaction(immediate=True) as conn:
        run = json.loads(conn.execute("SELECT input_snapshot_json FROM runs WHERE id=?", (run_id,)).fetchone()[0])
        run["requested_source_ids"].append(source_id)
        conn.execute("UPDATE runs SET input_snapshot_json=? WHERE id=?", (json.dumps(run), run_id))
        package = json.loads(conn.execute("SELECT result_json FROM research_workflow_runs WHERE id=?", (workflow_id,)).fetchone()[0])
        package["source_ids"].append(source_id)
        package["documents"]["release"] = {"status": "available", "source_id": source_id}
        conn.execute("UPDATE research_workflow_runs SET result_json=? WHERE id=?", (json.dumps(package), workflow_id))
    return source_id


def test_compiler_produces_atomic_validated_seeds_and_keeps_complete_qualifiers(context_case):
    repo, run_id, workflow_id, _, _, quote, capital, answer = context_case
    source_id = _attach_release(repo, run_id, workflow_id, RELEASE.replace("ABC", "COST"))
    snapshot = json.loads(repo.run_record(run_id)["input_snapshot_json"])
    sources = repo.source_packet("real", snapshot["source_ids"])
    before = [dict(task) for task in repo.tasks_for_run(run_id)]
    result = compile_earnings_assessment(repo, run_id, sources)
    assert result["status"] == "ready" and len(result["fact_claims"]) == 2
    assert [row["value"] for row in result["fact_claims"]] == ["10.00", "9.00"]
    assert [item["key"] for item in result["context"]["questions"]] == ["opportunity", "valuation", "catalyst", "downside", "portfolio_action"]
    assert result["metrics"]["context_chars"] <= 60000
    assert result["source_bindings"][source_id]["content_hash"] == hashlib.sha256(RELEASE.replace("ABC", "COST").encode()).hexdigest()
    assert all(value in json.dumps(result["context"], ensure_ascii=False) for value in (quote, capital, answer))
    assert [dict(task) for task in repo.tasks_for_run(run_id)] == before  # Compilation does not dispatch or mutate.


def test_compiler_requests_missing_financial_source_and_rejects_changed_packet(context_case):
    repo, run_id, *_ = context_case
    snapshot = json.loads(repo.run_record(run_id)["input_snapshot_json"])
    sources = repo.source_packet("real", snapshot["source_ids"])
    result = compile_earnings_assessment(repo, run_id, sources)
    assert result["status"] == "needs_valuation_evidence" and not result["fact_claims"]
    assert result["valuation_readiness"]["acquisition_requests"][0]["url"].endswith("EarningsPerShareDiluted.json")
    changed = copy.deepcopy(sources)
    changed[0]["content"] += " fabricated revised evidence"
    assert compile_earnings_assessment(repo, run_id, changed) is None


def test_sec_supplement_binds_to_original_release_without_rewriting_package(context_case):
    repo, run_id, workflow_id, *_ = context_case
    _attach_release(repo, run_id, workflow_id, RELEASE.replace("ABC", "COST").replace("U.S. dollars", "dollars"))
    raw, metadata = sec_source()
    raw = raw.replace("ABC CORP", "COST CORP")
    sid = repo.import_evidence(ImportRequest(namespace="real", kind="evidence", title="COST SEC diluted EPS", source_url=metadata["url"], content=raw, idempotency_key="compiler-sec"))["source_id"]
    repo.append_run_sources(run_id, [sid])
    snapshot = json.loads(repo.run_record(run_id)["input_snapshot_json"])
    result = compile_earnings_assessment(repo, run_id, repo.source_packet("real", snapshot["source_ids"]))
    assert result["status"] == "ready"
    assert [item["value"] for item in result["fact_claims"]] == ["10", "9"]
    assert all(item["source_ref"] == sid and item["currency"] == "USD" for item in result["fact_claims"])
    with repo.db.operation() as conn:
        package = json.loads(conn.execute("SELECT result_json FROM research_workflow_runs WHERE id=?", (workflow_id,)).fetchone()[0])
        assert sid not in package["source_ids"]
    assert sid in result["source_bindings"]
    assert result["seed_proofs"][0]["validation"]["proof"]["issuer_source_id"]


def test_compiler_cannot_silently_clip_a_required_oversized_packet(context_case):
    repo, run_id, *_ = context_case
    snapshot = json.loads(repo.run_record(run_id)["input_snapshot_json"])
    result = compile_earnings_assessment(repo, run_id, repo.source_packet("real", snapshot["source_ids"]), max_context_chars=500)
    assert result["status"] == "context_overflow" and result["context"] is None


def _attach_filing(repo, run_id, workflow_id, content, role="current_filing"):
    sid = repo.import_evidence(ImportRequest(namespace="real", kind="evidence", title="Annual financial report", source_url=f"https://example.com/{role}", content=content, idempotency_key=f"compiler-{role}"))["source_id"]
    repo.append_run_sources(run_id, [sid])
    with repo.db.transaction(immediate=True) as conn:
        snapshot = json.loads(conn.execute("SELECT input_snapshot_json FROM runs WHERE id=?", (run_id,)).fetchone()[0])
        snapshot["requested_source_ids"].append(sid)
        conn.execute("UPDATE runs SET input_snapshot_json=? WHERE id=?", (json.dumps(snapshot), run_id))
        package = json.loads(conn.execute("SELECT result_json FROM research_workflow_runs WHERE id=?", (workflow_id,)).fetchone()[0])
        package["source_ids"].append(sid)
        package["documents"][role] = {"status": "available", "source_id": sid}
        conn.execute("UPDATE research_workflow_runs SET result_json=? WHERE id=?", (json.dumps(package), workflow_id))
    return sid


def _large_filing():
    sections = [
        "CONSOLIDATED STATEMENTS OF INCOME\nUSD millions, except per-share amounts.\nFiscal year 2025 2024\nRevenue 200 180\nDiluted EPS 10 9",
        "CONSOLIDATED BALANCE SHEETS\nUSD millions.\nAs of December 28, 2025 and December 29, 2024\nCash 200 190\nDebt 100 120",
        "CONSOLIDATED STATEMENTS OF CASH FLOWS\nUSD millions.\nFiscal years ended 2025 2024\nOperating cash flows 90 80\nPurchases of property and equipment (40) (35)",
        "LIQUIDITY AND CAPITAL RESOURCES\nCapital expenditures were $40 million for fiscal 2025.\nConstruction costs increased due to higher steel prices.",
        "ITEM 1A. RISK FACTORS\nOur margins could decline if input costs rise faster than pricing.\nThese risks could affect future earnings and cash flows.",
    ]
    irrelevant = "General corporate background and exhibits unrelated to the selected financial sections.\n" * 900
    return irrelevant + ("\n" + irrelevant).join(sections) + "\n" + irrelevant


def _locator_lines(locator):
    parts = locator.replace("L", "").split("-")
    return range(int(parts[0]), int(parts[-1]) + 1)


def test_large_filings_are_bounded_with_original_lines_and_explicit_omissions(context_case):
    repo, run_id, workflow_id, _, source_ids, *_ = context_case
    release_id = _attach_release(repo, run_id, workflow_id, RELEASE.replace("ABC", "COST"))
    filing = _large_filing()
    filing_id = _attach_filing(repo, run_id, workflow_id, filing)
    snapshot = json.loads(repo.run_record(run_id)["input_snapshot_json"])
    sources = repo.source_packet("real", snapshot["source_ids"])
    result = compile_earnings_assessment(repo, run_id, sources, max_context_chars=80_000)
    assert result["status"] == "ready"
    assert result["metrics"]["context_chars"] <= 80_000
    evidence = result["context"]["evidence"]
    for sid, kind in [(source_ids[0], "complete_latest_transcript"), (release_id, "complete_earnings_release")]:
        span = next(row for row in evidence if row["id"] == sid)
        original = next(row["content"] for row in sources if row["id"] == sid)
        assert span["kind"] == kind
        assert span["content"] == "\n".join(f"L{i}: {line}" for i, line in enumerate(original.splitlines(), 1))
    selected = [row for row in evidence if row["id"] == filing_id]
    assert selected and all(row["kind"] == "filing_financial_section" for row in selected)
    assert all(row["content_hash"] == hashlib.sha256(filing.encode()).hexdigest() and row["version"] == 1 for row in selected)
    for span in selected:
        assert span["content"] == "\n".join(f"L{i}: {filing.splitlines()[i - 1]}" for i in _locator_lines(span["locator"]))
    coverage = result["context"]["coverage"]["filings"][0]
    assert coverage["status"] == "partial"
    assert set(coverage["topics_included"]) == {"income", "cash_flow", "balance_sheet", "capex_liquidity", "risk"}
    included = {i for locator in coverage["included_spans"] for i in _locator_lines(locator)}
    omitted = {i for locator in coverage["omitted_spans"] for i in _locator_lines(locator)}
    assert included.isdisjoint(omitted)
    assert included | omitted == set(range(1, len(filing.splitlines()) + 1))
    assert result["context"]["coverage"]["status"] == "partial"
    assert all(row["source_id"] == filing_id for row in result["context"]["coverage"]["omitted_spans"])


def test_small_filing_is_labelled_as_filing_and_unmatched_or_long_lines_are_not_clipped():
    from backend.app.research.assessment_evidence import _filing_selection
    source = {"id": "annual", "version": 3, "content_hash": "immutable", "content": "CONSOLIDATED BALANCE SHEETS\nUSD millions\nCash 200"}
    spans, coverage = _filing_selection(source, "prior_filing", 5_000)
    assert spans[0]["kind"] == "complete_filing"
    assert coverage["status"] == "complete" and coverage["omitted_spans"] == []
    source["content"] = "Capital expenditures " + "x" * 40_000
    spans, coverage = _filing_selection(source, "prior_filing", 4_000)
    assert spans == []
    assert coverage["status"] == "unavailable" and coverage["omitted_spans"] == ["L1"]
    source["content"] = "Unrecognized financial document format " * 2_000
    spans, coverage = _filing_selection(source, "prior_filing", 4_000)
    assert spans == [] and coverage["topic_gaps"]


def test_filing_selection_cannot_displace_an_oversized_required_release(context_case):
    repo, run_id, workflow_id, *_ = context_case
    _attach_release(repo, run_id, workflow_id, RELEASE.replace("ABC", "COST") + "\nAdditional management disclosure.\n" * 2_000)
    _attach_filing(repo, run_id, workflow_id, _large_filing())
    snapshot = json.loads(repo.run_record(run_id)["input_snapshot_json"])
    result = compile_earnings_assessment(repo, run_id, repo.source_packet("real", snapshot["source_ids"]), max_context_chars=40_000)
    assert result["status"] == "context_overflow"
    assert result["context"] is None
    assert result["metrics"]["context_chars"] > 40_000


def test_filing_cross_references_and_contents_do_not_displace_financial_tables():
    from backend.app.research.assessment_evidence import _filing_selection
    content = "CONSOLIDATED STATEMENTS OF INCOME .......... 20\nConsolidated statements of cash flows 21\n" + "See the consolidated statements of income for additional details.\n" * 40 + "Background.\n" * 900
    content += "CONSOLIDATED STATEMENTS OF INCOME\nUSD millions\nFiscal years 2025 2024\nRevenue 200 180\nNet income 90 80\n" + "Appendix.\n" * 900
    source = {"id": "report", "version": 1, "content_hash": hashlib.sha256(content.encode()).hexdigest(), "content": content}
    spans, coverage = _filing_selection(source, "current_filing", 6_000)
    income = next(span for span in spans if span.get("topic") == "income")
    assert "Revenue 200 180" in income["content"]
    assert "cash_flow" not in coverage["topics_found"]


def test_current_release_currency_binds_exact_frozen_comparative_sec_eps():
    from backend.app.research.earnings_financials import bound_release_eps_observations
    raw, meta = sec_source()
    # SEC has only the prior annual period; the current release is newer.
    data = json.loads(raw)
    data['units']['USD/shares'] = [row for row in data['units']['USD/shares'] if row['end'] == '2024-12-29']
    data['units']['USD/shares'][0]['start'] = '2024-01-01'
    raw = json.dumps(data)
    release = RELEASE.replace('U.S. dollars', 'dollars')
    sources = {'release': release, 'sec': raw}
    versions = {sid: {'version': 1, 'hash': hashlib.sha256(content.encode()).hexdigest()} for sid, content in sources.items()}
    metadata = {'sec': meta}
    result = bound_release_eps_observations('release', sources, metadata, versions, as_of='2026-09-25')
    current = result['observations'][2]
    assert current['value'] == '10.00' and current['currency'] == 'USD'
    assert current['proof']['currency_binding'] == 'same_table_exact_comparative_sec_eps'
    assert current['proof']['currency_sources'][0]['value'] == '9'
    validation = validate_fact_claim(seed(current), sources, source_metadata=metadata, source_versions=versions, as_of='2026-09-25')
    assert validation['validation_status'] == 'validated'
    for change in ('value', 'period', 'issuer', 'hash', 'future', 'conflicting_currency'):
        changed = copy.deepcopy(data)
        altered_sources, altered_versions = dict(sources), copy.deepcopy(versions)
        if change == 'value': changed['units']['USD/shares'][0]['val'] = 8
        if change == 'period': changed['units']['USD/shares'][0]['start'] = '2023-12-31'
        if change == 'issuer': changed['entityName'] = 'XYZ Corporation'
        if change == 'future': changed['units']['USD/shares'][0]['filed'] = '2026-10-01'
        if change == 'conflicting_currency': changed['units']['CAD/shares'] = copy.deepcopy(changed['units']['USD/shares'])
        altered_sources['sec'] = json.dumps(changed)
        altered_versions['sec']['hash'] = hashlib.sha256(altered_sources['sec'].encode()).hexdigest()
        if change == 'hash': altered_versions['sec']['hash'] = 'wrong'
        result = bound_release_eps_observations('release', altered_sources, metadata, altered_versions, as_of='2026-09-25')
        assert result['observations'][2]['currency'] is None, change


def test_quarter_only_release_binds_currency_to_exact_prior_sec_quarter():
    from backend.app.research.earnings_financials import bound_release_eps_observations
    release = RELEASE.replace('U.S. dollars', 'dollars').replace('13 Weeks Ended  52 Weeks Ended', '13 Weeks Ended').replace('December 28, 2025  December 29, 2024  December 28, 2025  December 29, 2024', 'December 28, 2025  December 29, 2024').replace('Diluted $ 2.50 $ 2.30 $ 10.00 $ 9.00', 'Diluted $ 2.50 $ 2.30')
    raw, metadata = sec_source()
    data = json.loads(raw)
    data['units']['USD/shares'] = [{'start': '2024-09-30', 'end': '2024-12-29', 'val': 2.3, 'form': '10-Q', 'filed': '2025-02-01', 'accn': '0000001234-25-000001'}]
    raw = json.dumps(data)
    sources = {'release': release, 'sec': raw}
    versions = {sid: {'version': 1, 'hash': hashlib.sha256(content.encode()).hexdigest()} for sid, content in sources.items()}
    assert not sec_eps_observations(raw, metadata, issuer='ABC', cik='1234')  # Annual default unchanged.
    result = bound_release_eps_observations('release', sources, {'sec': metadata}, versions, as_of='2026-09-25')
    assert [item['currency'] for item in result['observations']] == ['USD', 'USD']
    validation = validate_fact_claim(seed(result['observations'][0]), sources, source_metadata={'sec': metadata}, source_versions=versions, as_of='2026-09-25')
    assert validation['validation_status'] == 'validated'
