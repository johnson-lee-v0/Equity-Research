"""Workflow contracts: checkpoints, evidence lineage, partial coverage and handoffs."""
import asyncio
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from backend.app.api.research_workflows import create_workflow_router
from backend.app.config import Settings
from backend.app.memory.repository import Repository
from backend.app.research.discovery import FetchedSource
from backend.app.research.source_archive import archive_public_page
from backend.app.research.workflows import ResearchWorkflows


TRANSCRIPT = """Acme Q3 2026 earnings call transcript
Jane Smith — Chief Executive Officer
We saw strong demand in Europe. Our sales increased with new customers.
John Jones — Chief Financial Officer
Margins improved but costs remain under pressure.
Questions and Answers
Tom Brown — Analyst: Do you expect growth next quarter?
Jane Smith: We are confident about growth and our liquidity is strong.
"""
PREVIOUS = """Form 10-Q
PART I
Item 2. Management's Discussion and Analysis
Customer demand remains strong across our markets. We expect our distribution capacity to support continued growth.
PART II
Item 1A. Risk Factors
Our supply chain depends on qualified partners, and delays could adversely affect customer deliveries.
"""
CURRENT = PREVIOUS.replace("support continued growth", "remain constrained through the next quarter").replace("qualified partners", "a single manufacturing partner")


class Acquisition:
    def __init__(self, repo, namespace="real", missing=()):
        self.repo, self.namespace = repo, namespace
        self.missing = set(missing)
        self.calls = []
        self.fail_locate = False
        self.block = None

    async def resolve(self, ticker):
        self.calls.append("resolve")
        if self.block:
            await self.block.wait()
        return {"ticker": ticker, "name": "Acme Inc.", "cik": "0000001234"}

    async def locate(self, company):
        self.calls.append("locate")
        if self.fail_locate:
            raise ValueError("Latest period could not be verified.")
        return {"period_end": "2026-06-30", "fiscal_period": "Q3 2026", "earnings_date": "2026-07-15", "expected_form": "10-Q"}

    async def acquire(self, company, event):
        self.calls.append("acquire")
        documents = {}
        for role, content in [("transcript", TRANSCRIPT), ("current_filing", CURRENT), ("prior_filing", PREVIOUS), ("release", "Acme reports strong results for the fiscal period ended June 30, 2026.")]:
            if role in self.missing:
                documents[role] = {"status": "pending", "reason": f"{role} has not been published yet."}
                continue
            url = f"https://example.com/{company['ticker']}/{role}"
            page = FetchedSource(url, url, content, role, "2026-07-15T12:00:00Z")
            archived = archive_public_page(self.repo, page, namespace=self.namespace, scope="test-earnings")
            documents[role] = {"status": "available", "content": content, "source_id": archived["source_id"], "url": url, "title": role, "period_end": "2026-06-30" if role != "prior_filing" else "2026-03-31", "form": "10-Q"}
        return {"documents": documents, "gaps": [d["reason"] for role, d in documents.items() if d["status"] != "available" and role in {"transcript", "release"}], "comparison_gaps": [d["reason"] for role, d in documents.items() if d["status"] != "available" and role in {"current_filing", "prior_filing"}], "comparison_basis": "Prior quarterly report"}


class Trends:
    def __init__(self, acquisition):
        self.acquisition = acquisition

    async def collect(self, company, event, documents):
        return {"version": "earnings-trends.v1", "status": "available", "series": [], "sources": [], "gaps": []}


@pytest.fixture
def setup(tmp_path):
    config = Settings(data_dir=tmp_path)
    repo = Repository(config=config)
    acquisition = Acquisition(repo)
    service = ResearchWorkflows(repo, None, config, acquisition_factory=lambda namespace: acquisition, trends_factory=Trends)
    return service, acquisition, repo


def complete(service, ticker="ACME", **kwargs):
    identifier = service.store.create(ticker, **kwargs)
    asyncio.run(service.execute(identifier))
    return service.detail(identifier)


