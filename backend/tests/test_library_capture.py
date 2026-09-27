"""Company capture at real intake boundaries, without a backfill or model call."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.document_intelligence import create_document_router
from backend.app.config import Settings
from backend.app.memory.repository import Repository
from backend.app.orchestration.workflow import build_research_tasks
from backend.app.research.workflows import WorkflowStore
from backend.app.schemas import AgentOutputPayload, CoverageRequest, ImportRequest, ModelConfig, RoutingPlan, RunCreate


@pytest.fixture
def repo(tmp_path):
    repository = Repository(config=Settings(project_root=Path(__file__).resolve().parents[2], data_dir=tmp_path,
                                            enable_market_connectors=False, enable_reddit_intake=False))
    repository.control("firm", None, "pause")
    return repository


def entries(repo, namespace="real"):
    with repo.db.operation() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM research_library_entries WHERE namespace=? ORDER BY ticker", (namespace,))]


def mentions(repo, ticker="COST", namespace="real"):
    with repo.db.operation() as conn:
        return [dict(row) for row in conn.execute("""SELECT m.* FROM research_library_mentions m
            JOIN research_library_entries e ON e.id=m.entry_id WHERE e.namespace=? AND e.ticker=?""", (namespace, ticker))]


def create(repo, question, key, *, ticker=None, namespace="real", routing=False):
    body = RunCreate(question=question, ticker=ticker, namespace=namespace, idempotency_key=key)
    tasks = build_research_tasks(question, None, ticker, namespace, lean=True) if routing else [("A03", "fundamental_review", "Review retained material.", [])]
    return repo.create_run(body, tasks, allow_semantic_reuse=False)[0]


def test_ticker_null_document_question_captures_cost_without_prose_acronyms(repo):
    question = ("Investigate these saved document findings for COST (Q4 FY2026 / FY2026). "
                "Treat this local NLP analysis as heuristic evidence. Analysis ID: retained-document. "
                "The U.S. business opened a warehouse in Buffalo, N.Y. and reported EPS in USD.")
    result = create(repo, question, "capture-cost-question")
    assert repo.run_record(result["run_id"])["ticker"] is None
    assert [row["ticker"] for row in entries(repo)] == ["COST"]
    assert mentions(repo)[0]["run_id"] == result["run_id"]
    assert repo.firm_paused()
    assert repo.run_record(result["run_id"])["status"] == "queued"
    # Replaying the API request never creates a second mention.
    replay = create(repo, question, "capture-cost-question")
    assert replay["run_id"] == result["run_id"]
    assert len(mentions(repo)) == 1


def test_question_earnings_manual_document_and_coverage_share_one_company(repo):
    create(repo, "Review COST", "capture-cost-dedup")
    workflow = WorkflowStore(repo)
    workflow_id = workflow.create("cost", idempotency_key="capture-cost-workflow")
    assert workflow.create("COST", idempotency_key="capture-cost-workflow") == workflow_id
    app = FastAPI()
    app.include_router(create_document_router(repo.config, repo=repo))
    transcript = """Costco earnings call
