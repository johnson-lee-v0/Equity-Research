"""Strict SEC company-facts operands for sales, enterprise and asset valuation."""
from __future__ import annotations

from bisect import bisect_left
from datetime import date, timedelta
import hashlib
import json
import re
from typing import Any, Mapping
from urllib.parse import urlsplit

from .earnings_forecast import display, number

VERSION = "comparable-financials.v2"
# Prefer consolidated standard tags. No fallback from assets to equity, from
# debt components to total debt, or from operating income to EBITDA.
TAGS = {
    "RevenueFromContractWithCustomerExcludingAssessedTax": ("revenue", "GAAP revenue", "duration"),
    "Revenues": ("revenue", "GAAP revenue", "duration"),
    "SalesRevenueNet": ("revenue", "GAAP revenue", "duration"),
    "EarningsBeforeInterestTaxesDepreciationAndAmortization": ("ebitda", "reported EBITDA", "duration"),
    "OperatingIncomeLoss": ("operating income", "GAAP operating income", "duration"),
    "DepreciationDepletionAndAmortization": ("depreciation and amortization", "GAAP cash-flow D&A", "duration"),
    "CommonStockSharesOutstanding": ("shares", "reported common shares outstanding", "instant"),
    "EntityCommonStockSharesOutstanding": ("shares", "reported entity common shares outstanding", "instant"),
    "StockholdersEquity": ("book equity", "GAAP book equity", "instant"),
    "CashAndCashEquivalentsAtCarryingValue": ("cash", "GAAP cash and equivalents", "instant"),
    "LongTermDebtAndShortTermBorrowings": ("debt", "reported long-term debt and short-term borrowings", "instant"),
    "PreferredStockValue": ("preferred", "GAAP preferred stock carrying value", "instant"),
    "MinorityInterest": ("minority", "GAAP noncontrolling interests", "instant"),
}


def observations(content: str, metadata: Mapping, *, ticker: str, cik: str, as_of: str) -> list[dict]:
    try:
        data = json.loads(content)
        identity = int(cik)
        cutoff = date.fromisoformat(as_of[:10])
        url = urlsplit(str(metadata.get("url") or ""))
    except (ValueError, TypeError):
        return []
    if (not isinstance(data, dict) or data.get("cik") != identity or url.scheme != "https" or url.hostname != "data.sec.gov"
            or url.path != f"/api/xbrl/companyfacts/CIK{identity:010d}.json"):
        return []
    result = []
    # Company-facts archives can contain several megabytes on one line.
    # Index line breaks once rather than rescanning each record's prefix.
    newline_offsets = [match.start() for match in re.finditer("\n", content)]
    # Find exact original source substrings once. The taxonomy/tag relation is
    # re-established below; matching a similar numerical row is insufficient.
    quoted = {}
    for match in re.finditer(r"\{[^{}]*\}", content):
        try:
            quoted.setdefault(json.dumps(json.loads(match[0]), sort_keys=True), (match[0], match.start()))
        except ValueError:
            pass
    namespaces = data.get("facts")
    gaap = namespaces.get("us-gaap") if isinstance(namespaces, dict) else None
    if not isinstance(gaap, dict):
        return []
    for tag, (metric, basis, kind) in TAGS.items():
        taxonomy = "dei" if tag == "EntityCommonStockSharesOutstanding" else "us-gaap"
        concepts = namespaces.get(taxonomy) or {}
        node = concepts.get(tag) if isinstance(concepts, dict) else None
        units = node.get("units") if isinstance(node, dict) else None
        for unit, rows in (units if isinstance(units, dict) else {}).items():
            if not isinstance(rows, list):
                continue
            if unit != "shares" and not re.fullmatch(r"[A-Z]{3}", unit):
                continue
            if (metric == "shares") != (unit == "shares"):
                continue
            for row in rows:
                try:
                    end = date.fromisoformat(row["end"])
                    filed = date.fromisoformat(row["filed"])
                    start = date.fromisoformat(row["start"]) if row.get("start") else None
                except (ValueError, TypeError, KeyError):
                    continue
                numeric = number(row.get("val"))
                quote, offset = quoted.get(json.dumps(row, sort_keys=True), (None, 0))
                if (numeric is None or not quote or len(quote) > 2000 or filed > cutoff or filed < end
                        or row.get("form") not in {"10-K", "10-K/A", "10-Q", "10-Q/A"}
                        or not re.fullmatch(r"\d{10}-\d{2}-\d{6}", str(row.get("accn") or ""))):
                    continue
                days = (end - start).days + 1 if start else 0
                if kind == "duration" and not 60 <= days <= 378 or kind == "instant" and start:
                    continue
                first = bisect_left(newline_offsets, offset) + 1
                last = bisect_left(newline_offsets, offset + len(quote) - 1) + 1
                result.append({"subject": ticker, "issuer": ticker, "metric": metric, "value": display(numeric),
                    "unit": unit, "currency": None if unit == "shares" else unit, "basis": basis,
                    "period": f"{'annual ' if days >= 350 or row.get('form') in {'10-K', '10-K/A'} else 'quarterly '}period ended {end}", "period_start": start.isoformat() if start else None,
                    "period_end": end.isoformat(), "duration_days": days, "available_at": (filed + timedelta(days=1)).isoformat(),
                    "statement_type": f"SEC {taxonomy.upper()} " + tag, "locator": f"L{first}" if first == last else f"L{first}-L{last}",
                    "source_quote": quote, "source_ref": metadata.get("id"), "source_refs": [metadata.get("id")],
                    "proof": {"parser": VERSION, "taxonomy": taxonomy, "tag": tag, "cik": str(identity).zfill(10), "filed_at": filed.isoformat(), "accession": row["accn"]}})
    return result