def test_complete_workflow_archives_sources_and_reuses_document_views(setup):
    service, acquisition, repo = setup
    run = complete(service)
    assert run["status"] == "completed", run
    assert all(s["status"] == "completed" for s in run["steps"])
    assert len(run["result"]["source_ids"]) == 4
    analyses = run["result"]["analyses"]
    assert set(analyses) == {"transcript", "filing_comparison"}
    assert analyses["transcript"]["result"]["sentiment"]["sentences"] > 0
    assert analyses["filing_comparison"]["result"]["counts"]["changed"] > 0
    assert acquisition.calls == ["resolve", "locate", "acquire"]
    for document in run["result"]["documents"].values():
        assert "content" not in document
    record = service.analyses.get(analyses["transcript"]["id"])
    assert record["source_lineage"][0]["source_id"] in run["result"]["source_ids"]
    assert record["inputs"]["text"] == TRANSCRIPT
    assert repo.source_packet("real", run["result"]["source_ids"])


def test_librarian_includes_live_company_and_ticker_null_question_without_crossing_namespace(setup):
    from backend.app.schemas import RunCreate

    service, _, repo = setup
    body = RunCreate(question="Review COST earnings", idempotency_key="library-linked-question")
    retained, _ = repo.create_run(body, [("A03", "fundamental_review", "Review", [])])
    identifier = service.store.create("COST")
    context = service._context(service.store.get(identifier))
    assert [r["id"] for r in context["research"]["items"]] == [retained["run_id"]]
    assert len(context["library"]["items"]) == 1
    assert context["library"]["items"][0]["live"] is True
    demo_id = service.store.create("COST", namespace="demo")
    demo = service._context(service.store.get(demo_id))
    assert not demo["research"]["items"]
    assert len(demo["library"]["items"]) == 1
    assert demo["library"]["items"][0]["id"] != context["library"]["items"][0]["id"]


def test_pending_annual_filing_does_not_block_earnings_materials(setup):
    service, acquisition, _ = setup
    acquisition.missing = {"current_filing"}
    first = complete(service)
    assert first["status"] == "completed"
    assert "transcript" in first["result"]["analyses"]
    assert "filing_comparison" not in first["result"]["analyses"]
    assert first["result"]["documents"]["current_filing"]["status"] == "pending"
    assert first["result"]["comparison_gaps"]
    assert not first["result"]["gaps"]


def test_failed_optional_search_is_retryable_but_unavailable_document_is_not_a_gate(setup):
    service, acquisition, _ = setup
    original = acquisition.acquire
    async def unavailable_search(company, event):
        return await original(company, event) | {"material_discovery_status": "unavailable", "material_gaps": ["Search could not finish."]}
    acquisition.acquire = unavailable_search
    run = complete(service)
    assert run["status"] == "partial"
    assert "transcript" in run["result"]["analyses"]
    assert next(s for s in run["steps"] if s["id"] == "acquire")["status"] == "partial"
    async def completed_search(company, event):
        return await original(company, event) | {"material_discovery_status": "completed", "material_gaps": ["Optional presentation is unreadable."]}
    acquisition.acquire = completed_search
    service.store.retry(run["id"])
    asyncio.run(service.execute(run["id"]))
    retried = service.detail(run["id"])
    assert retried["status"] == "completed"
    assert acquisition.calls.count("acquire") == 2
    assert acquisition.calls.count("resolve") == 1
    assert retried["result"]["material_gaps"] == ["Optional presentation is unreadable."]


def test_missing_call_preserves_other_materials_and_retry_checkpoints(setup):
    service, acquisition, _ = setup
    acquisition.missing = {"transcript"}
    first = complete(service)
    assert first["status"] == "partial"
    assert first["result"]["materials"]
    acquisition.missing.clear()
    service.store.retry(first["id"])
    asyncio.run(service.execute(first["id"]))
    run = service.detail(first["id"])
    assert run["status"] == "completed", run
    assert acquisition.calls.count("resolve") == 1
    assert acquisition.calls.count("locate") == 1
    assert acquisition.calls.count("acquire") == 2
    assert next(s for s in run["steps"] if s["id"] == "transcript")["attempts"] == 2


def test_discovery_failure_is_failed_not_success_and_retry_resumes(setup):
    service, acquisition, _ = setup
    acquisition.fail_locate = True
    first = complete(service)
    assert first["status"] == "failed"
    assert any("Latest period" in gap for gap in first["result"]["gaps"])
    assert "acquire" not in acquisition.calls
    acquisition.fail_locate = False
    service.store.retry(first["id"])
    asyncio.run(service.execute(first["id"]))
    assert service.detail(first["id"])["status"] == "completed"
    assert acquisition.calls.count("resolve") == 1


