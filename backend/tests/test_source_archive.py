from pathlib import Path
import hashlib
import asyncio

from backend.app.config import Settings
from backend.app.memory.repository import Repository
from backend.app.research.discovery import FetchedSource
from backend.app.research.documents import document_record
from backend.app.research.source_archive import archive_public_page
from backend.app.research.source_refresh import refresh_watch_sources
from backend.app.orchestration.workflow import build_research_tasks
from backend.app.schemas import RunCreate
import backend.app.research.source_refresh as refresh_module


def repo(tmp_path):
    return Repository(config=Settings(project_root=Path(__file__).resolve().parents[2],data_dir=tmp_path,enable_market_connectors=False,enable_reddit_intake=False))


def page(content="EXMP report text version one.",**changes):
    values={"requested_url":"https://issuer.example.test/report","final_url":"https://issuer.example.test/report","content":content,"title":"Synthetic retained report","retrieved_at":"2026-09-17T00:00:00Z"}
    return FetchedSource(**(values|changes))


def test_changed_content_at_same_url_creates_immutable_supersession_and_replay_is_unchanged(tmp_path):
    repository=repo(tmp_path)
    first=archive_public_page(repository,page(),namespace="real",scope="test")
    next_page=page("EXMP report text version two.")
    second=archive_public_page(repository,next_page,namespace="real",scope="test")
    assert second["status"] == "updated" and second["source_id"] != first["source_id"]
    assert repository.source_head_ids("real",[first["source_id"]]) == [second["source_id"]]
    before=repository.sources("real",second["source_id"])[0]
    assert archive_public_page(repository,next_page,namespace="real",scope="test")["status"] == "unchanged"
    assert repository.sources("real",second["source_id"])[0] == before
    assert repository.sources("real",first["source_id"])[0]["content"] == page().content


def test_unreadable_pdf_retains_original_but_cannot_supply_analysis_source(tmp_path):
    repository=repo(tmp_path)
    raw=b"%PDF-synthetic unreadable original"
    failed=page("",error="No readable text.",original_bytes=raw,document_metadata={"mime_type":"application/pdf","original_hash":hashlib.sha256(raw).hexdigest(),"status":"unavailable","reason":"No readable text."})
    archived=archive_public_page(repository,failed,namespace="real",scope="test")
    assert archived["status"] == "unavailable" and archived["source_id"] is None
    assert archived["retained_source_id"]
    assert document_record(repository,"real",archived["retained_source_id"],include_bytes=True)["bytes"] == raw
    assert "No document claims" in repository.sources("real",archived["retained_source_id"])[0]["content"]


def test_active_case_deferral_preserves_other_case_refresh_and_does_not_duplicate_own_graph(tmp_path,monkeypatch):
    repository=repo(tmp_path)
    first=archive_public_page(repository,page(),namespace="real",scope="first")
    runs=[]
    for index in range(2):
        request=RunCreate(question=f"Synthetic dependent case {index}",namespace="real",source_ids=[first["source_id"]],idempotency_key=f"source-dependent-{index}")
        run,_=repository.create_run(request,build_research_tasks(request.question,None,None,"real",lean=True),allow_semantic_reuse=False)
        runs.append(run["run_id"])
    with repository.db.transaction(immediate=True) as conn:
        conn.execute("UPDATE tasks SET status='completed'")
        conn.execute("UPDATE runs SET status='completed'")
        before=conn.execute("SELECT count(*) FROM tasks WHERE run_id=?",(runs[0],)).fetchone()[0]
    calls=[]
    def fake_fetch(urls,**kwargs):
        calls.extend(urls)
        return [page("EXMP updated public evidence.")]
    monkeypatch.setattr(refresh_module,"fetch_public_pages",fake_fetch)
    refreshed=asyncio.run(refresh_watch_sources(repository,"real",runs[0],[first["source_id"]]))
    assert refreshed["status"] == "updated" and refreshed["changed"]
    assert refreshed["refresh_run_ids"] == [runs[1]]
    with repository.db.operation() as conn:
        assert conn.execute("SELECT count(*) FROM tasks WHERE run_id=?",(runs[0],)).fetchone()[0] == before
        assert conn.execute("SELECT count(*) FROM invalidations WHERE run_id=?",(runs[0],)).fetchone()[0] > 0
        assert conn.execute("SELECT count(*) FROM tasks WHERE run_id=?",(runs[1],)).fetchone()[0] > before
    assert len(calls) == 1
    repository.control("firm",None,"pause")
    assert asyncio.run(refresh_watch_sources(repository,"real",runs[0],[first["source_id"]]))["status"] == "paused"
    assert len(calls) == 1
