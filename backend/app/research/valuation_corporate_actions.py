"""Bounded, read-only corporate-action evidence for historical share bases."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from typing import Any, Mapping, Sequence

from ..schemas import ImportRequest
from .connectors import AlpacaConnector
from .earnings_forecast import number
from .earnings_sources import normalize_ticker


VERSION = "valuation-corporate-actions.v1"
ENDPOINT = "https://data.alpaca.markets/v1/corporate-actions"
TYPES = "forward_split,reverse_split,stock_dividend"
GROUPS = {"forward_splits": "forward_split", "reverse_splits": "reverse_split", "stock_dividends": "stock_dividend"}


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _events(pages: Sequence[Mapping[str, Any]], ticker: str) -> tuple[list[dict], list[str]]:
    events, issues = {}, []
    for page in pages:
        groups = page.get("corporate_actions")
        if not isinstance(groups, Mapping):
            issues.append("Corporate-action groups are missing.")
            continue
        for group, kind in GROUPS.items():
            rows = groups.get(group, [])
            if not isinstance(rows, list):
                issues.append(f"Invalid {kind} response.")
                continue
            for row in rows:
                if not isinstance(row, Mapping) or row.get("symbol") != ticker:
                    issues.append("A corporate-action record did not match the requested symbol.")
                    continue
                effective = _date(row.get("ex_date"))
                identifier = str(row.get("id") or "")
                if kind == "stock_dividend":
                    rate = number(row.get("rate"))
                    new_rate, old_rate = (Decimal(1) + rate if rate is not None else None), Decimal(1)
                else:
                    new_rate, old_rate = number(row.get("new_rate")), number(row.get("old_rate"))
                if not identifier or not effective or new_rate is None or old_rate is None or new_rate <= 0 or old_rate <= 0 or (kind == "stock_dividend" and rate <= 0):
                    issues.append("A corporate action lacked a valid ex-date or positive share ratio.")
                    continue
                event = {"id": identifier, "type": kind, "symbol": ticker, "date": effective.isoformat(), "ex_date": effective.isoformat(), "new_rate": str(new_rate), "old_rate": str(old_rate), "share_ratio": str(new_rate / old_rate), "process_date": row.get("process_date")}
                if identifier in events and events[identifier] != event:
                    issues.append("Conflicting corporate-action records were returned.")
                else:
                    events[identifier] = event
    return sorted(events.values(), key=lambda row: (row["date"], row["id"])), list(dict.fromkeys(issues))


def fetch_split_actions(connector: Any, ticker: str, *, start: str, end: str, max_pages: int = 3, timeout: float = 15) -> dict[str, Any]:
    """Use existing market credentials at one fixed public-data endpoint.

    Corporate-action filters use process dates; the returned ex-date determines
    the share-basis boundary. No account, trading, or credential mutation occurs.
    """
    ticker = normalize_ticker(ticker)
    if not _date(start) or not _date(end) or start > end or not 1 <= max_pages <= 3:
        raise ValueError("A valid bounded corporate-action date interval is required")
    envelope: dict[str, Any] = {"version": VERSION, "provider": "alpaca", "source_type": "corporate_actions", "request": {"endpoint": ENDPOINT, "symbol": ticker, "start": start, "end": end, "types": TYPES, "data_quality": "complete"}, "retrieved_at": datetime.now(timezone.utc).isoformat(), "status": "unavailable", "coverage_complete": False, "pages": [], "issues": []}
    credentials = connector.credentials
    if not credentials.alpaca_available:
        envelope["issues"] = ["Existing market-data credentials are unavailable."]
        return envelope
    params: dict[str, Any] = {"symbols": ticker, "types": TYPES, "start": start, "end": end, "limit": 1000, "data_quality": "complete"}
    tokens = set()
    for _ in range(max_pages):
        try:
            response = connector.http_get(ENDPOINT, params, {"APCA-API-KEY-ID": credentials.alpaca_api_key_id, "APCA-API-SECRET-KEY": credentials.alpaca_api_secret_key, "Accept": "application/json"}, timeout)
        except Exception:
            envelope["issues"].append("Corporate-action retrieval failed.")
            break
        if response.status_code != 200 or not isinstance(response.payload, Mapping):
            envelope["issues"].append("Corporate-action coverage was not returned by the configured provider.")
            break
        page = response.payload
        if not isinstance(page.get("corporate_actions"), Mapping):
            envelope["issues"].append("Corporate-action response was malformed.")
            break
        # Retain the public response needed to independently repeat parsing.
        envelope["pages"].append({"corporate_actions": dict(page["corporate_actions"]), "next_page_token": page.get("next_page_token")})
        token = page.get("next_page_token")
        if not token:
            envelope["coverage_complete"] = True
            break
        if not isinstance(token, str) or len(token) > 2000 or token in tokens:
            envelope["issues"].append("Corporate-action pagination was invalid or repeated.")
            break
        tokens.add(token)
        params = params | {"page_token": token}
    _, issues = _events(envelope["pages"], ticker)
    envelope["issues"].extend(issues)
    if envelope["issues"]:
        envelope["coverage_complete"] = False
    if not envelope["coverage_complete"] and envelope["pages"] and not envelope["issues"]:
        envelope["issues"].append("Corporate-action pagination exceeded the bounded request limit.")
    envelope["status"] = "complete" if envelope["coverage_complete"] else "partial" if envelope["pages"] else "unavailable"
    return envelope


def split_coverage_from_sources(sources: Sequence[Mapping[str, Any]], *, ticker: str, start: str, end: str) -> dict[str, Any]:
    """Reparse a retained response; provider-authored projections are ignored."""
    candidates = []
    for source in sources:
        if source.get("url") != ENDPOINT or not source.get("id"):
            continue
        content = source.get("content", source.get("original_content"))
        if not isinstance(content, str) or hashlib.sha256(content.encode()).hexdigest() != source.get("content_hash"):
            continue
        try:
            envelope = json.loads(content)
        except ValueError:
            continue
        if not isinstance(envelope, Mapping):
            continue
        request = envelope.get("request") or {}
        pages = envelope.get("pages")
        if envelope.get("version") != VERSION or envelope.get("provider") != "alpaca" or envelope.get("source_type") != "corporate_actions" or request.get("endpoint") != ENDPOINT or request.get("symbol") != ticker or request.get("types") != TYPES or request.get("data_quality") != "complete" or not isinstance(pages, list) or not 1 <= len(pages) <= 3 or not all(isinstance(page, Mapping) for page in pages):
            continue
        if not _date(request.get("start")) or not _date(request.get("end")):
            continue
        events, issues = _events(pages, ticker)
        complete = (envelope.get("status") == "complete" and envelope.get("coverage_complete") is True and not envelope.get("issues") and not issues and not pages[-1].get("next_page_token") and request["start"] <= start and request["end"] >= end)
        refs = [source["id"]]
        candidates.append({"status": "complete" if complete else "partial", "coverage_complete": complete, "events": [row | {"source_refs": refs} for row in events], "source_refs": refs, "start": request["start"], "end": request["end"], "retrieved_at": envelope.get("retrieved_at"), "issues": list(envelope.get("issues") or []) + issues})
    if not candidates:
        return {"status": "unverified", "coverage_complete": False, "events": [], "source_refs": [], "issues": ["No complete retained split and stock-dividend coverage is available; conspicuous raw-price discontinuities are excluded conservatively."]}
    return max(candidates, key=lambda row: (row["coverage_complete"], str(row.get("retrieved_at") or "")))


async def acquire_valuation_corporate_actions(repo: Any, config: Any, ticker: str, *, namespace: str, run_id: str | None = None) -> list[str]:
    """Archive seven years: five years of prices plus EPS-component lookback."""
    if not getattr(config, "enable_market_connectors", False):
        return []
    ticker = normalize_ticker(ticker)
    now = datetime.now(timezone.utc)
    start, end = (now - timedelta(days=7 * 366 + 14)).date().isoformat(), now.date().isoformat()
    title = f"{ticker} · historical split and stock-dividend coverage"
    with repo.db.operation() as conn:
        cached = conn.execute("SELECT id,url,original_content,content_hash,retrieval_at FROM sources WHERE namespace=? AND title=? ORDER BY retrieval_at DESC LIMIT 1", (namespace, title)).fetchone()
    coverage = split_coverage_from_sources([dict(cached)] if cached else [], ticker=ticker, start=start, end=end)
    if cached and str(cached["retrieval_at"] or "")[:10] == end and coverage["coverage_complete"]:
        identifiers = [cached["id"]]
    else:
        connector = AlpacaConnector(project_root=config.project_root, data_dir=config.data_dir)
        envelope = await asyncio.to_thread(fetch_split_actions, connector, ticker, start=start, end=end)
        if envelope["status"] == "unavailable":
            identifiers = []
        else:
            content = json.dumps(envelope, ensure_ascii=False, sort_keys=True, indent=2)
            try:
                saved = repo.import_evidence(ImportRequest(namespace=namespace, kind="evidence", title=title, content=content, source_url=ENDPOINT, observed_at=envelope["retrieved_at"], idempotency_key="valuation-actions:" + hashlib.sha256(content.encode()).hexdigest()))
                identifiers = [saved["source_id"]] if saved.get("source_id") else []
            except (ValueError, OSError):
                identifiers = []
    if run_id and identifiers:
        repo.append_run_sources(run_id, identifiers, reason="Retained split and stock-dividend evidence binds historical EPS and raw price share bases.")
    elif run_id:
        repo.emit(namespace, "valuation_history_gap", run_id=run_id, payload={"message": "Split-event coverage is unverified; historical P/E retains a share-basis limitation and conservative discontinuity exclusions."})
    return identifiers
