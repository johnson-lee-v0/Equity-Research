"""Source-bound, point-in-time P/E history and a reported/forecast EPS bridge.

This module performs no network requests or writes. Acquisition archives the
SEC company-concept and raw daily market bars before this compiler runs. A
historical observation only sees financial records published before its price
date; later comparative restatements cannot leak into an earlier multiple.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
import hashlib
import json
import re
from statistics import median
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

from .earnings_forecast import build_earnings_bridge, display, number


VERSION = "valuation-research-context.v1"


def multiple_statistics(values: Sequence[Any]) -> dict[str, Any]:
    """Population dispersion of retained samples, not a forecast interval."""
    valid = [value for raw in values if (value := number(raw)) is not None and value > 0]
    if not valid:
        return {"mean": None, "standard_deviation": None, "bands": [], "sample_count": 0}
    mean = sum(valid) / len(valid)
    deviation = (sum((value - mean) ** 2 for value in valid) / len(valid)).sqrt() if len(valid) >= 2 else None
    return {"mean": display(mean), "standard_deviation": display(deviation) if deviation is not None else None,
            "standard_deviation_basis": "population; equally weighted retained monthly observations",
            "bands": [{"sigma": sigma, "value": display(mean + deviation * sigma)} for sigma in (-3, -2, -1, 0, 1, 2, 3)] if deviation is not None else [],
            "sample_count": len(valid)}


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _source_content(source: Mapping[str, Any]) -> str | None:
    content = source.get("content", source.get("original_content"))
    expected = source.get("content_hash")
    if not isinstance(content, str) or not expected or hashlib.sha256(content.encode()).hexdigest() != expected:
        return None
    return content


def sec_eps_history(source: Mapping[str, Any], *, ticker: str, cik: str, as_of: str) -> list[dict[str, Any]]:
    """Retain all dated EPS vintages, not just the latest restated number.

    SEC ``fy`` and ``fp`` describe the filing, not necessarily a comparative
    observation. Fiscal placement therefore uses actual start/end dates.
    Filing-date-only records become eligible the following day, conservatively
    avoiding a same-day market close before an after-hours filing.
    """
    content = _source_content(source)
    cutoff = _date(as_of)
    if not content or not cutoff or not source.get("id"):
        return []
    try:
        data = json.loads(content)
        identity = int(cik)
        url = urlsplit(str(source.get("url") or source.get("source_url") or ""))
    except (TypeError, ValueError):
        return []
    if (not isinstance(data, dict) or data.get("cik") != identity or data.get("taxonomy") != "us-gaap"
            or data.get("tag") != "EarningsPerShareDiluted" or url.scheme != "https" or url.hostname != "data.sec.gov"
            or url.path != f"/api/xbrl/companyconcept/CIK{identity:010d}/us-gaap/EarningsPerShareDiluted.json"):
        return []
    quotations = {}
    for match in re.finditer(r"\{[^{}]*\}", content):
        try:
            record = json.loads(match[0])
        except ValueError:
            continue
        if isinstance(record, dict) and "accn" in record:
            quotations[json.dumps(record, sort_keys=True)] = (match[0], content.count("\n", 0, match.start()) + 1)
    result = []
    for unit, rows in (data.get("units") or {}).items():
        if not re.fullmatch(r"[A-Z]{3}/shares", unit) or not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict) or row.get("form") not in {"10-K", "10-K/A", "10-Q", "10-Q/A", "8-K"} or not re.fullmatch(r"\d{10}-\d{2}-\d{6}", str(row.get("accn") or "")):
                continue
            start, end, filed = (_date(row.get(key)) for key in ("start", "end", "filed"))
            value = number(row.get("val"))
            quote = quotations.get(json.dumps(row, sort_keys=True))
            if not start or not end or not filed or value is None or not quote or filed < end or filed > cutoff or not 60 <= (end - start).days + 1 <= 378:
                continue
            result.append({
                "issuer": ticker.upper(), "metric": "eps", "basis": "GAAP diluted", "value": display(value),
                "unit": unit.replace("/shares", "/share"), "currency": unit[:3],
                "period_start": start.isoformat(), "period_end": end.isoformat(), "duration_days": (end - start).days + 1,
                "filed_at": filed.isoformat(), "available_at": (filed + timedelta(days=1)).isoformat(),
                "source_refs": [source["id"]], "source_quote": quote[0], "locator": f"L{quote[1]}",
                "source_version": source.get("version"), "content_hash": source["content_hash"],
                "accession": row["accn"], "form": row["form"],
                "source_url": f"https://www.sec.gov/Archives/edgar/data/{identity}/{row['accn'].replace('-', '')}/",
            })
    return result


def _latest_periods(observations: Sequence[Mapping[str, Any]], point_date: date, currency: str) -> list[dict]:
    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for row in observations:
        available, end = _date(row.get("available_at")), _date(row.get("period_end"))
        if row.get("currency") != currency or row.get("basis") != "GAAP diluted" or not available or not end or available > point_date or end >= point_date or number(row.get("value")) is None:
            continue
        grouped.setdefault((str(row.get("period_start")), str(row.get("period_end"))), []).append(row)
    result = []
    for rows in grouped.values():
        latest = max(str(row["available_at"]) for row in rows)
        candidates = [row for row in rows if str(row["available_at"]) == latest]
        if len({number(row["value"]) for row in candidates}) == 1:
            result.append(dict(candidates[0]))
    return result


def trailing_eps_as_of(observations: Sequence[Mapping[str, Any]], *, point_date: str, currency: str = "USD", split_events: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    """Annual + current YTD − comparable prior YTD, using available vintages.

    This is a conventional rolling EPS bridge rather than an assertion that
    weighted-average diluted shares are identical between annual/YTD periods.
    Components and that limitation are surfaced to the reader.
    """
    priced = _date(point_date)
    if not priced:
        return {"status": "unavailable", "reason": "Invalid price date."}
    rows = _latest_periods(observations, priced, currency)
    annuals = [row for row in rows if 350 <= (row.get("duration_days") or ((_date(row["period_end"]) - _date(row["period_start"])).days + 1)) <= 378]
    if not annuals:
        return {"status": "unavailable", "reason": "No annual diluted EPS was available before this price date."}
    annual = max(annuals, key=lambda row: row["period_end"])
    annual_end = _date(annual["period_end"])
    current = [row for row in rows if _date(row["period_start"]) == annual_end + timedelta(days=1) and _date(row["period_end"]) > annual_end and 60 <= ((_date(row["period_end"]) - _date(row["period_start"])).days + 1) <= 315]
    components = [annual]
    signs = [1]
    end = annual_end
    formula = "most recently reported annual diluted EPS"
    value = number(annual["value"])
    if current:
        ytd = max(current, key=lambda row: row["period_end"])
        days = (_date(ytd["period_end"]) - _date(ytd["period_start"])).days + 1
        comparisons = [row for row in rows if row["period_start"] == annual["period_start"] and 350 <= (_date(ytd["period_end"]) - _date(row["period_end"])).days <= 378 and abs(((_date(row["period_end"]) - _date(row["period_start"])).days + 1) - days) <= 8]
        if len(comparisons) != 1:
            return {"status": "unavailable", "reason": "Comparable prior-year YTD EPS is missing or ambiguous."}
        previous = comparisons[0]
        components += [ytd, previous]
        signs += [1, -1]
        end = _date(ytd["period_end"])
        value = value + number(ytd["value"]) - number(previous["value"])
        formula = "last annual diluted EPS + current YTD diluted EPS − comparable prior YTD diluted EPS"
    # Old annuals cannot silently represent current TTM when new reports are
    # absent. Quarterly reporting allows a bounded lag after the period end.
    if (priced - end).days > 190:
        return {"status": "unavailable", "reason": "The latest reported EPS period is more than 190 days before the price date."}
    for split in split_events:
        effective = _date(split.get("date") or split.get("effective_date"))
        if effective and effective <= priced and any(_date(row["available_at"]) <= effective for row in components):
            reason = "A conspicuous raw-price discontinuity requires share-basis review; no split ratio was inferred." if split.get("type") == "unexplained_price_discontinuity" else "A split or stock dividend occurred after an EPS component was published; the share bases have not been reconciled."
            return {"status": "unavailable", "reason": reason}
    if value <= 0:
        return {"status": "unavailable", "reason": "P/E is not meaningful when trailing diluted EPS is zero or negative."}
    return {"status": "available", "value": display(value), "currency": currency, "period_end": end.isoformat(),
            "available_as_of": max(row["available_at"] for row in components), "formula": formula,
            "source_refs": list(dict.fromkeys(sid for row in components for sid in row["source_refs"])),
            "components": [{key: row.get(key) for key in ("value", "period_start", "period_end", "available_at", "source_refs", "locator", "accession", "source_url")} | {"sign": sign} for row, sign in zip(components, signs)]}


def raw_price_history(sources: Sequence[Mapping[str, Any]], *, ticker: str, as_of: str) -> tuple[list[dict], list[str]]:
    """Accept only verified raw daily provider archives for the same symbol."""
    cutoff = _date(as_of)
    by_date: dict[str, list[dict]] = {}
    notes: list[str] = []
    for source in sources:
        content = _source_content(source)
        if not content or str(source.get("url") or "") != "https://data.alpaca.markets/v2/stocks/bars":
            continue
        lines = content.splitlines()
        if len(lines) < 3 or lines[0] != "Road2M canonical research source":
            continue
        try:
            head = json.loads(lines[1])
        except ValueError:
            continue
        meta = head.get("metadata") or {}
        if head.get("provider") != "alpaca" or head.get("source_type") != "market_bars" or meta.get("timeframe") != "1Day":
            continue
        if meta.get("adjustment") != "raw":
            continue
        if meta.get("currency") != "USD":
            notes.append("Market price currency was not established as USD.")
            continue
        symbols = meta.get("symbols") or meta.get("requested_symbols") or []
        if isinstance(symbols, str):
            symbols = [symbols]
        if ticker not in symbols:
            notes.append("The price request did not bind the requested symbol.")
            continue
        for index, raw in enumerate(lines[2:], start=3):
            try:
                row = json.loads(raw)
            except ValueError:
                continue
            close, day = number(row.get("close")), _date(row.get("timestamp"))
            if row.get("symbol") != ticker or row.get("complete") is not True or close is None or close <= 0 or not day or not cutoff or day > cutoff:
                continue
            by_date.setdefault(day.isoformat(), []).append({"date": day.isoformat(), "close": display(close), "currency": "USD", "source_refs": [source["id"]], "locator": f"L{index}", "feed": meta.get("feed"), "retrieved_at": meta.get("retrieved_at")})
    selected = []
    for day, rows in by_date.items():
        if len({row["close"] for row in rows}) == 1:
            row = rows[0]
            row["source_refs"] = list(dict.fromkeys(sid for item in rows for sid in item["source_refs"]))
            selected.append(row)
        else:
            notes.append(f"Conflicting raw closing prices for {day} were excluded.")
    return sorted(selected, key=lambda row: row["date"]), list(dict.fromkeys(notes))


def build_historical_pe(observations: Sequence[Mapping[str, Any]], prices: Sequence[Mapping[str, Any]], *, as_of: str, years: int = 5, split_events: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    cutoff = _date(as_of)
    if not cutoff:
        raise ValueError("A valid as-of date is required")
    start = cutoff - timedelta(days=round(365.25 * max(1, min(years, 10))))
    monthly: dict[str, Mapping[str, Any]] = {}
    for row in sorted(prices, key=lambda row: str(row.get("date"))):
        day = _date(row.get("date"))
        if day and start <= day <= cutoff and number(row.get("close")) is not None and number(row["close"]) > 0:
            monthly[day.isoformat()[:7]] = row
    points, gaps = [], []
    for row in monthly.values():
        eps = trailing_eps_as_of(observations, point_date=row["date"], currency=str(row.get("currency") or "USD"), split_events=split_events)
        if eps["status"] != "available":
            gaps.append({"date": row["date"], "reason": eps["reason"]})
            continue
        points.append({"date": row["date"], "close": display(number(row["close"])), "ttm_eps": eps["value"], "pe": display(number(row["close"]) / number(eps["value"])), "source_refs": list(dict.fromkeys([*row.get("source_refs", []), *eps["source_refs"]])), "earnings_period_end": eps["period_end"], "available_as_of": eps["available_as_of"], "price_locator": row.get("locator"), "feed": row.get("feed"), "eps_formula": eps["formula"], "eps_components": eps["components"]})
    values = [number(row["pe"]) for row in points]
    note = "Monthly sampled raw daily closes from the stated market feed divided by GAAP diluted EPS available before each price date; the latest month uses its latest complete session. An IEX feed close is venue-specific, not a consolidated US-market closing auction. This is trailing P/E, not forward P/E. Filing dates without times are used from the following day. Annual + current YTD − prior YTD is a rolling approximation because diluted share weights can differ."
    note += " Raw prices avoid dividend and future split adjustments; any known split between an EPS publication and price date is excluded. Split-event coverage must be reviewed for issuers with corporate actions."
    if points and (_date(points[0]["date"]) - start).days > 45:
        note += " Less than the requested five years is available; the range describes retained samples only."
    return {"version": VERSION, "metric": "P/E", "basis": "Reported-as-of trailing GAAP diluted EPS", "currency": "USD", "points": points, "min": display(min(values)) if values else None, "median": display(median(values)) if values else None, "max": display(max(values)) if values else None, **multiple_statistics(values), "requested_years": years, "window_start": start.isoformat(), "window_end": cutoff.isoformat(), "status": "available" if points else "unavailable", "coverage_note": note, "gaps": gaps, "source_refs": list(dict.fromkeys(sid for row in points for sid in row["source_refs"]))}


def build_valuation_research_context(sources: Sequence[Mapping[str, Any]], *, ticker: str, cik: str, as_of: str, fiscal_period: str, period_end: str, extra_observations: Sequence[Mapping[str, Any]] = (), split_events: Sequence[Mapping[str, Any]] = (), share_class_count: int = 1) -> dict[str, Any]:
    """Compile retained primary financial records and five-year market history.

    ``extra_observations`` are independently bound issuer-release EPS records;
    the caller supplies their source IDs and publication/availability dates.
    They supplement SEC data while the corresponding 10-K/10-Q is pending.
    """
    observations = [row for source in sources for row in sec_eps_history(source, ticker=ticker, cik=cik, as_of=as_of)]
    bound_sources = {str(source.get("id")): source for source in sources if _source_content(source)}
    for raw in extra_observations:
        row = dict(raw)
        refs = row.get("source_refs") or ([row.get("source_ref") or row.get("source_id")] if row.get("source_ref") or row.get("source_id") else [])
        if not refs or any(sid not in bound_sources for sid in refs) or row.get("issuer", row.get("subject", ticker)).upper() != ticker.upper() or row.get("basis") != "GAAP diluted" or not row.get("currency"):
            continue
        available = _date(row.get("available_at"))
        if not available:
            published = _date(row.get("published_at") or row.get("publication_at") or bound_sources[refs[0]].get("publication_at"))
            available = published + timedelta(days=1) if published else None
        if not available or not _date(row.get("period_start")) or not _date(row.get("period_end")):
            continue
        row.update(source_refs=list(refs), available_at=available.isoformat(), duration_days=(_date(row["period_end"]) - _date(row["period_start"])).days + 1)
        observations.append(row)
    from .valuation_corporate_actions import split_coverage_from_sources

    prices, notes = raw_price_history(sources, ticker=ticker, as_of=as_of)
    cutoff = _date(as_of)
    if not cutoff:
        raise ValueError("A valid as-of date is required")
    coverage = split_coverage_from_sources(sources, ticker=ticker,
        start=(cutoff - timedelta(days=7 * 366)).isoformat(), end=cutoff.isoformat())
    actions = [*coverage["events"], *split_events]
    action_dates = {str(row.get("date")) for row in actions}
    discontinuities = []
    for previous, current in zip(prices, prices[1:]):
        elapsed = (_date(current["date"]) - _date(previous["date"])).days
        ratio = number(current["close"]) / number(previous["close"])
        if 0 < elapsed <= 10 and (ratio < Decimal("0.60") or ratio > Decimal("1.40")) and current["date"] not in action_dates:
            discontinuities.append({"type": "unexplained_price_discontinuity", "date": current["date"],
                "previous_date": previous["date"], "previous_close": previous["close"], "close": current["close"],
                "source_refs": list(dict.fromkeys([*previous["source_refs"], *current["source_refs"]]))})
    actions.extend(discontinuities)
    history = build_historical_pe(observations, prices, as_of=as_of, split_events=actions)
    from .comparable_history import build_comparable_histories
    comparable_histories = build_comparable_histories(list(sources), prices, ticker=ticker, cik=cik, as_of=as_of, split_events=actions)
    if share_class_count != 1:
        comparable_histories = {label: {**series, "points": [], "status": "unavailable", **multiple_statistics([]),
            "min": None, "median": None, "max": None, "gaps": [{"reason": "Multiple listed share classes require a class-specific capitalization/conversion bridge; total common shares cannot be multiplied by one class's quote."}]}
            for label, series in comparable_histories.items()}
    from .comparable_peers import compile_peer_comparisons
    peer_comparisons = compile_peer_comparisons(sources, ticker=ticker, as_of=as_of)
    from .secondary_valuation_history import compile_provider_histories
    provider_histories = compile_provider_histories(list(sources), ticker=ticker, as_of=as_of)
    history["split_coverage"] = coverage
    history["share_basis_review_events"] = discontinuities
    if coverage["coverage_complete"]:
        history["coverage_note"] += f" Retained provider coverage includes {len(coverage['events'])} split or stock-dividend events over the requested lookback; provider reporting can be delayed."
    else:
        history["coverage_note"] += " Split-event coverage is unverified or incomplete. Raw close changes exceeding 40% between nearby sessions are treated as review gaps, not automatically corrected or called splits."
    history["gaps"].extend({"reason": note} for note in notes)
    if not prices:
        history["gaps"].append({"reason": "No retained, completed raw daily price history for this issuer; split-adjusted or unrelated series are not substituted."})
    bridge = build_earnings_bridge(observations, fiscal_period=fiscal_period, period_end=period_end, as_of=as_of, split_events=actions)
    current = trailing_eps_as_of(observations, point_date=as_of, currency=bridge.get("currency") or "USD", split_events=actions)
    used = set(history["source_refs"]) | set(bridge["source_refs"]) | set(current.get("source_refs") or []) | set(coverage["source_refs"])
    used.update(sid for series in comparable_histories.values() for point in series["points"] for sid in point["source_refs"])
    used.update(sid for peer in peer_comparisons for sid in peer["source_refs"])
    used.update(sid for series in provider_histories.values() for sid in series["source_refs"])
    limitations = ([current["reason"]] if current.get("status") == "unavailable" and any(word in str(current.get("reason")) for word in ("split", "share-basis", "stock dividend")) else [])
    if bridge.get("share_basis_status") == "unreconciled":
        limitations.append("Some historical EPS figures use an unreconciled share basis. Reported rows remain visible, but mixed-basis totals and forecasts are withheld; a future target must independently establish its baseline share basis.")
    return {"version": VERSION, "as_of": as_of, "ticker": ticker, "historical_pe": history, "historical_multiples": comparable_histories, "provider_multiples": provider_histories, "peer_comparisons": peer_comparisons, "earnings_bridge": bridge, "current_earnings": current, "share_basis_limitations": limitations, "sources": [{"id": sid, "source_id": sid, "source_ref": sid, "title": bound_sources[sid].get("title"), "url": bound_sources[sid].get("url"), "content_hash": bound_sources[sid].get("content_hash"), "version": bound_sources[sid].get("version")} for sid in sorted(used) if sid in bound_sources]}
