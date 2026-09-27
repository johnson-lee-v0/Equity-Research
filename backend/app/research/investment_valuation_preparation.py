"""Calculate the analyst's proposal before the final reviewer explains it.

Receipts are scoped to immutable evidence and the analyst output. A correction
is a separate, single-use provider subattempt; it cannot restart the prose run
or turn unavailable accounting inputs into assumptions.
"""
from __future__ import annotations

import asyncio
import inspect
from copy import deepcopy
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..db import digest, json_dumps, json_loads, new_id, utc_now
from ..schemas import AgentOutputPayload, ValuationAssumptions
from .investment_process import ProcessPaused, dispatch_guard
from .synthesis_timeout_fallback import _source_bindings, _completed_output
from .valuation import VALUATION_CODE_VERSION as CODE_VERSION, build_valuation

VERSION = "investment-valuation-preparation.v1"
EVENT = "investment_valuation_preparation"
REPAIR_EVENT = "investment_valuation_input_correction"
REPAIR_TIMEOUT_SECONDS = 120
MAX_PACKET_CHARS = 55_000


class PreparationError(ValueError):
    """Preparation needs attention; do not regenerate the entire review."""


class InputCorrection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assumptions: ValuationAssumptions
    explanation: str = Field(min_length=1, max_length=1500)


def rebind_assumptions(value: dict | None, facts: list[dict]) -> dict:
    """Resolve only aliases belonging to the selected immutable A03 output."""
    aliases = {row["claim_id"]: row["fact_id"] for row in facts
               if row.get("claim_id") and row.get("fact_id")}
    def visit(item):
        if isinstance(item, dict):
            return {key: ([aliases.get(identifier, identifier) for identifier in child]
                          if key == "fact_claim_ids" and isinstance(child, list) else visit(child))
                    for key, child in item.items()}
        if isinstance(item, list):
            return [visit(child) for child in item]
        return item
    return ValuationAssumptions.model_validate(visit(value or {})).model_dump(mode="json")


def calculate(assumptions: dict, candidate: dict, *, facts: list[dict], sources: list[dict],
              as_of: str, horizon: str, research_context: dict | None = None) -> tuple[dict, list[str]]:
    result = build_valuation(assumptions, asset_class=candidate.get("asset_class") or "us_equity",
        horizon=horizon, as_of=as_of, validated_facts=facts,
        issuer=candidate.get("issuer") or candidate.get("issuer_name") or candidate.get("ticker"),
        source_records={row["id"]: row for row in sources}, research_context=research_context)
    errors = [str(reason) for method in result.get("methods", []) for reason in method.get("reasons", [])]
    try:
        prices = all(Decimal(str(result["scenarios"][name])).is_finite()
                     and Decimal(str(result["scenarios"][name])) > 0 for name in ("bear", "base", "bull"))
    except (KeyError, ValueError, InvalidOperation):
        prices = False
    selected = next((row for row in result.get("methods", []) if row.get("name") == result.get("selected_method") and row.get("supported")), {})
    bridges = selected.get("scenario_calculations") or {}
    complete = all(bridges.get(name, {}).get("inputs") and bridges[name].get("formula") for name in ("bear", "base", "bull"))
    if result.get("status") != "complete" or not prices or not complete:
        errors = errors or list(result.get("missing_inputs") or []) or ["Supply source-bound inputs and complete bear/base/bull assumptions."]
    else:
        errors = []
    import re
    def months(value):
        match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*[- ]?\s*(months?|years?)\s*", str(value or ""), re.I)
        return Decimal(match[1]) * (12 if match[2].lower().startswith("year") else 1) if match else str(value or "").strip().lower()
    if months(result.get("horizon")) != months(horizon):
        errors.append("The valuation method holding horizon must equal the requested " + horizon + "; forecast fiscal span is separate.")
    return result, errors


def _compact_result(result: dict) -> dict:
    retained = {key: deepcopy(result.get(key)) for key in
                ("status", "as_of", "horizon", "currency", "selected_method", "scenarios", "missing_inputs", "code_version", "input_hash")}
    retained["methods"] = [{key: deepcopy(row.get(key)) for key in
        ("name", "supported", "status", "formula", "scenario_calculations", "reasons")}
        for row in result.get("methods", [])]
    retained["implied_today"] = deepcopy((result.get("research_context") or {}).get("implied_today"))
    return retained


