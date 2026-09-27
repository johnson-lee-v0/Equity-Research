"""The cached model identity must match the provider configuration actually dispatched."""
from pathlib import Path
import json

import pytest

from backend.app.agents.model_policy import role_model
from backend.app.config import Settings
from backend.app.memory.repository import Repository
from backend.app.orchestration.workflow import build_research_tasks
from backend.app.research.decision_questions import FIVE_QUESTION_CONTRACT
from backend.app.schemas import ModelConfig, RunCreate


@pytest.fixture
def repo(tmp_path):
    return Repository(config=Settings(project_root=Path(__file__).resolve().parents[2], data_dir=tmp_path,
        codex_model="gpt-6-luna", codex_reasoning="high", enable_market_connectors=False, enable_reddit_intake=False))


@pytest.mark.parametrize("lean", [False, True])
def test_every_current_task_assignment_matches_cached_model_identity(repo, lean):
    agents = [f"A{index:02d}" for index in range(12)]
    with repo.db.operation() as conn:
        snapshot = repo._policy_snapshot_conn(conn, agents, lean=lean)
    for agent in agents:
        assert repo.resolve_model(agent, lean=lean)[0].model_dump() == snapshot[agent]
        assert snapshot[agent] == role_model(agent).model_dump()


def test_disabled_legacy_role_policy_cannot_change_dispatched_model(repo):
    repo.set_policy("role", "A03", ModelConfig(provider="codex", model="gpt-5.6-luna", reasoning_effort="max"), None)
    with repo.db.transaction(immediate=True) as conn:
        conn.execute("UPDATE model_policies SET enabled=0 WHERE scope='agent' AND scope_id='A03'")
        snapshot = repo._policy_snapshot_conn(conn, ["A03"], lean=True)
    assert repo.resolve_model("A03", lean=True)[0].model_dump() == snapshot["A03"]


def test_root_reddit_snapshot_records_the_contract_model_not_an_ordinary_role_override(repo):
    repo.set_policy("role", "A00", ModelConfig(provider="codex", model="gpt-6-sol", reasoning_effort="high"), None)
    body = RunCreate(question="Review the company's operating momentum from this Reddit idea.", namespace="real", origin="reddit", idempotency_key="reddit-policy-snapshot")
    created, _ = repo.create_run(body, build_research_tasks(body.question, body.horizon, body.ticker, body.namespace, lean=True), allow_semantic_reuse=False)
    snapshot = json.loads(repo.run_record(created["run_id"])["input_snapshot_json"])
    assert snapshot["model_policy"]["A00"] == repo.resolve_model("A00", reddit_intake=True)[0].model_dump()


def test_explicit_custom_policy_changes_both_legacy_and_lean_cache_identity(repo):
    custom = ModelConfig(provider="codex", model="gpt-6-astra", reasoning_effort="high", profile="researcher")
    repo.set_policy("role", "A03", custom, None)
    for lean in (False, True):
        with repo.db.operation() as conn:
            snapshot = repo._policy_snapshot_conn(conn, ["A03"], lean=lean)
        assert repo.resolve_model("A03", lean=lean)[0] == custom
        assert snapshot["A03"] == custom.model_dump()


def test_stronger_contract_override_is_preserved_and_weak_override_is_rejected(repo):
    stronger = ModelConfig(provider="codex", model="gpt-6-astra", reasoning_effort="high")
    weak = ModelConfig(provider="codex", model="gpt-6-luna", reasoning_effort="high")
    config, policy = repo.resolve_model("A11", stronger, research_contract=FIVE_QUESTION_CONTRACT)
    assert config == stronger
    assert policy == "task_override"
    _, policy = repo.resolve_model("A11", weak, research_contract=FIVE_QUESTION_CONTRACT)
    assert policy == "contract_incompatible_override"