Jane Smith — Chief Financial Officer
Membership renewal remained strong and our customers continued to shop at our warehouses.
We are investing in new locations and reviewing the cost of planned capital projects.
Questions and Answers
Alex Jones — Analyst: How is membership trending?
Jane Smith: We are seeing continued growth, although the recent rate has moderated.
"""
    with TestClient(app) as client:
        response = client.post("/api/document-analysis/transcript", json={"ticker": "COST", "period": "Q4 FY2026", "text": transcript})
        assert response.status_code == 200, response.text
        document_id = response.json()["id"]
    coverage = CoverageRequest(namespace="real", sector="Consumer staples", status="watch", reason="Review the latest earnings.")
    repo.set_coverage("cost", coverage)
    repo.set_coverage("COST", coverage)
    assert [row["ticker"] for row in entries(repo)] == ["COST"]
    captured = mentions(repo)
    assert {row["origin"] for row in captured} == {"question", "earnings", "document", "watchlist"}
    assert len(captured) == 4
    assert next(row for row in captured if row["origin"] == "document")["origin_ref"] == document_id
    assert next(row for row in captured if row["origin"] == "earnings")["workflow_id"] == workflow_id


def test_new_followup_company_is_captured_once_with_own_origin(repo):
    result = create(repo, "Review COST", "capture-followup-parent")
    first = repo.add_followup(result["run_id"], "Compare MSFT with this company.", "capture-followup-msft")
    replay = repo.add_followup(result["run_id"], "Compare MSFT with this company.", "capture-followup-msft")
    assert first["task_id"] == replay["task_id"]
    assert [row["ticker"] for row in entries(repo)] == ["COST", "MSFT"]
    captured = mentions(repo, "MSFT")
    assert len(captured) == 1
    assert captured[0]["run_id"] == result["run_id"]
    assert captured[0]["origin"] == "followup"


def test_portfolio_csv_captures_company_in_only_its_namespace(repo):
    request = ImportRequest(namespace="demo", kind="transactions", title="Imported transactions",
                            content="transaction_id,account,symbol,side,quantity,price,currency\n1,Fixture,cost,buy,2,700,USD\n2,Fixture,MSFT,buy,1,400,USD\n",
                            idempotency_key="capture-csv-transactions")
    imported = repo.import_evidence(request)
    assert repo.import_evidence(request)["duplicate"]
    assert entries(repo, "real") == []
    assert [row["ticker"] for row in entries(repo, "demo")] == ["COST", "MSFT"]
    assert {row["origin_ref"] for ticker in ("COST", "MSFT") for row in mentions(repo, ticker, "demo")} == {imported["id"]}


def test_portfolio_seed_captures_deduplicated_held_companies(repo, tmp_path):
    seed = tmp_path / "capture-portfolio.json"
    seed.write_text(json.dumps({"positions": [{"symbol": "COST", "quantity": "2", "cost_basis": "1000", "currency": "USD"},
                                                {"symbol": "COST", "quantity": "1", "cost_basis": "500", "currency": "USD"},
                                                {"symbol": "msft", "quantity": "1", "cost_basis": "400", "currency": "USD"}]}))
    assert repo.load_portfolio_seed(seed)["loaded"]
    assert repo.load_portfolio_seed(seed)["duplicate"]
    assert [row["ticker"] for row in entries(repo)] == ["COST", "MSFT"]
    assert len(mentions(repo, "COST")) == 1
    assert len(mentions(repo, "MSFT")) == 1
    assert entries(repo, "demo") == []


@pytest.mark.parametrize("fields,ticker", [
    ({"candidate_briefs": [{"ticker": "MSFT", "entry_advice": "Research is incomplete."}]}, "MSFT"),
    ({"decision_brief": {"candidate_briefs": [{"ticker": "COST", "entry_advice": "Research is incomplete."}]}}, "COST"),
    ({"decision_brief": {"ticker": "NVDA", "entry_advice": "Research is incomplete."}}, "NVDA"),
])
def test_committed_output_captures_top_and_nested_candidate_fields(repo, fields, ticker):
    result = create(repo, "Explore businesses with durable demand.", "capture-output-" + ticker)
    assert entries(repo) == []
    task_id = result["tasks"][0]["id"]
    config = ModelConfig(provider="codex", model="gpt-5.6-luna", reasoning_effort="max")
    attempt = repo.create_attempt(task_id, config, {})
    payload = AgentOutputPayload(status="completed", title="Candidate review", summary="Retained unverified research lead.",
                                 analysis="Additional evidence is needed before making a decision.", **fields)
    output = repo.commit_output(task_id, attempt["attempt_id"], payload, "real", config)
    assert [row["ticker"] for row in entries(repo)] == [ticker]
    captured = mentions(repo, ticker)
    assert len(captured) == 1
    assert captured[0]["origin_ref"] == output["id"]
    assert captured[0]["run_id"] == result["run_id"]


def test_routing_discovers_symbols_for_open_question_before_model_analysis(repo):
    result = create(repo, "Which retailers merit closer research?", "capture-routing-candidates", routing=True)
    assert entries(repo) == []
    route = RoutingPlan(intent="research", horizon="event", tickers=["COST", "WMT"], selected_analysts=["A03"], research_queries=[])
    repo.consume_routing_plan(result["run_id"], route)
    repo.consume_routing_plan(result["run_id"], route)
    assert [row["ticker"] for row in entries(repo)] == ["COST", "WMT"]
    assert len(mentions(repo, "COST")) == 1
    assert len(mentions(repo, "WMT")) == 1
