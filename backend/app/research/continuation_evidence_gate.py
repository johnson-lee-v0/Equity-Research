"""Skip only an untouched synthesis whose completed discovery added no evidence."""
from __future__ import annotations

from typing import Any

from ..db import digest, json_loads, utc_now
from .decision_questions import is_five_question_contract, validate_five_question_payload
from .synthesis_timeout_fallback import _completed_output, _source_bindings

EVENT = "continuation_no_new_evidence"
SKIPPED = "Skipped: follow-up discovery added no new verified evidence."
LIMITATION = (
    "Follow-up discovery added no new verified evidence, so the redundant analysis was skipped. "
    "Final review uses the intact initial analysis; the selected evidence gap remains unresolved."
)


def _source_identities(conn: Any, bindings: dict) -> dict[str, dict]:
    identities = {}
    for sid, binding in bindings.items():
        row = conn.execute("SELECT url,source_type,publication_at,observed_at FROM sources WHERE id=?", (sid,)).fetchone()
        identities[sid] = dict(row) | {"content_hash": binding["hash"]}
    return identities


def freeze_baseline(conn: Any, namespace: str, versions: dict) -> dict | None:
    bindings = _source_bindings(conn, namespace, versions)
    return {"source_versions": bindings, "source_identities": _source_identities(conn, bindings)} if bindings else None


def _facts(conn: Any, output: Any, identities: dict) -> set[str] | None:
    """Compare code-validated assertions, not model-authored novelty labels."""
    payload = json_loads(output["payload_json"], {})
    claims = payload.get("fact_claims") or []
    signatures = set()
    for row in conn.execute("SELECT claim_index FROM output_claims WHERE output_id=? AND validation_status='validated'", (output["id"],)):
        index = row[0]
        if not isinstance(index, int) or index < 0 or index >= len(claims) or not isinstance(claims[index], dict):
            return None
        claim = claims[index]
        identity = identities.get(claim.get("source_ref"))
        if identity is None:
            return None
        fields = ("claim", "subject", "metric", "value", "unit", "currency", "basis", "scale", "period", "period_start", "period_end", "statement_type", "locator", "source_quote")
        signatures.add(digest({key: claim.get(key) for key in fields} | {"source": identity}))
    return signatures


def _candidate(conn: Any, run_id: str, task_id: str, *, activate: bool = False) -> dict | None:
    run = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    task = conn.execute("SELECT * FROM tasks WHERE id=? AND run_id=?", (task_id, run_id)).fetchone()
    if (not run or not task or run["namespace"] != "real" or run["mode"] != "research"
            or run["cancel_requested"] or run["pause_requested"] or run["status"] in {"paused", "cancelled"}
            or task["pause_requested"] or task["agent_id"] != "A03"
            or task["kind"] != "research_synthesis_continuation_1"
            or task["current_attempt_id"] or task["output_id"]
            or conn.execute("SELECT 1 FROM task_attempts WHERE task_id=?", (task_id,)).fetchone()
            or conn.execute("SELECT 1 FROM outputs WHERE task_id=?", (task_id,)).fetchone()):
        return None
    if activate:
        if task["status"] != "queued":
            return None
    elif task["status"] != "cancelled" or task["terminal_summary"] != SKIPPED:
        return None
    snapshot = json_loads(run["input_snapshot_json"], {})
    if (not is_five_question_contract(snapshot.get("research_contract"))
            or snapshot.get("workflow_variant") != "lean" or not snapshot.get("lean_continuation_used")
            or not snapshot.get("lean_continuation_cio_reused") or snapshot.get("assessment_pipeline")):
        return None
    initial = conn.execute("SELECT * FROM tasks WHERE run_id=? AND agent_id='A03' AND kind='research_synthesis'", (run_id,)).fetchall()
    discovery = conn.execute("SELECT * FROM tasks WHERE run_id=? AND agent_id='A01' AND kind='universe_discovery_continuation_1'", (run_id,)).fetchall()
    if len(initial) != 1 or len(discovery) != 1:
        return None
    original = _completed_output(conn, initial[0], run["namespace"])
    followup = _completed_output(conn, discovery[0], run["namespace"])
    if (not original or not followup
            or original[0]["id"] != snapshot.get("lean_continuation_output_id")):
        return None
    original_payload = json_loads(original[0]["payload_json"], {})
    candidates = original_payload.get("candidate_briefs")
    if (not is_five_question_contract(original_payload.get("research_contract"))
            or not isinstance(candidates, list) or len(candidates) != 1 or not isinstance(candidates[0], dict)
            or not run["ticker"]
            or str(candidates[0].get("ticker") or "").strip().upper().replace(".", "-") != str(run["ticker"]).strip().upper().replace(".", "-")
            or validate_five_question_payload(original_payload)):
        return None
    cio = conn.execute("SELECT * FROM tasks WHERE id=? AND run_id=? AND agent_id='A11' AND kind='cio_review'", (snapshot.get("lean_continuation_cio_task_id"), run_id)).fetchone()
    if not cio or cio["status"] == "cancelled" or cio["pause_requested"]:
        return None
    if activate and (cio["status"] != "queued" or cio["current_attempt_id"] or cio["output_id"]
                     or conn.execute("SELECT 1 FROM task_attempts WHERE task_id=?", (cio["id"],)).fetchone()):
        return None
    if conn.execute("SELECT 1 FROM tasks WHERE run_id=? AND id NOT IN (?,?) AND status!='completed' LIMIT 1", (run_id, task_id, cio["id"])).fetchone():
        return None
    dependencies = {row[0] for row in conn.execute("SELECT depends_on_task_id FROM task_dependencies WHERE task_id=?", (cio["id"],))}
    discovery_dependencies = {row[0] for row in conn.execute("SELECT depends_on_task_id FROM task_dependencies WHERE task_id=?", (task_id,))}
    if dependencies != {task_id} or discovery_dependencies != {discovery[0]["id"]}:
        return None
    baseline = _source_bindings(conn, run["namespace"], original[1])
    bindings = _source_bindings(conn, run["namespace"], followup[1])
    if (not baseline or not bindings or any(bindings.get(sid) != value for sid, value in baseline.items())
            or not set(snapshot.get("source_ids") or []).issubset(bindings)
            or not set(bindings).issubset(set(json_loads(cio["input_refs_json"], [])))):
        return None
    identities = _source_identities(conn, bindings)
    baseline_identities = {sid: identities[sid] for sid in baseline}
    frozen = snapshot.get("lean_continuation_evidence_baseline")
    if (not isinstance(frozen, dict) or frozen.get("source_versions") != baseline
            or frozen.get("source_identities") != baseline_identities):
        return None
    # Same bytes under a different URL/date are conservatively new. A genuine
    # duplicate must preserve both the content and its source metadata.
    prior_fingerprints = {digest(value) for value in baseline_identities.values()}
    if any(digest(value) not in prior_fingerprints for value in identities.values()):
        return None
    if any(not value.get("url") or not value.get("source_type") or not value.get("publication_at")
           for sid, value in identities.items() if sid not in baseline):
        return None
    initial_facts = _facts(conn, original[0], identities)
    new_facts = _facts(conn, followup[0], identities)
    if initial_facts is None or new_facts is None or new_facts - initial_facts:
        return None
    gap_ids = snapshot.get("lean_continuation_gap_ids")
    if not isinstance(gap_ids, list) or len(gap_ids) != 1:
        return None
    gap = conn.execute("SELECT * FROM research_gaps WHERE id=? AND namespace=? AND root_run_id=?", (gap_ids[0], run["namespace"], run_id)).fetchone()
    if not gap or gap["status"] == "resolved" or gap["terminal_reason"] not in {None, "budget", "no_new_evidence"}:
        return None
    return {"version": "continuation-evidence-gate.v1", "run_id": run_id, "task_id": task_id,
            "as_of": run["as_of"], "original_output_id": original[0]["id"], "original_output_hash": original[0]["output_hash"],
            "discovery_task_id": discovery[0]["id"], "attempt_id": discovery[0]["current_attempt_id"],
            "discovery_output_id": followup[0]["id"], "discovery_output_hash": followup[0]["output_hash"],
            "cio_task_id": cio["id"], "source_versions": bindings, "source_identities": identities,
            "initial_fact_signatures": sorted(initial_facts), "followup_fact_signatures": sorted(new_facts),
            "gap_ids": gap_ids, "new_source_count": 0, "new_verified_fact_count": 0, "status": "skipped", "message": LIMITATION}


