"""Source-bound annual capital spending, independent of narrative extraction.

Cash PP&E is always a separate measure. A finance-lease bridge requires an
explicit, contemporaneous issuer definition and two facts from one filing.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

VERSION = "sec-capex-facts.v1"
CASH_TAG = "PaymentsToAcquirePropertyPlantAndEquipment"
LEASE_TAG = "FinanceLeasePrincipalPayments"
LEASE_BASIS = "cash_ppe_plus_finance_lease_principal"


def cached_financial_sources(repo: Any, namespace: str, cik: str) -> list[dict]:
    """Return verified immutable archives; no new repository or network access."""
    if not getattr(repo, "db", None):
        return []
    urls = [f"https://data.sec.gov/api/xbrl/companyfacts/CIK{int(cik):010d}.json",
            f"https://data.sec.gov/api/xbrl/companyconcept/CIK{int(cik):010d}/us-gaap/{CASH_TAG}.json"]
    with repo.db.operation() as conn:
        rows = conn.execute("SELECT id FROM sources WHERE namespace=? AND url IN (?,?) ORDER BY retrieval_at DESC LIMIT 8", (namespace, *urls)).fetchall()
    sources = repo.source_packet(namespace, [row["id"] for row in rows]) if rows else []
    return [source for source in sources if hashlib.sha256(source.get("content", "").encode()).hexdigest() == source.get("content_hash")]


async def companyfacts_source(acquisition: Any, company: dict, *, research_as_of: str) -> dict | None:
    """Reuse today's verified companyfacts for both trends and valuation."""
    cik = str(company.get("cik", ""))
    if not re.fullmatch(r"\d{1,10}", cik):
        return None
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{int(cik):010d}.json"
    for source in cached_financial_sources(acquisition.repo, acquisition.namespace, cik):
        if source.get("url") == url and str(source.get("retrieved_at") or source.get("retrieval_at") or "")[:10] == research_as_of[:10]:
            try:
                payload = json.loads(source["content"])
                if payload.get("cik") == int(cik) and isinstance(payload.get("facts", {}).get("us-gaap"), dict):
                    return source
            except (ValueError, TypeError):
                pass
    try:
        payload, page = await acquisition._json(url)
        if payload.get("cik") != int(cik) or not isinstance(payload.get("facts", {}).get("us-gaap"), dict):
            return None
        from .source_archive import archive_public_observation
        result = archive_public_observation(acquisition.repo, page, namespace=acquisition.namespace, scope=f"annual-financials:{cik}:{research_as_of[:10]}")
        if result.get("source_id"):
            return acquisition.repo.source_packet(acquisition.namespace, [result["source_id"]])[0]
    except (ValueError, OSError):
        pass
    return None


