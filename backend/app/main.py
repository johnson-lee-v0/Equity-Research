"""FastAPI application for the local Hedge Fund Office backend."""
from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import shutil
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Literal
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import ValidationError, BaseModel, ConfigDict, Field

from .config import Settings, settings
from .db import Database, NAMESPACES, utc_now
from .memory.repository import Repository
from .orchestration.workflow import Orchestrator, build_research_tasks
from .providers.registry import ProviderRegistry
from .research.sec import SecConnector
from .research.reddit_service import RedditMonitor
from .research.case_store import CaseDecisionStore
from .research.watch_service import WatchMonitor
from .research.learning import LearningJournal, OutcomeRequest, LifecycleRequest
from .research.documents import document_record
from .research.decision_questions import FIVE_QUESTION_CONTRACT
from .research.library import create_library_router
from .research.library_store import LibraryStore
from .api.labs import create_labs_router
from .api.document_intelligence import create_document_router
from .api.research_workflows import create_workflow_router
from .api.research_actions import create_research_actions_router
from .api.memory import create_memory_router
from .api.market_news import create_market_news_router
from .api.valuation_history import create_valuation_history_router
from .api.run_reader import project_run_reader
from .memory.shared import SharedMemoryService
from .research.workflows import ResearchWorkflows
from .schemas import (
    ControlRequest,
    CoverageRequest,
    ImportRequest,
    ModelConfig,
    ModelPolicyRequest,
    PortfolioPolicy,
    PortfolioPolicyRequest,
    MonitoringRequest,
    MonitoringUpdate,
    ProviderPreflightRequest,
    ResearchRequest,
    RedditIntakeRequest,
    RiskSettingsRequest,
    RunCreate,
    RunMessage,
    SecRequest,
    SimulationCreate,
)
from .simulation.engine import SimulationService


class RedditConnectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    namespace: Literal["real", "demo"] = "real"
    subreddit: str = Field(default="wallstreetbets", min_length=2, max_length=50)
    window_days: int = Field(default=7, ge=1, le=30)
    enabled: bool = False


class RedditItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    namespace: Literal["real", "demo"] = "real"
    idempotency_key: str | None = Field(default=None, max_length=200)


class RedditPostRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    namespace: Literal["real"] = "real"
    url: str = Field(min_length=1, max_length=2_000)
    dispatch: bool = True


class ApiProblem(Exception):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
        self.status_code = status_code


def problem_response(problem: ApiProblem) -> JSONResponse:
    return JSONResponse(status_code=problem.status_code, content={"error": {"code": problem.code, "message": problem.message, "details": problem.details}})


def namespace_value(value: str) -> str:
    if value not in NAMESPACES:
        raise ApiProblem("invalid_namespace", "namespace must be real, demo or simulation")
    return value


