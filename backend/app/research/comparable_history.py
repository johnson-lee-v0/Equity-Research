"""Own-company reported-as-of multiples from unchanged SEC and price archives."""
from datetime import date, timedelta
from decimal import Decimal
from statistics import median

from .comparable_financials import observations
from .earnings_forecast import display, number
from .valuation_history import _source_content, multiple_statistics


def build_comparable_histories(sources: list[dict], prices: list[dict], *, ticker: str, cik: str, as_of: str, split_events=()) -> dict:
    rows = [row for source in sources if (content := _source_content(source))
            for row in observations(content, source, ticker=ticker, cik=cik, as_of=as_of)]
    end = date.fromisoformat(as_of[:10])
    start = end - timedelta(days=round(365.25 * 5))
    monthly = {row["date"][:7]: row for row in sorted(prices, key=lambda item: item["date"]) if start.isoformat() <= row["date"] <= end.isoformat()}

    def current(metric, day, currency="USD"):
        selected = [row for row in rows if row["metric"] == metric and row["available_at"] <= day and row["period_end"] < day and row.get("currency") == (None if metric == "shares" else currency)]
        groups = {}
        for row in selected:
            groups.setdefault((row["period_start"], row["period_end"]), []).append(row)
        final = []
        for values in groups.values():
            latest = max(row["available_at"] for row in values)
            values = [row for row in values if row["available_at"] == latest]
            if len({row["value"] for row in values}) == 1:
                final.append(values[0])
        return final

    def instant(metric, day):
        available = current(metric, day)
        if not available:
            raise ValueError(f"No reported {metric} available before the price date.")
        row = max(available, key=lambda item: item["period_end"])
        if (date.fromisoformat(day) - date.fromisoformat(row["period_end"])).days > 190:
            raise ValueError(f"The reported {metric} is more than 190 days old.")
        return number(row["value"]), [row]

    def trailing(metric, day):
        available = current(metric, day)
        annuals = [row for row in available if 350 <= row["duration_days"] <= 378]
        if not annuals:
            raise ValueError(f"No annual {metric} available before the price date.")
        annual = max(annuals, key=lambda item: item["period_end"])
        components = [annual]
        value = number(annual["value"])
        next_start = (date.fromisoformat(annual["period_end"]) + timedelta(days=1)).isoformat()
        ytds = [row for row in available if row["period_start"] == next_start and 60 <= row["duration_days"] <= 315]
        period_end = annual["period_end"]
        if ytds:
            ytd = max(ytds, key=lambda item: item["period_end"])
            prior = [row for row in available if row["period_start"] == annual["period_start"] and abs(row["duration_days"] - ytd["duration_days"]) <= 8 and 350 <= (date.fromisoformat(ytd["period_end"]) - date.fromisoformat(row["period_end"])).days <= 378]
            if len(prior) != 1:
                raise ValueError(f"The comparable prior-year YTD {metric} is missing or ambiguous.")
            value += number(ytd["value"]) - number(prior[0]["value"])
            components += [ytd, prior[0] | {"sign": -1}]
            period_end = ytd["period_end"]
        if (date.fromisoformat(day) - date.fromisoformat(period_end)).days > 190:
            raise ValueError(f"The latest {metric} reporting period is more than 190 days old.")
        return value, components

    result = {}
    for label, metric in (("P/S", "revenue"), ("EV/EBITDA", "ebitda"), ("P/book", "book equity")):
        points, gaps = [], []
        for price in monthly.values():
            day = price["date"]
            try:
                if price.get("currency") != "USD":
                    raise ValueError("The market-price currency is not matched to USD financial statements.")
                shares, share_rows = instant("shares", day)
                ebitda_proxy = False
                try:
                    denominator, components = instant(metric, day) if label == "P/book" else trailing(metric, day)
                except ValueError:
                    if label != "EV/EBITDA":
                        raise
                    operating, operating_rows = trailing("operating income", day)
                    depreciation, depreciation_rows = trailing("depreciation and amortization", day)
                    if {(row["period_start"], row["period_end"], row.get("sign", 1)) for row in operating_rows} != {(row["period_start"], row["period_end"], row.get("sign", 1)) for row in depreciation_rows}:
                        raise ValueError("Operating income and D&A periods do not match for an EBITDA proxy.")
                    denominator, components = operating + depreciation, [*operating_rows, *depreciation_rows]
                    ebitda_proxy = True
                all_rows = [*share_rows, *components]
                numerator = number(price["close"]) * shares
                if label == "EV/EBITDA":
                    for key, sign in (("cash", -1), ("debt", 1), ("preferred", 1), ("minority", 1)):
                        value, bridge_rows = instant(key, day)
                        numerator += sign * value
                        all_rows.extend(bridge_rows)
                if numerator <= 0 or denominator <= 0 or shares <= 0:
                    raise ValueError("The multiple requires positive equity/enterprise value and denominator.")
                if any(str(event.get("date") or "") <= day and str(event.get("date") or "") >= row["available_at"] for event in split_events for row in share_rows):
                    raise ValueError("A split or price discontinuity requires reconciliation of the reported share count.")
                points.append({"date": day, "close": price["close"], "multiple": display(numerator / denominator), "denominator_value": display(denominator), "ebitda_basis": "Operating income + cash-flow D&A proxy" if ebitda_proxy else "Reported EBITDA" if label == "EV/EBITDA" else None,
                    "shares": display(shares), "numerator_value": display(numerator), "earnings_period_end": max(row["period_end"] for row in components),
                    "available_as_of": max(row["available_at"] for row in all_rows), "source_refs": list(dict.fromkeys([*price["source_refs"], *[row["source_ref"] for row in all_rows]])),
                    "components": [{key: row.get(key) for key in ("metric", "value", "period_start", "period_end", "available_at", "source_refs", "locator", "sign")} for row in all_rows]})
            except ValueError as exc:
                gaps.append({"date": day, "reason": str(exc)})
        values = [number(point["multiple"]) for point in points]
        result[label] = {"metric": label, "basis": "Reported-as-of book equity; not appraised NAV" if label == "P/book" else f"Reported-as-of trailing {metric}",
            "denominator_name": "Total book equity" if label == "P/book" else f"Trailing {metric}", "currency": "USD", "status": "available" if points else "unavailable", "points": points,
            "min": display(min(values)) if values else None, "max": display(max(values)) if values else None, "median": display(median(values)) if values else None,
            **multiple_statistics(values), "window_start": start.isoformat(), "window_end": end.isoformat(), "requested_years": 5, "gaps": gaps,
            "coverage_note": "Monthly raw closes × shares reported before that date; duration metrics use annual + current YTD − prior YTD. No missing liabilities are assumed zero. Known share-basis conflicts and missing facts create gaps. P/book uses accounting equity and is not an appraised NAV series. EBITDA uses a labelled operating-income + cash-flow D&A proxy where reported EBITDA is absent; it may differ from adjusted EBITDA. Own-company history is separate from peer comparables."}
    return result
