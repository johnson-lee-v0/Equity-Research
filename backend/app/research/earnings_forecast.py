"""A fiscal-year bridge which keeps reported quarters distinct from estimates.

Inputs are source-bound *standalone* diluted EPS observations. Cumulative YTD
EPS is deliberately not differenced into quarterly EPS: diluted share counts
can differ between periods, so that would not reproduce reported quarterly EPS.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Mapping, Sequence


VERSION = "earnings-quarter-bridge.v1"


def number(value: Any) -> Decimal | None:
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def display(value: Decimal | None) -> str | None:
    return format(value.quantize(Decimal("0.000001")), "f") if value is not None else None


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def _eligible(observations: Sequence[Mapping[str, Any]], as_of: date) -> list[dict]:
    grouped: dict[tuple, list[dict]] = {}
    for source in observations:
        item = dict(source)
        start, end, available = (_date(item.get(key)) for key in ("period_start", "period_end", "available_at"))
        if not start or not end or not available or available > as_of or end > as_of or end < start or number(item.get("value")) is None:
            continue
        if item.get("basis") != "GAAP diluted" or not re.fullmatch(r"[A-Z]{3}", str(item.get("currency") or "")) or not item.get("source_refs"):
            continue
        item.update(duration_days=(end - start).days + 1)
        grouped.setdefault((start, end, item["currency"]), []).append(item)
    selected = []
    for rows in grouped.values():
        latest = max(str(row["available_at"]) for row in rows)
        rows = [row for row in rows if str(row["available_at"]) == latest]
        if len({number(row["value"]) for row in rows}) == 1:
            selected.append(rows[0])
    return selected


def _quarter_rows(rows: Sequence[dict], start: date, end: date) -> dict[int, dict]:
    grouped: dict[int, list[dict]] = {}
    for row in rows:
        begin, finish = _date(row["period_start"]), _date(row["period_end"])
        if not begin or not finish or begin < start or finish > end or not 60 <= row["duration_days"] <= 140:
            continue
        elapsed = (finish - start).days + 1
        # Supports calendar quarters and common 12/12/12/16-week retail years.
        quarter = 1 if elapsed <= 140 else 2 if elapsed <= 230 else 3 if elapsed <= 315 else 4
        grouped.setdefault(quarter, []).append(row)
    result = {}
    for quarter, matches in grouped.items():
        if len({(row["period_start"], row["period_end"], number(row["value"])) for row in matches}) == 1:
            result[quarter] = matches[0]
    # A YTD row must never be accepted as a late standalone quarter, even when
    # a shortened reporting year makes its duration look quarter-like.
    for quarter, row in list(result.items()):
        if quarter > 1 and _date(row["period_start"]) == start:
            result.pop(quarter)
    return result


def build_earnings_bridge(
    observations: Sequence[Mapping[str, Any]], *, fiscal_period: str,
    period_end: str, as_of: str, split_events: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Show the event's fiscal year, and project only its unreported quarters.

    Prior-year seasonality times the growth in comparable reported quarters is
    a mechanical forecast, never company guidance. Once Q4/year-end has passed,
    missing historical quarters stay explicit acquisition gaps, not forecasts.
    """
    observed = _date(as_of)
    event_end = _date(period_end)
    year_match = re.search(r"(?:FY\s*)?(20\d{2})", fiscal_period, re.I)
    quarter_match = re.search(r"Q([1-4])", fiscal_period, re.I)
    year = year_match.group(1) if year_match else ""
    result: dict[str, Any] = {
        "version": VERSION, "fiscal_year": f"FY{year}" if year else fiscal_period,
        "currency": None, "unit": "per share", "quarters": [],
        "reported_total": None, "projected_total": None, "full_year_total": None,
        "annual_reported_value": None, "source_refs": [], "status": "unavailable",
        "method": "Reported standalone diluted EPS; estimates use prior-year quarterly seasonality and growth in comparable reported quarters.",
        "coverage_note": "", "gaps": [],
    }
    if not observed or not event_end or not year:
        result["gaps"] = ["A dated fiscal earnings event is required to assign reported quarters."]
        return result
    rows = _eligible(observations, observed)
    annuals = sorted((row for row in rows if 350 <= row["duration_days"] <= 378), key=lambda row: row["period_end"])
    annual = next((row for row in reversed(annuals) if row["period_end"] == event_end.isoformat()), None)
    prior = next((row for row in reversed(annuals) if _date(row["period_end"]) < event_end and (event_end - _date(row["period_end"])).days < 390), None)
    current_q = int(quarter_match.group(1)) if quarter_match else (4 if annual else None)
    if annual:
        start, year_end = _date(annual["period_start"]), event_end
    elif prior:
        start = _date(prior["period_end"]) + timedelta(days=1)
        # Only projected quarter dates are approximate; actual boundaries are
        # copied verbatim. A 53rd week requires an explicit issuer calendar.
        year_end = start + timedelta(days=prior["duration_days"] - 1)
    else:
        result["gaps"] = ["A reported prior annual period is required to establish the fiscal calendar."]
        return result
    currency = (annual or prior)["currency"]
    rows = [row for row in rows if row["currency"] == currency]
    result.update(currency=currency, unit=f"{currency}/share", period_start=start.isoformat(), period_end=year_end.isoformat())
    actual = _quarter_rows(rows, start, max(year_end, event_end))
    prior_quarters = _quarter_rows(rows, _date(prior["period_start"]), _date(prior["period_end"])) if prior else {}

    def unresolved_events(row: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        available = _date(row.get("available_at"))
        return [event for event in split_events
                if available and (effective := _date(event.get("date") or event.get("effective_date")))
                and available <= effective <= observed]

    affected_actual = {q: unresolved_events(row) for q, row in actual.items() if unresolved_events(row)}
    affected_prior = {q for q, row in prior_quarters.items() if unresolved_events(row)}
    annual_unresolved = bool(annual and unresolved_events(annual))
    # If a full year has reported, a missing fourth standalone EPS is an
    # acquisition gap. Annual EPS minus Q1-Q3 is not reported fourth-quarter EPS.
    latest_reported_q = max(actual, default=0)
    effective_q = max(current_q or 0, latest_reported_q)
    completed = bool(annual or effective_q == 4)
    if completed:
        affected_prior = set()  # Prior-year EPS is not an operand after the year has reported.
    comparable = [q for q in range(1, effective_q + 1) if q in actual and q in prior_quarters]
    prior_sum = sum((number(prior_quarters[q]["value"]) for q in comparable), Decimal(0))
    actual_sum = sum((number(actual[q]["value"]) for q in comparable), Decimal(0))
    growth = actual_sum / prior_sum - 1 if comparable == list(range(1, effective_q + 1)) and prior_sum > 0 and actual_sum > 0 and not affected_actual and not set(comparable).intersection(affected_prior) else None
    if prior and not unresolved_events(prior) and 4 not in prior_quarters and all(q in prior_quarters and q not in affected_prior for q in (1, 2, 3)):
        # This residual is an assumption for the forecast, never a backfilled
        # reported quarter. Annual and quarterly diluted share counts differ.
        residual = number(prior["value"]) - sum((number(prior_quarters[q]["value"]) for q in (1, 2, 3)), Decimal(0))
        prior_quarters[4] = {
            "value": display(residual), "period_end": prior["period_end"],
            "source_refs": list(dict.fromkeys([*prior["source_refs"], *[sid for q in (1, 2, 3) for sid in prior_quarters[q]["source_refs"]]])),
            "seasonal_proxy": True,
        }
    refs: list[str] = []
    for quarter in range(1, 5):
        point: dict[str, Any] = {"period": f"FY{year} Q{quarter}", "kind": "missing", "value": None, "source_refs": [], "rationale": "A standalone reported quarter was not found in the retained primary sources.", "period_start": None, "period_end": None}
        if quarter in actual:
            row = actual[quarter]
            point.update(kind="reported", value=display(number(row["value"])), source_refs=list(row["source_refs"]), rationale="Reported standalone GAAP diluted EPS.", period_start=row["period_start"], period_end=row["period_end"], locator=row.get("locator"), available_at=row["available_at"])
            if quarter in affected_actual:
                point.update(share_basis_status="unreconciled", share_basis_events=[dict(event) for event in affected_actual[quarter]], rationale="Reported standalone GAAP diluted EPS on its original share basis. A later split, stock dividend or conspicuous price discontinuity requires reconciliation; this number is retained as reported but excluded from totals and forecast growth.")
                refs.extend(sid for event in affected_actual[quarter] for sid in event.get("source_refs", []))
                result["gaps"].append(f"{point['period']}: reported EPS share basis has not been reconciled with subsequent corporate actions.")
        elif not completed and quarter > effective_q and growth is not None and quarter in prior_quarters and quarter not in affected_prior:
            previous = prior_quarters[quarter]
            estimate = number(previous["value"]) * (1 + growth)
            point.update(kind="projection", value=display(estimate), source_refs=list(dict.fromkeys([*previous["source_refs"], *[sid for q in comparable for r in (actual[q], prior_quarters[q]) for sid in r["source_refs"]]])), rationale=f"Analyst model, not company guidance: prior-year Q{quarter} EPS {previous['value']} × (1 + {display(growth)} comparable-quarter growth).", formula="prior-year standalone quarter EPS × (1 + comparable reported-quarter EPS growth)", growth_rate=display(growth), prior_year_value=previous["value"], prior_year_period_end=previous["period_end"])
            if previous.get("seasonal_proxy"):
                point.update(formula="(prior annual EPS − sum of prior reported Q1–Q3 EPS) × (1 + comparable reported-quarter EPS growth)", rationale=f"Analyst model, not company guidance: prior-year seasonal residual {previous['value']} × (1 + {display(growth)} comparable-quarter growth). The residual is annual EPS minus reported Q1–Q3 EPS; it approximates Q4 seasonality and is not reported Q4 EPS because diluted share counts and rounding differ.", prior_year_kind="modeled_seasonal_residual")
        if point["kind"] == "missing":
            if not completed and quarter > effective_q and (affected_actual or affected_prior):
                point["rationale"] = "Projection withheld until the current and prior-year quarterly EPS share bases are reconciled after corporate actions."
            result["gaps"].append(f"{point['period']}: " + ("reported quarter missing from retained sources" if completed or quarter <= effective_q else point["rationale"] if affected_actual or affected_prior else "insufficient comparable quarters for a defensible seasonal estimate"))
        result["quarters"].append(point)
        refs.extend(point["source_refs"])
    reported = [point for point in result["quarters"] if point["kind"] == "reported"]
    projected = [point for point in result["quarters"] if point["kind"] == "projection"]
    result["reported_total"] = display(sum((number(point["value"]) for point in reported), Decimal(0))) if reported and not affected_actual else None
    result["projected_total"] = display(sum((number(point["value"]) for point in projected), Decimal(0))) if projected else None
    if annual:
        result["annual_reported_value"] = display(number(annual["value"]))
        if not annual_unresolved:
            result["full_year_total"] = result["annual_reported_value"]
        else:
            result["annual_share_basis_status"] = "unreconciled"
            result["gaps"].append("Reported annual EPS precedes an unreconciled corporate action; it is retained as originally reported but is not a current-basis full-year total.")
        refs.extend(annual["source_refs"])
    elif len(reported) + len(projected) == 4 and not affected_actual:
        result["full_year_total"] = display(sum((number(point["value"]) for point in result["quarters"]), Decimal(0)))
    result.update(source_refs=list(dict.fromkeys(refs)), status="complete" if not result["gaps"] else "partial", reported_quarters=len(reported), projected_quarters=len(projected))
    result["coverage_note"] = "Quarterly diluted EPS can differ in sum from annual EPS because of rounding and different diluted share counts. " + ("The full-year total uses the reported annual figure." if annual and not annual_unresolved else "The original annual EPS is retained but its share basis needs reconciliation." if annual_unresolved else "The full-year estimate combines reported standalone quarters with separately labeled seasonal estimates; it is not company guidance.")
    if affected_actual or affected_prior or annual_unresolved:
        result["share_basis_status"] = "unreconciled"
        result["coverage_note"] += " Original reported EPS remains visible, but figures on unreconciled share bases are not summed or used to forecast missing quarters. A separately reported annual value is shown as the full-year total only if it follows the relevant corporate actions."
    return result
