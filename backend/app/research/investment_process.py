"""Latest earnings is a durable prerequisite of an investment investigation.

The original request, horizon and origin are never rewritten. Receipts bind a
specific workflow package and source versions to this case; retrying a case
reuses that frozen receipt, while a new case checks freshness independently.
"""
from __future__ import annotations

import hashlib
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from ..db import digest, json_dumps, json_loads, utc_now

VERSION = "investment-process.v1"
STAGES = ["ticker", "earnings", "questions", "answers", "pricing", "decision"]
CACHE_AGE = timedelta(hours=1)


class ProcessPaused(asyncio.CancelledError):
    """A retained prerequisite may resume only with its parent authorization."""


def dispatch_guard(repo, run_id: str, task_id: str | None = None) -> None:
    run = repo.run_record(run_id)
    task = repo.task(task_id) if task_id else None
    if (not run or run["cancel_requested"] or run["pause_requested"]
            or run["status"] in {"cancelled", "failed", "completed"}
            or repo.firm_dispatch_paused(run_id)
            or (task and (task["pause_requested"] or task["status"] == "cancelled"))):
        raise ProcessPaused("Earnings collection is waiting for the investment run to resume.")
    if task_id:
        allowed, reason = repo.reddit_task_dispatch_allowed(task_id)
        if not allowed:
            raise ProcessPaused(reason or "Reddit screening has not admitted this investigation.")


def process_view(conn, run: Any) -> dict | None:
    """Expose saved prerequisite progress, including older direct handoffs."""
    snapshot = json_loads(run["input_snapshot_json"], {})
    saved = snapshot.get("investment_process")
    if isinstance(saved, dict):
        return saved
    if not str(run["origin_ref"] or "").startswith("workflow:"):
        return None
    from .earnings_context import verified_earnings_package
    verified = verified_earnings_package(conn, run["id"])
    if not verified:
        return None
    package = verified["package"]
    return {"version": VERSION, "stages": STAGES, "earnings": [receipt_for(
        run["ticker"], verified["workflow_id"], package, reused=True)]}


def receipt_for(ticker: str, identifier: str, package: dict, *, reused: bool) -> dict:
    event = package.get("event") or {}
    gaps = list(dict.fromkeys(str(gap) for key in ("gaps", "material_gaps", "comparison_gaps")
                              for gap in package.get(key, [])))
    return {"ticker": ticker, "status": "partial" if gaps else "completed",
            "workflow_id": identifier, "package_hash": digest(package), "reused": reused,
            "fiscal_period": event.get("fiscal_period"), "period_end": event.get("period_end"),
            "checked_at": event.get("located_at") or (package.get("company") or {}).get("verified_at"), "gaps": gaps}


def _save(repo, run_id: str, receipt: dict) -> None:
    with repo.db.transaction(immediate=True) as conn:
        run = conn.execute("SELECT namespace,input_snapshot_json FROM runs WHERE id=?", (run_id,)).fetchone()
        snapshot = json_loads(run["input_snapshot_json"], {})
        process = snapshot.get("investment_process") or {"version": VERSION, "stages": STAGES, "earnings": []}
        process["earnings"] = [item for item in process["earnings"] if item.get("ticker") != receipt["ticker"]] + [receipt]
        snapshot["investment_process"] = process
        conn.execute("UPDATE runs SET input_snapshot_json=?,updated_at=? WHERE id=?", (json_dumps(snapshot), utc_now(), run_id))
        repo.db.emit(conn, namespace=run["namespace"], run_id=run_id, event_type="investment_earnings",
                     payload={"message": f"{receipt['ticker']} earnings: {receipt['status']}.", **receipt})


