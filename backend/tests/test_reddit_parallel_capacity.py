"""Regression coverage for bounded Reddit dispatch and inbox filtering.

These tests use only temporary SQLite repositories.  Reddit posts are inserted
as retained source fixtures, so no connector, model provider, production
database, or network call is involved.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from backend.app.config import Settings
from backend.app.memory.repository import Repository
from backend.app.schemas import RunCreate


ROOT = Path(__file__).resolve().parents[2]


def repository(tmp_path: Path) -> Repository:
    return Repository(config=Settings(project_root=ROOT, data_dir=tmp_path))


def reddit_post(key: str, index: int = 0) -> dict[str, object]:
    hour = index % 24
    return {
        "post_id": f"t3_{key}",
        "subreddit": "wallstreetbets",
        "title": f"Synthetic Reddit post {key}",
        "body": "A retained source fixture with a bounded research lead.",
        "created_at": f"2026-09-12T{hour:02d}:00:00Z",
        "permalink": f"https://www.reddit.com/r/wallstreetbets/comments/{key}/",
        "score": index,
    }


def ingest_posts(repo: Repository, count: int) -> None:
    posts = [reddit_post(f"post-{index}", index) for index in range(count)]
    result = repo.ingest_reddit_result(
        {
            "posts": posts,
            "status": "ok",
            "capability": "ready",
            "error": None,
            "metadata": {"retrieved_at": "2026-09-12T12:00:00Z"},
        }
    )
    assert result["created_count"] == count


def create_lineage_run(
    repo: Repository,
    key: str,
    *,
    origin: str,
    parent_run_id: str | None = None,
    root_run_id: str | None = None,
) -> str:
    run, reused = repo.create_run(
        RunCreate(
            question=f"Synthetic Reddit capacity lineage {key}",
            namespace="real",
            idempotency_key=f"reddit-capacity-{key}",
            origin=origin,
            origin_ref=f"t3_{key}" if origin == "reddit" else None,
            root_run_id=root_run_id,
        ),
        [],
        allow_semantic_reuse=False,
        parent_run_id=parent_run_id,
    )
    assert reused is False
    return str(run["run_id"])


def set_run_status(repo: Repository, run_id: str, status: str) -> None:
    with repo.db.transaction(immediate=True) as conn:
        conn.execute("UPDATE runs SET status=? WHERE id=?", (status, run_id))


def test_default_reddit_dispatch_admits_three_root_groups(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    ingest_posts(repo, 5)

    result = repo.dispatch_reddit_backlog()

    dispatched = result["dispatched"]
    assert len(dispatched) == 3
    assert len({item["item_id"] for item in dispatched}) == 3
    assert len({item["run_id"] for item in dispatched}) == 3
    assert result["parallel_limit"] == 3
    assert result["active_post_count"] == 3
    assert result["available_slots"] == 0
    assert repo.reddit_queue_capacity() == {
        "parallel_limit": 3,
        "active_post_count": 3,
        "available_slots": 0,
    }

    blocked_by_capacity = repo.dispatch_reddit_backlog()
    assert blocked_by_capacity["dispatched"] == []
    assert "capacity is full" in blocked_by_capacity["message"]


def test_reddit_capacity_counts_root_and_mislabeled_descendants_once(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    root = create_lineage_run(repo, "root", origin="reddit")
    repair = create_lineage_run(repo, "repair", origin="repair", parent_run_id=root, root_run_id=root)
    mislabeled_child = create_lineage_run(repo, "mislabeled", origin="user", parent_run_id=root, root_run_id=root)
    set_run_status(repo, root, "running")
    set_run_status(repo, repair, "waiting_evidence")
    set_run_status(repo, mislabeled_child, "waiting_review")

    capacity = repo.reddit_queue_capacity()

    assert repo.canonical_run_origin(mislabeled_child) == "reddit"
    assert capacity["active_post_count"] == 1
    assert capacity["available_slots"] == 2


def test_paused_reddit_repair_child_keeps_root_slot_occupied(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    root = create_lineage_run(repo, "paused-root", origin="reddit")
    child = create_lineage_run(repo, "paused-child", origin="repair", parent_run_id=root, root_run_id=root)
    set_run_status(repo, root, "completed")
    set_run_status(repo, child, "paused")

    capacity = repo.reddit_queue_capacity()

    assert capacity["active_post_count"] == 1
    assert capacity["available_slots"] == 2


def test_concurrent_dispatchers_do_not_oversubscribe_or_duplicate_items(tmp_path: Path) -> None:
    initial = repository(tmp_path)
    ingest_posts(initial, 8)
    dispatchers = [repository(tmp_path) for _ in range(4)]

    def dispatch(instance: Repository) -> dict[str, object]:
        return instance.dispatch_reddit_backlog()

    with ThreadPoolExecutor(max_workers=len(dispatchers)) as executor:
        results = list(executor.map(dispatch, dispatchers))

    dispatched = [item for result in results for item in result["dispatched"]]
    assert len(dispatched) == 3
    assert len({item["item_id"] for item in dispatched}) == 3
    assert len({item["run_id"] for item in dispatched}) == 3
    assert initial.reddit_queue_capacity()["active_post_count"] == 3
    with initial.db.operation() as conn:
        dispatch_rows = conn.execute(
            "SELECT item_id,run_id,status FROM intake_dispatches WHERE status='dispatched'"
        ).fetchall()
        active_roots = conn.execute(
            "SELECT COUNT(*) FROM runs WHERE namespace='real' AND origin='reddit' AND status IN ('queued','running','waiting_evidence','waiting_review','paused')"
        ).fetchone()[0]
    assert len(dispatch_rows) == 3
    assert len({row["item_id"] for row in dispatch_rows}) == 3
    assert len({row["run_id"] for row in dispatch_rows}) == 3
    assert active_roots == 3


def test_processed_results_filter_applies_before_pagination(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    ingest_posts(repo, 6)
    with repo.db.transaction(immediate=True) as conn:
        conn.execute(
            "UPDATE intake_items SET status='processed',reason='Synthetic completed result' "
            "WHERE namespace='real' AND origin='reddit' AND external_id IN (?,?,?)",
            ("t3_post-0", "t3_post-1", "t3_post-2"),
        )

    first_page = repo.intake_status(status="processed", limit=2, offset=0)
    second_page = repo.intake_status(status="processed", limit=2, offset=2)

    assert first_page["filter"] == "processed"
    assert first_page["status_filter"] == "processed"
    assert first_page["total"] == 6
    assert first_page["filtered_total"] == 3
    assert first_page["counts"]["processed"] == 3
    assert first_page["filtered_counts"] == {"processed": 3}
    assert first_page["has_more"] is True
    assert second_page["has_more"] is False
    assert first_page["offset"] == 0
    assert second_page["offset"] == 2
    assert len(first_page["items"]) == 2
    assert len(second_page["items"]) == 1
    assert all(item["status"] == "processed" for item in first_page["items"] + second_page["items"])
    assert {item["external_id"] for item in first_page["items"] + second_page["items"]} == {
        "t3_post-0",
        "t3_post-1",
        "t3_post-2",
    }
