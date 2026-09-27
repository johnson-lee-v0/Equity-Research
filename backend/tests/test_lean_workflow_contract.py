"""Focused contracts for the default lean research path."""
from __future__ import annotations

import json
import asyncio
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

import backend.app.orchestration.workflow as workflow_module

from backend.app.agents.roles import ROLE_BY_ID, role_prompt
from backend.app.config import Settings
from backend.app.memory.repository import Repository
from backend.app.orchestration.provider_context import prepare_provider_context
from backend.app.orchestration.workflow import (
    Orchestrator,
    _reddit_research_stage_question,
    build_research_tasks,
)
from backend.app.research.source_context import number_source_lines
from backend.app.research.connectors import MarketBar, MarketBarsResult
from backend.app.providers.base import ProviderResult
from backend.app.research.discovery import FetchedSource
from backend.app.schemas import AgentOutputPayload, CandidateDecisionBrief, FactClaim, ImportRequest, MissingGap, ModelConfig, RedditTriage, ResearchCandidate, RoutingPlan, RunCreate


ROOT = Path(__file__).resolve().parents[2]


def make_repository(tmp_path: Path) -> Repository:
    return Repository(config=Settings(data_dir=tmp_path, project_root=ROOT, codex_timeout_seconds=30))


def test_lean_route_is_four_stages_with_researcher_luna_defaults(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    body = RunCreate(
        question="Research ALFA with a bounded public evidence packet.",
        namespace="real",
        horizon="3m",
        idempotency_key="lean-four-stage-contract",
    )
    created, reused = repository.create_run(
        body,
        build_research_tasks(body.question, body.horizon, body.ticker, body.namespace, lean=True),
        allow_semantic_reuse=False,
    )
    assert reused is False
    routing = created["tasks"][0]
    a00_config, _ = repository.resolve_model("A00", lean=True)
    attempt = repository.create_attempt(routing["id"], a00_config, {})
    route = RoutingPlan(
        intent="research",
        horizon="3m",
        tickers=["ALFA"],
        selected_analysts=["A03"],
        research_queries=["Find a dated primary issuer filing."],
        rationale="The question needs bounded evidence.",
    )
    committed = repository.commit_output(
        routing["id"], attempt["attempt_id"],
        AgentOutputPayload(
            status="completed",
            title="Lean route",
            summary="A bounded research route.",
            analysis="The route is retained as structured planning context.",
            routing_plan=route,
        ),
        "real",
        a00_config,
    )
    expanded = repository.consume_routing_plan(created["run_id"], route, routing_output_id=committed["id"])

    assert expanded["reused"] is False
    assert [task["kind"] for task in repository.tasks_for_run(created["run_id"])] == [
        "routing", "universe_discovery", "research_synthesis", "cio_review",
    ]
    assert all(task["agent_id"] != "A10" for task in repository.tasks_for_run(created["run_id"]))
    snapshot = repository.run_snapshot(created["run_id"])
    assert snapshot["workflow_variant"] == "lean"
    assert snapshot["workflow_version"] == "lean-research-v1"
    for agent_id in ("A01", "A03"):
        config, _ = repository.resolve_model(agent_id, lean=True)
        assert config.model == ("gpt-6-luna" if agent_id == "A01" else "gpt-6-sol")
        assert config.reasoning_effort == "high"
    cio, _ = repository.resolve_model("A11", lean=True)
    assert cio.model == "gpt-6-astra"
    assert cio.reasoning_effort == "medium"


def test_market_source_compaction_keeps_current_original_locator(tmp_path: Path) -> None:
    del tmp_path  # This pure source-packet contract needs no repository.
    header = {
        "provider": "alpaca",
        "source_type": "market_bars",
        "metadata": {"timeframe": "1Day", "symbols": ["TEST"], "currency": "USD", "oversized": "x" * 30_000},
    }
    rows = [
        json.dumps({"symbol": "TEST", "timestamp": f"2026-01-{(index % 28) + 1:02d}T00:00:00Z", "close": str(index), "payload": "y" * 100})
        for index in range(3, 277)
    ]
    source = "\n".join(["Road2M canonical research source", json.dumps(header), *rows])

    compacted = number_source_lines(source)

    assert len(compacted) <= 24_000
    assert "L276:" in compacted
    assert "L3:" not in compacted
    assert "oversized" not in compacted
    assert "omitted original lines L3-" in compacted


def test_dated_numeric_table_compaction_keeps_newest_treasury_rows_and_notes() -> None:
    first_date = date(2003, 1, 6)
    latest_date = date(2026, 9, 11)
    span = (latest_date - first_date).days
    rows = []
    for row_number in range(175):
        observation_date = first_date + timedelta(days=(span * row_number) // 174)
        values = " ".join(f"{4.0 + column / 100:.2f}" for column in range(12))
        rows.append(f"{observation_date:%m/%d/%Y} {values}")

    # The line numbers mirror the long Treasury page captured in the live
    # run: the table starts at L339/L345 and its latest observation is L520.
    lines = [
        "Daily Treasury Rates | U.S. Department of the Treasury",
        *[f"navigation item {number}" for number in range(2, 339)],
        "Daily Treasury Real Yield Curve Rates",
        "Table published by the Treasury Department",
        "The rates below are daily observations.",
        "Select a year to view the historical series.",
        "Values are expressed as percentages.",
        "Download the complete table as CSV.",
        "Date 5 YR 7 YR 10 YR 20 YR 30 YR",
        *rows,
        "Notes: observations are reported for business dates and are not forecasts.",
        "Current publication note: the latest row above is the current archived observation.",
        "Footer navigation follows.",
    ]
    assert len(lines) == 523
    source = "\n".join(lines)
    source_hash = sha256(source.encode()).hexdigest()

    compacted = number_source_lines(source, max_chars=12_000)

    assert len(compacted) <= 12_000
    assert "L1: Daily Treasury Rates" in compacted
    assert "L339: Daily Treasury Real Yield Curve Rates" in compacted
    assert "L345: Date 5 YR 7 YR 10 YR 20 YR 30 YR" in compacted
    assert "L520: 09/11/2026" in compacted
    assert "L346:" not in compacted
    assert "L521: Notes: observations are reported" in compacted
    assert sha256(source.encode()).hexdigest() == source_hash
    assert source == "\n".join(lines)


def test_dated_numeric_table_compaction_supports_descending_rows() -> None:
    latest_date = date(2026, 9, 11)
    rows = []
    for row_number in range(48):
        observation_date = latest_date - timedelta(days=row_number * 7)
        rows.append(f"{observation_date:%Y-%m-%d} 1.10 2.20 3.30 4.40 5.50 6.60 7.70 8.80")
    lines = [
        "Issuer daily rate archive",
        "Navigation and explanatory material",
        "Historical rate table",
        "Date 1 2 3 4 5 6 7 8",
        *rows,
        "Notes: values are ordered newest first.",
    ]
    source = "\n".join(lines)

    compacted = number_source_lines(source, max_chars=2_400)

    assert len(compacted) <= 2_400
    assert "L1: Issuer daily rate archive" in compacted
    assert "L3: Historical rate table" in compacted
    assert "L4: Date 1 2 3 4 5 6 7 8" in compacted
    assert "L5: 2026-09-11" in compacted
    assert "L52: 2025-10-03" not in compacted
    assert "L53: Notes: values are ordered newest first." in compacted
    row_locators = [
        int(line[1:line.index(":")])
        for line in compacted.splitlines()
        if line.startswith("L") and ": " in line and line[1:line.index(":")].isdigit()
    ]
    selected_row_locators = [locator for locator in row_locators if locator >= 5 and locator <= 52]
    assert selected_row_locators == sorted(selected_row_locators)


def test_long_dated_prose_keeps_the_existing_non_table_excerpt() -> None:
    lines = [
        "Issuer research page",
        *[f"The dated release on 2026-09-{number:02d} changed guidance by {number} points." for number in range(1, 10)],
        *[f"Narrative paragraph {number} with no tabular observation." for number in range(10, 160)],
    ]
    source = "\n".join(lines)

    compacted = number_source_lines(source, max_chars=1_500)

    assert len(compacted) <= 1_500
    assert "L1: Issuer research page" in compacted
    assert "L2: The dated release" in compacted
    assert "L159:" not in compacted
    assert "outside the retained table excerpt" not in compacted


def test_actual_alpaca_source_binds_price_to_completed_close_row() -> None:
    timestamp = "2026-09-12T00:00:00Z"
    result = MarketBarsResult(
        provider="alpaca",
        status="ok",
        capability="ready",
        bars=(MarketBar(
            symbol="TEST",
            timestamp=timestamp,
            open="99",
            high="101",
            low="98",
            close="100",
            volume="500",
            complete=True,
        ),),
        metadata={"timeframe": "1Day", "symbols": ["TEST"], "currency": "USD", "retrieved_at": timestamp},
    )
    source = result.canonical_source_text()
    payload = AgentOutputPayload(
        status="completed",
        title="CIO",
        summary="TEST price",
        analysis="The archived market row supplies the dated close.",
        fact_claims=[FactClaim(claim="TEST close", value="100", unit="USD/share", period="2026-09-12", source_ref="alpaca", locator="L3")],
        candidate_briefs=[CandidateDecisionBrief(
            ticker="TEST",
            target_price="100",
            target_price_currency="USD",
            target_price_as_of="2026-09-12",
            target_price_source_refs=["alpaca"],
            target_price_basis="fundamental valuation using the dated close observation",
        )],
    )

    normalized = Repository._normalize_decision_brief(
        payload,
        {"alpaca"},
        source_content={"alpaca": source},
        source_metadata={"alpaca": {"source_type": "market_bars", "url": "https://data.alpaca.markets/v2/stocks/bars"}},
        run_ticker="TEST",
    )

    assert normalized.candidate_briefs[0].target_price == "100.00000000"
    assert normalized.candidate_briefs[0].target_price_source_refs == ["alpaca"]


def test_lean_source_amendment_queues_targeted_tasks_in_same_case(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    initial = repository.import_evidence(ImportRequest(
        namespace="real",
        kind="evidence",
        title="TEST issuer page",
        content="TEST issuer fact, version one.",
        source_url="https://issuer.example/test",
        idempotency_key="lean-source-initial",
    ))
    prior_source_id = initial["source_id"]
    body = RunCreate(
        question="Research TEST using the retained issuer page.",
        namespace="real",
        ticker="TEST",
        horizon="3m",
        source_ids=[prior_source_id],
        idempotency_key="lean-source-refresh-run",
    )
    created, _ = repository.create_run(
        body,
        build_research_tasks(body.question, body.horizon, body.ticker, body.namespace, lean=True),
        allow_semantic_reuse=False,
    )
    routing = created["tasks"][0]
    config, _ = repository.resolve_model("A00", lean=True)
    route = RoutingPlan(
        intent="research",
        horizon="3m",
        tickers=["TEST"],
        selected_analysts=["A03"],
        research_queries=["Find the issuer page."],
        rationale="A bounded source-backed review is required.",
    )
    attempt = repository.create_attempt(routing["id"], config, {prior_source_id: {"version": 1}})
    output = repository.commit_output(
        routing["id"], attempt["attempt_id"],
        AgentOutputPayload(status="completed", title="Route", summary="Route", analysis="Route", routing_plan=route),
        "real", config,
    )
    repository.consume_routing_plan(created["run_id"], route, routing_output_id=output["id"])

    amended = repository.import_evidence(ImportRequest(
        namespace="real",
        kind="evidence",
        title="TEST issuer page amended",
        content="TEST issuer fact, version two.",
        source_url="https://issuer.example/test",
        supersedes_id=prior_source_id,
        idempotency_key="lean-source-amendment",
    ))

    assert amended["refresh_run_ids"] == [created["run_id"]]
    tasks = repository.tasks_for_run(created["run_id"])
    assert all(task["run_id"] == created["run_id"] for task in tasks)
    assert any(task["kind"].startswith("research_synthesis_source_refresh_") for task in tasks)
    assert any(task["kind"].startswith("cio_review_source_refresh_") for task in tasks)
    assert not any(task["kind"] == "routing" and task["sequence_no"] > 0 for task in tasks)
    snapshot = repository.run_snapshot(created["run_id"])
    assert amended["source_id"] in snapshot["source_ids"]
    assert prior_source_id not in snapshot["source_ids"]
    with repository.db.operation() as conn:
        run_count = conn.execute("SELECT COUNT(*) FROM runs WHERE idempotency_key LIKE 'refresh:%'").fetchone()[0]
        invalidated = conn.execute("SELECT COUNT(*) FROM invalidations WHERE run_id=? AND source_id=?", (created["run_id"], amended["source_id"])).fetchone()[0]
    assert run_count == 0
    assert invalidated >= 1


def test_attempt_decision_inputs_remain_frozen_after_run_snapshot_changes(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    body = RunCreate(
        question="Research TEST with a frozen local portfolio context.",
        namespace="real",
        ticker="TEST",
        horizon="3m",
        idempotency_key="attempt-decision-inputs-frozen",
    )
    created, _ = repository.create_run(
        body,
        build_research_tasks(body.question, body.horizon, body.ticker, body.namespace, lean=True),
        allow_semantic_reuse=False,
    )
    task = created["tasks"][0]
    config, _ = repository.resolve_model("A00", lean=True)
    attempt = repository.create_attempt(task["id"], config, {})
    frozen = {
        "portfolio_snapshot": {"accounts": [{"id": "acct-old", "observed_at": "2026-09-12T00:00:00Z"}]},
        "account_snapshot_id": "snap-old",
        "portfolio_snapshot_captured_at": "2026-09-12T00:00:00Z",
        "portfolio_snapshot_as_of": "2026-09-11T00:00:00Z",
        "deterministic_market": {"code_version": "technical-indicators.v1", "candidates": [{"ticker": "TEST"}]},
        "prior_output_ids": ["out-old"],
        "prior_fact_ids": ["fact-old"],
        "prompt": "must not be persisted",
    }
    stored = repository.record_attempt_decision_inputs(attempt["attempt_id"], frozen)

    with repository.db.transaction(immediate=True) as conn:
        row = conn.execute("SELECT input_snapshot_json FROM runs WHERE id=?", (created["run_id"],)).fetchone()
        snapshot = json.loads(row[0])
        snapshot["portfolio_snapshot"] = {"accounts": [{"id": "acct-new", "observed_at": "2026-09-13T00:00:00Z"}]}
        snapshot["account_snapshot_id"] = "snap-new"
        conn.execute("UPDATE runs SET input_snapshot_json=? WHERE id=?", (json.dumps(snapshot), created["run_id"]))

    expected = {key: frozen[key] for key in (
        "portfolio_snapshot", "account_snapshot_id", "portfolio_snapshot_captured_at",
        "portfolio_snapshot_as_of", "deterministic_market", "prior_output_ids", "prior_fact_ids",
    )}
    assert stored == expected
    assert repository.attempt_decision_inputs(attempt["attempt_id"]) == expected
    assert repository.record_attempt_decision_inputs(
        attempt["attempt_id"],
        {**expected, "account_snapshot_id": "snap-overwrite-attempt"},
    ) == expected


def test_name_only_yolo_requires_typed_issuer_name_bound_to_post() -> None:
    post = {
        "title": "Bloom Energy - YOLO SHORT",
        "body": "",
        "source_flair": "YOLO",
    }
    source_content = json.dumps({"post": post})
    accepted = Repository._normalized_reddit_route(
        RoutingPlan(
            intent="research",
            tickers=[],
            selected_analysts=["A03"],
            reddit_triage=RedditTriage(
                classification="yolo_ticker",
                reason="The retained title names an issuer for bounded research.",
                evidence_excerpt=post["title"],
                issuer_name="Bloom Energy",
                tickers=[],
            ),
        ),
        question="Research this retained Reddit lead.",
        horizon="event",
        source_content=source_content,
        source_post=post,
    )
    assert accepted["reddit_triage"]["classification"] == "yolo_ticker"
    assert accepted["reddit_triage"]["issuer_name"] == "Bloom Energy"
    assert accepted["tickers"] == []
    assert "Bloom Energy - YOLO SHORT" in accepted["research_queries"][0]

    ambiguous = Repository._normalized_reddit_route(
        RoutingPlan(
            intent="research",
            tickers=[],
            reddit_triage=RedditTriage(
                classification="yolo_ticker",
                reason="The post describes a YOLO trade.",
                evidence_excerpt="YOLO",
                tickers=[],
            ),
        ),
        question="Research this retained Reddit lead.",
        horizon="event",
        source_content=json.dumps({"post": {"title": "YOLO all in", "body": "I am all in tomorrow.", "source_flair": "YOLO"}}),
        source_post={"title": "YOLO all in", "body": "I am all in tomorrow.", "source_flair": "YOLO"},
    )
    assert ambiguous["reddit_triage"]["classification"] == "skip"


def test_lean_continuation_stays_in_case_and_uses_no_repair_child(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    body = RunCreate(
        question="Research ALFA and identify a material public filing gap.",
        namespace="real",
        horizon="3m",
        idempotency_key="lean-continuation-contract",
    )
    created, _ = repository.create_run(
        body,
        build_research_tasks(body.question, body.horizon, body.ticker, body.namespace, lean=True),
        allow_semantic_reuse=False,
    )
    routing = created["tasks"][0]
    a00_config, _ = repository.resolve_model("A00", lean=True)
    route = RoutingPlan(
        intent="research", horizon="3m", tickers=["ALFA"], selected_analysts=["A03"],
        research_queries=["Find the latest public filing."], rationale="Bounded public research.",
    )
    route_attempt = repository.create_attempt(routing["id"], a00_config, {})
    route_output = repository.commit_output(
        routing["id"], route_attempt["attempt_id"],
        AgentOutputPayload(status="completed", title="Route", summary="Route", analysis="Route", routing_plan=route),
        "real", a00_config,
    )
    repository.consume_routing_plan(created["run_id"], route, routing_output_id=route_output["id"])

    synthesis = next(task for task in repository.tasks_for_run(created["run_id"])
                     if task["kind"] == "research_synthesis")
    researcher_config, _ = repository.resolve_model("A03", lean=True)
    attempt = repository.create_attempt(synthesis["id"], researcher_config, {})
    result = repository.commit_output(
        synthesis["id"], attempt["attempt_id"],
        AgentOutputPayload(
            status="needs_review",
            title="Research gap",
            summary="A material public fact remains unresolved.",
            analysis="The current packet cannot establish the latest debt covenant.",
            missing_gaps=[MissingGap(
                key="latest_debt_covenant",
                description="Latest public debt covenant in the issuer filing.",
                relevant_role="A03",
                reopen_when="A dated issuer filing states the covenant.",
            )],
        ),
        "real", researcher_config,
    )

    assert result["repair_run_ids"] == []
    assert len(result["continuation_task_ids"]) == 3
    assert [task["kind"] for task in repository.tasks_for_run(created["run_id"])] == [
        "routing", "universe_discovery", "research_synthesis",
        "universe_discovery_continuation_1", "research_synthesis_continuation_1", "cio_review",
    ]
    tasks = repository.tasks_for_run(created["run_id"])
    cio = next(task for task in tasks if task["kind"] == "cio_review")
    continuation_synthesis = next(task for task in tasks if task["kind"] == "research_synthesis_continuation_1")
    assert result["continuation_task_ids"][2] == cio["id"]
    assert repository.task_dependency_states(cio["id"]) == [{"id": continuation_synthesis["id"], "status": "queued"}]
    assert json.loads(cio["dependency_json"]) == ["research_synthesis_continuation_1"]
    assert cio["status"] == "queued"
    with repository.db.operation() as conn:
        repair_count = conn.execute("SELECT COUNT(*) FROM research_repairs WHERE root_run_id=?", (created["run_id"],)).fetchone()[0]
    assert repair_count == 0
    snapshot = repository.run_snapshot(created["run_id"])
    assert snapshot["lean_continuation"]["used"] is True


def test_lean_continuation_preserves_subject_and_established_candidates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = make_repository(tmp_path)
    body = RunCreate(
        question="Research the established ALFA and BETA leads from public sources.",
        namespace="real",
        horizon="3m",
        idempotency_key="lean-continuation-preserves-universe",
    )
    created, _ = repository.create_run(
        body,
        build_research_tasks(body.question, body.horizon, body.ticker, body.namespace, lean=True),
        allow_semantic_reuse=False,
    )
    routing = created["tasks"][0]
    a00_config, _ = repository.resolve_model("A00", lean=True)
    route = RoutingPlan(
        intent="research", horizon="3m", tickers=[], selected_analysts=["A03"],
        research_queries=["Research the established ALFA and BETA leads."],
        rationale="Keep the public lead set bounded.",
    )
    route_attempt = repository.create_attempt(routing["id"], a00_config, {})
    route_output = repository.commit_output(
        routing["id"], route_attempt["attempt_id"],
        AgentOutputPayload(status="completed", title="Route", summary="Route", analysis="Route", routing_plan=route),
        "real", a00_config,
    )
    repository.consume_routing_plan(created["run_id"], route, routing_output_id=route_output["id"])
    with repository.db.transaction(immediate=True) as conn:
        snapshot = json.loads(conn.execute("SELECT input_snapshot_json FROM runs WHERE id=?", (created["run_id"],)).fetchone()[0])
        snapshot["research_candidates"] = [
            {"ticker": "ALFA", "name": "Alpha issuer", "rationale": "Original ALFA lead", "source_urls": [], "source_ids": []},
            {"ticker": "BETA", "name": "Beta issuer", "rationale": "Original BETA lead", "source_urls": [], "source_ids": []},
        ]
        conn.execute("UPDATE runs SET input_snapshot_json=? WHERE id=?", (json.dumps(snapshot), created["run_id"]))

    synthesis = next(task for task in repository.tasks_for_run(created["run_id"])
                     if task["kind"] == "research_synthesis")
    researcher_config, _ = repository.resolve_model("A03", lean=True)
    attempt = repository.create_attempt(synthesis["id"], researcher_config, {})
    result = repository.commit_output(
        synthesis["id"], attempt["attempt_id"],
        AgentOutputPayload(
            status="needs_review",
            title="Research gap",
            summary="A material public fact remains unresolved.",
            analysis="The current packet cannot establish the named public fact.",
            missing_gaps=[MissingGap(
                key="macro_context",
                description="A dated public macro context source is missing.",
                relevant_role="A03",
                reopen_when="A dated issuer or macro source states the context.",
            )],
        ),
        "real", researcher_config,
    )

    assert len(result["continuation_task_ids"]) == 3
    continuation_snapshot = repository.run_snapshot(created["run_id"])
    assert continuation_snapshot is not None
    continuation_route = continuation_snapshot["routing_plan"]
    assert continuation_route["tickers"] == ["ALFA", "BETA"]
    assert continuation_route["research_queries"][0] == "Research the established ALFA and BETA leads."
    assert any("dated public macro context" in query for query in continuation_route["research_queries"])
    with repository.db.operation() as conn:
        raw_snapshot = json.loads(conn.execute("SELECT input_snapshot_json FROM runs WHERE id=?", (created["run_id"],)).fetchone()[0])
    public_context = raw_snapshot["lean_continuation_public_context"]
    assert public_context["mode"] == "evidence_gap"
    assert public_context["gap_key"] == "macro_context"
    assert public_context["query"] == raw_snapshot["lean_continuation_query"]
    assert public_context["candidate_tickers"] == ["ALFA", "BETA"]

    continuation_task = next(
        task for task in repository.tasks_for_run(created["run_id"])
        if task["kind"] == "universe_discovery_continuation_1"
    )
    assert "dated current release" in continuation_task["instruction"]
    beta_url = "https://beta.example/source"
    monkeypatch.setattr(
        workflow_module,
        "fetch_public_pages",
        lambda _urls, **_kwargs: [FetchedSource(
            requested_url=beta_url,
            final_url=beta_url,
            content="BETA issuer evidence.",
            title="BETA evidence",
            retrieved_at="2026-09-13T00:00:00Z",
        )],
    )
    archived, records, source_ids = asyncio.run(
        Orchestrator(repository, None, repository.config)._archive_discovery(
            created["run_id"],
            continuation_task,
            AgentOutputPayload(
                status="completed",
                title="Evidence refresh",
                summary="A targeted page was found.",
                analysis="The page is retained for the established BETA lead.",
                discovery_urls=["https://tlt.example/source", beta_url],
                research_candidates=[
                    ResearchCandidate(
                        ticker="TLT",
                        name="Treasury ETF",
                        rationale="Unrequested alternative.",
                        source_urls=["https://tlt.example/source"],
                    ),
                    ResearchCandidate(
                        ticker="BETA",
                        name="Beta refresh",
                        rationale="Updated public evidence.",
                        source_urls=[beta_url],
                    ),
                ],
            ),
        )
    )
    assert [item.ticker for item in archived.research_candidates] == ["ALFA", "BETA"]
    assert [item["ticker"] for item in records] == ["ALFA", "BETA"]
    assert len(source_ids) == 1
    repository.record_discovery(created["run_id"], continuation_task["id"], records, source_ids)
    persisted_candidates = repository.run_snapshot(created["run_id"])["research_candidates"]
    assert [item["ticker"] for item in persisted_candidates] == ["ALFA", "BETA"]
    # A same-task retry can supersede a legacy marker without deleting the
    # historical event, while replaying that retry remains idempotent.
    with repository.db.transaction(immediate=True) as conn:
        retried = repository._record_discovery_conn(
            conn,
            created["run_id"],
            continuation_task["id"],
            records,
            source_ids,
            "retry-output-id",
        )
        replayed = repository._record_discovery_conn(
            conn,
            created["run_id"],
            continuation_task["id"],
            records,
            source_ids,
            "retry-output-id",
        )
    assert retried["reused"] is False
    assert replayed["reused"] is True
    with repository.db.operation() as conn:
        marker_count = conn.execute(
            "SELECT COUNT(*) FROM events WHERE run_id=? AND task_id=? AND type='discovery_archived'",
            (created["run_id"], continuation_task["id"]),
        ).fetchone()[0]
    assert marker_count == 2

    # Verify the actual provider-bound packet uses only the active gap query
    # while retaining the public subject and archived URL context.
    with repository.db.transaction(immediate=True) as conn:
        conn.execute(
            "UPDATE tasks SET input_refs_json=? WHERE id=?",
            (json.dumps(source_ids), continuation_task["id"]),
        )
    captured_prompt: dict[str, Any] = {}

    class PromptProvider:
        def is_preflighted(self, _config: ModelConfig) -> bool:
            return True

        async def execute(
            self,
            _attempt_id: str,
            prompt: str,
            _config: ModelConfig,
            _schema: dict[str, Any],
            _workdir: Path,
            _on_event: Any = None,
            **_kwargs: Any,
        ) -> ProviderResult:
            captured_prompt["prompt"] = prompt
            captured_prompt["packet"] = json.loads(
                prompt.split("<untrusted_evidence_packet>\n", 1)[1]
                .rsplit("\n</untrusted_evidence_packet>", 1)[0]
            )
            return ProviderResult(
                payload=AgentOutputPayload(
                    status="completed",
                    title="Gap refresh",
                    summary="The targeted public refresh completed.",
                    analysis="No new candidate admission was requested.",
                ).model_dump(),
                usage=None,
            )

    class PromptRegistry:
        def __init__(self) -> None:
            self.provider = PromptProvider()

        def adapter(self, provider: str) -> PromptProvider:
            assert provider == "codex"
            return self.provider

        async def preflight(self, config: ModelConfig, execute: bool) -> dict[str, Any]:
            return {"available": True, "actual_execution": execute}

        @asynccontextmanager
        async def generation_slot(self, _provider: str):
            yield

    monkeypatch.setattr(workflow_module, "fetch_public_pages", lambda _urls, **_kwargs: [])
    continuation_task = repository.task(continuation_task["id"])
    assert continuation_task is not None
    asyncio.run(
        Orchestrator(repository, PromptRegistry(), repository.config)._execute_task(
            created["run_id"], continuation_task,
        )
    )
    packet = captured_prompt["packet"]
    gap_query = raw_snapshot["lean_continuation_query"]
    assert packet["research_queries"] == [gap_query]
    assert packet["routing_plan"]["research_queries"] == [gap_query]
    assert packet["established_candidate_tickers"] == ["ALFA", "BETA"]
    assert packet["archived_public_urls"] == [beta_url]
    assert "Research the established ALFA and BETA leads." in packet["subject_context"]
    full_prompt = captured_prompt["prompt"]
    assert "one allowed lean evidence-gap continuation" in full_prompt
    assert "single bounded discovery stage" not in full_prompt
    assert "identify up to five candidate tickers" not in full_prompt
    assert "reserve the source budget" not in full_prompt
    assert "dated current release" in full_prompt


def test_lean_continuation_drops_unrequested_candidate_admissions() -> None:
    snapshot = {
        "research_candidates": [
            {"ticker": "ALFA", "name": "Alpha", "rationale": "Original lead", "source_ids": []},
            {"ticker": "BETA", "name": "Beta", "rationale": "Original lead", "source_ids": []},
        ],
        "routing_plan": {"tickers": []},
    }
    filtered = Repository._restrict_lean_continuation_candidates(
        snapshot,
        [
            {"ticker": "TLT", "name": "Treasury ETF", "rationale": "New alternative"},
            {"ticker": "BETA", "name": "Beta refresh", "source_urls": ["https://beta.example/source"], "source_ids": ["src-beta"]},
        ],
    )

    assert [item["ticker"] for item in filtered] == ["ALFA", "BETA"]
    assert filtered[0]["rationale"] == "Original lead"
    assert filtered[1]["source_ids"] == ["src-beta"]


def test_lean_gap_public_query_removes_internal_source_locators() -> None:
    query = Repository._lean_public_gap_query(
        {"description": "Latest revenue in [src_issuer_2026 L14-L18] and [source_market L3]."},
        ["ALFA"],
    )

    assert "src_issuer" not in query
    assert "source_market" not in query
    assert "Latest revenue in" in query


def test_partial_discovery_failure_is_saved_and_exposed_to_next_a01_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = make_repository(tmp_path)
    body = RunCreate(
        question="Research ALFA; private account notes must stay local.",
        namespace="real",
        horizon="3m",
        idempotency_key="lean-discovery-fetch-failure-context",
    )
    created, _ = repository.create_run(
        body,
        build_research_tasks(body.question, body.horizon, body.ticker, body.namespace, lean=True),
        allow_semantic_reuse=False,
    )
    routing = created["tasks"][0]
    a00_config, _ = repository.resolve_model("A00", lean=True)
    route = RoutingPlan(
        intent="research",
        horizon="3m",
        tickers=["ALFA"],
        selected_analysts=["A03"],
        research_queries=["Find the latest dated issuer filing."],
        rationale="A bounded public source review is required.",
    )
    route_attempt = repository.create_attempt(routing["id"], a00_config, {})
    route_output = repository.commit_output(
        routing["id"],
        route_attempt["attempt_id"],
        AgentOutputPayload(status="completed", title="Route", summary="Route", analysis="Route", routing_plan=route),
        "real",
        a00_config,
    )
    repository.consume_routing_plan(created["run_id"], route, routing_output_id=route_output["id"])
    discovery = next(task for task in repository.tasks_for_run(created["run_id"])
                     if task["kind"] == "universe_discovery")
    success_url = "https://issuer.example.test/alfa/results"
    failed_url = "https://issuer.example.test/alfa/filing.pdf?token=private"
    success = FetchedSource(
        requested_url=success_url,
        final_url=success_url,
        content="ALFA issuer results page.",
        title="ALFA results",
        retrieved_at="2026-09-12T00:00:00Z",
    )
    failed = FetchedSource(
        requested_url=failed_url,
        final_url=failed_url,
        content="",
        title="ALFA filing",
        retrieved_at="2026-09-12T00:00:00Z",
        error="HTTP 403 at https://internal.example.test/src_private_account",
    )
    monkeypatch.setattr(workflow_module, "fetch_public_pages", lambda _urls, **_kwargs: [success, failed])
    payload = AgentOutputPayload(
        status="completed",
        title="Discovery",
        summary="A public lead was found.",
        analysis="The issuer page is retained for downstream review.",
        research_candidates=[ResearchCandidate(
            ticker="ALFA",
            name="ALFA issuer",
            rationale="Issuer results page.",
            source_urls=[success_url, failed_url],
        )],
    )

    archived, records, source_ids = asyncio.run(
        Orchestrator(repository, None, repository.config)._archive_discovery(
            created["run_id"], discovery, payload,
        )
    )

    assert archived.research_candidates[0].ticker == "ALFA"
    assert records[0]["source_ids"] == source_ids
    snapshot = repository.run_snapshot(created["run_id"])
    assert snapshot is not None
    assert snapshot["discovery_fetch_failures"] == [{
        "url": "https://issuer.example.test/alfa/filing.pdf",
        "reason": "HTTP 403 at [public URL]",
    }]
    assert "private" not in json.dumps(snapshot["discovery_fetch_failures"]).casefold()

    captured: dict[str, Any] = {}

    class CaptureProvider:
        def is_preflighted(self, _config: ModelConfig) -> bool:
            return True

        async def execute(
            self,
            _attempt_id: str,
            prompt: str,
            _config: ModelConfig,
            _schema: dict[str, Any],
            _workdir: Path,
            _on_event: Any = None,
            **_kwargs: Any,
        ) -> ProviderResult:
            packet = json.loads(prompt.split("<untrusted_evidence_packet>\n", 1)[1].rsplit("\n</untrusted_evidence_packet>", 1)[0])
            captured.update(packet)
            return ProviderResult(
                payload=AgentOutputPayload(
                    status="completed",
                    title="Discovery retry",
                    summary="The bounded public discovery retry completed.",
                    analysis="The retry retained only public discovery context.",
                ).model_dump(),
                usage=None,
            )

    class CaptureRegistry:
        def __init__(self) -> None:
            self.provider = CaptureProvider()

        def adapter(self, provider: str) -> CaptureProvider:
            assert provider == "codex"
            return self.provider

        async def preflight(self, config: ModelConfig, execute: bool) -> dict[str, Any]:
            return {"available": True, "actual_execution": execute}

        @asynccontextmanager
        async def generation_slot(self, _provider: str):
            yield

    # Use the saved diagnostics as the next A01 input.  The fetcher is empty
    # for this prompt capture; no additional network call is made.
    monkeypatch.setattr(workflow_module, "fetch_public_pages", lambda _urls, **_kwargs: [])
    asyncio.run(Orchestrator(repository, CaptureRegistry(), repository.config)._execute_task(created["run_id"], discovery))
    assert captured["discovery_fetch_failures"] == snapshot["discovery_fetch_failures"]
    assert "private account" not in captured["question"].casefold()
    assert "alternate issuer-authored" in captured["question"]


def test_lean_continuation_requires_current_explicit_gap(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    body = RunCreate(
        question="Research ALFA from bounded public sources.",
        namespace="real",
        horizon="3m",
        idempotency_key="lean-continuation-current-gap-only",
    )
    created, _ = repository.create_run(
        body,
        build_research_tasks(body.question, body.horizon, body.ticker, body.namespace, lean=True),
        allow_semantic_reuse=False,
    )
    routing = created["tasks"][0]
    a00_config, _ = repository.resolve_model("A00", lean=True)
    route = RoutingPlan(
        intent="research", horizon="3m", tickers=["ALFA"], selected_analysts=["A03"],
        research_queries=["Find dated public issuer evidence."], rationale="Bounded public research.",
    )
    route_attempt = repository.create_attempt(routing["id"], a00_config, {})
    route_output = repository.commit_output(
        routing["id"], route_attempt["attempt_id"],
        AgentOutputPayload(status="completed", title="Route", summary="Route", analysis="Route", routing_plan=route),
        "real", a00_config,
    )
    repository.consume_routing_plan(created["run_id"], route, routing_output_id=route_output["id"])

    discovery = next(task for task in repository.tasks_for_run(created["run_id"])
                     if task["kind"] == "universe_discovery")
    discovery_config, _ = repository.resolve_model("A01", lean=True)
    discovery_attempt = repository.create_attempt(discovery["id"], discovery_config, {})
    discovery_output = repository.commit_output(
        discovery["id"], discovery_attempt["attempt_id"],
        AgentOutputPayload(
            status="completed", title="Discovery", summary="Discovery archived the bounded packet.",
            analysis="An older archival note remains in the ledger for audit.",
            missing_data=["A generic archival note from the initial discovery packet."],
        ),
        "real", discovery_config,
    )
    assert discovery_output["continuation_task_ids"] == []

    synthesis = next(task for task in repository.tasks_for_run(created["run_id"])
                     if task["kind"] == "research_synthesis")
    synthesis_config, _ = repository.resolve_model("A03", lean=True)
    synthesis_attempt = repository.create_attempt(synthesis["id"], synthesis_config, {})
    synthesis_output = repository.commit_output(
        synthesis["id"], synthesis_attempt["attempt_id"],
        AgentOutputPayload(
            status="completed", title="Synthesis", summary="The current packet is sufficient.",
            analysis="No material public fact is currently missing from this review.",
        ),
        "real", synthesis_config,
    )

    assert synthesis_output["continuation_task_ids"] == []
    assert not any("continuation" in task["kind"] for task in repository.tasks_for_run(created["run_id"]))


def test_lean_market_context_exposes_all_completed_timeframes() -> None:
    sources: list[dict[str, object]] = []
    for timeframe in ("1Min", "1Hour", "1Day", "1Week"):
        source_type = "derived_weekly_market_bars" if timeframe == "1Week" else "market_bars"
        header = {
            "provider": "alpaca",
            "source_type": source_type,
            "metadata": {
                "timeframe": timeframe,
                "symbols": ["ALFA"],
                "currency": "USD",
                "feed": "iex",
                "adjustment": "split",
            },
        }
        rows = [
            {"symbol": "ALFA", "timestamp": f"2026-08-{index:02d}T00:00:00Z", "open": "9", "high": "12", "low": "8", "close": str(10 + index), "volume": "100", "complete": True}
            for index in (1, 2, 3)
        ]
        sources.append({
            "id": f"src-{timeframe}",
            "content_hash": f"hash-{timeframe}",
            "content": "\n".join(["Road2M canonical research source", json.dumps(header), *(json.dumps(row) for row in rows)]),
        })

    context = Orchestrator._lean_deterministic_market_context(
        {"as_of": "2026-09-13T00:00:00Z", "horizon": "3m"}, sources, ["ALFA"]
    )

    candidate = context["candidates"][0]
    assert set(candidate["technicals"]) == {"1Min", "1Hour", "1Day", "1Week"}
    assert set(candidate["technical"]["frequencies"]) == {"minute", "hourly", "daily", "weekly"}
    assert set(candidate["latest_bars"]) == {"1Min", "1Hour", "1Day", "1Week"}
    assert candidate["latest_bars"]["1Day"]["timestamp"] == "2026-08-03T00:00:00Z"


def test_lean_market_context_suppresses_calculations_on_asset_identity_conflict() -> None:
    identity = {
        "provider": "alpaca",
        "status": "active",
        "capability": "ready",
        "asset_id": "asset-gold-com",
        "symbol": "GOLD",
        "name": "Gold.com, Inc.",
        "exchange": "NASDAQ",
        "retrieved_at": "2026-09-13T00:00:00Z",
    }
    identity_source = {
        "id": "asset-identity-gold",
        "content": Orchestrator._asset_identity_source_text(identity, "GOLD"),
        "content_hash": "asset-identity-gold",
    }
    market_header = {
        "provider": "alpaca",
        "source_type": "market_bars",
        "metadata": {
            "timeframe": "1Day",
            "symbols": ["GOLD"],
            "currency": "USD",
            "feed": "iex",
            "adjustment": "split",
        },
    }
    market_source = {
        "id": "market-gold",
        "content_hash": "market-gold",
        "content": "\n".join([
            "Road2M canonical research source",
            json.dumps(market_header),
            json.dumps({"symbol": "GOLD", "timestamp": "2026-09-12T00:00:00Z", "open": "9", "high": "12", "low": "8", "close": "10", "volume": "100", "complete": True}),
        ]),
    }

    context = Orchestrator._lean_deterministic_market_context(
        {
            "as_of": "2026-09-13T00:00:00Z",
            "horizon": "3m",
            "input_snapshot_json": json.dumps({"research_candidates": [{"ticker": "GOLD", "name": "Barrick"}]}),
        },
        [identity_source, market_source],
        ["GOLD"],
    )

    candidate = context["candidates"][0]
    assert candidate["instrument_identity"]["status"] == "conflict"
    assert candidate["instrument_identity"]["provider_name"] == "Gold.com, Inc."
    assert candidate["scenario"]["status"] == "insufficient_evidence"
    assert all(item["sample_count"] == 0 for item in candidate["technical"]["frequencies"].values())
    provider_view = prepare_provider_context({"deterministic_market": context}, [])["deterministic_market"]
    assert provider_view["candidates"][0]["instrument_identity"]["asset_id"] == "asset-gold-com"


def test_archived_asset_identity_selects_latest_bound_record_and_keeps_symbol_conflict() -> None:
    older = {
        "provider": "alpaca",
        "status": "active",
        "capability": "ready",
        "asset_id": "asset-gold-old",
        "symbol": "GOLD",
        "name": "Old Gold Listing",
        "retrieved_at": "2026-09-10T00:00:00Z",
    }
    newer = {
        "provider": "alpaca",
        "status": "active",
        "capability": "ready",
        "asset_id": "asset-gold-new",
        "symbol": "GOLD",
        "name": "Gold.com, Inc.",
        "retrieved_at": "2026-09-13T00:00:00Z",
    }
    mismatch = {
        "provider": "alpaca",
        "status": "active",
        "capability": "ready",
        "asset_id": "asset-barrick",
        "symbol": "B",
        "name": "Barrick Mining Corporation",
        "retrieved_at": "2026-09-13T00:00:00Z",
    }

    selected, selected_refs = Orchestrator._asset_identity_from_sources(
        [
            {"id": "identity-old", "content": Orchestrator._asset_identity_source_text(older, "GOLD")},
            {"id": "identity-new", "content": Orchestrator._asset_identity_source_text(newer, "GOLD")},
        ],
        "GOLD",
    )
    wrong_symbol, wrong_refs = Orchestrator._asset_identity_from_sources(
        [{"id": "identity-mismatch", "content": Orchestrator._asset_identity_source_text(mismatch, "GOLD")}],
        "GOLD",
    )

    assert selected["asset_id"] == "asset-gold-new"
    assert selected_refs == ["identity-new"]
    assert wrong_symbol["symbol"] == "B"
    assert wrong_refs == ["identity-mismatch"]


def test_derived_weekly_source_carries_explicit_daily_metadata_only() -> None:
    daily_rows = [
        {
            "symbol": "ALFA",
            "timestamp": f"2026-08-{index:02d}T00:00:00Z",
            "open": "9",
            "high": "12",
            "low": "8",
            "close": str(10 + index),
            "volume": "100",
            "complete": True,
        }
        for index in (3, 4, 5, 6, 7, 10, 11)
    ]
    weekly = Orchestrator._weekly_bars(
        daily_rows,
        "ALFA",
        source_metadata={
            "currency": "CAD",
            "symbols": ["ALFA"],
            "feed": "iex",
            "adjustment": "split",
        },
        source_id="daily-source-alfa",
        retrieved_at=datetime.fromisoformat("2026-09-13T00:00:00+00:00"),
    )
    header = json.loads(weekly.canonical_source_text().splitlines()[1])
    metadata = header["metadata"]

    assert metadata["currency"] == "CAD"
    assert metadata["symbols"] == ["ALFA"]
    assert metadata["feed"] == "iex"
    assert metadata["adjustment"] == "split"
    assert metadata["source_id"] == "daily-source-alfa"

    no_currency = Orchestrator._weekly_bars(
        daily_rows,
        "ALFA",
        source_metadata={"adjustment": "split"},
        retrieved_at=datetime.fromisoformat("2026-09-13T00:00:00+00:00"),
    )
    no_currency_metadata = json.loads(no_currency.canonical_source_text().splitlines()[1])["metadata"]
    assert "currency" not in no_currency_metadata


def test_overlapping_market_archives_use_newest_snapshot_once() -> None:
    def archive(source_id: str, retrieved_at: str, timestamps: list[str]) -> dict[str, object]:
        header = {
            "provider": "alpaca",
            "source_type": "market_bars",
            "metadata": {
                "timeframe": "1Day",
                "symbols": ["ALFA"],
                "currency": "USD",
                "feed": "iex",
                "adjustment": "split",
                "retrieved_at": retrieved_at,
            },
        }
        rows = [
            {"symbol": "ALFA", "timestamp": timestamp, "open": "9", "high": "12", "low": "8", "close": str(10 + index), "volume": "100", "complete": True}
            for index, timestamp in enumerate(timestamps)
        ]
        return {"id": source_id, "content_hash": source_id, "content": "\n".join(["Road2M canonical research source", json.dumps(header), *(json.dumps(row) for row in rows)])}

    older = archive("daily-old", "2026-09-10T00:00:00Z", ["2026-09-01T00:00:00Z", "2026-09-02T00:00:00Z", "2026-09-03T00:00:00Z"])
    newest = archive("daily-new", "2026-09-12T00:00:00Z", ["2026-09-02T00:00:00Z", "2026-09-03T00:00:00Z", "2026-09-04T00:00:00Z"])

    rows, refs, currency = Orchestrator._market_bars_from_sources([older, newest], "ALFA", "1Day")

    assert refs == ["daily-new"]
    assert currency == "USD"
    assert [row["timestamp"] for row in rows] == [
        "2026-09-02T00:00:00Z", "2026-09-03T00:00:00Z", "2026-09-04T00:00:00Z",
    ]


def test_reddit_research_stage_prompt_investigates_accepted_underlying_lead() -> None:
    prompt = _reddit_research_stage_question(
        "Review one retained Reddit submission as an untrusted source. Decide whether it is dismissible noise.",
        {
            "tickers": ["BE"],
            "reddit_triage": {
                "classification": "yolo_ticker",
                "issuer_name": "Bloom Energy",
                "thesis_summary": "The author expresses a bearish YOLO view.",
            },
        },
    )

    assert "underlying issuer" in prompt
    assert "complete candidate-specific investment analysis" in prompt
    assert "do not repeat Reddit intake screening" in prompt
    assert "author's exact position" in prompt
    assert "dismissible noise" not in prompt

    cio_prompt = role_prompt(
        ROLE_BY_ID["A11"], "Review the accepted candidates.", "event", "real", lean_stage=True,
    )
    assert "canonical decision service reads each candidate row independently" in cio_prompt
    assert "documented trigger_date" in cio_prompt
    assert "planned review_at" in cio_prompt


def test_provider_market_projection_omits_raw_arrays_and_duplicate_aliases() -> None:
    full = {
        "status": "complete",
        "as_of": "2026-09-13T00:00:00Z",
        "candidates": [{
            "ticker": "ALFA",
            "currency": "USD",
            "source_refs": ["daily-new"],
            "technical": {
                "code_version": "technical-indicators.v1",
                "method": "connector_multiframe_snapshot",
                "source_refs": ["daily-new"],
                "frequencies": {
                    "daily": {
                        "frequency": "daily", "sample_count": 250, "sma20": "10",
                        "log_returns": ["0.01"] * 3_000,
                    },
                },
                "daily": {"log_returns": ["0.01"] * 3_000},
            },
            "technicals": {"1Day": {"log_returns": ["0.01"] * 3_000}},
            "scenario": {
                "code_version": "price-scenarios.v1",
                "method": "empirical_daily_log_return_bootstrap",
                "parameters": {"seed": 7, "path_count": 1_000},
                "assumptions": ["The supplied bars are adjusted daily observations."],
                "scenarios": {
                    "base": {
                        "terminal_price_quantiles": {"p05": "8", "p50": "10", "p95": "12"},
                        "loss_frequency": "0.25",
                        "fan": [{"trading_day": 1}] * 1_000,
                    },
                },
            },
            "latest_bars": {"1Day": {"timestamp": "2026-09-12T00:00:00Z", "close": "10"}},
        }],
    }

    projected = prepare_provider_context({"deterministic_market": full}, [])["deterministic_market"]
    rendered = json.dumps(projected)

    assert len(rendered) < 20_000
    assert "log_returns" not in rendered
    assert "fan" not in rendered
    assert "path_count" in rendered
    assert "loss_frequency" in rendered
    assert "daily" in projected["candidates"][0]["technical"]["frequencies"]
    assert "technicals" not in projected["candidates"][0]


def test_provider_case_decision_projection_compacts_technicals_and_bounds_watch_data() -> None:
    full = {
        "schema_version": "case-decision.v1",
        "run_id": "run-alfa",
        "decision_revision": 3,
        "outcome": "watchlist",
        "rationale": "Keep the candidate under review.",
        "material_blockers": [{"key": "catalyst", "reason": "Awaiting an issuer update."}],
        "candidates": [{
            "ticker": "ALFA",
            "outcome": "watchlist",
            "sizing": {"execution_state": "awaiting_input", "shares": None},
            "future_target": {"price": "12", "currency": "USD", "source_refs": ["src-alfa"]},
            "technical_indicators": {
                "code_version": "technical-indicators.v1",
                "method": "connector_multiframe_snapshot",
                "frequencies": {
                    "daily": {"frequency": "daily", "sample_count": 250, "sma20": "10", "log_returns": ["0.01"] * 3_000},
                },
                "technicals": {"1Day": {"log_returns": ["0.01"] * 3_000}},
                "log_returns": ["0.01"] * 3_000,
            },
            "watch_triggers": [{
                "type": "evidence",
                "condition": "x" * 20_000,
                "evidence_condition": "y" * 20_000,
                "source_refs": ["src-alfa"] * 100,
            } for _ in range(30)],
        }],
    }

    projected = prepare_provider_context({"current_case_decision": full}, [])["current_case_decision"]

    assert projected is not full
    assert projected["rationale"] == full["rationale"]
    assert projected["material_blockers"] == full["material_blockers"]
    candidate = projected["candidates"][0]
    assert candidate["sizing"] == full["candidates"][0]["sizing"]
    assert candidate["future_target"] == full["candidates"][0]["future_target"]
    assert "log_returns" not in json.dumps(candidate["technical_indicators"])
    assert "technicals" not in candidate["technical_indicators"]
    assert "frequencies" in candidate["technical_indicators"]
    assert len(candidate["watch_triggers"]) == 20
    assert len(candidate["watch_triggers"][0]["condition"]) == 4_000
    assert len(candidate["watch_triggers"][0]["source_refs"]) == 20
    assert len(full["candidates"][0]["watch_triggers"]) == 30
    assert len(full["candidates"][0]["technical_indicators"]["log_returns"]) == 3_000


def test_provider_evidence_projection_keeps_selected_market_rows_and_true_locators() -> None:
    def market(source_id: str, close: str) -> dict[str, object]:
        header = {
            "provider": "alpaca", "source_type": "market_bars",
            "metadata": {"timeframe": "1Day", "symbols": ["ALFA"], "currency": "USD", "feed": "iex", "adjustment": "split"},
        }
        content = "\n".join([
            "Road2M canonical research source", json.dumps(header),
            json.dumps({"symbol": "ALFA", "timestamp": "2026-09-12T00:00:00Z", "close": close, "complete": True}),
        ])
        return {"id": source_id, "title": source_id, "url": "https://data.alpaca.markets/v2/stocks/bars", "version": 1, "content_hash": source_id, "content": content}

    narrative = {
        "id": "issuer-page", "title": "Issuer page", "url": "https://issuer.example/alfa", "version": 1,
        "content_hash": "issuer-page", "content": "\n".join(["header"] + [f"public fact line {index}" for index in range(1, 1_000)]),
    }
    packet = prepare_provider_context(
        {"deterministic_market": {"candidates": [{"ticker": "ALFA", "source_refs": ["daily-new"]}]}},
        [market("daily-old", "9"), market("daily-new", "10"), narrative],
    )
    projected, metadata = packet["evidence"], packet["evidence_projection"]
    by_id = {item["id"]: item for item in projected}

    assert "daily-new" in by_id
    assert "daily-old" not in by_id
    assert "daily-old" in metadata["omitted_source_ids"]
    assert "L3:" in by_id["daily-new"]["content"]
    assert len(by_id["issuer-page"]["content"]) <= 12_000


def test_provider_evidence_projection_prioritizes_late_handoff_and_reports_omissions() -> None:
    def narrative(source_id: str) -> dict[str, object]:
        return {
            "id": source_id,
            "title": source_id,
            "url": f"https://issuer.example/{source_id}",
            "version": 1,
            "content_hash": source_id,
            "content": "Public archived evidence " + ("x" * 4_000),
        }

    sources = [narrative(f"historical-{index}") for index in range(105)]
    sources.append(narrative("late-critical-macro"))
    packet = prepare_provider_context(
        {},
        sources,
        priority_source_ids=["late-critical-macro"],
    )
    projected, metadata = packet["evidence"], packet["evidence_projection"]
    included = [item["id"] for item in projected]
    all_ids = {item["id"] for item in sources}

    assert included[0] == "late-critical-macro"
    assert "late-critical-macro" in metadata["priority_included_source_ids"]
    assert metadata["omitted_source_ids"]
    assert set(included) | set(metadata["omitted_source_ids"]) == all_ids
    assert set(included).isdisjoint(metadata["omitted_source_ids"])
    assert sum(len(item["content"]) for item in projected) <= 320_000


def test_lean_attempt_keeps_all_explicit_source_refs_beyond_memory_cap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = make_repository(tmp_path)
    body = RunCreate(
        question="Research ALFA using the complete explicit public source packet.",
        namespace="real",
        ticker="ALFA",
        horizon="3m",
        idempotency_key="lean-explicit-source-packet-over-memory-cap",
    )
    created, _ = repository.create_run(
        body,
        build_research_tasks(body.question, body.horizon, body.ticker, body.namespace, lean=True),
        allow_semantic_reuse=False,
    )
    routing = created["tasks"][0]
    config, _ = repository.resolve_model("A00", lean=True)
    route = RoutingPlan(
        intent="research",
        horizon="3m",
        tickers=["ALFA"],
        selected_analysts=["A03"],
        research_queries=["Find the latest dated ALFA issuer evidence."],
        rationale="The full task source packet is required for an auditable review.",
    )
    route_attempt = repository.create_attempt(routing["id"], config, {})
    route_output = repository.commit_output(
        routing["id"],
        route_attempt["attempt_id"],
        AgentOutputPayload(status="completed", title="Route", summary="Route", analysis="Route", routing_plan=route),
        "real",
        config,
    )
    repository.consume_routing_plan(created["run_id"], route, routing_output_id=route_output["id"])

    explicit_ids: list[str] = []
    for index in range(116):
        imported = repository.import_evidence(ImportRequest(
            namespace="real",
            kind="evidence",
            title=f"ALFA public source {index}",
            content=f"Dated public ALFA evidence source {index}.",
            source_url=f"https://issuer.example/alfa/{index}",
            idempotency_key=f"lean-explicit-source-{index}",
        ))
        explicit_ids.append(imported["source_id"])
    late_source_id = explicit_ids[-1]
    synthesis = next(
        task for task in repository.tasks_for_run(created["run_id"])
        if task["kind"] == "research_synthesis"
    )
    with repository.db.transaction(immediate=True) as conn:
        conn.execute(
            "UPDATE tasks SET input_refs_json=? WHERE id=?",
            (json.dumps(explicit_ids), synthesis["id"]),
        )

    async def no_market(_self: Any, _run_id: str, _task: Any, _run: Any) -> tuple[list[str], list[dict[str, Any]]]:
        return [], []

    monkeypatch.setattr(Orchestrator, "_prepare_market_evidence", no_market)
    captured: dict[str, Any] = {}

    class CaptureProvider:
        def is_preflighted(self, _config: ModelConfig) -> bool:
            return True

        async def execute(
            self,
            attempt_id: str,
            prompt: str,
            _config: ModelConfig,
            schema: dict[str, Any],
            _workdir: Path,
            _on_event: Any = None,
            **_kwargs: Any,
        ) -> ProviderResult:
            captured["attempt_id"] = attempt_id
            captured["schema"] = schema
            captured["prompt"] = prompt
            captured["packet"] = json.loads(
                prompt.split("<untrusted_evidence_packet>\n", 1)[1]
                .rsplit("\n</untrusted_evidence_packet>", 1)[0]
            )
            return ProviderResult(
                payload=AgentOutputPayload(
                    status="completed",
                    title="Synthesis",
                    summary="The complete explicit packet was retained.",
                    analysis="The provider received the late source locator.",
                ).model_dump(),
                usage=None,
            )

    class CaptureRegistry:
        def __init__(self) -> None:
            self.provider = CaptureProvider()

        def adapter(self, provider: str) -> CaptureProvider:
            assert provider == "codex"
            return self.provider

        async def preflight(self, _config: ModelConfig, execute: bool) -> dict[str, Any]:
            return {"available": True, "actual_execution": execute}

        @asynccontextmanager
        async def generation_slot(self, _provider: str):
            yield

    synthesis = repository.task(synthesis["id"])
    assert synthesis is not None
    asyncio.run(Orchestrator(repository, CaptureRegistry(), repository.config)._execute_task(created["run_id"], synthesis))

    assert late_source_id in captured["packet"]["source_ids"]
    assert late_source_id in captured["packet"]["evidence_projection"]["included_source_ids"]
    assert late_source_id in json.dumps(captured["schema"], ensure_ascii=False)
    with repository.db.operation() as conn:
        attempt_row = conn.execute(
            "SELECT id,source_versions_json FROM task_attempts WHERE id=?",
            (captured["attempt_id"],),
        ).fetchone()
    assert attempt_row is not None
    assert late_source_id in json.loads(attempt_row["source_versions_json"])
