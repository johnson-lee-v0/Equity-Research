from __future__ import annotations

import json
from pathlib import Path

from backend.app.config import Settings
from backend.app.memory.repository import Repository
from backend.app.schemas import AgentOutputPayload, FactClaim, ImportRequest, ModelConfig, ResearchRequest, RoutingPlan, RunCreate


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ModelConfig(provider="codex", model="gpt-5.6-luna", reasoning_effort="max")


def make_repository(tmp_path: Path) -> Repository:
    return Repository(config=Settings(data_dir=tmp_path, project_root=ROOT, codex_timeout_seconds=30))


def import_source(repository: Repository, key: str, content: str) -> str:
    result = repository.import_evidence(
        ImportRequest(
            namespace="real",
            kind="evidence",
            title="Retained filing",
            content=content,
            source_url="https://issuer.example.test/filing",
            publication_at="2026-09-01",
            observed_at="2026-09-01",
            idempotency_key=key,
        )
    )
    return str(result["source_id"])


def test_run_grouping_and_authoritative_answer_survive_a00_explanation(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    first, _ = repository.create_run(
        RunCreate(question="  What is   ABC? ", namespace="real", idempotency_key="group-contract-1"),
        [("A00", "routing", "Route", [])],
        allow_semantic_reuse=False,
    )
    second, _ = repository.create_run(
        RunCreate(question="what is ABC?", namespace="real", idempotency_key="group-contract-2"),
        [("A00", "routing", "Route", [])],
        allow_semantic_reuse=False,
    )
    assert first["run_id"] != second["run_id"]
    summaries = repository.run_summaries("real")
    grouped = {item["question_group_id"] for item in summaries if item["id"] in {first["run_id"], second["run_id"]}}
    assert len(grouped) == 1

    # A completed CIO answer remains the preferred run answer after a later
    # A00 explanation is appended to the same run.
    answer, _ = repository.create_run(
        RunCreate(question="ABC recommendation", namespace="real", idempotency_key="answer-contract-1"),
        [("A11", "cio_review", "CIO", []), ("A00", "routing", "Route", [])],
        allow_semantic_reuse=False,
    )
    cio_task = answer["tasks"][0]["id"]
    cio_attempt = repository.create_attempt(cio_task, CONFIG, {})
    cio = repository.commit_output(
        cio_task,
        cio_attempt["attempt_id"],
        AgentOutputPayload(
            status="completed",
            title="Authoritative CIO answer",
            summary="Recommend the bounded action.",
            analysis="The committee answer is complete.",
            decision_disposition="recommend",
        ),
        "real",
        CONFIG,
    )
    followup = repository.add_followup(answer["run_id"], "Explain the saved answer.", "answer-followup-1", output_id=cio["id"])
    followup_attempt = repository.create_attempt(followup["task_id"], CONFIG, {})
    repository.commit_output(
        followup["task_id"],
        followup_attempt["attempt_id"],
        AgentOutputPayload(
            status="completed",
            title="Explanation",
            summary="The answer was saved.",
            analysis="This is a bounded explanation of the existing answer.",
        ),
        "real",
        CONFIG,
    )
    snapshot = repository.run_snapshot(answer["run_id"])
    assert snapshot is not None
    assert snapshot["latest_output_id"] == cio["id"]
    assert snapshot["latest_output_title"] == "Authoritative CIO answer"
    assert snapshot["decision_disposition"] == "recommend"
    assert snapshot["evidence_status"] == "sufficient"


def test_claim_projection_is_immutable_and_records_fact_association(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    source_id = import_source(repository, "claim-contract-1", "Revenue was 100 USD in 2026.")
    run, _ = repository.create_run(
        RunCreate(question="Validate revenue", namespace="real", source_ids=[source_id], idempotency_key="claim-run-1"),
        [("A03", "fundamental_review", "Review", [])],
        allow_semantic_reuse=False,
    )
    task_id = run["tasks"][0]["id"]
    attempt = repository.create_attempt(task_id, CONFIG, {source_id: {"version": 1}})
    waiting_task = repository.task_dict(repository.task(task_id))
    assert waiting_task["dispatch_state"] == "waiting_capacity"
    assert waiting_task["attempts"][0]["started_at"] is None
    assert waiting_task["attempts"][0]["queued_at"]
    repository.mark_provider_started(task_id, attempt["attempt_id"])
    started_task = repository.task_dict(repository.task(task_id))
    assert started_task["dispatch_state"] == "running"
    assert started_task["attempts"][0]["started_at"]
    output = repository.commit_output(
        task_id,
        attempt["attempt_id"],
        AgentOutputPayload(
            status="completed",
            title="Revenue claim",
            summary="Revenue is sourced.",
            analysis="The retained line contains the reported amount.",
            fact_claims=[FactClaim(claim="Revenue", value="100", unit="USD", period="2026", source_ref=source_id, locator="L1")],
            source_refs=[source_id],
        ),
        "real",
        CONFIG,
    )
    with repository.db.operation() as conn:
        before = conn.execute("SELECT payload_json,output_hash FROM outputs WHERE id=?", (output["id"],)).fetchone()
        association = conn.execute("SELECT fact_id,validation_origin,validation_status FROM output_claims WHERE output_id=?", (output["id"],)).fetchone()
    assert association and association["fact_id"] and association["validation_origin"] == "recorded" and association["validation_status"] == "validated"
    dto = repository.output_with_sources(output["id"])["output"]
    claim = dto["fact_claims"][0]
    assert claim["fact_id"] == association["fact_id"]
    assert claim["validation_origin"] == "recorded"
    assert claim["excerpt"] == "Revenue was 100 USD in 2026."
    with repository.db.operation() as conn:
        after = conn.execute("SELECT payload_json,output_hash FROM outputs WHERE id=?", (output["id"],)).fetchone()
    assert tuple(before) == tuple(after)
    indexed = next(item for item in repository.search("real", "Revenue claim", "research") if item["id"] == output["id"])
    assert source_id in indexed["source_refs"]

    # Simulate a pre-association historical output: fallback enrichment is
    # explicit and never invents a durable fact relationship.
    with repository.db.transaction(immediate=True) as conn:
        conn.execute("DELETE FROM output_claims WHERE output_id=?", (output["id"],))
    historical = repository.output_with_sources(output["id"])["output"]["fact_claims"][0]
    assert historical["validation_origin"] == "archived_source_check"
    assert historical["fact_id"] is None
    with repository.db.operation() as conn:
        preserved = conn.execute("SELECT payload_json,output_hash FROM outputs WHERE id=?", (output["id"],)).fetchone()
    assert tuple(before) == tuple(preserved)


def test_cancel_closes_descendants_and_keeps_attempt_history(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    run, _ = repository.create_run(
        RunCreate(question="Cancel dependency graph", namespace="real", idempotency_key="cancel-contract-1"),
        [("A00", "routing", "Route", []), ("A10", "pm_review", "PM", ["routing"]), ("A11", "cio_review", "CIO", ["pm_review"])],
        allow_semantic_reuse=False,
    )
    routing_task = run["tasks"][0]["id"]
    attempt = repository.create_attempt(routing_task, CONFIG, {})
    assert repository.control("run", run["run_id"], "cancel") == 1
    snapshot = repository.run_snapshot(run["run_id"])
    assert snapshot is not None
    assert snapshot["status"] == "cancelled"
    assert all(task["status"] == "cancelled" and task["allowed_actions"] == [] for task in snapshot["tasks"])
    with repository.db.operation() as conn:
        attempt_row = conn.execute("SELECT status FROM task_attempts WHERE id=?", (attempt["attempt_id"],)).fetchone()
    assert attempt_row["status"] == "cancelled"


def test_evidence_research_preserves_question_and_local_parent_gaps(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    source_id = import_source(repository, "research-contract-source", "A retained evidence line.")
    parent, _ = repository.create_run(
        RunCreate(question="  Original   question?  ", namespace="real", source_ids=[source_id], idempotency_key="research-parent-1"),
        [("A11", "cio_review", "CIO", [])],
        allow_semantic_reuse=False,
    )
    task_id = parent["tasks"][0]["id"]
    attempt = repository.create_attempt(task_id, CONFIG, {source_id: {"version": 1}})
    repository.commit_output(
        task_id,
        attempt["attempt_id"],
        AgentOutputPayload(
            status="needs_review",
            title="CIO needs evidence",
            summary="A material gap remains.",
            analysis="The answer is deferred pending evidence.",
            missing_data=["Need a dated filing."],
            decision_disposition="defer",
        ),
        "real",
        CONFIG,
    )
    # Missing-data reports now create an automatic repair. Close that worker
    # so this fixture specifically exercises a fresh manual request; active
    # child reuse and round limits have separate focused coverage.
    with repository.db.operation() as connection:
        automatic = connection.execute("SELECT repair_run_id FROM research_repairs WHERE root_run_id=?", (parent["run_id"],)).fetchall()
    assert len(automatic) == 1
    repository.control("run", automatic[0]["repair_run_id"], "cancel")
    child, reused = repository.create_evidence_research(
        parent["run_id"],
        ResearchRequest(instruction="Find the dated filing supporting the gap.", idempotency_key="research-child-1"),
    )
    assert reused is False and child["run_id"] != parent["run_id"]
    parent_row = repository.run_record(parent["run_id"])
    child_row = repository.run_record(child["run_id"])
    child_snapshot = json.loads(child_row["input_snapshot_json"])
    assert child_row["request"] == parent_row["request"]
    assert child_row["parent_run_id"] == parent["run_id"]
    assert child_row["followup_kind"] == "evidence_research"
    assert child_row["research_instruction"] == "Find the dated filing supporting the gap."
    assert child_snapshot["parent_research_context"]["missing_data"] == ["Need a dated filing."]
    assert child_snapshot["parent_research_context"]["output_id"]
    assert child["tasks"][0]["kind"] == "routing"

    child_task_id = child["tasks"][0]["id"]
    child_attempt = repository.create_attempt(child_task_id, CONFIG, {source_id: {"version": 1}})
    child_output = repository.commit_output(
        child_task_id,
        child_attempt["attempt_id"],
        AgentOutputPayload(
            status="completed",
            title="Repair route",
            summary="A route was recorded.",
            analysis="The repair route must still perform fresh discovery.",
            routing_plan=RoutingPlan(intent="direct_answer"),
        ),
        "real",
        CONFIG,
    )
    repository.consume_routing_plan(child["run_id"], RoutingPlan(intent="direct_answer"), routing_output_id=child_output["id"])
    child_detail = repository.run_snapshot(child["run_id"])
    assert child_detail["routing_plan"]["intent"] == "research"
    assert any(task["agent_id"] == "A01" and task["kind"] == "universe_discovery" for task in child_detail["tasks"])
    assert [source["id"] for source in child_detail["sources"]] == [source_id]
    assert child_detail["sources"][0]["title"] == "Retained filing"


def test_provider_free_route_does_not_promote_private_question_to_web_query() -> None:
    route = Repository._fallback_route("What should I do with my account balance and portfolio?", None, None)
    assert route["intent"] == "research"
    assert route["research_queries"] == []


def test_pm_revision_becomes_a_prerequisite_only_for_unstarted_cio(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    run, _ = repository.create_run(
        RunCreate(question="Revision dependency", namespace="real", idempotency_key="revision-edge-run"),
        [
            ("A03", "fundamental_review", "Analyst", []),
            ("A10", "pm_review", "PM", ["fundamental_review"]),
            ("A11", "cio_review", "CIO", ["pm_review"]),
        ],
        allow_semantic_reuse=False,
    )
    revision = repository.add_revision_task(run["run_id"], 1, ["A03: verify the dated revenue line"])
    assert revision is not None
    cio = next(task for task in repository.tasks_for_run(run["run_id"]) if task["agent_id"] == "A11")
    dependencies = json.loads(cio["dependency_json"])
    assert dependencies == ["pm_review", "pm_revision_1"]
    dependency_rows = repository.task_dependency_states(cio["id"])
    assert {item["id"] for item in dependency_rows} >= {revision["id"], next(task["id"] for task in repository.tasks_for_run(run["run_id"]) if task["kind"] == "pm_review")}

    # Once CIO has a current attempt, a later revision must not rewrite the
    # historical graph or make an already started decision wait retroactively.
    cio_attempt = repository.create_attempt(cio["id"], CONFIG, {})
    repository.mark_provider_started(cio["id"], cio_attempt["attempt_id"])
    assert repository.add_revision_task(run["run_id"], 2, ["A03: verify the dated revenue line again"]) is not None
    cio_after = next(task for task in repository.tasks_for_run(run["run_id"]) if task["id"] == cio["id"])
    assert json.loads(cio_after["dependency_json"]) == dependencies


def test_historical_cio_pm_relation_is_explicitly_inferred(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    run, _ = repository.create_run(
        RunCreate(question="Historical committee relation", namespace="real", idempotency_key="historical-review-run"),
        [("A10", "pm_review", "PM", []), ("A11", "cio_review", "CIO", ["pm_review"])],
        allow_semantic_reuse=False,
    )
    pm_id = run["tasks"][0]["id"]
    pm_attempt = repository.create_attempt(pm_id, CONFIG, {})
    repository.commit_output(
        pm_id,
        pm_attempt["attempt_id"],
        AgentOutputPayload(status="completed", title="Saved PM report", summary="PM accepted the packet.", analysis="Historical PM output."),
        "real",
        CONFIG,
    )
    cio_id = run["tasks"][1]["id"]
    cio_attempt = repository.create_attempt(cio_id, CONFIG, {})
    # Simulate a legacy completed task after migration 008: the new nullable
    # provider marker is absent, while the original task start is retained.
    with repository.db.operation() as conn:
        pm_created_at = conn.execute("SELECT created_at FROM outputs WHERE id=?", (repository.latest_outputs(run["run_id"])[0]["id"],)).fetchone()[0]
    with repository.db.transaction(immediate=True) as conn:
        conn.execute(
            "UPDATE tasks SET started_at=?,provider_started_at=NULL WHERE id=?",
            (pm_created_at, cio_id),
        )
    cio_view = repository.task_dict(repository.task(cio_id))
    assert cio_view["review_context"]["relation"] == "historical_inference"
    assert cio_view["review_context"]["label"] == "Latest PM report available before CIO started (historical inference)"


def test_namespace_export_does_not_bleed_scoped_audit_rows(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    real, _ = repository.create_run(
        RunCreate(question="Real follow-up export", namespace="real", idempotency_key="export-real-run"),
        [("A00", "routing", "Route", [])],
        allow_semantic_reuse=False,
    )
    demo, _ = repository.create_run(
        RunCreate(question="Demo follow-up export", namespace="demo", idempotency_key="export-demo-run"),
        [("A00", "routing", "Route", [])],
        allow_semantic_reuse=False,
    )
    real_followup = repository.add_followup(real["run_id"], "Keep this private real instruction.", "export-real-followup")
    demo_followup = repository.add_followup(demo["run_id"], "Keep this isolated demo instruction.", "export-demo-followup")

    real_audit = repository.export("real")["records"]["audit_log"]
    demo_audit = repository.export("demo")["records"]["audit_log"]
    real_ids = {row["id"] for row in real_audit}
    demo_ids = {row["id"] for row in demo_audit}
    assert any(row["subject_id"] == real["run_id"] for row in real_audit)
    assert any(row["subject_id"] == demo["run_id"] for row in demo_audit)
    assert real_followup["task_id"] not in {row["subject_id"] for row in demo_audit}
    assert demo_followup["task_id"] not in {row["subject_id"] for row in real_audit}
    assert real_ids.isdisjoint(demo_ids)
