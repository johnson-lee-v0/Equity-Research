"""Focused coverage for bounded cross-question task memory."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from backend.app.config import Settings
from backend.app.memory.repository import Repository
from backend.app.schemas import AgentOutputPayload, ImportRequest, ModelConfig, RunCreate


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ModelConfig(provider="codex", model="gpt-5.6-luna", reasoning_effort="max")


@pytest.fixture()
def repository(tmp_path: Path) -> Repository:
    return Repository(config=Settings(data_dir=tmp_path, project_root=ROOT, codex_timeout_seconds=30))


def import_source(repository: Repository, key: str, content: str, *, namespace: str = "real", supersedes_id: str | None = None) -> dict[str, Any]:
    result = repository.import_evidence(
        ImportRequest(
            namespace=namespace,
            kind="evidence",
            title=f"{namespace} dated evidence",
            content=content,
            source_url="https://example.test/evidence",
            publication_at="2026-09-01",
            observed_at="2026-09-01",
            supersedes_id=supersedes_id,
            idempotency_key=key,
        )
    )
    assert result["duplicate"] is False
    return result


def create_task(repository: Repository, key: str, question: str, *, ticker: str | None = None, namespace: str = "real", source_ids: list[str] | None = None, parent_run_id: str | None = None, root_run_id: str | None = None) -> tuple[dict[str, Any], str]:
    body = RunCreate(
        question=question,
        namespace=namespace,
        horizon="1m",
        ticker=ticker,
        source_ids=source_ids or [],
        idempotency_key=key,
        root_run_id=root_run_id,
    )
    created, reused = repository.create_run(
        body,
        [("A03", "fundamental_review", "Review the bounded evidence packet.", [])],
        allow_semantic_reuse=False,
        parent_run_id=parent_run_id,
    )
    assert reused is False
    return created, created["tasks"][0]["id"]


def commit_output(repository: Repository, task_id: str, source_id: str | None, *, summary: str = "A saved historical opinion.") -> dict[str, Any]:
    source_versions: dict[str, Any] = {}
    refs: list[str] = []
    if source_id:
        source = repository.sources("real", source_id)[0]
        source_versions[source_id] = {"version": source["version"], "hash": source["content_hash"]}
        refs = [source_id]
    attempt = repository.create_attempt(task_id, CONFIG, source_versions)
    return repository.commit_output(
        task_id,
        attempt["attempt_id"],
        AgentOutputPayload(
            status="completed",
            title="Saved analyst opinion",
            summary=summary,
            analysis="This is retained reasoning tied to the saved source packet.",
            source_refs=refs,
        ),
        "real",
        CONFIG,
    )


def test_same_ticker_question_reuses_current_source_and_historical_output(repository: Repository) -> None:
    source = import_source(repository, "memory-uso-source", "USO inventory observation: 42 units on 2026-09-01.")
    first, first_task = create_task(repository, "memory-uso-first", "Assess USO inventory exposure.", ticker="USO", source_ids=[source["source_id"]])
    old_output = commit_output(repository, first_task, source["source_id"], summary="USO exposure appears manageable from the dated observation.")

    second, second_task = create_task(repository, "memory-uso-second", "What entry risks remain for USO?", ticker="USO")
    context = repository.prepare_task_memory(second_task)

    assert context is not None
    assert context["source_ids"] == [source["source_id"]]
    assert context["effective_source_ids"] == context["source_ids"]
    assert context["current_price_refresh_required"] is True
    assert context["refresh_requirements"][0]["ticker"] == "USO"
    source_items = [item for item in context["reused"] if item["record_type"] == "source"]
    output_items = [item for item in context["reused"] if item["record_type"] == "output"]
    assert source_items and source_items[0]["record_id"] == source["source_id"]
    assert source_items[0]["source_versions"][0]["content_hash"] == hashlib.sha256("USO inventory observation: 42 units on 2026-09-01.".encode()).hexdigest()
    assert "USO inventory observation" in source_items[0]["excerpt"]
    assert output_items and output_items[0]["record_id"] == old_output["id"]
    assert output_items[0]["title"].startswith("Historical opinion ·")
    assert "not a fact" in output_items[0]["reuse_reason"]
    assert "re-check" in output_items[0]["reuse_reason"]


def test_unrelated_ticker_and_namespace_are_isolated(repository: Repository) -> None:
    real_source = import_source(repository, "memory-real-source", "USO public observation.")
    real_run, real_task = create_task(repository, "memory-real-run", "Assess USO trend.", ticker="USO", source_ids=[real_source["source_id"]])
    commit_output(repository, real_task, real_source["source_id"])
    demo_source = import_source(repository, "memory-demo-source", "USO demo observation.", namespace="demo")
    _, demo_task = create_task(repository, "memory-demo-run", "Assess USO trend.", ticker="USO", namespace="demo", source_ids=[demo_source["source_id"]])
    commit_output(repository, demo_task, None)

    unrelated, unrelated_task = create_task(repository, "memory-unrelated-run", "Assess XYZ trend.", ticker="XYZ")
    unrelated_context = repository.prepare_task_memory(unrelated_task)
    assert unrelated_context is not None
    assert unrelated_context["source_ids"] == []
    assert unrelated_context["reused"] == []
    assert unrelated_context["fresh"] == []

    real_again, real_again_task = create_task(repository, "memory-real-again", "Assess USO catalysts.", ticker="USO")
    real_context = repository.prepare_task_memory(real_again_task)
    assert real_context is not None
    assert all(item["namespace"] == "real" for item in [*real_context["reused"], *real_context["fresh"]])
    assert demo_source["source_id"] not in real_context["source_ids"]
    assert all(item["record_id"] != demo_source["source_id"] for item in real_context["reused"])


def test_superseded_source_and_opinion_never_become_current_memory(repository: Repository) -> None:
    old = import_source(repository, "memory-old-source", "USO price was 70 USD on 2026-09-01.")
    first, first_task = create_task(repository, "memory-old-run", "Review USO price evidence.", ticker="USO", source_ids=[old["source_id"]])
    old_output = commit_output(repository, first_task, old["source_id"], summary="The old USO price was retained as context.")
    amended = import_source(repository, "memory-new-source", "USO price was 73 USD on 2026-09-12.", supersedes_id=old["source_id"])

    second, second_task = create_task(repository, "memory-new-run", "Recheck USO price evidence.", ticker="USO")
    context = repository.prepare_task_memory(second_task)

    assert context is not None
    assert amended["source_id"] in context["source_ids"]
    assert old["source_id"] not in context["source_ids"]
    all_items = [*context["reused"], *context["fresh"]]
    assert all(item["record_id"] != old["source_id"] for item in all_items)
    assert all(item["record_id"] != old_output["id"] for item in all_items)
    current_source = next(item for item in all_items if item["record_type"] == "source")
    assert current_source["record_id"] == amended["source_id"]
    assert "73 USD" in current_source["excerpt"]


def test_memory_is_bounded_and_prior_output_has_caveat(repository: Repository) -> None:
    source_ids: list[str] = []
    for index in range(10):
        imported = import_source(repository, f"memory-bound-source-{index}", f"USO observation {index}: " + ("x" * 4000))
        source_ids.append(imported["source_id"])
    first, first_task = create_task(repository, "memory-bound-first", "Build a broad USO evidence packet.", ticker="USO", source_ids=source_ids)
    commit_output(repository, first_task, source_ids[0], summary="A prior opinion that must carry a historical caveat.")
    second, second_task = create_task(repository, "memory-bound-second", "Compare USO evidence.", ticker="USO")
    context = repository.prepare_task_memory(second_task)

    assert context is not None
    items = [*context["fresh"], *context["reused"]]
    assert len(items) <= 8
    assert sum(len(str(item.get("excerpt") or "")) for item in items) <= context["memory_char_budget"]
    assert len(context["source_ids"]) <= 8
    prior_outputs = [item for item in items if item["record_type"] == "output"]
    assert prior_outputs
    assert all(item["title"].startswith("Historical opinion ·") for item in prior_outputs)
    assert all("opinion" in item["reuse_reason"] and "re-check" in item["reuse_reason"] for item in prior_outputs)


def test_linked_repair_reuses_root_reasoning_without_question_overlap(repository: Repository) -> None:
    source = import_source(repository, "memory-linked-source", "Issuer filing observation.")
    root, root_task = create_task(repository, "memory-linked-root", "Original question with no ticker", source_ids=[source["source_id"]])
    root_output = commit_output(repository, root_task, source["source_id"], summary="Root question reasoning retained for the repair.")
    repair, repair_task = create_task(
        repository,
        "memory-linked-repair",
        "A bounded repair instruction with unrelated wording",
        parent_run_id=root["run_id"],
        root_run_id=root["run_id"],
    )
    context = repository.prepare_task_memory(repair_task)

    assert context is not None
    assert any(item["record_id"] == root_output["id"] for item in context["reused"])
    assert source["source_id"] in context["source_ids"]


def test_routed_candidates_require_current_price_refresh_for_each_ticker(repository: Repository) -> None:
    source = import_source(repository, "memory-routed-source", "USO and BWET observations from the saved packet.")
    first, first_task = create_task(repository, "memory-routed-first", "Route a multi-candidate research question.", source_ids=[source["source_id"]])
    with repository.db.transaction(immediate=True) as connection:
        snapshot = json.loads(connection.execute("SELECT input_snapshot_json FROM runs WHERE id=?", (first["run_id"],)).fetchone()[0])
        snapshot["routing_plan"] = {"tickers": ["USO", "BWET"]}
        connection.execute("UPDATE runs SET input_snapshot_json=? WHERE id=?", (json.dumps(snapshot), first["run_id"]))
    commit_output(repository, first_task, source["source_id"], summary="Historical multi-candidate reasoning.")

    second, second_task = create_task(repository, "memory-routed-second", "Review the saved routed candidates again.")
    with repository.db.transaction(immediate=True) as connection:
        snapshot = json.loads(connection.execute("SELECT input_snapshot_json FROM runs WHERE id=?", (second["run_id"],)).fetchone()[0])
        snapshot["routing_plan"] = {"tickers": ["USO", "BWET"]}
        connection.execute("UPDATE runs SET input_snapshot_json=? WHERE id=?", (json.dumps(snapshot), second["run_id"]))
    context = repository.prepare_task_memory(second_task)

    assert context is not None
    assert context["current_price_refresh_required"] is True
    assert {item["ticker"] for item in context["refresh_requirements"]} == {"USO", "BWET"}


def test_generic_web_document_is_context_only_and_primary_coverage_stays_missing(repository: Repository) -> None:
    source = repository.import_evidence(
        ImportRequest(
            namespace="real",
            kind="evidence",
            title="Company blog observation",
            content="ACME observation: revenue was USD 42 on 2026-09-01.",
            source_url="https://blog.example.test/acme",
            publication_at="2026-09-01",
            observed_at="2026-09-01",
            idempotency_key="memory-generic-blog",
        )
    )
    _run, task_id = create_task(repository, "memory-generic-blog-run", "Review ACME fundamentals.", ticker="ACME", source_ids=[source["source_id"]])
    context = repository.prepare_task_memory(task_id)

    assert context is not None
    items = [item for item in [*context["fresh"], *context["reused"]] if item["record_type"] == "source"]
    assert items
    item = items[0]
    assert item["primary_evidence"] is False
    assert item["primary_coverage"] == "unknown_external_document"
    assert item["freshness_status"] == "fresh"
    assert context["coverage_requirements"][0]["status"] == "missing"


def test_source_packet_exposes_policy_owned_primary_coverage(repository: Repository) -> None:
    title_only = repository.import_evidence(
        ImportRequest(
            namespace="real",
            kind="evidence",
            title="Synthetic 10-K filing",
            content="ACME revenue was USD 43 for year ended 2025-12-31.",
            source_url="https://example.test/acme-filing",
            publication_at="2026-09-01",
            observed_at="2026-09-01",
            idempotency_key="memory-primary-title-only",
        )
    )
    trusted_host = repository.import_evidence(
        ImportRequest(
            namespace="real",
            kind="evidence",
            title="Company report",
            content="ACME revenue was USD 42 for year ended 2025-12-31.",
            source_url="https://www.sec.gov/Archives/synthetic-acme",
            publication_at="2026-09-01",
            observed_at="2026-09-01",
            idempotency_key="memory-primary-trusted-host",
        )
    )

    title_only_packet = repository.source_packet("real", [title_only["source_id"]])[0]
    trusted_packet = repository.source_packet("real", [trusted_host["source_id"]])[0]
    assert title_only_packet["primary_evidence"] is False
    assert title_only_packet["primary_coverage"] == "unknown_external_document"
    assert trusted_packet["primary_evidence"] is True
    assert trusted_packet["primary_coverage"] == "primary_document"


def test_scoped_deferred_supersession_invalidates_all_but_queues_other_cases(repository: Repository) -> None:
    old = import_source(repository, "memory-defer-old", "ACME revenue was USD 41 on 2026-09-01.")
    active, active_task = create_task(repository, "memory-defer-active", "Review active ACME case.", ticker="ACME", source_ids=[old["source_id"]])
    other, _other_task = create_task(repository, "memory-defer-other", "Review another ACME case.", ticker="ACME", source_ids=[old["source_id"]])

    amended = repository.import_evidence(
        ImportRequest(
            namespace="real",
            kind="evidence",
            title="ACME dated evidence amendment",
            content="ACME revenue was USD 42 on 2026-09-12.",
            source_url="https://example.test/evidence",
            publication_at="2026-09-12",
            observed_at="2026-09-12",
            supersedes_id=old["source_id"],
            idempotency_key="memory-defer-new",
        ),
        refresh_mode="defer",
        defer_run_id=active["run_id"],
    )

    assert amended["refresh_deferred"] is True
    assert amended["refresh_run_ids"]
    assert repository.run_record(active["run_id"])["status"] == "queued"
    with repository.db.operation() as connection:
        active_invalidations = connection.execute("SELECT COUNT(*) FROM invalidations WHERE run_id=? AND source_id=?", (active["run_id"], amended["source_id"])).fetchone()[0]
        other_refresh = connection.execute("SELECT COUNT(*) FROM runs WHERE idempotency_key LIKE ?", (f"refresh:{other['run_id']}:%",)).fetchone()[0]
    assert active_invalidations >= 1
    assert other_refresh >= 1


def test_defer_without_a_scoped_run_is_rejected(repository: Repository) -> None:
    with pytest.raises(ValueError, match="defer_run_id"):
        repository.import_evidence(
            ImportRequest(
                namespace="real",
                kind="evidence",
                title="Unscoped amendment",
                content="ACME revenue was USD 43 on 2026-09-13.",
                source_url="https://example.test/evidence",
                idempotency_key="memory-defer-unscoped",
            ),
            refresh_mode="defer",
        )


def test_reverted_content_cannot_silently_become_a_different_amendment(repository: Repository) -> None:
    old = import_source(repository, "memory-revert-old", "ACME revenue was USD 41 on 2026-09-01.")
    amended = import_source(repository, "memory-revert-amended", "ACME revenue was USD 42 on 2026-09-12.", supersedes_id=old["source_id"])

    with pytest.raises(ValueError, match="different source lineage"):
        repository.import_evidence(
            ImportRequest(
                namespace="real",
                kind="evidence",
                title="ACME reverted evidence",
                content="ACME revenue was USD 41 on 2026-09-01.",
                source_url="https://example.test/evidence",
                publication_at="2026-09-13",
                observed_at="2026-09-13",
                supersedes_id=amended["source_id"],
                idempotency_key="memory-revert-again",
            )
        )


def test_memory_does_not_refresh_old_observations_or_clamp_future_dates(repository: Repository) -> None:
    stale_source = repository.import_evidence(
        ImportRequest(
            namespace="real",
            kind="evidence",
            title="Old dated observation",
            content="ACME observation: revenue was USD 40 on 2026-01-01.",
            source_url="https://blog.example.test/acme-old",
            publication_at="2026-01-01",
            observed_at="2026-01-01",
            idempotency_key="memory-stale-observation",
        )
    )
    future_source = repository.import_evidence(
        ImportRequest(
            namespace="real",
            kind="evidence",
            title="Future dated observation",
            content="ACME observation: revenue was USD 45 on 2026-09-18.",
            source_url="https://blog.example.test/acme-future",
            publication_at="2026-09-18",
            observed_at="2026-09-18",
            idempotency_key="memory-future-observation",
        )
    )
    _stale_run, stale_task = create_task(repository, "memory-stale-run", "Review stale ACME context.", ticker="ACME", source_ids=[stale_source["source_id"]])
    _future_run, future_task = create_task(repository, "memory-future-run", "Review future ACME context.", ticker="ACME", source_ids=[future_source["source_id"]])
    with repository.db.transaction(immediate=True) as connection:
        connection.execute("UPDATE runs SET as_of=? WHERE id IN (?,?)", ("2026-09-17T00:00:00Z", _stale_run["run_id"], _future_run["run_id"]))

    stale_context = repository.prepare_task_memory(stale_task)
    future_context = repository.prepare_task_memory(future_task)

    assert stale_context is not None and future_context is not None
    assert not [item for item in [*stale_context["fresh"], *stale_context["reused"]] if item["record_type"] == "source"]
    assert not [item for item in [*future_context["fresh"], *future_context["reused"]] if item["record_type"] == "source"]
