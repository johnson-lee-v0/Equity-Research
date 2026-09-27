"""Durable, versioned agent recipes over the shared Research Engine services.

The scheduler owns dependencies and lifecycle; agents own one bounded capability.
Only fetched, archived text reaches document analysts. Checkpoints contain source
IDs and hashes, never a model's reconstruction of a missing document.
"""
from __future__ import annotations

import asyncio
from contextlib import closing
from dataclasses import dataclass
import hashlib
import re
import sqlite3
from typing import Any

from ..db import json_dumps, json_loads, new_id, utc_now
from ..schemas import RunCreate
from .decision_questions import FIVE_QUESTION_CONTRACT
from .document_intelligence import AnalysisStore, VERSION, FILING_COMPARISON_VERSION, analyze_transcript, compare_filings, enrich_transcript
from .lab_data import LabData, load_json
from .library_store import register_ticker


@dataclass(frozen=True)
class AgentSpec:
    id: str
    name: str
    description: str
    dependencies: tuple[str, ...] = ()
    allow_failed_dependencies: bool = False


@dataclass(frozen=True)
class WorkflowSpec:
    id: str
    name: str
    description: str
    version: str
    agents: tuple[AgentSpec, ...]
    result_agent: str = "publish"
    critical_agents: tuple[str, ...] = ("resolve", "locate", "publish")


EARNINGS = WorkflowSpec("earnings", "Latest earnings", "Collect the latest earnings materials, examine historical trends, and connect the evidence to your research.", "earnings.v2", (
    AgentSpec("resolve", "Company resolver", "Verify ticker and SEC company identity."),
    AgentSpec("locate", "Earnings locator", "Locate and verify the most recent earnings period and call.", ("resolve",)),
    AgentSpec("acquire", "Earnings material collector", "Archive the call, release, presentations, supplements and related SEC reports.", ("resolve", "locate")),
    AgentSpec("transcript", "Transcript analyst", "Extract speaker tone, entities, themes and exact evidence.", ("acquire",)),
    AgentSpec("filings", "Filing analyst", "Compare narrative changes when comparable SEC reports are available.", ("acquire",)),
    AgentSpec("trends", "Historical trend analyst", "Verify quarterly metrics, five-year capital spending and prior guidance against actual results.", ("resolve", "locate", "acquire")),
    AgentSpec("context", "Research librarian", "Attach existing research, portfolio and dated Congress context."),
    AgentSpec("publish", "Brief assembler", "Save the findings, source lineage and unresolved gaps.", ("resolve", "locate", "acquire", "transcript", "filings", "trends", "context"), True),
))
WORKFLOWS = {EARNINGS.id: EARNINGS}
ACTIVE = {"queued", "running"}
DONE = {"completed", "partial", "failed", "skipped"}


def normalize_ticker(value: str) -> str:
    ticker = value.strip().upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9.-]{0,14}", ticker):
        raise ValueError("Enter a ticker such as COST, MSFT or BRK.B.")
    return ticker


def public_document(value: dict) -> dict:
    return {key: item for key, item in value.items() if key not in {"content", "original_bytes", "raw", "html"}}


