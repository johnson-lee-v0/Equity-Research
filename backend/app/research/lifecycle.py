"""Current review overlays; frozen investment decisions remain unchanged."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from .learning import candidate_key


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
    except (ValueError, TypeError, InvalidOperation):
        return None


def review_overlay(candidate: dict[str, Any], checks: list[dict[str, Any]], *, paused: bool, state: str | None = None, now: str | None = None) -> dict[str, Any]:
    clock = _time(now) or datetime.now(timezone.utc)
    quotes = [check["observation"] for check in checks if isinstance(check.get("observation"), dict) and _time(check["observation"].get("as_of")) and _number(check["observation"].get("price")) is not None]
    current_quote: dict[str, Any] = {"status": "unavailable", "price": None, "currency": None, "as_of": None, "freshness": "unknown", "kind": "close", "reason": "No completed price check has been retained."}
    if quotes:
        quote = max(quotes, key=lambda q: _time(q["as_of"]))
        age = clock-_time(quote["as_of"])
        valid = timedelta(0) <= age <= timedelta(days=4) and _number(quote["price"]) > 0 and bool(quote.get("currency"))
        current_quote = quote | {"kind": "close", "freshness": "fresh" if valid else "stale", "status": "available" if valid else "unavailable", "reason": "Completed daily close; not a live quote." if valid else "The retained price is stale, future-dated, or incomplete."}
        associated = [check for check in checks if check.get("observation") == quote]
        latest_check = max(associated,key=lambda check:check.get("checked_at") or "",default={})
        current_quote["observation_status"] = latest_check.get("observation_status") or ("retained_previous" if paused or latest_check.get("status") in {"paused","unavailable"} else "current")
        current_quote["last_check_status"] = "paused" if paused else latest_check.get("status")
    entry = candidate.get("entry") or {}
    lower, upper = _number(entry.get("lower") or entry.get("upper")), _number(entry.get("upper") or entry.get("lower"))
    price = _number(current_quote.get("price"))
    distance: dict[str, Any] = {"status": "unavailable", "absolute": None, "percent": None, "inside_range": None, "reason": "A fresh close and a supported entry range in the same currency are required."}
    if current_quote["status"] == "available" and lower and upper and 0 < lower <= upper and current_quote["currency"] == entry.get("currency"):
        nearest = lower if price < lower else upper if price > upper else price
        distance = {"status": "available", "absolute": str(price-nearest), "percent": str((price-nearest)/nearest*100), "inside_range": lower <= price <= upper, "basis": "(close − nearest entry boundary) / boundary × 100; zero inside range"}
    pending_dates = []
    calendar_dates = {}
    for check in checks:
        if check.get("review_state") == "reviewed" or check.get("status") == "expired":
            continue
        trigger = check.get("trigger") or {}
        for field in ("trigger_date", "review_at"):
            stamp = _time(trigger.get(field))
            if stamp:
                pending_dates.append(stamp)
                raw = str(trigger.get(field))
                if len(raw) == 10:
                    calendar_dates[stamp] = raw
    stamp = _time((candidate.get("action_plan") or {}).get("review_at"))
    resolved_reviews = {_time((check.get("trigger") or {}).get("review_at") or (check.get("trigger") or {}).get("trigger_date")) for check in checks if check.get("review_state") == "reviewed"}
    if stamp and stamp not in resolved_reviews:
        pending_dates.append(stamp)
        raw = str((candidate.get("action_plan") or {}).get("review_at"))
        if len(raw) == 10:
            calendar_dates[stamp] = raw
    due = min(pending_dates) if pending_dates else None
    outcome = candidate.get("outcome")
    lifecycle_state = state or {"recommend": "recommended", "watchlist": "watchlist", "decline": "declined"}.get(outcome, "research")
    checked = [_time(check.get("checked_at")) for check in checks if _time(check.get("checked_at"))]
    overdue = bool(due and due <= clock and lifecycle_state != "closed")
    expires = _time((candidate.get("action_plan") or {}).get("expires_at"))
    setup_expired = bool(expires and expires <= clock)
    next_action = "Review the overdue catalyst or thesis condition; its date does not prove it occurred." if overdue else "Await the stated price or evidence condition." if checks else "Set a dated review or observable reopening condition."
    if paused:
        next_action = "Research is paused. " + next_action
    if setup_expired and lifecycle_state != "closed":
        next_action = ("Research is paused. " if paused else "") + "The entry setup expired. Reassess before proposing a new entry; existing holdings still need their planned review."
    return {"candidate_key": candidate_key(candidate), "lifecycle_state": lifecycle_state, "current_quote": current_quote, "distance_to_entry": distance, "review_due_at": calendar_dates.get(due, due.isoformat()) if due else None, "expires_at":expires.isoformat() if expires else None, "setup_expired":setup_expired, "overdue": overdue, "last_checked_at": max(checked).isoformat() if checked else None, "paused": paused, "next_action": next_action}
