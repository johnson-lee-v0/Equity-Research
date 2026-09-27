"""Durable Reddit screening, recovery, and source-version boundaries."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.app.config import Settings
from backend.app.memory.repository import Repository
from backend.app.schemas import ResearchRequest, RunCreate
from backend.app.orchestration.workflow import build_research_tasks
from backend.app.research.reddit_service import RedditMonitor
from backend.app.db import utc_now


ROOT = Path(__file__).resolve().parents[2]


def make_repository(tmp_path: Path) -> Repository:
    return Repository(config=Settings(data_dir=tmp_path, project_root=ROOT, codex_timeout_seconds=30))


def post(
    key: str,
    *,
    title: str,
    body: str = "",
    score: int = 1,
    flair: str | None = None,
) -> dict[str, object]:
    value: dict[str, object] = {
        "post_id": f"t3_{key}",
        "subreddit": "wallstreetbets",
        "title": title,
        "body": body,
        "permalink": f"https://www.reddit.com/r/wallstreetbets/comments/{key}/",
        "created_at": "2026-09-12T12:00:00Z",
        "score": score,
    }
    if flair is not None:
        value["source_flair"] = flair
    return value


def ingest(repository: Repository, value: dict[str, object]) -> None:
    repository.ingest_reddit_result(
        {"posts": [value], "metadata": {"retrieved_at": "2026-09-13T00:00:00Z"}, "status": "ok"}
    )


def dispatch(repository: Repository, key: str) -> tuple[str, str]:
    item = next(item for item in repository.intake_status(limit=20)["items"] if item["external_id"] == f"t3_{key}")
    result = repository.dispatch_reddit_backlog(max_dispatches=1, max_cost=1, item_id=item["id"])
    run_id = result["dispatched"][0]["run_id"]
    return str(item["id"]), str(run_id)


def route(
    *,
    classification: str,
    reason: str,
    excerpt: str = "",
    summary: str = "",
    tickers: list[str] | None = None,
) -> dict[str, object]:
    return {
        "intent": "research",
        "selected_analysts": ["A03"],
        "research_queries": [],
        "rationale": reason,
        "reddit_triage": {
            "classification": classification,
            "reason": reason,
            "thesis_summary": summary,
            "evidence_excerpt": excerpt,
            "tickers": list(tickers or []),
        },
    }


def test_score_only_observation_preserves_triage_and_identity_hash(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    ingest(repository, post("score", title="TSLA can rise", body="TSLA can rise because deliveries improve.", flair="DD"))
    item_id, run_id = dispatch(repository, "score")
    triage = route(
        classification="thesis",
        reason="The post states an outlook and a supporting reason.",
        excerpt="TSLA can rise because deliveries improve.",
        summary="TSLA may rise as deliveries improve.",
        tickers=["TSLA"],
    )
    repository.consume_routing_plan(run_id, triage)
    before = next(item for item in repository.intake_status(limit=20)["items"] if item["id"] == item_id)
    before_identity = before["triage"]["tickers"]
    with repository.db.operation() as conn:
        before_payload = json.loads(conn.execute("SELECT payload_json FROM intake_items WHERE id=?", (item_id,)).fetchone()[0])
    ingest(repository, post("score", title="TSLA can rise", body="TSLA can rise because deliveries improve.", score=99, flair="DD"))
    after = next(item for item in repository.intake_status(limit=20)["items"] if item["id"] == item_id)
    with repository.db.operation() as conn:
        after_payload = json.loads(conn.execute("SELECT payload_json FROM intake_items WHERE id=?", (item_id,)).fetchone()[0])
    assert after["run_id"] == run_id
    assert after["triage"]["tickers"] == before_identity
    assert after_payload["triage_identity_hash"] == before_payload["triage_identity_hash"]
    assert after_payload["triage_identity_hash"]


def test_semantic_edit_detaches_old_run_and_late_finalize_keeps_new_item_queued(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    ingest(repository, post("edit", title="TSLA can rise", body="TSLA can rise because deliveries improve."))
    item_id, run_id = dispatch(repository, "edit")
    repository.consume_routing_plan(
        run_id,
        route(
            classification="thesis",
            reason="The post states an outlook and a supporting reason.",
            excerpt="TSLA can rise because deliveries improve.",
            summary="TSLA may rise.",
            tickers=["TSLA"],
        ),
    )
    ingest(repository, post("edit", title="Daily thread", body="Share your moves."))
    current = next(item for item in repository.intake_status(limit=20)["items"] if item["id"] == item_id)
    assert current["status"] == "queued"
    assert current["run_id"] is None
    repository.finalize_intake_run(run_id, "completed")
    current = next(item for item in repository.intake_status(limit=20)["items"] if item["id"] == item_id)
    assert current["status"] == "queued"
    assert current["run_id"] is None
    assert current["triage"] is None


def test_reverted_semantic_source_is_a_new_current_lineage_version(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    ingest(repository, post("revert", title="AMD can double", body="AMD can double because its new chip wins."))
    ingest(repository, post("revert", title="AMD daily thread", body="Share your moves."))
    ingest(repository, post("revert", title="AMD can double", body="AMD can double because its new chip wins."))
    item = next(item for item in repository.intake_status(limit=20)["items"] if item["external_id"] == "t3_revert")
    assert len(item["versions"]) == 3
    assert item["versions"][-1]["identity_hash"] == item["versions"][0]["identity_hash"]
    assert item["versions"][-1]["source_id"] != item["versions"][0]["source_id"]
    _item_id, run_id = dispatch(repository, "revert")
    consumed = repository.consume_routing_plan(
        run_id,
        route(
            classification="thesis",
            reason="The post states a claim with a supporting reason.",
            excerpt="AMD can double because its new chip wins.",
            summary="AMD may benefit from its new chip.",
            tickers=["AMD"],
        ),
    )
    assert consumed["routing_plan"]["reddit_triage"]["classification"] == "thesis"
    assert any(task["kind"] == "research_synthesis" for task in repository.tasks_for_run(run_id))


def test_recovery_quarantines_existing_graph_when_route_is_skip_or_missing(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    ingest(repository, post("recover_skip", title="S&P 500 P/E ratio down to 19.4x"))
    _item_id, run_id = dispatch(repository, "recover_skip")
    accepted = repository.consume_routing_plan(
        run_id,
        route(
            classification="thesis",
            reason="temporary accepted fixture",
            excerpt="S&P 500 P/E ratio down to 19.4x",
            summary="A valuation observation.",
            tickers=[],
        ),
    )
    assert len(accepted["task_ids"]) > 1
    with repository.db.transaction(immediate=True) as conn:
        conn.execute("UPDATE tasks SET status='completed' WHERE run_id=? AND kind='routing'", (run_id,))
        snapshot = json.loads(conn.execute("SELECT input_snapshot_json FROM runs WHERE id=?", (run_id,)).fetchone()[0])
        snapshot["routing_consumed"] = False
        conn.execute("UPDATE runs SET input_snapshot_json=? WHERE id=?", (json.dumps(snapshot), run_id))
    recovered = repository.consume_routing_plan(
        run_id,
        route(classification="skip", reason="The title is a bare statistic.", excerpt="S&P 500 P/E ratio down to 19.4x"),
    )
    assert recovered["reused"] is True
    assert all(task["status"] in {"completed", "blocked"} for task in repository.tasks_for_run(run_id))
    assert any(task["status"] == "blocked" for task in repository.tasks_for_run(run_id))
    with repository.db.transaction(immediate=True) as conn:
        conn.execute("UPDATE runs SET status='blocked',finished_at=updated_at WHERE id=?", (run_id,))

    ingest(repository, post("recover_missing", title="A statistic"))
    _item_id, missing_run_id = dispatch(repository, "recover_missing")
    repository.consume_routing_plan(
        missing_run_id,
        route(classification="thesis", reason="temporary accepted fixture", excerpt="A statistic", summary="A claim."),
    )
    with repository.db.transaction(immediate=True) as conn:
        snapshot = json.loads(conn.execute("SELECT input_snapshot_json FROM runs WHERE id=?", (missing_run_id,)).fetchone()[0])
        snapshot["routing_plan"].pop("reddit_triage", None)
        snapshot["routing_consumed"] = True
        conn.execute("UPDATE runs SET input_snapshot_json=? WHERE id=?", (json.dumps(snapshot), missing_run_id))
    recovered = repository.consume_routing_plan(missing_run_id, None)
    assert recovered["reused"] is True
    assert any(task["status"] == "blocked" for task in repository.tasks_for_run(missing_run_id))
    saved = repository.run_snapshot(missing_run_id)
    assert saved["routing_plan"]["reddit_triage"]["reason"].startswith("Screening unavailable:")


def test_manual_repair_requires_an_approved_root_screen(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    ingest(repository, post("repair_skip", title="Daily thread", body="Share your moves."))
    _item_id, run_id = dispatch(repository, "repair_skip")
    repository.consume_routing_plan(run_id, route(classification="skip", reason="Daily thread; no investment thesis.", excerpt="Daily thread"))
    with pytest.raises(ValueError, match="did not authorize evidence research"):
        repository.create_evidence_research(
            run_id,
            ResearchRequest(instruction="Find public catalysts.", idempotency_key="repair-skip-1"),
        )


def test_recovery_claim_is_idempotent_when_creator_resumes_after_link(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A recovery dispatcher cannot cancel the run created by its peer."""
    repository = make_repository(tmp_path)
    for key in ("race_a", "race_b", "race_c"):
        ingest(repository, post(key, title=f"A claim for {key}", body="A company may grow."))

    original_create_run = repository.create_run
    nested: list[dict[str, object]] = []
    first_create = True

    def interposed_create_run(*args: object, **kwargs: object):
        nonlocal first_create
        result = original_create_run(*args, **kwargs)
        if first_create:
            first_create = False
            nested.append(repository.dispatch_reddit_backlog(max_dispatches=2, max_cost=2))
        return result

    monkeypatch.setattr(repository, "create_run", interposed_create_run)
    first = repository.dispatch_reddit_backlog(max_dispatches=1, max_cost=1)
    assert len(first["dispatched"]) == 1
    run_id = str(first["dispatched"][0]["run_id"])
    assert nested and run_id in {str(item["run_id"]) for item in nested[0]["recovered"]}
    assert len(nested[0]["dispatched"]) == 2
    with repository.db.operation() as conn:
        run = conn.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
        dispatch = conn.execute("SELECT status,run_id FROM intake_dispatches WHERE run_id=?", (run_id,)).fetchone()
    assert run["status"] == "queued"
    assert dispatch["status"] == "dispatched"
    assert dispatch["run_id"] == run_id
    assert first["active_post_count"] == 3
    assert first["available_slots"] == 0