class WorkflowStore:
    def __init__(self, repo):
        self.repo = repo
        self.db = repo.db

    @staticmethod
    def event(conn, identifier, status, agent_id=None, detail=None):
        conn.execute("INSERT INTO research_workflow_events(run_id,agent_id,status,detail,created_at) VALUES(?,?,?,?,?)", (identifier, agent_id, status, detail, utc_now()))

    def create(self, ticker, *, workflow="earnings", namespace="real", idempotency_key=None):
        ticker = normalize_ticker(ticker)
        if workflow not in WORKFLOWS or namespace not in {"real", "demo"}:
            raise ValueError("Unknown workflow or namespace.")
        with self.db.transaction(immediate=True) as conn:
            if idempotency_key:
                prior = conn.execute("SELECT * FROM research_workflow_runs WHERE namespace=? AND idempotency_key=?", (namespace, idempotency_key)).fetchone()
                if prior:
                    if prior["ticker"] != ticker or prior["workflow"] != workflow:
                        raise ValueError("This request key was already used for a different ticker or workflow.")
                    return prior["id"]
            active = conn.execute("SELECT id FROM research_workflow_runs WHERE namespace=? AND workflow=? AND ticker=? AND status IN ('queued','running')", (namespace, workflow, ticker)).fetchone()
            if active:
                return active["id"]
            if conn.execute("SELECT COUNT(*) FROM research_workflow_runs WHERE status IN ('queued','running')").fetchone()[0] >= 20:
                raise ValueError("The workflow queue is full. Wait for a run to finish.")
            identifier, now = new_id("wf_"), utc_now()
            spec = WORKFLOWS[workflow]
            conn.execute("INSERT INTO research_workflow_runs(id,workflow,version,namespace,ticker,status,idempotency_key,created_at,updated_at) VALUES(?,?,?,?,?,'queued',?,?,?)", (identifier, workflow, spec.version, namespace, ticker, idempotency_key, now, now))
            register_ticker(conn, namespace, ticker, origin="earnings", origin_ref=identifier, workflow_id=identifier, created_at=now)
            for agent in spec.agents:
                conn.execute("INSERT INTO research_workflow_steps(run_id,agent_id) VALUES(?,?)", (identifier, agent.id))
            self.event(conn, identifier, "queued")
        return identifier

    def get(self, identifier):
        with self.db.operation() as conn:
            row = conn.execute("SELECT * FROM research_workflow_runs WHERE id=?", (identifier,)).fetchone()
            if not row:
                raise KeyError(identifier)
            result = dict(row)
            result["result"] = json_loads(result.pop("result_json"), {})
            spec = WORKFLOWS.get(result["workflow"])
            specs = {a.id: a for a in spec.agents} if spec else {}
            steps = {}
            for step in conn.execute("SELECT * FROM research_workflow_steps WHERE run_id=?", (identifier,)):
                value = dict(step)
                value["id"] = value.pop("agent_id")
                value.pop("run_id")
                value["name"] = specs[value["id"]].name if value["id"] in specs else value["id"].replace("_", " ").title()
                value["output"] = json_loads(value.pop("output_json"), {})
                steps[value["id"]] = value
            order = [key for key in specs if key in steps] + [key for key in steps if key not in specs]
            result["steps"] = [steps[key] for key in order]
            result["source_refresh_of"] = ((result["result"].get("trends") or {}).get("source_refresh") or {}).get("parent_workflow_id") or steps.get("trends", {}).get("output", {}).get("_refresh_from")
        return result

    def history(self, namespace="real", limit=25):
        with self.db.operation() as conn:
            rows = conn.execute("""SELECT id,workflow,version,namespace,ticker,status,created_at,updated_at,error,research_run_id,
                COALESCE(json_extract(result_json, '$.trends.source_refresh.parent_workflow_id'),
                    (SELECT json_extract(output_json, '$._refresh_from') FROM research_workflow_steps
                     WHERE run_id=research_workflow_runs.id AND agent_id='trends')) AS source_refresh_of
                FROM research_workflow_runs WHERE namespace=? ORDER BY created_at DESC,rowid DESC LIMIT ?""", (namespace, limit)).fetchall()
        return [dict(row) | {"steps": []} for row in rows]

    def step(self, identifier, agent_id, status, *, output=None, error=None):
        with self.db.transaction(immediate=True) as conn:
            run = conn.execute("SELECT status FROM research_workflow_runs WHERE id=?", (identifier,)).fetchone()
            if not run or run["status"] not in ACTIVE:
                return
            now = utc_now()
            if status == "running":
                conn.execute("UPDATE research_workflow_steps SET status=?,attempts=attempts+1,started_at=?,finished_at=NULL,error=NULL WHERE run_id=? AND agent_id=?", (status, now, identifier, agent_id))
            else:
                conn.execute("UPDATE research_workflow_steps SET status=?,output_json=?,error=?,finished_at=? WHERE run_id=? AND agent_id=?", (status, json_dumps(output or {}), error, now, identifier, agent_id))
            conn.execute("UPDATE research_workflow_runs SET updated_at=? WHERE id=?", (now, identifier))
            self.event(conn, identifier, status, agent_id, error)

    def finish(self, identifier, status, result, error=None):
        with self.db.transaction(immediate=True) as conn:
            conn.execute("UPDATE research_workflow_runs SET status=?,result_json=?,error=?,updated_at=? WHERE id=? AND status IN ('running','queued')", (status, json_dumps(result), error, utc_now(), identifier))
            run = conn.execute("SELECT namespace,ticker FROM research_workflow_runs WHERE id=?", (identifier,)).fetchone()
            if run:
                register_ticker(conn, run["namespace"], run["ticker"], origin="earnings", origin_ref=identifier, workflow_id=identifier, name=(result.get("company") or {}).get("name") or "")
            self.event(conn, identifier, status, detail=error)

    def retry(self, identifier):
        run = self.get(identifier)
        if run["status"] in ACTIVE:
            return identifier
        unavailable_search = {s["id"] for s in run["steps"] if s["id"] == "acquire" and s["output"].get("material_discovery_status") == "unavailable"}
        if run["status"] == "completed" and not unavailable_search:
            raise ValueError("This workflow is complete. Start a new run to check the latest earnings.")
        if run["workflow"] not in WORKFLOWS or run["version"] != WORKFLOWS[run["workflow"]].version:
            raise ValueError("The workflow version changed. Start a new run to preserve the historical record.")
        reset = {s["id"] for s in run["steps"] if s["status"] != "completed"} | unavailable_search
        spec = WORKFLOWS[run["workflow"]]
        # Invalidate every descendant, including a successfully assembled partial brief.
        for agent in spec.agents:
            if set(agent.dependencies) & reset:
                reset.add(agent.id)
        with self.db.transaction(immediate=True) as conn:
            bound = conn.execute("SELECT 1 FROM earnings_assessment_links WHERE workflow_id=? LIMIT 1", (identifier,)).fetchone()
            regular_bound = conn.execute("SELECT 1 FROM runs r, json_each(json_extract(r.input_snapshot_json,'$.investment_process.earnings')) e WHERE json_extract(e.value,'$.workflow_id')=? AND json_extract(e.value,'$.status') IN ('completed','partial') LIMIT 1", (identifier,)).fetchone()
            if bound or regular_bound:
                raise ValueError("This earnings package is frozen in an investment review. Start a new workflow to preserve its lineage.")
            active = conn.execute("SELECT id FROM research_workflow_runs WHERE namespace=? AND workflow=? AND ticker=? AND status IN ('queued','running') AND id<>?", (run["namespace"], run["workflow"], run["ticker"], identifier)).fetchone()
            if active:
                raise ValueError("Another run for this ticker is already active.")
            if run["research_run_id"]:
                raise ValueError("This evidence package already has a research handoff. Start a new workflow to preserve its lineage.")
            for agent_id in reset:
                conn.execute("UPDATE research_workflow_steps SET status='pending',output_json='{}',error=NULL,started_at=NULL,finished_at=NULL WHERE run_id=? AND agent_id=?", (identifier, agent_id))
            conn.execute("UPDATE research_workflow_runs SET status='queued',result_json='{}',error=NULL,updated_at=? WHERE id=?", (utc_now(), identifier))
            self.event(conn, identifier, "queued", detail="Retry incomplete agents and their dependants; retain completed checkpoints.")
        return identifier

    def refresh_sources(self, identifier, *, candidates=None):
        """Fork a source-coverage revision; linked assessment evidence is immutable."""
        parent = self.get(identifier)
        if parent["status"] in ACTIVE or not parent["result"].get("trends"):
            raise ValueError("Finish collecting this earnings package before refreshing its source coverage.")
        if parent["version"] != WORKFLOWS[parent["workflow"]].version:
            raise ValueError("Start a new latest-earnings workflow after a recipe version change.")
        packet = self.repo.source_packet(parent["namespace"], parent["result"].get("source_ids", []))
        by_id = {source["id"]: source for source in packet}
        if parent.get("research_run_id"):
            linked = self.repo.run_record(parent["research_run_id"])
            snapshot = json_loads(linked["input_snapshot_json"], {})
            frozen = {row["id"]: row for row in snapshot.get("source_versions", []) if row.get("id")}
            if any(source["id"] not in frozen or frozen[source["id"]].get("content_hash") != source["content_hash"] or frozen[source["id"]].get("version") != source.get("version") for source in packet):
                raise ValueError("An archived source no longer matches the linked assessment snapshot. Start a new latest-earnings workflow.")
        documents = list(parent["result"].get("documents", {}).values()) + parent["result"].get("materials", [])
        for document in documents:
            if not isinstance(document, dict) or document.get("status") != "available":
                continue
            source = by_id.get(document.get("source_id"))
            if not source or source.get("namespace") != parent["namespace"] or source.get("content_hash") != document.get("content_hash") or hashlib.sha256(source["content"].encode()).hexdigest() != document.get("content_hash"):
                raise ValueError("A retained source changed. Start a new latest-earnings workflow to establish fresh provenance.")
        prior_steps = {step["id"]: step for step in parent["steps"]}
        if any(prior_steps[key]["status"] not in {"completed", "partial"} for key in ("resolve", "locate", "acquire")):
            raise ValueError("A verified issuer, event and source package are required before source refresh.")
        with self.db.transaction(immediate=True) as conn:
            active = conn.execute("SELECT id FROM research_workflow_runs WHERE namespace=? AND workflow=? AND ticker=? AND status IN ('queued','running')", (parent["namespace"], parent["workflow"], parent["ticker"])).fetchone()
            if active:
                return active["id"]
            if conn.execute("SELECT COUNT(*) FROM research_workflow_runs WHERE status IN ('queued','running')").fetchone()[0] >= 20:
                raise ValueError("The workflow queue is full. Wait for a run to finish.")
            refreshed, now = new_id("wf_"), utc_now()
            conn.execute("INSERT INTO research_workflow_runs(id,workflow,version,namespace,ticker,status,created_at,updated_at) VALUES(?,?,?,?,?,'queued',?,?)", (refreshed, parent["workflow"], parent["version"], parent["namespace"], parent["ticker"], now, now))
            register_ticker(conn, parent["namespace"], parent["ticker"], origin="earnings", origin_ref=refreshed, workflow_id=refreshed, created_at=now)
            for agent in WORKFLOWS[parent["workflow"]].agents:
                previous = prior_steps[agent.id]
                copied = agent.id in {"resolve", "locate", "transcript"}
                if copied:
                    output = previous["output"]
                elif agent.id == "acquire":
                    output = previous["output"] | {"_refresh_from": identifier}
                elif agent.id == "trends":
                    output = parent["result"]["trends"] | {"_refresh_from": identifier, "_source_candidates": candidates}
                else:
                    output = {}
                conn.execute("INSERT INTO research_workflow_steps(run_id,agent_id,status,output_json,error) VALUES(?,?,?,?,?)", (refreshed, agent.id, previous["status"] if copied else "pending", json_dumps(output), previous.get("error") if copied else None))
            self.event(conn, refreshed, "queued", detail="Refresh primary source coverage for the same verified earnings event; prior workflow " + identifier + " and its assessments remain unchanged.")
        return refreshed

    def recover(self):
        with self.db.transaction(immediate=True) as conn:
            rows = conn.execute("SELECT id FROM research_workflow_runs WHERE status IN ('queued','running')").fetchall()
            for row in rows:
                conn.execute("UPDATE research_workflow_steps SET status='pending',error='Interrupted; resuming from checkpoint' WHERE run_id=? AND status='running'", (row["id"],))
                conn.execute("UPDATE research_workflow_runs SET status='queued',updated_at=? WHERE id=?", (utc_now(), row["id"]))
                self.event(conn, row["id"], "queued", detail="Resumed after application restart.")
        return [row["id"] for row in rows]