def _scope_valid(conn, scope: dict, parent_attempt_id: str | None = None) -> bool:
    run = conn.execute("SELECT * FROM runs WHERE id=? AND namespace=?", (scope["run_id"], scope["namespace"])).fetchone()
    task = conn.execute("SELECT * FROM tasks WHERE id=? AND run_id=?", (scope["task_id"], scope["run_id"])).fetchone()
    parent = conn.execute("SELECT * FROM task_attempts WHERE id=? AND task_id=?", (parent_attempt_id, scope["task_id"])).fetchone() if parent_attempt_id else None
    if not (run and task and run["ticker"] == scope["ticker"] and run["as_of"] == scope["as_of"] and run["horizon"] == scope["horizon"]
            and task["agent_id"] == "A11" and parent and task["current_attempt_id"] == parent_attempt_id
            and json_loads(parent["source_versions_json"], {}) == scope["source_versions"]
            and json_loads(parent["resolved_config_json"], {}) == scope["model"]):
        return False
    for oid, expected in scope["analyst_outputs"].items():
        source_task = conn.execute("SELECT t.* FROM tasks t JOIN outputs o ON o.task_id=t.id WHERE o.id=? AND t.run_id=? AND t.agent_id='A03'", (oid, scope["run_id"])).fetchone()
        bound = _completed_output(conn, source_task, scope["namespace"])
        if not bound or bound[0]["id"] != oid or bound[0]["output_hash"] != expected:
            return False
    return bool(_source_bindings(conn, scope["namespace"], scope["source_versions"]))



def _guard(repo, scope: dict, parent_attempt_id: str):
    dispatch_guard(repo, scope["run_id"], scope["task_id"])
    with repo.db.operation() as conn:
        if not _scope_valid(conn, scope, parent_attempt_id):
            raise PreparationError("Valuation preparation evidence or analyst output changed; review the new source revision before continuing.")


def _records(conn, scope: dict, event: str) -> list[dict]:
    key = digest(scope)
    return [value for row in conn.execute("SELECT payload_json FROM events WHERE run_id=? AND task_id=? AND type=? ORDER BY sequence_id",
            (scope["run_id"], scope["task_id"], event))
            if (value := json_loads(row[0], {})).get("preparation_key") == key]


def _emit(repo, scope: dict, event: str, record: dict, *, parent_attempt_id: str, start: bool = False):
    with repo.db.transaction(immediate=True) as conn:
        if start:
            if _records(conn, scope, event):
                raise PreparationError("The one input-only valuation correction was already attempted for these unchanged inputs. Review its saved diagnostic.")
        # Terminal failure receipts remain writable after cancellation so the
        # budget and audit trail survive. Successful results require the gate.
        if record.get("status") in {"started", "completed", "ready"}:
            run = conn.execute("SELECT * FROM runs WHERE id=?", (scope["run_id"],)).fetchone()
            task = conn.execute("SELECT * FROM tasks WHERE id=?", (scope["task_id"],)).fetchone()
            if (not _scope_valid(conn, scope, parent_attempt_id) or run["cancel_requested"] or run["pause_requested"]
                    or task["pause_requested"] or task["status"] != "running" or repo._firm_dispatch_paused_conn(conn, scope["run_id"])):
                raise ProcessPaused("Valuation preparation stopped before its result could be accepted.")
        payload = {"version": VERSION, "preparation_key": digest(scope), "scope": scope,
                   "parent_attempt_id": parent_attempt_id, "at": utc_now(), **record}
        repo.db.emit(conn, namespace=scope["namespace"], run_id=scope["run_id"], task_id=scope["task_id"],
                     attempt_id=parent_attempt_id, event_type=event, payload=payload)


