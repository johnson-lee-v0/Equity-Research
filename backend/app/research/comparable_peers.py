"""Bounded peer discovery and deterministic, dated comparable ratios."""
from __future__ import annotations

from datetime import date
import json
import re
from urllib.parse import urlsplit

from ..db import json_loads, utc_now
from ..providers.base import ProviderError
from .source_archive import archive_public_observation

PEER_RECEIPT_VERSION = "comparable-peer-preparation.v1"


def record_peer_receipt(repo, package, run_id, *, status, source_ids=(), gaps=(), discovery=None):
    """Retain an optional preparation result independently of core analysis."""
    return repo.emit(package["namespace"], "comparable_peers_checked", run_id=run_id, payload={
        "version": PEER_RECEIPT_VERSION, "ticker": package["ticker"],
        "cik": str(package["package"]["company"].get("cik") or ""),
        "status": status, "checked_at": utc_now(), "source_ids": list(dict.fromkeys(source_ids)),
        "gaps": [str(gap)[:400] for gap in gaps][:4],
        "discovery": {key: discovery[key] for key in ("attempt_id", "stage", "status", "record_path", "limits", "error_kind", "started_at", "finished_at") if key in (discovery or {})},
    })


def _saved_peer_receipt(repo, package, run_id):
    after, receipt = 0, None
    # The public event reader is paginated. A long provider trace must not
    # hide the terminal receipt and trigger another optional discovery call.
    while True:
        events = repo.events(package["namespace"], after=after, run_id=run_id, limit=5000)
        for event in events:
            payload = event.get("payload") or {}
            if (event.get("type") == "comparable_peers_checked" and payload.get("version") == PEER_RECEIPT_VERSION
                    and payload.get("ticker") == package["ticker"]
                    and payload.get("cik") == str(package["package"]["company"].get("cik") or "")
                    and payload.get("status") in {"completed", "partial", "failed"}):
                receipt = payload
        if len(events) < 5000:
            return receipt
        after = events[-1]["sequence_id"]


async def acquire_peers(repo, acquisition, package, run_id, config) -> list[str]:
    """A maximum of three candidates; numbers come only from archived data."""
    if not getattr(config, "enable_market_connectors", False):
        return []
    receipt = _saved_peer_receipt(repo, package, run_id)
    if receipt is not None:
        snapshot = json_loads(repo.run_record(run_id)["input_snapshot_json"], {})
        retained = {row["id"] for row in snapshot.get("source_versions", []) if isinstance(row, dict) and row.get("id")}
        # A receipt cannot attach an unrelated or no-longer-retained source.
        return [sid for sid in receipt.get("source_ids", []) if sid in retained]
    from .assessment_pipeline import _acquire_comparable_financials, acquire_valuation_price_history
    company = package["package"]["company"]
    schema = {"type": "object", "additionalProperties": False, "required": ["peers"], "properties": {"peers": {"type": "array", "maxItems": 3, "items": {"type": "object", "additionalProperties": False,
        "required": ["ticker", "rationale"], "properties": {"ticker": {"type": "string"}, "rationale": {"type": "string"}}}}}}
    ids, gaps = [], []
    discovery_start = len(getattr(acquisition, "discovery_records", []))
    def discovery_record():
        records = getattr(acquisition, "discovery_records", [])[discovery_start:]
        return next((row for row in reversed(records) if row.get("stage") == "valuation_peers"), None)
    try:
        if acquisition.dispatch_guard:
            acquisition.dispatch_guard()
        candidates = await acquisition._discover("valuation_peers", f"Find at most three publicly traded US SEC-reporting peers for {company['name']} ({package['ticker']}). Compare business mix, revenue model, margins and asset intensity; explain each fit and major limitation. Exclude funds and the subject ticker. Return ticker candidates only, no prices or valuation numbers. Empty peers is valid when no meaningful comparison exists.", schema)
    except (ValueError, OSError, ProviderError) as exc:
        message = "Optional peer discovery was unavailable: " + str(exc)[:340]
        repo.emit(package["namespace"], "comparable_peer_gap", run_id=run_id, payload={"message": message, "error_kind": getattr(exc, "kind", type(exc).__name__), "discovery": discovery_record()})
        record_peer_receipt(repo, package, run_id, status="failed", gaps=[message], discovery=discovery_record())
        return []
    seen = {package["ticker"]}
    for item in candidates.get("peers", [])[:3]:
        ticker = str(item.get("ticker") or "").strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}", ticker) or ticker in seen:
            continue
        seen.add(ticker)
        try:
            if acquisition.dispatch_guard:
                acquisition.dispatch_guard()
            peer = await acquisition.resolve(ticker)
            if peer.get("earnings_applicability") == "not_applicable":
                continue
            _, identity_page = await acquisition._json(peer["submissions_url"])
            saved = archive_public_observation(repo, identity_page, namespace=package["namespace"], scope=run_id)
            if not saved.get("source_id"):
                raise ValueError("Peer issuer identity could not be archived.")
            peer_ids = [saved["source_id"]]
            peer_ids += await _acquire_comparable_financials(repo, acquisition, package, run_id, peer["cik"])
            peer_ids += await acquire_valuation_price_history(repo, config, ticker, namespace=package["namespace"], run_id=run_id)
            repo.append_run_sources(run_id, peer_ids, reason=f"Dated primary financial and price sources retained for comparable issuer {ticker}.")
            repo.emit(package["namespace"], "comparable_peer_acquired", run_id=run_id, payload={"ticker": ticker, "rationale": str(item.get("rationale") or "")[:1000], "source_ids": peer_ids})
            ids.extend(peer_ids)
        except (ValueError, OSError, ProviderError) as exc:
            message = f"Optional peer {ticker} was unavailable: " + str(exc)[:320]
            gaps.append(message)
            repo.emit(package["namespace"], "comparable_peer_gap", run_id=run_id, payload={"ticker": ticker, "message": message, "error_kind": getattr(exc, "kind", type(exc).__name__)})
    record_peer_receipt(repo, package, run_id, status="partial" if gaps else "completed", source_ids=ids, gaps=gaps,
                        discovery=discovery_record() or candidates.get("_discovery"))
    return list(dict.fromkeys(ids))


