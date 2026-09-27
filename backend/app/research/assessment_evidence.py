"""Compile a verified earnings archive for one final investment assessment.

No provider call, network operation, or database mutation occurs here. The
compiler supplies evidence and exact financial seeds, never an analyst vote.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any

from ..db import digest, json_loads
from ..schemas import FactClaim
from .decision_questions import QUESTION_TEXT
from .earnings_context import build_earnings_context, verified_earnings_package
from .earnings_financials import bound_release_eps_observations, sec_eps_observations
from .fact_validation import validate_fact_claim


VERSION = "earnings-assessment-evidence.v2"
MAX_CONTEXT_CHARS = 160_000
MAX_FILING_CHARS = 24_000
_FILING_TOPICS = {
    "income": re.compile(r"(?:consolidated\s+)?statements? of (?:income|operations|earnings)|income statements?", re.I),
    "cash_flow": re.compile(r"statements? of cash flows?|cash flow statements?", re.I),
    "balance_sheet": re.compile(r"balance sheets?|statements? of financial (?:position|condition)", re.I),
    "capex_liquidity": re.compile(r"capital expenditures?|capital spending|liquidity and capital resources|investing activities|cash (?:used in|provided by) investing", re.I),
    "risk": re.compile(r"risk factors|market risk|credit risk|liquidity risk|interest rate risk|foreign (?:currency|exchange) risk", re.I),
}
_SECTION_HEADING = re.compile(r"^(?:part\s+[IVX]+\b|item\s+\d+[A-Z]?\s*[.:—-]|(?:condensed\s+)?(?:consolidated\s+)?(?:statements? of |balance sheets?\b)|notes to (?:the )?(?:consolidated )?financial)", re.I)

_THEMES = re.compile(r"renewal|member|growth|capital expenditur|capex|margin|tariff|inflation|pricing|forecast|guidance|risk|cash flow|profit|earnings", re.I)
_REQUEST_THEMES = re.compile(r"renewal|member|capital expenditur|capex|growth", re.I)


def _span(source: dict[str, Any], first: int, last: int, *, kind: str) -> dict[str, Any]:
    lines = source["content"].splitlines()
    return {"id": source["id"], "url": source.get("url"), "title": source.get("title"),
            "version": source["version"], "content_hash": source["content_hash"],
            "locator": f"L{first}" if first == last else f"L{first}-L{last}",
            "kind": kind, "content": "\n".join(f"L{index}: {lines[index - 1]}" for index in range(first, last + 1))}


def _filing_selection(source: dict[str, Any], role: str, budget: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Select intact original lines, never synthesize or relabel a filing.

    Small filings fit whole. Larger filings contribute bounded financial and
    risk sections; all excluded original line ranges are disclosed. These are
    reading excerpts, not a substitute for the separately validated facts.
    """
    lines = source["content"].splitlines()
    total = len(lines)
    anchors = {topic: [] for topic in _FILING_TOPICS}
    for index, line in enumerate(lines):
        # A table-of-contents entry is not the financial table itself.
        if re.search(r"\.{3,}\s*\d+\s*$", line) or re.search(r"\btable of contents\b", line, re.I):
            continue
        for topic, pattern in _FILING_TOPICS.items():
            match = pattern.search(line)
            if not match or (len(line.strip()) > 190 and topic != "capex_liquidity"):
                continue
            if topic != "capex_liquidity":
                if re.search(r"\s+\d{1,3}\s*$", line):
                    continue
                prefix = re.sub(r"^\s*item\s+\d+[A-Z]?\s*[.:—-]?\s*", "", line[:match.start()], flags=re.I)
                if not re.fullmatch(r"(?:(?:condensed|consolidated|combined|unaudited|interim|comparative|standalone|and)\b|[\s()–—-])*", prefix, re.I):
                    continue  # A narrative cross-reference is not a section heading.
            anchors[topic].append(index)
    selected: list[dict[str, Any]] = []
    intervals: list[tuple[int, int]] = []
    included_topics = set()
    whole = _span(source, 1, total, kind="complete_filing") | {"document_role": role} if total else None
    if whole and len(json.dumps(whole, ensure_ascii=False)) <= budget:
        selected, intervals = [whole], [(1, total)]
        included_topics = {key for key, value in anchors.items() if value}
    else:
        # Round-robin the first section of each topic before considering a
        # second occurrence, so an income statement cannot crowd out risk.
        candidates = []
        for occurrence in range(2):
            for topic, positions in anchors.items():
                # Heading repetition inside one window is not extra coverage.
                spaced = [value for i, value in enumerate(positions) if not i or value - positions[i - 1] > 12]
                if occurrence >= len(spaced):
                    continue
                anchor = spaced[occurrence]
                first = max(0, anchor - 2)
                stop = min(total, anchor + (90 if topic in {"income", "cash_flow", "balance_sheet", "risk"} else 16))
                for index in range(anchor + 1, stop):
                    if _SECTION_HEADING.match(lines[index].strip()):
                        stop = index
                        break
                last = first
                chars = 0
                for index in range(first, stop):
                    cost = len(json.dumps(f"L{index + 1}: {lines[index]}", ensure_ascii=False)) + 2
                    if chars + cost > 3_500:
                        break
                    chars += cost
                    last = index + 1
                if last <= anchor:
                    continue  # A single oversized line is never truncated.
                candidates.append((topic, first + 1, last))
        used = 0
        for topic, first, last in candidates:
            if any(start <= first and end >= last for start, end in intervals):
                included_topics.add(topic)
                continue
            span = _span(source, first, last, kind="filing_financial_section") | {"topic": topic, "document_role": role}
            size = len(json.dumps(span, ensure_ascii=False))
            if used + size > budget:
                continue
            selected.append(span)
            intervals.append((first, last))
            included_topics.add(topic)
            used += size
    merged: list[list[int]] = []
    for first, last in sorted(intervals):
        if merged and first <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], last)
        else:
            merged.append([first, last])
    omitted = []
    cursor = 1
    for first, last in merged:
        if cursor < first:
            omitted.append([cursor, first - 1])
        cursor = last + 1
    if cursor <= total:
        omitted.append([cursor, total])
    locator = lambda pair: f"L{pair[0]}" if pair[0] == pair[1] else f"L{pair[0]}-L{pair[1]}"
    coverage = {"source_id": source["id"], "document_role": role,
                "status": "complete" if total and not omitted else "partial" if selected else "unavailable",
                "total_lines": total, "included_lines": sum(last - first + 1 for first, last in merged),
                "included_spans": [locator(pair) for pair in merged], "omitted_spans": [locator(pair) for pair in omitted],
                "topics_found": [topic for topic, positions in anchors.items() if positions],
                "topics_included": sorted(included_topics),
                "topic_gaps": [topic for topic in _FILING_TOPICS if topic not in included_topics],
                "selection_method": "complete source" if total and not omitted else "bounded original-line financial/risk excerpts",
                "instruction": "Omitted lines were not supplied to the reviewer. Do not infer the filing or any topic was reviewed completely from these excerpts; use explicit periods and units, and request a source passage if needed."}
    return selected, coverage


