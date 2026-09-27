"""Recover reported trend gaps from issuer releases and earnings exhibits.

Source location and extraction are separate: only independently fetched issuer
material enters the deterministic parsers. Derived ratios retain their operands
and formula; historical gaps are never relabeled as forecasts.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json
import re
from typing import Any

from .earnings_sources import _company_present, _date_present, _primary_release_url, _schema, material_rejection, presentation_redirect
from .earnings_financials import release_eps_observations
from .quarterly_capex import quarterly_capex_points, presentation_period

VERSION = "earnings-primary-backfill.v3"
_DATE = re.compile(r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+20\d{2}\b", re.I)
_NUMBER = re.compile(r"\(?\s*-?\d+(?:,\d{3})*(?:\.\d+)?\s*\)?")
_SCHEMA = _schema({"sources": {"type": "array", "maxItems": 6, "items": _schema({
    "url": {"type": "string"}, "fiscal_period": {"type": "string"},
    "period_end": {"type": "string"}, "published_at": {"type": "string"},
})}})


def _values(row: str, label: str, count: int) -> list[Decimal] | None:
    remainder = re.sub(label, "", row, count=1, flags=re.I).strip()
    numbers = _NUMBER.findall(remainder)
    if len(numbers) != count or re.sub(r"[\s$|]+", "", _NUMBER.sub("", remainder)):
        return None
    try:
        return [Decimal(re.sub(r"[\s,()]", "", raw)) * (-1 if "(" in raw else 1) for raw in numbers]
    except InvalidOperation:
        return None


def _point(document: dict[str, Any], metric: str, period: str, value: Decimal | float, quote: str, **extra: Any) -> dict[str, Any]:
    return {"metric": metric, "period": period, "period_end": document.get("period_end"), "value": float(value),
        "kind": "actual", "source_id": document["source_id"], "url": document["url"], "quote": quote,
        "published_at": document.get("published_at") or document.get("earnings_date"), "low": None, "high": None,
        "qualifier": None, "source_kind": "sec_earnings_exhibit" if "sec.gov/" in document["url"] else "issuer_release", "source_method": "Company investor-relations / earnings exhibit", "recovery": VERSION, **extra}


def primary_release_points(document: dict[str, Any], company: dict[str, Any], sec_cash_points: list[dict[str, Any]] = (), *, cash_sources: dict[str, dict[str, Any]] | None = None, issuer_sources: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Parse supported issuer statement layouts, keeping units and periods exact."""
    content = str(document.get("content") or "")
    period = str(document.get("fiscal_period") or "")
    if not document.get("source_id") or not _company_present(content[:6500], company):
        return []
    points = quarterly_capex_points(document, company, cash_sources or {}, issuer_sources=issuer_sources)
    if not _primary_release_url(str(document.get("url") or ""), company):
        return points
    from .earnings_trends import _fiscal_period, _period_label
    parsed_period = _fiscal_period(period)
    if not parsed_period or not parsed_period[1]:
        return []
    year, quarter = parsed_period
    period = _period_label(parsed_period)
    # Only an explicit annual statement with explicit USD denomination is a
    # deterministic management actual. Quarter/YTD/guidance are not substitutes.
    if quarter == 4:
        for match in re.finditer(r"[^\n.!?]*(?:capital expenditures?|capital spending|capex)[^\n!?]*(?:\.(?=\s+[A-Z]|\s*$)|$)", content, re.I):
            quote = match[0].strip()
            if (len(quote) > 900 or not re.search(r"full[ -]year|total year|fiscal\s+(?:year\s+)?" + str(year), quote, re.I)
                    or not re.search(r"\b(?:were|was|totaled|totalled|spent)\b", quote, re.I)
                    or re.search(r"expect|plan|guidance|forecast|anticipate|estimate|budget|target|year.to.date|first\s+(?:six|nine)", quote, re.I)):
                continue
            amounts = list(re.finditer(r"(?:USD\s*\$?|U\.S\.\s*\$)\s*([\d,.]+)\s*(billion|million)", quote, re.I))
            if len(amounts) == 1:
                value = Decimal(amounts[0][1].replace(",", "")) * (1 if amounts[0][2].lower() == "billion" else Decimal(".001"))
                points.append(_point(document, "capex", f"FY{year}", value, quote))
    # The sentence explicitly identifies quarterly net sales, not comparable
    # sales, membership revenue or year-to-date revenue.
    sales = list(re.finditer(r"Net sales for the (?:\d{1,2}[ -]week )?(?:fourth |first |second |third )?quarter (increased|decreased|rose|declined)\s+([\d.]+)\s*(?:percent|%)[^\n]*?\.(?=\s+[A-Z]|\s*$)", content, re.I))
    if len(sales) == 1:
        sign = -1 if sales[0][1].lower() in {"decreased", "declined"} else 1
        points.append(_point(document, "net_sales_growth", period, Decimal(sales[0][2]) * sign, sales[0][0]))
    # Some issuers state an exactly reported flat quarter in words. Bind it
    # to the named reporting quarter and reported basis, never to a nearby
    # currency-neutral percentage or an approximately flat forecast.
    ordinal = ("first", "second", "third", "fourth")[quarter - 1]
    flat = list(re.finditer(
        rf"(?im)^{ordinal}[ -]quarter (?:net sales|revenues?) were (?:\$[\d,.]+\s+(?:million|billion),\s*)?flat on a reported basis\b[^\n]*",
        content))
    if len(flat) == 1 and re.search(rf"\b(?:fiscal\s+(?:year\s+)?|FY\s*){year}\b", content[:6500], re.I):
        points.append(_point(document, "net_sales_growth", period, 0, flat[0][0], calculation={
            "formula": "Reported flat revenue = 0% year-over-year change at the issuer's reported precision",
            "inputs": {"reported_revenue_change": "flat"},
            "basis": "Company-wide reported quarterly revenue, excluding the separate currency-neutral comparison.",
        }))
    parsed = release_eps_observations(content)["observations"]
    current = [row for row in parsed if row["issuer"] == company["ticker"] and row["comparison"] == "current" and row["duration_weeks"] < 18]
    lines = content.splitlines()
    if len(current) == 1 and current[0]["period_end"] == document.get("period_end"):
        proof = current[0]["proof"]
        start, date_line, stop = proof["table_line"] - 1, proof["date_line"] - 1, proof["metric_line"] - 1
        count = len(_DATE.findall(lines[date_line]))
        sales_rows = [(i, line) for i, line in enumerate(lines[start:stop], start) if re.match(r"^Net sales\b", line, re.I)]
        cost_rows = [(i, line) for i, line in enumerate(lines[start:stop], start) if re.match(r"^Merchandise costs\b", line, re.I)]
        if len(sales_rows) == len(cost_rows) == 1:
            net_sales = _values(sales_rows[0][1], r"^Net sales", count)
            costs = _values(cost_rows[0][1], r"^Merchandise costs", count)
            if net_sales and costs and net_sales[0] > 0 and 0 <= costs[0] <= net_sales[0]:
                margin = ((net_sales[0] - costs[0]) / net_sales[0] * 100).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
                quote = "\n".join(lines[start:cost_rows[0][0] + 1])
                points.append(_point(document, "gross_margin", period, margin, quote, calculation={
                    "formula": "(net sales − merchandise costs) / net sales × 100", "rounding": "2 decimal places",
                    "inputs": {"net_sales": str(net_sales[0]), "merchandise_costs": str(costs[0])},
                    "basis": "Reported merchandise gross margin as a percentage of net sales; calculated from the same-period income statement.",
                }))
    # An unfiled latest annual release may already contain the cash-flow table.
    # Resolve its dollar denomination through the exact comparative SEC cash
    # fact, not the issuer's listing or management's broader capex estimate.
    if quarter == 4:
        for start, line in enumerate(lines):
            if not re.fullmatch(r"(?:CONDENSED\s+)?CONSOLIDATED STATEMENTS? OF CASH FLOWS", line.strip(), re.I):
                continue
            end = next((i for i in range(start + 1, min(len(lines), start + 30)) if "CASH FLOWS FROM OPERATING" in lines[i]), None)
            if end is None:
                continue
            header = "\n".join(lines[start:end])
            dates = _DATE.findall(header)
            if len(dates) != 2 or not re.search(r"\b(?:52|53) Weeks Ended\b", header, re.I) or not re.search(r"\b(?:amounts|dollars) in millions\b", header, re.I):
                continue
            ends = [datetime.strptime(re.sub(r"\s+", " ", item).title(), "%B %d, %Y").date().isoformat() for item in dates]
            if ends[0] != document.get("period_end"):
                continue
            rows = [(i, row) for i, row in enumerate(lines[end:min(len(lines), end + 70)], end) if re.match(r"^(?:Additions to|Purchases of) property and equipment\b", row, re.I)]
            if len(rows) != 1:
                continue
            amounts = _values(rows[0][1], r"^(?:Additions to|Purchases of) property and equipment", 2)
            if not amounts or any(amount > 0 for amount in amounts):
                continue
            anchor = None
            for source_id, source in (cash_sources or {}).items():
                try:
                    payload = json.loads(source.get("content", ""))
                except (ValueError, TypeError):
                    continue
                expected_url = f"https://data.sec.gov/api/xbrl/companyconcept/CIK{int(company['cik']):010d}/us-gaap/PaymentsToAcquirePropertyPlantAndEquipment.json"
                facts_url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{int(company['cik']):010d}.json"
                if payload.get("cik") != int(company['cik']):
                    continue
                if source.get("url") == facts_url:
                    node = payload.get("facts", {}).get("us-gaap", {}).get("PaymentsToAcquirePropertyPlantAndEquipment", {})
                elif source.get("url") == expected_url and payload.get("tag") == "PaymentsToAcquirePropertyPlantAndEquipment" and payload.get("taxonomy") == "us-gaap":
                    node = payload
                else:
                    continue
                for row in node.get("units", {}).get("USD", []):
                    try:
                        annual = 350 <= (date.fromisoformat(row['end']) - date.fromisoformat(row['start'])).days + 1 <= 378
                        exact_value = Decimal(str(row.get("val"))) / 1_000_000_000 == abs(amounts[1]) / 1000
                    except (ValueError, TypeError, KeyError, InvalidOperation):
                        continue
                    if annual and row.get("form") in {"10-K", "10-K/A"} and row.get("filed", "9999") <= str(document.get("published_at") or document.get("earnings_date") or "") and row.get("end") == ends[1] and exact_value:
                        anchor = {"source_id": source_id, "value": float(abs(amounts[1]) / 1000)}
                        break
                if anchor:
                    break
            if anchor:
                points.append(_point(document, "capex_cash_ppe", f"FY{year}", abs(amounts[0]) / 1000, "\n".join(lines[start:rows[0][0] + 1]),
                    unit="USD billions", currency_binding={"method": "exact_comparative_sec_cash_ppe", "source_id": anchor["source_id"], "period_end": ends[1], "value": anchor["value"]},
                    calculation={"formula": "absolute cash outflow in USD millions / 1,000", "inputs": {"reported_cash_outflow_millions": str(amounts[0])}}))
    return points