def compile_peer_comparisons(sources, *, ticker, as_of):
    from .comparable_history import build_comparable_histories
    from .valuation_history import _source_content, raw_price_history
    from .valuation_corporate_actions import split_coverage_from_sources
    from datetime import timedelta

    peers = []
    seen = set()
    for identity in sources:
        raw = _source_content(identity)
        if not raw:
            continue
        try:
            data = json.loads(raw)
            cik = int(data["cik"])
            url = urlsplit(identity.get("url") or "")
            tickers = data["tickers"]
        except (ValueError, KeyError, TypeError):
            continue
        if url.scheme != "https" or url.hostname != "data.sec.gov" or url.path != f"/submissions/CIK{cik:010d}.json" or not isinstance(tickers, list):
            continue
        if len(tickers) != 1:
            # A price for one class cannot certify total company market cap.
            # Dual-class/ADR issuers need an explicit share conversion bridge.
            continue
        for peer in tickers:
            if peer == ticker or peer in seen or not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}", str(peer)):
                continue
            prices, _ = raw_price_history(sources, ticker=peer, as_of=as_of)
            if not prices:
                continue
            seen.add(peer)
            latest_price = max(prices, key=lambda row: row["date"])
            if (date.fromisoformat(as_of[:10]) - date.fromisoformat(latest_price["date"])).days > 7:
                continue
            # Scope financial archives by exact CIK, and require the issuer
            # name in submissions to match the financial endpoint's entity.
            company_sources = []
            normal = lambda text: re.sub(r"[^a-z0-9]", "", str(text).casefold())
            for source in sources:
                if not _source_content(source):
                    continue
                try:
                    financial = json.loads(source["content"])
                    if isinstance(financial, dict) and financial.get("cik") == cik and normal(financial.get("entityName")) == normal(data.get("name")):
                        company_sources.append(source)
                except (ValueError, TypeError):
                    continue
            end = date.fromisoformat(as_of[:10])
            split_coverage = split_coverage_from_sources(sources, ticker=peer, start=(end - timedelta(days=7 * 366)).isoformat(), end=end.isoformat())
            # Do not fill a peer ratio across a conspicuous raw-price jump.
            jumps = [{"date": right["date"]} for left, right in zip(prices, prices[1:]) if 0 < (date.fromisoformat(right["date"]) - date.fromisoformat(left["date"])).days <= 10 and (float(right["close"]) / float(left["close"]) < .6 or float(right["close"]) / float(left["close"]) > 1.4)]
            histories = build_comparable_histories(company_sources, [latest_price], ticker=peer, cik=str(cik), as_of=as_of, split_events=[*split_coverage["events"], *jumps])
            for metric, series in histories.items():
                if not series["points"]:
                    continue
                point = series["points"][-1]
                peers.append({"ticker": peer, "issuer_name": data.get("name"), "metric": "P/NAV" if metric == "P/book" else metric,
                    "nav_basis": "book_equity" if metric == "P/book" else None, "basis": "trailing", "as_of": point["date"],
                    "multiple": point["multiple"], "ebitda_basis": point.get("ebitda_basis"), "numerator_value": point["numerator_value"], "denominator_value": point["denominator_value"], "components": point["components"],
                    "rationale": "Candidate peer with separately verified issuer, financial and price records. The investment review must assess business mix, margins, leverage and asset quality before using this comparison.",
                    "source_refs": list(dict.fromkeys([identity["id"], *point["source_refs"]])),
                    "share_basis_coverage": "complete" if split_coverage["coverage_complete"] else "Incomplete split-event coverage; conspicuous price jumps excluded."})
    return peers