def _transcript_spans(source: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Preserve entire exchanges/management turns, not isolated quotations."""
    lines = source["content"].splitlines()
    boundaries = []
    for index, line in enumerate(lines):
        if re.fullmatch(r"Operator\s*:?", line.strip(), re.I):
            following = " ".join(lines[index + 1:index + 3])
            if re.search(r"(?:first|next|final) question comes", following, re.I):
                boundaries.append(index)
    closing = next((index for index, line in enumerate(lines) if re.search(r"this concludes today's call|you may now disconnect", line, re.I)), len(lines) - 1)
    # For a differently formatted transcript, retain the complete source as
    # one unit. If it cannot fit, report that omission instead of clipping an
    # answer or pretending the unrecognized format was parsed completely.
    if not boundaries:
        return [_span(source, 1, len(lines), kind="complete_transcript_unparsed")], []
    prepared = []
    management_starts = [index - 1 for index in range(1, boundaries[0]) if re.match(r"^(?:CEO|CFO|COO|Chief |President|Investor Relations)", lines[index].strip(), re.I)]
    for number, start in enumerate(management_starts):
        end = (management_starts[number + 1] if number + 1 < len(management_starts) else boundaries[0]) - 1
        if end >= start:
            prepared.append(_span(source, start + 1, end + 1, kind="complete_management_turn"))
    exchanges = []
    for index, start in enumerate(boundaries):
        end = (boundaries[index + 1] - 1 if index + 1 < len(boundaries) else closing)
        if end < start:
            continue
        block = _span(source, start + 1, end + 1, kind="complete_question_answer_exchange")
        exchanges.append(block)
    return prepared, exchanges


def _compact_trends(earnings: dict[str, Any]) -> dict[str, Any]:
    # Per-point source identities are retained; hashes/versions appear once in
    # the immutable source manifest instead of being repeated for every bar.
    def observation(item: dict[str, Any]) -> dict[str, Any]:
        keep = {"period", "value", "low", "high", "kind", "status", "source_id", "locator", "qualifier", "amount_qualifier", "unit", "published_at", "measure_basis", "calculation", "definition_source", "source_method"}
        return {key: value for key, value in item.items() if key in keep and value is not None}
    series = []
    for item in earnings.get("series", []):
        columns = ["period", "value", "kind", "source_id", "locator", "low", "high", "qualifier"]
        series.append({key: item.get(key) for key in ("id", "label", "unit", "frequency", "basis", "coverage_note")} | {
            "columns": columns,
            "points": [[point.get(key, point.get("amount_qualifier") if key == "qualifier" else None) for key in columns] for point in item.get("points", [])],
        })
        calculated = [{"period": point.get("period"), "measure_basis": point["measure_basis"],
            "formula": point["calculation"]["formula"],
            "inputs": [{key: operand.get(key) for key in ("tag", "value", "unit", "source_id", "accession")} for operand in point["calculation"]["inputs"]],
            "definition_source": point.get("definition_source"), "status": "calculated_actual"}
            for point in item.get("points", []) if point.get("measure_basis") and isinstance(point.get("calculation", {}).get("inputs"), list)]
        if calculated:
            series[-1]["calculated_observations"] = calculated
    guidance = earnings.get("capex_guidance") or {}
    comparisons = []
    for item in guidance.get("comparisons", []):
        kept = {key: value for key, value in item.items() if key not in {"initial_source", "actual_source", "revisions"}}
        comparisons.append(kept | {"initial_source": observation(item["initial_source"]), "actual_source": observation(item["actual_source"]), "revisions": [observation(row) for row in item.get("revisions", [])]})
    return {"series": series, "capex_guidance": {"comparisons": comparisons, "explanations": [observation(row) | {"quote": row.get("quote")} for row in guidance.get("explanations", [])], "coverage": guidance.get("coverage")},
            "validation_version": earnings.get("validation_version"), "projection": earnings.get("projection"),
            "evidence_instruction": "Numerical observations were quote- and period-checked by the earnings workflow and rebound to the unchanged archive during compilation. Cite their source_id and original locator. Exact numerical quotations remain in the workflow archive and compiler quote register. They are not repeated here or promoted to independently corroborated investment facts."}


def compile_earnings_assessment(repo: Any, run_id: str, sources: list[dict[str, Any]], *, max_context_chars: int = MAX_CONTEXT_CHARS) -> dict[str, Any] | None:
    """Return a verified packet, ready-to-commit facts and acquisition needs.

    ``None`` means identity, namespace or immutable source bindings failed.
    ``status='needs_valuation_evidence'`` is actionable, not a finished report:
    callers should acquire the returned SEC source and compile again. The
    original workflow package is never changed by supplemental acquisitions.
    """
    started = time.perf_counter()
    with repo.db.operation() as conn:
        verified = verified_earnings_package(conn, run_id)
    if not verified:
        return None
    run = repo.run_record(run_id)
    snapshot = json_loads(run["input_snapshot_json"], {})
    expected = {item["id"]: item for item in snapshot.get("source_versions", []) if isinstance(item, dict) and item.get("id")}
    bindings = {}
    for source in sources:
        sid = source.get("id")
        frozen = expected.get(sid, {})
        if (source.get("namespace") != verified["namespace"] or source.get("version") != frozen.get("version")
                or source.get("content_hash") != frozen.get("content_hash")
                or hashlib.sha256(str(source.get("content") or "").encode()).hexdigest() != frozen.get("content_hash")):
            return None
        bindings[sid] = {"version": source["version"], "content_hash": source["content_hash"]}
    earnings = build_earnings_context(repo, run_id, sources)
    if earnings is None:
        return None
    ticker, package = verified["ticker"], verified["package"]
    by_id = {source["id"]: source for source in sources}
    source_content = {source["id"]: source["content"] for source in sources}
    source_metadata = {source["id"]: {key: source.get(key) for key in ("source_type", "url", "title", "publisher", "publication_at", "observed_at", "retrieved_at", "version", "content_hash")} for source in sources}
    source_versions = {sid: {"version": item["version"], "hash": item["content_hash"]} for sid, item in bindings.items()}
    as_of = run["as_of"] or package.get("as_of")
    observations = []
    parser_gaps = []
    documents = package.get("documents") or {}
    release_ids = list(dict.fromkeys(item["source_id"] for key, item in documents.items() if isinstance(item, dict) and key in {"release", "earnings_release"} and item.get("source_id")))
    filing_documents = [(key, item["source_id"]) for key, item in documents.items() if isinstance(item, dict) and key in {"current_filing", "prior_filing", "previous_filing"} and item.get("source_id") and item["source_id"] not in release_ids]
    financial_ids = list(dict.fromkeys([*release_ids, *(sid for _, sid in filing_documents)]))
    for sid in financial_ids:
        if sid not in by_id:
            continue
        parsed = bound_release_eps_observations(sid, source_content, source_metadata, source_versions, as_of=as_of)
        parser_gaps.extend(parsed["gaps"])
        observations.extend(item | {"source_ref": sid} for item in parsed["observations"] if item["issuer"] == ticker)
    for source in sources:
        if "EarningsPerShareDiluted" in str(source.get("url") or ""):
            observations.extend(item | {"source_ref": source["id"]} for item in sec_eps_observations(source["content"], source, issuer=ticker, cik=str(package["company"]["cik"]), as_of=as_of))
    # Prefer the newest annual anchor, then a prior annual for the historical
    # growth comparison. Quarter EPS is not silently annualized.
    annual = [item for item in observations if (item.get("duration_weeks") or 52) >= 50]
    annual.sort(key=lambda item: (item["period_end"], not item.get("proof", {}).get("currency_binding"), item.get("comparison") == "current"), reverse=True)
    facts: list[dict[str, Any]] = []
    seed_proofs = []
    seen_periods = set()
    for item in annual:
        if not item.get("currency") or not item.get("basis") or (item["period_start"], item["period_end"]) in seen_periods:
            continue
        fields = {key: item[key] for key in ("value", "unit", "currency", "basis", "period", "period_start", "period_end", "statement_type", "locator", "source_quote", "source_ref", "subject", "metric")}
        fields.update(claim_id=f"c{len(facts) + 1}", claim=f"{ticker} reported GAAP diluted EPS for {item['period']}.", source_version=str(bindings[item["source_ref"]]["version"]))
        claim = FactClaim.model_validate(fields)
        validation = validate_fact_claim(claim, source_content, source_metadata=source_metadata, source_versions=source_versions, as_of=as_of)
        if validation["validation_status"] == "validated" and validation["semantic_status"] == "supported":
            facts.append(claim.model_dump())
            seed_proofs.append({"claim_id": claim.claim_id, "validation": validation["extraction"], "source_id": claim.source_ref})
            seen_periods.add((item["period_start"], item["period_end"]))
        if len(facts) >= 2:
            break
    sec_url = f"https://data.sec.gov/api/xbrl/companyconcept/CIK{int(package['company']['cik']):010d}/us-gaap/EarningsPerShareDiluted.json"
    has_sec = any(source.get("url") == sec_url for source in sources)
    readiness = {"status": "ready" if facts else "needs_financial_baseline", "baseline_claim_id": facts[0]["claim_id"] if facts else None,
                 "baseline_period": facts[0]["period"] if facts else None,
                 "baseline_period_end": facts[0]["period_end"] if facts else None,
                 "baseline_is_latest_event": bool(facts and facts[0]["period_end"] == (package.get("event") or {}).get("period_end")),
                 "acquisition_requests": [] if facts or has_sec else [{"kind": "annual_diluted_eps", "url": sec_url, "reason": "Acquire an explicit-currency annual diluted EPS anchor; the latest earnings release's dollar symbol alone is insufficient."}],
                 "missing_inputs": [] if facts else ["A source-bound annual diluted EPS anchor with reporting currency, period and accounting/share basis."],
                 "instruction": "Use the newest validated annual baseline. If it predates the latest earnings event, disclose its age and model the full fiscal interval to the forecast period. Forecast growth and valuation multiples are assumptions requiring reasons, not missing reported facts. Preserve the target when personal position sizing is unavailable."}
    from .comparable_financials import latest_seeds
    comparable_metrics = []
    for item in latest_seeds(sources, ticker=ticker, cik=str(package["company"]["cik"]), as_of=as_of):
        if item["metric"] == "shares" and len(package["company"].get("tickers") or [ticker]) != 1:
            # A total common-share count cannot establish the quoted class's
            # per-share target without its own conversion/capitalization map.
            continue
        fields = {key: item.get(key) for key in ("value", "unit", "currency", "basis", "period", "period_start", "period_end", "statement_type", "locator", "source_quote", "source_ref", "subject", "metric")}
        claim = FactClaim.model_validate(fields | {"claim_id": f"c{len(facts) + 1}", "claim": f"{ticker} reported {item['metric']} for {item['period']}.", "source_version": str(bindings[item["source_ref"]]["version"])})
        validation = validate_fact_claim(claim, source_content, source_metadata=source_metadata, source_versions=source_versions, as_of=as_of)
        if validation["validation_status"] == "validated" and validation["semantic_status"] == "supported":
            facts.append(claim.model_dump())
            seed_proofs.append({"claim_id": claim.claim_id, "validation": validation["extraction"], "source_id": claim.source_ref})
            comparable_metrics.append(item["metric"])
    readiness["comparable_financial_metrics"] = comparable_metrics
    readiness["comparable_instruction"] = "Use saved source-bound sales, EBITDA or NAV/book operands where present. Never substitute operating income for EBITDA or assets for equity NAV. Peer ratios require their own evidence; gaps remain explicit."
    if "shares" in comparable_metrics and any(metric in comparable_metrics for metric in ("revenue", "ebitda", "book equity")):
        readiness.update(status="ready", missing_inputs=[])
    # Operating observations strengthen the five-question review without
    # changing EPS anchor selection or certifying valuation readiness alone.
    from .earnings_operating_facts import operating_trend_seeds
    for fields in operating_trend_seeds(earnings, sources, ticker=ticker, event=earnings["event"]):
        claim = FactClaim.model_validate(fields | {"claim_id": f"c{len(facts) + 1}"})
        validation = validate_fact_claim(claim, source_content, source_metadata=source_metadata, source_versions=source_versions, as_of=as_of)
        if validation["validation_status"] == "validated" and validation["semantic_status"] == "supported":
            facts.append(claim.model_dump())
            seed_proofs.append({"claim_id": claim.claim_id, "validation": validation["extraction"], "source_id": claim.source_ref})
    context: dict[str, Any] = {
        "version": VERSION, "ticker": ticker, "event": earnings["event"], "source_bindings": bindings,
        "questions": [{"key": key, "question": question} for key, question in QUESTION_TEXT.items()],
        "financial_seeds": [{key: value for key, value in fact.items() if value is not None and key not in {"binding_checks", "semantic_status", "matched_excerpt", "freshness"}} for fact in facts],
        "valuation_readiness": readiness, "earnings_context": _compact_trends(earnings) | {"event": earnings["event"], "workflow_id": verified["workflow_id"], "instruction": earnings["instruction"]},
        "evidence": [], "gaps": list(dict.fromkeys([*earnings["gaps"], *readiness["missing_inputs"]])),
        "coverage": {"complete_exchange_count": 0, "complete_management_turn_count": 0, "omitted_spans": [], "raw_archive_available": True,
                     "latest_transcript": "unavailable", "earnings_release": "complete" if release_ids and all(sid in by_id for sid in release_ids) else "unavailable",
                     "instruction": "The available latest transcript and earnings releases are supplied in full, with source-bound historical observations. Filing coverage is separately listed below; do not infer that archived documents or omitted topics were read in full."},
        "assessment_instruction": "Answer all five questions with citations; include a defended base/bear/bull 12-month target from a code-calculated valuation. Select growth and multiple assumptions with reasons anchored to the source facts, trend record and risks. Distinguish historical facts, management statements, forecasts and portfolio sizing. Do not return a finished assessment with a null target.",
    }
    from .valuation_context import compile_valuation_context, compact_valuation_context
    context["valuation_research_context"] = compact_valuation_context(compile_valuation_context(repo, run_id, sources, as_of=as_of))
    candidates: list[dict[str, Any]] = []
    transcript_id = earnings.get("latest_transcript_source_id")
    if transcript_id and transcript_id in by_id:
        prepared, exchanges = _transcript_spans(by_id[transcript_id])
        candidates.append(_span(by_id[transcript_id], 1, len(by_id[transcript_id]["content"].splitlines()), kind="complete_latest_transcript"))
        context["coverage"].update(complete_exchange_count=len(exchanges), complete_management_turn_count=len(prepared), latest_transcript="complete")
    for sid in release_ids:
        if sid in by_id:
            candidates.insert(0, _span(by_id[sid], 1, len(by_id[sid]["content"].splitlines()), kind="complete_earnings_release"))
    for span in candidates:
        context["evidence"].append(span)
        # Never trade away late-call questions to meet a size target. A
        # package exceeding this boundary requires an explicit split rather
        # than a superficially complete review of only keyword matches.
    # Preserve unvalidated raw financial observations as cited source data,
    # not as accepted valuation input or a fake fact register.
    context["financial_observation_gaps"] = list(dict.fromkeys(parser_gaps))
    context["coverage"]["filings"] = []
    mandatory_chars = len(json.dumps(context, ensure_ascii=False))
    # Reserve space for coverage metadata before allocating optional filing
    # excerpts. The latest call and actual release can never be traded away.
    available = max(0, max_context_chars - mandatory_chars - 8_000)
    filing_budget = min(MAX_FILING_CHARS, available // max(1, len(filing_documents)))
    seen_filings = set()
    for role, sid in filing_documents:
        if sid in seen_filings:
            continue
        seen_filings.add(sid)
        if sid not in by_id:
            context["coverage"]["filings"].append({"source_id": sid, "document_role": role, "status": "unavailable", "reason": "The filing is not present in the frozen source packet."})
            context["coverage"]["omitted_spans"].append({"source_id": sid, "document_role": role, "reason": "Source unavailable in frozen packet."})
            continue
        spans, coverage = _filing_selection(by_id[sid], role, filing_budget)
        context["evidence"].extend(spans)
        context["coverage"]["filings"].append(coverage)
        context["coverage"]["omitted_spans"].extend({"source_id": sid, "document_role": role, "locator": locator, "reason": "Outside the bounded financial/risk excerpts supplied for this assessment."} for locator in coverage["omitted_spans"])
    context["coverage"]["status"] = "partial" if context["coverage"]["omitted_spans"] or any(row["status"] != "complete" for row in context["coverage"]["filings"]) else "complete_selected_sources"
    context_chars = len(json.dumps(context, ensure_ascii=False))
    if context_chars > max_context_chars:
        return {"version": VERSION, "status": "context_overflow", "route": None, "fact_claims": facts, "seed_proofs": seed_proofs,
                "context": None, "valuation_readiness": readiness, "source_bindings": bindings,
                "metrics": {"context_chars": context_chars, "max_context_chars": max_context_chars},
                "error": "The complete latest call, release and trend packet exceeds its assessment budget; split the evidence compilation without truncating answers."}
    return {"version": VERSION, "status": "ready" if readiness["status"] == "ready" else "needs_valuation_evidence",
            "route": {"intent": "research", "horizon": "12 months", "tickers": [ticker], "selected_analysts": ["A03"], "research_queries": [], "rationale": "Verified single-company earnings archive; code compiles evidence before one final investment assessment."},
            "fact_claims": facts, "seed_proofs": seed_proofs, "context": context,
            "trend_quote_register": {"series": earnings.get("series"), "capex_guidance": earnings.get("capex_guidance")},
            "valuation_readiness": readiness, "source_bindings": bindings, "context_hash": digest(context),
            "metrics": {"source_count": len(sources), "source_chars": sum(len(source["content"]) for source in sources), "context_chars": context_chars,
                        "max_context_chars": max_context_chars, "fact_count": len(facts), "compile_ms": round((time.perf_counter() - started) * 1000, 2)}}
