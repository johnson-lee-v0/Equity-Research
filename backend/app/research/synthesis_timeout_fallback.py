"""Receipt-bound review of an intact initial analysis after an optional timeout."""
from __future__ import annotations

import hashlib
from typing import Any

from ..db import digest, json_loads, utc_now
from .decision_questions import is_five_question_contract

EVENT = "synthesis_continuation_timeout_fallback"
TIMEOUT = "Codex execution exceeded its configured timeout."
LIMITATION = (
    "The optional follow-up analysis exhausted its timeout retries and produced no usable output. "
    "Final review uses the completed initial analysis and retained follow-up evidence directly; "
    "the follow-up analyst assessment remains unavailable and its evidence gap is not assumed resolved."
)


def _completed_output(conn: Any, task: Any, namespace: str) -> tuple[Any, dict] | None:
    if not task or task["status"] != "completed" or not task["output_id"] or task["pause_requested"]:
        return None
    output = conn.execute("SELECT * FROM outputs WHERE id=? AND task_id=? AND provenance=?",
                          (task["output_id"], task["id"], namespace)).fetchone()
    attempt = conn.execute("SELECT * FROM task_attempts WHERE id=? AND task_id=?",
                           (task["current_attempt_id"], task["id"])).fetchone()
    usable_statuses = {"completed", "needs_review"}
    if task["agent_id"] == "A01":
        usable_statuses.add("insufficient_evidence")
    if (not output or not attempt or attempt["status"] != "completed"
            or output["attempt_id"] != attempt["id"]
            or output["agent_id"] != task["agent_id"] or output["status"] not in usable_statuses
            or digest(json_loads(output["payload_json"], {})) != output["output_hash"]
            or conn.execute("SELECT 1 FROM invalidations WHERE output_id=?", (output["id"],)).fetchone()):
        return None
    versions = json_loads(attempt["source_versions_json"], {})
    if not isinstance(versions, dict) or not versions:
        return None
    return output, versions


def _source_bindings(conn: Any, namespace: str, versions: dict) -> dict | None:
    retained = {}
    for sid, frozen in versions.items():
        if not isinstance(sid, str) or not isinstance(frozen, dict):
            return None
        expected_hash = frozen.get("hash") or frozen.get("content_hash")
        version_no = str(frozen.get("version") or "")
        source = conn.execute("SELECT original_content,content_hash FROM sources WHERE id=? AND namespace=?", (sid, namespace)).fetchone()
        version = conn.execute("SELECT version_no,content,content_hash FROM source_versions WHERE source_id=? ORDER BY version_no DESC LIMIT 1", (sid,)).fetchone()
        if (not source or not version or not expected_hash or not version_no
                or str(version["version_no"]) != version_no
                or source["content_hash"] != expected_hash or version["content_hash"] != expected_hash
                or hashlib.sha256(str(source["original_content"] or "").encode()).hexdigest() != expected_hash
                or hashlib.sha256(str(version["content"] or "").encode()).hexdigest() != expected_hash
                or conn.execute("SELECT 1 FROM sources WHERE supersedes_source_id=?", (sid,)).fetchone()):
            return None
        retained[sid] = {"version": version_no, "hash": expected_hash}
    return retained