async def prepare(engine, run, task, attempt_id: str, config, context: dict, sources: list[dict], source_versions: dict) -> dict:
    """Return authoritative calculations, or an honest financial-unavailability receipt."""
    repo = engine.repository
    readiness = context.get("valuation_readiness") or {}
    if readiness.get("status") != "ready":
        return {"version": VERSION, "status": "unavailable", "missing_inputs": readiness.get("missing_inputs") or ["No usable financial baseline."],
                "assumptions": None, "calculation": None, "model_calls": 0}
    prior = [row for row in context.get("prior_outputs", []) if row.get("agent_id") == "A03"]
    choices = [(row, candidate) for row in reversed(prior) for candidate in row.get("candidate_briefs", [])
               if candidate.get("ticker") == run["ticker"]]
    if not choices:
        raise PreparationError("The initial analyst did not retain a candidate valuation proposal; review that output before final review.")
    selected, candidate = next(((row, cand) for row, cand in choices if cand.get("valuation_assumptions")), choices[0])
    with repo.db.operation() as conn:
        output = conn.execute("SELECT output_hash FROM outputs WHERE id=?", (selected["id"],)).fetchone()
        hashes = {row["id"]: row["output_hash"] for row in conn.execute("SELECT o.id,o.output_hash FROM outputs o JOIN tasks t ON t.id=o.task_id WHERE t.run_id=? AND o.agent_id='A03'", (run["id"],)) if row["id"] in {item["id"] for item in prior}}
    if not output or len(hashes) != len(prior):
        raise PreparationError("The saved analyst output is unavailable.")
    scope = {"version": VERSION, "calculator_version": CODE_VERSION, "run_id": run["id"], "task_id": task["id"],
             "namespace": run["namespace"], "ticker": run["ticker"], "as_of": run["as_of"], "horizon": run["horizon"],
             "analyst_output_id": selected["id"], "analyst_output_hash": output["output_hash"],
             "analyst_outputs": hashes, "facts_hash": digest([row.get("fact_claims", []) for row in prior]),
             "research_context_hash": digest(context.get("valuation_research_context")),
             "source_versions": source_versions, "model": config.model_dump(mode="json")}
    _guard(repo, scope, attempt_id)
    facts = [fact for row in prior for fact in row.get("fact_claims", [])]
    research_context = context.get("valuation_research_context")
    schema_errors = []
    try:
        assumptions = rebind_assumptions(candidate.get("valuation_assumptions"), selected.get("fact_claims", []))
    except ValidationError as exc:
        assumptions = candidate.get("valuation_assumptions") or {}
        schema_errors = [str(exc)[:1800]]
    with repo.db.operation() as conn:
        saved = next((row for row in reversed(_records(conn, scope, EVENT)) if row.get("status") == "ready"), None)
    if saved:
        assumptions = saved["assumptions"]
    if schema_errors and not saved:
        result, errors = {}, schema_errors
    else:
        result, errors = calculate(assumptions, candidate, facts=facts, sources=sources, as_of=run["as_of"],
                                   horizon=run["horizon"], research_context=research_context)
    repaired = False
    if errors:
        assumptions = await _correct(engine, scope, attempt_id, config, candidate, assumptions, context, errors)
        repaired = True
        _guard(repo, scope, attempt_id)
        result, errors = calculate(assumptions, candidate, facts=facts, sources=sources, as_of=run["as_of"],
                                   horizon=run["horizon"], research_context=research_context)
        if errors:
            _emit(repo, scope, EVENT, {"status": "invalid", "errors": errors[:8]}, parent_attempt_id=attempt_id)
            raise PreparationError("Input-only valuation correction did not pass: " + "; ".join(errors)[:1800])
    receipt = {"version": VERSION, "status": "ready", "preparation_key": digest(scope),
               "analyst_output_id": selected["id"], "scope": scope, "assumptions": assumptions, "assumptions_hash": digest(assumptions),
               "calculation": _compact_result(result), "model_calls": int(repaired), "reused": bool(saved),
               "interpretation": "Conditional calculations using initial analyst assumptions, not endorsed facts or an approved recommendation."}
    _guard(repo, scope, attempt_id)
    if not saved:
        _emit(repo, scope, EVENT, receipt, parent_attempt_id=attempt_id)
    return receipt