async def _monitoring_loop(repo: Repository, engine: Orchestrator, app_config: Settings, stop: asyncio.Event, activity: dict[str, bool] | None = None) -> None:
    """Run opt-in local monitoring rules while the backend process is alive."""
    while not stop.is_set():
        if activity is not None:
            activity["active"] = True
        try:
            due = repo.claim_due_schedules()
            for schedule in due:
                namespace = schedule["namespace"]
                source_ids = list(schedule.get("source_ids") or [])
                try:
                    # A recurring rule follows immutable amendment lineage at
                    # dispatch time.  Historical research runs keep their
                    # original source IDs, while a monitor always researches
                    # the newest descendant of each configured source.
                    effective_source_ids = repo.source_head_ids(namespace, source_ids)
                    repo.source_packet(namespace, effective_source_ids)
                    fingerprint = repo.source_fingerprint(namespace, source_ids)
                    if schedule.get("mode") == "source_change" and not repo.monitoring_source_changed(schedule["id"], fingerprint):
                        # The occurrence is still acknowledged for durable
                        # catch-up accounting, but unchanged immutable source
                        # heads do not spend a model call.
                        repo.ack_schedule(schedule["id"], schedule["scheduled_for"], source_fingerprint=fingerprint)
                        continue
                    body = RunCreate(
                        question=schedule["request"],
                        namespace=namespace,
                        horizon="event",
                        ticker=None,
                        source_ids=effective_source_ids,
                        research_contract=FIVE_QUESTION_CONTRACT,
                        idempotency_key=f"schedule:{schedule['id']}:{schedule['scheduled_for']}",
                    )
                    task_plan = build_research_tasks(body.question, body.horizon, body.ticker, namespace, lean=True)
                    # A scheduled occurrence has its own durable idempotency
                    # key.  Semantic reuse is intentionally disabled here:
                    # returning an older completed run would make
                    # ack_schedule reject the occurrence key and leave the
                    # pending scan stuck forever.
                    result, reused = repo.create_run(body, task_plan, allow_semantic_reuse=False)
                    repo.ack_schedule(schedule["id"], schedule["scheduled_for"], result["run_id"], fingerprint if schedule.get("mode") == "source_change" else None)
                    if not reused and not repo.firm_paused():
                        engine.schedule(result["run_id"])
                    elif repo.firm_paused():
                        repo.emit(namespace, "paused", payload={"schedule_id": schedule["id"], "message": "Monitoring scan was recorded but firm pause prevented dispatch."})
                except ValueError as exc:
                    repo.emit(namespace, "blocked", payload={"schedule_id": schedule["id"], "message": "Monitoring scan was blocked because its source packet is unavailable."})
                except Exception:
                    repo.emit(namespace, "failed", payload={"schedule_id": schedule["id"], "message": "Monitoring scan could not be queued."})
        except Exception:
            # A transient local database/read error must not kill the
            # scheduler.  The next tick will retry due rows after the claim
            # transaction has either committed or rolled back.
            pass
        finally:
            if activity is not None:
                activity["active"] = False
        try:
            await asyncio.wait_for(stop.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            continue


def create_app(config: Settings | None = None, repository: Repository | None = None, providers: ProviderRegistry | None = None, *, reddit_connector: Any | None = None) -> FastAPI:
    app_config = config or settings
    repo = repository or Repository(config=app_config)
    registry = providers or ProviderRegistry(app_config)
    engine = Orchestrator(repo, registry, app_config)
    simulations = SimulationService(repo, registry)
    sec = SecConnector(repo, app_config)
    simulation_tasks: dict[str, asyncio.Task] = {}
    monitor_activity: dict[str, bool] = {"active": False}
    reddit_monitor = RedditMonitor(repo, engine, app_config, reddit_connector)
    case_store = CaseDecisionStore(repo)
    watch_monitor = WatchMonitor(repo, engine, app_config)
    learning_journal = LearningJournal(repo)
    research_workflows = ResearchWorkflows(repo, registry, app_config, engine=engine)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        application.state.repository = repo
        application.state.providers = registry
        application.state.engine = engine
        application.state.simulations = simulations
        application.state.simulation_tasks = simulation_tasks
        application.state.monitor_activity = monitor_activity
        application.state.sec = sec
        application.state.reddit_connector = reddit_connector
        application.state.reddit_monitor = reddit_monitor
        application.state.watch_monitor = watch_monitor
        application.state.research_workflows = research_workflows
        repo.recover()
        simulations.recover()
        repo.load_portfolio_seed()
        LibraryStore(repo, app_config.evidence_dir).backfill()
        research_workflows.recover()
        # Build the readable vault once; graph GETs remain read-only. Notes
        # edited by the user are preserved by the shared-memory service.
        await asyncio.to_thread(SharedMemoryService(repo, app_config.shared_memory_vault_path).sync, namespace="real")
        monitor_stop = asyncio.Event()
        monitor_task = asyncio.create_task(_monitoring_loop(repo, engine, app_config, monitor_stop, monitor_activity), name="road2m-monitoring")
        application.state.monitor_stop = monitor_stop
        application.state.monitor_task = monitor_task
        reddit_stop = asyncio.Event()
        reddit_task = asyncio.create_task(reddit_monitor.run(reddit_stop), name="road2m-reddit-intake")
        application.state.reddit_stop = reddit_stop
        application.state.reddit_task = reddit_task
        watch_stop = asyncio.Event()
        watch_task = asyncio.create_task(watch_monitor.run(watch_stop), name="road2m-watchlist")
        yield
        await research_workflows.close()
        watch_stop.set()
        watch_task.cancel()
        await asyncio.gather(watch_task, return_exceptions=True)
        if reddit_stop is not None:
            reddit_stop.set()
        if reddit_task is not None:
            reddit_task.cancel()
            await asyncio.gather(reddit_task, return_exceptions=True)
        monitor_stop.set()
        monitor_task.cancel()
        await asyncio.gather(monitor_task, return_exceptions=True)
        await engine.cancel_active()
        active = [task for task in engine._run_tasks.values() if not task.done()]
        for task in active:
            task.cancel()
        if active:
            await asyncio.gather(*active, return_exceptions=True)
        active_simulations = [task for task in simulation_tasks.values() if not task.done()]
        for task in active_simulations:
            task.cancel()
        if active_simulations:
            await asyncio.gather(*active_simulations, return_exceptions=True)
        simulations.recover()
        # The optional local Laya worker is process-local and must not outlive
        # the application lifespan.  Import lazily so ordinary API startup
        # never loads the isolated runtime or model weights.
        from .research.laya_runtime import close_runtime

        close_runtime()

    application = FastAPI(title="ResearchCouncil API", version="0.1.0", lifespan=lifespan)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(app_config.cors_origins),
        allow_origin_regex=r"^https?://(?:localhost|127\.0\.0\.1|\[::1\])(?::\d+)?$",
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "OPTIONS"],
        allow_headers=["Content-Type", "X-Road2M-Client", "Last-Event-ID"],
    )

    def local_host_header(value: str | None, *, synthetic_peer: bool = False) -> bool:
        """Validate the listener Host header as well as the peer address.

        A foreign Host header can otherwise reach a loopback-bound process
        through a proxy or a rebinding setup.  TestClient's synthetic hosts
        remain accepted for in-process contract tests.
        """
        if not value:
            return False
        raw = value.strip()
        if not raw or any(char in raw for char in ("/", "?", "#", "@")):
            return False
        try:
            parsed = urlsplit("//" + raw)
            hostname = (parsed.hostname or "").lower()
            port = parsed.port
        except ValueError:
            return False
        if port is not None and not 1 <= port <= 65535:
            return False
        allowed = {"127.0.0.1", "localhost", "::1"}
        # Starlette's in-process clients use synthetic peer names.  They are
        # accepted only when the socket peer is synthetic too; a real
        # loopback listener may never use them as a Host authority.
        if synthetic_peer:
            allowed.update({"testclient", "testserver"})
        return hostname in allowed

    @application.exception_handler(ApiProblem)
    async def api_problem_handler(_: Request, exc: ApiProblem):
        return problem_response(exc)

    @application.exception_handler(RequestValidationError)
    async def validation_handler(_: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content={"error": {"code": "validation_error", "message": "Request validation failed.", "details": {"issues": exc.errors()}}})

    @application.exception_handler(HTTPException)
    async def http_problem_handler(_: Request, exc: HTTPException):
        detail = exc.detail if isinstance(exc.detail, str) else "Request rejected."
        return JSONResponse(status_code=exc.status_code, content={"error": {"code": "request_rejected", "message": detail, "details": {}}})

    @application.middleware("http")
    async def local_only(request: Request, call_next):
        # Use the ASGI route path, which is independent of the untrusted Host
        # header.  Reconstructing ``request.url`` first lets a Host value with
        # a path/query evade this check before routing resolves /api/*.
        if str(request.scope.get("path", "")).startswith("/api"):
            host = (request.client.host if request.client else "").lower()
            synthetic_peer = host in {"testclient", "testserver"}
            # testserver is accepted for local in-process contract tests; the
            # actual listener remains loopback in uvicorn launch instructions.
            if host not in {"127.0.0.1", "::1", "localhost"} and not synthetic_peer:
                return problem_response(ApiProblem("local_only", "The backend accepts requests from the local machine only.", status_code=403))
            if not local_host_header(request.headers.get("host"), synthetic_peer=synthetic_peer):
                return problem_response(ApiProblem("local_only", "The backend accepts local Host headers only.", status_code=403))
        return await call_next(request)

    async def mutation_guard(request: Request, client: str | None = Header(default=None, alias="X-Road2M-Client")) -> None:
        if client != "local-ui":
            raise ApiProblem("missing_client_header", "Mutations require X-Road2M-Client: local-ui.", status_code=400)
        origin = request.headers.get("origin")
        if origin and origin not in app_config.cors_origins:
            parsed = urlsplit(origin)
            request_host = request.headers.get("host", "").lower()
            origin_host = (parsed.hostname or "").lower()
            same_local_origin = (
                parsed.scheme in {"http", "https"}
                and origin_host in {"127.0.0.1", "localhost", "::1"}
                and bool(parsed.netloc)
                and parsed.netloc.lower() == request_host
            )
            if not same_local_origin:
                raise ApiProblem("origin_rejected", "Request origin is not an allowed local frontend origin.", status_code=403)

    def get_repo() -> Repository:
        return repo

    def safe_namespace(value: str) -> str:
        return namespace_value(value)

    @application.get("/api/health")
    async def health():
        statuses = await registry.health()
        live = any((item.available if hasattr(item, "available") else item.get("available", False)) and (item.provider if hasattr(item, "provider") else item.get("provider")) == "codex" for item in statuses)
        return {"status": "ok", "version": application.version, "backend_time": utc_now(), "database": "ready", "live_available": live}

    @application.get("/api/office")
    async def office(namespace: str = Query("real"), run_id: str | None = Query(None)):
        namespace = safe_namespace(namespace)
        described = await registry.describe()
        codex = next((item for item in described["items"] if item["id"] == "codex"), None)
        portfolio = repo.portfolio(namespace)
        with repo.db.operation() as conn:
            account_count = len(portfolio["accounts"])
            position_count = len(portfolio["positions"])
            currencies = sorted({balance["currency"] for account in portfolio["accounts"] for balance in account["balances"]})
        if run_id:
            scoped = repo.run_snapshot(run_id, namespace)
            if not scoped:
                raise ApiProblem("not_found", "Run was not found.", status_code=404)
        return {"namespace": namespace, "run_id": run_id, "agents": repo.all_agents(namespace, run_id=run_id), "paused": repo.firm_paused(), "connection": {"status": "connected", "last_event_at": repo.last_event_at(namespace)}, "event_cursor": repo.event_cursor(namespace), "portfolio_summary": {"accounts": account_count, "positions": position_count, "currencies": currencies}, "provider_status": codex["status"] if codex else "unknown"}

    @application.get("/api/agents/{agent_id}")
    async def agent(agent_id: str, namespace: str = Query("real"), run_id: str | None = Query(None)):
        namespace = safe_namespace(namespace)
        if run_id and not repo.run_snapshot(run_id, namespace):
            raise ApiProblem("not_found", "Run was not found.", status_code=404)
        snapshot = repo.agent_snapshot(agent_id, namespace, run_id=run_id)
        if not snapshot:
            raise ApiProblem("not_found", "Agent was not found.", status_code=404)
        if agent_id == "A07":
            simulation_rows = simulations.list()
            snapshot["agent"]["related_simulations"] = simulation_rows
            snapshot["agent"]["current_simulation"] = next((item for item in simulation_rows if item.get("status") == "running"), None)
        return snapshot

    @application.post("/api/runs", status_code=202, dependencies=[Depends(mutation_guard)])
    async def create_run(body: RunCreate):
        try:
            # The public root-run boundary owns the active research contract.
            # A missing or explicit null marker from a client must not opt a
            # new API run out of the five-question review.  Direct repository
            # callers remain able to read and create historical legacy runs.
            body = body.model_copy(update={"research_contract": FIVE_QUESTION_CONTRACT})
            namespace = body.namespace
            repo.source_packet(namespace, body.source_ids)
            task_plan = build_research_tasks(body.question, body.horizon, body.ticker, namespace, lean=True)
            result, reused = repo.create_run(body, task_plan)
        except (ValueError, ValidationError) as exc:
            raise ApiProblem("run_rejected", str(exc))
        if not reused and not repo.firm_paused():
            engine.schedule(result["run_id"])
        return result

    @application.get("/api/runs")
    async def list_runs(namespace: str = Query("real"), include_intake: bool = Query(False), include_actions: bool = Query(False)):
        namespace = safe_namespace(namespace)
        items = repo.run_summaries(namespace, include_intake=include_intake)
        if not include_actions:
            items = [item for item in items if not str(item.get("followup_kind") or "").startswith("optional_research_")]
        for item in items:
            run_id = item.get("id") or item.get("run_id")
            item["current_decision"] = case_store.current(run_id, namespace)
            item["display_title"] = case_store.title(run_id)
        return {"items": items}

    @application.get("/api/case-decisions")
    async def case_decisions(namespace: str = Query("real")):
        return {"items": case_store.list(safe_namespace(namespace))}

    @application.get("/api/watchlist")
    @application.get("/api/lifecycle")
    async def watchlist(namespace: str = Query("real")):
        return case_store.watchlist(safe_namespace(namespace)) | {"enabled": watch_monitor.enabled()}

    @application.post("/api/lifecycle/{run_id}", dependencies=[Depends(mutation_guard)])
    async def record_lifecycle(run_id: str, body: LifecycleRequest):
        try:
            return case_store.record_lifecycle(run_id, body)
        except ValueError as exc:
            raise ApiProblem("lifecycle_rejected", str(exc))

    @application.get("/api/learning")
    async def learning(namespace: str = Query("real")):
        return learning_journal.list(safe_namespace(namespace))

    @application.post("/api/learning/{baseline_id}/observations", dependencies=[Depends(mutation_guard)])
    async def record_learning(baseline_id: str, body: OutcomeRequest):
        try:
            return learning_journal.record(baseline_id, body)
        except ValueError as exc:
            raise ApiProblem("outcome_rejected", str(exc))

    @application.post("/api/watchlist/check", dependencies=[Depends(mutation_guard)])
    async def check_watchlist(body: RedditItemRequest):
        return await watch_monitor.check_once(body.namespace)

    @application.get("/api/portfolio-policy")
    async def portfolio_policy(namespace: str = Query("real")):
        namespace = safe_namespace(namespace)
        with repo.db.operation() as conn:
            row = conn.execute("SELECT value_json FROM app_settings WHERE key=?", (f"portfolio_policy:{namespace}",)).fetchone()
        return {"namespace": namespace, "configured": bool(row), "policy": json.loads(row[0]) if row else PortfolioPolicy().model_dump()}

    @application.put("/api/portfolio-policy", dependencies=[Depends(mutation_guard)])
    async def save_portfolio_policy(body: PortfolioPolicyRequest):
        from decimal import Decimal, InvalidOperation

        policy = body.model_dump(exclude={"namespace"})
        try:
            for value in policy["limits"].values():
                initial, maximum = Decimal(value["initial_notional"]), Decimal(value["max_notional"])
                if not initial.is_finite() or not maximum.is_finite() or not 0 < initial <= maximum:
                    raise ValueError("Each initial amount must be positive and no larger than its maximum.")
                if value.get("planned_loss_limit") is not None:
                    loss = Decimal(value["planned_loss_limit"])
                    if not loss.is_finite() or not 0 < loss <= maximum:
                        raise ValueError("Planned loss limits must be positive and no larger than the position maximum.")
                value["currency"] = value["currency"].upper()
        except (InvalidOperation, ValueError) as exc:
            raise ApiProblem("invalid_portfolio_policy", str(exc) if isinstance(exc, ValueError) else "Position limits must be decimal amounts.")
        with repo.db.transaction(immediate=True) as conn:
            conn.execute("INSERT INTO app_settings(key,value_json,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at", (f"portfolio_policy:{body.namespace}", json.dumps(policy), utc_now()))
            repo._audit(conn, "portfolio_policy_changed", "portfolio_policy", body.namespace, policy)
        return {"namespace": body.namespace, "configured": True, "policy": policy}

    @application.get("/api/runs/{run_id}")
    async def get_run(run_id: str, namespace: str | None = Query(None), view: Literal["full", "reader"] = Query("full")):
        if namespace:
            namespace = safe_namespace(namespace)
        snapshot = repo.run_snapshot(run_id, namespace)
        if not snapshot:
            raise ApiProblem("not_found", "Run was not found.", status_code=404)
        snapshot["current_decision"] = case_store.current(run_id, namespace)
        snapshot["decision_history"] = case_store.history(run_id, namespace)
        snapshot["calculation_context"] = case_store.calculations(run_id, namespace)
        snapshot["display_title"] = case_store.title(run_id)
        return project_run_reader(snapshot) if view == "reader" else snapshot

    @application.get("/api/runs/{run_id}/memory")
    async def get_run_memory(run_id: str, namespace: str = Query("real")):
        namespace = safe_namespace(namespace)
        if not repo.run_snapshot(run_id, namespace):
            raise ApiProblem("not_found", "Run was not found.", status_code=404)
        return repo.run_memory(run_id, namespace=namespace)

    @application.get("/api/runs/{run_id}/gaps")
    async def get_run_gaps(run_id: str, namespace: str = Query("real"), include_resolved: bool = Query(True)):
        namespace = safe_namespace(namespace)
        if not repo.run_snapshot(run_id, namespace):
            raise ApiProblem("not_found", "Run was not found.", status_code=404)
        return {"run_id": run_id, "namespace": namespace, "items": repo.gaps_for_run(run_id, namespace=namespace, include_resolved=include_resolved)}

    @application.get("/api/runs/{run_id}/simulations")
    async def get_run_simulations(run_id: str, namespace: str = Query("real")):
        namespace = safe_namespace(namespace)
        if not repo.run_snapshot(run_id, namespace):
            raise ApiProblem("not_found", "Run was not found.", status_code=404)
        return {"run_id": run_id, "namespace": namespace, "items": repo.candidate_simulations_for_run(run_id, namespace=namespace)}

    @application.get("/api/inbox")
    @application.get("/api/reddit/inbox")
    async def inbox(
        namespace: str = Query("real"),
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
        status: str | None = Query(None),
        filter_status: str | None = Query(None, alias="filter"),
    ):
        namespace = safe_namespace(namespace)
        if namespace not in {"real", "demo"}:
            raise ApiProblem("invalid_namespace", "Reddit intake is available in real or demo namespaces only.")
        try:
            return reddit_monitor.inbox(namespace, limit=limit, offset=offset, status=status, status_filter=filter_status)
        except ValueError as exc:
            raise ApiProblem("invalid_reddit_filter", str(exc))

    @application.get("/api/reddit/connection")
    async def get_reddit_connection(namespace: str = Query("real")):
        try:
            return reddit_monitor.connection(namespace)
        except ValueError as exc:
            raise ApiProblem("invalid_namespace", str(exc))

    @application.put("/api/reddit/connection", dependencies=[Depends(mutation_guard)])
    async def save_reddit_connection(body: RedditConnectionRequest):
        try:
            return reddit_monitor.save(body.namespace, body.subreddit, enabled=body.enabled, window_days=body.window_days)
        except ValueError as exc:
            raise ApiProblem("invalid_reddit_settings", str(exc))

    def reddit_item(item_id: str, namespace: str) -> dict[str, Any]:
        with repo.db.operation() as conn:
            row = conn.execute("SELECT * FROM intake_items WHERE id=? AND namespace=? AND origin='reddit'", (item_id, namespace)).fetchone()
        if not row:
            raise ApiProblem("not_found", "Reddit item was not found.", status_code=404)
        return dict(row)

    @application.post("/api/reddit/inbox/{item_id}/dispatch", dependencies=[Depends(mutation_guard)])
    async def dispatch_reddit_item(item_id: str, body: RedditItemRequest):
        item = reddit_item(item_id, body.namespace)
        if item.get("run_id") and item["status"] != "queued":
            return {"item_id": item_id, "run_id": item["run_id"], "reused": True}
        result = reddit_monitor.dispatch(body.namespace, item_id=item_id)
        return {**result, "item_id": item_id, "run_id": next((x.get("run_id") for x in result.get("dispatched", []) if x.get("item_id") == item_id), None)}

    @application.post("/api/reddit/inbox/{item_id}/reuse", dependencies=[Depends(mutation_guard)])
    async def reuse_reddit_item(item_id: str, body: RedditItemRequest):
        item = reddit_item(item_id, body.namespace)
        if not item.get("run_id") or not repo.run_snapshot(item["run_id"], body.namespace):
            raise ApiProblem("no_existing_research", "This post has no saved research run to reuse.", status_code=409)
        return {"item_id": item_id, "run_id": item["run_id"], "reused": True}

    @application.post("/api/intake/reddit/poll", status_code=202, dependencies=[Depends(mutation_guard)])
    async def poll_reddit(body: RedditIntakeRequest):
        try:
            result = await reddit_monitor.poll(
                body.namespace,
                body.subreddit,
                max_posts=body.max_posts,
                max_pages=body.max_pages,
                max_dispatches=min(body.max_dispatches, int(getattr(app_config, "reddit_parallel_limit", 3) or 3)),
                force_backfill=body.force_backfill,
            )
        except ValueError as exc:
            raise ApiProblem("reddit_intake_rejected", str(exc), status_code=400)
        return result

    @application.post("/api/intake/reddit/post", status_code=202, dependencies=[Depends(mutation_guard)])
    async def intake_reddit_post(body: RedditPostRequest):
        from .research.connectors import RedditConnector

        connector = reddit_monitor.connector or RedditConnector(project_root=app_config.project_root, data_dir=app_config.data_dir)
        try:
            result = await asyncio.to_thread(connector.fetch_post, body.url, timeout=20)
        except ValueError as exc:
            raise ApiProblem("invalid_reddit_post", str(exc))
        if result.capability != "ready" or not result.posts:
            raise ApiProblem("reddit_post_unavailable", result.error or "The selected post is unavailable.", {"capability": result.capability}, status_code=502)
        post = result.posts[0]
        ingested = repo.ingest_reddit_result(result, namespace=body.namespace, subreddit=post.subreddit, phase="explicit_post")
        with repo.db.operation() as conn:
            item = conn.execute("SELECT id,run_id,status FROM intake_items WHERE namespace=? AND origin='reddit' AND external_id=?", (body.namespace, post.post_id)).fetchone()
        if not item:
            raise ApiProblem("reddit_post_not_retained", "The selected post could not be retained.", status_code=500)
        dispatched = reddit_monitor.dispatch(body.namespace, item_id=item["id"]) if body.dispatch else {}
        with repo.db.operation() as conn:
            current = conn.execute("SELECT id,run_id,status FROM intake_items WHERE id=?", (item["id"],)).fetchone()
        return {"item_id": current["id"], "run_id": current["run_id"], "status": current["status"], "title": post.title, "capability": result.capability, "ingest": ingested, "dispatch": dispatched}

    @application.get("/api/candidate-simulations/{simulation_id}/replay")
    async def replay_candidate_simulation(simulation_id: str, namespace: str = Query("real")):
        namespace = safe_namespace(namespace)
        if namespace not in {"real", "demo"}:
            raise ApiProblem("invalid_namespace", "Candidate simulations are available in real or demo namespaces only.")
        result = repo.replay_candidate_simulation(simulation_id, namespace=namespace)
        if result is None:
            raise ApiProblem("not_found", "Candidate simulation was not found.", status_code=404)
        return result

    @application.post("/api/runs/{run_id}/messages", dependencies=[Depends(mutation_guard)])
    async def message(run_id: str, body: RunMessage):
        try:
            result = repo.add_followup(run_id, body.message, body.idempotency_key, output_id=body.output_id)
        except ValueError as exc:
            raise ApiProblem("message_rejected", str(exc), status_code=404 if "unknown" in str(exc) else 400)
        if not result.get("reused") and not repo.firm_paused():
            engine.schedule(run_id)
        return {"run_id": run_id, "event_id": result["event_id"], "task_id": result.get("task_id"), "reused": bool(result.get("reused"))}

    @application.post("/api/runs/{run_id}/research", status_code=202, dependencies=[Depends(mutation_guard)])
    async def research(run_id: str, body: ResearchRequest):
        try:
            result, reused = repo.create_evidence_research(run_id, body)
        except ValueError as exc:
            raise ApiProblem("research_rejected", str(exc), status_code=404 if "unknown" in str(exc) else 400)
        child_run_id = result["run_id"]
        if not reused and not repo.firm_paused():
            engine.schedule(child_run_id)
        return result | {"parent_run_id": run_id}

    @application.post("/api/control", dependencies=[Depends(mutation_guard)])
    async def control(body: ControlRequest):
        # Capture durable run ids before a firm resume changes their state to
        # queued.  This also lets task-level resume wake the owning run.
        resume_run_ids: list[str] = []
        retry_run_ids: list[str] = []
        if body.action == "resume":
            if body.scope == "firm":
                resume_run_ids = [
                    row["id"]
                    for row in repo.runs(namespace=None)
                    if row["mode"] == "research"
                    and not row["cancel_requested"]
                    and (
                        row["status"] == "paused"
                        or (row["status"] == "queued" and not row["pause_requested"])
                    )
                ]
            elif body.scope == "run" and body.id:
                resume_run_ids = [body.id]
            elif body.scope == "task" and body.id:
                task_row = repo.task(body.id)
                if task_row:
                    resume_run_ids = [task_row["run_id"]]
        if body.action == "retry":
            if body.scope == "run" and body.id:
                retry_run_ids = [body.id]
            elif body.scope == "task" and body.id:
                task_row = repo.task(body.id)
                if task_row:
                    retry_run_ids = [task_row["run_id"]]
        try:
            affected = repo.control(body.scope, body.id, body.action)
        except ValueError as exc:
            raise ApiProblem("control_rejected", str(exc))
        if body.action == "cancel":
            await engine.cancel_active(
                None if body.scope in {"firm", "task"} else body.id,
                body.id if body.scope == "task" else None,
            )
        elif body.action == "resume":
            for run_id in resume_run_ids:
                state = repo.run_record(run_id)
                if state and state["status"] in {"queued", "paused", "running"} and not state["cancel_requested"]:
                    engine.schedule(run_id)
        elif body.action == "run_once" and body.id:
            # This authorization belongs only to the selected case. It never
            # clears firm pause or wakes other queued research or monitors.
            engine.schedule(body.id)
        elif body.action == "retry":
            for run_id in retry_run_ids:
                state = repo.run_record(run_id)
                if state and state["status"] == "queued" and not repo.firm_paused():
                    engine.schedule(run_id)
        return {"ok": True, "affected": affected}

    @application.post("/api/tasks/{task_id}/cancel", dependencies=[Depends(mutation_guard)])
    async def cancel_task(task_id: str):
        result = await control(ControlRequest(scope="task", id=task_id, action="cancel"))
        return result

    @application.get("/api/events")
    async def events(request: Request, namespace: str = Query("real"), after: int = Query(0, ge=0), run_id: str | None = Query(None)):
        namespace = safe_namespace(namespace)
        header_cursor = request.headers.get("last-event-id")
        if header_cursor and header_cursor.isdigit():
            after = max(after, int(header_cursor))

        async def stream() -> AsyncIterator[str]:
            cursor = after
            while True:
                if await request.is_disconnected():
                    break
                batch = repo.events(namespace, cursor, run_id, app_config.max_event_replay)
                if batch:
                    for event in batch:
                        cursor = max(cursor, event["sequence_id"])
                        yield f"id: {event['sequence_id']}\nevent: task_event\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                else:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @application.get("/api/outputs/{output_id}")
    async def output(output_id: str, namespace: str | None = Query(None)):
        if namespace:
            namespace = safe_namespace(namespace)
        result = repo.output_with_sources(output_id, namespace)
        if not result:
            raise ApiProblem("not_found", "Output was not found.", status_code=404)
        return result

    @application.get("/api/providers")
    async def providers():
        return await registry.describe()

    @application.get("/api/model-policy")
    async def model_policy():
        return repo.get_policy()

    @application.put("/api/model-policy", dependencies=[Depends(mutation_guard)])
    async def update_model_policy(body: ModelPolicyRequest):
        if body.config:
            preflight = await registry.preflight(body.config, execute=False)
            if not preflight.get("available"):
                raise ApiProblem("unsupported_model", str(preflight.get("reason") or "Selected model is unavailable."), {"status": preflight.get("status")})
        try:
            return repo.set_policy("role" if body.scope == "role" else body.scope, body.agent_id, body.config, body.profile)
        except ValueError as exc:
            raise ApiProblem("policy_rejected", str(exc))

    @application.post("/api/providers/{provider_id}/preflight", dependencies=[Depends(mutation_guard)])
    async def provider_preflight(provider_id: str, body: ProviderPreflightRequest):
        if provider_id not in {"codex", "ollama"}:
            raise ApiProblem("unsupported_provider", "Provider is not enabled.", status_code=404)
        current, _ = repo.resolve_model("A00")
        config = ModelConfig(provider=provider_id, model=body.model or current.model, reasoning_effort=body.reasoning_effort if body.reasoning_effort is not None else current.reasoning_effort, profile=current.profile)
        if body.execute:
            slot_factory = getattr(registry, "generation_slot", None)
            if callable(slot_factory):
                async with slot_factory(provider_id):
                    return await registry.preflight(config, True)
            # Keep injected test/demonstration registries compatible while
            # the production registry remains the shared concurrency gate.
            return await registry.preflight(config, True)
        return await registry.preflight(config, False)

    @application.get("/api/portfolio")
    async def portfolio(namespace: str = Query("real")):
        namespace = safe_namespace(namespace)
        return repo.portfolio(namespace)

    @application.put("/api/risk-settings", dependencies=[Depends(mutation_guard)])
    async def risk_settings(body: RiskSettingsRequest):
        try:
            return repo.set_risk_settings(body)
        except ValueError as exc:
            raise ApiProblem("risk_settings_rejected", str(exc))

    @application.post("/api/imports", dependencies=[Depends(mutation_guard)])
    async def imports(body: ImportRequest):
        try:
            result = repo.import_evidence(body)
        except ValueError as exc:
            raise ApiProblem("import_rejected", str(exc))
        # Amendments create immutable derived refresh runs.  Queue them at
        # the boundary where the import is committed so they cannot remain
        # durably queued forever.  A firm pause preserves the queued state;
        # resume will dispatch it through the normal control path.
        refresh_run_ids = list(result.get("refresh_run_ids") or []) if isinstance(result, dict) else []
        if refresh_run_ids and not repo.firm_paused():
            for refresh_run_id in refresh_run_ids:
                engine.schedule(refresh_run_id)
        return result

    @application.get("/api/sources")
    async def sources(namespace: str = Query("real")):
        namespace = safe_namespace(namespace)
        return {"items": repo.sources(namespace)}

    @application.get("/api/sources/{source_id}")
    async def source(source_id: str, namespace: str | None = Query(None)):
        if namespace:
            namespace = safe_namespace(namespace)
        items = repo.sources(namespace, source_id)
        if not items:
            raise ApiProblem("not_found", "Source was not found.", status_code=404)
        return items[0] | {"document":document_record(repo,items[0]["namespace"],source_id)}

    @application.get("/api/sources/{source_id}/document")
    async def source_document(source_id: str, namespace: str = Query("real")):
        record = document_record(repo,safe_namespace(namespace),source_id,include_bytes=True)
        if not record:
            raise ApiProblem("not_found", "Retained PDF was not found in this namespace.", status_code=404)
        return Response(content=record["bytes"],media_type="application/pdf",headers={"Content-Disposition":'inline; filename="research-source.pdf"',"Content-Security-Policy":"sandbox; default-src 'none'","X-Content-Type-Options":"nosniff"})

    @application.get("/api/sources/{source_id}/versions")
    async def source_versions(source_id: str, namespace: str | None = Query(None)):
        if namespace:
            namespace = safe_namespace(namespace)
        versions = repo.source_versions(source_id, namespace)
        if not versions:
            raise ApiProblem("not_found", "Source was not found.", status_code=404)
        return {"items": versions}

    @application.post("/api/sources/sec", dependencies=[Depends(mutation_guard)])
    async def sec_sources(body: SecRequest):
        try:
            return await sec.fetch(body.namespace, body.cik, body.form)
        except ValueError as exc:
            raise ApiProblem("sec_request_rejected", str(exc))

    @application.get("/api/coverage")
    async def coverage(namespace: str = Query("real")):
        namespace = safe_namespace(namespace)
        return repo.coverage(namespace)

    @application.put("/api/coverage/{symbol}", dependencies=[Depends(mutation_guard)])
    async def update_coverage(symbol: str, body: CoverageRequest):
        try:
            return repo.set_coverage(symbol, body)
        except ValueError as exc:
            raise ApiProblem("coverage_rejected", str(exc))

    @application.get("/api/memory/search")
    async def memory_search(namespace: str = Query("real"), q: str = Query(""), kind: str = Query("all")):
        namespace = safe_namespace(namespace)
        if kind not in {"all", "fact", "research", "decision", "source", "simulation"}:
            raise ApiProblem("invalid_kind", "kind must be all, fact, research, decision, source or simulation")
        # Search namespace is always explicit. A real query cannot include the
        # simulation namespace by accidental broad search.
        return {"items": repo.search(namespace, q, kind)}

    @application.get("/api/decisions")
    async def decisions(namespace: str = Query("real")):
        namespace = safe_namespace(namespace)
        return {"items": repo.decisions(namespace)}

    @application.get("/api/export")
    async def export(namespace: str = Query("real"), format: str = Query("json")):
        namespace = safe_namespace(namespace)
        archive = repo.export(namespace)
        if format == "json":
            return Response(content=json.dumps(archive, ensure_ascii=False, indent=2), media_type="application/json", headers={"Content-Disposition": f'attachment; filename="road2m-{namespace}-export.json"'})
        if format == "markdown":
            records = archive["records"]
            lines = [f"# ResearchCouncil export ({namespace})", "", f"Exported at: {archive['exported_at']}", ""]
            for kind, rows in records.items():
                lines.extend([f"## {kind}", "", f"Records: {len(rows)}", ""])
                for row in rows[:100]:
                    title = row.get("title") or row.get("label") or row.get("id")
                    lines.append(f"- **{title}** (`{row.get('id','')}`)")
                lines.append("")
            return Response(content="\n".join(lines), media_type="text/markdown", headers={"Content-Disposition": f'attachment; filename="road2m-{namespace}-export.md"'})
        raise ApiProblem("invalid_format", "format must be json or markdown")

    @application.get("/api/monitoring")
    async def monitoring(namespace: str = Query("real")):
        namespace = safe_namespace(namespace)
        return {"items": repo.monitoring(namespace), "process_required": True}

    @application.post("/api/monitoring", dependencies=[Depends(mutation_guard)])
    async def add_monitoring(body: MonitoringRequest):
        try:
            if body.mode == "source_change" and not body.source_ids:
                raise ValueError("source_change monitoring requires at least one source ID")
            repo.source_packet(body.namespace, body.source_ids)
            return repo.add_monitoring(body)
        except ValueError as exc:
            raise ApiProblem("monitoring_rejected", str(exc))

    @application.put("/api/monitoring/{schedule_id}", dependencies=[Depends(mutation_guard)])
    async def update_monitoring(schedule_id: str, body: MonitoringUpdate, namespace: str | None = Query(None)):
        try:
            if namespace:
                namespace = safe_namespace(namespace)
            return repo.update_monitoring(schedule_id, body.enabled, namespace=namespace)
        except ValueError as exc:
            raise ApiProblem("monitoring_rejected", str(exc), status_code=404 if "unknown" in str(exc) else 400)

    @application.post("/api/simulations", status_code=202, dependencies=[Depends(mutation_guard)])
    async def create_simulation(body: SimulationCreate):
        try:
            # Real evidence may be cited as context; the resulting records are
            # always written in the simulation namespace.
            for source_id in body.source_ids:
                if not repo.sources(None, source_id):
                    raise ValueError(f"unknown source ID: {source_id}")
            result = simulations.create(body)
        except ValueError as exc:
            raise ApiProblem("simulation_rejected", str(exc))
        if not result.get("reused"):
            simulation_id = result["simulation_id"]
            task = asyncio.create_task(simulations.run(simulation_id), name=f"road2m-simulation-{simulation_id}")
            simulation_tasks[simulation_id] = task
            task.add_done_callback(lambda completed, key=simulation_id: simulation_tasks.pop(key, None))
        return {"simulation_id": result["simulation_id"], "status": result["status"]}

    @application.get("/api/simulations")
    async def list_simulations():
        return {"items": simulations.list()}

    @application.get("/api/simulations/{simulation_id}")
    async def get_simulation(simulation_id: str):
        result = simulations.get(simulation_id)
        if result is None:
            raise ApiProblem("not_found", "Simulation was not found.", status_code=404)
        return result

    @application.post("/api/simulations/{simulation_id}/replay", dependencies=[Depends(mutation_guard)])
    async def replay_simulation(simulation_id: str):
        try:
            result = simulations.replay(simulation_id)
        except ValueError as exc:
            raise ApiProblem("simulation_incomplete", str(exc), status_code=409)
        if result is None:
            raise ApiProblem("not_found", "Simulation was not found.", status_code=404)
        return result

    @application.post("/api/backup", dependencies=[Depends(mutation_guard)])
    async def backup():
        destination = app_config.backup_dir / f"road2m-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.sqlite3"
        repo.db.backup_to(destination)
        return {"path": str(destination), "created_at": utc_now(), "validated": True}

    @application.post("/api/restore", dependencies=[Depends(mutation_guard)])
    async def restore(payload: dict[str, Any]):
        # A live SQLite connection cannot be safely replaced through an HTTP
        # request: another worker or the lifespan scheduler may still hold a
        # connection between validation and the backup API call.  The
        # repository's stopped-service restore script performs integrity,
        # schema and foreign-key checks before swapping files.  Keep this
        # endpoint explicit so a malformed archive can never replace the
        # active database and falsely report success.
        raise ApiProblem(
            "restore_requires_stopped_service",
            "Stop the backend and restore through scripts/restore.sh --confirm-stopped, then restart the service.",
            status_code=409,
        )

    application.include_router(create_library_router(app_config.evidence_dir, repo=repo))
    application.include_router(create_labs_router(app_config, mutation_guard))
    application.include_router(create_document_router(app_config, mutation_guard, repo=repo, briefs=research_workflows.transcript_briefs))
    application.include_router(create_workflow_router(research_workflows, mutation_guard))
    application.include_router(create_research_actions_router(repo, engine, mutation_guard))
    application.include_router(create_memory_router(repo, mutation_guard))
    application.include_router(create_market_news_router())
    application.include_router(create_valuation_history_router(repo, registry, app_config, mutation_guard))

    # Serve a production frontend when root's build exists, while preserving
    # API routing and the local-only boundary. StaticFiles is optional here so
    # backend tests remain independent of frontend installation.
    dist = app_config.project_root / "frontend" / "dist"
    if dist.is_dir():
        from fastapi.staticfiles import StaticFiles

        class LocalFrontend(StaticFiles):
            async def get_response(self, path, scope):
                response = await super().get_response(path, scope)
                # The shell names hashed lazy chunks, which change on a local
                # rebuild. Revalidate it instead of retaining a stale module map.
                if path in {".", "", "index.html"} or str(response.headers.get("content-type", "")).startswith("text/html"):
                    response.headers["Cache-Control"] = "no-cache"
                return response

        application.mount("/", LocalFrontend(directory=dist, html=True), name="frontend")

    return application


app = create_app()