def _verified_workflow(repo, workflow: dict, namespace: str, ticker: str) -> bool:
    from .workflows import EARNINGS
    package = workflow.get("result") or {}
    company, event = package.get("company") or {}, package.get("event") or {}
    if (workflow.get("namespace") != namespace or workflow.get("ticker") != ticker
            or workflow.get("version") != EARNINGS.version or workflow.get("status") not in {"completed", "partial"}
            or company.get("ticker") != ticker or not company.get("cik")
            or event.get("verification") != "primary_release" or not event.get("period_end")):
        return False
    steps = {step["id"]: step["status"] for step in workflow.get("steps", [])}
    if (any(steps.get(key) != "completed" for key in ("resolve", "locate"))
            or any(steps.get(key) not in {"completed", "partial"} for key in ("acquire", "publish"))):
        return False
    ids = package.get("source_ids") or []
    if not ids or len(ids) > 100:
        return False
    try:
        sources = {item["id"]: item for item in repo.source_packet(namespace, ids)}
    except ValueError:
        return False
    if set(sources) != set(ids) or any(not item.get("content") or hashlib.sha256(item["content"].encode()).hexdigest() != item.get("content_hash") for item in sources.values()):
        return False
    documents = [*package.get("documents", {}).values(), *package.get("materials", [])]
    for document in documents:
        if not isinstance(document, dict) or document.get("status", "available") != "available":
            continue
        source = sources.get(document.get("source_id"))
        if not source or not document.get("content_hash") or source["content_hash"] != document["content_hash"]:
            return False
    transcript = package.get("documents", {}).get("transcript") or {}
    if transcript.get("status") == "available":
        # A newly collected/reused package must still satisfy today's reader
        # checks. This does not rewrite a package frozen in an older case.
        from .discovery import FetchedSource
        from .earnings_sources import _transcript_text, transcript_structure_rejection
        source = sources.get(transcript.get("source_id"))
        if not source:
            return False
        page = FetchedSource(source["url"], source["url"], source["content"],
                             source.get("title") or source["url"], source.get("retrieval_at") or "")
        if transcript_structure_rejection(_transcript_text(page)):
            return False
    # Trend rows are descriptive indexes. Exact quotes are independently
    # re-bound by build_earnings_context before any provider sees them.
    for metadata in (package.get("trends") or {}).get("sources", []):
        source = sources.get(metadata.get("source_id"))
        if not source or (metadata.get("url") and metadata["url"] != source.get("url")):
            return False
    bindings = package.get("source_bindings")
    if bindings and (set(bindings) != set(sources) or any(
            bindings[sid].get("content_hash") != source["content_hash"] or bindings[sid].get("version") != source.get("version")
            for sid, source in sources.items())):
        return False
    with repo.db.operation() as conn:
        return not any(conn.execute("SELECT 1 FROM sources WHERE namespace=? AND supersedes_source_id=? LIMIT 1", (namespace, sid)).fetchone() for sid in ids)


