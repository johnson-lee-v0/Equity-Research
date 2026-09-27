"""Bounded issuer news and current-report coverage after a reporting period.

An earnings package is a point-in-time event. This separate prerequisite
checks what happened after the latest reported period through this case's
cutoff, including non-earnings releases and 8-K/6-K events. Discovery only
suggests documents; primary origin, dates and excerpts are checked on fetch.
"""
from __future__ import annotations

from datetime import date, timedelta
import hashlib

from ..db import json_dumps, json_loads, utc_now
from .earnings_sources import EarningsAcquisition, _company_present, _date_present, _primary_release_url, _schema, company_with_verified_issuer_domain
from .investment_process import dispatch_guard, ProcessPaused, STAGES
from .source_archive import archive_public_observation

VERSION = "interim-events.v1"
SCHEMA = _schema({
    "events": {"type": "array", "maxItems": 6, "items": _schema({
        "url": {"type": "string"}, "title": {"type": "string"},
        "published_at": {"type": "string"}, "event_date": {"type": "string"},
        "kind": {"type": "string", "enum": ["press_release", "company_event"]},
        "quote": {"type": "string"},
    })},
    "coverage_gap": {"type": "string"},
})


def _date(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def _save(repo, run_id, receipt):
    with repo.db.transaction(immediate=True) as conn:
        row = conn.execute("SELECT namespace,input_snapshot_json FROM runs WHERE id=?", (run_id,)).fetchone()
        snapshot = json_loads(row["input_snapshot_json"], {})
        process = snapshot.setdefault("investment_process", {"version": "investment-process.v1", "stages": STAGES, "earnings": []})
        process["interim_events"] = [item for item in process.get("interim_events", []) if item.get("ticker") != receipt["ticker"]] + [receipt]
        conn.execute("UPDATE runs SET input_snapshot_json=?,updated_at=? WHERE id=?", (json_dumps(snapshot), utc_now(), run_id))
        repo.db.emit(conn, namespace=row["namespace"], run_id=run_id, event_type="interim_events_checked", payload={"ticker": receipt["ticker"], "status": receipt["status"], "source_ids": receipt.get("source_ids", []), "message": "Press release and company-event coverage retained with its cutoff and gaps."})


async def collect_interim_events(acquisition, company, *, cutoff: str, run_id: str):
    """Independently injectable collection; never treat no matches as no events."""
    end = _date(cutoff)
    if not end:
        raise ValueError("A valid research cutoff is required for interim-event coverage.")
    filings = [row for row in company.get("filings", []) if row.get("form") in {"10-K", "10-Q", "20-F", "40-F"}
               and _date(row.get("filed_at")) and _date(row["filed_at"]) <= end]
    latest = max(filings, key=lambda row: row["filed_at"], default=None)
    start = _date(latest.get("period_end")) if latest else None
    gaps = []
    if not start or start > end:
        start = end - timedelta(days=180)
        gaps.append("The latest periodic filing's covered period was unavailable; this bounded search covers the prior 180 days.")
    if (end - start).days > 400:
        start = end - timedelta(days=400)
        gaps.append("The last periodic report is old; coverage is bounded to 400 days and earlier intervening events remain unreviewed.")
    events, checks = [], []
    current_reports = [row for row in company.get("filings", []) if row.get("form") in {"8-K", "8-K/A", "6-K"}
                       and _date(row.get("filed_at")) and start <= _date(row["filed_at"]) <= end]
    current_reports.sort(key=lambda row: row["filed_at"], reverse=True)
    if len(current_reports) > 8:
        gaps.append(f"{len(current_reports)} current reports fall in the window; only the latest eight were fetched.")
    candidates = []
    for row in current_reports[:8]:
        if not row.get("accession") or not row.get("primary_document"):
            gaps.append("A SEC current-report row lacked its document locator.")
            continue
        candidates.append({"url": f"https://www.sec.gov/Archives/edgar/data/{int(company['cik'])}/{row['accession'].replace('-', '')}/{row['primary_document']}",
                           "title": f"{company['ticker']} {row['form']} filed {row['filed_at']}", "kind": "current_report",
                           "published_at": row["filed_at"], "event_date": row.get("period_end") or "", "quote": "", "sec_row": row})
    search_status = "completed"
    try:
        located = await acquisition._discover("interim_events", (
            f"As of {end.isoformat()}, find material issuer-authored PRESS RELEASES and company EVENT updates for "
            f"{company['name']} ({company['ticker']}, CIK {company['cik']}) published from {start.isoformat()} through {end.isoformat()} inclusive. "
            f"Verified company sites: {company.get('website', '')} {company.get('investor_website', '')}. "
            "Cover developments BETWEEN periodic filings, not only the latest earnings: guidance changes, monthly operating updates, "
            "acquisitions/divestitures, financing and capital returns, leadership, litigation/regulatory developments and investor-day updates. "
            "Return at most six direct issuer/SEC document URLs with exact material quotes and explicit publication dates. "
            "Do not return news-site interpretations or undated index/calendar pages. Distinguish publication date from the underlying event date; "
            "use empty event_date when the source does not establish it. Exclude planned events without a published substantive update. "
            "Preserve unavailable sections or incomplete search as coverage_gap. No matches is not proof that nothing happened."
        ), SCHEMA)
        candidates.extend([item for item in located.get("events", [])[:6] if isinstance(item, dict)])
        if located.get("coverage_gap"):
            gaps.append(str(located["coverage_gap"])[:2000])
    except ProcessPaused:
        raise
    except Exception as exc:
        search_status = "unavailable"
        gaps.append("Issuer press-release/event discovery was unavailable: " + str(exc)[:400])
    seen = set()
    for candidate in candidates:
        if acquisition.dispatch_guard:
            acquisition.dispatch_guard()
        url = str(candidate.get("url") or "")
        if url in seen:
            continue
        seen.add(url)
        published = _date(candidate.get("published_at"))
        event_date = _date(candidate.get("event_date"))
        reason = None
        if not published or not start <= published <= end:
            reason = "The publication date was missing or outside the research window."
        elif not _primary_release_url(url, company):
            reason = "The URL is not on the verified issuer or SEC domain."
        if reason:
            checks.append({"url": url, "status": "rejected", "reason": reason})
            continue
        page = await acquisition._fetch(url)
        header = page.title + "\n" + page.content[:8000]
        if page.error or not _primary_release_url(page.final_url, company):
            reason = page.error or "The page redirected away from the primary source."
        elif not _company_present(header, company) or len(page.content) < 150:
            reason = "The fetched document did not establish issuer identity and readable event content."
        elif not candidate.get("sec_row") and not _date_present(header, published.isoformat()):
            reason = "Publication date could not be matched to the fetched document header."
        elif candidate.get("quote") and candidate["quote"] not in page.content:
            reason = "The proposed event excerpt was not present in the fetched document."
        elif not candidate.get("sec_row") and len(str(candidate.get("quote", ""))) < 20:
            reason = "The event lacked a meaningful exact source excerpt."
        if event_date and (event_date > end or not _date_present(page.content, event_date.isoformat())):
            event_date = None
        if reason:
            checks.append({"url": url, "status": "rejected", "reason": reason})
            continue
        if acquisition.dispatch_guard:
            acquisition.dispatch_guard()
        saved = archive_public_observation(acquisition.repo, page, namespace=acquisition.namespace, scope="interim-events:" + run_id)
        if not saved.get("source_id"):
            checks.append({"url": url, "status": "unavailable", "reason": saved.get("reason")})
            continue
        events.append({"source_id": saved["source_id"], "title": page.title or candidate["title"], "url": page.final_url,
                       "kind": candidate["kind"], "published_at": published.isoformat(),
                       "publication_basis": "SEC submissions filing date" if candidate.get("sec_row") else "Dated primary document header",
                       "event_date": event_date.isoformat() if event_date else None, "retrieved_at": page.retrieved_at,
                       "quote": candidate.get("quote") or "", "content_hash": hashlib.sha256(page.content.encode()).hexdigest(),
                       "sec_items": candidate.get("sec_row", {}).get("items"), "accession": candidate.get("sec_row", {}).get("accession")})
    if checks:
        gaps.append(f"{len(checks)} candidate documents could not be retained; see acquisition checks.")
    if not events:
        gaps.append("No readable primary event documents were verified in this bounded pass. This does not establish that no events occurred.")
    return {"version": VERSION, "ticker": company["ticker"], "status": "partial" if gaps else "completed",
            "window_start": start.isoformat(), "cutoff": end.isoformat(), "checked_at": utc_now(),
            "baseline_filing": {key: latest.get(key) for key in ("form", "period_end", "filed_at", "accession")} if latest else None,
            "issuer_verification": {key: company.get(key) for key in ("ticker", "tickers", "name", "website", "investor_website", "cik", "submissions_url", "verification_hash", "verified_at")},
            "issuer_domain_verification": company.get("issuer_domain_verification"),
            "events": events, "checks": checks, "gaps": gaps, "discovery_status": search_status,
            "source_ids": list(dict.fromkeys(item["source_id"] for item in events)),
            "coverage": "Bounded primary-source search, not exhaustive event coverage", "discovery_records": acquisition.discovery_records}


async def ensure_interim_events(service, run_id, tickers, *, task_id):
    repo = service.repo
    run = repo.run_record(run_id)
    if not run or run["namespace"] != "real":
        return []
    attached = []
    for ticker in list(dict.fromkeys(tickers))[:5]:
        dispatch_guard(repo, run_id, task_id)
        snapshot = json_loads(repo.run_record(run_id)["input_snapshot_json"], {})
        prior = next((item for item in (snapshot.get("investment_process") or {}).get("interim_events", []) if item.get("ticker") == ticker), None)
        if prior and prior.get("status") != "collecting":
            if prior.get("issuer_domain_verification"):
                with repo.db.operation() as conn:
                    identity = {**prior.get("issuer_verification", {}), "issuer_domain_verification": prior["issuer_domain_verification"]}
                    bound = company_with_verified_issuer_domain(identity, prior, conn, run["namespace"])
                if not bound.get("issuer_domain_verification"):
                    raise ValueError("The frozen intervening-event issuer identity changed; start a new investment review.")
            packet = repo.source_packet(run["namespace"], prior.get("source_ids", []))
            expected = {item["source_id"]: item["content_hash"] for item in prior.get("events", [])}
            if any(expected.get(source["id"]) != source["content_hash"] or hashlib.sha256(source["content"].encode()).hexdigest() != source["content_hash"] for source in packet):
                raise ValueError("The frozen intervening-event evidence changed; start a new investment review.")
            attached.extend(prior.get("source_ids", []))
            continue
        acquisition = service.acquisition_factory(run["namespace"]) if service.acquisition_factory else EarningsAcquisition(repo, service.registry, service.config, namespace=run["namespace"])
        acquisition.dispatch_guard = lambda: dispatch_guard(repo, run_id, task_id)
        try:
            # Re-read SEC submissions to include events since a reused earnings package.
            company = await acquisition.resolve(ticker)
            # Keep those fresh filing rows while restoring the same case's
            # independently fetched corporate-to-IR domain proof. SEC often
            # omits website fields on every refresh, not only the first one.
            saved_earnings = (snapshot.get("investment_process") or {}).get("earnings", [])
            with repo.db.operation() as conn:
                for saved in reversed(saved_earnings):
                    if saved.get("ticker") != ticker or not saved.get("workflow_id"):
                        continue
                    row = conn.execute("SELECT result_json FROM research_workflow_runs WHERE id=? AND namespace=? AND ticker=? AND status IN ('completed','partial')",
                                       (saved["workflow_id"], run["namespace"], ticker)).fetchone()
                    if row:
                        package = json_loads(row["result_json"], {})
                        company = company_with_verified_issuer_domain(company, package.get("event", {}), conn, run["namespace"])
                    break
            receipt = await collect_interim_events(acquisition, company, cutoff=run["as_of"], run_id=run_id)
        except ProcessPaused:
            raise
        except Exception as exc:
            receipt = {"version": VERSION, "ticker": ticker, "status": "unavailable", "cutoff": str(run["as_of"])[:10],
                       "checked_at": utc_now(), "events": [], "source_ids": [], "gaps": ["Press release/event coverage could not be established: " + str(exc)[:600]]}
        dispatch_guard(repo, run_id, task_id)
        repo.append_run_sources(run_id, receipt["source_ids"], reason="Primary releases and events between reporting periods attached before investment synthesis.")
        _save(repo, run_id, receipt)
        attached.extend(receipt["source_ids"])
    return list(dict.fromkeys(attached))