def _eligible_rows(source: dict, company: dict, event: dict, research_as_of: str) -> dict[str, list[dict]]:
    from .earnings_trends import _annual_fiscal_year, _fiscal_period
    output = {CASH_TAG: [], LEASE_TAG: []}
    content = source.get("content", "")
    if not source.get("source_id", source.get("id")) or not source.get("content_hash") or hashlib.sha256(content.encode()).hexdigest() != source["content_hash"]:
        return output
    try:
        payload = json.loads(content)
        if not isinstance(payload, dict):
            return output
        cik = int(company["cik"])
        observed = str(source.get("retrieved_at") or source.get("retrieval_at") or source.get("source_observed_at") or "")[:10]
        cutoff = min(date.fromisoformat(research_as_of[:10]), date.fromisoformat(observed))
        event_end = date.fromisoformat(event["period_end"])
        period = _fiscal_period(event["fiscal_period"])
        if not period or payload.get("cik") != cik:
            return output
    except (ValueError, TypeError, KeyError):
        return output
    root = f"https://data.sec.gov/api/xbrl/"
    if source.get("url") == f"{root}companyfacts/CIK{cik:010d}.json":
        nodes = payload.get("facts", {}).get("us-gaap", {})
    elif source.get("url") == f"{root}companyconcept/CIK{cik:010d}/us-gaap/{CASH_TAG}.json" and payload.get("taxonomy") == "us-gaap" and payload.get("tag") == CASH_TAG:
        nodes = {CASH_TAG: payload}
    else:
        return output
    quoted = {}
    for match in re.finditer(r"\{[^{}]*\}", content):
        try:
            quoted.setdefault(json.dumps(json.loads(match[0]), sort_keys=True), match[0])
        except ValueError:
            pass
    if not isinstance(nodes, dict):
        return output
    for tag in output:
        node = nodes.get(tag)
        units = node.get("units") if isinstance(node, dict) else None
        rows = units.get("USD") if isinstance(units, dict) else None
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            try:
                start, end, filed = (date.fromisoformat(row[key]) for key in ("start", "end", "filed"))
                amount = Decimal(str(row["val"]))
                year = _annual_fiscal_year(end.isoformat(), event_end.isoformat(), period, company.get("fiscal_year_end", ""))
                quote = quoted.get(json.dumps(row, sort_keys=True))
                completed = period[0] if period[1] in (None, 4) else period[0] - 1
                if (not amount.is_finite() or amount < 0 or not quote or not re.fullmatch(r"\d{10}-\d{2}-\d{6}", str(row.get("accn") or ""))
                        or row.get("form") not in {"10-K", "10-K/A"} or row.get("fp") != "FY"
                        or not 330 <= (end - start).days <= 400 or not end <= filed <= cutoff
                        or end > event_end or not completed - 4 <= year <= completed):
                    continue
            except (ValueError, TypeError, KeyError, InvalidOperation):
                continue
            output[tag].append({"row": row, "quote": quote, "year": year, "amount": amount,
                "source": source, "source_id": source.get("source_id", source.get("id"))})
    return output


def _definition(documents: list[dict], year: int, research_as_of: str) -> dict | None:
    from .earnings_trends import _fiscal_period
    for source in documents:
        period = _fiscal_period(source.get("fiscal_period", ""))
        if not period or period[0] != year or period[1] not in (None, 4):
            continue
        content = source.get("content", "")
        try:
            if (hashlib.sha256(content.encode()).hexdigest() != source.get("content_hash")
                    or date.fromisoformat(str(source.get("published_at") or "")[:10]) > date.fromisoformat(research_as_of[:10])):
                continue
        except ValueError:
            continue
        # Bind the definition in the reporting year, never borrow a current
        # definition for an older year or mistake guidance for an actual.
        pattern = r"capital expenditures?,?\s+including principal payments on finance leases,?\s+(?:were|was|totaled|totalled)\s+\$[\d.,]+\s*(?:billion|million)"
        match = re.search(pattern, content, re.I)
        if match and source.get("source_id"):
            return {"source_id": source["source_id"], "quote": match[0], "published_at": source.get("published_at"), "url": source.get("url")}
    return None


def _annual_conflict(documents: list[dict], year: int, total: Decimal, research_as_of: str) -> bool:
    from .earnings_trends import _amount_qualifier, _compact, _fiscal_period, _semantic_measure
    for document in documents:
        period = _fiscal_period(document.get("fiscal_period", ""))
        raw = document.get("content", "")
        if not period or period[0] != year or period[1] not in (None, 4) or hashlib.sha256(raw.encode()).hexdigest() != document.get("content_hash"):
            continue
        try:
            if date.fromisoformat(str(document.get("published_at") or "")[:10]) > date.fromisoformat(research_as_of[:10]):
                continue
        except ValueError:
            continue
        for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z])", _compact(raw)):
            if len(sentence) > 1800 or not re.search(r"capital expenditures?,?\s+including principal payments on finance leases", sentence, re.I):
                continue
            for amount in re.finditer(r"\$\s*([\d,]+(?:\.\d+)?)\s*(billion|million)", sentence, re.I):
                scale = Decimal(1_000_000_000 if amount[2].lower() == "billion" else 1_000_000)
                reported = Decimal(amount[1].replace(",", "")) * scale
                billions = float(reported / 1_000_000_000)
                period_sentence = re.sub(r",?\s+including principal payments on finance leases,?", "", sentence, flags=re.I)
                if not _semantic_measure("capex", period_sentence, billions, "actual", year) or _amount_qualifier(period_sentence, billions, "USD billions") in {"less_than", "greater_than"}:
                    continue
                places = len(amount[1].split(".")[1]) if "." in amount[1] else 0
                tolerance = scale / (Decimal(10) ** places) / 2
                if abs(reported - total) > tolerance:
                    return True
    return False


