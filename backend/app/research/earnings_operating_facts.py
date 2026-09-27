"""Bind a small set of current-quarter operating observations to raw earnings text.

The trend register selects candidates, but never certifies them. Both seeding and
claim validation reconstruct the issuer, fiscal quarter, reporting date and the
specific percentage clause from the retained source. Unrecognized wording is
left as cited context rather than promoted through a general numeric matcher.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Mapping


VERSION = "earnings-operating-facts.v1"
OPERATING_BASES = {
    "net_sales_growth": "Reported quarterly net sales growth year over year",
    "paid_members_growth": "Total paid membership growth year over year; excludes cardholder growth",
    "renewal_us_canada": "Reported quarter-end membership renewal rate; US and Canada",
    "renewal_worldwide": "Reported quarter-end membership renewal rate; worldwide",
    "gross_margin": "Reported quarterly gross margin rate; excludes adjusted margins and basis-point changes",
}
_QUARTERS = {"first": 1, "second": 2, "third": 3, "fourth": 4}
_QWORDS = "first|second|third|fourth"
_MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
_PERCENT = r"(?P<value>\d+(?:\.\d+)?)\s*(?:%|percent\b)"
_STATEMENT = "reported current-quarter operating results"


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def _period(value: Any) -> str | None:
    parts = str(value or "").upper().split("/")
    match = re.fullmatch(r"\s*Q([1-4])\s*(?:FY)?\s*(20\d{2})\s*", parts[0])
    if not match or any(not re.fullmatch(rf"\s*(?:FY)?\s*{match[2]}\s*", part) for part in parts[1:]):
        return None
    return f"Q{match[1]} FY{match[2]}"


def _reporting_context(content: str) -> dict[str, Any] | None:
    # Accept only an explicit earnings-call header or results-release title.
    # Provider metadata and a market quote's date cannot establish the period.
    header = content[:18000]
    call = re.search(r"(?im)^Earnings Call:\s*Q([1-4])\s+(20\d{2})[ \t]*\n([A-Za-z]{3} \d{1,2}, 20\d{2})[ \t]*$", header)
    release = re.search(rf"(?im)^[^\n]*Reports\s+({_QWORDS})\s+Quarter(?:\s+and)?\s+(?:Fiscal Year|Fiscal)\s+(20\d{{2}})\s+Operating Results[ \t]*\n(\d{{2}}/\d{{2}}/20\d{{2}})[ \t]*$", header)
    if bool(call) == bool(release):
        return None
    match = call or release
    quarter = int(match[1]) if call else _QUARTERS[match[1].lower()]
    year = int(match[2])
    try:
        published = datetime.strptime(match[3].strip(), "%b %d, %Y" if call else "%m/%d/%Y").date()
    except ValueError:
        return None
    intros = list(re.finditer(r"operating results for\s+([^\n]{0,260}?\bended\s+" + rf"(?:{_MONTHS})\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+20\d{{2}})?)", header, re.I))
    if len(intros) != 1 or intros[0].start() <= match.start():
        return None
    intro = intros[0]
    issuers = set(re.findall(r"\b(?:NASDAQ|NYSE)(?:\s+Global Select Market)?\s*:\s*([A-Z][A-Z0-9.-]{0,14})\b", header[:intro.start()], re.I))
    if len(issuers) != 1:
        return None
    quarter_match = re.search(rf"\b({_QWORDS})\s+quarter\b", intro[1], re.I)
    fiscal_year = re.search(r"fiscal year\s+(20\d{2})", intro[1], re.I)
    if not quarter_match or _QUARTERS[quarter_match[1].lower()] != quarter or (fiscal_year and int(fiscal_year[1]) != year):
        return None
    ended = re.search(rf"ended\s+({_MONTHS})\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(20\d{{2}}))?", intro[1], re.I)
    try:
        month = datetime.strptime(ended[1].title(), "%B").month
        possible = [date(int(ended[3]), month, int(ended[2]))] if ended[3] else [date(published.year - offset, month, int(ended[2])) for offset in (0, 1)]
    except ValueError:
        return None
    dates = [item for item in possible if 0 <= (published - item).days <= 150]
    if len(dates) != 1:
        return None
    return {"issuer": next(iter(issuers)).upper(), "period": f"Q{quarter} FY{year}",
            "period_end": dates[0].isoformat(), "quarter": quarter, "published_at": published.isoformat(),
            "header_offset": match.start(), "intro_offset": intro.start(), "intro_end": intro.end(),
            "period_date_basis": "explicit date" if ended[3] else "month/day in reporting statement, nearest past occurrence within 150 days of explicit call date"}


def _percentage(metric: str, quote: str, quarter: int) -> Decimal | None:
    if len(quote) > 700 or re.search(r"\b(?:expect|forecast|guidance|project|would|could|will|plan)\b", quote, re.I):
        return None
    if metric == "net_sales_growth":
        pattern = r"^Net sales for the quarter increased\s+" + _PERCENT + r",[^\n]*\blast year\.\s*$"
    elif metric == "paid_members_growth":
        pattern = r"^We ended the quarter with\s+\d+(?:\.\d+)?\s+million total paid members, up\s+" + _PERCENT + r"\s+versus last year\b"
    elif metric in {"renewal_us_canada", "renewal_worldwide"}:
        if not re.search(rf"^In terms of renewal rates at Q{quarter} end,", quote, re.I):
            return None
        pattern = (r"\bour U\.S\. and Canada renewal rate was\s+" if metric == "renewal_us_canada" else r"\bthe worldwide rate came in at\s+") + _PERCENT
    else:
        pattern = r"^Our reported gross margin rate was (?:lower|higher) year-over-year by\s+\d+(?:\.\d+)?\s+basis points, coming in at\s+" + _PERCENT + r"\s+compared to\s+\d+(?:\.\d+)?\s*(?:%|percent)\s+last year\.\s*$"
    matches = list(re.finditer(pattern, quote, re.I))
    if len(matches) != 1:
        return None
    value = _decimal(matches[0]["value"])
    return value if value is not None and 0 <= value <= 100 else None


def _observation(metric: str, quote: str, content: str) -> dict[str, Any] | None:
    if metric not in OPERATING_BASES or not quote or content.count(quote) != 1:
        return None
    context = _reporting_context(content)
    if context is None:
        return None
    offset = content.find(quote)
    # The implicit quarterly scope ends when prepared remarks turn to annual
    # results or questions. An analyst repeating a number is not this parser's
    # issuer assertion; it remains available as ordinary cited context.
    intervening = content[context["intro_end"]:offset]
    if offset <= context["intro_end"] or re.search(r"(?im)^Analyst(?:\s*,|\s*$)|\bfor the (?:full fiscal|full|fiscal) year\b", intervening):
        return None
    value = _percentage(metric, quote, context["quarter"])
    if value is None:
        return None
    first = content.count("\n", 0, offset) + 1
    last = content.count("\n", 0, offset + len(quote) - 1) + 1
    return {"subject": context["issuer"], "issuer": context["issuer"], "metric": metric,
            "value": format(value, "f"), "unit": "percent", "currency": None, "scale": None,
            "period": context["period"], "period_start": None, "period_end": context["period_end"],
            "basis": OPERATING_BASES[metric], "statement_type": _STATEMENT,
            "source_quote": quote, "locator": f"L{first}" if first == last else f"L{first}-L{last}",
            "proof": {"parser": VERSION, "header_line": content.count("\n", 0, context["header_offset"]) + 1,
                      "reporting_statement_line": content.count("\n", 0, context["intro_offset"]) + 1,
                      "value_line": first, "published_at": context["published_at"],
                      "period_date_basis": context["period_date_basis"]}}


def bind_operating_claim(claim: Mapping[str, Any], content: str, metadata: Mapping[str, Any]) -> dict[str, Any] | None:
    """Reconstruct all bounds without trusting claim proof or source metadata."""
    item = _observation(str(claim.get("metric") or ""), str(claim.get("source_quote") or ""), content)
    if item is None or _decimal(claim.get("value")) != _decimal(item["value"]):
        return None
    fields = ("subject", "unit", "currency", "scale", "period", "period_start", "period_end", "basis", "statement_type", "source_quote", "locator")
    if any(str(claim.get(key) or "") != str(item[key] or "") for key in fields):
        return None
    return item


def operating_trend_seeds(earnings_context: dict[str, Any], sources: list[dict[str, Any]], *, ticker: str, event: dict[str, Any]) -> list[dict[str, Any]]:
    """Return raw FactClaim fields; the caller assigns IDs and validates again.

    This does not replace package/hash/version checks in the compiler. It only
    selects current, actual trend candidates whose raw-source proof can be
    reconstructed. Conflicting candidates for one metric are left unresolved.
    """
    period = _period(event.get("fiscal_period"))
    if not period or earnings_context.get("ticker") != ticker:
        return []
    by_id = {source["id"]: source for source in sources}
    candidates: dict[str, list[dict[str, Any]]] = {}
    for series in earnings_context.get("series", []):
        metric = series.get("id")
        if metric not in OPERATING_BASES or series.get("unit") != "percent" or series.get("frequency") != "quarterly":
            continue
        for point in series.get("points", []):
            source = by_id.get(point.get("source_id"))
            if not source or point.get("kind") != "actual" or _period(point.get("period")) != period:
                continue
            item = _observation(metric, str(point.get("quote") or ""), str(source.get("content") or ""))
            if (item is None or item["subject"] != ticker or item["period"] != period
                    or item["period_end"] != event.get("period_end") or item["period_end"] != point.get("period_end")
                    or item["locator"] != point.get("locator") or point.get("source_version") != source.get("version")
                    or _decimal(item["value"]) != _decimal(point.get("value"))):
                continue
            fields = {key: value for key, value in item.items() if key not in {"issuer", "proof"}}
            fields.update(source_ref=source["id"], source_version=str(source["version"]),
                          claim=f"{ticker} reported {metric.replace('_', ' ')} of {item['value']}% for {period}.")
            candidates.setdefault(metric, []).append(fields)
    # Stable order makes the EPS-first compiler's local claim aliases repeatable.
    return [candidates[metric][0] for metric in OPERATING_BASES if len(candidates.get(metric, [])) == 1]