async def _correct(engine, scope, parent_attempt_id, config, candidate, assumptions, context, errors):
    repo = engine.repository
    with repo.db.operation() as conn:
        previous = _records(conn, scope, REPAIR_EVENT)
    if previous:
        completed = next((row for row in reversed(previous) if row.get("status") == "completed"), None)
        if completed:
            _guard(repo, scope, parent_attempt_id)
            return InputCorrection.model_validate({"assumptions": completed["assumptions"], "explanation": completed["explanation"]}).assumptions.model_dump(mode="json")
        raise PreparationError("The one input-only valuation correction was already attempted or interrupted for these unchanged inputs. Review its saved diagnostic.")
    adapter = engine.providers.adapter(config.provider)
    # Only public financial records and proposed assumptions belong here. No
    # portfolio, full prior report, raw filings or cross-case memory is sent.
    fields = ("fact_id", "claim_id", "metric", "value", "unit", "currency", "period", "period_start", "period_end", "basis", "scale", "statement_type", "source_ref", "locator", "validation_status", "semantic_status")
    packet = {"stage": "valuation_input_correction", "ticker": scope["ticker"], "as_of": scope["as_of"], "horizon": scope["horizon"],
        "proposed_assumptions": assumptions, "calculator_errors": errors[:8],
        "financial_facts": [{key: fact.get(key) for key in fields} for fact in context.get("financial_seeds", [])],
        "valuation_readiness": context.get("valuation_readiness"),
        "method_fit": {key: candidate.get(key) for key in ("asset_class", "issuer", "issuer_name")}}
    if len(json_dumps(packet)) > MAX_PACKET_CHARS:
        raise PreparationError("Valuation input correction exceeds its bounded packet; review the oversized proposal.")
    prompt = ("Repair only the rejected structured valuation inputs. Do not write a research report, browse, run commands or acquire sources. "
        "Preserve valid analyst growth and multiple assumptions and their rationales; change only what the calculator diagnostic requires. "
        "Use exact supplied durable fact IDs, units, currencies and accounting periods. Never invent missing historical facts or default missing debt/shares to zero. "
        "The holding horizon is fixed; forecast years describe the historical-to-forecast fiscal span. Today value uses current reported earnings, without discounting. "
        "Return assumptions and a short explanation only.\n<untrusted_evidence_packet>\n" + json_dumps(packet) + "\n</untrusted_evidence_packet>")
    correction_id = new_id("valuation_correction_")
    result = None
    started = False
    call = None
    monitor = None
    slot = engine.providers.generation_slot
    accepts_origin = "origin" in inspect.signature(slot).parameters or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in inspect.signature(slot).parameters.values())
    try:
        available = await engine.providers.preflight(config, execute=False)
        if not available.get("available"):
            raise PreparationError("Valuation input correction provider is unavailable: " + str(available.get("reason") or available.get("status")))
        async with (slot(config.provider, origin=repo.canonical_run_origin(scope["run_id"])) if accepts_origin else slot(config.provider)):
            _guard(repo, scope, parent_attempt_id)
            _emit(repo, scope, REPAIR_EVENT, {"status": "started", "correction_attempt_id": correction_id,
                "prompt_hash": digest(prompt), "schema_hash": digest(InputCorrection.model_json_schema()),
                "timeout_seconds": REPAIR_TIMEOUT_SECONDS}, parent_attempt_id=parent_attempt_id, start=True)
            started = True
            engine.active[correction_id] = (scope["run_id"], adapter)
            repo.update_task_progress(scope["task_id"], "Correcting one rejected valuation input before final review.")
            call = asyncio.create_task(adapter.execute(correction_id, prompt, config, InputCorrection.model_json_schema(),
                engine.config.data_dir / "workers" / correction_id))
            async def monitor_scope():
                while True:
                    await asyncio.sleep(0.25)
                    dispatch_guard(repo, scope["run_id"], scope["task_id"])
            monitor = asyncio.create_task(monitor_scope())
            done, _ = await asyncio.wait({call, monitor}, timeout=REPAIR_TIMEOUT_SECONDS, return_when=asyncio.FIRST_COMPLETED)
            if not done:
                raise asyncio.TimeoutError()
            if monitor in done:
                await monitor
            result = await call
        _guard(repo, scope, parent_attempt_id)
        parsed = InputCorrection.model_validate(result.payload)
        corrected = parsed.assumptions.model_dump(mode="json")
        _emit(repo, scope, REPAIR_EVENT, {"status": "completed", "correction_attempt_id": correction_id,
            "assumptions": corrected, "explanation": parsed.explanation, "usage": result.usage}, parent_attempt_id=parent_attempt_id)
        return corrected
    except BaseException as exc:
        if started:
            if call is not None and not call.done():
                call.cancel()
            await adapter.cancel(correction_id)
            if call is not None:
                await asyncio.gather(call, return_exceptions=True)
            status = "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed"
            message = "Valuation input correction exceeded its 120-second limit." if isinstance(exc, asyncio.TimeoutError) else str(exc)[:1800]
            _emit(repo, scope, REPAIR_EVENT, {"status": status, "correction_attempt_id": correction_id,
                "error": message, "usage": result.usage if result else None}, parent_attempt_id=parent_attempt_id)
        if isinstance(exc, asyncio.CancelledError):
            raise
        raise PreparationError("Valuation input correction needs attention: " + (message if started else str(exc))[:1800]) from exc
    finally:
        if monitor is not None:
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
        engine.active.pop(correction_id, None)