def test_duplicate_active_requests_coalesce_and_explicit_keys_validate(setup):
    service, _, _ = setup
    first = service.store.create(" acme ", idempotency_key="request-001")
    assert first == service.store.create("ACME")
    assert first == service.store.create("ACME", idempotency_key="request-001")
    assert first != service.store.create("ACME", namespace="demo")
    with pytest.raises(ValueError, match="different ticker"):
        service.store.create("COST", idempotency_key="request-001")


def test_same_process_restart_resumes_interrupted_checkpoint(setup):
    service, acquisition, repo = setup
    identifier = service.store.create("ACME")
    service.store.step(identifier, "resolve", "completed", output={"ticker": "ACME", "name": "Acme", "cik": "0000001234"})
    service.store.step(identifier, "locate", "running")
    restarted = ResearchWorkflows(repo, None, service.config, acquisition_factory=lambda namespace: acquisition, trends_factory=Trends)
    assert restarted.store.recover() == [identifier]
    asyncio.run(restarted.execute(identifier))
    assert restarted.detail(identifier)["status"] == "completed"
    assert "resolve" not in acquisition.calls
    assert next(s for s in restarted.detail(identifier)["steps"] if s["id"] == "locate")["attempts"] == 2


def test_user_cancellation_is_terminal_and_retryable(setup):
    service, acquisition, _ = setup
    async def scenario():
        acquisition.block = asyncio.Event()
        identifier = service.store.create("ACME")
        service.schedule(identifier)
        for _ in range(50):
            if "resolve" in acquisition.calls:
                break
            await asyncio.sleep(.01)
        result = await service.cancel(identifier)
        assert result["status"] == "cancelled"
        assert not any(s["status"] == "running" for s in result["steps"])
        assert identifier not in service.store.recover()
        acquisition.block = None
        service.store.retry(identifier)
        await service.execute(identifier)
        assert service.detail(identifier)["status"] == "completed"
    asyncio.run(scenario())


def test_handoff_uses_exact_archived_sources_and_is_idempotent(setup):
    service, _, repo = setup
    scheduled = []
    service.engine = SimpleNamespace(schedule=scheduled.append)
    run = complete(service)
    first = service.handoff(run["id"])
    assert service.handoff(run["id"])["run_id"] == first["run_id"]
    assert len(scheduled) == 1
    with repo.db.operation() as conn:
        stored = conn.execute("SELECT ticker,origin_ref,input_snapshot_json FROM runs WHERE id=?", (first["run_id"],)).fetchone()
    assert stored["ticker"] == "ACME"
    assert stored["origin_ref"] == "workflow:" + run["id"]
    assert all(source_id in stored["input_snapshot_json"] for source_id in run["result"]["source_ids"])


def test_partial_handoff_keeps_gaps_and_respects_firm_pause(setup):
    service, acquisition, repo = setup
    acquisition.missing = {"current_filing"}
    run = complete(service)
    service.engine = SimpleNamespace(schedule=lambda _: pytest.fail("Paused engine must not dispatch"))
    with repo.db.transaction(immediate=True) as conn:
        conn.execute("INSERT OR REPLACE INTO app_settings(key,value_json,updated_at) VALUES('firm_paused','true','2026-09-25')")
    handoff = service.handoff(run["id"])
    assert handoff["status"] == "paused"
    with repo.db.operation() as conn:
        question = conn.execute("SELECT request FROM runs WHERE id=?", (handoff["run_id"],)).fetchone()[0]
    assert "current_filing has not been published" in question
    with pytest.raises(ValueError, match="complete|lineage"):
        service.store.retry(run["id"])


