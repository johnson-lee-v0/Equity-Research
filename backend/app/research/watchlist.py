"""Deterministic watch checks; a review trigger is never a trade instruction."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from typing import Any


def watch_key(run_id: str, ticker: str, trigger: dict[str, Any], candidate: dict[str, Any] | None = None) -> str:
    # Revision and source IDs are deliberately absent. Repeating the same
    # already-fired condition in a new decision must not start another loop.
    condition = {key: trigger.get(key) for key in (
        "type", "condition", "operator", "threshold", "upper_threshold",
        "currency", "catalyst", "evidence_condition", "trigger_date", "review_at", "reopen_when",
    )}
    account = ((candidate or {}).get("sizing") or {}).get("account_id") or (candidate or {}).get("account_id")
    instrument = ((candidate or {}).get("instrument_identity") or {}).get("asset_id") or ticker.upper()
    identity = [instrument, candidate.get("direction"), account] if candidate and (candidate.get("direction") or account) else ticker.upper()
    body = json.dumps([run_id, identity, condition], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


def monitoring_triggers(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    """An explicit review plan is executable by the existing local monitor."""
    triggers = [dict(trigger) for trigger in candidate.get("watch_triggers", [])]
    plan = candidate.get("action_plan") or {}
    review_at = plan.get("review_at")
    if _time(review_at) and not any(_time(t.get("trigger_date") or t.get("review_at")) == _time(review_at) for t in triggers):
        triggers.append({"type":"date", "condition":"Review the investment thesis and material evidence", "review_at":review_at, "status":"active", "source_refs":candidate.get("source_refs") or []})
    return triggers


def invalidation_trigger(revision: Any, source_ids: list[str]) -> dict[str, Any]:
    return {"type":"evidence","condition":f"Refresh invalidated evidence for decision revision {revision}","evidence_condition":"invalidated_decision","source_refs":source_ids,"status":"active"}


def _time(value: Any) -> datetime | None:
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _decimal(value: Any) -> Decimal | None:
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def evaluate_watch_trigger(
    trigger: dict[str, Any], *, now: datetime | None = None,
    observation: dict[str, Any] | None = None,
    previous_observation: dict[str, Any] | None = None,
    evidence_changed: bool = False, paused: bool = False,
) -> dict[str, Any]:
    """Check only observable conditions; prose catalysts never self-certify."""
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    result: dict[str, Any] = {"status": "active", "fired": False, "checked_at": clock.isoformat(), "reason": "Condition has not been met."}
    if paused or trigger.get("status") == "paused":
        return result | {"status": "paused", "reason": "Watch checks are paused."}
    if trigger.get("status") in {"fired", "expired"}:
        return result | {"status": trigger["status"], "reason": "This condition was already handled."}
    kind = trigger.get("type")
    if kind == "price":
        observed = observation or {}
        price, threshold = _decimal(observed.get("price")), _decimal(trigger.get("threshold"))
        stamp = _time(observed.get("as_of"))
        if price is None or price <= 0 or threshold is None or threshold <= 0 or stamp is None:
            return result | {"status": "unavailable", "reason": "A dated price and a numeric watch threshold are required."}
        if stamp > clock or clock - stamp > timedelta(days=4):
            return result | {"status": "unavailable", "reason": "The price observation is stale or future-dated."}
        if not trigger.get("currency") or str(observed.get("currency", "")).upper() != str(trigger["currency"]).upper():
            return result | {"status": "unavailable", "reason": "Price and threshold currencies do not match."}
        op = trigger.get("operator")
        if op == "at_or_below":
            met = price <= threshold
        elif op == "at_or_above":
            met = price >= threshold
        elif op == "between":
            upper = _decimal(trigger.get("upper_threshold"))
            if upper is None or upper < threshold:
                return result | {"status": "unavailable", "reason": "A valid upper threshold is required."}
            met = threshold <= price <= upper
        elif op == "crosses":
            previous = previous_observation or {}
            old_price, old_time = _decimal(previous.get("price")), _time(previous.get("as_of"))
            if old_price is None or old_price <= 0 or old_time is None or old_time >= stamp or str(previous.get("currency", "")).upper() != str(observed.get("currency", "")).upper():
                return result | {"status": "active", "observation": observed, "reason": "First price observation saved; a crossing needs a later observation."}
            met = (old_price < threshold <= price) or (old_price > threshold >= price)
        else:
            return result | {"status": "unavailable", "reason": "This price comparison is unsupported."}
        return result | {"status": "fired" if met else "active", "fired": met, "condition_met":met, "observation": observed, "reason": "The price condition was met; reassess the case before any action." if met else "The latest price has not met the condition."}
    if kind in {"catalyst", "evidence"} and evidence_changed:
        return result | {"status": "fired", "fired": True, "reason": "The linked evidence changed; verify whether it changes the investment case."}
    if kind in {"date", "catalyst", "evidence"}:
        due = _time(trigger.get("trigger_date") or trigger.get("review_at"))
        if due:
            met = clock >= due
            return result | {"status": "fired" if met else "active", "fired": met, "reason": "The review date arrived; the catalyst itself still needs verification." if met else "Waiting for the scheduled review date."}
        return result | {"status": "unavailable", "reason": "This condition needs a dated review or new linked evidence; its occurrence cannot be inferred from the description."}
    return result | {"status": "unavailable", "reason": "This watch condition is unsupported."}
