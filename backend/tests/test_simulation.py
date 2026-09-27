from __future__ import annotations

import asyncio
import json
import sys
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings
from app.db import Database, json_loads
from app.memory.repository import Repository
from app.providers.base import ProviderError, ProviderResult
from app.schemas import ImportRequest, ModelConfig, SimulationCreate
from app.simulation.engine import MAX_LONG_EXPOSURE, SimulationService


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def repository(tmp_path):
    config = Settings(project_root=Path(__file__).resolve().parents[2], data_dir=tmp_path / "data")
    database = Database(config=config)
    return Repository(database=database, config=config)


def simulation_body(**overrides):
    values = {
        "title": "Declared shock mechanism",
        "question": "How does a bounded cohort react to an exogenous shock?",
        "horizon": "3m",
        "participants": 6,
        "rounds": 5,
        "seed": 4242,
        "initial_price": "100.00",
        "shock_percent": "-20",
        "source_ids": [],
        "use_llm": False,
        "idempotency_key": "simulation-test-1",
    }
    values.update(overrides)
    return SimulationCreate(**values)


class FakeAdapter:
    def __init__(self, payload=None, error=None):
        self.payload = payload or {
            "actions": [
                {"participant_id": "p01", "action": "buy", "target_weight": "0.25"},
                {"participant_id": "p03", "action": "hold", "target_weight": "0.10"},
            ]
        }
        self.error = error
        self.calls = []

    async def execute(self, *args):
        self.calls.append(args)
        if self.error:
            raise self.error
        return ProviderResult(self.payload, {"input_tokens": 12, "output_tokens": 8})


class FakeProviders:
    def __init__(self, adapter: FakeAdapter, preflight=None):
        self.fake_adapter = adapter
        self.preflight_result = preflight or {"available": True, "actual_execution": False}
        self.preflight_calls = []
        self.slot_calls = []

    def adapter(self, provider):
        assert provider == "codex"
        return self.fake_adapter

    async def preflight(self, config, execute):
        self.preflight_calls.append((config, execute))
        return self.preflight_result

    @asynccontextmanager
    async def generation_slot(self, provider):
        self.slot_calls.append(provider)
        yield


def import_source(repository, namespace, key, content):
    return repository.import_evidence(
        ImportRequest(
            namespace=namespace,
            kind="evidence",
            title=f"{namespace} source",
            content=content,
            source_url=None,
            publication_at=None,
            observed_at=None,
            supersedes_id=None,
            idempotency_key=key,
        )
    )["source_id"]


def test_rules_ledger_limits_conservation_and_exact_replay(repository):
    service = SimulationService(repository)
    created = service.create(simulation_body())
    run(service.run(created["simulation_id"]))
    record = service.get(created["simulation_id"])

    assert record is not None
    assert record["status"] == "completed"
    assert len(record["participants"]) == 6
    assert len(record["rounds"]) == 5
    assert record["rounds"][0]["price_transition"]["mechanism"] == "declared_shock"
    assert Decimal(record["rounds"][0]["price"]) == Decimal("80.00000000")
    for transition in [item["price_transition"] for item in record["rounds"][1:]]:
        assert abs(Decimal(transition["drift_percent"])) <= Decimal("1.00000000")
        assert transition["mechanism"] == "seeded_bounded_drift"

    participants = record["participants"]
    assert len({item["horizon"] for item in participants}) == 6
    assert len({item["style"] for item in participants}) == 6
    assert len({tuple(item["information_set"]) for item in participants}) == 6
    assert any(item["information_mode"] == "shock_informed" for item in participants)
    assert any(item["information_mode"] == "delayed_price" for item in participants)
    for item in participants:
        assert Decimal(item["initial_cash"]) == Decimal("100000.00000000")
        assert Decimal(item["initial_shares"]) == Decimal("0.00000000")
        assert Decimal(item["cash"]) >= 0
        assert Decimal(item["shares"]) >= 0
        assert Decimal(item["exposure"]) <= MAX_LONG_EXPOSURE

    initial_state = record["inputs"]["initial_state"]
    opening_cash = sum((Decimal(item["cash"]) for item in initial_state["participants"]), Decimal("0")) + Decimal(initial_state["external_market_counterparty"]["cash"])
    opening_shares = sum((Decimal(item["shares"]) for item in initial_state["participants"]), Decimal("0")) + Decimal(initial_state["external_market_counterparty"]["shares"])
    for round_record in record["rounds"]:
        state = round_record["state"]
        assert Decimal(state["total_cash"]) == opening_cash
        assert Decimal(state["total_shares"]) == opening_shares
        for action in round_record["actions"]:
            assert Decimal(action["cash_after"]) >= 0
            assert Decimal(action["shares_after"]) >= 0
            assert Decimal(action["exposure_after"]) <= MAX_LONG_EXPOSURE

    baseline = record["rules_only_baseline"]
    assert [item["price"] for item in baseline["rounds"]] == [item["price"] for item in record["rounds"]]
    assert service.replay(created["simulation_id"])["matches"] is True


