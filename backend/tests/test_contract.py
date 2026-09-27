from __future__ import annotations

import tempfile
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app.config import Settings
from backend.app.db import Database
from backend.app.memory.repository import Repository, _contains_numeric_token
from backend.app.research.calculations import issuance_share_count, risk_checks
from backend.app.schemas import AgentOutputPayload, Calculation, FactClaim, ImportRequest, ModelConfig, MonitoringRequest, RunCreate


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def repository(tmp_path: Path) -> Repository:
    config = Settings(data_dir=tmp_path, project_root=ROOT, codex_timeout_seconds=30)
    return Repository(config=config)


def source_request(namespace: str, key: str, content: str, supersedes_id: str | None = None) -> ImportRequest:
    return ImportRequest(namespace=namespace, kind="evidence", title="Dated 10-K filing", content=content, source_url="https://example.com/filing", publication_at="2026-09-01", observed_at="2026-09-01", supersedes_id=supersedes_id, idempotency_key=key)


def test_migrations_seed_all_roles_and_namespace_dedup(repository: Repository) -> None:
    assert len(repository.all_agents("real")) == 12
    first = repository.import_evidence(source_request("real", "source-real-1", "Revenue 10 million"))
    assert first["duplicate"] is False
    duplicate = repository.import_evidence(source_request("real", "source-real-2", "Revenue 10 million"))
    assert duplicate["duplicate"] is True
    # The same content in demo is a separate authoritative namespace.
    demo = repository.import_evidence(source_request("demo", "source-demo-1", "Revenue 10 million"))
    assert demo["duplicate"] is False


def test_amendment_emits_invalidation_for_dependent_task(repository: Repository) -> None:
    first = repository.import_evidence(source_request("real", "source-amend-1", "Shares 10", None))
    body = RunCreate(question="Review ABC", namespace="real", horizon="1m", ticker="ABC", source_ids=[first["source_id"]], idempotency_key="run-amend-1")
    created, _ = repository.create_run(body, [("A02", "filing_review", "Review", [])])
    task_id = created["tasks"][0]["id"]
    config = ModelConfig(provider="codex", model="gpt-5.6-luna", reasoning_effort="max")
    attempt = repository.create_attempt(task_id, config, {first["source_id"]: {"version": 1}})
    old_payload = AgentOutputPayload(
        status="completed",
        title="ABC filing",
        summary="Shares were reported.",
        analysis="The cited filing reports ten shares.",
        fact_claims=[FactClaim(claim="Shares outstanding", value="10", unit="shares", period="2026", source_ref=first["source_id"], locator="L1")],
        source_refs=[first["source_id"]],
    )
    old_output = repository.commit_output(task_id, attempt["attempt_id"], old_payload, "real", config)
    amended = repository.import_evidence(source_request("real", "source-amend-2", "Shares 11", first["source_id"]))
    assert amended["source_id"] != first["source_id"]
    task = repository.task(task_id)
    assert task["status"] == "completed"
    assert task["output_id"] == old_output["id"]
    assert amended["refresh_run_ids"]
    refresh = repository.run_snapshot(amended["refresh_run_ids"][0])
    assert refresh and refresh["tasks"][0]["status"] == "queued"
    assert refresh["tasks"][0]["input_refs"] == [amended["source_id"]]
    with repository.db.operation() as conn:
        invalidation = conn.execute("SELECT output_id,source_id,supersedes_source_id FROM invalidations WHERE task_id=?", (task_id,)).fetchone()
    assert invalidation and invalidation["output_id"] == old_output["id"]
    assert repository.output_with_sources(old_output["id"])["output"]["stale"] is True
    events = repository.events("real", 0)
    assert any(event["type"] == "queued" and event["task_id"] == task_id for event in events)


def test_model_precedence_and_frozen_account_snapshot(repository: Repository) -> None:
    source = repository.import_evidence(source_request("real", "source-model-1", "Dated evidence"))
    body = RunCreate(question="Review ABC", namespace="real", horizon="1m", ticker="ABC", source_ids=[source["source_id"]], model_override=ModelConfig(provider="codex", model="gpt-5.6-luna", reasoning_effort="max", profile="run"), idempotency_key="run-model-1")
    created, _ = repository.create_run(body, [("A03", "fundamental_review", "Review", [])])
    cfg, source_name = repository.resolve_model("A03", body.model_override)
    assert cfg.model == "gpt-5.6-luna" and source_name == "task_override"
    row = repository.run_record(created["run_id"])
    assert row["account_snapshot_id"]
    assert "portfolio_snapshot" in row["input_snapshot_json"]


