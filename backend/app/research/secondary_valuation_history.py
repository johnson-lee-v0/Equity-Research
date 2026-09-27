"""Dated provider ratios, kept separate from code-verified SEC calculations.

These are an explanatory market comparison, never executable valuation facts
or point-in-time backtest inputs. One user-requested refresh retains a private
immutable page; it cannot amend any source used by an earlier decision.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
import re
from statistics import median
from urllib.robotparser import RobotFileParser

from .earnings_forecast import display, number
from .source_archive import archive_public_observation
from .valuation_history import _source_content, multiple_statistics

VERSION = "secondary-valuation-history.v1"
LABELS = {"PS Ratio": "P/S", "EV/EBITDA Ratio": "EV/EBITDA", "PB Ratio": "P/book", "P/TBV Ratio": "P/tangible book"}
DATE_PATTERN = re.compile(r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{1,2}, \d{4}\b")


def history_url(ticker: str) -> str:
    if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}", ticker):
        raise ValueError("Use a valid US-listed ticker.")
    return f"https://stockanalysis.com/stocks/{ticker.lower()}/financials/ratios/?p=quarterly"


def parse_history(source: dict, *, ticker: str, as_of: str) -> dict:
    """Reject shifted columns, mismatched issuers and future archive vintages."""
    content = _source_content(source)
    retrieved = str(source.get("retrieval_at") or source.get("retrieved_at") or "")
    if not content or source.get("url") != history_url(ticker) or not retrieved:
        return {}
    try:
        cutoff = date.fromisoformat(as_of[:10])
        retrieved_date = date.fromisoformat(retrieved[:10])
    except ValueError:
        return {}
    try:
        future_vintage = len(as_of) > 10 and datetime.fromisoformat(retrieved.replace("Z", "+00:00")) > datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return {}
    if retrieved_date > cutoff or future_vintage:
        return {}
    # Require both the issuer heading and the US listing/currency banner.
    if not re.search(r"\(" + re.escape(ticker) + r"\)\s*\n(?:NASDAQ|NYSE|NYSEARCA|NYSEAMERICAN|CBOE): " + re.escape(ticker) + r"\b[^\n]*USD", content):
        return {}
    lines = content.splitlines()
    headers = [lines[index + 1].strip() for index, line in enumerate(lines[:-1]) if line.strip() == "Fiscal Quarter"]
    date_rows = [DATE_PATTERN.findall(lines[index + 1]) for index, line in enumerate(lines[:-1]) if line.strip() == "Period Ending"]
    if not headers or not date_rows or any(row != headers[0] for row in headers) or any(row != date_rows[0] for row in date_rows):
        return {}
    periods = re.findall(r"Current|Q[1-4] \d{4}", headers[0])
    if " ".join(periods) != headers[0] or not 2 <= len(periods) <= 24 or len(periods) != len(date_rows[0]):
        return {}
    try:
        dates = [datetime.strptime(value, "%b %d, %Y").date() for value in date_rows[0]]
    except ValueError:
        return {}
    if any(left <= right for left, right in zip(dates, dates[1:])) or dates[0] > retrieved_date:
        return {}
    start = cutoff - timedelta(days=round(365.25 * 5))
    result = {}
    for row_name, metric in LABELS.items():
        candidates = [(index, lines[index + 1].split()) for index, line in enumerate(lines[:-1]) if line.strip() == row_name]
        if len(candidates) != 1 or len(candidates[0][1]) != len(dates):
            continue
        index, cells = candidates[0]
        points, gaps = [], []
        for period, day, cell in zip(periods, dates, cells):
            if not start <= day <= cutoff:
                continue
            value = number(cell.replace(",", "")) if re.fullmatch(r"-?\d[\d,]*(?:\.\d+)?", cell) else None
            if value is None or value <= 0:
                gaps.append({"date": day.isoformat(), "reason": "The provider did not report a positive multiple for this period."})
                continue
            points.append({"date": day.isoformat(), "multiple": display(value), "source_refs": [source["id"]],
                "source_url": source["url"], "source_locator": f"L{index + 1}-L{index + 2}",
                "period_label": period, "provider_value": cell, "retrieved_at": retrieved})
        points.sort(key=lambda row: row["date"])
        values = [number(row["multiple"]) for row in points]
        basis = "Provider-computed quarter-end ratios and latest snapshot; historical financials may be restated."
        if metric in {"P/book", "P/tangible book"}:
            basis += " Accounting equity, not appraised NAV."
        result[metric] = {"metric": metric, "status": "available" if points else "unavailable", "points": points,
            "basis": basis, "provider": "Stock Analysis", "provenance": "secondary_provider", "sampling": "quarterly",
            "as_of": retrieved, "source_url": source["url"], "source_refs": [source["id"]],
            "window_start": start.isoformat(), "window_end": cutoff.isoformat(), "requested_years": 5,
            "min": display(min(values)) if values else None, "max": display(max(values)) if values else None,
            "median": display(median(values)) if values else None, **multiple_statistics(values), "gaps": gaps,
            "coverage_note": "Stock Analysis supplies these dated ratios. They are separate from SEC-derived reported-as-of history and cannot fill its gaps or certify a target's accounting inputs. Quarter-end labels are the provider's valuation dates, not the date the financial results became public. Historical values may reflect restatements. This is not point-in-time backtest data; no missing debt or minority interests are assumed zero. P/book and P/tangible book do not establish appraised NAV."}
    return result


def compile_provider_histories(sources: list[dict], *, ticker: str, as_of: str) -> dict:
    for source in sorted(sources, key=lambda row: str(row.get("retrieval_at") or row.get("retrieved_at") or ""), reverse=True):
        if result := parse_history(source, ticker=ticker, as_of=as_of):
            return result
    return {}


def saved_history(repo, *, ticker: str, namespace: str, as_of: str) -> dict:
    url = history_url(ticker)
    with repo.db.operation() as conn:
        rows = [dict(row) for row in conn.execute("SELECT id,url,title,original_content,content_hash,retrieval_at FROM sources WHERE namespace=? AND url=? ORDER BY retrieval_at DESC LIMIT 8", (namespace, url))]
    histories = compile_provider_histories(rows, ticker=ticker, as_of=as_of)
    return {"version": VERSION, "ticker": ticker, "namespace": namespace, "as_of": as_of,
            "status": "available" if histories else "unavailable", "provider_multiples": histories,
            "source_url": url, "decision_unchanged": True}


async def refresh_history(repo, acquisition, *, ticker: str, namespace: str, as_of: str, run_id: str | None = None) -> dict:
    saved = saved_history(repo, ticker=ticker, namespace=namespace, as_of=as_of)
    current = next(iter(saved["provider_multiples"].values()), {})
    if str(current.get("as_of") or "")[:10] == as_of[:10]:
        if run_id:
            repo.append_run_sources(run_id, current["source_refs"], reason="Same-day provider multiple history reused as secondary context.")
        return saved
    # The provider permits AI agents that honor robots. Never bypass a denial,
    # login, bot challenge or changed column layout to obtain the optional data.
    robots = await acquisition._fetch("https://stockanalysis.com/robots.txt")
    if robots.error or not robots.content:
        raise ValueError("The ratio provider's automated-access policy could not be checked.")
    policy = RobotFileParser()
    policy.parse(robots.content.splitlines())
    url = history_url(ticker)
    from .discovery import _source_user_agent
    if not policy.can_fetch(_source_user_agent("stockanalysis.com"), url):
        raise ValueError("The ratio provider does not allow automated access to this page.")
    page = await acquisition._fetch(url)
    import hashlib
    preview = {"id": "preview", "content": page.content, "content_hash": hashlib.sha256(page.content.encode()).hexdigest(), "url": page.final_url, "retrieval_at": page.retrieved_at}
    # The request's start timestamp precedes the page receipt. Validate the
    # returned page at its own receipt, then retain that timestamp as evidence.
    if page.error or not parse_history(preview, ticker=ticker, as_of=page.retrieved_at):
        raise ValueError("The provider did not return a matching, dated ratio table; prior saved history remains available.")
    result = await asyncio.to_thread(archive_public_observation, repo, page, namespace=namespace, scope=f"valuation-history:{ticker}:{as_of[:10]}")
    if not result.get("source_id"):
        raise ValueError(result.get("reason") or "Provider history could not be archived.")
    if run_id:
        repo.append_run_sources(run_id, [result["source_id"]], reason="Provider-computed ratio history retained separately from primary valuation operands.")
    return saved_history(repo, ticker=ticker, namespace=namespace, as_of=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
