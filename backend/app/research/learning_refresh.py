"""Bounded price-only paper observations in the existing local watch loop."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from .connectors import AlpacaConnector
from .learning import LearningJournal, OutcomeRequest, _time


async def refresh_paper_outcomes(repo: Any, config: Any, namespace: str, *, connector: Any = None, limit: int = 3) -> dict[str, Any]:
    if repo.firm_paused() or not config.enable_market_connectors:
        return {"checked":0,"paused":repo.firm_paused(),"reason":"Paper price refresh follows the local monitor and market connector settings."}
    journal = LearningJournal(repo)
    items = journal.list(namespace)["items"]
    clock = datetime.now(timezone.utc).replace(microsecond=0)
    # Rotate by last observation so unavailable/old ideas cannot starve the
    # rest of the journal. No model call, prediction or trade is involved.
    items.sort(key=lambda item:max((o.get("observed_at","") for o in item["observations"]),default=""))
    checked, failures = 0, []
    market = connector or AlpacaConnector(project_root=config.project_root,data_dir=config.data_dir)
    cache: dict[tuple[str,str],str | None] = {}
    identities: dict[str,dict[str,Any] | None] = {}
    for item in items:
        if checked >= limit or repo.firm_paused():
            break
        observations = item.get("observations") or []
        if any((_time(o.get("observed_at")) or clock-timedelta(days=2)).date() == clock.date() for o in observations):
            continue
        anchor = item.get("reference_quote")
        if not anchor and observations:
            continue  # A missing frozen anchor can never be repaired later.
        start = _time((anchor or {}).get("as_of"))
        refs = []
        errors = []
        if start:
            for ticker in dict.fromkeys([item["ticker"],item.get("benchmark_ticker")]):
                if not ticker or repo.firm_paused():
                    continue
                key = (ticker,start.isoformat())
                if key not in cache:
                    try:
                        expected = item.get("instrument_identity" if ticker == item["ticker"] else "benchmark_identity") or {}
                        if not expected.get("asset_id") or not expected.get("exchange"):
                            raise ValueError("Missing original asset identity")
                        if ticker not in identities:
                            if repo.firm_paused():
                                break
                            identities[ticker] = await asyncio.to_thread(market.fetch_asset_identity,ticker,timeout=15)
                        identity = identities[ticker] or {}
                        observed = _time(identity.get("observed_at") or identity.get("retrieved_at"))
                        if identity.get("asset_id") != expected["asset_id"] or identity.get("exchange") != expected["exchange"] or identity.get("symbol") != ticker or identity.get("status") != "active" or not observed or observed > datetime.now(timezone.utc) or clock-observed > timedelta(days=1):
                            raise ValueError("Asset identity changed or could not be verified")
                        if repo.firm_paused():
                            break
                        result = await asyncio.to_thread(market.fetch_bars,ticker,"daily",start=start,end=clock,limit=1000,max_pages=5,max_bars=5000,timeout=15,retries=0,feed=config.alpaca_data_feed,adjustment="split",include_technicals=False)
                        if result.capability == "ready" and result.bars and not repo.firm_paused():
                            result.metadata["instrument_identity"] = {field:identity.get(field) for field in ("asset_id","exchange","symbol","status","observed_at","retrieved_at")}
                            saved = repo.import_evidence(result.as_import_request(namespace=namespace,title=f"{ticker} paper outcome observations"))
                            cache[key] = saved.get("source_id")
                        else:
                            cache[key] = None
                            errors.append(f"{ticker}: completed daily data unavailable")
                    except Exception as exc:
                        cache[key] = None
                        errors.append(f"{ticker}: refresh unavailable ({type(exc).__name__})")
                if cache[key]:
                    refs.append(cache[key])
        if repo.firm_paused():
            break
        try:
            journal.record(item["id"],OutcomeRequest(namespace=namespace,idempotency_key="daily-price:"+clock.date().isoformat(),evaluation_at=clock.isoformat(),source_ids=refs,review_note="Automatic price-only observation. Thesis and catalyst remain unassessed."+(" " + "; ".join(errors) if errors else "")))
            checked += 1
        except ValueError as exc:
            failures.append({"baseline_id":item["id"],"reason":str(exc)})
    return {"checked":checked,"failures":failures,"paused":repo.firm_paused()}