def _retain_currency_sources(result: dict, documents: list[dict], cash_sources: dict[str, dict]) -> None:
    known_ids = {document.get("source_id") for document in [*documents, *result["documents"]]}
    for point in result["points"]:
        sid = (point.get("currency_binding") or {}).get("source_id")
        if sid in cash_sources and sid not in known_ids:
            result["documents"].append(cash_sources[sid] | {"source_id": sid, "kind": "trend_sec_financials"})
            known_ids.add(sid)


async def recover_primary_trends(acquisition: Any, company: dict[str, Any], event: dict[str, Any], documents: list[dict[str, Any]], points: dict[str, list[dict[str, Any]]], *, candidates: list[dict[str, Any]] | None = None, research_as_of: str | None = None) -> dict[str, Any]:
    """One bounded primary-source follow-up; callers may supply located URLs."""
    from .earnings_trends import _fiscal_period, _period_label
    latest = _fiscal_period(event.get("fiscal_period", ""))
    result: dict[str, Any] = {"documents": [], "points": [], "checks": [], "gaps": []}
    if not latest:
        return result
    # First recover what is already archived: this path never needs discovery.
    existing = {document.get("url") for document in documents}
    cash_ids = list(dict.fromkeys(source_id for point in points.get("capex_cash_ppe", []) for source_id in (point.get("source_id"), (point.get("currency_binding") or {}).get("source_id")) if source_id))
    cash_sources = {source["id"]: source for source in acquisition.repo.source_packet(acquisition.namespace, cash_ids)} if cash_ids else {}
    if getattr(acquisition, "repo", None) and company.get("cik"):
        from .capex_facts import cached_financial_sources
        cash_sources.update({source["id"]: source for source in cached_financial_sources(acquisition.repo, acquisition.namespace, company["cik"])})
    issuer_ids = list(dict.fromkeys(document["issuer_link_source_id"] for document in documents if document.get("issuer_link_source_id")))
    issuer_sources = {source["id"]: source for source in acquisition.repo.source_packet(acquisition.namespace, issuer_ids)} if issuer_ids else {}
    for document in documents:
        result["points"].extend(primary_release_points(document, company, points.get("capex_cash_ppe", []), cash_sources=cash_sources, issuer_sources=issuer_sources))
    _retain_currency_sources(result, documents, cash_sources)
    index = latest[0] * 4 + (latest[1] or 4) - 1
    desired = {_period_label((number // 4, number % 4 + 1)) for number in range(index - 5, index + 1)}
    missing: dict[str, list[str]] = {}
    for metric in ("net_sales_growth", "gross_margin", "capex_quarterly", "capex_cash_ppe_quarterly"):
        observed = [*points.get(metric, []), *(point for point in result["points"] if point["metric"] == metric)]
        if observed or (metric == "capex_quarterly" and points.get("capex")):
            needed = desired - {point["period"] for point in observed if point.get("kind") == "actual" and point.get("value") is not None}
            for period in sorted(needed):
                missing.setdefault(period, []).append(metric)
    completed = latest[0] if latest[1] in (None, 4) else latest[0] - 1
    for metric in ("capex_cash_ppe", "capex"):
        observed = [*points.get(metric, []), *(point for point in result["points"] if point["metric"] == metric)]
        # Always seek reported cash spending. A management measure is relevant
        # when it was actually observed (for example a guidance series).
        if metric == "capex" and not observed:
            continue
        actual_periods = {point["period"] for point in observed if point.get("kind") == "actual" and point.get("value") is not None}
        for year in range(completed - 4, completed + 1):
            if f"FY{year}" not in actual_periods:
                missing.setdefault(f"Q4 FY{year}", []).append(metric)
    if not missing:
        return result
    if candidates is None:
        try:
            located = await acquisition._discover("primary_trend_recovery", (
                f"Locate direct PRIMARY earnings releases or financial supplements for {company['name']} ({company['ticker']}, SEC CIK {company['cik']}). "
                f"Only these completed reporting periods and missing metrics: {missing}. Latest known earnings: {event['earnings_date']}. "
                "Prefer company investor-relations releases with income/cash-flow tables, then the exact earnings8-K Exhibit99.1 or99.2. For quarterly capex, prioritize earnings presentations/slides with multi-quarter cash-flow reconciliations; the latest deck may fill several requested quarters. For annual capex actuals, also locate the issuer-hosted annual report/10-K and its explicit cash-flow or capex table; a quarter-only transcript is insufficient. "
                "Use at most two searches and six web actions. Return at most two URLs per requested period, six total. "
                "Return only issuer-hosted or sec.gov direct documents, never third-party numbers/snippets. Do not infer unavailable figures. "
                "Give exact reported fiscal_period (Q4 FY2025 format), period_end and actual publication date YYYY-MM-DD. "
                "The application independently fetches and verifies every URL; no numerical extraction is requested."
            ), _SCHEMA)
            candidates = located.get("sources", [])
        except Exception as exc:
            result["gaps"].append("Primary-source recovery could not locate the missing reported periods: " + str(exc)[:180])
            return result
    semaphore = asyncio.Semaphore(3)
    async def fetch(candidate: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        url = candidate.get("url", "")
        period = candidate.get("fiscal_period", "")
        check = {"url": url, "period": period, "status": "rejected"}
        try:
            published = datetime.strptime(candidate.get("published_at", ""), "%Y-%m-%d").date().isoformat()
            period_end = datetime.strptime(candidate.get("period_end", ""), "%Y-%m-%d").date().isoformat()
        except (TypeError, ValueError):
            return None, check | {"reason": "The source candidate lacks exact reporting and publication dates."}
        cutoff = str(research_as_of or event['earnings_date'])[:10]
        if period not in missing or url in existing or not period_end <= published <= cutoff or not _primary_release_url(url, company):
            return None, check | {"reason": "The candidate does not match a requested historical primary-source gap."}
        async with semaphore:
            page = await acquisition._fetch(url)
        historical_event = {"fiscal_period": period, "period_end": period_end, "earnings_date": published}
        # A quarter-only deck needs its event date independently grounded in
        # an already retained document or official SEC Item 2.02 metadata.
        known_event = any(item.get("fiscal_period") == period and str(item.get("published_at") or item.get("earnings_date") or "")[:10] == published for item in documents)
        known_event = known_event or any(row.get("form") == "8-K" and "2.02" in re.split(r"[,;\s]+", row.get("items", "")) and (row.get("period_end") or row.get("filed_at")) == published for row in company.get("filings", []))
        deck_period = presentation_period(page.content)
        reason = material_rejection(page, company, historical_event, allow_presentation_cover=known_event and deck_period == _fiscal_period(period))
        if reason:
            return None, check | {"status": "unavailable" if page.error else "rejected", "reason": reason}
        document = acquisition._document(page, title=page.title, kind="primary_trend_history", fiscal_period=period,
            period_end=period_end, published_at=published, provenance="issuer" if "sec.gov" not in url else "sec",
            verification="Fetched primary issuer, earnings period and publication date matched")
        redirect = presentation_redirect(page, company)
        if redirect:
            document.update({"provenance": "issuer_redirected_asset", "issuer_redirect": redirect})
        return document, check | {"status": document["status"], "source_id": document.get("source_id")}
    fetched = await asyncio.gather(*(fetch(candidate) for candidate in candidates[:6] if isinstance(candidate, dict)))
    for document, check in fetched:
        result["checks"].append(check)
        if document and document.get("status") == "available":
            result["documents"].append(document)
            recovered = primary_release_points(document, company, points.get("capex_cash_ppe", []), cash_sources=cash_sources)
            result["points"].extend(recovered)
            if not recovered:
                result["gaps"].append(f"{check['period']}: primary material was retrieved, but its numerical layout needs a supported parser; the figure is not classified as unreported.")
    recovered_periods = {(point["metric"], point["period"]) for point in result["points"]}
    for period, metrics in missing.items():
        for metric in metrics:
            observation_period = "FY" + period.split("FY")[-1] if metric in {"capex", "capex_cash_ppe"} else period
            if (metric, observation_period) not in recovered_periods:
                result["gaps"].append(f"{period} {metric}: primary-source recovery did not verify the reported value; this is an evidence gap, not a projection or zero.")
    _retain_currency_sources(result, documents, cash_sources)
    return result