def _eligible(conn: Any, run_id: str, task_id: str) -> dict | None:
    run = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    task = conn.execute("SELECT * FROM tasks WHERE id=? AND run_id=?", (task_id, run_id)).fetchone()
    if (not run or not task or run["namespace"] != "real" or run["mode"] != "research"
            or run["cancel_requested"] or run["pause_requested"] or run["status"] in {"paused", "cancelled"}
            or task["pause_requested"] or task["agent_id"] != "A03"
            or task["kind"] != "research_synthesis_continuation_1" or task["status"] != "failed"
            or task["output_id"] or int(task["retry_count"]) < int(task["retry_limit"])):
        return None
    snapshot = json_loads(run["input_snapshot_json"], {})
    if (not is_five_question_contract(snapshot.get("research_contract"))
            or snapshot.get("workflow_variant") != "lean" or not snapshot.get("lean_continuation_used")
            or not snapshot.get("lean_continuation_cio_reused") or snapshot.get("assessment_pipeline")):
        return None
    attempts = conn.execute("SELECT * FROM task_attempts WHERE task_id=? ORDER BY attempt_no", (task_id,)).fetchall()
    if (len(attempts) != int(task["retry_count"]) + 1 or len(attempts) < int(task["retry_limit"]) + 1
            or not attempts or attempts[-1]["id"] != task["current_attempt_id"]
            or any(row["status"] != "failed" or row["provider"] != "codex" or row["error"] != TIMEOUT
                   or not row["provider_started_at"] for row in attempts)
            or conn.execute("SELECT 1 FROM outputs WHERE task_id=?", (task_id,)).fetchone()):
        return None
    initial = conn.execute("SELECT * FROM tasks WHERE run_id=? AND agent_id='A03' AND kind='research_synthesis'", (run_id,)).fetchall()
    discovery = conn.execute("SELECT * FROM tasks WHERE run_id=? AND agent_id='A01' AND kind='universe_discovery_continuation_1'", (run_id,)).fetchall()
    if len(initial) != 1 or len(discovery) != 1:
        return None
    initial_bound = _completed_output(conn, initial[0], run["namespace"])
    discovery_bound = _completed_output(conn, discovery[0], run["namespace"])
    if not initial_bound or not discovery_bound or initial_bound[0]["id"] != snapshot.get("lean_continuation_output_id"):
        return None
    cio = conn.execute("SELECT * FROM tasks WHERE id=? AND run_id=? AND agent_id='A11' AND kind='cio_review'",
                       (snapshot.get("lean_continuation_cio_task_id"), run_id)).fetchone()
    if not cio or cio["status"] == "cancelled" or cio["pause_requested"]:
        return None
    if conn.execute("SELECT 1 FROM tasks WHERE run_id=? AND id NOT IN (?,?) AND status!='completed' LIMIT 1",
                    (run_id, task_id, cio["id"])).fetchone():
        return None
    dependencies = {row[0] for row in conn.execute("SELECT depends_on_task_id FROM task_dependencies WHERE task_id=?", (cio["id"],))}
    discovery_dependencies = {row[0] for row in conn.execute("SELECT depends_on_task_id FROM task_dependencies WHERE task_id=?", (task_id,))}
    if dependencies != {task_id} or discovery_dependencies != {discovery[0]["id"]}:
        return None
    failed_versions = json_loads(attempts[-1]["source_versions_json"], {})
    if not isinstance(failed_versions, dict) or not failed_versions:
        return None
    bindings = _source_bindings(conn, run["namespace"], failed_versions)
    initial_bindings = _source_bindings(conn, run["namespace"], initial_bound[1])
    discovery_bindings = _source_bindings(conn, run["namespace"], discovery_bound[1])
    if (not bindings or not initial_bindings or not discovery_bindings
            or any(bindings.get(sid) != binding for sid, binding in (initial_bindings | discovery_bindings).items())
            or not set(snapshot.get("source_ids") or []).issubset(bindings)
            or not set(bindings).issubset(set(json_loads(cio["input_refs_json"], [])))):
        return None
    gap_ids = snapshot.get("lean_continuation_gap_ids")
    if not isinstance(gap_ids, list) or len(gap_ids) != 1:
        return None
    gap = conn.execute("SELECT * FROM research_gaps WHERE id=? AND namespace=? AND root_run_id=?",
                       (gap_ids[0], run["namespace"], run_id)).fetchone()
    if not gap or gap["status"] == "resolved" or gap["terminal_reason"] not in {None, "budget"}:
        return None
    return {
        "version": "synthesis-timeout-fallback.v1", "run_id": run_id, "task_id": task_id,
        "as_of": run["as_of"],
        "attempt_id": attempts[-1]["id"], "attempt_ids": [row["id"] for row in attempts],
        "original_output_id": initial_bound[0]["id"], "original_output_hash": initial_bound[0]["output_hash"],
        "discovery_output_id": discovery_bound[0]["id"], "discovery_output_hash": discovery_bound[0]["output_hash"],
        "cio_task_id": cio["id"], "source_versions": bindings, "gap_ids": gap_ids,
        "status": "partial", "message": LIMITATION,
    }