def test_monitor_schedules_a_run_recovered_from_a_split_reservation(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    ingest(repository, post("restart", title="A restartable claim", body="A company may grow."))
    item = next(item for item in repository.intake_status(limit=20)["items"] if item["external_id"] == "t3_restart")
    source_id = str(item["versions"][-1]["source_id"])
    question = "Review one retained Reddit submission as an untrusted source."
    body = RunCreate(
        question=question,
        namespace="real",
        horizon="event",
        source_ids=[source_id],
        idempotency_key="reddit-restart-recovery",
        origin="reddit",
        origin_ref="t3_restart",
    )
    tasks = build_research_tasks(question, body.horizon, body.ticker, "real", initial_only=True)
    created, _ = repository.create_run(body, tasks, allow_semantic_reuse=False)
    run_id = str(created["run_id"])
    now = utc_now()
    with repository.db.transaction(immediate=True) as conn:
        conn.execute("UPDATE intake_items SET status='processing',run_id=NULL,updated_at=? WHERE id=?", (now, item["id"]))
        conn.execute(
            "INSERT INTO intake_dispatches(id,item_id,run_id,status,cost_units,created_at,updated_at) VALUES(?,?,NULL,'queued',1,?,?)",
            ("restart-reservation", item["id"], now, now),
        )

    class Engine:
        def __init__(self) -> None:
            self.scheduled: list[str] = []

        def schedule(self, value: str) -> None:
            self.scheduled.append(value)

    engine = Engine()
    monitor = RedditMonitor(repository, engine, repository.config)
    result = monitor.dispatch("real", max_dispatches=3)
    assert run_id in {str(item["run_id"]) for item in result["recovered"]}
    assert engine.scheduled == [run_id]
    assert result["active_post_count"] == 1