def project_capex(sources: list[dict], company: dict, event: dict, documents: list[dict], *, research_as_of: str) -> dict:
    """Pure projection with exact JSON quotes and explicit bridge operands."""
    cash, leases = [], []
    for source in sources:
        rows = _eligible_rows(source, company, event, research_as_of)
        cash.extend(rows[CASH_TAG]); leases.extend(rows[LEASE_TAG])
    # Preserve the first reported vintage, including conflicts. Bridge operands
    # must match that exact filing; a later restatement is not a replacement.
    selected = {}
    gaps = []
    amounts_by_filing = {}
    for item in cash:
        key = tuple(item["row"].get(field) for field in ("start", "end", "accn", "filed", "form"))
        amounts_by_filing.setdefault(key, set()).add(item["amount"])
    for item in sorted(cash, key=lambda value: (value["row"]["filed"], value["row"]["accn"], value["source_id"])):
        selected.setdefault(item["row"]["end"], item)
    points = []
    used = set()
    for item in selected.values():
        row, source = item["row"], item["source"]
        key = tuple(row.get(field) for field in ("start", "end", "accn", "filed", "form"))
        if len(amounts_by_filing[key]) != 1:
            gaps.append(f"FY{item['year']} cash PP&E has conflicting values in the first filing; neither a value nor a later replacement was selected.")
            continue
        point = {"metric": "capex_cash_ppe", "period": f"FY{item['year']}", "period_end": row["end"],
            "value": float(item["amount"] / 1_000_000_000), "kind": "actual", "source_id": item["source_id"],
            "url": source["url"], "quote": item["quote"], "published_at": row["filed"], "low": None, "high": None,
            "recovery": VERSION, "source_method": "SEC XBRL annual cash-flow fact", "accession": row["accn"]}
        points.append(point); used.add(item["source_id"])
        definition = _definition(documents, item["year"], research_as_of)
        matching = [lease for lease in leases if all(lease["row"].get(key) == row.get(key) for key in ("start", "end", "accn", "filed", "form"))]
        if not definition or not matching or len({lease["amount"] for lease in matching}) != 1:
            continue
        lease = matching[0]
        if _annual_conflict(documents, item["year"], item["amount"] + lease["amount"], research_as_of):
            gaps.append(f"FY{item['year']} calculated capex conflicts with an explicit same-measure issuer annual actual beyond reported precision; the bridge was withheld.")
            continue
        operands = [{"tag": tag, "source_id": value["source_id"], "url": value["source"]["url"], "quote": value["quote"], "value": str(value["amount"]), "unit": "USD", "accession": row["accn"]}
                    for tag, value in ((CASH_TAG, item), (LEASE_TAG, lease))]
        points.append(point | {"metric": "capex", "value": float((item["amount"] + lease["amount"]) / 1_000_000_000),
            "measure_basis": LEASE_BASIS, "source_method": "Calculated from SEC cash purchases plus finance-lease principal; issuer definition verified",
            "definition_source": definition, "calculation": {"formula": "(cash purchases of PP&E + finance-lease principal payments) / 1,000,000,000",
                "basis": "Issuer-defined capex including finance-lease principal", "inputs": operands},
            "published_at": max(row["filed"], str(definition.get("published_at") or row["filed"]))})
        used.add(lease["source_id"])
    return {"points": points, "sources": [source for source in sources if source.get("source_id", source.get("id")) in used], "version": VERSION, "gaps": gaps}
