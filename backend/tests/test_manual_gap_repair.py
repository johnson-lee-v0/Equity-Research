"""Focused contracts for the bounded manual evidence-repair wrapper."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app.config import Settings
from backend.app.memory.repository import Repository
from backend.app.schemas import AgentOutputPayload, ImportRequest, MissingGap, ResearchRequest, RunCreate, ModelConfig


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ModelConfig(provider="codex", model="gpt-5.6-luna", reasoning_effort="max")


def make_repository(tmp_path: Path) -> Repository:
    return Repository(config=Settings(data_dir=tmp_path, project_root=ROOT, codex_timeout_seconds=30))


def import_source(repository: Repository, key: str, *, namespace: str = "real") -> str:
    result = repository.import_evidence(
        ImportRequest(
            namespace=namespace,
            kind="evidence",
            title="Retained filing",
            content="A dated public evidence line.",
            source_url="https://issuer.example.test/filing",
            publication_at="2026-09-01",
            observed_at="2026-09-01",
            idempotency_key=key,
        )
    )
    return str(result["source_id"])


def make_root(repository: Repository, key: str, *, source_ids: list[str] | None = None, origin: str = "user", task: tuple[str, str, str] = ("A00", "routing", "Route")) -> dict[str, object]:
    run, reused = repository.create_run(
        RunCreate(
            question="Original question stays attached to every repair.",
            namespace="real",
            ticker="USO",
            source_ids=source_ids or [],
            idempotency_key=key,
            origin=origin,
            origin_ref="reddit-item-1" if origin == "reddit" else None,
        ),
        [(task[0], task[1], task[2], [])],
        allow_semantic_reuse=False,
    )
    assert reused is False
    return run


def manual_request(key: str, instruction: str = "Find the dated public filing.", **kwargs: object) -> ResearchRequest:
    return ResearchRequest(instruction=instruction, idempotency_key=key, **kwargs)


def test_manual_repair_preserves_scope_registers_ledger_and_reuses_key(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    source_id = import_source(repository, "manual-source-1")
    root = make_root(repository, "manual-root-1", source_ids=[source_id])

    child, reused = repository.create_evidence_research(root["run_id"], manual_request("manual-child-1"))
    assert reused is False
    assert child["run_id"] != root["run_id"]
    assert child["parent_run_id"] == root["run_id"]
    assert child["root_run_id"] == root["run_id"]
    assert child["origin"] == "repair"
    assert child["source_ids"] == [source_id]
    assert child["effective_source_ids"] == [source_id]
    assert child["repair_round"] == 1
    assert child["research_repair_id"]

    child_row = repository.run_record(child["run_id"])
    assert child_row["request"] == repository.run_record(root["run_id"])["request"]
    assert child_row["research_instruction"] == "Find the dated public filing."
    child_snapshot = json.loads(child_row["input_snapshot_json"])
    assert child_snapshot["source_ids"] == [source_id]
    assert child_snapshot["parent_research_context"]["source_ids"] == [source_id]
    assert child_snapshot["parent_research_context"]["root_run_id"] == root["run_id"]
    with repository.db.operation() as conn:
        repair = conn.execute("SELECT * FROM research_repairs WHERE repair_run_id=?", (child["run_id"],)).fetchone()
        task = conn.execute("SELECT * FROM tasks WHERE run_id=? ORDER BY sequence_no", (child["run_id"],)).fetchone()
    assert repair["root_run_id"] == root["run_id"]
    assert repair["round_no"] == 1
    assert repair["status"] == "queued"
    assert "Find the dated public filing." in task["instruction"]

    replay, reused = repository.create_evidence_research(root["run_id"], manual_request("manual-child-1"))
    assert reused is True
    assert replay["run_id"] == child["run_id"]
    with pytest.raises(ValueError, match="different run request"):
        repository.create_evidence_research(root["run_id"], manual_request("manual-child-1", "A changed instruction."))


def test_manual_repair_reuses_any_active_root_child_and_caps_two_rounds(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    root = make_root(repository, "manual-round-root")
    first, reused = repository.create_evidence_research(root["run_id"], manual_request("manual-round-1"))
    assert reused is False and first["repair_round"] == 1

    active, reused = repository.create_evidence_research(root["run_id"], manual_request("manual-round-active"))
    assert reused is True
    assert active["run_id"] == first["run_id"]

    with repository.db.transaction(immediate=True) as conn:
        conn.execute("UPDATE runs SET status='completed',finished_at=updated_at WHERE id=?", (first["run_id"],))
        conn.execute("UPDATE research_repairs SET status='completed' WHERE repair_run_id=?", (first["run_id"],))
    second, reused = repository.create_evidence_research(root["run_id"], manual_request("manual-round-2"))
    assert reused is False
    assert second["run_id"] != first["run_id"]
    assert second["repair_round"] == 2

    with repository.db.transaction(immediate=True) as conn:
        conn.execute("UPDATE runs SET status='completed',finished_at=updated_at WHERE id=?", (second["run_id"],))
        conn.execute("UPDATE research_repairs SET status='completed' WHERE repair_run_id=?", (second["run_id"],))
    with pytest.raises(ValueError, match="two-round repair limit"):
        repository.create_evidence_research(root["run_id"], manual_request("manual-round-3"))


def test_manual_repair_rejects_paused_cancelled_and_cross_namespace_inputs(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    paused = make_root(repository, "manual-paused-root")
    assert repository.control("run", paused["run_id"], "pause") == 1
    with pytest.raises(ValueError, match="paused root"):
        repository.create_evidence_research(paused["run_id"], manual_request("manual-paused-child"))
    with repository.db.operation() as conn:
        assert conn.execute("SELECT COUNT(*) FROM research_repairs WHERE root_run_id=?", (paused["run_id"],)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM runs WHERE parent_run_id=?", (paused["run_id"],)).fetchone()[0] == 0

    cancelled = make_root(repository, "manual-cancelled-root")
    assert repository.control("run", cancelled["run_id"], "cancel") == 1
    with pytest.raises(ValueError, match="cancelled"):
        repository.create_evidence_research(cancelled["run_id"], manual_request("manual-cancelled-child"))

    real_source = import_source(repository, "manual-real-source")
    demo_source = import_source(repository, "manual-demo-source", namespace="demo")
    scoped = make_root(repository, "manual-scope-root", source_ids=[real_source])
    with pytest.raises(ValueError, match="retained by the parent"):
        repository.create_evidence_research(
            scoped["run_id"],
            manual_request("manual-cross-namespace", source_ids=[demo_source]),
        )


def test_manual_repair_preserves_reddit_lineage_and_gap_links(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    root = make_root(repository, "manual-reddit-root", origin="reddit", task=("A03", "fundamental_review", "Review"))
    task_id = root["tasks"][0]["id"]
    attempt = repository.create_attempt(task_id, CONFIG, {})
    assert repository.control("run", root["run_id"], "pause") == 1
    repository.commit_output(
        task_id,
        attempt["attempt_id"],
        AgentOutputPayload(
            status="needs_review",
            title="A public gap",
            summary="One public filing is missing.",
            analysis="The repair remains bounded.",
            missing_gaps=[MissingGap(key="dated_filing", description="A dated public issuer filing is missing.", relevant_role="A02")],
        ),
        "real",
        CONFIG,
    )
    assert repository.control("run", root["run_id"], "resume") == 1
    gap = repository.gaps_for_run(root["run_id"])[0]

    child, reused = repository.create_evidence_research(
        root["run_id"],
        manual_request("manual-reddit-child", gap_ids=[gap["id"]]),
    )
    assert reused is False
    assert child["origin"] == "reddit"
    assert child["root_run_id"] == root["run_id"]
    with repository.db.operation() as conn:
        linked = conn.execute("SELECT repair_id,gap_id FROM research_repair_gaps WHERE gap_id=?", (gap["id"],)).fetchone()
        updated_gap = conn.execute("SELECT status,repair_run_id,repair_round FROM research_gaps WHERE id=?", (gap["id"],)).fetchone()
    assert linked["repair_id"] == child["research_repair_id"]
    assert updated_gap["status"] == "in_progress"
    assert updated_gap["repair_run_id"] == child["run_id"]
    assert updated_gap["repair_round"] == 1


def test_manual_repair_can_use_saved_missing_data_without_retyping(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    root = make_root(repository, "manual-missing-root", task=("A11", "cio_review", "CIO"))
    task_id = root["tasks"][0]["id"]
    attempt = repository.create_attempt(task_id, CONFIG, {})
    assert repository.control("run", root["run_id"], "pause") == 1
    repository.commit_output(
        task_id,
        attempt["attempt_id"],
        AgentOutputPayload(
            status="needs_review",
            title="Saved CIO gap",
            summary="A dated primary source is still needed.",
            analysis="The prior answer was deferred.",
            missing_data=["Need a dated public filing."],
            decision_disposition="defer",
        ),
        "real",
        CONFIG,
    )
    assert repository.control("run", root["run_id"], "resume") == 1
    request = SimpleNamespace(
        instruction="",
        idempotency_key="manual-missing-child",
        source_ids=None,
        model_override=None,
        gap_ids=[],
        root_run_id=None,
    )
    child, reused = repository.create_evidence_research(root["run_id"], request)
    assert reused is False
    child_row = repository.run_record(child["run_id"])
    assert child_row["research_instruction"] == "Resolve the recorded evidence gaps:\n- Need a dated public filing."
    child_snapshot = json.loads(child_row["input_snapshot_json"])
    assert child_snapshot["parent_research_context"]["missing_data"] == ["Need a dated public filing."]
    assert child_snapshot["parent_research_context"]["output_id"]


def test_manual_repair_keeps_private_inputs_and_gap_budget_terminal(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    root = make_root(repository, "manual-private-root")
    with pytest.raises(ValueError, match="private account"):
        repository.create_evidence_research(root["run_id"], manual_request("manual-private-child", "Find my portfolio cost basis."))
    with pytest.raises(ValueError, match="at most six"):
        repository.create_evidence_research(
            root["run_id"],
            manual_request("manual-gap-cap", gap_ids=[f"gap-{index}" for index in range(7)]),
        )