class ResearchWorkflows:
    def __init__(self, repo, registry, config, *, acquisition_factory=None, trends_factory=None, engine=None):
        self.repo, self.registry, self.config, self.engine = repo, registry, config, engine
        self.store = WorkflowStore(repo)
        self.analyses = AnalysisStore(config.evidence_dir)
        from .transcript_briefs import TranscriptBriefs
        self.transcript_briefs = TranscriptBriefs(config, repo, registry)
        self.acquisition_factory = acquisition_factory
        self.trends_factory = trends_factory
        self.tasks: dict[str, asyncio.Task] = {}
        self.execution_locks: dict[str, asyncio.Lock] = {}
        self.slots = asyncio.Semaphore(2)
        # Recipe implementations are separate from the dependency/lifecycle engine.
        # New feature recipes register their agent dispatcher and WorkflowSpec here.
        self.dispatchers = {"earnings": self._earnings_agent}
        if engine is not None:
            engine.earnings_workflows = self

    def catalog(self):
        return {"items": [{"id": spec.id, "name": spec.name, "description": spec.description, "version": spec.version,
                           "agents": [{"id": a.id, "name": a.name, "description": a.description, "dependencies": list(a.dependencies)} for a in spec.agents]} for spec in WORKFLOWS.values()]}

    def schedule(self, identifier, *, parent_run_id=None, parent_task_id=None):
        if identifier not in self.tasks or self.tasks[identifier].done():
            task = asyncio.create_task(self.execute(identifier, parent_run_id=parent_run_id, parent_task_id=parent_task_id), name=f"research-workflow:{identifier}")
            self.tasks[identifier] = task
            task.add_done_callback(lambda done: self.tasks.pop(identifier, None) if self.tasks.get(identifier) is done else None)
        return self.tasks[identifier]

    def recover(self):
        for identifier in self.store.recover():
            # Investment prerequisites share their parent run's pause/grant.
            # Its orchestrator resumes them; they cannot dispatch on their own.
            workflow = self.store.get(identifier)
            key = str(workflow.get("idempotency_key") or "")
            if key.startswith("investment-process:"):
                parent = self.repo.run_record(key.split(":", 2)[1])
                if not parent or parent["cancel_requested"] or parent["status"] == "cancelled":
                    self.store.finish(identifier, "cancelled", workflow["result"], "Parent investment review is unavailable or cancelled.")
            else:
                self.schedule(identifier)

    async def close(self):
        active = list(self.tasks.values())
        for task in active:
            task.cancel()
        if active:
            await asyncio.gather(*active, return_exceptions=True)
        # Keep active checkpoints resumable; user cancellation is a separate transition.

    async def cancel(self, identifier):
        run = self.store.get(identifier)
        if run["status"] in ACTIVE:
            self.store.finish(identifier, "cancelled", run["result"], "Cancelled by user.")
            task = self.tasks.get(identifier)
            if task:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            with self.repo.db.transaction(immediate=True) as conn:
                conn.execute("UPDATE research_workflow_steps SET status='skipped',error='Cancelled by user',finished_at=? WHERE run_id=? AND status IN ('pending','running')", (utc_now(), identifier))
        return self.detail(identifier)

    def detail(self, identifier):
        run = self.store.get(identifier)
        # Available call evidence remains readable while historical collection runs.
        if run["status"] in ACTIVE and any(s["id"] == "acquire" and s["output"] for s in run["steps"]):
            run["result"] = self._publish(run, {s["id"]: s for s in run["steps"]})
        for analysis in run["result"].get("analyses", {}).values():
            record = self.analyses.get(analysis["id"])
            if record:
                analysis["result"] = (
                    enrich_transcript(record["result"], record.get("inputs", {}).get("text"))
                    if record.get("kind") == "transcript" else record["result"]
                )
                if record.get("kind") == "transcript":
                    analysis["result"]["plain_language"] = self.transcript_briefs.load(analysis["result"], run["namespace"])
        return run

    async def execute(self, identifier, *, parent_run_id=None, parent_task_id=None):
        # Standalone collection and multiple investing cases can encounter the
        # same active ticker. One lock owns its durable checkpoints at a time.
        from .investment_process import ProcessPaused
        async with self.execution_locks.setdefault(identifier, asyncio.Lock()):
            try:
                await self._execute(identifier, parent_run_id=parent_run_id, parent_task_id=parent_task_id)
            except ProcessPaused:
                run = self.store.get(identifier)
                key = str(run.get("idempotency_key") or "")
                owner = key.split(":", 2)[1] if key.startswith("investment-process:") else parent_run_id
                parent = self.repo.run_record(owner) if owner else None
                if parent and (parent["cancel_requested"] or parent["status"] == "cancelled"):
                    self.store.finish(identifier, "cancelled", run["result"], "Parent investment review was cancelled.")
                raise

    async def _execute(self, identifier, *, parent_run_id=None, parent_task_id=None):
        async with self.slots:
            run = self.store.get(identifier)
            if run["status"] not in ACTIVE:
                return
            from .investment_process import dispatch_guard, ProcessPaused
            parent_key = str(run.get("idempotency_key") or "")
            if parent_key.startswith("investment-process:"):
                owner = parent_key.split(":", 2)[1]
                if parent_run_id != owner:
                    parent_task_id = next((task["id"] for task in self.repo.tasks_for_run(owner)
                                           if task["agent_id"] == "A03" and task["status"] not in {"completed", "cancelled"}), None)
                parent_run_id = owner
            guard = (lambda: dispatch_guard(self.repo, parent_run_id, parent_task_id)) if parent_run_id else None
            if guard:
                guard()
            if run["status"] not in ACTIVE:
                return
            spec = WORKFLOWS.get(run["workflow"])
            if not spec or run["version"] != spec.version:
                self.store.finish(identifier, "failed", {}, "The workflow version changed. Start a new run.")
                return
            with self.repo.db.transaction(immediate=True) as conn:
                conn.execute("UPDATE research_workflow_runs SET status='running',updated_at=? WHERE id=?", (utc_now(), identifier))
            try:
                if self.acquisition_factory:
                    acquisition = self.acquisition_factory(run["namespace"])
                else:
                    from .earnings_sources import EarningsAcquisition
                    acquisition = EarningsAcquisition(self.repo, self.registry, self.config, namespace=run["namespace"])
                acquisition.dispatch_guard = guard
                while True:
                    if guard:
                        guard()
                    run = self.store.get(identifier)
                    if run["status"] not in ACTIVE:
                        return
                    steps = {s["id"]: s for s in run["steps"]}
                    pending = [a for a in spec.agents if steps[a.id]["status"] == "pending"]
                    if not pending:
                        break
                    ready = [a for a in pending if all(steps[d]["status"] in DONE for d in a.dependencies)]
                    if not ready:
                        raise ValueError("Workflow dependencies could not be resolved.")
                    active_agents = [asyncio.create_task(self._execute_agent(run, agent, steps, acquisition)) for agent in ready]
                    try:
                        await asyncio.gather(*active_agents)
                    except BaseException:
                        for active_agent in active_agents:
                            active_agent.cancel()
                        await asyncio.gather(*active_agents, return_exceptions=True)
                        raise
                run = self.store.get(identifier)
                steps = {s["id"]: s for s in run["steps"]}
                result = steps[spec.result_agent]["output"]
                failed = [steps[key] for key in spec.critical_agents if steps[key]["status"] in {"failed", "skipped"}]
                status = "failed" if failed else "partial" if result.get("gaps") else "completed"
                self.store.finish(identifier, status, result, failed[0].get("error") if failed else None)
            except ProcessPaused:
                # Preserve unfinished checkpoints without a failed-workflow
                # label. On resume reset only interrupted running stages.
                with self.repo.db.transaction(immediate=True) as conn:
                    conn.execute("UPDATE research_workflow_steps SET status='pending' WHERE run_id=? AND status='running'", (identifier,))
                    conn.execute("UPDATE research_workflow_runs SET status='queued' WHERE id=? AND status='running'", (identifier,))
                raise
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.store.finish(identifier, "failed", self.store.get(identifier)["result"], str(exc)[:2000])

    async def _execute_agent(self, run, agent, steps, acquisition):
        from .investment_process import ProcessPaused
        failed = [d for d in agent.dependencies if steps[d]["status"] in {"failed", "skipped"}]
        if failed and not agent.allow_failed_dependencies:
            self.store.step(run["id"], agent.id, "skipped", error="Required agent did not finish: " + ", ".join(failed))
            return
        self.store.step(run["id"], agent.id, "running")
        try:
            timeout = (max(120, self.config.codex_timeout_seconds * 8 + 240) if agent.id == "transcript"
                       else max(120, self.config.codex_timeout_seconds * 3 + 240) if agent.id == "trends"
                       else max(60, self.config.codex_timeout_seconds + 180) if agent.id in {"resolve", "locate", "acquire"} else 180)
            output = await asyncio.wait_for(self.dispatchers[run["workflow"]](run, agent.id, steps, acquisition), timeout=timeout)
            incomplete = output.get("gaps") or output.get("material_discovery_status") == "unavailable"
            self.store.step(run["id"], agent.id, "partial" if incomplete else "completed", output=output)
        except ProcessPaused:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            message = "Agent timed out; retry this workflow." if isinstance(exc, TimeoutError) else str(exc)
            self.store.step(run["id"], agent.id, "failed", error=message[:2000])

    async def _earnings_agent(self, run, agent_id, steps, acquisition):
        outputs = {key: step["output"] for key, step in steps.items()}
        if outputs.get("resolve") and outputs.get("locate", {}).get("issuer_domain_verification"):
            from .earnings_sources import company_with_verified_issuer_domain
            with self.repo.db.operation() as conn:
                outputs["resolve"] = company_with_verified_issuer_domain(outputs["resolve"], outputs["locate"], conn, run["namespace"])
        if agent_id == "resolve":
            return await acquisition.resolve(run["ticker"])
        if agent_id == "locate":
            if outputs["resolve"].get("earnings_applicability") == "not_applicable":
                raise ValueError("Company quarterly earnings do not apply to this SEC-verified investment fund. Use fund holdings, NAV and distributions for the investment questions.")
            return await acquisition.locate(outputs["resolve"])
        if agent_id == "acquire":
            prior_acquired = outputs.get("acquire", {})
            if prior_acquired.get("_refresh_from"):
                acquired = await acquisition.refresh_missing_filings(outputs["resolve"], outputs["locate"], prior_acquired, research_as_of=run["created_at"])
            else:
                acquired = await acquisition.acquire(outputs["resolve"], outputs["locate"])
            # Sources are the authoritative text store. Checkpoints carry provenance only.
            acquired["documents"] = {role: public_document(doc) for role, doc in acquired.get("documents", {}).items()}
            acquired["materials"] = [public_document(doc) for doc in acquired.get("materials", [])]
            available = list(dict.fromkeys(source_id for d in [*acquired["documents"].values(), *acquired["materials"]] if d.get("status") == "available" for source_id in (d.get("source_id"), d.get("issuer_link_source_id")) if source_id))
            self.repo.source_packet(run["namespace"], available)
            return acquired
        if agent_id == "trends":
            if self.trends_factory:
                analyst = self.trends_factory(acquisition)
            else:
                from .earnings_trends import EarningsTrends
                analyst = EarningsTrends(acquisition)
            documents = dict(outputs["acquire"].get("documents", {}))
            for index, material in enumerate(outputs["acquire"].get("materials", [])):
                documents[f"material_{index}"] = material
            prior_trends = outputs.get("trends", {})
            if prior_trends.get("_refresh_from"):
                trends = await analyst.refresh_primary(outputs["resolve"], outputs["locate"], prior_trends, documents, candidates=prior_trends.get("_source_candidates"), research_as_of=run["created_at"])
            else:
                trends = await analyst.collect(outputs["resolve"], outputs["locate"], documents)
            self.repo.source_packet(run["namespace"], [source["source_id"] for source in trends.get("sources", []) if source.get("source_id")])
            return trends
        if agent_id in {"transcript", "filings"}:
            computed = await asyncio.to_thread(self._analyze, run, agent_id, outputs)
            if "record_request" not in computed:
                return computed
            # Computation may continue in its worker after cancellation. Only the
            # live task can persist a result, so cancelled work creates no history.
            if self.store.get(run["id"])["status"] not in ACTIVE:
                raise asyncio.CancelledError()
            record = self.analyses.save(computed["kind"], computed.pop("record_request"), computed.pop("result"))
            if agent_id == "transcript" and self.acquisition_factory is None:
                try:
                    def summary_guard():
                        if self.store.get(run["id"])["status"] not in ACTIVE:
                            raise asyncio.CancelledError()
                        if acquisition.dispatch_guard:
                            acquisition.dispatch_guard()
                        elif self.repo.firm_paused():
                            raise ValueError("Research is paused; prepare explanations when processing resumes.")
                    owner_key = str(run.get("idempotency_key") or "")
                    owner = owner_key.split(":", 2)[1] if owner_key.startswith("investment-process:") else None
                    origin = self.repo.canonical_run_origin(owner) if owner else "earnings"
                    await self.transcript_briefs.generate(record["result"], run["namespace"], guard=summary_guard, origin=origin)
                except Exception as exc:
                    # Reading aids cannot make the original transcript disappear.
                    computed.setdefault("gaps", []).append("Plain-language call explanation unavailable: " + str(exc)[:240])
            return computed | {"id": record["id"]}
        if agent_id == "context":
            return await asyncio.to_thread(self._context, run)
        if agent_id == "publish":
            return self._publish(run, steps)
        raise ValueError("No handler is registered for agent " + agent_id)

    def _analyze(self, run, agent_id, outputs):
        documents = outputs["acquire"].get("documents", {})
        roles = ("transcript",) if agent_id == "transcript" else ("prior_filing", "current_filing")
        missing = [role for role in roles if documents.get(role, {}).get("status") != "available"]
        if missing:
            message = f"{'Transcript analysis' if agent_id == 'transcript' else 'Optional filing comparison'} is waiting for " + ", ".join(role.replace("_", " ") for role in missing) + "."
            return {"gaps" if agent_id == "transcript" else "comparison_gaps": [message]}
        sources = {s["id"]: s for s in self.repo.source_packet(run["namespace"], [documents[role]["source_id"] for role in roles])}
        texts = [sources[documents[role]["source_id"]]["content"] for role in roles]
        lineage = [{"role": role, "source_id": documents[role]["source_id"], "content_hash": sources[documents[role]["source_id"]]["content_hash"]} for role in roles]
        if agent_id == "transcript" and documents["transcript"].get("extraction_version"):
            from .discovery import FetchedSource
            from .earnings_sources import ACQUISITION_VERSION, _transcript_text
            doc = documents["transcript"]
            texts[0] = _transcript_text(FetchedSource(doc.get("url", ""), doc.get("url", ""), texts[0], doc.get("title", ""), doc.get("retrieved_at", "")))
            input_hash = hashlib.sha256(texts[0].encode()).hexdigest()
            if doc.get("analysis_content_hash") and doc["analysis_content_hash"] != input_hash:
                raise ValueError("Transcript extraction changed after collection. Retry collection before using this analysis.")
            lineage[0] |= {"analysis_input_hash": input_hash, "extraction_version": ACQUISITION_VERSION}
        kind = "transcript" if agent_id == "transcript" else "filing_comparison"
        event = outputs.get("locate", {})
        analysis_version = VERSION if agent_id == "transcript" else FILING_COMPARISON_VERSION
        metadata = {"ticker": run["ticker"], "workflow_run_id": run["id"], "namespace": run["namespace"], "source_lineage": lineage, "analysis_version": analysis_version}
        gaps = []
        if agent_id == "transcript":
            result = analyze_transcript(texts[0], run["ticker"])
            metadata |= {"title": f"{run['ticker']} {event.get('fiscal_period', '')} earnings call", "period": event.get("fiscal_period", ""), "text": texts[0]}
        else:
            previous, current = documents["prior_filing"], documents["current_filing"]
            result = compare_filings(texts[0], texts[1], previous_form=previous.get("form"), current_form=current.get("form"))
            metadata |= {"title": f"{run['ticker']} {current.get('form', 'filing')} narrative changes", "previous_period": previous.get("period_end", ""), "current_period": current.get("period_end", ""), "previous_text": texts[0], "current_text": texts[1], "comparison_basis": outputs["acquire"].get("comparison_basis")}
            for side, coverage in result.get("documents", {}).items():
                missing_sections = coverage.get("missing_core_sections", [])
                if missing_sections:
                    gaps.append(f"{side.capitalize()} filing extraction has no narrative for {', '.join(missing_sections)}; review source coverage before treating changes as complete.")
        return {"record_request": metadata, "result": result, "kind": kind, "summary": result["summary"], "method": result["method"], "source_lineage": lineage, "analysis_version": analysis_version, "gaps": gaps}

    def _context(self, run):
        ticker, namespace = run["ticker"], run["namespace"]
        with self.repo.db.operation() as conn:
            research = [dict(r) for r in conn.execute("""SELECT id,ticker,request,status,created_at FROM runs WHERE namespace=? AND
                (upper(ticker)=? OR id IN (SELECT m.run_id FROM research_library_mentions m
                 JOIN research_library_entries e ON e.id=m.entry_id WHERE e.namespace=? AND e.ticker=?))
                ORDER BY created_at DESC LIMIT 10""", (namespace, ticker, namespace, ticker))]
            positions = [dict(r) for r in conn.execute("SELECT symbol,quantity,currency,status,observed_at,account_id FROM positions WHERE namespace=? AND upper(symbol)=? ORDER BY observed_at DESC LIMIT 10", (namespace, ticker))]
            watchlist = [dict(r) for r in conn.execute("SELECT id,ticker,status,reason,reopen_trigger,updated_at FROM research_items WHERE namespace=? AND upper(ticker)=? ORDER BY updated_at DESC LIMIT 10", (namespace, ticker))]
            library_items = [dict(r) | {"historical": False, "live": True} for r in conn.execute("SELECT id,name,updated_at FROM research_library_entries WHERE namespace=? AND ticker=?", (namespace, ticker))]
        context = {"research": {"items": research}, "portfolio": {"positions": positions}, "watchlist": {"items": watchlist}, "library": {"items": library_items}, "congress": {"available": False, "items": [], "note": "No Congress snapshot installed."}, "strategies": {"saved_studies": 0, "items": [], "note": "Backtests keep their original assumptions and dates. This workflow does not run trading strategies."}}
        # Personal historical archives are real-world context, never mixed into demo cases.
        if namespace != "real":
            return context
        library = self.config.evidence_dir / "research-library" / "desk.sqlite3"
        if library.is_file():
            with closing(sqlite3.connect(library.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
                for row in conn.execute("SELECT id,data,updated_at FROM ideas ORDER BY updated_at DESC"):
                    idea = json_loads(row[1], {})
                    if str(idea.get("ticker", "")).upper() == ticker:
                        if library_items:
                            library_items[0].update({"historical": True, "historical_id": row[0]})
                        else:
                            library_items.append({"id": row[0], "name": idea.get("name"), "updated_at": row[2], "historical": True})
        labs = LabData(self.config.evidence_dir / "labs" / "congress")
        if labs.available:
            manifest = labs.manifest()
            candidates = labs.rows(search=ticker, offset=0, limit=100)
            # Text search yields candidates; ticker equality prevents COST matching Costco prose elsewhere.
            items = [r for r in candidates["items"] if str(r.get("ticker", "")).upper() == ticker]
            context["congress"] = {"available": True, "snapshot_date": manifest.get("snapshotDate"), "items": items[:10], "note": "Historical disclosed transactions; these do not establish current holdings. Up to 100 search candidates checked."}
        studies = load_json(self.config.evidence_dir / "labs" / "backtests" / "index.json", [])
        context["strategies"] |= {"saved_studies": len(studies), "items": studies[:5]}
        return context

    def _publish(self, run, steps):
        outputs = {key: step["output"] for key, step in steps.items()}
        acquired = outputs.get("acquire", {})
        release = outputs.get("locate", {}).get("release_document")
        documents = ({"release": release} if release else {}) | acquired.get("documents", {})
        materials = acquired.get("materials") or [d for role, d in documents.items() if role != "prior_filing" and d.get("status") == "available"]
        analyses = {output["kind"]: output for key in ("transcript", "filings") if (output := outputs.get(key, {})).get("id")}
        gaps = [gap for key, step in steps.items() if key != "filings" for gap in step["output"].get("gaps", [])]
        gaps += [step["name"] + ": " + step["error"] for key, step in steps.items() if key != "filings" and step.get("error")]
        if acquired.get("material_discovery_status") == "unavailable":
            gaps.append("The search for additional earnings materials did not finish. Available sources remain readable; retry can complete the search.")
        comparison_gaps = list(acquired.get("comparison_gaps", [])) + outputs.get("filings", {}).get("comparison_gaps", []) + outputs.get("filings", {}).get("gaps", [])
        if steps.get("filings", {}).get("error"):
            comparison_gaps.append(steps["filings"]["error"])
        for role in ("transcript",):
            document = documents.get(role, {})
            if document.get("status") != "available":
                gaps.append(document.get("reason") or f"{role.replace('_', ' ').capitalize()} is unavailable.")
        company, event = outputs.get("resolve", {}), outputs.get("locate", {})
        if event.get("issuer_domain_verification"):
            from .earnings_sources import company_with_verified_issuer_domain
            with self.repo.db.operation() as conn:
                company = company_with_verified_issuer_domain(company, event, conn, run["namespace"])
        trends = outputs.get("trends") or None
        source_ids = list(dict.fromkeys(source_id for d in [*documents.values(), *materials, *(trends or {}).get("sources", [])] if d.get("status", "available") == "available" for source_id in (d.get("source_id"), d.get("issuer_link_source_id")) if source_id))
        if company.get("issuer_domain_verification"):
            source_ids = list(dict.fromkeys([*source_ids, *[item["source_id"] for item in company["issuer_domain_verification"]["sources"]]]))
        source_bindings = {source["id"]: {"version": source["version"], "content_hash": source["content_hash"]}
                           for source in self.repo.source_packet(run["namespace"], source_ids)}
        summary = f"{run['ticker']} · {event.get('fiscal_period') or 'Latest earnings'}. " + " ".join(a["summary"] for a in analyses.values())
        if not analyses:
            summary += "Document analysis is waiting for verified sources."
        return {"company": company, "event": event, "source_bindings": source_bindings, "documents": documents, "materials": materials, "material_gaps": acquired.get("material_gaps", []), "comparison_gaps": list(dict.fromkeys(comparison_gaps)), "material_checks": acquired.get("material_checks", []), "trends": trends, "analyses": analyses, "context": outputs.get("context", {}), "summary": summary.strip(), "gaps": list(dict.fromkeys(gaps)), "source_ids": source_ids, "as_of": utc_now(), "comparison_basis": acquired.get("comparison_basis"), "workflow_version": run["version"], "analysis_version": VERSION, "limitations": ["Tone and filing differences are reading aids. Historical trends retain their source definitions, periods and coverage gaps.", "Guidance comparisons describe observed errors, not a guarantee of future forecast accuracy.", "A completed document package is evidence for research, not an investment recommendation."]}

    def handoff(self, identifier, *, reassess=False):
        run = self.store.get(identifier)
        if run["status"] in ACTIVE or not run["result"].get("source_ids"):
            raise ValueError("Wait for a saved evidence package before starting investment research.")
        if run["research_run_id"] and not reassess:
            return {"run_id": run["research_run_id"], "status": "existing"}
        if run["research_run_id"] and reassess:
            prior = self.repo.run_record(run["research_run_id"])
            if prior and prior["status"] not in {"completed", "failed", "blocked", "cancelled"}:
                return {"run_id": prior["id"], "status": "existing"}
        from ..orchestration.workflow import build_research_tasks
        package = run["result"]
        event = package.get("event", {})
        question = (f"Assess {run['ticker']} after its {event.get('fiscal_period', 'latest')} earnings, period ended {event.get('period_end', 'unknown')}. "
                    "Use the attached archived documents. Evaluate business performance, changes in guidance and risks, narrative changes, valuation, catalyst, downside and portfolio fit. "
                    "Explain what happened in the call and what it changes for an existing shareholder's thesis. "
                    "Read analyst questions together with the complete management answers, including qualifying or reassuring statements. "
                    "Use the sourced historical metric series to distinguish quarterly growth, renewal and margin trends. Compare capital spending with prior dated guidance, retaining ranges and revisions; explain misses only where the source supports the cause. "
                    "Check the current portfolio and the dates/status of its position records before discussing exposure; do not assume a holding, quantity, cost basis or risk tolerance. "
                    "If a position is verified, distinguish thesis implications, concentration and downside from a new-entry decision. "
                    "Produce a defended 12-month price target with bear/base/bull calculations from source-backed financial baselines and explicit forecast assumptions. Separate the forecast earnings period from the target horizon. "
                    "If holdings are unavailable, retain the price target, distinguish research from portfolio sizing, and provide conditional review questions for position-specific actions. "
                    "Distinguish management statements from verified facts; retain unknowns and filing gaps. "
                    "The automated lexical analysis is a navigation aid, not proof or a buy/sell signal. "
                    "Known acquisition/analysis gaps: " + "; ".join([*package.get("gaps", []), *package.get("material_gaps", []), *package.get("comparison_gaps", [])]))
        from ..db import digest
        with self.repo.db.operation() as conn:
            revision = conn.execute("SELECT COUNT(*) FROM earnings_assessment_links WHERE workflow_id=?", (identifier,)).fetchone()[0]
        key = f"workflow:{identifier}:assessment:{revision + 1}" if reassess else f"workflow:{identifier}:research"
        body = RunCreate(question=question, namespace=run["namespace"], ticker=run["ticker"], horizon="12 months", source_ids=package["source_ids"], research_contract=FIVE_QUESTION_CONTRACT, origin="user", origin_ref=f"workflow:{identifier}", idempotency_key=key)
        self.repo.source_packet(body.namespace, body.source_ids)
        result, _ = self.repo.create_run(body, build_research_tasks(body.question, body.horizon, body.ticker, body.namespace, lean=True), allow_semantic_reuse=False)
        with self.repo.db.transaction(immediate=True) as conn:
            if run["research_run_id"]:
                conn.execute("INSERT OR IGNORE INTO earnings_assessment_links(run_id,workflow_id,package_hash,created_at) VALUES(?,?,?,?)", (run["research_run_id"], identifier, digest(package), utc_now()))
            conn.execute("INSERT OR IGNORE INTO earnings_assessment_links(run_id,workflow_id,package_hash,created_at) VALUES(?,?,?,?)", (result["run_id"], identifier, digest(package), utc_now()))
            row = conn.execute("SELECT input_snapshot_json FROM runs WHERE id=?", (result["run_id"],)).fetchone()
            snapshot = json_loads(row[0], {})
            snapshot["assessment_pipeline"] = "earnings-assessment.v1"
            conn.execute("UPDATE runs SET input_snapshot_json=? WHERE id=?", (json_dumps(snapshot), result["run_id"]))
            conn.execute("UPDATE research_workflow_runs SET research_run_id=?,updated_at=? WHERE id=?", (result["run_id"], utc_now(), identifier))
            self.store.event(conn, identifier, "research_handoff", detail=result["run_id"])
        paused = self.repo.firm_paused()
        if self.engine and not paused:
            self.engine.schedule(result["run_id"])
        return {"run_id": result["run_id"], "status": "paused" if paused else "queued"}