def test_replay_detects_tampered_action_history(repository):
    service = SimulationService(repository)
    created = service.create(simulation_body(idempotency_key="simulation-tamper-1"))
    run(service.run(created["simulation_id"]))
    assert service.replay(created["simulation_id"])["matches"] is True

    with repository.db.transaction(immediate=True) as conn:
        row = conn.execute(
            "SELECT id,action_json FROM simulation_events WHERE simulation_id=? AND event_type='action' ORDER BY rowid LIMIT 1",
            (created["simulation_id"],),
        ).fetchone()
        action = json.loads(row["action_json"])
        action["quantity"] = "999999.00000000"
        conn.execute("UPDATE simulation_events SET action_json=? WHERE id=?", (json.dumps(action), row["id"]))

    replay = service.replay(created["simulation_id"])
    assert replay["matches"] is False
    assert replay["replay"]["mismatches"]


def test_sources_are_single_namespace_and_snapshotted(repository):
    real_id = import_source(repository, "real", "simulation-source-real", "real retained text")
    demo_id = import_source(repository, "demo", "simulation-source-demo", "demo retained text")
    service = SimulationService(repository)

    with pytest.raises(ValueError, match="mixed namespaces"):
        service.create(simulation_body(source_ids=[real_id, demo_id], idempotency_key="simulation-mixed-1"))
    with pytest.raises(ValueError, match="missing"):
        service.create(simulation_body(source_ids=["missing-source"], idempotency_key="simulation-missing-1"))

    created = service.create(simulation_body(source_ids=[real_id], idempotency_key="simulation-snapshot-1"))
    with repository.db.transaction(immediate=True) as conn:
        conn.execute("UPDATE sources SET original_content='tampered live row' WHERE id=?", (real_id,))
    record = service.get(created["simulation_id"])
    assert record["inputs"]["source_namespace"] == "real"
    assert record["inputs"]["sources"][0]["content"] == "real retained text"
    assert record["inputs"]["sources"][0]["id"] == real_id
    assert record["inputs"]["sources"][0]["content_hash"]
    assert record["inputs"]["sources"][0]["locator"] is None

    with repository.db.transaction(immediate=True) as conn:
        conn.execute(
            "INSERT INTO sources(id,namespace,source_type,title,retrieval_at,content_hash,original_content,created_at) VALUES(?,?,?,?,?,?,?,?)",
            ("simulation-source-row", "simulation", "other", "forbidden", "2026-09-12T00:00:00Z", "hash", "synthetic", "2026-09-12T00:00:00Z"),
        )
    with pytest.raises(ValueError, match="simulation sources"):
        service.create(simulation_body(source_ids=["simulation-source-row"], idempotency_key="simulation-source-sim-1"))


