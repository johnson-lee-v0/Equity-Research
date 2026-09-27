from __future__ import annotations

import json
import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.research.library import create_library_router, import_desk


def test_library_retains_revisions_and_never_executes_legacy_tasks(tmp_path):
    original = tmp_path / "original.sqlite3"
    with sqlite3.connect(original) as db:
        db.executescript("""
            CREATE TABLE ideas(id TEXT PRIMARY KEY, data TEXT, updated_at TEXT);
            CREATE TABLE packets(id INTEGER PRIMARY KEY, idea_id TEXT, data TEXT);
            CREATE TABLE events(id INTEGER PRIMARY KEY, idea_id TEXT, data TEXT);
        """)
        db.execute("INSERT INTO ideas VALUES(?,?,?)", ("idea", json.dumps({"ticker": "ABC", "name": "Example", "packet": {"summary": "Retained conclusion"}}), "2026-01-01"))
        for i in (1, 2):
            db.execute("INSERT INTO packets VALUES(?,?,?)", (i, "idea", json.dumps({"summary": f"Revision {i}"})))
        db.execute("INSERT INTO events VALUES(?,?,?)", (1, "idea", '{"summary":"Original event"}'))
    before = original.read_bytes()
    evidence = tmp_path / "evidence"
    assert import_desk(original, evidence) == {"ideas": 1, "packets": 2, "events": 1}
    assert original.read_bytes() == before
    app = FastAPI()
    app.include_router(create_library_router(evidence))
    with TestClient(app) as client:
        assert client.get("/api/research-library?q=abc").json()["items"][0]["revisions"] == 2
        assert not client.get("/api/research-library?q=missing").json()["items"]
        detail = client.get("/api/research-library/idea").json()
        assert [p["data"]["summary"] for p in detail["packets"]] == ["Revision 2", "Revision 1"]
        assert detail["historical"] is True
        assert client.get("/api/research-library/unknown").status_code == 404
    original.unlink()
    with TestClient(app) as client:
        assert client.get("/api/research-library").json()["total"] == 1


def test_empty_library_does_not_seed_fake_records(tmp_path):
    app = FastAPI()
    app.include_router(create_library_router(tmp_path))
    with TestClient(app) as client:
        assert client.get("/api/research-library").json() == {"items": [], "imported": False, "total": 0}

import pytest

from backend.app.config import Settings
from backend.app.memory.repository import Repository
from backend.app.research.library_store import LibraryStore, mentioned_tickers, normalize_question, register_ticker


@pytest.fixture
def repo(tmp_path):
    return Repository(config=Settings(data_dir=tmp_path / "live"))


def saved_run(repo, identifier="run_test", ticker="COST", namespace="real", request="Research COST", status="queued"):
    with repo.db.transaction() as conn:
        conn.execute("INSERT INTO runs(id,idempotency_key,namespace,request,ticker,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)", (identifier, identifier, namespace, request, ticker, status, "2026-09-20T00:00:00Z", "2026-09-20T00:00:00Z"))
    return identifier


def saved_source(repo, identifier="src_current", namespace="real"):
    with repo.db.transaction() as conn:
        conn.execute("INSERT INTO sources(id,namespace,source_type,url,title,retrieval_at,content_hash,original_content,created_at) VALUES(?,?,?,?,?,?,?,?,?)", (identifier, namespace, "document", "https://example.com/" + identifier, "Evidence " + identifier, "2026-09-20", identifier, "Renewal rate was 92%.", "2026-09-20"))