REVIEW_INSTRUCTION = (
    "Valuation preparation is complete before this review. valuation_preparation contains authoritative conditional calculations "
    "from the initial analyst's assumptions, not endorsed facts. Explain the bear/base/bull targets, requested holding horizon, "
    "historical-to-forecast fiscal growth span, and separately the current-earnings implied value (or its stated gap). "
    "Do not say pricing is unavailable when these calculations are ready. Set valuation_assumptions to null; the backend attaches "
    "the exact prepared proposal after your review. Supply valuation_review with status accept, disagree, or reject and a reason. "
    "Disagree retains the conditional numbers while explaining your reservations. Reject is a suitability judgment: explain why "
    "the method is inappropriate and claim no target or current-value numbers anywhere in your output. Do not substitute numeric "
    "assumptions or request missing-source repair merely because you disagree with valuation assumptions."
)


def constrain_review_schema(schema: dict) -> dict:
    schema = deepcopy(schema)
    candidate = schema["$defs"]["CandidateDecisionBrief"]
    candidate["properties"]["valuation_review"] = {"$ref": "#/$defs/ValuationReview"}
    candidate["required"] = list(dict.fromkeys([*candidate.get("required", []), "valuation_review"]))
    candidate["properties"]["valuation_assumptions"] = {"type": "null"}
    # Keep a single authoritative candidate; aggregate aliases must not provide
    # a second proposal that canonical projection could silently prefer.
    schema["properties"]["candidate_briefs"].update(minItems=1, maxItems=1)
    schema["properties"]["valuation_assumptions"] = {"type": "null"}
    schema["properties"]["decision_brief"] = {"type": "null"}
    return schema