def test_api_validation_mutation_guard_and_history(setup):
    service, _, _ = setup
    app = FastAPI()
    async def guard():
        raise HTTPException(403, "blocked")
    app.include_router(create_workflow_router(service, guard))
    with TestClient(app) as client:
        assert client.get("/api/research-workflows/catalog").json()["items"][0]["id"] == "earnings"
        assert client.get("/api/research-workflows/runs/nope").status_code == 404
        assert client.post("/api/research-workflows/runs", json={"ticker": "COST"}).status_code == 403
    app = FastAPI()
    app.include_router(create_workflow_router(service))
    with TestClient(app) as client:
        assert client.post("/api/research-workflows/runs", json={"ticker": "COST; DROP"}).status_code == 422
        assert client.get("/api/research-workflows/runs?namespace=private").status_code == 422
        assert client.get("/api/research-workflows/runs?limit=1000").status_code == 422
        assert client.post("/api/research-workflows/runs/nope/retry").status_code == 404


def test_cross_namespace_sources_are_rejected_before_analysis(setup):
    service, acquisition, _ = setup
    run = complete(service, namespace="demo")
    assert run["status"] == "partial"
    assert not run["result"]["analyses"]
    assert any("another namespace" in gap for gap in run["result"]["gaps"])


def test_workflow_checkpoints_are_in_database_backup(setup, tmp_path):
    service, _, repo = setup
    run = complete(service)
    path = tmp_path / "snapshot.sqlite3"
    repo.db.backup_to(path)
    import sqlite3
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT status FROM research_workflow_runs WHERE id=?", (run["id"],)).fetchone()[0] == "completed"
        assert conn.execute("SELECT COUNT(*) FROM research_workflow_steps WHERE run_id=?", (run["id"],)).fetchone()[0] == 8


def test_constructor_errors_are_saved_as_failures(setup):
    service, _, _ = setup
    def broken_factory(namespace):
        raise RuntimeError("Connector setup failed")
    service.acquisition_factory = broken_factory
    run = complete(service)
    assert run["status"] == "failed"
    assert run["error"] == "Connector setup failed"


def test_old_recipe_steps_remain_readable_after_version_change(setup):
    service, _, repo = setup
    identifier = service.store.create("ACME")
    with repo.db.transaction(immediate=True) as conn:
        conn.execute("UPDATE research_workflow_runs SET version='earnings.v0' WHERE id=?", (identifier,))
        conn.execute("UPDATE research_workflow_steps SET agent_id='old_resolver' WHERE run_id=? AND agent_id='resolve'", (identifier,))
    assert any(s["id"] == "old_resolver" for s in service.detail(identifier)["steps"])
    asyncio.run(service.execute(identifier))
    assert service.detail(identifier)["status"] == "failed"
    with pytest.raises(ValueError, match="version changed"):
        service.store.retry(identifier)


def test_optional_filing_coverage_remains_explicit_without_blocking_earnings(setup, monkeypatch):
    service, _, _ = setup
    from backend.app.research import workflows
    original = workflows.compare_filings
    risk_only = "Form 10-Q\nPART II\nItem 1A. Risk Factors\nSupply constraints could adversely affect sales and customer demand. Manufacturing delays continue to constrain our distribution network."
    monkeypatch.setattr(workflows, "compare_filings", lambda *a, **kw: original(risk_only, risk_only, **kw))
    run = complete(service)
    assert run["status"] == "completed"
    assert any("MD&A" in gap for gap in run["result"]["comparison_gaps"])
    assert run["result"]["analyses"]["filing_comparison"]["analysis_version"] == "local-filing-comparison.v2"


def test_cancelled_analysis_does_not_write_late_history(setup, monkeypatch):
    service, acquisition, _ = setup
    from backend.app.research import workflows
    import threading
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    original = workflows.analyze_transcript
    def slow(*args):
        started.set()
        release.wait(5)
        try:
            return original(*args)
        finally:
            finished.set()
    monkeypatch.setattr(workflows, "analyze_transcript", slow)
    acquisition.missing = {"current_filing"}
    async def scenario():
        identifier = service.store.create("ACME")
        service.schedule(identifier)
        assert await asyncio.to_thread(started.wait, 5)
        await service.cancel(identifier)
        release.set()
        assert await asyncio.to_thread(finished.wait, 5)
        assert not service.analyses.history()
        assert service.detail(identifier)["status"] == "cancelled"
    asyncio.run(scenario())