def bind_claim(claim: Mapping, sources: Mapping[str, str], metadata: Mapping[str, Mapping], versions: Mapping, *, as_of: str | None) -> dict | None:
    from .earnings_financials import _ISSUER, _TABLE

    sid = str(claim.get("source_ref") or "")
    content = sources.get(sid, "")
    expected = versions.get(sid) or {}
    if claim.get("scale") or not as_of or hashlib.sha256(content.encode()).hexdigest() != (expected.get("hash") or expected.get("content_hash")):
        return None
    try:
        data = json.loads(content)
        cik, entity = data["cik"], data["entityName"]
    except (ValueError, KeyError, TypeError):
        return None
    def identity(text: str) -> tuple:
        legal = {"corporation", "corp", "inc", "incorporated", "company", "co", "limited", "ltd", "plc", "new"}
        return tuple(word for word in re.findall(r"[a-z0-9]+", text.casefold()) if word not in legal)
    ticker = str(claim.get("subject") or "").upper()
    identity_source = None
    for other_id, raw in sources.items():
        bound = versions.get(other_id) or {}
        if hashlib.sha256(raw.encode()).hexdigest() != (bound.get("hash") or bound.get("content_hash")):
            continue
        if set(word.upper() for word in _ISSUER.findall(raw)) != {ticker}:
            continue
        lines = raw.splitlines()
        names = [lines[index - 1].strip() for index, line in enumerate(lines) if index and _TABLE.fullmatch(line.strip())]
        if identity(entity) and any(identity(name) == identity(entity) for name in names):
            identity_source = other_id
            break
    if not identity_source:
        return None
    for row in observations(content, metadata.get(sid, {}), ticker=ticker, cik=cik, as_of=as_of):
        keys = ("metric", "value", "unit", "currency", "basis", "period", "period_start", "period_end", "statement_type", "locator", "source_quote")
        if all(str(claim.get(key) or "") == str(row.get(key) or "") for key in keys):
            return row | {"proof": row["proof"] | {"issuer_source_id": identity_source}}
    return None


def latest_seeds(sources: list[dict], *, ticker: str, cik: str, as_of: str) -> list[dict]:
    rows = [row for source in sources for row in observations(source.get("content") or "", source, ticker=ticker, cik=cik, as_of=as_of)]
    groups: dict[tuple, list[dict]] = {}
    for row in rows:
        if row["duration_days"] and row["duration_days"] < 350:
            continue
        groups.setdefault((row["metric"], row["currency"]), []).append(row)
    selected = []
    for rows in groups.values():
        newest_end = max(row["period_end"] for row in rows)
        newest = [row for row in rows if row["period_end"] == newest_end]
        filed = max(row["available_at"] for row in newest)
        newest = [row for row in newest if row["available_at"] == filed]
        if len({row["value"] for row in newest}) == 1:
            selected.append(newest[0])
    return selected
