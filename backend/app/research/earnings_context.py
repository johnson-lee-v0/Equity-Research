"""A bounded, source-bound earnings package for the Researcher and CIO."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from ..db import digest, json_dumps, json_loads, new_id, utc_now
from .decision_questions import is_five_question_contract
from .earnings_fallback import valid_fallbacks
from .earnings_trends import VALIDATION_VERSION

VERSION = "earnings-research-context.v1"
MAX_CONTEXT_CHARS = 60_000
MAX_TRANSCRIPT_CHARS = 100_000
CLAIM_INSTRUCTION = (
    "Use the earnings context to examine every supplied historical period and dated guidance revision; "
    "read the latest transcript's complete management answers and qualifiers. Historical observations "
    "describe their stated periods, not current valuation. Keep management capex separate from cash PP&E. "
    "For fact_claims use one atomic value, one metric, one geography and one reporting period per claim. "
    "Never put combined values, semicolon summaries, or paraphrases into value. Copy a verbatim source_quote "
    "and its original locator. Numeric value must be a single number with separate supported unit/scale; "
    "a text value must occur exactly in that quote. Include only issuer/period/basis metadata actually bound "
    "by the cited passage. If the strict binder cannot establish a claim, retain it as a cited management "
    "statement or research inference in the answer, not a verified fact or calculation input. "
    "The context's quote checks do not replace investment fact validation or establish independent corroboration."
)


def verified_earnings_package(conn: Any, run_id: str, *, ticker: str | None = None, frozen_receipt: dict | None = None) -> dict[str, Any] | None:
    """Require reciprocal workflow identity and its unchanged original archive."""
    run = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    if not run or run["namespace"] != "real":
        return None
    snapshot = json_loads(run["input_snapshot_json"], {})
    symbol = ticker or run["ticker"]
    receipt = next((item for item in (snapshot.get("investment_process") or {}).get("earnings", [])
                    if item.get("ticker") == symbol and item.get("status") in {"completed", "partial"}), None)
    if frozen_receipt is not None:
        if frozen_receipt.get("ticker") != symbol or frozen_receipt.get("status") not in {"completed", "partial"}:
            return None
        receipt = frozen_receipt
    original_workflow = str(run["origin_ref"] or "").startswith("workflow:") and (not ticker or ticker == run["ticker"])
    identifier = receipt.get("workflow_id") if receipt else run["origin_ref"][9:] if original_workflow else None
    if not identifier:
        return None
    workflow = conn.execute("SELECT * FROM research_workflow_runs WHERE id=?", (identifier,)).fetchone()
    if receipt:
        if not workflow or receipt.get("package_hash") != digest(json_loads(workflow["result_json"], {})):
            return None
    elif workflow and workflow["research_run_id"] != run_id:
        link = conn.execute("SELECT * FROM earnings_assessment_links WHERE run_id=? AND workflow_id=?", (run_id, workflow["id"])).fetchone()
        if not link or link["package_hash"] != digest(json_loads(workflow["result_json"], {})):
            return None
    if not workflow or workflow["workflow"] != "earnings" or workflow["namespace"] != run["namespace"] or workflow["ticker"] != symbol or workflow["status"] not in {"partial", "completed"}:
        return None
    steps = {row["agent_id"]: row["status"] for row in conn.execute("SELECT agent_id,status FROM research_workflow_steps WHERE run_id=?", (workflow["id"],))}
    if any(steps.get(key) != "completed" for key in ("resolve", "locate")) or steps.get("acquire") not in {"completed", "partial"} or steps.get("publish") not in {"completed", "partial"}:
        return None
    package = json_loads(workflow["result_json"], {})
    if (package.get("company") or {}).get("ticker") != symbol or not (package.get("company") or {}).get("cik"):
        return None
    ids = package.get("source_ids") or []
    attached_ids = snapshot.get("source_ids") if receipt else snapshot.get("requested_source_ids")
    if not ids or not set(ids).issubset(set(attached_ids or [])):
        return None
    frozen = {row.get("id"): row for row in snapshot.get("source_versions", []) if isinstance(row, dict)}
    bindings = {}
    for source_id in ids:
        source = conn.execute("SELECT * FROM sources WHERE id=? AND namespace=?", (source_id, run["namespace"])).fetchone()
        version = conn.execute("SELECT * FROM source_versions WHERE source_id=? ORDER BY version_no DESC LIMIT 1", (source_id,)).fetchone()
        expected = frozen.get(source_id) or {}
        if (not source or not version or not source["original_content"] or not str(source["url"] or "").startswith(("http://", "https://"))
                or source["content_hash"] != expected.get("content_hash") or version["content_hash"] != expected.get("content_hash")
                or version["version_no"] != expected.get("version")
                or hashlib.sha256(source["original_content"].encode()).hexdigest() != source["content_hash"]
                or hashlib.sha256(version["content"].encode()).hexdigest() != source["content_hash"]
                or conn.execute("SELECT 1 FROM sources WHERE supersedes_source_id=? LIMIT 1", (source_id,)).fetchone()):
            return None
        bindings[source_id] = {"version": version["version_no"], "content_hash": source["content_hash"]}
    return {"workflow_id": workflow["id"], "namespace": run["namespace"], "ticker": symbol, "package": package, "source_bindings": bindings}


def build_earnings_context(repo: Any, run_id: str, sources: list[dict[str, Any]], *, max_chars: int = MAX_CONTEXT_CHARS, ticker: str | None = None) -> dict[str, Any] | None:
    with repo.db.operation() as conn:
        verified = verified_earnings_package(conn, run_id, ticker=ticker)
    if not verified:
        return None
    source_by_id = {source["id"]: source for source in sources}
    bindings = verified["source_bindings"]
    for source_id, binding in bindings.items():
        source = source_by_id.get(source_id)
        if not source or source.get("namespace") != verified["namespace"] or source.get("version") != binding["version"] or source.get("content_hash") != binding["content_hash"] or hashlib.sha256(str(source.get("content") or "").encode()).hexdigest() != binding["content_hash"]:
            return None
    package = verified["package"]
    documents = package.get("documents") or {}
    transcript = documents.get("transcript") or {}
    transcript_id = transcript.get("source_id") if transcript.get("status") == "available" else None
    if transcript_id not in bindings or transcript.get("content_hash") != bindings[transcript_id]["content_hash"]:
        transcript_id = None
    result: dict[str, Any] = {
        "version": VERSION, "workflow_id": verified["workflow_id"], "ticker": verified["ticker"],
        "source_bindings": bindings, "latest_transcript_source_id": transcript_id,
        "event": {key: (package.get("event") or {}).get(key) for key in ("fiscal_period", "period_end", "earnings_date")},
        "instruction": CLAIM_INSTRUCTION, "series": [], "capex_guidance": {"comparisons": [], "explanations": []},
        "gaps": [str(gap)[:1000] for key in ("gaps", "material_gaps", "comparison_gaps") for gap in package.get(key, [])][:30],
        "projection": {"max_chars": max_chars, "omitted_items": 0, "unbound_items": 0},
    }
    normalized: dict[str, tuple[str, list[int]]] = {}

    def bind(value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict) or value.get("source_id") not in bindings or not value.get("quote"):
            result["projection"]["unbound_items"] += 1
            return None
        sid = value["source_id"]
        content = source_by_id[sid]["content"]
        if sid not in normalized:
            positions = [index for index, char in enumerate(content) if not char.isspace()]
            normalized[sid] = ("".join(content[index] for index in positions), positions)
        stripped, positions = normalized[sid]
        quote = re.sub(r"\s+", "", str(value["quote"]))
        start = stripped.find(quote)
        if not quote or start < 0:
            result["projection"]["unbound_items"] += 1
            return None
        if value.get("measure_basis") == "cash_ppe_plus_finance_lease_principal":
            # A computed capex total cites two financial facts and an issuer
            # definition. Verify all three archived quotations, not just the
            # cash operand used as its primary source locator.
            components = [*(value.get("calculation") or {}).get("inputs", []), value.get("definition_source")]
            if len(components) != 3 or any(not isinstance(component, dict) or not bind(component) for component in components):
                result["projection"]["unbound_items"] += 1
                return None
        low, high = positions[start], positions[start + len(quote) - 1] + 1
        first, last = content.count("\n", 0, low) + 1, content.count("\n", 0, high - 1) + 1
        return {**{key: item for key, item in value.items() if key not in {"url", "quote", "text"}},
                "quote": content[low:high], "locator": f"L{first}" if first == last else f"L{first}-L{last}",
                "source_version": bindings[sid]["version"], "content_hash": bindings[sid]["content_hash"]}

    def append(target: list, item: dict) -> bool:
        target.append(item)
        if len(json.dumps(result, ensure_ascii=False)) > max_chars - 500:
            target.pop()
            result["projection"]["omitted_items"] += 1
            return False
        return True

    trends = package.get("trends") or {}
    if trends.get("validation_version") == VALIDATION_VERSION:
        result["validation_version"] = VALIDATION_VERSION
        result["trend_status"] = trends.get("status")
        for series in trends.get("series", []):
            projected = {key: series.get(key) for key in ("id", "label", "unit", "frequency", "basis", "coverage_note")}
            projected["points"] = []
            if not append(result["series"], projected):
                continue
            for point in series.get("points", []):
                item = bind(point) if point.get("value") is not None or point.get("low") is not None else {"period": point.get("period"), "value": None, "status": "missing"}
                if item:
                    append(projected["points"], item)
        guidance = trends.get("capex_guidance") or {}
        # Keep the full comparison only when every numerical component has
        # its own retained source quote. Derived variances stay labelled.
        for comparison in guidance.get("comparisons", []):
            initial, actual = bind(comparison.get("initial_source")), bind(comparison.get("actual_source"))
            revisions = [bind(revision) for revision in comparison.get("revisions", [])]
            if initial and actual and all(revisions):
                append(result["capex_guidance"]["comparisons"], {
                    **{key: value for key, value in comparison.items() if key not in {"initial_source", "actual_source", "revisions"}},
                    "initial_source": initial, "actual_source": actual, "revisions": revisions,
                    "status": "workflow_calculation_from_quote_checked_observations",
                })
        for explanation in guidance.get("explanations", []):
            item = bind(explanation)
            if item:
                append(result["capex_guidance"]["explanations"], item)
        result["capex_guidance"]["coverage"] = str(guidance.get("coverage") or "")[:2000]
    else:
        result["gaps"].append("Validated historical trend observations are unavailable in this earnings package.")
    result["projection"]["status"] = "partial" if result["projection"]["omitted_items"] or result["projection"]["unbound_items"] else "complete"
    if len(json.dumps(result, ensure_ascii=False)) > max_chars:
        return None
    return result


def queue_earnings_reassessment(repo: Any, run_id: str) -> int:
    """Append one source-preserving A03/CIO revision; never dispatch or reset history."""
    with repo.db.transaction(immediate=True) as conn:
        run = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not run or run["cancel_requested"] or run["status"] == "cancelled":
            raise ValueError("An existing uncancelled earnings research case is required")
        verified = verified_earnings_package(conn, run_id)
        snapshot = json_loads(run["input_snapshot_json"], {})
        if not verified or run["mode"] != "research" or not is_five_question_contract(snapshot.get("research_contract")) or snapshot.get("workflow_variant") != "lean":
            raise ValueError("A five-question case with unchanged verified earnings archives is required")
        pending = conn.execute("SELECT id FROM tasks WHERE run_id=? AND (kind LIKE 'research_synthesis_earnings_revision_%' OR kind LIKE 'cio_review_earnings_revision_%') AND status IN ('queued','running','interrupted','waiting_evidence','waiting_review')", (run_id,)).fetchall()
        if pending:
            return 0
        if run["status"] != "completed":
            raise ValueError("Wait for the current earnings research decision to finish before reassessing it")
        prior = conn.execute("SELECT c.output_id,c.revision FROM case_decision_versions c JOIN outputs o ON o.id=c.output_id JOIN tasks t ON t.id=o.task_id WHERE c.run_id=? AND c.namespace=? AND t.run_id=c.run_id AND o.provenance=c.namespace AND t.agent_id='A11' AND t.status='completed' ORDER BY c.revision DESC LIMIT 1", (run_id, run["namespace"])).fetchone()
        if not prior:
            raise ValueError("A completed canonical CIO decision is required before earnings reassessment")
        accepted = {item["task_id"] for item in valid_fallbacks(conn, run_id)}
        tasks = conn.execute("SELECT * FROM tasks WHERE run_id=? ORDER BY sequence_no", (run_id,)).fetchall()
        if any(task["status"] != "completed" and task["id"] not in accepted for task in tasks):
            raise ValueError("Unresolved research tasks must be recovered before earnings reassessment")
        source_ids = list(dict.fromkeys(snapshot.get("source_ids") or []))
        all_source_versions = repo._source_version_snapshot_conn(conn, run["namespace"], source_ids)
        revision = 1 + sum(task["kind"].startswith("research_synthesis_earnings_revision_") for task in tasks)
        sequence = max((task["sequence_no"] for task in tasks), default=0) + 1
        now = utc_now()
        researcher, cio = new_id("task_"), new_id("task_")
        instruction = (
            "Reassess this same earnings case using the complete latest transcript and source-bound historical trend context. "
            "The prior provider packet truncated the call before key renewal, capex and Q&A passages. Re-evaluate those "
            "previous missing-data statements from the complete supplied evidence; do not copy unsupported prior fact claims. "
            "Retain genuine filing, current valuation and portfolio gaps. Use only the retained source packet; no discovery, "
            "web search, or new market retrieval is authorized for this context correction. " + CLAIM_INSTRUCTION
        )
        for task_id, agent, kind, offset, dependencies in (
            (researcher, "A03", f"research_synthesis_earnings_revision_{revision}", 0, []),
            (cio, "A11", f"cio_review_earnings_revision_{revision}", 1, [researcher]),
        ):
            conn.execute("INSERT INTO tasks(id,run_id,agent_id,kind,instruction,status,sequence_no,dependency_json,input_snapshot_hash,input_refs_json,retry_limit,timeout_seconds,created_at,updated_at) VALUES(?,?,?,?,?,'queued',?,?,?,?,1,?,?,?)", (
                task_id, run_id, agent, kind, instruction, sequence + offset, json_dumps(dependencies), digest(snapshot), json_dumps(source_ids), repo.config.codex_timeout_seconds, now, now,
            ))
        conn.execute("INSERT INTO task_dependencies(task_id,depends_on_task_id) VALUES(?,?)", (cio, researcher))
        conn.execute("UPDATE runs SET status=CASE WHEN pause_requested=1 THEN 'paused' ELSE 'queued' END,finished_at=NULL,error=NULL,updated_at=? WHERE id=?", (now, run_id))
        conn.execute("UPDATE run_dispatch_authorizations SET revoked_at=?,revocation_reason='earnings_context_refresh' WHERE run_id=? AND revoked_at IS NULL", (now, run_id))
        repo.db.emit(conn, namespace=run["namespace"], event_type="earnings_context_refresh_queued", run_id=run_id, payload={
            "message": "A source-preserving Researcher and CIO reassessment is queued; previous research remains in history.",
            "workflow_id": verified["workflow_id"], "prior_output_id": prior["output_id"], "prior_revision": prior["revision"],
            "context_version": VERSION, "source_bindings": verified["source_bindings"], "task_ids": [researcher, cio],
            "source_versions": all_source_versions,
        })
        return 2


def revision_packet_valid(conn: Any, task_id: str) -> bool:
    """Freeze every reassessment source, including market context, until dispatch."""
    task = conn.execute("SELECT t.*,r.namespace FROM tasks t JOIN runs r ON r.id=t.run_id WHERE t.id=?", (task_id,)).fetchone()
    if not task or "_earnings_revision_" not in task["kind"]:
        return True
    event = conn.execute("SELECT payload_json FROM events WHERE run_id=? AND type='earnings_context_refresh_queued' AND EXISTS(SELECT 1 FROM json_each(json_extract(payload_json,'$.task_ids')) WHERE value=?) ORDER BY sequence_id DESC LIMIT 1", (task["run_id"], task_id)).fetchone()
    payload = json_loads(event[0], {}) if event else {}
    verified = verified_earnings_package(conn, task["run_id"])
    if not verified or verified["workflow_id"] != payload.get("workflow_id") or verified["source_bindings"] != payload.get("source_bindings"):
        return False
    versions = payload.get("source_versions", [])
    ids = json_loads(task["input_refs_json"], [])
    if not versions or {row.get("id") for row in versions} != set(ids):
        return False
    for expected in versions:
        source = conn.execute("SELECT * FROM sources WHERE id=? AND namespace=?", (expected["id"], task["namespace"])).fetchone()
        version = conn.execute("SELECT version_no,content_hash FROM source_versions WHERE source_id=? ORDER BY version_no DESC LIMIT 1", (expected["id"],)).fetchone()
        if (not source or not version or source["content_hash"] != expected.get("content_hash") or version["content_hash"] != expected.get("content_hash")
                or version["version_no"] != expected.get("version") or hashlib.sha256(str(source["original_content"] or "").encode()).hexdigest() != expected.get("content_hash")):
            return False
    return True
