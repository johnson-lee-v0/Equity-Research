"""Quarterly spending from issuer slide tables, never a YTD chart estimate.

The cash-flow reconciliation binds columns, units and both lease-bridge
operands. A matching SEC cash fact establishes USD when a deck only says '$'.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re

from .capex_facts import CASH_TAG, LEASE_BASIS

VERSION = "quarterly-capex-tables.v1"
METRIC_IDS = {"capex_quarterly", "capex_cash_ppe_quarterly"}
_QUARTER = re.compile(r"\bQ([1-4])\s*(?:['’](\d{2})|(?:FY\s*)?(20\d{2}))\b", re.I)
_NUMBER = re.compile(r"\(?\s*-?\d+(?:,\d{3})*(?:\.\d+)?\s*\)?")


def presentation_period(content: str) -> tuple[int, int] | None:
    """Use the cover, not a comparative quarter elsewhere in a slide deck."""
    cover = re.split(r"\[PDF page 2\]", content, maxsplit=1)[0]
    cover = re.sub(r"\s+", " ", cover[:2200])
    match = re.search(r"(?:earnings|results)\s+(?:presentation|slides)\s+(?:for\s+)?Q([1-4])\s*(?:FY\s*)?(20\d{2})\b", cover, re.I)
    return (int(match[2]), int(match[1])) if match else None


def _issuer_origin(document: dict, company: dict, issuer_sources: dict[str, dict]) -> bool:
    from .earnings_sources import _company_present, _primary_release_url
    if _primary_release_url(str(document.get("url") or ""), company):
        return True
    redirect = document.get("issuer_redirect")
    if isinstance(redirect, dict) and document.get("provenance") == "issuer_redirected_asset":
        return bool(redirect.get("version") == "issuer-pdf-redirect.v1"
            and _primary_release_url(str(redirect.get("requested_url") or ""), company)
            and redirect.get("final_url") == document.get("url")
            and redirect.get("content_hash") == document.get("content_hash")
            and re.fullmatch(r"[a-f0-9]{64}", str(redirect.get("original_hash") or "")))
    # This receipt is emitted by the collector after checking the actual HTML
    # link. Revalidate its archived issuer-page hash before using a CDN asset.
    issuer = issuer_sources.get(document.get("issuer_link_source_id"))
    if not issuer or document.get("provenance") != "issuer_linked_asset":
        return False
    content = issuer.get("content", "")
    return bool(document.get("issuer_link_target_url") == document.get("url")
        and document.get("issuer_link_url") == issuer.get("url")
        and _primary_release_url(str(issuer.get("url") or ""), company)
        and _company_present(content[:6500], company)
        and hashlib.sha256(content.encode()).hexdigest() == issuer.get("content_hash") == document.get("issuer_link_content_hash"))


def _numbers(text: str, labels: str, count: int) -> list[Decimal] | None:
    numeric = re.sub(labels, "", text, flags=re.I)
    if re.sub(r"[\s$]+", "", _NUMBER.sub("", numeric)):
        return None
    values = _NUMBER.findall(numeric)
    if len(values) != count:
        return None
    try:
        result = [Decimal(re.sub(r"[\s,()]", "", value)) * (-1 if "(" in value else 1) for value in values]
        return result if all(value.is_finite() and value >= 0 for value in result) else None
    except InvalidOperation:
        return None


def _currency_anchor(sources: dict[str, dict], company: dict, periods: list[tuple[int, int]], cash: list[Decimal], document: dict) -> dict | None:
    """A same-table quarter's exact cash PP&E value must match verified USD."""
    from .earnings_trends import _annual_fiscal_year
    try:
        cik = int(company["cik"])
        cutoff = date.fromisoformat(str(document["published_at"])[:10])
        event_end = date.fromisoformat(document["period_end"])
        fiscal_month = int(company.get("fiscal_year_end", "")[:2])
    except (ValueError, KeyError, TypeError):
        return None
    if not 1 <= fiscal_month <= 12:
        return None
    for sid, source in sources.items():
        content = source.get("content", "")
        if hashlib.sha256(content.encode()).hexdigest() != source.get("content_hash"):
            continue
        try:
            payload = json.loads(content)
            if payload.get("cik") != cik:
                continue
            facts_url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
            concept_url = f"https://data.sec.gov/api/xbrl/companyconcept/CIK{cik:010d}/us-gaap/{CASH_TAG}.json"
            if source.get("url") == facts_url:
                node = payload.get("facts", {}).get("us-gaap", {}).get(CASH_TAG, {})
            elif source.get("url") == concept_url and payload.get("tag") == CASH_TAG and payload.get("taxonomy") == "us-gaap":
                node = payload
            else:
                continue
            rows = node.get("units", {}).get("USD", [])
        except (TypeError, ValueError, AttributeError):
            continue
        for row in rows:
            try:
                start, end, filed = (date.fromisoformat(row[key]) for key in ("start", "end", "filed"))
                if (row.get("form") not in {"10-Q", "10-Q/A", "10-K", "10-K/A"}
                        or not 70 <= (end - start).days <= 110 or not end <= filed <= cutoff or end > event_end
                        or not re.fullmatch(r"\d{10}-\d{2}-\d{6}", str(row.get("accn", "")))):
                    continue
                year = _annual_fiscal_year(end.isoformat(), document["period_end"], presentation_period(document["content"]), company["fiscal_year_end"])
                quarter = ((end.month - fiscal_month - 1) % 12) // 3 + 1
                index = periods.index((year, quarter))
                if Decimal(str(row["val"])) != cash[index] * 1_000_000:
                    continue
                quote = next((match[0] for match in re.finditer(r"\{[^{}]*\}", content) if json.loads(match[0]) == row), None)
                if quote:
                    return {"method": "exact_comparative_sec_cash_ppe", "source_id": sid, "url": source["url"], "quote": quote,
                        "period": f"Q{quarter} FY{year}", "period_end": row["end"], "value": float(cash[index] / 1000), "unit": "USD billions"}
            except (ValueError, TypeError, KeyError, InvalidOperation):
                continue
    return None


