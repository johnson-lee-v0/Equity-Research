"""Frozen paper decisions and append-only, source-backed outcome observations.

This measures a fixed close-to-close counterfactual, never an executed trade.
Price observations can be refreshed without rewriting a decision or choosing
a better entry after the result is known. No reads perform network or writes.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
import re
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from ..db import digest, utc_now

CODE_VERSION = "paper-outcomes.v1"


def retained_identity(sources: list[dict[str, Any]], ticker: str, as_of: Any) -> dict[str, Any] | None:
    """Only dated, retained provider identities can anchor asset continuity."""
    end = _time(as_of)
    matches = []
    if not end:
        return None
    for source in sources:
        if not source.get("content_hash") or not source.get("version"):
            continue
        for line in str(source.get("content") or source.get("original_content") or "").splitlines():
            try:
                item = json.loads(line)
            except (ValueError, TypeError):
                continue
            if not isinstance(item, dict):
                continue
            identity = item.get("instrument_identity") or (item.get("metadata") or {}).get("instrument_identity")
            if not isinstance(identity, dict):
                continue
            observed = _time(identity.get("observed_at") or identity.get("retrieved_at") or identity.get("as_of"))
            captured = _time(source.get("retrieved_at") or (item.get("metadata") or {}).get("retrieved_at"))
            # Repository timestamps have second precision.
            observed = observed.replace(microsecond=0) if observed else None
            captured = captured.replace(microsecond=0) if captured else None
            asset_id = identity.get("asset_id") or identity.get("id")
            symbol = identity.get("symbol") or identity.get("provider_symbol") or identity.get("ticker")
            if asset_id and identity.get("exchange") and str(symbol).upper() == ticker.upper() and observed and captured and observed <= end and captured <= end and end-observed <= timedelta(days=7) and identity.get("status") in {"active","consistent","verified","validated","supported"}:
                matches.append((observed,{"asset_id":asset_id,"exchange":identity["exchange"],"symbol":ticker,"observed_at":observed.isoformat(),"source_ref":source.get("id"),"source_hash":source["content_hash"]}))
    if not matches:
        return None
    newest = max(stamp for stamp,_ in matches)
    current = [identity for stamp,identity in matches if stamp == newest]
    return current[0] if len({(row["asset_id"],row["exchange"]) for row in current}) == 1 else None


def candidate_key(candidate: dict[str, Any]) -> str:
    identity = candidate.get("instrument_identity") or {}
    instrument = identity.get("asset_id") or identity.get("id") or candidate.get("ticker", "").upper()
    return digest([instrument, candidate.get("direction", "long"), (candidate.get("sizing") or {}).get("account_id")])[:24]


def _time(value: Any) -> datetime | None:
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def _number(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value))
        return number if number.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _text(value: Decimal | None) -> str | None:
    return format(value.quantize(Decimal("0.000001")), "f") if value is not None else None


def _research_effort(conn: Any, run_id: str, namespace: str) -> dict[str, Any]:
    """Count a pricing correction once, including reuse after a restart."""
    attempts = conn.execute("SELECT a.usage_json FROM task_attempts a JOIN tasks t ON t.id=a.task_id WHERE t.run_id=?", (run_id,)).fetchall()
    measured_tokens: list[int] = []

    def measured(usage: Any) -> int | None:
        total = usage.get("total_tokens") if isinstance(usage, dict) else None
        return total if type(total) is int and total >= 0 else None

    for attempt in attempts:
        total = measured(json.loads(attempt["usage_json"] or "{}"))
        if total is not None:
            measured_tokens.append(total)
    # These subattempts have their own durable receipts, not task_attempts rows.
    # Their usage must not also be added to the parent review's provider usage.
    corrections: dict[str, int | None] = {}
    rows = conn.execute("SELECT payload_json FROM events WHERE namespace=? AND run_id=? AND type='investment_valuation_input_correction' ORDER BY sequence_id", (namespace, run_id))
    for row in rows:
        try:
            record = json.loads(row["payload_json"])
        except (TypeError, ValueError):
            continue
        if not isinstance(record, dict):
            continue
        correction_id = record.get("correction_attempt_id")
        if not isinstance(correction_id, str) or not correction_id:
            continue
        if record.get("status") == "started":
            corrections.setdefault(correction_id, None)
        elif correction_id in corrections and record.get("status") in {"completed", "failed", "cancelled"}:
            total = measured(record.get("usage"))
            if total is not None:
                corrections[correction_id] = total
    measured_tokens.extend(total for total in corrections.values() if total is not None)
    attempt_count = len(attempts) + len(corrections)
    return {
        "attempts": attempt_count,
        "valuation_correction_attempts": len(corrections),
        "measured_tokens": sum(measured_tokens) if measured_tokens else None,
        "attempts_with_token_usage": len(measured_tokens),
        "usage_incomplete": len(measured_tokens) < attempt_count,
        "cash_cost": None,
        "cost_reason": "Subscription usage does not establish a per-case monetary cost.",
    }


def freeze_decision(conn: Any, case: dict[str, Any], namespace: str, *, now: str | None = None, sources: list[dict[str, Any]] | None = None) -> None:
    """Called inside the case-persistence transaction, never from a GET."""
    now = now or utc_now()
    research_effort = _research_effort(conn, case["run_id"], namespace)
    for candidate in case.get("candidates", []):
        plan = candidate.get("action_plan") or {}
        benchmark = plan.get("benchmark_ticker")
        if benchmark and not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,19}", benchmark):
            benchmark = None
        decision_time, frozen_time = _time(case.get("as_of")), _time(now)
        retrospective = bool(case.get("correction") or case.get("correction_key") or not decision_time or not frozen_time or frozen_time-decision_time > timedelta(minutes=5))
        key = candidate_key(candidate)
        baseline = {
            "code_version": CODE_VERSION, "run_id": case["run_id"],
            "decision_revision": case["decision_revision"], "candidate_key": key,
            "ticker": candidate["ticker"], "direction": candidate.get("direction", "long"),
            "outcome": candidate.get("outcome"), "decision_as_of": case.get("as_of"),
            "frozen_at": now, "retrospective": retrospective,
            "candidate": candidate, "input_hash": digest(candidate),
            "benchmark_ticker": benchmark, "benchmark_rationale": plan.get("benchmark_rationale"),
            "review_at": plan.get("review_at"), "horizon": candidate.get("horizon"),
            "entry_rule": "Frozen observed predecision close, not an assumed execution or a retrospectively selected entry.",
            "reference_quote": frozen_quote(sources or [],candidate["ticker"],case.get("as_of")),
            "benchmark_quote": frozen_quote(sources or [],benchmark,case.get("as_of")) if benchmark else None,
            "instrument_identity": retained_identity(sources or [],candidate["ticker"],case.get("as_of")),
            "benchmark_identity": retained_identity(sources or [],benchmark,case.get("as_of")) if benchmark else None,
            "return_basis": "split-adjusted price-only, excluding fees, dividends, borrow and FX",
            "kind": "declined_counterfactual" if candidate.get("outcome") == "decline" else "hypothetical",
            "research_effort": research_effort,
        }
        conn.execute("INSERT OR IGNORE INTO idea_baselines(id,namespace,run_id,decision_revision,candidate_key,ticker,direction,outcome,baseline_json,frozen_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (
            "base_"+uuid4().hex, namespace, case["run_id"], case["decision_revision"], key,
            candidate["ticker"], candidate.get("direction", "long"), candidate.get("outcome"), json.dumps(baseline), now,
        ))


class OutcomeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    namespace: Literal["real", "demo"] = "real"
    idempotency_key: str = Field(min_length=1, max_length=200)
    evaluation_at: str
    source_ids: list[str] = Field(default_factory=list, max_length=100)
    thesis_result: Literal["unknown", "supported", "weakened", "invalidated", "mixed"] = "unknown"
    catalyst_result: Literal["unknown", "occurred", "delayed", "cancelled", "mixed"] = "unknown"
    review_note: str = Field(default="", max_length=4000)
    error_tags: list[Literal["thesis", "valuation", "timing", "data", "sizing", "execution"]] = Field(default_factory=list, max_length=6)


class LifecycleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    namespace: Literal["real", "demo"] = "real"
    idempotency_key: str = Field(min_length=1, max_length=200)
    candidate_key: str = Field(min_length=1, max_length=100)
    state: Literal["held", "closed", "watchlist"]
    reason: str = Field(min_length=1, max_length=4000)
    confirmed: bool = False


def source_bars(sources: list[dict[str, Any]], ticker: str) -> list[dict[str, Any]]:
    """Read the retained connector format with source/line attribution."""
    rows: dict[str, dict[str, Any]] = {}
    # Identity is source-level evidence. Resolving it for every bar caused
    # each row to rescan every retained JSON archive, turning a large daily
    # window into a quadratic projection. Keep the boundary in the cache
    # key: an explicit source may not fall back to another archive, and a
    # different capture/version must get a fresh resolution.
    identity_cache: dict[tuple[Any, ...], dict[str, Any] | None] = {}
    source_signatures = tuple(
        (
            index,
            str(source.get("id") or ""),
            str(source.get("content_hash") or ""),
            str(source.get("version") or ""),
        )
        for index, source in enumerate(sources)
    )
    for source_index, source in enumerate(sources):
        body = str(source.get("content") or source.get("original_content") or "")
        metadata: dict[str, Any] = {}
        source_status = None
        parsed: list[tuple[int, dict[str, Any]]] = []
        for line, text in enumerate(body.splitlines(), 1):
            try:
                item = json.loads(text)
            except (ValueError, TypeError):
                continue
            if not isinstance(item, dict):
                continue
            if item.get("source_type") == "market_bars":
                metadata = item.get("metadata") or {}
                source_status = item.get("status")
            elif item.get("symbol", "").upper() == ticker.upper():
                parsed.append((line, item))
        if source_status != "ok" or str(metadata.get("timeframe", "")).casefold() not in {"daily", "1day", "1d", "day"}:
            continue
        if str(metadata.get("adjustment", "")).casefold() != "split":
            continue
        for line, item in parsed:
            stamp, price = _time(item.get("timestamp")), _number(item.get("close"))
            if stamp is None or price is None or price <= 0 or item.get("complete") is not True:
                continue
            captured = _time(source.get("retrieved_at") or metadata.get("retrieved_at"))
            available = stamp+timedelta(days=1)
            if captured is None or captured < available:
                continue
            row = {"as_of": stamp.isoformat(), "available_at":available.isoformat(), "captured_at":captured.isoformat(), "price": str(price), "currency": metadata.get("currency"), "source_ref": source.get("id"), "source_hash":source.get("content_hash"), "source_version":source.get("version"), "locator": f"L{line}", "adjustment": "split", "coverage_start":metadata.get("start"), "coverage_end":metadata.get("end"), "pagination_complete":bool(metadata.get("pagination_exhausted") or metadata.get("pagination_stopped_reason") == "provider_exhausted")}
            explicit_identity = any(key in metadata for key in ("instrument_identity","asset_id","exchange"))
            capture_key = captured.isoformat()
            if explicit_identity:
                # An explicit but invalid identity is intentionally terminal
                # for this source. Restrict the resolver input to the source
                # that supplied the explicit marker so another archive cannot
                # silently relabel these bars.
                identity_key = ("source", source_index, source_signatures[source_index], ticker.upper(), capture_key)
                identity_sources = [source]
            else:
                # Connector bars without an inline identity may use a dated
                # identity record from the retained packet. The packet and
                # capture boundary are part of the cache key, while the
                # resolver still sees the complete packet exactly as before.
                identity_key = ("packet", source_signatures, ticker.upper(), capture_key)
                identity_sources = sources
            if identity_key not in identity_cache:
                identity_cache[identity_key] = retained_identity(identity_sources, ticker, capture_key)
            row["instrument_identity"] = identity_cache[identity_key]
            prior = rows.get(row["as_of"])
            if prior and (prior.get("conflict") or _number(prior["price"]) != price or prior["currency"] != row["currency"] or (prior.get("instrument_identity") or {}).get("asset_id") != (row.get("instrument_identity") or {}).get("asset_id")):
                # An unresolved conflicting close is not selected by source order.
                rows[row["as_of"]] = {"conflict": True, "as_of": row["as_of"]}
            elif not prior:
                rows[row["as_of"]] = row
            elif row.get("pagination_complete"):
                prior_start, prior_end = _time(prior.get("coverage_start")), _time(prior.get("coverage_end"))
                row_start, row_end = _time(row.get("coverage_start")), _time(row.get("coverage_end"))
                if row_start and row_end and (not prior.get("pagination_complete") or not prior_start or not prior_end or (row_start <= prior_start and row_end >= prior_end)):
                    rows[row["as_of"]] = row
    return sorted(rows.values(), key=lambda row: row["as_of"])


def frozen_quote(sources: list[dict[str, Any]], ticker: str, as_of: str | None) -> dict[str, Any] | None:
    decision = _time(as_of)
    if decision is None:
        return None
    prior_sources = []
    for source in sources:
        try:
            header = json.loads(str(source.get("content") or source.get("original_content") or "").splitlines()[0])
        except (ValueError,IndexError):
            header = {}
        captured = _time(source.get("retrieved_at") or (header.get("metadata") or {}).get("retrieved_at"))
        if captured and captured <= decision:
            prior_sources.append(source)
    eligible = [row for row in source_bars(prior_sources,ticker) if not row.get("conflict") and _time(row["captured_at"]) <= decision and _time(row["available_at"]) <= decision and decision-_time(row["available_at"]) <= timedelta(days=4) and row.get("source_hash") and row.get("source_version")]
    return max(eligible,key=lambda row:row["as_of"]) if eligible else None


def _path_return(rows: list[dict[str, Any]], anchor: dict[str, Any] | None, end: datetime, direction: str, identity: dict[str, Any] | None = None) -> dict[str, Any]:
    if not anchor:
        return {"status":"unavailable","reason":"No source-bound observed close was frozen before this decision. A later entry cannot be substituted."}
    start = _time(anchor["as_of"])
    selected = [row for row in rows if start <= _time(row["as_of"]) and _time(row.get("available_at") or row["as_of"]) <= end]
    if not selected or any(row.get("conflict") for row in selected):
        return {"status": "unavailable", "reason": "Missing or conflicting split-adjusted daily closes."}
    if not identity or not identity.get("asset_id") or not identity.get("exchange") or any((row.get("instrument_identity") or {}).get("asset_id") != identity["asset_id"] or (row.get("instrument_identity") or {}).get("exchange") != identity["exchange"] for row in selected):
        return {"status":"unavailable","reason":"A matching retained asset ID and exchange are required at the original decision and each later price observation. Missing or changed identity cannot establish a paper return."}
    first, last = selected[0], selected[-1]
    if first["as_of"] != anchor["as_of"] or _number(first["price"]) != _number(anchor["price"]) or first.get("currency") != anchor.get("currency"):
        return {"status":"unavailable","reason":"Frozen anchor is missing or its adjustment vintage changed. Resolve corporate-action/price provenance before comparing returns."}
    complete_windows = [row for row in selected if row.get("pagination_complete") and _time(row.get("coverage_start")) and _time(row.get("coverage_end")) and _time(row["coverage_start"]) <= start and _time(row["coverage_end"]) >= end]
    if not complete_windows or end-_time(last["available_at"]) > timedelta(days=4) or len(selected) < 2:
        return {"status":"unavailable","reason":"A completed provider window covering the frozen anchor and evaluation is required; sparse selected prices cannot establish the outcome."}
    currencies = {row.get("currency") for row in selected}
    if len(currencies) != 1 or not next(iter(currencies)):
        return {"status": "unavailable", "reason": "A single explicit observation currency is required."}
    entry = Decimal(first["price"])
    sign = Decimal(-1) if direction == "short" else Decimal(1)
    wealth = [Decimal(1)+sign*(Decimal(row["price"])-entry)/entry for row in selected]
    peak, drawdown = Decimal(1), Decimal(0)
    for value in wealth:
        peak = max(peak, value)
        drawdown = min(drawdown, (value-peak)/peak)
    day = _time(first["as_of"]).date()
    end_day = _time(last["as_of"]).date()
    weekdays = 0
    while day <= end_day:
        weekdays += day.weekday() < 5
        day += timedelta(days=1)
    coverage = min(1, len(selected)/max(1, weekdays))
    return {
        "status": "complete", "return": _text(wealth[-1]-1),
        "drawdown": _text(drawdown) if coverage >= 0.8 else None,
        "drawdown_basis": "observed daily closes; intraday loss may be larger",
        "currency": first["currency"], "entry": first, "exit": last,
        "observation_count": len(selected), "weekday_coverage": coverage,
        "source_refs": sorted({row["source_ref"] for row in selected if row.get("source_ref")}),
    }


def evaluate_baseline(baseline: dict[str, Any], request: OutcomeRequest, sources: list[dict[str, Any]], *, now: str | None = None) -> dict[str, Any]:
    end, start, clock = _time(request.evaluation_at), _time(baseline.get("decision_as_of")), _time(now or utc_now())
    if end is None or start is None or end < start or end > clock:
        raise ValueError("Evaluation must be dated between the frozen decision and the present.")
    if (request.thesis_result != "unknown" or request.catalyst_result != "unknown") and (not request.review_note.strip() or not sources):
        raise ValueError("A thesis or catalyst assessment requires a review note and retained evidence.")
    instrument = _path_return(source_bars(sources, baseline["ticker"]), baseline.get("reference_quote"), end, baseline["direction"],baseline.get("instrument_identity"))
    benchmark_ticker = baseline.get("benchmark_ticker")
    benchmark = _path_return(source_bars(sources, benchmark_ticker), baseline.get("benchmark_quote"), end, "long",baseline.get("benchmark_identity")) if benchmark_ticker else {"status": "unavailable", "reason": "No benchmark was frozen with the decision."}
    comparable = instrument["status"] == benchmark["status"] == "complete" and instrument["currency"] == benchmark["currency"] and instrument["entry"]["as_of"] == benchmark["entry"]["as_of"] and instrument["exit"]["as_of"] == benchmark["exit"]["as_of"]
    excess = Decimal(instrument["return"])-Decimal(benchmark["return"]) if comparable else None
    review_at = _time(baseline.get("review_at"))
    return {
        "code_version": CODE_VERSION, "kind": baseline["kind"], "decision_revision": baseline["decision_revision"],
        "evaluation_at": end.isoformat(), "observed_at": clock.isoformat(), "retrospective": baseline.get("retrospective", True),
        "status": instrument["status"], "instrument": instrument, "benchmark": benchmark,
        "benchmark_ticker": benchmark_ticker, "excess_return": _text(excess), "benchmark_comparable": comparable,
        "return_basis": baseline["return_basis"], "horizon": baseline.get("horizon"),
        "maturity": "matured" if review_at and end >= review_at else "interim" if review_at else "horizon_unconfigured",
        "thesis_result": request.thesis_result, "catalyst_result": request.catalyst_result,
        "assessment_basis": "user-reviewed evidence; price movement alone does not prove the thesis",
        "review_note": request.review_note, "error_tags": request.error_tags,
        "source_refs": request.source_ids, "source_hashes": {source["id"]: source.get("content_hash") for source in sources},
        "missed_opportunity": bool(baseline.get("outcome") == "decline" and excess is not None and excess > 0),
        "limitations": ["Hypothetical comparison, not an executed position or realized performance.", "Dividends, fees, financing, borrow costs, intraday gaps and currency conversion are excluded.", "Retrospective baselines are excluded from prospective summary results."],
    }


class LearningJournal:
    def __init__(self, repo: Any):
        self.repo = repo

    def list(self, namespace: str) -> dict[str, Any]:
        with self.repo.db.operation() as conn:
            rows = conn.execute("SELECT * FROM idea_baselines WHERE namespace=? ORDER BY frozen_at DESC", (namespace,)).fetchall()
            items = []
            for row in rows:
                observations = conn.execute("SELECT id,payload_json FROM idea_outcomes WHERE baseline_id=? ORDER BY observed_at,id", (row["id"],)).fetchall()
                item = json.loads(row["baseline_json"]) | {"id": row["id"], "observations": [json.loads(o["payload_json"]) | {"id": o["id"]} for o in observations]}
                items.append(item)
        initial: dict[tuple[str,str],dict[str,Any]] = {}
        for item in sorted(items,key=lambda row:row["decision_revision"]):
            initial.setdefault((item["run_id"],item["candidate_key"]),item)
        latest = [max(item["observations"],key=lambda observation:(_time(observation["evaluation_at"]),_time(observation["observed_at"]))) for item in initial.values() if item["observations"]]
        evaluated = [item for item in latest if item["status"] == "complete" and not item["retrospective"]]
        comparable = [item for item in evaluated if item["benchmark_comparable"] and item["maturity"] == "matured"]
        return {"items": items, "summary": {"baselines": len(items), "initial_idea_cohort":len(initial), "unevaluated":len(initial)-len(latest), "evaluated": len(latest), "prospective_evaluated": len(evaluated), "matured_comparable": len(comparable), "unavailable": sum(item["status"] != "complete" for item in latest), "declined_baselines": sum(item["outcome"] == "decline" for item in initial.values()), "missed_opportunities": sum(item["missed_opportunity"] for item in comparable), "mean_excess_return": _text(sum((Decimal(item["excess_return"]) for item in comparable), Decimal(0))/len(comparable)) if comparable else None, "cohort_basis":"First recorded decision for each case and candidate. Revisions are retained but never counted as extra independent ideas."}, "method": "Frozen close-to-close paper comparisons; all decision outcomes retained, no actual trading performance inferred."}

    def record(self, baseline_id: str, request: OutcomeRequest) -> dict[str, Any]:
        with self.repo.db.operation() as conn:
            row = conn.execute("SELECT * FROM idea_baselines WHERE id=? AND namespace=?", (baseline_id, request.namespace)).fetchone()
            prior = conn.execute("SELECT id,payload_json FROM idea_outcomes WHERE baseline_id=? AND idempotency_key=?", (baseline_id,request.idempotency_key)).fetchone() if row else None
        if not row:
            raise ValueError("Paper baseline was not found in this namespace.")
        request_hash = digest(request.model_dump())
        if prior:
            saved = json.loads(prior["payload_json"])
            if saved.get("request_hash") != request_hash:
                raise ValueError("Idempotency key already belongs to a different outcome observation.")
            return saved | {"id": prior["id"]}
        sources = self.repo.source_packet(request.namespace, request.source_ids)
        payload = evaluate_baseline(json.loads(row["baseline_json"]), request, sources)
        payload["request_hash"] = request_hash
        with self.repo.db.transaction(immediate=True) as conn:
            prior = conn.execute("SELECT id,payload_json FROM idea_outcomes WHERE baseline_id=? AND idempotency_key=?", (baseline_id,request.idempotency_key)).fetchone()
            if prior:
                saved = json.loads(prior["payload_json"])
                if saved.get("request_hash") != payload["request_hash"]:
                    raise ValueError("Idempotency key already belongs to a different outcome observation.")
                return saved | {"id": prior["id"]}
            record_id = "outcome_"+uuid4().hex
            conn.execute("INSERT INTO idea_outcomes(id,baseline_id,idempotency_key,payload_json,observed_at) VALUES(?,?,?,?,?)", (record_id,baseline_id,request.idempotency_key,json.dumps(payload),utc_now()))
            return payload | {"id": record_id}
