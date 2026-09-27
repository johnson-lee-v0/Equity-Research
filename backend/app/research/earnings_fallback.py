"""Code-owned recovery from bounded supplemental discovery, never model output."""
from __future__ import annotations

import hashlib
from typing import Any

from ..db import json_dumps, json_loads, utc_now
from .decision_questions import is_five_question_contract


DISCOVERY_GAP = "Supplemental public discovery exceeded its tool limit and did not produce a usable research output. Analysis uses the attached earnings archive with incomplete source coverage."
VALUATION_GAP = "The archive fallback does not establish current valuation or portfolio holdings. Missing dated valuation inputs and verified position records remain unknown; use conditional conclusions and defer unsupported recommendations."


def is_bounded_discovery_error(error: str) -> bool:
    return any(marker in str(error).lower() for marker in (
        "bounded discovery search-query limit", "bounded discovery web-action limit",
    ))


def _eligible(conn: Any, run_id: str, task_id: str) -> dict[str, Any] | None:
    run = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    task = conn.execute("SELECT * FROM tasks WHERE id=? AND run_id=?", (task_id, run_id)).fetchone()
    if not run or not task or run["namespace"] != "real" or run["mode"] != "research" or run["cancel_requested"]:
        return None
    snapshot = json_loads(run["input_snapshot_json"], {})
    if not is_five_question_contract(snapshot.get("research_contract")) or snapshot.get("workflow_variant") != "lean":
        return None
    if task["agent_id"] != "A01" or task["kind"] != "universe_discovery" or task["status"] not in {"blocked", "failed"} or task["output_id"]:
        return None
    attempt = conn.execute("SELECT * FROM task_attempts WHERE id=? AND task_id=?", (task["current_attempt_id"], task_id)).fetchone()
    if not attempt or attempt["status"] not in {"blocked", "failed"} or not is_bounded_discovery_error(attempt["error"] or ""):
        return None
    if conn.execute("SELECT 1 FROM outputs WHERE task_id=? LIMIT 1", (task_id,)).fetchone():
        return None
    ref = str(run["origin_ref"] or "")
    if not ref.startswith("workflow:"):
        return None
    workflow = conn.execute("SELECT * FROM research_workflow_runs WHERE id=? AND research_run_id=?", (ref[9:], run_id)).fetchone()
    if not workflow or workflow["workflow"] != "earnings" or workflow["namespace"] != run["namespace"] or workflow["ticker"] != run["ticker"] or workflow["status"] not in {"completed", "partial"}:
        return None
    steps = {row["agent_id"]: row["status"] for row in conn.execute("SELECT agent_id,status FROM research_workflow_steps WHERE run_id=?", (workflow["id"],))}
    if any(steps.get(key) != "completed" for key in ("resolve", "locate", "acquire")) or steps.get("publish") not in {"completed", "partial"}:
        return None
    package = json_loads(workflow["result_json"], {})
    company = package.get("company") or {}
    if company.get("ticker") != run["ticker"] or not company.get("cik"):
        return None
    source_ids = package.get("source_ids")
    requested = snapshot.get("requested_source_ids") or []
    if not isinstance(source_ids, list) or not source_ids or not set(source_ids).issubset(set(requested)):
        return None
    versions = {item.get("id"): item for item in snapshot.get("source_versions", []) if isinstance(item, dict)}
    retained = {}
    for source_id in source_ids:
        source = conn.execute("SELECT * FROM sources WHERE id=? AND namespace=?", (source_id, run["namespace"])).fetchone()
        version = conn.execute("SELECT * FROM source_versions WHERE source_id=? ORDER BY version_no DESC LIMIT 1", (source_id,)).fetchone()
        frozen = versions.get(source_id) or {}
        if not source or not version or not str(source["original_content"] or "").strip() or not str(source["url"] or "").startswith(("https://", "http://")):
            return None
        expected_hash = str(frozen.get("content_hash") or "")
        if (not expected_hash or source["content_hash"] != expected_hash or version["content_hash"] != expected_hash
                or version["version_no"] != frozen.get("version")
                or hashlib.sha256(source["original_content"].encode()).hexdigest() != expected_hash
                or hashlib.sha256(version["content"].encode()).hexdigest() != expected_hash):
            return None
        # A later amendment is a new research packet, not this frozen fallback.
        if conn.execute("SELECT 1 FROM sources WHERE supersedes_source_id=? LIMIT 1", (source_id,)).fetchone():
            return None
        retained[source_id] = {"version": version["version_no"], "content_hash": expected_hash}
    return {"run_id": run_id, "task_id": task_id, "attempt_id": attempt["id"], "workflow_id": workflow["id"],
            "ticker": run["ticker"], "source_versions": retained, "reason": attempt["error"],
            "gaps": list(dict.fromkeys([DISCOVERY_GAP, VALUATION_GAP, *[
                str(gap) for key in ("gaps", "material_gaps", "comparison_gaps") for gap in package.get(key, []) if gap
            ]]))}