async def ensure_latest_earnings(service, run_id: str, tickers: list[str], *, task_id: str,
                                 retry_unavailable: bool = False) -> list[str]:
    """Bounded acquisition before synthesis; unavailable evidence stays explicit."""
    repo = service.repo
    run = repo.run_record(run_id)
    if not run or run["namespace"] != "real":
        return []
    attached: list[str] = []
    for ticker in list(dict.fromkeys(tickers))[:5]:
        dispatch_guard(repo, run_id, task_id)
        snapshot = json_loads(repo.run_record(run_id)["input_snapshot_json"], {})
        prior = next((item for item in (snapshot.get("investment_process") or {}).get("earnings", []) if item.get("ticker") == ticker), None)
        retry_meta = {"retry_event_id": prior["retry_event_id"]} if prior and prior.get("retry_event_id") else {}
        retry = None
        if retry_unavailable:
            with repo.db.operation() as conn:
                retry = conn.execute("SELECT e.sequence_id FROM events e JOIN tasks t ON t.id=e.task_id "
                    "WHERE e.run_id=? AND e.task_id=? AND e.type='retry_requested' AND t.agent_id='A01' "
                    "AND t.kind='universe_discovery' ORDER BY e.sequence_id DESC LIMIT 1", (run_id, task_id)).fetchone()
        if retry_unavailable and prior and prior.get("status") == "unavailable" and prior.get("workflow_id"):
            # Only a newly recorded explicit retry of this A01 task may retry
            # its own failed prerequisite. A resume/restart is not permission
            # to repeat a failed search, and frozen usable packages stay intact.
            workflow = service.store.get(prior["workflow_id"])
            if (retry and retry["sequence_id"] != retry_meta.get("retry_event_id")
                    and workflow["status"] == "failed"
                    and workflow.get("idempotency_key") == f"investment-process:{run_id}:{ticker}"):
                retry_meta = {"retry_event_id": retry["sequence_id"]}
                # Consume authorization before resetting checkpoints. The old
                # failure remains in the workflow event/task-attempt histories.
                _save(repo, run_id, prior | retry_meta)
                service.store.retry(workflow["id"])
                prior = prior | retry_meta | {"status": "collecting"}
        if prior and prior.get("status") != "collecting":
            # A completed prerequisite is immutable for this investigation.
            # A new case, not a synthesis retry, checks the next earnings date.
            if prior.get("workflow_id") and prior.get("status") in {"completed", "partial"}:
                from .earnings_context import verified_earnings_package
                with repo.db.operation() as conn:
                    verified = verified_earnings_package(conn, run_id, ticker=ticker)
                if not verified:
                    raise ValueError("The frozen earnings evidence changed. Start a new investment review.")
                attached.extend(verified["source_bindings"])
            continue
        identifier = prior.get("workflow_id") if prior else None
        reused = False
        if not identifier:
            now = datetime.now(timezone.utc)
            with repo.db.operation() as conn:
                candidates = conn.execute("SELECT id FROM research_workflow_runs WHERE namespace=? AND ticker=? AND workflow='earnings' AND status IN ('completed','partial') ORDER BY created_at DESC,rowid DESC LIMIT 10", (run["namespace"], ticker)).fetchall()
            for row in candidates:
                candidate = service.store.get(row["id"])
                try:
                    # Re-publishing historical bars does not re-check the
                    # latest event. Only the locator's clock permits reuse.
                    checked = datetime.fromisoformat(str((candidate["result"].get("event") or {}).get("located_at")).replace("Z", "+00:00"))
                    fresh = timedelta(0) <= now - checked <= CACHE_AGE
                except (TypeError, ValueError):
                    fresh = False
                if fresh and _verified_workflow(repo, candidate, run["namespace"], ticker):
                    identifier, reused = candidate["id"], True
                    break
        if not identifier:
            identifier = service.store.create(ticker, namespace=run["namespace"], idempotency_key=f"investment-process:{run_id}:{ticker}")
        if retry:
            # A failed old A01 model attempt may predate its first earnings
            # collection. Its explicit retry authorizes this collection, not
            # another automatic attempt if collection subsequently fails.
            retry_meta = {"retry_event_id": retry["sequence_id"]}
        _save(repo, run_id, {"ticker": ticker, "status": "collecting", "workflow_id": identifier, "gaps": [], "reused": reused, **retry_meta})
        workflow = service.store.get(identifier)
        if workflow["status"] in {"queued", "running"}:
            active = service.tasks.get(identifier)
            if not active or active.done():
                active = service.schedule(identifier, parent_run_id=run_id, parent_task_id=task_id)
            try:
                await asyncio.shield(active)
            except asyncio.CancelledError:
                # A user can cancel the collection from Documents while the
                # original investment question remains active. Preserve that
                # explicit missing-evidence outcome without cancelling it too.
                if asyncio.current_task().cancelling():
                    raise
                state = service.store.get(identifier)["status"]
                if state == "queued":
                    raise ProcessPaused("Earnings collection is waiting for its owner investment review to resume.")
                if state != "cancelled":
                    raise
            dispatch_guard(repo, run_id, task_id)
            workflow = service.store.get(identifier)
        package = workflow.get("result") or {}
        if _verified_workflow(repo, workflow, run["namespace"], ticker):
            ids = list(package["source_ids"])
            repo.append_run_sources(run_id, ids, reason="Latest earnings materials and historical trends attached before the five investment questions.")
            _save(repo, run_id, receipt_for(ticker, identifier, package, reused=reused) | retry_meta)
            attached.extend(ids)
        else:
            reason = workflow.get("error") or "A current primary-release earnings package could not be verified; do not infer earnings or growth."
            not_applicable = next((step["output"].get("earnings_applicability") for step in workflow.get("steps", []) if step["id"] == "resolve"), None) == "not_applicable"
            _save(repo, run_id, {"ticker": ticker, "status": "not_applicable" if not_applicable else "unavailable", "workflow_id": identifier, "checked_at": utc_now(), "gaps": [reason], **retry_meta})
    return list(dict.fromkeys(attached))
