"""Bounded public-document refresh; the watch owner admits any research."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from .discovery import fetch_public_pages
from .source_archive import archive_public_page


async def refresh_watch_sources(repo: Any, namespace: str, run_id: str, source_ids: list[str]) -> dict[str,Any]:
    checked_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    if repo.firm_paused():
        return {"status":"paused","checked_at":checked_at,"sources":[],"refresh_run_ids":[]}
    urls=[]
    try:
        heads=repo.source_head_ids(namespace,source_ids)
    except ValueError:
        return {"status":"unavailable","checked_at":checked_at,"sources":[],"refresh_run_ids":[],"reason":"Linked evidence is missing or outside this workspace; a research review must repair the references."}
    for source_id in heads:
        sources=repo.sources(namespace,source_id)
        if not sources:
            continue
        source=sources[0]
        url=source.get("url")
        if not url:
            continue
        host=(urlsplit(url).hostname or "").casefold()
        # Provider observations and Reddit edits have their own authenticated
        # connectors/screening path, and are not generic public HTML pages.
        if source.get("source_type") in {"market_data","market_bars","asset_identity","reddit","reddit_submission","csv"} or host == "reddit.com" or host.endswith(".reddit.com") or host == "alpaca.markets" or host.endswith(".alpaca.markets"):
            continue
        if url not in urls:
            urls.append(url)
        if len(urls) == 2:
            break
    if not urls:
        return {"status":"unavailable","checked_at":checked_at,"sources":[],"refresh_run_ids":[],"reason":"No eligible public document is linked; connector data and Reddit posts use their dedicated collection paths."}
    pages=await asyncio.to_thread(fetch_public_pages,urls,max_sources=2,max_bytes=repo.config.max_source_bytes,timeout=15)
    results=[]
    refresh_runs=[]
    for page in pages:
        if repo.firm_paused():
            return {"status":"paused","checked_at":checked_at,"sources":results,"refresh_run_ids":refresh_runs,"reason":"Pause stopped source refresh before further archival or dispatch."}
        saved=archive_public_page(repo,page,namespace=namespace,scope="watch:"+run_id,defer_run_id=run_id)
        results.append({"url":page.final_url,"status":saved["status"],"source_id":saved.get("source_id"),"retained_source_id":saved.get("retained_source_id"),"reason":saved.get("reason")})
        refresh_runs.extend(saved.get("refresh_run_ids") or [])
    changed=any(result["status"] in {"updated","archived"} for result in results)
    unavailable=not results or any(result["status"] == "unavailable" for result in results)
    return {"status":"partial" if changed and unavailable else "updated" if changed else "unavailable" if unavailable else "unchanged","changed":changed,"checked_at":checked_at,"sources":results,"refresh_run_ids":list(dict.fromkeys(refresh_runs)),"reason":"Retrieved documents retain their original observation dates; archival does not itself verify a claim."}