def apply_review(payload: AgentOutputPayload, receipt: dict, *, ticker: str, horizon: str) -> tuple[AgentOutputPayload, bool]:
    """Keep reviewer judgment, but never accept a replacement calculator."""
    if receipt.get("status") != "ready":
        return payload, False
    if len(payload.candidate_briefs) != 1 or payload.candidate_briefs[0].ticker != ticker:
        raise PreparationError("Prepared valuation review requires exactly the verified company candidate.")
    candidate = payload.candidate_briefs[0]
    review = candidate.valuation_review
    if review is None or not review.reason.strip():
        raise PreparationError("Prepared valuation review requires an explicit accept, disagree or reject judgment and reason.")
    if payload.valuation_assumptions is not None or payload.decision_brief is not None:
        raise PreparationError("Prepared valuation cannot be replaced through aggregate proposal aliases.")
    if candidate.horizon not in {None, horizon} or payload.horizon not in {None, horizon}:
        raise PreparationError("The final reviewer changed the requested holding horizon.")
    if candidate.valuation_assumptions is not None and digest(candidate.valuation_assumptions.model_dump(mode="json")) != receipt["assumptions_hash"]:
        raise PreparationError("The final reviewer changed verified valuation assumptions. Explain disagreement without replacing the calculated inputs.")
    if review.status == "reject":
        # The exemption is for an unpriced quality judgment only. Neither the
        # candidate nor aggregate aliases may carry a replacement target.
        if candidate.valuation_assumptions is not None or candidate.target_price is not None or payload.target_price is not None or any(row.value is not None for row in payload.calculations):
            raise PreparationError("A rejected valuation method must not claim a target or current-value calculation.")
        import re
        text = json_dumps(payload.model_dump(mode="json"))
        if re.search(r'(?:target(?: price)?|fair value|implied (?:price|value)|current[- ]value|worth)[^.!?\n]{0,50}(?:\$\s*\d|USD\s*\d|\d[\d,.]*\s*(?:USD|dollars))', text, re.I):
            raise PreparationError("A rejected valuation method must not claim numeric prices in its narrative.")
        reason = "Final reviewer rejected the proposed valuation method: " + review.reason.strip()
        candidate = candidate.model_copy(update={"valuation_assumptions": None, "target_price_missing_reason": reason,
            "scenario_assessment": "do_not_use", "scenario_reason": reason, "horizon": horizon})
        return payload.model_copy(update={"candidate_briefs": [candidate]}), True
    base = receipt["calculation"]["scenarios"]["base"]
    for supplied in (candidate.target_price, payload.target_price):
        if supplied is not None:
            try:
                same = Decimal(supplied) == Decimal(base)
            except InvalidOperation:
                same = False
            if not same:
                raise PreparationError("The final reviewer supplied a target that differs from the prepared calculator.")
    candidate = candidate.model_copy(update={"valuation_assumptions": ValuationAssumptions.model_validate(receipt["assumptions"]),
        "horizon": horizon, "target_price": base, "target_price_currency": receipt["calculation"].get("currency"),
        "target_price_as_of": receipt["calculation"].get("as_of"), "target_price_missing_reason": None})
    return payload.model_copy(update={"candidate_briefs": [candidate]}), False


def valid_attempt_receipt(conn, attempt_id: str | None, *, payload: AgentOutputPayload | None = None) -> bool:
    """Repeat preparation provenance checks at commit/publication/recovery."""
    if not attempt_id:
        return True
    row = conn.execute("SELECT context_json FROM attempt_decision_inputs WHERE attempt_id=?", (attempt_id,)).fetchone()
    receipt = (json_loads(row[0], {}) if row else {}).get("valuation_preparation")
    if not receipt or receipt.get("status") != "ready":
        return True
    scope = receipt.get("scope")
    if not isinstance(scope, dict) or not _scope_valid(conn, scope, attempt_id):
        return False
    if digest(scope) != receipt.get("preparation_key") or digest(receipt.get("assumptions")) != receipt.get("assumptions_hash"):
        return False
    saved = next((event for event in reversed(_records(conn, scope, EVENT)) if event.get("status") == "ready"), None)
    if not (saved and saved.get("assumptions_hash") == receipt["assumptions_hash"]
            and saved.get("calculation") == receipt.get("calculation")):
        return False
    if payload is None:
        output = conn.execute("SELECT payload_json FROM outputs WHERE attempt_id=?", (attempt_id,)).fetchone()
        if output:
            try:
                payload = AgentOutputPayload.model_validate(json_loads(output[0], {}))
                # Repository normalization mirrors the already verified single
                # candidate into decision_brief. Validate that exact mirror;
                # raw providers are still forbidden to supply a second proposal.
                brief = payload.decision_brief
                if brief is not None:
                    base = receipt["calculation"]["scenarios"]["base"]
                    if (brief.valuation_assumptions is not None or brief.target_price not in {None, base}
                            or brief.horizon not in {None, scope["horizon"]}
                            or brief.candidate_briefs != payload.candidate_briefs):
                        return False
                    payload = payload.model_copy(update={"decision_brief": None})
            except ValidationError:
                return False
    if payload is not None:
        try:
            _, rejected = apply_review(payload, receipt, ticker=scope["ticker"], horizon=scope["horizon"])
            if not rejected and (not payload.candidate_briefs[0].valuation_assumptions or digest(payload.candidate_briefs[0].valuation_assumptions.model_dump(mode="json")) != receipt["assumptions_hash"]):
                return False
        except (PreparationError, ValidationError, ValueError):
            return False
    return True
