"""One company library for current research and retained historical findings.

The historical worker is never imported or executed. Live answers are projected
from immutable research outputs; their original source relationships survive.
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query


def import_desk(source: Path, evidence_dir: Path) -> dict[str, int]:
    if not source.is_file():
        raise ValueError("The original research database does not exist.")
    directory = evidence_dir / "research-library"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = directory / "desk.sqlite3"
    if source.resolve() == destination.resolve():
        raise ValueError("Choose the original database, not the retained snapshot.")
    handle, staged = tempfile.mkstemp(prefix="desk-", suffix=".sqlite3", dir=directory)
    os.close(handle)
    try:
        with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as original:
            with closing(sqlite3.connect(staged)) as target:
                original.backup(target)
                if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise ValueError("The research database failed its integrity check.")
                counts = {name: target.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                          for name in ("ideas", "packets", "events")}
        os.chmod(staged, 0o600)
        os.replace(staged, destination)
        return counts
    finally:
        Path(staged).unlink(missing_ok=True)


def create_library_router(evidence_dir: Path, repo: Any = None) -> APIRouter:
    from .library_store import LibraryStore, historical_questions, normalize_ticker

    router = APIRouter(prefix="/api/research-library", tags=["Research library"])
    database = evidence_dir / "research-library" / "desk.sqlite3"
    live = LibraryStore(repo, evidence_dir) if repo else None

    def namespace_value(namespace):
        if namespace not in {"real", "demo", "simulation"}:
            raise HTTPException(422, "Unknown library namespace.")
        return namespace

    def connect() -> sqlite3.Connection:
        db = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        return db

    def unpack(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["data"] = json.loads(value["data"])
        return value

    def historical_list(namespace):
        if namespace != "real" or not database.is_file():
            return []
        with closing(connect()) as db:
            rows = db.execute("SELECT id,data,updated_at FROM ideas ORDER BY updated_at DESC,id").fetchall()
            counts = dict(db.execute("SELECT idea_id,COUNT(*) FROM packets GROUP BY idea_id"))
        items = []
        for row in rows:
            idea = json.loads(row["data"])
            packet = idea.get("packet") or {}
            items.append({"id": row["id"], "ticker": normalize_ticker(idea.get("ticker")) or idea.get("ticker", ""),
                    "name": idea.get("name", ""), "archived": bool(idea.get("archived")),
                    "disposition": packet.get("disposition") or idea.get("disposition", ""),
                    "summary": packet.get("summary") or idea.get("legacySummary") or idea.get("whyWaiting", ""),
                    "researched_at": packet.get("researchedAt") or idea.get("reviewedAt"),
                    "updated_at": row["updated_at"], "revisions": counts.get(row["id"], 0),
                    "namespace": "real", "live": False, "historical": True})
        return items

    def historical_detail(idea_id, namespace):
        if namespace != "real" or not database.is_file():
            return None
        with closing(connect()) as db:
            row = db.execute("SELECT * FROM ideas WHERE id=?", (idea_id,)).fetchone()
            if row is None:
                return None
            packets = db.execute("SELECT * FROM packets WHERE idea_id=? ORDER BY id DESC", (idea_id,)).fetchall()
            events = db.execute("SELECT * FROM events WHERE idea_id=? ORDER BY id DESC", (idea_id,)).fetchall()
        idea = unpack(row)
        history = [unpack(p) for p in packets]
        for packet in history:
            packet["questions"] = historical_questions(packet["data"], prefix=f"legacy-{packet['id']}")
        return {"idea": idea, "packets": history, "events": [unpack(e) for e in events],
                "historical": True, "live": False, "namespace": "real", "ticker": idea["data"].get("ticker", ""),
                "questions": historical_questions(idea["data"].get("packet")), "runs": [], "workflows": []}

    def catalog(namespace):
        retained = historical_list(namespace)
        current = live.list(namespace) if live else []
        # The retained idea ID remains a valid bookmark after a live review.
        merged = {item["ticker"] or item["id"]: item for item in retained}
        for item in current:
            key = item["ticker"]
            old = merged.get(key)
            if old:
                merged[key] = old | item | {"id": old["id"], "live_id": item["id"], "historical": True,
                    "name": item["name"] or old["name"], "revisions": old["revisions"] + item["revisions"],
                    "summary": item["summary"] if item["revisions"] else old["summary"]}
            else:
                merged[key] = item
        return sorted(merged.values(), key=lambda item: (item["updated_at"], item["id"]), reverse=True)

    @router.get("")
    def list_findings(q: str = Query("", max_length=200), namespace: str = Query("real")):
        namespace_value(namespace)
        all_items = catalog(namespace)
        items = [item for item in all_items if q.casefold() in " ".join(str(item.get(k) or "") for k in ("ticker", "name", "summary")).casefold()]
        return {"items": items, "imported": namespace == "real" and database.is_file(), "total": len(all_items)}

    @router.get("/{idea_id}")
    def finding(idea_id: str, namespace: str = Query("real")):
        namespace_value(namespace)
        item = next((item for item in catalog(namespace) if idea_id in {item["id"], item.get("live_id")}), None)
        if not item:
            raise HTTPException(404, "Research finding not found.")
        retained = historical_detail(item["id"], namespace) if item.get("historical") else None
        current = live.detail(item.get("live_id") or item["id"], namespace) if live and item.get("live") else None
        if current and retained:
            historical_packets = retained["packets"]
            # Preserve the retained current packet even if the snapshot's
            # revision table did not contain a matching revision.
            current["historical_idea"] = retained["idea"]
            retained_current = retained["idea"]["data"].get("packet", {})
            if retained_current and not any(p["data"] == retained_current for p in historical_packets):
                current["packets"].append({"id": "historical-current", "at": retained_current.get("researchedAt"),
                    "data": retained_current, "questions": retained["questions"]})
            current["packets"].extend(historical_packets)
            current["events"] = retained["events"]
            current["historical"] = True
            if not current["idea"]["data"].get("name"):
                current["idea"]["data"]["name"] = retained["idea"]["data"].get("name", "")
            if not any(q.get("answer") for q in current["questions"]):
                current["questions"] = retained["questions"]
                current["idea"]["data"]["packet"] = retained["idea"]["data"].get("packet", {})
            return current
        return current or retained

    return router
