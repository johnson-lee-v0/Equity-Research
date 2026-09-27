"""Local Reddit monitor with independent history and new-post checkpoints."""
from __future__ import annotations

import asyncio
import inspect
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from ..db import json_dumps, json_loads, utc_now
from .connectors import RedditConnector, resolve_provider_credentials


def stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def later(*values: str | None) -> str | None:
    return max((value for value in values if value), default=None)


def earlier(*values: str | None) -> str | None:
    return min((value for value in values if value), default=None)


class RedditMonitor:
    def __init__(self, repository: Any, engine: Any, config: Any, connector: Any = None, *, now=None):
        self.repo, self.engine, self.config = repository, engine, config
        self.connector = connector
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.locks: dict[str, asyncio.Lock] = {}

    @staticmethod
    def validate(namespace: str, community: str) -> str:
        if namespace not in {"real", "demo"}:
            raise ValueError("Reddit intake is available in real or demo namespaces only.")
        community = str(community).lower().removeprefix("r/").strip()
        if not re.fullmatch(r"[a-z0-9_]{2,50}", community):
            raise ValueError("Invalid subreddit name.")
        return community

    def state(self, namespace: str = "real", community: str = "wallstreetbets") -> dict[str, Any]:
        community = self.validate(namespace, community)
        with self.repo.db.operation() as conn:
            row = conn.execute("SELECT * FROM intake_monitors WHERE namespace=? AND community=?", (namespace, community)).fetchone()
        if row:
            return {**dict(row), "enabled": bool(row["enabled"]), "state": json_loads(row["state_json"], {})}
        return {"namespace": namespace, "community": community, "enabled": bool(self.config.enable_reddit_intake) and namespace == "real", "window_days": 7, "revision": 0, "state": {}}

    def save(self, namespace: str, community: str, *, enabled: bool, window_days: int) -> dict[str, Any]:
        community = self.validate(namespace, community)
        if namespace == "demo" and enabled:
            raise ValueError("Live Reddit intake is available in the Real workspace.")
        if isinstance(window_days, bool) or not 1 <= int(window_days) <= 30:
            raise ValueError("History window must be between 1 and 30 days.")
        current = self.state(namespace, community)
        state = current["state"] if current["window_days"] == int(window_days) else {}
        with self.repo.db.transaction(immediate=True) as conn:
            # One selected community per namespace; retained records remain.
            conn.execute("UPDATE intake_monitors SET enabled=0,revision=revision+1 WHERE namespace=? AND community<>?", (namespace, community))
            conn.execute("INSERT INTO intake_monitors(namespace,community,enabled,window_days,state_json,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(namespace,community) DO UPDATE SET enabled=excluded.enabled,window_days=excluded.window_days,state_json=excluded.state_json,revision=intake_monitors.revision+1,updated_at=excluded.updated_at", (namespace, community, int(enabled), int(window_days), json_dumps(state), utc_now()))
        return self.connection(namespace, community)

    def selected_community(self, namespace: str) -> str:
        with self.repo.db.operation() as conn:
            row = conn.execute("SELECT community FROM intake_monitors WHERE namespace=? ORDER BY enabled DESC,updated_at DESC,rowid DESC LIMIT 1", (namespace,)).fetchone()
        return str(row[0]) if row else "wallstreetbets"

    def _configured(self) -> bool:
        if self.connector is not None:
            return True
        return resolve_provider_credentials(project_root=self.config.project_root, data_dir=self.config.data_dir).reddit_available

    def connection(self, namespace: str = "real", community: str | None = None) -> dict[str, Any]:
        row = self.state(namespace, community or self.selected_community(namespace))
        state = row["state"]
        configured = self._configured()
        status = "missing_credentials" if not configured else "configured_unchecked"
        if configured and state.get("last_status"):
            status = "connected" if state["last_status"] == "ready" else "error"
        reason = state.get("last_error") if configured else "Reddit needs its client ID, client secret and user agent in the private backend configuration. These credentials were not found."
        return {
            "namespace": namespace, "subreddit": row["community"], "window_days": row["window_days"],
            "enabled": row["enabled"], "status": status, "connected": status == "connected", "reason": reason,
            "last_sync_at": state.get("last_poll_at"), "coverage_start": state.get("oldest_at"),
            "coverage_end": state.get("newest_at"), "requested_cutoff": state.get("backfill_cutoff"),
            "coverage_complete": bool(state.get("backfill_complete")) if state.get("backfill_done") or state.get("oldest_at") else None,
            "coverage_reason": state.get("coverage_reason", "History has not been checked."),
            "phase": "poll" if state.get("backfill_done") else "backfill",
            "catching_up": bool(state.get("poll_cursor")), "poll_gap": state.get("poll_gap"),
        }

    def inbox(
        self,
        namespace: str = "real",
        limit: int = 100,
        offset: int = 0,
        *,
        status: str | None = None,
        status_filter: str | None = None,
    ) -> dict[str, Any]:
        self.validate(namespace, "wallstreetbets")
        data = self.repo.intake_status(
            namespace,
            limit=limit,
            offset=offset,
            status=status,
            status_filter=status_filter,
        )
        connection = self.connection(namespace)
        counts = data.get("counts", {})
        total = sum(counts.values())
        capacity = self.repo.reddit_queue_capacity(namespace)
        return {**data, "connection": connection, "availability": "available" if total else "empty" if connection["last_sync_at"] else "not_checked",
                "retained_count": total, "backlog_count": sum(counts.get(s, 0) for s in ("queued", "failed", "blocked")),
                "covered_count": sum(counts.get(s, 0) for s in ("processed", "dismissed", "processing")),
                "coverage_start": connection["coverage_start"], "coverage_end": connection["coverage_end"],
                "coverage_complete": connection["coverage_complete"], "coverage_reason": connection["coverage_reason"],
                "last_poll_at": connection["last_sync_at"], "error_message": connection["reason"], "offset": offset,
                "has_more": bool(data.get("has_more")), **capacity}

    def _checkpoint(self, row: dict[str, Any], state: dict[str, Any]) -> bool:
        with self.repo.db.transaction(immediate=True) as conn:
            changed = conn.execute("INSERT INTO intake_monitors(namespace,community,enabled,window_days,revision,state_json,updated_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(namespace,community) DO UPDATE SET state_json=excluded.state_json,updated_at=excluded.updated_at WHERE intake_monitors.revision=excluded.revision", (row["namespace"], row["community"], int(row["enabled"]), row["window_days"], row["revision"], json_dumps(state), utc_now())).rowcount
        return bool(changed)

    async def poll(self, namespace: str = "real", community: str | None = None, *, max_posts: int = 100, max_pages: int = 5, max_dispatches: int = 3, force_backfill: bool = False, require_enabled: bool = False) -> dict[str, Any]:
        community = self.validate(namespace, community or self.selected_community(namespace))
        if namespace == "demo" and self.connector is None:
            raise ValueError("Live Reddit reads are unavailable in Demo.")
        configured_limit = min(max(1, int(getattr(self.config, "reddit_parallel_limit", 3) or 3)), 8)
        if not 1 <= max_posts <= 1000 or not 1 <= max_pages <= 10 or not 0 <= max_dispatches <= configured_limit:
            raise ValueError(f"Poll limits are invalid; at most {configured_limit} Reddit research groups can be dispatched.")
        async with self.locks.setdefault(namespace, asyncio.Lock()):
            row = self.state(namespace, community)
            if require_enabled and not row["enabled"]:
                return {"dispatch": {"dispatched": []}, "message": "Intake is disabled."}
            state = dict(row["state"])
            if force_backfill:
                state = {}
            if not state.get("backfill_cutoff"):
                state["backfill_cutoff"] = stamp(self.now() - timedelta(days=row["window_days"]))
            phase = "poll" if state.get("backfill_done") else "backfill"
            if phase == "backfill":
                cutoff, cursor = state["backfill_cutoff"], state.get("backfill_cursor")
            else:
                cutoff = state.get("poll_cutoff") or state.get("newest_at") or state["backfill_cutoff"]
                cursor = state.get("poll_cursor")
                state["poll_cutoff"] = cutoff
            # Freeze the requested boundary before any network read, including
            # failed first attempts and process restarts.
            if not self._checkpoint(row, state):
                return {"dispatch": {"dispatched": []}, "message": "Settings changed; the next poll will use the saved window."}
            connector = self.connector or RedditConnector(project_root=self.config.project_root, data_dir=self.config.data_dir)
            try:
                method = connector.fetch_new_posts
                kwargs = {"cutoff": cutoff, "after_cursor": cursor, "max_posts": max_posts, "max_pages": max_pages, "timeout": 20}
                result = await method(community, **kwargs) if inspect.iscoroutinefunction(method) else await asyncio.to_thread(method, community, **kwargs)
                metadata = dict(result.get("metadata", {})) if isinstance(result, dict) else dict(result.metadata)
                capability = str(result.get("capability", "ready")) if isinstance(result, dict) else result.capability
                posts = result.get("posts", []) if isinstance(result, dict) else result.posts
                error = result.get("error") if isinstance(result, dict) else result.error
                stopped = metadata.get("pagination_stopped_reason")
                ingested = None
                if posts or capability == "ready":
                    ingested = self.repo.ingest_reddit_result(result, namespace=namespace, subreddit=community, phase=phase)
                state["last_poll_at"] = metadata.get("retrieved_at") or stamp(self.now())
                state["last_status"] = capability
                state["last_error"] = error
                if ingested and ingested.get("failed_count", 0):
                    raise ValueError("Some posts could not be retained; the batch will be retried before its checkpoint advances.")
                state["oldest_at"] = earlier(state.get("oldest_at"), metadata.get("oldest_covered_at"))
                state["newest_at"] = later(state.get("newest_at"), metadata.get("latest_covered_at"))
                next_cursor = metadata.get("last_consumed_cursor")
                reached = bool(metadata.get("cutoff_reached"))
                finished = reached or stopped in {"listing_exhausted", "cursor_stalled"}
                if phase == "backfill":
                    if next_cursor:
                        state["backfill_cursor"] = next_cursor
                    if finished:
                        state["backfill_done"] = True
                        state["backfill_complete"] = reached
                        state["coverage_reason"] = "Requested cutoff reached for accessible submissions; deleted or inaccessible posts may be absent." if reached else "Reddit's available listing ended before the requested cutoff; historical coverage is partial."
                    elif capability == "ready":
                        state["coverage_reason"] = "History is still being retrieved in bounded batches."
                else:
                    if finished:
                        state["poll_cursor"] = None
                        state["poll_cutoff"] = None
                        if not reached:
                            state["poll_gap"] = "A listing ended before the previous high-water mark; some posts may be inaccessible."
                    elif next_cursor:
                        state["poll_cursor"] = next_cursor
                if not self._checkpoint(row, state):
                    return {"phase": phase, "ingest": ingested, "dispatch": {"dispatched": []}, "message": "Settings changed during retrieval; retained posts are saved and the new window will be checked next."}
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Report a bounded diagnostic, never provider response text
                # that might contain private headers or credentials.
                original = dict(row["state"])
                original.setdefault("backfill_cutoff", state["backfill_cutoff"])
                original.update(last_status="error", last_error=f"The Reddit batch did not complete ({type(exc).__name__}); its saved checkpoint will be retried.", last_poll_at=stamp(self.now()))
                current = self._checkpoint(row, original)
                if not current or require_enabled and not self.state(namespace, community)["enabled"]:
                    max_dispatches = 0
                return {"phase": phase, "error": original["last_error"], "dispatch": self.dispatch(namespace, max_dispatches=max_dispatches)}
            if require_enabled and not self.state(namespace, community)["enabled"]:
                max_dispatches = 0
            return {"phase": phase, "ingest": ingested, "connection": self.connection(namespace, community), "dispatch": self.dispatch(namespace, max_dispatches=max_dispatches)}

    def dispatch(self, namespace: str, *, max_dispatches: int = 3, item_id: str | None = None) -> dict[str, Any]:
        if not max_dispatches or self.repo.firm_paused():
            return {"dispatched": [], "message": "Dispatch is paused."}
        # Keep this quick read for the monitor's visible priority message. The
        # repository repeats the same canonical-root check inside its immediate
        # reservation transaction, closing the race with a newly queued user
        # question.
        active_user = getattr(self.repo, "active_user_group_count", None)
        if callable(active_user) and active_user(namespace):
            return {"dispatched": [], "message": "User questions have priority; Reddit posts remain queued."}
        configured_limit = min(max(1, int(getattr(self.config, "reddit_parallel_limit", 3) or 3)), 8)
        bounded = min(max(0, int(max_dispatches)), configured_limit)
        kwargs = {"max_dispatches": bounded, "max_cost": bounded}
        if item_id is not None:
            kwargs["item_id"] = item_id
        result = self.repo.dispatch_reddit_backlog(namespace, **kwargs)
        # Recovery may relink a run created before a previous process exited.
        # Those queued graphs need the same scheduler handoff as fresh
        # admissions or they would remain permanently occupied but idle.
        scheduled: set[str] = set()
        for item in [*result.get("recovered", []), *result.get("dispatched", [])]:
            run_id = str(item.get("run_id") or "").strip()
            if run_id and run_id not in scheduled:
                scheduled.add(run_id)
                self.engine.schedule(run_id)
        return result

    async def run(self, stop: asyncio.Event) -> None:
        # Network retrieval stays on its one-minute cadence.  Dispatch is a
        # separate inexpensive five-second tick so a completed Reddit group
        # immediately frees capacity for the next retained post.
        last_network_poll = 0.0
        while not stop.is_set():
            try:
                community = self.selected_community("real")
                if self.state("real", community)["enabled"] and not self.repo.firm_paused():
                    now = time.monotonic()
                    parallel_limit = min(max(1, int(getattr(self.config, "reddit_parallel_limit", 3) or 3)), 8)
                    if last_network_poll <= 0.0 or now - last_network_poll >= 60.0:
                        last_network_poll = now
                        await self.poll("real", community, max_dispatches=parallel_limit, require_enabled=True)
                    else:
                        self.dispatch("real", max_dispatches=parallel_limit)
            except asyncio.CancelledError:
                raise
            except Exception:
                # A temporarily unavailable database must not kill the local
                # monitor. Poll diagnostics are recorded when storage works.
                pass
            try:
                await asyncio.wait_for(stop.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass
