"""Durable case decisions shared by every result view."""
from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from ..db import utc_now
from ..schemas import AgentOutputPayload
from .decisions import build_case_decision
from .watchlist import invalidation_trigger, monitoring_triggers, watch_key
from .learning import freeze_decision, candidate_key, LifecycleRequest
from .lifecycle import review_overlay
from .fact_references import resolve_fact_references
from .decision_questions import candidate_proposal_hash


class CaseDecisionStore:
    def __init__(self, repository: Any):
        self.repo = repository

    def persist(
        self,
        run_id: str,
        output_id: str,
        *,
        deterministic_market: dict[str, Any] | None = None,
        correction_key: str | None = None,
        correction_reason: str | None = None,
        validated_payload: AgentOutputPayload | None = None,
        correction_input_hash: str | None = None,
    ) -> dict[str, Any] | None:
        has_correction = correction_key is not None or correction_reason is not None
        if validated_payload is not None and not has_correction:
            raise ValueError("validated_payload requires a case decision correction")
        if correction_input_hash is not None and not has_correction:
            raise ValueError("correction_input_hash requires a case decision correction")
        if validated_payload is not None and not isinstance(validated_payload, AgentOutputPayload):
            raise TypeError("validated_payload must be an AgentOutputPayload")
        if has_correction:
            if not isinstance(correction_key, str) or not correction_key.strip():
                raise ValueError("correction_key is required for a case decision correction")
            if not isinstance(correction_reason, str) or not correction_reason.strip():
                raise ValueError("correction_reason is required for a case decision correction")
            correction_key = correction_key.strip()
            correction_reason = correction_reason.strip()
            if correction_key == "original":
                raise ValueError("correction_key must differ from the original projection")
            if len(correction_key) > 200:
                raise ValueError("correction_key is too long")
            if len(correction_reason) > 4000:
                raise ValueError("correction_reason is too long")
            if correction_input_hash is not None:
                if not isinstance(correction_input_hash, str) or not correction_input_hash.strip():
                    raise ValueError("correction_input_hash must be a non-empty string")
                correction_input_hash = correction_input_hash.strip()
                if len(correction_input_hash) > 200:
                    raise ValueError("correction_input_hash is too long")
        projection_key = correction_key if has_correction else "original"
        with self.repo.db.operation() as conn:
            run = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            output = conn.execute("SELECT o.*,t.run_id,t.kind,a.source_versions_json AS attempt_sources FROM outputs o JOIN tasks t ON t.id=o.task_id JOIN task_attempts a ON a.id=o.attempt_id WHERE o.id=? AND t.run_id=?", (output_id, run_id)).fetchone()
            if has_correction:
                saved = conn.execute(
                    "SELECT payload_json FROM case_decision_versions WHERE output_id=? AND run_id=? AND projection_key=? ORDER BY revision DESC LIMIT 1",
                    (output_id, run_id, projection_key),
                ).fetchone()
            else:
                # A correction intentionally shares the immutable CIO output.
                # Ordinary persistence returns the latest projection for that
                # output, including a later code-only correction.
                saved = conn.execute(
                    "SELECT payload_json FROM case_decision_versions WHERE output_id=? AND run_id=? ORDER BY revision DESC LIMIT 1",
                    (output_id, run_id),
                ).fetchone()
            if saved:
                # New five-question CIO projections are persisted only after
                # their post-Astra receipt.  Returning the existing immutable
                # projection here therefore remains correct and avoids an
                # ineffective second read/reprojection race.
                return json.loads(saved[0])
        if not run or not output:
            return None
        snapshot = json.loads(run["input_snapshot_json"] or "{}")
        stored_payload = json.loads(output["payload_json"])
        route = snapshot.get("routing_plan") or stored_payload.get("routing_plan") or {}
        triage = route.get("reddit_triage") or {}
        kind = "investment"
        if output["agent_id"] == "A00":
            if triage.get("classification") == "skip":
                kind = "screening_failed" if str(triage.get("reason", "")).casefold().startswith("screening unavailable:") else "screened_out"
            elif route.get("intent") == "direct_answer":
                kind = "direct_answer"
            else:
                return None
        elif output["agent_id"] != "A11":
            return None
        enriched = self.repo.output_dict(output)
        # A controlled correction may supply the original CIO artifact after
        # it has been revalidated by the typed provider contract.  Keep the
        # database output itself immutable and retain its DB-enriched facts;
        # only the projection input is replaced for this new correction key.
        payload = (
            validated_payload.model_dump(mode="json")
            if validated_payload is not None
            else stored_payload
        )
        payload["fact_claims"] = enriched["fact_claims"]
        # The run marker is code-owned and may be absent from older immutable
        # provider payloads.  Receipt-backed joint review is likewise loaded
        # from the append-only repository table; provider JSON cannot supply
        # canonical review status or scores.
        if snapshot.get("research_contract"):
            payload["research_contract"] = snapshot.get("research_contract")
        versions = json.loads(output["attempt_sources"] or "{}")
        source_ids = list(versions) if isinstance(versions, dict) else [item["id"] for item in versions if isinstance(item,dict) and item.get("id")]
        sources = self.repo.source_packet(run["namespace"], source_ids)
        attempt_inputs = self.repo.attempt_decision_inputs(output["attempt_id"]) or {}
        payload, fact_reference_audit = resolve_fact_references(self.repo,output,payload,enriched["fact_claims"],attempt_inputs,sources)
        expected_proposal_hashes: dict[str, str] = {}
        if snapshot.get("research_contract") == "five-questions.v1":
            # The post-Astra receipt is bound to the same canonical candidate
            # projection used below.  Resolving aliases and frozen prior
            # facts can change the proposal's visible IDs; hashing the raw
            # provider candidate before that projection would reject a valid
            # receipt even though both sides used the same validated proposal.
            raw_candidates = list(payload.get("candidate_briefs") or [])
            nested = payload.get("decision_brief") if isinstance(payload.get("decision_brief"), dict) else {}
            raw_candidates.extend(nested.get("candidate_briefs") or [])
            for raw_candidate in raw_candidates:
                if not isinstance(raw_candidate, dict):
                    continue
                candidate_key = str(raw_candidate.get("ticker") or raw_candidate.get("instrument") or "").strip().upper()
                if candidate_key and candidate_key not in expected_proposal_hashes:
                    expected_proposal_hashes[candidate_key] = candidate_proposal_hash(raw_candidate)
        review_receipts: dict[str, list[dict[str, Any]]] = {}
        if snapshot.get("research_contract") == "five-questions.v1":
            # The post-Astra receipt belongs to the A11 attempt while the
            # pre-disposition receipt belongs to A03.  Read the complete
            # append-only run namespace and reject a post receipt bound to a
            # different immutable candidate proposal.
            frozen_pre_ids = {
                str(item).strip()
                for item in attempt_inputs.get("decision_receipt_ids") or []
                if str(item).strip()
            }
            for receipt in self.repo.decision_model_reviews(run_id, namespace=run["namespace"]):
                receipt = dict(receipt)
                if receipt.get("phase") in {"pre_a11", "pre_a11_disposition", "disposition"}:
                    if not frozen_pre_ids or str(receipt.get("id") or "") not in frozen_pre_ids:
                        continue
                elif receipt.get("phase") in {"post_astra", "post_astra_resolution", "resolution"}:
                    # A11's post result belongs to the exact committed A11
                    # attempt.  Earlier retries must never win by ordering.
                    if str(receipt.get("attempt_id") or "") != str(output["attempt_id"]):
                        continue
                else:
                    continue
                if receipt.get("phase") in {"post_astra", "post_astra_resolution", "resolution"}:
                    candidate_key = str(receipt.get("candidate_key") or "").strip().upper()
                    if not expected_proposal_hashes.get(candidate_key) or receipt.get("proposal_hash") != expected_proposal_hashes[candidate_key]:
                        receipt["status"] = "failed"
                        receipt["failure_reason"] = "The post-Astra receipt is not bound to this immutable candidate proposal."
                review_receipts.setdefault(str(receipt.get("candidate_key") or ""), []).append(receipt)
        portfolio = dict(attempt_inputs.get("portfolio_snapshot", snapshot.get("portfolio_snapshot")) or {})
        portfolio["question"] = run["request"]
        market = (attempt_inputs.get("deterministic_market") or {}) if attempt_inputs else (deterministic_market or snapshot.get("deterministic_market") or {})
        portfolio["deterministic_market"] = market
        if kind == "investment":
            from .valuation_context import compile_valuation_context
            process = attempt_inputs.get("investment_process", snapshot.get("investment_process"))
            context = None if process else compile_valuation_context(self.repo, run_id, sources, as_of=output["created_at"], frozen_versions=versions)
            valuation_contexts = {context["ticker"]: context} if context else {}
            for receipt in (process or {}).get("earnings", [])[:5]:
                ticker = receipt.get("ticker")
                if ticker and ticker not in valuation_contexts:
                    candidate_context = compile_valuation_context(self.repo, run_id, sources, as_of=output["created_at"], frozen_versions=versions, ticker=ticker, frozen_receipt=receipt)
                    if candidate_context:
                        valuation_contexts[ticker] = candidate_context
            result = build_case_decision(run_id, payload, portfolio, sources, as_of=output["created_at"], review_receipts=review_receipts,
                valuation_research_contexts=valuation_contexts or None).model_dump(mode="json")
            if snapshot.get("assessment_pipeline") == "earnings-assessment.v1":
                candidates = result.get("candidates") or []
                if not candidates or any(not candidate.get("future_target") or (candidate.get("valuation") or {}).get("status") != "complete" for candidate in candidates):
                    raise ValueError("The assessment requires a supported calculated price target before publication.")
        else:
            result = {"schema_version": "case-decision.v1", "run_id": run_id, "decision_revision": 1, "as_of": output["created_at"], "outcome": "decline" if kind == "screened_out" else None, "execution_state": "failed" if kind == "screening_failed" else "ready", "candidates": [], "material_blockers": []}
        result.update({"kind": kind, "source_output_id": output_id, "summary": payload.get("summary", ""), "rationale": triage.get("reason") if kind == "screened_out" else payload.get("summary", ""), "projection_key": projection_key})
        result["fact_reference_audit"] = fact_reference_audit
        if has_correction:
            correction = {
                "type": "code_only",
                "key": correction_key,
                "reason": correction_reason,
                "source_output_id": output_id,
            }
            result.update({"correction_key": correction_key, "correction_reason": correction_reason, "correction": correction})
            if correction_input_hash is not None:
                result["correction_input_hash"] = correction_input_hash
                correction["input_hash"] = correction_input_hash
        calculation_context = dict(market)
        calculation_context["portfolio_snapshot"] = attempt_inputs.get("portfolio_snapshot", snapshot.get("portfolio_snapshot")) or {}
        calculation_context["account_snapshot_id"] = attempt_inputs.get("account_snapshot_id", snapshot.get("account_snapshot_id"))
        calculation_context["portfolio_snapshot_captured_at"] = attempt_inputs.get("portfolio_snapshot_captured_at", snapshot.get("portfolio_snapshot_captured_at")) or run["created_at"]
        with self.repo.db.transaction(immediate=True) as conn:
            if has_correction:
                prior = conn.execute(
                    "SELECT payload_json FROM case_decision_versions WHERE output_id=? AND run_id=? AND projection_key=? ORDER BY revision DESC LIMIT 1",
                    (output_id, run_id, projection_key),
                ).fetchone()
            else:
                prior = conn.execute(
                    "SELECT payload_json FROM case_decision_versions WHERE output_id=? AND run_id=? ORDER BY revision DESC LIMIT 1",
                    (output_id, run_id),
                ).fetchone()
            if prior:
                return json.loads(prior[0])
            revision = conn.execute("SELECT COALESCE(MAX(revision),0)+1 FROM case_decision_versions WHERE run_id=?", (run_id,)).fetchone()[0]
            result["decision_revision"] = revision
            for item in result["candidates"]:
                item["decision_revision"] = revision
            conn.execute("INSERT INTO case_decision_versions(id,run_id,namespace,output_id,projection_key,revision,kind,payload_json,calculation_context_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)", ("case_" + uuid4().hex, run_id, run["namespace"], output_id, projection_key, revision, kind, json.dumps(result), json.dumps(calculation_context), utc_now()))
            freeze_decision(conn, result, run["namespace"],sources=sources)
            if has_correction:
                self.repo.db.emit(
                    conn,
                    namespace=run["namespace"],
                    event_type="case_decision_corrected",
                    run_id=run_id,
                    payload={
                        "output_id": output_id,
                        "revision": revision,
                        "projection_key": projection_key,
                        "correction_key": correction_key,
                        "correction_reason": correction_reason,
                        "correction_input_hash": correction_input_hash,
                        "message": "Code-only correction appended; the original CIO output and prior case decision remain immutable.",
                    },
                )
            else:
                self.repo.db.emit(conn, namespace=run["namespace"], event_type="case_decision_saved", run_id=run_id, payload={"output_id": output_id, "revision": revision, "projection_key": projection_key, "message": "Current case decision saved."})
        return result

    def current(self, run_id: str, namespace: str | None = None) -> dict[str, Any] | None:
        with self.repo.db.operation() as conn:
            row = conn.execute("SELECT d.payload_json,EXISTS(SELECT 1 FROM invalidations i WHERE i.output_id=d.output_id) AS stale FROM case_decision_versions d WHERE d.run_id=? AND (? IS NULL OR d.namespace=?) ORDER BY d.revision DESC LIMIT 1", (run_id, namespace, namespace)).fetchone()
        return json.loads(row[0]) | {"stale": bool(row["stale"])} if row else None

    def history(self, run_id: str, namespace: str | None = None) -> list[dict[str, Any]]:
        with self.repo.db.operation() as conn:
            rows = conn.execute("SELECT payload_json FROM case_decision_versions WHERE run_id=? AND (? IS NULL OR namespace=?) ORDER BY revision DESC", (run_id, namespace, namespace)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def calculations(self, run_id: str, namespace: str | None = None) -> dict[str, Any]:
        with self.repo.db.operation() as conn:
            row = conn.execute("SELECT calculation_context_json FROM case_decision_versions WHERE run_id=? AND (? IS NULL OR namespace=?) ORDER BY revision DESC LIMIT 1", (run_id,namespace,namespace)).fetchone()
            run = conn.execute("SELECT input_snapshot_json FROM runs WHERE id=? AND (? IS NULL OR namespace=?)", (run_id,namespace,namespace)).fetchone()
        if row:
            return json.loads(row[0])
        return json.loads(run[0]).get("deterministic_market", {}) if run else {}

    def title(self, run_id: str) -> str:
        with self.repo.db.operation() as conn:
            row = conn.execute("SELECT COALESCE(i.title,r.request) FROM runs r LEFT JOIN intake_items i ON i.namespace=r.namespace AND i.external_id=r.origin_ref AND i.origin='reddit' WHERE r.id=?", (run_id,)).fetchone()
        return row[0] if row else "Research case"

    def list(self, namespace: str = "real") -> list[dict[str, Any]]:
        with self.repo.db.operation() as conn:
            rows = conn.execute("SELECT d.payload_json,EXISTS(SELECT 1 FROM invalidations i WHERE i.output_id=d.output_id) AS stale FROM case_decision_versions d WHERE d.namespace=? AND d.revision=(SELECT MAX(v.revision) FROM case_decision_versions v WHERE v.run_id=d.run_id) ORDER BY d.created_at DESC", (namespace,)).fetchall()
        return [json.loads(row[0]) | {"stale": bool(row["stale"])} for row in rows]

    def watchlist(self, namespace: str = "real") -> dict[str, Any]:
        paused = self.repo.firm_paused()
        items = []
        for case in self.list(namespace):
            for candidate in case.get("candidates", []):
                key = candidate_key(candidate)
                with self.repo.db.operation() as conn:
                    event = conn.execute("SELECT * FROM idea_lifecycle_events WHERE namespace=? AND run_id=? AND candidate_key=? ORDER BY created_at DESC,rowid DESC LIMIT 1", (namespace,case["run_id"],key)).fetchone()
                    baseline = conn.execute("SELECT id FROM idea_baselines WHERE namespace=? AND run_id=? AND decision_revision=? AND candidate_key=?", (namespace,case["run_id"],case["decision_revision"],key)).fetchone()
                state = event["state"] if event else None
                if candidate.get("outcome") not in {"watchlist", "recommend"} and not candidate.get("watch_triggers") and not (candidate.get("action_plan") or {}).get("review_at") and not state:
                    continue
                checks = []
                triggers = monitoring_triggers(candidate)
                if case.get("stale"):
                    with self.repo.db.operation() as conn:
                        amendments = conn.execute("SELECT DISTINCT source_id FROM invalidations WHERE namespace=? AND run_id=? AND output_id=?",(namespace,case["run_id"],case.get("source_output_id"))).fetchall()
                    if amendments:
                        triggers.append(invalidation_trigger(case.get("decision_revision"),[row[0] for row in amendments]))
                for trigger in triggers:
                    trigger_key = watch_key(case["run_id"], candidate["ticker"], trigger,candidate)
                    with self.repo.db.operation() as conn:
                        row = conn.execute("SELECT * FROM watch_checks WHERE trigger_key=?", (trigger_key,)).fetchone()
                        if not row:
                            row = conn.execute("SELECT * FROM watch_checks WHERE trigger_key=?", (watch_key(case["run_id"],candidate["ticker"],trigger),)).fetchone()
                        tasks = conn.execute("SELECT status FROM tasks WHERE run_id=? AND kind LIKE ?", (case["run_id"],"%_watch_"+trigger_key[:16])).fetchall() if row and row["followup_task_id"] else []
                        if row and row["followup_task_id"] and not tasks:
                            first = conn.execute("SELECT kind FROM tasks WHERE id=?",(row["followup_task_id"],)).fetchone()
                            if first and "_watch_" in first[0]:
                                tasks = conn.execute("SELECT status FROM tasks WHERE run_id=? AND kind LIKE ?",(case["run_id"],"%_watch_"+first[0].split("_watch_",1)[1])).fetchall()
                        episode = conn.execute("SELECT * FROM watch_review_episodes WHERE trigger_key=? ORDER BY episode DESC LIMIT 1",(trigger_key,)).fetchone()
                        if episode:
                            tasks = conn.execute("SELECT status FROM tasks WHERE id=?",(episode["final_task_id"],)).fetchall()
                    check = json.loads(row["result_json"]) if row else {"status": "pending", "reason": "This condition has not been checked yet."}
                    if row:
                        check.setdefault("checked_at", row["checked_at"])
                    if tasks:
                        check["review_state"] = "reviewed" if all(t["status"] == "completed" for t in tasks) else "failed" if any(t["status"] in {"failed","blocked","cancelled"} for t in tasks) else "reviewing"
                    if episode:
                        check["episode"] = episode["episode"]
                    if paused:
                        check = check | {"status": "paused", "reason": "Research and watch checks are paused."}
                    elif case.get("stale"):
                        check = check | {"status": "awaiting_review", "reason": "Supporting evidence changed; this decision needs a refreshed review."}
                    checks.append({"trigger_key": trigger_key, "trigger": trigger, **check})
                overlay = review_overlay(candidate,checks,paused=paused,state=state)
                items.append({"run_id": case["run_id"], "title": self.title(case["run_id"]), "decision_revision": case["decision_revision"], "source_output_id":case.get("source_output_id"), "stale": bool(case.get("stale")), "candidate": candidate, "checks": checks, "learning": {"baseline_id": baseline["id"] if baseline else None}, **overlay})
        return {"items": items, "paused": paused}

    def record_lifecycle(self, run_id: str, request: LifecycleRequest) -> dict[str, Any]:
        case = self.current(run_id,request.namespace)
        if not case or not any(candidate_key(candidate) == request.candidate_key for candidate in case.get("candidates",[])):
            raise ValueError("The candidate was not found in this namespace.")
        if not request.confirmed:
            raise ValueError("An actual held or closed state requires explicit user confirmation; a recommendation is not an execution.")
        with self.repo.db.transaction(immediate=True) as conn:
            prior = conn.execute("SELECT * FROM idea_lifecycle_events WHERE namespace=? AND idempotency_key=?", (request.namespace,request.idempotency_key)).fetchone()
            if prior:
                if (prior["run_id"],prior["candidate_key"],prior["state"],prior["reason"]) != (run_id,request.candidate_key,request.state,request.reason):
                    raise ValueError("This idempotency key belongs to a different lifecycle event.")
                return dict(prior)
            record = {"id":"life_"+uuid4().hex,"namespace":request.namespace,"run_id":run_id,"candidate_key":request.candidate_key,"state":request.state,"decision_revision":case["decision_revision"],"reason":request.reason,"idempotency_key":request.idempotency_key,"created_at":utc_now()}
            conn.execute("INSERT INTO idea_lifecycle_events(id,namespace,run_id,candidate_key,state,decision_revision,reason,idempotency_key,created_at) VALUES(:id,:namespace,:run_id,:candidate_key,:state,:decision_revision,:reason,:idempotency_key,:created_at)",record)
            return record