def test_llm_uses_one_fake_call_and_keeps_rules_baseline(repository):
    adapter = FakeAdapter()
    providers = FakeProviders(adapter)
    service = SimulationService(repository, providers=providers)
    created = service.create(simulation_body(use_llm=True, idempotency_key="simulation-llm-1"))
    run(service.run(created["simulation_id"]))
    record = service.get(created["simulation_id"])

    assert record["status"] == "completed"
    assert len(providers.preflight_calls) == 1
    assert providers.preflight_calls[0][1] is False
    assert len(providers.slot_calls) == 1
    assert len(adapter.calls) == 1
    assert adapter.calls[0][3]["required"] == ["actions"]
    assert record["model"]["provider"] == "codex"
    assert record["model"]["model"] == "gpt-6-sol"
    assert record["recorded_responses"][0]["response"] == adapter.payload
    assert record["participants"][0]["target_weight"] == "0.25000000"
    assert [item["price"] for item in record["rules_only_baseline"]["rounds"]] == [item["price"] for item in record["rounds"]]
    assert service.replay(created["simulation_id"])["matches"] is True
    assert len(adapter.calls) == 1


def test_invalid_model_actions_are_recorded_as_deterministic_noops(repository):
    payload = {
            "actions": [
                {"participant_id": "p01", "action": "buy", "target_weight": "0.90"},
            {"participant_id": "p03", "action": "not-an-action", "target_weight": "0.10"},
        ]
    }
    adapter = FakeAdapter(payload=payload)
    service = SimulationService(repository, providers=FakeProviders(adapter))
    created = service.create(simulation_body(use_llm=True, idempotency_key="simulation-invalid-1"))
    run(service.run(created["simulation_id"]))
    record = service.get(created["simulation_id"])

    assert record["status"] == "completed"
    response = record["recorded_responses"][0]
    assert len(response["repairs"]) == 2
    assert all(item["deterministic"] is True for item in response["repairs"])
    round_one = record["rounds"][0]
    model_actions = {item["participant_id"]: item for item in round_one["actions"] if item["participant_id"] in {"p01", "p03"}}
    assert set(model_actions) == {"p01", "p03"}
    for action in model_actions.values():
        assert action["action"] == "hold"
        assert Decimal(action["quantity"]) == 0
        assert action["repair"] is not None
    with repository.db.operation() as conn:
        repairs = conn.execute(
            "SELECT repair_attempt_json FROM simulation_events WHERE simulation_id=? AND round_no=1 AND event_type='action' AND repair_attempt_json IS NOT NULL",
            (created["simulation_id"],),
        ).fetchall()
    assert len(repairs) == 2
    assert service.replay(created["simulation_id"])["matches"] is True


def test_provider_failure_is_visible_and_does_not_fallback(repository):
    adapter = FakeAdapter()
    providers = FakeProviders(adapter, preflight={"available": False, "status": "auth_required", "reason": "subscription authentication unavailable"})
    service = SimulationService(repository, providers=providers)
    created = service.create(simulation_body(use_llm=True, idempotency_key="simulation-provider-failure-1"))
    run(service.run(created["simulation_id"]))
    record = service.get(created["simulation_id"])

    assert record["status"] == "failed"
    assert record["failure"]["status"] == "blocked"
    assert "authentication" in record["failure"]["reason"]
    assert record["blocking_reason"] == record["failure"]["reason"]
    assert record["rounds"] == []
    assert record["rules_only_baseline"]["rounds"]
    assert adapter.calls == []
    with pytest.raises(ValueError, match="incomplete"):
        service.replay(created["simulation_id"])


def test_idempotency_and_run_claim_prevent_duplicate_records(repository):
    service = SimulationService(repository)
    body = simulation_body(idempotency_key="simulation-idempotent-1")
    first = service.create(body)
    second = service.create(body)
    assert first["simulation_id"] == second["simulation_id"]
    assert second["reused"] is True
    run(service.run(first["simulation_id"]))
    run(service.run(first["simulation_id"]))
    with repository.db.operation() as conn:
        count = conn.execute("SELECT COUNT(*) FROM simulations WHERE idempotency_key=?", (body.idempotency_key,)).fetchone()[0]
        events = conn.execute("SELECT COUNT(*) FROM simulation_events WHERE simulation_id=?", (first["simulation_id"],)).fetchone()[0]
    assert count == 1
    assert events == body.rounds * (body.participants + 1)


def test_replay_rejects_incomplete_run(repository):
    service = SimulationService(repository)
    created = service.create(simulation_body(idempotency_key="simulation-incomplete-1"))
    with pytest.raises(ValueError, match="incomplete"):
        service.replay(created["simulation_id"])