def test_output_requires_attempt_sources_and_late_cancel_cannot_commit(repository: Repository) -> None:
    source = repository.import_evidence(source_request("real", "source-output-1", "Revenue 10 million"))
    body = RunCreate(question="Review ABC", namespace="real", horizon="1m", ticker="ABC", source_ids=[source["source_id"]], idempotency_key="run-output-1")
    created, _ = repository.create_run(body, [("A03", "fundamental_review", "Review", [])])
    task_id = created["tasks"][0]["id"]
    config = ModelConfig(provider="codex", model="gpt-5.6-luna", reasoning_effort="max")
    attempt = repository.create_attempt(task_id, config, {source["source_id"]: {"version": 1}})
    payload = AgentOutputPayload(status="completed", title="ABC", summary="Sourced", analysis="Sourced analysis", fact_claims=[FactClaim(claim="Revenue", value="10", unit="USD", period="2026", source_ref=source["source_id"], locator="p. 1")], source_refs=[source["source_id"]])
    committed = repository.commit_output(task_id, attempt["attempt_id"], payload, "real", config)
    assert committed["id"].startswith("out_")
    # A second task demonstrates the cancellation race independently.
    body2 = RunCreate(question="Review XYZ", namespace="real", horizon="1m", ticker="XYZ", source_ids=[source["source_id"]], idempotency_key="run-output-2")
    created2, _ = repository.create_run(body2, [("A03", "fundamental_review", "Review", [])])
    task2 = created2["tasks"][0]["id"]
    attempt2 = repository.create_attempt(task2, config, {source["source_id"]: {"version": 1}})
    repository.control("run", created2["run_id"], "cancel")
    with pytest.raises(RuntimeError, match="late_result_discarded"):
        repository.commit_output(task2, attempt2["attempt_id"], payload, "real", config)


def test_decimal_issuance_and_risk_gate() -> None:
    result = issuance_share_count(existing_assets="100", existing_shares="10", cash_raised="20", issue_price="2")
    assert result["assets_after"] == "120.00000000"
    assert result["shares_after"] == "20.00000000"
    checks = risk_checks(positions=[], risk_settings={"max_position_weight": "0.10", "max_sector_weight": "0.50", "cash_floor": "0.20"}, account_values_available=True, proposal={"target_position_weight": "0.20", "sector_weight_after": "0.40", "cash_weight_after": "0.30"})
    assert any(item["check"] == "max_position_weight" and item["status"] == "fail" for item in checks)
    assert any(item["check"] == "cash_floor" and item["status"] == "pass" for item in checks)


def test_calculations_are_recomputed_from_verified_source_lines(repository: Repository) -> None:
    source = repository.import_evidence(source_request("real", "source-calc-1", "Revenue 100 USD for year ended 2025-12-31\nAssets 20 USD for year ended 2025-12-31"))
    body = RunCreate(question="Calculate ratio", namespace="real", horizon="1m", ticker="ABC", source_ids=[source["source_id"]], idempotency_key="run-calc-1")
    created, _ = repository.create_run(body, [("A03", "fundamental_review", "Review", [])])
    task_id = created["tasks"][0]["id"]
    config = ModelConfig(provider="codex", model="gpt-5.6-luna", reasoning_effort="max")
    attempt = repository.create_attempt(task_id, config, {source["source_id"]: {"version": 1}})
    payload = AgentOutputPayload(
        status="completed",
        title="Ratio",
        summary="A cited ratio.",
        analysis="The ratio is independently recomputed.",
        fact_claims=[
            FactClaim(claim="Revenue", value="100", unit="USD", period="2025", period_end="2025-12-31", source_ref=source["source_id"], locator="L1"),
            FactClaim(claim="Assets", value="20", unit="USD", period="2025", period_end="2025-12-31", source_ref=source["source_id"], locator="L2"),
        ],
        calculations=[Calculation(label="Revenue/assets", value="999", unit="fraction", formula="999", operation="ratio", input_fact_indices=[0, 1])],
        source_refs=[source["source_id"]],
    )
    committed = repository.commit_output(task_id, attempt["attempt_id"], payload, "real", config)
    saved = repository.output_with_sources(committed["id"])["output"]
    assert saved["calculations"][0]["value"] == "5.00000000"
    assert saved["calculations"][0]["formula"].startswith("backend.ratio")