def quarterly_capex_points(document: dict, company: dict, cash_sources: dict[str, dict], *, issuer_sources: dict[str, dict] | None = None) -> list[dict]:
    """Parse an explicit quarterly FCF table and contemporaneous capex basis."""
    from .earnings_sources import _company_present
    from .earnings_trends import _fiscal_period
    content = str(document.get("content") or "")
    period = presentation_period(content)
    if (not document.get("source_id") or not _issuer_origin(document, company, issuer_sources or {})
            or not _company_present(content[:3000], company)
            or not period or period != _fiscal_period(document.get("fiscal_period", ""))
            or hashlib.sha256(content.encode()).hexdigest() != document.get("content_hash")):
        return []
    # Only an issuer's explicit definition authorizes the lease-inclusive sum.
    compact = re.sub(r"\s+", " ", content)
    definition = re.search(r"Capital expenditures for periods presented were related to purchases of property and equipment and principal payments on finance leases\.", compact, re.I)
    tables = list(re.finditer(r"Free Cash Flow Reconciliation\s+(?:In Millions|\(?USD in millions\)?)\s+([\s\S]+?)(?=Free cash flow \(FCF\)|\[PDF page|$)", content, re.I))
    if len(tables) != 1:
        return []
    table = tables[0]
    header = re.split(r"Net cash provided by", table[1], maxsplit=1, flags=re.I)[0]
    columns = list(_QUARTER.finditer(header))
    periods = [(int(m[3]) if m[3] else 2000 + int(m[2]), int(m[1])) for m in columns]
    if (not periods or len(periods) > 12 or len(set(periods)) != len(periods) or periods != sorted(periods)
            or any(value > period for value in periods)
            or re.sub(r"\s+", "", _QUARTER.sub("", header))):
        return []
    cash_match = re.search(r"Less:\s*Purchases of([\s\S]+?)(?=Less:\s*Principal payments)", table[1], re.I)
    lease_match = re.search(r"Less:\s*Principal payments([\s\S]+?)(?=Free Cash Flow)", table[1], re.I)
    if not cash_match or not lease_match:
        return []
    cash = _numbers(cash_match[1], r"property and equipment", len(periods))
    leases = _numbers(lease_match[1], r"on finance leases", len(periods))
    if cash is None or leases is None:
        return []
    if re.search(r"\b(?:CAD|AUD|EUR|GBP|JPY|RMB|CNY)\b|C\$|A\$|[€£¥]", table[0], re.I):
        return []
    anchor = _currency_anchor(cash_sources, company, periods, cash, document)
    if not anchor:
        return []
    quote = table[0][:table[0].lower().index("free cash flow", len("Free Cash Flow Reconciliation"))].rstrip()
    page_markers = list(re.finditer(r"\[PDF page (\d+)\]", content[:table.start()]))
    output = []
    for index, (year, quarter) in enumerate(periods):
        base = {"period": f"Q{quarter} FY{year}", "period_end": document.get("period_end") if (year, quarter) == period else None,
            "kind": "actual", "source_id": document["source_id"], "url": document["url"], "published_at": document["published_at"],
            "quote": quote, "low": None, "high": None, "source_kind": "issuer_presentation", "recovery": VERSION,
            "source_method": "Issuer earnings presentation quarterly cash-flow reconciliation", "currency_binding": anchor,
            "source_locator": {"page": int(page_markers[-1][1]) if page_markers else None, "column": index + 1, "period": f"Q{quarter} FY{year}"}}
        output.append(base | {"metric": "capex_cash_ppe_quarterly", "value": float(cash[index] / 1000),
            "calculation": {"formula": "cash purchases of PP&E in USD millions / 1,000", "inputs": {"cash_ppe_millions": str(cash[index])}}})
        if definition:
            output.append(base | {"metric": "capex_quarterly", "value": float((cash[index] + leases[index]) / 1000), "measure_basis": LEASE_BASIS,
                "definition_source": {"source_id": document["source_id"], "url": document["url"], "quote": definition[0]},
                "calculation": {"formula": "(quarterly cash purchases of PP&E + quarterly finance-lease principal payments) / 1,000",
                    "basis": "Issuer-defined quarterly capex including finance-lease principal; inputs in USD millions",
                    "inputs": [{"tag": CASH_TAG, "source_id": document["source_id"], "quote": cash_match[0], "value": str(cash[index]), "unit": "USD millions"},
                               {"tag": "FinanceLeasePrincipalPayments", "source_id": document["source_id"], "quote": lease_match[0], "value": str(leases[index]), "unit": "USD millions"}]}})
    return output
