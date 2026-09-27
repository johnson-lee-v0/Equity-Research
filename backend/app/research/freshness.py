"""Dated evidence policy; fetching an old observation never resets its age."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from typing import Any, Mapping

POLICY_VERSION = "evidence-freshness.v1"
POLICIES = {"market_price":4, "daily_macro":4, "monthly_macro":45, "quarterly_fundamental":150, "annual_fundamental":460, "issuer_identity":30, "dated_assertion":90, "catalyst":0}


def _time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    # A fiscal year, quarter or vague period has no unambiguous observation
    # date. Never turn it into a fabricated calendar period end.
    if not re.match(r"^\d{4}-\d{2}-\d{2}(?:$|T| )",text):
        return None
    try:
        value = datetime.fromisoformat(text.replace("Z","+00:00"))
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    except ValueError:
        return None


def _evaluate(observation: Any, *, kind: str, as_of: str, source: Mapping[str,Any] | None = None) -> dict[str,Any]:
    source = source or {}
    clock, stamp = _time(as_of), _time(observation)
    result = {"policy_version":POLICY_VERSION,"kind":kind,"max_age_days":POLICIES[kind],"as_of":as_of,"observation_at":stamp.isoformat() if stamp else None,"publication_at":source.get("publication_at"),"status":"unknown","reason":"An exact observation or reporting-period end date is required; retrieval time does not establish freshness."}
    if source.get("stale") is True or source.get("is_superseded") is True or source.get("superseded_by"):
        return result | {"status":"superseded","reason":"This archived version has been superseded or invalidated."}
    if not clock or not stamp:
        return result
    known_at = [_time(source.get(name)) for name in ("publication_at","retrieved_at","retrieval_at")]
    if any(value and value > clock for value in known_at):
        return result | {"status":"future","reason":"This source was not published or retained by the decision's as-of time."}
    if kind == "catalyst":
        return result | {"status":"fresh" if stamp >= clock else "stale","reason":"Scheduled future date; this does not verify occurrence." if stamp >= clock else "The scheduled date has passed; verify what actually occurred."}
    age = clock-stamp
    if age < timedelta(0):
        return result | {"status":"future","reason":"An observed fact cannot have a future observation or reporting-period end."}
    status = "fresh" if age <= timedelta(days=POLICIES[kind]) else "stale"
    return result | {"status":status,"age_days":round(age.total_seconds()/86400,3),"reason":"Within the dated evidence policy; this does not certify that no newer release exists." if status == "fresh" else "Observation exceeds the policy's maximum age; re-fetching the same value does not refresh it."}


def evaluate_price_freshness(observed_at: Any, *, as_of: str, source: Mapping[str,Any] | None = None) -> dict[str,Any]:
    return _evaluate(observed_at,kind="market_price",as_of=as_of,source=source)


def evaluate_fact_freshness(fact: Mapping[str,Any], source: Mapping[str,Any] | None = None, *, as_of: str) -> dict[str,Any]:
    source = source or {}
    metric = str(fact.get("metric") or fact.get("claim") or "").casefold()
    source_type = str(source.get("source_type") or "").casefold()
    period = str(fact.get("period") or "").casefold()
    if any(word in metric for word in ("catalyst date","meeting date","earnings date","scheduled","event date")):
        kind = "catalyst"
    elif any(word in metric for word in ("ticker identity","issuer identity","instrument identity")):
        kind = "issuer_identity"
    elif source_type in {"market_data","market_bars"} or re.search(r"\b(?:open|high|low|close|price|nav)\b",metric):
        kind = "market_price"
    elif re.search(r"\b(?:yield|interest rate|policy rate|exchange rate)\b",metric):
        kind = "daily_macro"
    elif re.search(r"\b(?:inflation|cpi|pce|employment|payrolls|unemployment)\b",metric):
        kind = "monthly_macro"
    elif re.search(r"\b(?:q[1-4]|quarter|quarterly)\b",period):
        kind = "quarterly_fundamental"
    elif source_type == "filing" or re.search(r"\b(?:eps|revenue|earnings|ebitda|fcf|cash flow|debt|diluted shares|shares outstanding)\b",metric):
        kind = "annual_fundamental"
    else:
        kind = "dated_assertion"
    # Dates must describe the fact. Publication dates cannot silently replace
    # a missing fiscal/observation date, and provider-authored status is ignored.
    stamp = next((fact.get(name) for name in ("period_end","observed_at","as_of","period") if _time(fact.get(name))),None)
    return _evaluate(stamp,kind=kind,as_of=as_of,source=source)