def test_api_post_schedules_and_returns_durable_result(setup):
    service, _, _ = setup
    app = FastAPI()
    app.include_router(create_workflow_router(service))
    with TestClient(app) as client:
        response = client.post("/api/research-workflows/runs", json={"ticker": "acme"})
        assert response.status_code == 202
        identifier = response.json()["id"]
        import time
        for _ in range(100):
            run = client.get("/api/research-workflows/runs/" + identifier).json()
            if run["status"] not in {"queued", "running"}:
                break
            time.sleep(.01)
        assert run["status"] == "completed"
        assert client.get("/api/research-workflows/runs").json()["items"][0]["id"] == identifier


def test_document_history_keeps_demo_analysis_out_of_real_default(setup):
    service, acquisition, _ = setup
    real = complete(service)
    acquisition.namespace = "demo"
    demo = complete(service, namespace="demo")
    from backend.app.api.document_intelligence import create_document_router
    app = FastAPI()
    app.include_router(create_document_router(service.config))
    with TestClient(app) as client:
        rows = client.get("/api/document-analysis/history").json()["items"]
        assert len(rows) == 2
        assert all(r["workflow_run_id"] == real["id"] for r in rows)
        rows = client.get("/api/document-analysis/history?namespace=demo").json()["items"]
        assert len(rows) == 2
        assert all(r["workflow_run_id"] == demo["id"] for r in rows)


def test_transcript_detects_qa_from_analyst_without_editorial_heading():
    from backend.app.research.document_intelligence import analyze_transcript
    result = analyze_transcript(TRANSCRIPT.replace("Questions and Answers\n", ""), "ACME")
    assert next(row for row in result["sentences"] if row["role"] == "analyst")["section"] == "Q&A"
    assert result["sentences"][-1]["section"] == "Q&A"
    assert result["sentences"][0]["section"] == "Prepared remarks"


def test_automatic_filing_html_excludes_hidden_xbrl():
    from backend.app.research.discovery import _PageTextParser
    parser = _PageTextParser()
    parser.feed('<html><ix:hidden><ix:nonFraction>987654321</ix:nonFraction></ix:hidden><p>Management expects demand to improve.</p></html>')
    text = ''.join(parser.parts)
    assert '987654321' not in text
    assert 'Management expects demand' in text


def test_transcript_analysis_reextracts_canonical_archive_without_source_churn(setup):
    service, _, repo = setup
    from backend.app.research.earnings_sources import ACQUISITION_VERSION
    import hashlib
    spoken = "Operator\nWelcome everyone to the call.\n" + TRANSCRIPT + "\nOperator\nThis concludes today's call. You may now disconnect."
    raw = "ACME Q3 2026\nEditorial summary is weak and disappointing.\n" + spoken + "\nNewsletter sign up."
    url = "https://example.com/canonical-transcript"
    page = FetchedSource(url, url, raw, "Acme earnings", "2026-07-15T00:00:00Z")
    archived = archive_public_page(repo, page, namespace="real", scope="generic-discovery")
    doc = {"status": "available", "source_id": archived["source_id"], "url": url, "extraction_version": ACQUISITION_VERSION}
    output = service._analyze({"id": "test", "namespace": "real", "ticker": "ACME"}, "transcript", {"acquire": {"documents": {"transcript": doc}}, "locate": {}})
    assert "Editorial summary" not in output["record_request"]["text"]
    assert "Newsletter" not in output["record_request"]["text"]
    assert output["source_lineage"][0]["content_hash"] == hashlib.sha256(raw.encode()).hexdigest()
    assert output["source_lineage"][0]["analysis_input_hash"] == hashlib.sha256(output["record_request"]["text"].encode()).hexdigest()
    repeated = archive_public_page(repo, page, namespace="real", scope="earnings:transcript")
    assert repeated["source_id"] == archived["source_id"]