def test_numeric_source_locator_accepts_terminal_period_but_never_decimal_fragments() -> None:
    # Filing prose commonly ends numeric values with sentence punctuation.
    # The locator must accept those values while retaining exact-token
    # matching for nearby integers and decimals.
    assert _contains_numeric_token(Decimal("128528"), "Revenue was 128528.")
    assert _contains_numeric_token(Decimal("109433"), "Prior-year revenue was 109433.")
    assert _contains_numeric_token(Decimal("10"), "The full count is 10.")
    assert not _contains_numeric_token(Decimal("10"), "The count is 100.")
    assert _contains_numeric_token(Decimal("10.5"), "The reported value is 10.5.")
    assert not _contains_numeric_token(Decimal("10"), "The reported value is 10.5.")
    assert not _contains_numeric_token(Decimal("1.2"), "Malformed source value 1.2.3.")
    assert not _contains_numeric_token(Decimal("2.3"), "Malformed source value 1.2.3.")


def test_monitoring_claim_is_pending_until_ack(repository: Repository) -> None:
    rule = repository.add_monitoring(MonitoringRequest(name="source scan", enabled=True, timezone="UTC", interval_minutes=5, condition="research", source_ids=[], mode="interval_research"))
    with repository.db.transaction(immediate=True) as conn:
        conn.execute("UPDATE schedules SET next_execution_at=? WHERE id=?", ("2020-01-01T00:00:00Z", rule["id"]))
    first = repository.claim_due_schedules("2026-09-12T00:00:00Z")
    second = repository.claim_due_schedules("2026-09-12T00:00:01Z")
    assert first and second and first[0]["scheduled_for"] == second[0]["scheduled_for"]
    assert repository.ack_schedule(rule["id"], first[0]["scheduled_for"])
    assert repository.claim_due_schedules("2026-09-12T00:00:02Z") == []


def test_semantic_reuse_requires_same_policy_and_portfolio(repository: Repository) -> None:
    def make(key: str) -> RunCreate:
        return RunCreate(question="Stable cache question", namespace="real", horizon="1m", ticker="ABC", idempotency_key=key)

    first, _ = repository.create_run(make("semantic-cache-1"), [("A00", "routing", "Review", [])])
    repository.set_run_status(first["run_id"], "completed")
    same, reused = repository.create_run(make("semantic-cache-2"), [("A00", "routing", "Review", [])])
    assert reused and same.get("semantic_reuse") is True and same["run_id"] == first["run_id"]

    repository.set_policy("firm", None, ModelConfig(provider="ollama", model="local-test", reasoning_effort=None), None)
    policy_changed, policy_reused = repository.create_run(make("semantic-cache-3"), [("A00", "routing", "Review", [])])
    assert policy_reused is False and policy_changed["run_id"] != first["run_id"]

    with repository.db.transaction(immediate=True) as conn:
        conn.execute("INSERT INTO accounts(id,label,account_type,base_currency,namespace,reconciliation_status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)", ("acct-cache", "Changed account", "taxable", "CAD", "real", "unconfirmed", "2026-09-12T00:00:00Z", "2026-09-12T00:00:00Z"))
    portfolio_changed, portfolio_reused = repository.create_run(make("semantic-cache-4"), [("A00", "routing", "Review", [])])
    assert portfolio_reused is False and portfolio_changed["run_id"] != first["run_id"]


def test_monitoring_follows_amended_source_and_avoids_overlap(repository: Repository) -> None:
    original = repository.import_evidence(source_request("real", "monitor-lineage-1", "old source"))["source_id"]
    rule = repository.add_monitoring(MonitoringRequest(name="lineage", enabled=True, timezone="UTC", interval_minutes=5, condition="scan", source_ids=[original], mode="source_change"))
    with repository.db.transaction(immediate=True) as conn:
        conn.execute("UPDATE schedules SET next_execution_at=? WHERE id=?", ("2020-01-01T00:00:00Z", rule["id"]))
    before = repository.source_fingerprint("real", [original])
    amended = repository.import_evidence(source_request("real", "monitor-lineage-2", "new source", original))["source_id"]
    assert repository.source_head_ids("real", [original]) == [amended]
    assert repository.source_fingerprint("real", [original]) != before
    claimed = repository.claim_due_schedules("2026-09-12T00:00:00Z")[0]
    body = RunCreate(question="scan", namespace="real", horizon="event", source_ids=[amended], idempotency_key=f"schedule:{rule['id']}:{claimed['scheduled_for']}")
    run, _ = repository.create_run(body, [("A00", "routing", "scan", [])])
    assert repository.ack_schedule(rule["id"], claimed["scheduled_for"], run["run_id"], repository.source_fingerprint("real", [original]))
    with repository.db.transaction(immediate=True) as conn:
        conn.execute("UPDATE schedules SET next_execution_at=? WHERE id=?", ("2020-01-01T00:00:00Z", rule["id"]))
    assert repository.claim_due_schedules("2026-09-12T00:00:00Z") == []