def valid_fallbacks(conn: Any, run_id: str) -> list[dict]:
    """A stored exception is usable only while all frozen inputs still match."""
    rows = conn.execute("SELECT task_id,attempt_id,payload_json FROM events WHERE run_id=? AND type=? ORDER BY sequence_id", (run_id, EVENT)).fetchall()
    if len(rows) != 1:
        return []
    saved = json_loads(rows[0]["payload_json"], {})
    current = _eligible(conn, run_id, rows[0]["task_id"])
    return [current] if current and current == saved and current["attempt_id"] == rows[0]["attempt_id"] else []


def activate_fallback(repo: Any, run_id: str, *, task_id: str, queue_run: bool = False) -> dict | None:
    """Admit only an exhausted optional Codex timeout; never redo analysis.

    ``queue_run`` supports explicit recovery after an older worker recorded
    the failure. It only revives the untouched dependency-blocked final review.
    The caller must separately authorize/schedule the same run.
    """
    with repo.db.transaction(immediate=True) as conn:
        run = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if (not run or run["status"] in {"completed", "cancelled", "paused"}
                or (not queue_run and repo._firm_dispatch_paused_conn(conn, run_id))):
            return None
        candidate = _eligible(conn, run_id, task_id)
        if not candidate:
            return None
        cio = conn.execute("SELECT * FROM tasks WHERE id=?", (candidate["cio_task_id"],)).fetchone()
        if (cio["status"] not in {"queued", "blocked"} or cio["current_attempt_id"] or cio["output_id"]
                or conn.execute("SELECT 1 FROM task_attempts WHERE task_id=?", (cio["id"],)).fetchone()
                or (cio["status"] == "blocked" and cio["error"] != "A required task did not produce a usable completed output.")
                or conn.execute("SELECT 1 FROM tasks WHERE run_id=? AND status IN ('running','waiting_review')", (run_id,)).fetchone()
                or conn.execute("SELECT 1 FROM task_attempts a JOIN tasks t ON t.id=a.task_id WHERE t.run_id=? AND a.status='running'", (run_id,)).fetchone()):
            return None
        prior = conn.execute("SELECT 1 FROM events WHERE run_id=? AND type=?", (run_id, EVENT)).fetchone()
        if prior:
            if not valid_fallbacks(conn, run_id):
                return None
        else:
            repo.db.emit(conn, namespace=run["namespace"], event_type=EVENT, run_id=run_id,
                         task_id=task_id, attempt_id=candidate["attempt_id"], payload=candidate)
        if queue_run:
            now = utc_now()
            if cio["status"] == "blocked":
                conn.execute("UPDATE tasks SET status='queued',blocked_reason=NULL,error=NULL,finished_at=NULL,dispatch_state='queued',wait_reason=NULL,terminal_summary=NULL,updated_at=? WHERE id=?", (now, cio["id"]))
            # Match Repository.set_run_status without a nested transaction;
            # no attempt, output, source snapshot, or gap status is rewritten.
            conn.execute("UPDATE runs SET status='queued',error=NULL,finished_at=NULL,updated_at=? WHERE id=?", (now, run_id))
            repo.db.emit(conn, namespace=run["namespace"], event_type="queued", run_id=run_id,
                         payload={"message": "Final review queued using the intact initial analysis after optional timeout."})
        return candidate