def valid_receipts(conn: Any, run_id: str) -> list[dict]:
    rows = conn.execute("SELECT task_id,attempt_id,payload_json FROM events WHERE run_id=? AND type=? ORDER BY sequence_id", (run_id, EVENT)).fetchall()
    if len(rows) != 1:
        return []
    saved = json_loads(rows[0]["payload_json"], {})
    current = _candidate(conn, run_id, rows[0]["task_id"])
    return [current] if current and current == saved and current["attempt_id"] == rows[0]["attempt_id"] else []


def review_allowed(conn: Any, run_id: str, task_id: str) -> bool:
    rows = conn.execute("SELECT task_id FROM events WHERE run_id=? AND type=?", (run_id, EVENT)).fetchall()
    allowed = {item["task_id"] for item in valid_receipts(conn, run_id) if item["cio_task_id"] == task_id}
    return all(row["task_id"] in allowed for row in rows)


def activate_conn(repo: Any, conn: Any, run_id: str, task_id: str) -> dict | None:
    """Cancel only never-started redundant work; no fake attempt or output."""
    previous = conn.execute("SELECT 1 FROM events WHERE run_id=? AND type=?", (run_id, EVENT)).fetchone()
    if previous:
        return next((item for item in valid_receipts(conn, run_id) if item["task_id"] == task_id), None)
    if repo._firm_dispatch_paused_conn(conn, run_id):
        return None
    receipt = _candidate(conn, run_id, task_id, activate=True)
    if receipt is None:
        return None
    now = utc_now()
    conn.execute("UPDATE tasks SET status='cancelled',finished_at=?,updated_at=?,dispatch_state='finished',wait_reason=NULL,terminal_summary=?,progress_message=?,error=NULL,blocked_reason=NULL WHERE id=?", (now, now, SKIPPED, SKIPPED, task_id))
    conn.execute("UPDATE research_gaps SET status='terminal',terminal_reason='no_new_evidence',resolved_by_output_id=NULL,repair_run_id=NULL,updated_at=? WHERE id=? AND status IN ('open','in_progress','terminal')", (now, receipt["gap_ids"][0]))
    namespace = conn.execute("SELECT namespace FROM runs WHERE id=?", (run_id,)).fetchone()[0]
    repo.db.emit(conn, namespace=namespace, event_type=EVENT, run_id=run_id, task_id=task_id,
                 attempt_id=receipt["attempt_id"], payload=receipt)
    return receipt