def test_saved_workflow_and_manual_history_expose_full_conversation_without_rewriting(setup):
    service, _, _ = setup
    run = complete(service)
    identifier = run["result"]["analyses"]["transcript"]["id"]
    import json
    path = service.analyses.directory / (identifier + ".json")
    saved = json.loads(path.read_text())
    saved["result"].pop("reading_context", None)
    for sentence in saved["result"]["sentences"]:
        for key in ("turn_id", "exchange_id", "statement_kind"):
            sentence.pop(key, None)
    path.write_text(json.dumps(saved))
    before = path.read_bytes()
    from backend.app.api.document_intelligence import create_document_router
    app = FastAPI()
    app.include_router(create_workflow_router(service))
    app.include_router(create_document_router(service.config))
    with TestClient(app) as client:
        result = client.get("/api/research-workflows/runs/" + run["id"]).json()["result"]["analyses"]["transcript"]["result"]
        context = result["reading_context"]
        assert context["full_text_available"]
        assert context["transcript_text"] == TRANSCRIPT
        exchange = context["exchanges"][0]
        assert exchange["answered"]
        turns = {t["id"]: t for t in context["turns"]}
        assert any("Do you expect growth" in turns[key]["text"] for key in exchange["question_turn_ids"])
        assert any("confident about growth" in turns[key]["text"] for key in exchange["answer_turn_ids"])
        manual = client.get("/api/document-analysis/history/" + identifier).json()
        assert manual["result"]["reading_context"] == context
    assert path.read_bytes() == before


def test_earnings_research_handoff_reviews_existing_position_without_assuming_holdings(setup):
    service, _, repo = setup
    run = complete(service)
    result = service.handoff(run["id"])
    with repo.db.operation() as conn:
        question = conn.execute("SELECT request FROM runs WHERE id=?", (result["run_id"],)).fetchone()[0]
    assert "existing shareholder's thesis" in question
    assert "complete management answers" in question
    assert "do not assume a holding" in question
    assert "conditional review questions" in question


def test_trend_sources_join_package_and_handoff(setup):
    service, acquisition, repo = setup
    page = FetchedSource('https://example.com/prior-capex', 'https://example.com/prior-capex', 'Acme planned $4 billion of capital spending for FY2025.', 'Prior guidance', '2024-10-01T12:00:00Z')
    archived = archive_public_page(repo, page, namespace='real', scope='test-trends')
    class HistoricalTrends:
        def __init__(self, acquisition):
            pass
        async def collect(self, company, event, documents):
            assert company['ticker'] == 'ACME'
            assert documents['transcript']['source_id']
            assert 'content' not in documents['transcript']
            return {'version': 'earnings-trends.v1', 'status': 'available', 'series': [], 'sources': [{'source_id': archived['source_id'], 'url': page.final_url}], 'gaps': []}
    service.trends_factory = HistoricalTrends
    run = complete(service)
    assert run['version'] == 'earnings.v2'
    assert archived['source_id'] in run['result']['source_ids']
    handoff = service.handoff(run['id'])
    with repo.db.operation() as conn:
        row = conn.execute('SELECT request,input_snapshot_json FROM runs WHERE id=?', (handoff['run_id'],)).fetchone()
    assert archived['source_id'] in row['input_snapshot_json']
    assert 'prior dated guidance' in row['request']


def test_trend_failure_preserves_call_and_retries_only_unfinished_work(setup):
    service, acquisition, _ = setup
    class FailingTrends:
        def __init__(self, acquisition):
            pass
        async def collect(self, *args):
            raise ValueError('Historical sources unavailable')
    service.trends_factory = FailingTrends
    first = complete(service)
    assert first['status'] == 'partial'
    assert first['result']['analyses']['transcript']['result']['reading_context']
    assert any('Historical sources unavailable' in gap for gap in first['result']['gaps'])
    service.trends_factory = Trends
    service.store.retry(first['id'])
    asyncio.run(service.execute(first['id']))
    assert service.detail(first['id'])['status'] == 'completed'
    assert acquisition.calls == ['resolve', 'locate', 'acquire']


def test_call_is_readable_while_historical_trends_are_running(setup):
    service, _, _ = setup
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        class WaitingTrends:
            def __init__(self, acquisition):
                pass
            async def collect(self, *args):
                started.set()
                await release.wait()
                return {'status': 'available', 'series': [], 'sources': [], 'gaps': []}
        service.trends_factory = WaitingTrends
        identifier = service.store.create('ACME')
        task = asyncio.create_task(service.execute(identifier))
        await started.wait()
        for _ in range(100):
            run = service.detail(identifier)
            if run['result'].get('analyses', {}).get('transcript'):
                break
            await asyncio.sleep(.01)
        assert run['status'] == 'running'
        assert run['result']['analyses']['transcript']['result']['reading_context']
        assert run['result']['materials']
        assert run['result']['trends'] is None
        release.set()
        await task
    asyncio.run(scenario())
