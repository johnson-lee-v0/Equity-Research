"""Local watch monitoring with durable, deduplicated case continuations."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any
from uuid import uuid4

from ..agents.roles import LEAN_WORKFLOW_MARKER
from ..db import utc_now
from .case_store import CaseDecisionStore
from .connectors import AlpacaConnector
from .learning import candidate_key
from .learning_refresh import refresh_paper_outcomes
from .source_refresh import refresh_watch_sources
from .watchlist import evaluate_watch_trigger, invalidation_trigger, monitoring_triggers, watch_key


def _later(value: Any, previous: Any) -> bool:
    try:
        current = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        before = datetime.fromisoformat(str(previous).replace("Z", "+00:00"))
        return current > before
    except (ValueError,TypeError):
        return False


def review_state(conn: Any, key: str, old: dict[str, Any]) -> str | None:
    if not old.get("followup_task_id"):
        return None
    latest = conn.execute("SELECT final_task_id FROM watch_review_episodes WHERE trigger_key=? ORDER BY episode DESC LIMIT 1", (key,)).fetchone()
    if latest:
        task = conn.execute("SELECT status FROM tasks WHERE id=?", (latest[0],)).fetchone()
        statuses = [task[0]] if task else ["missing"]
    else:
        first = conn.execute("SELECT kind FROM tasks WHERE id=?",(old["followup_task_id"],)).fetchone()
        suffix = str(first[0]).split("_watch_",1)[1] if first and "_watch_" in first[0] else key[:16]
        statuses = [row[0] for row in conn.execute("SELECT status FROM tasks WHERE run_id=? AND kind LIKE ?", (old.get("run_id"),"%_watch_"+suffix))]
    if statuses and all(value == "completed" for value in statuses):
        return "reviewed"
    return "failed" if not statuses or any(value in {"failed","blocked","cancelled","missing"} for value in statuses) else "reviewing"


class WatchMonitor:
    def __init__(self, repo: Any, engine: Any, config: Any, connector: Any = None):
        self.repo, self.engine, self.config = repo, engine, config
        self.store = CaseDecisionStore(repo)
        self.connector = connector
        self.lock = asyncio.Lock()

    def enabled(self) -> bool:
        with self.repo.db.operation() as conn:
            row = conn.execute("SELECT value_json FROM app_settings WHERE key='watch_checks_enabled'").fetchone()
        return json.loads(row[0]) is True if row else True

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                if self.enabled() and not self.repo.firm_paused():
                    await self.check_once()
                    await refresh_paper_outcomes(self.repo,self.config,"real",connector=self.connector)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.repo.emit("real", "watch_check_failed", payload={"message": f"Watch monitoring could not complete ({type(exc).__name__})."})
            try:
                await asyncio.wait_for(stop.wait(), timeout=300)
            except asyncio.TimeoutError:
                pass

    async def check_once(self, namespace: str = "real") -> dict[str, Any]:
        async with self.lock:
            if self.repo.firm_paused():
                return {"paused": True, "checked": 0, "dispatched": []}
            watches = self.store.watchlist(namespace)["items"]
            with self.repo.db.operation() as conn:
                states = {row["trigger_key"]: dict(row) for row in conn.execute("SELECT w.* FROM watch_checks w JOIN runs r ON r.id=w.run_id WHERE r.namespace=?", (namespace,))}
                reviews = {key:review_state(conn,key,old) for key,old in states.items()}
            jobs = []
            for item in watches:
                if item.get("lifecycle_state") == "closed":
                    continue
                triggers = monitoring_triggers(item["candidate"])
                if item.get("stale"):
                    with self.repo.db.operation() as conn:
                        amendments = conn.execute("SELECT DISTINCT source_id FROM invalidations WHERE namespace=? AND run_id=? AND output_id=?",(namespace,item["run_id"],item.get("source_output_id"))).fetchall()
                    if not amendments:
                        continue
                    triggers = [invalidation_trigger(item.get("decision_revision"),[row[0] for row in amendments])]
                for trigger in triggers:
                    if item.get("setup_expired") and trigger.get("type") == "price" and item.get("lifecycle_state") != "held":
                        continue
                    key = watch_key(item["run_id"], item["candidate"]["ticker"], trigger, item["candidate"])
                    legacy_key = watch_key(item["run_id"],item["candidate"]["ticker"],trigger)
                    if key not in states and legacy_key in states:
                        # Existing once-fired events stay handled after the
                        # candidate identity becomes part of the new key.
                        states[key] = states[legacy_key]
                        reviews[key] = reviews.get(legacy_key)
                    state = states.get(key, {})
                    reviewed = reviews.get(key) == "reviewed"
                    if reviews.get(key) in {"reviewing","failed"} or state.get("status") == "expired" or trigger.get("status") in {"fired", "expired", "paused"}:
                        continue
                    if (state.get("status") == "fired" or state.get("followup_task_id")) and not (reviewed and trigger.get("type") in {"price","evidence","catalyst"}):
                        continue
                    jobs.append((item, trigger))
            jobs.sort(key=lambda pair: states.get(watch_key(pair[0]["run_id"], pair[0]["candidate"]["ticker"], pair[1],pair[0]["candidate"]), {}).get("checked_at", ""))
            checked, dispatched = 0, []
            for item, trigger in jobs[:10]:
                if self.repo.firm_paused():
                    break
                run_id, ticker = item["run_id"], item["candidate"]["ticker"]
                key = watch_key(run_id, ticker, trigger,item["candidate"])
                old = states.get(key, {})
                reviewed = reviews.get(key) == "reviewed"
                now = datetime.now(timezone.utc)
                observation, market_result, source_id = None, None, None
                old_result = json.loads(old.get("result_json") or "{}")
                if trigger.get("type") == "price" and self.config.enable_market_connectors:
                    connector = self.connector or AlpacaConnector(project_root=self.config.project_root, data_dir=self.config.data_dir)
                    if self.repo.firm_paused():
                        break
                    try:
                        market_result = await asyncio.to_thread(connector.fetch_bars, ticker, "daily", start=now-timedelta(days=7), end=now, limit=20, max_pages=1, max_bars=20, timeout=15, retries=0, feed=self.config.alpaca_data_feed, adjustment="split", include_technicals=False)
                    except Exception:
                        market_result = None
                    bars = [bar for bar in market_result.bars if bar.complete and bar.symbol == ticker] if market_result else []
                    if market_result and market_result.capability == "ready" and bars and not self.repo.firm_paused():
                        latest = max(bars, key=lambda bar: bar.timestamp)
                        retained = self.repo.import_evidence(market_result.as_import_request(namespace=namespace, title=f"{ticker} watch price observation"))
                        source_id = retained.get("source_id")
                        packet = self.repo.source_packet(namespace,[source_id]) if source_id else []
                        source = packet[0] if packet else {}
                        observation = {"price": str(latest.close), "currency": market_result.metadata.get("currency"), "as_of": latest.timestamp, "provider": "Alpaca", "feed": self.config.alpaca_data_feed, "source_ref":source_id, "source_hash":source.get("content_hash"), "source_version":source.get("version")}
                refs = trigger.get("source_refs") or []
                source_refresh = old_result.get("source_refresh") or {}
                last_refresh = source_refresh.get("checked_at")
                try:
                    refresh_due = not last_refresh or now-datetime.fromisoformat(last_refresh.replace("Z","+00:00")) >= timedelta(days=1)
                except (TypeError,ValueError):
                    refresh_due = True
                if refs and trigger.get("type") in {"date","evidence","catalyst"} and refresh_due:
                    source_refresh = await refresh_watch_sources(self.repo,namespace,run_id,refs)
                    for other_run in source_refresh.get("refresh_run_ids") or []:
                        if other_run != run_id and not self.repo.firm_paused():
                            self.engine.schedule(other_run)
                try:
                    fingerprint = self.repo.source_fingerprint(namespace, refs) if refs else ""
                except ValueError:
                    fingerprint = ""
                changed = bool((source_refresh.get("changed") and refresh_due) or (fingerprint and old.get("source_fingerprint") and fingerprint != old["source_fingerprint"]))
                result = evaluate_watch_trigger(trigger, now=now, observation=observation, previous_observation=old_result.get("observation"), evidence_changed=changed, paused=self.repo.firm_paused())
                if not result.get("observation") and old_result.get("observation"):
                    result["observation"] = old_result["observation"]
                    result["observation_status"] = "retained_previous"
                if result.get("status") in {"paused","unavailable"}:
                    for field in ("rearmed","rearmed_at","event_observation_at","review_state"):
                        if field in old_result:
                            result[field] = old_result[field]
                if item.get("stale") and trigger.get("evidence_condition") == "invalidated_decision" and not old.get("followup_task_id") and not self.repo.firm_paused():
                    result.update({"status":"fired","fired":True,"reason":"Decision evidence was invalidated. Refresh the evidence and reassess; no entry-price condition is implied."})
                result.update({"decision_revision":item.get("decision_revision"),"source_fingerprint":fingerprint,"source_refresh":source_refresh})
                if reviewed and result.get("status") not in {"paused", "unavailable"}:
                    # A completed event cannot fire again merely because its
                    # original price/date condition remains true.
                    if trigger.get("type") == "price":
                        rearmed = bool(old_result.get("rearmed"))
                        event_at = old_result.get("event_observation_at") or (old_result.get("observation") or {}).get("as_of")
                        rearmed_at = old_result.get("rearmed_at")
                        if result.get("condition_met") is False and observation and _later(observation.get("as_of"),event_at):
                            rearmed = True
                            rearmed_at = observation.get("as_of")
                        result["rearmed"] = rearmed
                        result.update({"rearmed_at":rearmed_at,"event_observation_at":event_at})
                        if result["fired"] and (not rearmed or not _later((observation or {}).get("as_of"),rearmed_at)):
                            result.update({"status":"active","fired":False,"reason":"Reviewed. Waiting for the price condition to clear before it can trigger a new review."})
                    elif not changed:
                        result.update({"status":"active","fired":False,"reason":"Reviewed. Waiting for newly changed evidence; the old review date does not trigger another episode."})
                    result["review_state"] = "reviewed"
                    result["new_episode"] = bool(result["fired"])
                # A condition already observed remains pending until admission.
                # Rechecking must not erase the event when capacity is occupied.
                if old_result.get("pending_review") and old_result.get("decision_revision") == item.get("decision_revision") and not self.repo.firm_paused():
                    result = old_result | {"status": "fired", "fired": True, "checked_at": now.isoformat()}
                task_id = None
                if result["fired"] and not dispatched:
                    task_id = self._queue_review(item, trigger, key, source_id, result)
                    if task_id:
                        dispatched.append({"run_id": run_id, "task_id": task_id, "ticker": ticker})
                        self.engine.schedule(run_id)
                if result["fired"] and not task_id:
                    result.update({"status": "active", "pending_review": True, "reason": "Condition met; review is waiting for research capacity."})
                elif task_id:
                    result["pending_review"] = False
                    result["rearmed"] = False
                    result["review_state"] = "reviewing"
                    result["event_observation_at"] = (result.get("observation") or {}).get("as_of")
                saved_fingerprint = old.get("source_fingerprint", "") if result.get("pending_review") else fingerprint
                with self.repo.db.transaction(immediate=True) as conn:
                    conn.execute("INSERT INTO watch_checks(trigger_key,run_id,ticker,trigger_json,status,result_json,source_fingerprint,followup_task_id,checked_at) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(trigger_key) DO UPDATE SET status=excluded.status,result_json=excluded.result_json,source_fingerprint=excluded.source_fingerprint,followup_task_id=COALESCE(excluded.followup_task_id,watch_checks.followup_task_id),checked_at=excluded.checked_at", (key,run_id,ticker,json.dumps(trigger),result["status"],json.dumps(result),saved_fingerprint,task_id or old.get("followup_task_id"),utc_now()))
                checked += 1
            return {"paused": self.repo.firm_paused(), "checked": checked, "dispatched": dispatched}

    def _queue_review(self, item: dict[str, Any], trigger: dict[str, Any], key: str, source_id: str | None, check: dict[str, Any]) -> str | None:
        run_id, ticker = item["run_id"], item["candidate"]["ticker"]
        with self.repo.db.transaction(immediate=True) as conn:
            paused = conn.execute("SELECT value_json FROM app_settings WHERE key='firm_paused'").fetchone()
            if paused and json.loads(paused[0]):
                return None
            old = conn.execute("SELECT * FROM watch_checks WHERE trigger_key=?", (key,)).fetchone()
            if old and old["followup_task_id"]:
                if not check.get("new_episode") or review_state(conn,key,dict(old)) != "reviewed":
                    return old["followup_task_id"]
            run = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            current_revision = conn.execute("SELECT MAX(revision) FROM case_decision_versions WHERE run_id=?",(run_id,)).fetchone()[0]
            if item.get("decision_revision") is not None and current_revision != item["decision_revision"]:
                return None
            if not run or run["cancel_requested"] or run["pause_requested"] or run["status"] not in {"completed", "needs_review"}:
                return None
            if conn.execute("SELECT 1 FROM tasks WHERE run_id=? AND status IN ('queued','running','waiting_evidence','waiting_review')", (run_id,)).fetchone():
                return None
            if run["origin"] == "reddit":
                if self.repo._active_user_group_ids_conn(conn, run["namespace"]):
                    return None
                if len(self.repo._active_reddit_group_ids_conn(conn, run["namespace"])) >= self.config.reddit_parallel_limit:
                    return None
            snapshot = json.loads(run["input_snapshot_json"])
            previous_portfolio = snapshot.get("portfolio_snapshot") or {}
            current_portfolio = self.repo._portfolio_snapshot_conn(conn, run["namespace"])
            # New balances and holdings are local observations, not inferred
            # transfers. Keep their original timestamps/reconciliation state.
            # A policy edit applies to new cases; this case retains its policy.
            current_portfolio["portfolio_policy"] = previous_portfolio.get("portfolio_policy", {})
            snapshot["portfolio_snapshot"] = current_portfolio
            snapshot["account_snapshot_id"] = "snap_" + uuid4().hex
            snapshot["portfolio_snapshot_captured_at"] = utc_now()
            snapshot["portfolio_snapshot_as_of"] = None
            source_ids = list(snapshot.get("source_ids") or [])
            source_ids = self.repo._source_head_ids_conn(conn,run["namespace"],source_ids)
            for ref in trigger.get("source_refs") or []:
                if ref not in source_ids and conn.execute("SELECT 1 FROM sources WHERE id=? AND namespace=?",(ref,run["namespace"])).fetchone():
                    source_ids.append(ref)
            if source_id and source_id not in source_ids:
                source_ids.append(source_id)
            snapshot["source_ids"] = source_ids
            snapshot["watch_review"] = {"ticker": ticker, "trigger_key": key, "condition": trigger, "observation": check.get("observation"), "checked_at": check["checked_at"]}
            # Each watch event has its own bounded review. The one-off intake
            # gap allowance cannot recursively create extra watch repairs.
            snapshot["lean_continuation_used"] = True
            now = utc_now()
            episode = conn.execute("SELECT COALESCE(MAX(episode),0)+1 FROM watch_review_episodes WHERE trigger_key=?", (key,)).fetchone()[0]
            if old and old["followup_task_id"] and episode == 1:
                episode = 2  # Preserve a historical pre-episode task graph.
            conn.execute("UPDATE runs SET status='queued',as_of=?,account_snapshot_id=?,input_snapshot_json=?,updated_at=?,finished_at=NULL WHERE id=?", (now,snapshot["account_snapshot_id"],json.dumps(snapshot),now,run_id))
            sequence = conn.execute("SELECT COALESCE(MAX(sequence_no),0)+1 FROM tasks WHERE run_id=?", (run_id,)).fetchone()[0]
            stages = [("A03", "research_synthesis"), ("A11", "cio_review")]
            if trigger.get("type") != "price":
                stages.insert(0, ("A01", "universe_discovery"))
            previous_id, previous_kind, first_id = None, None, None
            for agent_id, base_kind in stages:
                task_id, kind = "task_"+uuid4().hex, f"{base_kind}_watch_{key[:16]}" + (f"_e{episode}" if episode > 1 else "")
                existing = conn.execute("SELECT id FROM tasks WHERE run_id=? AND kind=?", (run_id,kind)).fetchone()
                if existing:
                    return existing[0]
                instruction = f"{LEAN_WORKFLOW_MARKER} Targeted watch review for {ticker}: {trigger.get('condition')}. {check['reason']} Verify only the affected evidence and refresh this same case. Preserve other candidates unless this evidence changes them. A review date is not proof a catalyst occurred. Retain the complete prior decision as context and publish a new case decision only after CIO assessment."
                conn.execute("INSERT INTO tasks(id,run_id,agent_id,kind,instruction,status,sequence_no,dependency_json,input_snapshot_hash,input_refs_json,retry_limit,timeout_seconds,assignment_reason,origin,created_at,updated_at) VALUES(?,?,?,?,?,'queued',?,?,?,?,?,?,?,?,?,?)", (task_id,run_id,agent_id,kind,instruction,sequence,json.dumps([previous_kind] if previous_kind else []),hashlib.sha256(json.dumps(snapshot,sort_keys=True).encode()).hexdigest(),json.dumps(source_ids),1,self.config.codex_timeout_seconds,"A saved watch condition triggered a targeted review.",run["origin"],now,now))
                if previous_id:
                    conn.execute("INSERT INTO task_dependencies(task_id,depends_on_task_id) VALUES(?,?)", (task_id,previous_id))
                first_id = first_id or task_id
                previous_id, previous_kind = task_id, kind
                sequence += 1
            conn.execute("INSERT INTO watch_review_episodes(id,namespace,trigger_key,run_id,decision_revision,candidate_key,source_fingerprint,episode,first_task_id,final_task_id,condition_json,observation_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",("watch_episode_"+uuid4().hex,run["namespace"],key,run_id,item.get("decision_revision"),candidate_key(item["candidate"]),check.get("source_fingerprint") or "",episode,first_id,previous_id,json.dumps(trigger),json.dumps(check),now))
            conn.execute("INSERT INTO watch_checks(trigger_key,run_id,ticker,trigger_json,status,result_json,source_fingerprint,followup_task_id,checked_at) VALUES(?,?,?,?,'fired',?,'',?,?) ON CONFLICT(trigger_key) DO UPDATE SET status='fired',followup_task_id=excluded.followup_task_id", (key,run_id,ticker,json.dumps(trigger),json.dumps(check),first_id,now))
            self.repo.db.emit(conn, namespace=run["namespace"], event_type="watch_review_queued", run_id=run_id, payload={"ticker":ticker,"task_id":first_id,"message":"A watch condition triggered a targeted case review."})
            return first_id