def saved_output(repo, run_id, identifier, payload, when="2026-09-21T00:00:00Z", source_ids=None):
    task, attempt = "task_" + identifier, "attempt_" + identifier
    with repo.db.transaction() as conn:
        conn.execute("INSERT INTO tasks(id,run_id,agent_id,kind,instruction,status,sequence_no,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)", (task, run_id, "A11", identifier, "Research", "completed", 1, when, when))
        conn.execute("INSERT INTO task_attempts(id,task_id,attempt_no,status,provider,model,prompt_version,output_schema_version,started_at) VALUES(?,?,?,?,?,?,?,?,?)", (attempt, task, 1, "completed", "test", "test", "1", "1", when))
        frozen = {row["id"]: {"version": 1, "hash": row["content_hash"]} for row in conn.execute("SELECT id,content_hash FROM sources WHERE namespace='real'") if source_ids is None or row["id"] in source_ids}
        conn.execute("UPDATE task_attempts SET source_versions_json=? WHERE id=?", (json.dumps(frozen), attempt))
        conn.execute("INSERT INTO outputs(id,task_id,attempt_id,agent_id,version,status,conclusion,payload_json,provenance,output_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (identifier, task, attempt, "A11", 1, "completed", payload.get("summary", "Saved answer"), json.dumps(payload), "real", identifier, when))


def library_client(repo, evidence):
    app = FastAPI()
    app.include_router(create_library_router(evidence, repo=repo))
    return TestClient(app)


def test_registration_is_durable_idempotent_and_namespace_scoped(repo):
    run = saved_run(repo)
    with repo.db.transaction() as conn:
        first = register_ticker(conn, "real", "cost", origin="research", origin_ref=run, run_id=run)
        again = register_ticker(conn, "real", "$COST", origin="research", origin_ref=run, run_id=run)
        demo = register_ticker(conn, "demo", "COST", origin="mention", origin_ref="demo")
        assert first == again and demo != first
        assert conn.execute("SELECT COUNT(*) FROM research_library_mentions").fetchone()[0] == 2
        with pytest.raises(ValueError, match="namespace"):
            register_ticker(conn, "demo", "COST", origin="research", origin_ref=run, run_id=run)
        assert register_ticker(conn, "real", "unknown", origin="x", origin_ref="x") is None
    with library_client(repo, repo.config.evidence_dir) as client:
        assert [i["ticker"] for i in client.get("/api/research-library").json()["items"]] == ["COST"]
        assert client.get(f"/api/research-library/{first}?namespace=demo").status_code == 404
        assert client.get("/api/research-library?namespace=wrong").status_code == 422
        assert client.get(f"/api/research-library/{first}").json()["runs"][0]["id"] == run


def test_backfill_captures_tickerless_questions_and_candidates_once(repo):
    run = saved_run(repo, ticker=None, request="Compare COST and MSFT earnings, EPS and AI demand")
    saved_output(repo, run, "out_candidates", {"candidate_briefs": [{"ticker": "NVDA", "instrument": "NVIDIA"}]})
    store = LibraryStore(repo)
    assert store.backfill() == 3
    assert store.backfill() == 0
    items = {item["ticker"]: item for item in store.list()}
    assert set(items) == {"COST", "MSFT", "NVDA"}
    assert items["NVDA"]["name"] == "NVIDIA"
    assert store.detail(items["NVDA"]["id"])["runs"][0]["id"] == run
    assert mentioned_tickers("Is $BRK.B or NYSE:COST preferable? CEO and EPS are not tickers.") == ["BRK.B", "COST"]


def test_answers_update_live_and_keep_citations_attached_to_correct_question(repo):
    run = saved_run(repo, status="completed")
    saved_source(repo)
    saved_source(repo, "src_other")
    saved_source(repo, "src_demo", namespace="demo")
    first = {"ticker": "COST", "key_questions": [{"key": "opportunity", "answer": "Membership grew. [src_current L1]"}, {"key": "downside", "answer": "Unresolved competition.", "source_refs": ["src_missing", "src_demo"]}]}
    saved_output(repo, run, "out_first", {"candidate_briefs": [first], "source_refs": ["src_other", "src_current"]})
    store = LibraryStore(repo)
    store.backfill()
    identifier = store.list()[0]["id"]
    before = store.detail(identifier)
    assert len(before["questions"]) == 2
    opportunity, downside = before["questions"]
    assert [s["id"] for s in opportunity["sources"]] == ["src_current"]
    assert opportunity["answer_segments"][-1]["citation_numbers"] == [1]
    assert downside["sources"] == []
    assert downside["citation_gaps"]
    assert all(s["id"] != "src_other" for q in before["questions"] for s in q["sources"])
    updated = {"ticker": "COST", "key_questions": [{"key": "opportunity", "answer": "New quarter adds evidence. [src_other L2]"}]}
    saved_output(repo, run, "out_second", {"candidate_briefs": [updated]}, when="2026-09-22T00:00:00Z")
    after = store.detail(identifier)
    assert after["questions"][0]["answer"].startswith("New quarter")
    assert after["questions"][0]["sources"][0]["id"] == "src_other"
    assert len(after["packets"]) == 2
    assert before["packets"][0]["data"] == after["packets"][1]["data"]


def test_legacy_and_live_company_merge_without_overwriting_snapshot(repo, tmp_path):
    original = tmp_path / "desk.sqlite3"
    with sqlite3.connect(original) as conn:
        conn.executescript("CREATE TABLE ideas(id TEXT PRIMARY KEY,data TEXT,updated_at TEXT); CREATE TABLE packets(id INTEGER PRIMARY KEY,idea_id TEXT,data TEXT); CREATE TABLE events(id INTEGER PRIMARY KEY,idea_id TEXT,data TEXT);")
        packet = {"summary": "Historical conclusion", "questions": [{"question": "Is retention stable?", "answer": "Earlier evidence said yes.", "sources": [{"label": "Original result", "url": "https://example.com/historical"}], "uncertainty": "Historical only."}]}
        conn.execute("INSERT INTO ideas VALUES(?,?,?)", ("old-cost", json.dumps({"ticker": "COST", "name": "Costco", "packet": packet}), "2026-01-01"))
        conn.execute("INSERT INTO packets VALUES(?,?,?)", (1, "old-cost", json.dumps(packet)))
    import_desk(original, repo.config.evidence_dir)
    retained = (repo.config.evidence_dir / "research-library/desk.sqlite3").read_bytes()
    saved_run(repo)
    LibraryStore(repo).backfill()
    with library_client(repo, repo.config.evidence_dir) as client:
        items = client.get("/api/research-library").json()["items"]
        assert len(items) == 1 and items[0]["id"] == "old-cost"
        assert items[0]["live"] is True and items[0]["historical"] is True
        detail = client.get("/api/research-library/old-cost").json()
        assert detail["questions"][0]["answer_segments"] == [{"text": "Earlier evidence said yes.", "citation_numbers": [1]}]
        assert detail["questions"][0]["uncertainty"] == "Historical only."
        assert detail["runs"][0]["status"] == "queued"
        assert client.get("/api/research-library?namespace=demo").json()["items"] == []
        assert client.get("/api/research-library/old-cost?namespace=demo").status_code == 404
    assert (repo.config.evidence_dir / "research-library/desk.sqlite3").read_bytes() == retained


def test_verified_fact_citation_does_not_assign_unrelated_source():
    question = normalize_question({"key": "downside", "answer": "Renewals weakened.", "verified_facts": [{"source_ref": "src_renewal", "locator": "L4"}]}, identifier="q", origin="research", source_lookup={"src_renewal": {"id": "src_renewal", "url": "https://example.com/r", "title": "Renewal"}, "src_unrelated": {"id": "src_unrelated", "url": "https://example.com/u"}})
    assert question["sources"] == [{"number": 1, "id": "src_renewal", "url": "https://example.com/r", "title": "Renewal", "publisher": None, "published_at": None, "quote": "", "locator": "L4"}]
    assert question["answer_segments"][0]["citation_numbers"] == [1]


def test_citations_are_bound_to_attempt_packet_and_preserve_original_locator(repo):
    run = saved_run(repo)
    saved_source(repo, "src_attached")
    saved_source(repo, "src_elsewhere")
    candidate = {"ticker": "COST", "key_questions": [{"key": "opportunity", "answer": "Attached finding. [src_attached L20-L24] Outside finding. [src_elsewhere L8]"}]}
    saved_output(repo, run, "out_scoped", {"candidate_briefs": [candidate]}, source_ids=["src_attached"])
    store = LibraryStore(repo)
    store.backfill()
    question = store.detail(store.list()[0]["id"])["questions"][0]
    assert [s["id"] for s in question["sources"]] == ["src_attached"]
    assert question["sources"][0]["locator"] == "L20-L24"
    assert question["sources"][0]["source_version"] == 1
    assert question["sources"][0]["content_hash"] == "src_attached"
    assert question["citation_gaps"]
    assert any(s["text"] == "[Source unavailable]" for s in question["answer_segments"])


def test_wrong_source_hash_is_not_cited_from_current_catalog(repo):
    run = saved_run(repo)
    saved_source(repo)
    saved_output(repo, run, "out_hash", {"candidate_briefs": [{"ticker": "COST", "key_questions": [{"key": "opportunity", "answer": "Finding. [src_current L2]"}]}]})
    with repo.db.transaction() as conn:
        conn.execute("UPDATE task_attempts SET source_versions_json=? WHERE id=?", (json.dumps({"src_current": {"version": 1, "hash": "wrong-version-hash"}}), "attempt_out_hash"))
    store = LibraryStore(repo)
    store.backfill()
    question = store.detail(store.list()[0]["id"])["questions"][0]
    assert question["sources"] == []
    assert question["citation_gaps"]


def test_foreign_proposed_fact_cannot_become_a_verified_question_citation(repo):
    run = saved_run(repo)
    saved_source(repo)
    saved_output(repo, run, "out_foreign", {"candidate_briefs": [{"ticker": "COST", "entry_advice": "Earlier opinion"}]})
    with repo.db.transaction() as conn:
        conn.execute("INSERT INTO fact_claims(id,namespace,subject,predicate,source_id,locator,status,created_at) VALUES(?,?,?,?,?,?,?,?)", ("fact_foreign", "real", "COST", "Speculation", "src_current", "L1", "proposed", "2026-09-21"))
        conn.execute("INSERT INTO output_claims(output_id,claim_index,fact_id,validation_status,validation_origin) VALUES(?,?,?,?,?)", ("out_foreign", 0, "fact_foreign", "proposed", "recorded"))
    saved_output(repo, run, "out_current", {"candidate_briefs": [{"ticker": "COST", "key_questions": [{"key": "opportunity", "answer": "Unsupported extrapolation", "supporting_claim_ids": ["fact_foreign"]}]}]}, when="2026-09-22T00:00:00Z")
    store = LibraryStore(repo)
    store.backfill()
    question = store.detail(store.list()[0]["id"])["questions"][0]
    assert question["sources"] == []
    assert question["status"] == "unavailable"
    assert "could not be verified or is out of date" in question["unknowns"][0]
    assert "unresolved or stale" in question["audit"]["unknowns"][0]


def test_nested_questions_are_preserved_for_tickerless_single_company_run(repo):
    run = saved_run(repo, ticker=None, request="Review latest earnings")
    saved_source(repo)
    saved_output(repo, run, "out_nested", {"decision_brief": {"ticker": "COST", "key_questions": [{"key": "opportunity", "answer": "Saved five-question answer. [src_current L9]"}]}})
    store = LibraryStore(repo)
    store.backfill()
    item = store.list()[0]
    detail = store.detail(item["id"])
    assert item["ticker"] == "COST"
    assert detail["questions"][0]["answer"].startswith("Saved five-question")
    assert detail["questions"][0]["sources"][0]["id"] == "src_current"


def test_mention_extraction_excludes_prose_acronyms_and_keeps_explicit_symbols():
    assert mentioned_tickers("hello") == []
    assert mentioned_tickers("WHY BUY NOW") == []
    assert mentioned_tickers("Investigate these saved document findings for COST. Analysis ID: abc. Local NLP covering U.S. and N.Y. earnings.") == ["COST"]
    assert mentioned_tickers("Research $AI and NYSE:COST") == ["AI", "COST"]


def test_backfill_reconciles_only_automatic_false_mentions(repo):
    run = saved_run(repo, ticker=None, request="Research COST\nTechnical note: HTTP 403 and 10-K unavailable")
    with repo.db.transaction() as conn:
        conn.execute("UPDATE runs SET research_instruction=? WHERE id=?", ("A00 should review $AAPL and TSLA as examples for the PM", run))
        for ticker in ("HTTP", "K", "A00", "AAPL", "TSLA", "PM"):
            register_ticker(conn, "real", ticker, origin="research", origin_ref=run, run_id=run)
        register_ticker(conn, "real", "AAPL", origin="watchlist", origin_ref="explicit-watch")
    store = LibraryStore(repo)
    store.backfill()
    assert {i["ticker"] for i in store.list()} == {"COST", "AAPL"}
    assert store.backfill() == 0
    with repo.db.operation() as conn:
        assert conn.execute("SELECT origin FROM research_library_mentions m JOIN research_library_entries e ON e.id=m.entry_id WHERE e.ticker='AAPL'").fetchone()[0] == "watchlist"


def test_management_source_is_scoped_for_live_answers_but_retained_for_history():
    raw = {"question": "What did management say?", "answer": "A saved finding.", "managementAnswer": {"answer": "Quoted answer", "speaker": "CFO", "source": {"id": "src_foreign", "url": "https://unrelated.example/quote"}}}
    live = normalize_question(raw, identifier="q", origin="research", source_lookup={})
    assert live["managementAnswer"]["source"] is None
    assert live["citation_gaps"]
    historical = normalize_question(raw, identifier="q", origin="historical")
    assert historical["managementAnswer"]["source"]["url"] == "https://unrelated.example/quote"


def test_same_second_canonical_decision_wins_over_analyst_output(repo):
    run = saved_run(repo)
    saved_source(repo)
    payload = {"candidate_briefs": [{"ticker": "COST", "key_questions": [{"key": "opportunity", "answer": "Analyst view. [src_current L1]"}]}]}
    saved_output(repo, run, "out_zzzz", payload)
    saved_output(repo, run, "out_aaaa", payload)
    with repo.db.transaction() as conn:
        conn.execute("UPDATE outputs SET agent_id='A03' WHERE id='out_zzzz'")
        canonical = {"candidates": [{"ticker": "COST", "key_questions": [{"key": "opportunity", "answer": "Canonical final view. [src_current L2]"}]}]}
        conn.execute("INSERT INTO case_decision_versions(id,run_id,namespace,output_id,revision,kind,payload_json,created_at) VALUES(?,?,?,?,?,?,?,?)", ("case_aaaa", run, "real", "out_aaaa", 1, "investment", json.dumps(canonical), "2026-09-21T00:00:00Z"))
    store = LibraryStore(repo)
    store.backfill()
    detail = store.detail(store.list()[0]["id"])
    assert detail["questions"][0]["answer"].startswith("Canonical final")
    assert len(detail["packets"]) == 2


def test_generated_pending_question_is_concise_and_original_request_survives(repo):
    original = "Assess COST after earnings. Internal acquisition details HTTP 403 and source IDs."
    run = saved_run(repo, request=original)
    with repo.db.transaction() as conn:
        conn.execute("UPDATE runs SET origin_ref='workflow:latest' WHERE id=?", (run,))
    store = LibraryStore(repo)
    store.backfill()
    item = store.list()[0]
    assert item["summary"] == "What changed for COST after its latest earnings?"
    detail = store.detail(item["id"])
    assert detail["questions"][0]["question"] == item["summary"]
    assert detail["runs"][0]["request"] == original


def test_revision_counts_follow_company_answers_in_shared_run_without_fact_projection(repo, monkeypatch):
    run = saved_run(repo, ticker=None, request="Compare COST, MSFT and NVDA earnings")
    cost = {"ticker": "COST", "key_questions": [{"key": "opportunity", "answer": "Costco's saved finding."}]}
    microsoft = {"ticker": "MSFT", "key_questions": [{"key": "opportunity", "answer": "Microsoft's saved finding."}]}
    saved_output(repo, run, "out_shared", {"candidate_briefs": [cost, microsoft]})
    saved_output(repo, run, "out_msft_only", {"candidate_briefs": [microsoft]}, when="2026-09-22T00:00:00Z")
    saved_output(repo, run, "out_mentions_only", {"research_candidates": [{"ticker": "NVDA", "name": "NVIDIA"}], "routing_plan": {"tickers": ["COST", "MSFT", "NVDA"]}}, when="2026-09-23T00:00:00Z")
    store = LibraryStore(repo)
    store.backfill()
    items = {item["ticker"]: item for item in store.list()}
    assert {ticker: item["revisions"] for ticker, item in items.items()} == {"COST": 1, "MSFT": 2, "NVDA": 0}
    for item in items.values():
        assert item["revisions"] == len(store.detail(item["id"])["packets"])
    with repo.db.transaction() as conn:
        for revision in (1, 2):
            conn.execute("INSERT INTO case_decision_versions(id,run_id,namespace,output_id,projection_key,revision,kind,payload_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)", (f"case_shared_{revision}", run, "real", "out_shared", f"projection-{revision}", revision, "investment", json.dumps({"candidates": [cost]}), f"2026-09-2{revision}T00:00:00Z"))
    items = {item["ticker"]: item for item in store.list()}
    assert {ticker: item["revisions"] for ticker, item in items.items()} == {"COST": 2, "MSFT": 2, "NVDA": 0}
    for item in items.values():
        assert item["revisions"] == len(store.detail(item["id"])["packets"])
    def no_fact_projection(*args, **kwargs):
        raise AssertionError("The directory must not load heavy fact projections.")
    monkeypatch.setattr(repo, "output_dict", no_fact_projection)
    assert {item["ticker"]: item["revisions"] for item in store.list()} == {"COST": 2, "MSFT": 2, "NVDA": 0}


from backend.tests.test_earnings_archive_fallback import case as archive_case
from backend.app.research.earnings_fallback import DISCOVERY_GAP, VALUATION_GAP


def test_coverage_gaps_follow_selected_run_and_revision_without_citations_or_namespace_leak(archive_case):
    repo, run_id, _, _, _, _ = archive_case
    repo.control("run", run_id, "use_archived_evidence")
    store = LibraryStore(repo)
    store.backfill()
    real_id = next(i["id"] for i in store.list() if i["ticker"] == "COST")
    pending = store.detail(real_id)
    assert DISCOVERY_GAP in pending["coverage_gaps"] and VALUATION_GAP in pending["coverage_gaps"]
    assert pending["questions"][0]["sources"] == []
    saved_output(repo, run_id, "out_archive_answer", {"candidate_briefs": [{"ticker": "COST", "key_questions": [{"key": "opportunity", "answer": "A saved conditional conclusion."}]}]})
    with repo.db.operation() as conn:
        immutable_before = tuple(conn.execute("SELECT payload_json,output_hash FROM outputs WHERE id='out_archive_answer'").fetchone())
    answered = store.detail(real_id)
    assert answered["coverage_gaps"] == answered["packets"][0]["coverage_gaps"]
    assert DISCOVERY_GAP in answered["coverage_gaps"]
    assert answered["questions"][0]["answer"] == "A saved conditional conclusion."
    assert answered["questions"][0]["sources"] == []
    assert all(not segment["citation_numbers"] for segment in answered["questions"][0]["answer_segments"])
    saved_run(repo, identifier="run_demo_coverage", namespace="demo")
    current_run = saved_run(repo, identifier="run_new_review")
    saved_output(repo, current_run, "out_new_answer", {"candidate_briefs": [{"ticker": "COST", "key_questions": [{"key": "opportunity", "answer": "New review, separately retained."}]}]}, when="2026-09-22T00:00:00Z")
    store.backfill()
    current = store.detail(real_id)
    assert current["coverage_gaps"] == []
    assert current["packets"][0]["coverage_gaps"] == []
    assert DISCOVERY_GAP in current["packets"][1]["coverage_gaps"]
    demo_id = store.list("demo")[0]["id"]
    assert store.detail(demo_id, "demo")["coverage_gaps"] == []
    assert store.detail(real_id, "demo") is None
    with repo.db.operation() as conn:
        assert tuple(conn.execute("SELECT payload_json,output_hash FROM outputs WHERE id='out_archive_answer'").fetchone()) == immutable_before


def test_library_never_exposes_invalid_archive_receipt_gaps(archive_case):
    repo, run_id, task_id, _, _, _ = archive_case
    repo.control("run", run_id, "use_archived_evidence")
    store = LibraryStore(repo)
    store.backfill()
    identifier = store.list()[0]["id"]
    assert store.detail(identifier)["coverage_gaps"]
    repo.control("task", task_id, "retry")
    assert repo.earnings_archive_fallbacks(run_id) == []
    assert store.detail(identifier)["coverage_gaps"] == []


def test_parenthesized_and_grouped_citations_keep_exact_packet_scope(repo):
    run = saved_run(repo)
    for source in ("src_one", "src_two", "src_unrelated"):
        saved_source(repo, source)
    answer = "First observation (src_one L59-L69). Comparison (src_one L2917; src_two L98-L103). Existing bracket [src_two L111]. Outside packet (src_unrelated L4)."
    saved_output(repo, run, "out_parenthesized", {"candidate_briefs": [{"ticker": "COST", "key_questions": [{"key": "opportunity", "answer": answer}]}]}, source_ids=["src_one", "src_two"])
    store = LibraryStore(repo)
    store.backfill()
    question = store.detail(store.list()[0]["id"])["questions"][0]
    assert [source["id"] for source in question["sources"]] == ["src_one", "src_two"]
    assert [segment["citation_numbers"] for segment in question["answer_segments"] if segment["citation_numbers"]] == [[1], [1, 2], [2]]
    assert question["sources"][0]["locator"] == "L59-L69; L2917"
    assert question["sources"][1]["locator"] == "L98-L103; L111"
    assert question["citation_gaps"]
    assert all("src_" not in segment["text"] for segment in question["answer_segments"])
    assert question["answer"] == answer
    with repo.db.operation() as conn:
        assert json.loads(conn.execute("SELECT payload_json FROM outputs WHERE id='out_parenthesized'").fetchone()[0])["candidate_briefs"][0]["key_questions"][0]["answer"] == answer


def test_unresolved_fact_ids_are_auditable_without_appearing_in_reader_uncertainty():
    unknowns = ["Unresolved evidence: Forward EPS is missing.; reference fact_abc123 is unresolved or stale; reference fact_def456 is unresolved or stale"]
    row = normalize_question({"question": "What is it worth?", "answer": "Value remains uncertain.", "unknowns": unknowns}, identifier="uncertain", origin="research")
    assert row["unknowns"] == ["Evidence limits: Forward EPS is missing.; Some supporting evidence could not be verified or is out of date"]
    assert row["audit"]["unknowns"] == unknowns
    assert "fact_" not in " ".join(row["unknowns"])
    assert row["sources"] == []


def test_ticker_or_placeholder_name_cannot_downgrade_issuer_and_read_model_recovers_prior_name(repo):
    with repo.db.transaction() as conn:
        identifier = register_ticker(conn, "real", "COST", origin="issuer", origin_ref="verified", name="Costco Wholesale Corporation")
        register_ticker(conn, "real", "COST", origin="candidate", origin_ref="brief-1", name="COST")
        register_ticker(conn, "real", "COST", origin="candidate", origin_ref="brief-2", name="Unknown")
        register_ticker(conn, "real", "COST", origin="candidate", origin_ref="brief-3", name="COST (src_placeholder L3)")
        assert conn.execute("SELECT name FROM research_library_entries WHERE id=?", (identifier,)).fetchone()[0] == "Costco Wholesale Corporation"
        conn.execute("INSERT INTO research_workflow_runs(id,workflow,version,namespace,ticker,status,result_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)", ("wf_name", "earnings", "earnings.v2", "real", "COST", "completed", json.dumps({"company": {"ticker": "COST", "name": "Costco Wholesale Corporation"}}), "2026-09-21", "2026-09-21"))
        # Simulate an existing store written before the downgrade guard.
        conn.execute("UPDATE research_library_entries SET name='COST' WHERE id=?", (identifier,))
    store = LibraryStore(repo)
    assert store.list()[0]["name"] == "Costco Wholesale Corporation"
    assert store.detail(identifier)["idea"]["data"]["name"] == "Costco Wholesale Corporation"
    with repo.db.operation() as conn:
        assert conn.execute("SELECT name FROM research_library_entries WHERE id=?", (identifier,)).fetchone()[0] == "COST"


def test_library_price_targets_keep_each_case_revision_and_its_evidence(repo):
    run = saved_run(repo)
    saved_source(repo, "src_earlier")
    saved_source(repo, "src_later")
    saved_source(repo, "src_unrelated")
    for revision, price, source in [(1, "120", "src_earlier"), (2, "150", "src_later")]:
        candidate = {"ticker": "COST", "horizon": "12 months", "key_questions": [{"key": "valuation", "answer": f"Base target {price}."}],
                     "valuation": {"status": "complete", "currency": "USD", "horizon": "12 months", "selected_method": "eps_multiple", "scenarios": {"base": price},
                                   "methods": [{"name": "eps_multiple", "supported": True, "source_refs": [source],
                                                "scenario_calculations": {"base": {"inputs": [{"key": "baseline_eps", "value": "5", "kind": "fact", "source_refs": [source]}], "formula": "EPS × P/E", "output_price": price}}}]},
                     "future_target": {"value": price, "currency": "USD"}, "payoff": {"status": "complete"}}
        saved_output(repo, run, f"out_target_{revision}", {"candidate_briefs": [candidate]}, when=f"2026-09-2{revision}T00:00:00Z", source_ids=[source, "src_unrelated"])
        with repo.db.transaction() as conn:
            conn.execute("INSERT INTO case_decision_versions(id,run_id,namespace,output_id,revision,kind,payload_json,created_at) VALUES(?,?,?,?,?,?,?,?)", (f"case_target_{revision}", run, "real", f"out_target_{revision}", revision, "investment", json.dumps({"candidates": [candidate]}), f"2026-09-2{revision}T00:00:00Z"))
    store = LibraryStore(repo)
    store.backfill()
    detail = store.detail(store.list()[0]["id"])
    newest, earlier = [row["data"] for row in detail["packets"]]
    assert newest["valuation"]["scenarios"]["base"] == "150"
    assert earlier["valuation"]["scenarios"]["base"] == "120"
    assert earlier["valuation"]["methods"][0]["scenario_calculations"]["base"]["formula"] == "EPS × P/E"
    assert earlier["decision_revision"] == 1 and newest["decision_revision"] == 2
    assert [s["id"] for s in earlier["target_sources"]] == ["src_earlier"]
    assert [s["id"] for s in newest["target_sources"]] == ["src_later"]
    assert earlier["target_sources"][0]["content_hash"] == "src_earlier"
    assert earlier["target_sources"][0]["source_version"] == 1
    assert detail["idea"]["data"]["packet"] == newest
    assert earlier["payoff"] == {"status": "complete"}


@pytest.mark.parametrize("inline_rationale", [False, True])
def test_library_target_sources_require_the_frozen_attempt_namespace_and_hash(repo, inline_rationale):
    run = saved_run(repo)
    for identifier, namespace in [("src_attached", "real"), ("src_elsewhere", "real"), ("src_foreign", "demo")]:
        saved_source(repo, identifier, namespace)
    candidate = {"ticker": "COST", "key_questions": [{"key": "valuation", "answer": "Saved assessment."}],
                 "valuation": {"status": "partial", "methods": [{"name": "eps_multiple", "inputs": [{"source_refs": ["src_attached", "src_elsewhere", "src_foreign"]}]}]}}
    if inline_rationale:
        candidate["valuation"]["methods"][0] = {"name": "eps_multiple", "inputs": [], "source_refs": [],
            "scenario_calculations": {"base": {"steps": ["Growth rationale (src_attached:L97-L103,L118-L121)."],
                "inputs": [{"kind": "assumption", "rationale": "Context (src_elsewhere:L5; src_foreign:L6)."}]}}}
    saved_output(repo, run, "out_target_scoped", {"candidate_briefs": [candidate]}, source_ids=["src_attached"])
    with repo.db.transaction() as conn:
        conn.execute("INSERT INTO case_decision_versions(id,run_id,namespace,output_id,revision,kind,payload_json,created_at) VALUES(?,?,?,?,?,?,?,?)", ("case_target_scoped", run, "real", "out_target_scoped", 1, "investment", json.dumps({"candidates": [candidate]}), "2026-09-21"))
    store = LibraryStore(repo)
    store.backfill()
    identifier = store.list()[0]["id"]
    packet = store.detail(identifier)["idea"]["data"]["packet"]
    assert [s["id"] for s in packet["target_sources"]] == ["src_attached"]
    assert len(packet["target_citation_gaps"]) == 2
    with repo.db.transaction() as conn:
        conn.execute("UPDATE task_attempts SET source_versions_json=? WHERE id=?", (json.dumps({"src_attached": {"version": 1, "hash": "invalid"}}), "attempt_out_target_scoped"))
    packet = store.detail(identifier)["idea"]["data"]["packet"]
    assert packet["target_sources"] == []
    assert len(packet["target_citation_gaps"]) == 3


def test_library_does_not_promote_analyst_proposal_to_canonical_calculation(repo):
    run = saved_run(repo)
    saved_source(repo)
    candidate = {"ticker": "COST", "key_questions": [{"key": "valuation", "answer": "Earlier opinion."}],
                 "future_target": {"value": "120", "source_refs": ["src_current"]},
                 "valuation": {"status": "complete", "scenarios": {"base": "999"}},
                 "valuation_assumptions": {"methods": [{"name": "eps_multiple"}]}}
    saved_output(repo, run, "out_proposed_target", {"candidate_briefs": [candidate]})
    store = LibraryStore(repo)
    store.backfill()
    packet = store.detail(store.list()[0]["id"])["idea"]["data"]["packet"]
    assert packet["valuation"] is None
    assert packet["future_target"]["value"] == "120"
    assert packet["target_provenance"] == "saved_analyst_target"
    assert [s["id"] for s in packet["target_sources"]] == ["src_current"]


def test_preparation_output_is_progress_not_a_library_revision(repo):
    previous = saved_run(repo, identifier="run_previous", status="completed")
    saved_output(repo, previous, "out_previous_review", {"candidate_briefs": [{"ticker": "COST", "entry_advice": "Earlier investment conclusion.", "key_questions": [{"key": "valuation", "answer": "Earlier defended answer."}]}]})
    active = saved_run(repo, identifier="run_preparing", status="running")
    saved_output(repo, active, "out_preparation", {"summary": "Evidence compilation is complete.", "candidate_briefs": [{"ticker": "COST", "entry_advice": "Evidence is ready for the reviewer.", "key_questions": [{"key": "valuation", "answer": "Target still needs review."}]}]}, when="2026-09-23T00:00:00Z")
    with repo.db.transaction() as conn:
        conn.execute("UPDATE runs SET created_at='2026-09-23T00:00:00Z',updated_at='2026-09-23T00:00:00Z' WHERE id=?", (active,))
        conn.execute("UPDATE outputs SET agent_id='A03' WHERE id='out_preparation'")
        conn.execute("UPDATE task_attempts SET provider='deterministic',model='earnings-evidence-compiler.v1' WHERE id='attempt_out_preparation'")
    store = LibraryStore(repo)
    store.backfill()
    item = store.list()[0]
    detail = store.detail(item["id"])
    assert item["status"] == "running"
    assert item["revisions"] == 1
    assert item["summary"] == "Earlier investment conclusion."
    assert [row["id"] for row in detail["packets"]] == ["out_previous_review"]
    assert detail["questions"][0]["answer"] == "Earlier defended answer."
    assert detail["runs"][0]["id"] == active
    with repo.db.operation() as conn:
        assert conn.execute("SELECT count(*) FROM outputs WHERE id='out_preparation'").fetchone()[0] == 1
    # The filter is specific to this deterministic preparation artifact.
    # Existing analyst research stays in the library.
    with repo.db.transaction() as conn:
        conn.execute("UPDATE task_attempts SET model='research-analyst' WHERE id='attempt_out_preparation'")
    assert store.list()[0]["revisions"] == 2
    assert store.detail(item["id"])["packets"][0]["id"] == "out_preparation"