def valid_fallbacks(conn: Any, run_id: str) -> list[dict[str, Any]]:
    """Revalidate identity, attempt and retained bytes at every use."""
    result = []
    for row in conn.execute("SELECT * FROM earnings_discovery_fallbacks WHERE run_id=?", (run_id,)).fetchall():
        candidate = _eligible(conn, run_id, row["task_id"])
        if candidate and candidate["attempt_id"] == row["attempt_id"] and candidate["workflow_id"] == row["workflow_id"] and candidate["source_versions"] == json_loads(row["source_versions_json"], {}):
            result.append(candidate | {"status": "partial", "created_at": row["created_at"], "gaps": json_loads(row["gaps_json"], [])})
    return result


def activate_fallback(repo: Any, run_id: str, *, task_id: str | None = None, queue_run: bool = False) -> dict[str, Any] | None:
    """Save an auditable exception to one dependency, without dispatching work."""
    with repo.db.transaction(immediate=True) as conn:
        run = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not run or run["status"] in {"completed", "cancelled"} or run["cancel_requested"]:
            return None
        tasks = conn.execute("SELECT id FROM tasks WHERE run_id=? AND agent_id='A01' AND kind='universe_discovery'", (run_id,)).fetchall()
        if len(tasks) != 1 or (task_id and task_id != tasks[0]["id"]):
            return None
        candidate = _eligible(conn, run_id, tasks[0]["id"])
        if not candidate:
            return None
        if queue_run and conn.execute("SELECT 1 FROM tasks WHERE run_id=? AND id!=? AND (status='running' OR (status IN ('blocked','failed','cancelled') AND current_attempt_id IS NOT NULL)) LIMIT 1", (run_id, candidate["task_id"])).fetchone():
            return None
        prior = conn.execute("SELECT * FROM earnings_discovery_fallbacks WHERE task_id=?", (candidate["task_id"],)).fetchone()
        if prior and not valid_fallbacks(conn, run_id):
            return None
        now = utc_now()
        if not prior:
            conn.execute("INSERT INTO earnings_discovery_fallbacks(task_id,run_id,attempt_id,workflow_id,source_versions_json,gaps_json,reason,created_at) VALUES(?,?,?,?,?,?,?,?)", (
                candidate["task_id"], run_id, candidate["attempt_id"], candidate["workflow_id"], json_dumps(candidate["source_versions"]), json_dumps(candidate["gaps"]), candidate["reason"], now,
            ))
            repo.db.emit(conn, namespace=run["namespace"], event_type="earnings_archive_fallback", run_id=run_id, task_id=candidate["task_id"], attempt_id=candidate["attempt_id"], payload=candidate | {"status": "partial", "message": DISCOVERY_GAP})
        if queue_run:
            # Only dependency-blocked tasks with no attempted generation may
            # resume. A downstream provider failure remains a separate failure.
            descendants = {row[0] for row in conn.execute("WITH RECURSIVE children(id) AS (SELECT task_id FROM task_dependencies WHERE depends_on_task_id=? UNION SELECT d.task_id FROM task_dependencies d JOIN children c ON d.depends_on_task_id=c.id) SELECT id FROM children", (candidate["task_id"],))}
            for child in conn.execute("SELECT * FROM tasks WHERE run_id=? AND status='blocked' AND current_attempt_id IS NULL", (run_id,)).fetchall():
                if child["id"] in descendants and child["error"] == "A required task did not produce a usable completed output.":
                    conn.execute("UPDATE tasks SET status='queued',blocked_reason=NULL,error=NULL,finished_at=NULL,dispatch_state='queued',wait_reason=NULL,terminal_summary=NULL,updated_at=? WHERE id=?", (now, child["id"]))
            conn.execute("UPDATE runs SET status=CASE WHEN pause_requested=1 THEN 'paused' ELSE 'queued' END,error=NULL,finished_at=NULL,updated_at=? WHERE id=?", (now, run_id))
        return candidate | {"status": "partial", "created_at": prior["created_at"] if prior else now}
