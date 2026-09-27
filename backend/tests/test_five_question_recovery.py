"""Crash/restart coverage for the receipt-backed five-question A11 barrier."""

from __future__ import annotations

import asyncio
import copy
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from backend.app.config import Settings
from backend.app.memory.repository import Repository
from backend.app.orchestration.workflow import Orchestrator
from backend.app.research.case_store import CaseDecisionStore
from backend.app.schemas import ModelConfig
from backend.tests.test_five_question_commit_path import (
    _DeterministicLaya,
    _DeterministicProvider,
    _DeterministicRegistry,
    _discovery_payload,
    _provider_payloads,
    _routing_payload,
    create_five_question_case,
)
from backend.tests.test_manager_commit_path import create_manager_case


ROOT = Path(__file__).resolve().parents[2]


def _repository(data_dir: Path) -> Repository:
    return Repository(
        config=Settings(
            project_root=ROOT,
            data_dir=data_dir,
            enable_market_connectors=False,
            enable_reddit_intake=False,
        )
    )


def _run_id(repository: Repository) -> str:
    runs = repository.runs(namespace="real")
    assert runs
    return str(runs[0]["id"])


def _a11(repository: Repository, run_id: str) -> Any:
    return next(row for row in repository.tasks_for_run(run_id) if row["agent_id"] == "A11")


class _NoCallProvider:
    """A provider boundary that makes accidental paid reruns fail loudly."""

    def __init__(self) -> None:
        self.calls = 0

    def is_preflighted(self, _config: ModelConfig) -> bool:
        return True

    async def preflight(self, config: ModelConfig, execute: bool) -> dict[str, Any]:
        return {
            "available": True,
            "status": "ready",
            "reason": None,
            "model": config.model,
            "reasoning_effort": config.reasoning_effort,
            "actual_execution": execute,
        }

    async def execute(self, *_args: Any, **_kwargs: Any) -> Any:
        self.calls += 1
        raise AssertionError("Astra/provider generation was rerun during local recovery")

    async def cancel(self, _attempt_id: str) -> dict[str, Any]:
        return {"cancelled": True}


class _NoCallRegistry:
    def __init__(self, provider: _NoCallProvider) -> None:
        self.provider = provider

    def adapter(self, _provider: str) -> _NoCallProvider:
        return self.provider

    @asynccontextmanager
    async def generation_slot(self, _provider: str, **_kwargs: Any):
        yield

    async def preflight(self, config: ModelConfig, execute: bool) -> dict[str, Any]:
        return await self.provider.preflight(config, execute)


class _TwoCandidateProvider(_DeterministicProvider):
    """Deterministic A03/A11 provider with two independently reviewed rows."""

    def __init__(self) -> None:
        super().__init__(self._payload)

    @staticmethod
    def _payload(packet: dict[str, Any]) -> Any:
        agent_id = str(packet.get("agent_id") or "").upper()
        if agent_id == "A00":
            return _routing_payload()
        if agent_id == "A01":
            return _discovery_payload()
        source_ids = list(packet.get("source_ids") or [])
        assert len(source_ids) >= 2
        payload = _provider_payloads(source_ids[0], source_ids[1])[agent_id]
        if agent_id not in {"A03", "A11"}:
            return payload
        raw = payload.model_dump(mode="json")
        second = copy.deepcopy(raw["candidate_briefs"][0])
        second["ticker"] = "DEF"
        second["instrument"] = "DEF Inc"
        raw["candidate_briefs"].append(second)
        return type(payload).model_validate(raw)


class _InterruptSecondPostLaya(_DeterministicLaya):
    def classify(self, state: str, questions: Any) -> dict[str, Any]:
        if "resolution" in questions and sum(item["phase"] == "post_astra" for item in self.calls) >= 1:
            raise asyncio.CancelledError()
        return super().classify(state, questions)


def _interrupted_before_post(data_dir: Path) -> tuple[Repository, str]:
    repository = _repository(data_dir)
    async def interrupt_before_post(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        with patch.object(Orchestrator, "_run_post_astra_laya", interrupt_before_post):
            create_five_question_case(data_dir, repository=repository)
    run_id = _run_id(repository)
    a11 = _a11(repository, run_id)
    assert a11["output_id"]
    assert a11["status"] == "waiting_review"
    assert CaseDecisionStore(repository).current(run_id, "real") is None
    return repository, run_id


def test_restart_recovers_committed_a11_before_post_without_provider_rerun(tmp_path: Path) -> None:
    repository, run_id = _interrupted_before_post(tmp_path)
    # Reproduce the historical restart bug: the process had already marked
    # the run terminal even though the durable A11 proposal still awaited
    # local review and canonical persistence.
    repository.set_run_status(
        run_id,
        "completed",
        event_type="completed",
        message="Synthetic premature terminal state.",
    )
    provider = _NoCallProvider()
    laya = _DeterministicLaya()

    asyncio.run(
        Orchestrator(
            repository,
            _NoCallRegistry(provider),
            repository.config,
            laya_runtime=laya,
        ).run(run_id)
    )

    decision = CaseDecisionStore(repository).current(run_id, "real")
    assert decision is not None
    a11 = _a11(repository, run_id)
    assert a11["status"] == "completed"
    assert repository.run_record(run_id)["status"] == "completed"
    assert provider.calls == 0
    assert [item["phase"] for item in laya.calls] == ["post_astra"]
    assert decision["source_output_id"] == a11["output_id"]
    post_reviews = repository.decision_model_reviews(
        run_id,
        namespace="real",
        attempt_id=a11["current_attempt_id"],
        phase="post_astra",
    )
    assert len(post_reviews) == 1

    # A second process restart must observe the same canonical revision and
    # receipt rather than appending another local review or case version.
    asyncio.run(
        Orchestrator(
            repository,
            _NoCallRegistry(provider),
            repository.config,
            laya_runtime=laya,
        ).run(run_id)
    )
    assert CaseDecisionStore(repository).current(run_id, "real") == decision
    assert len(
        repository.decision_model_reviews(
            run_id,
            namespace="real",
            attempt_id=a11["current_attempt_id"],
            phase="post_astra",
        )
    ) == 1
    assert [item["phase"] for item in laya.calls] == ["post_astra"]


def test_restart_reuses_existing_post_receipt_after_persistence_interrupt(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    original = Orchestrator._record_decision_if_needed

    def interrupt_after_post(self: Orchestrator, task: Any, run: Any, payload: Any, risk: Any, output_id: str | None = None) -> None:
        if task["agent_id"] == "A11":
            raise asyncio.CancelledError()
        original(self, task, run, payload, risk, output_id)

    with patch.object(Orchestrator, "_record_decision_if_needed", interrupt_after_post):
        with pytest.raises(asyncio.CancelledError):
            create_five_question_case(
                tmp_path,
                repository=repository,
                laya_runtime=_DeterministicLaya(),
            )

    run_id = _run_id(repository)
    a11 = _a11(repository, run_id)
    assert a11["status"] == "waiting_review"
    before = repository.decision_model_reviews(
        run_id,
        namespace="real",
        attempt_id=a11["current_attempt_id"],
        phase="post_astra",
    )
    assert len(before) == 1
    provider = _NoCallProvider()
    laya = _DeterministicLaya()
    asyncio.run(
        Orchestrator(
            repository,
            _NoCallRegistry(provider),
            repository.config,
            laya_runtime=laya,
        ).run(run_id)
    )

    after = repository.decision_model_reviews(
        run_id,
        namespace="real",
        attempt_id=a11["current_attempt_id"],
        phase="post_astra",
    )
    assert [item["id"] for item in after] == [item["id"] for item in before]
    assert laya.calls == []
    assert provider.calls == 0
    assert CaseDecisionStore(repository).current(run_id, "real") is not None
    assert _a11(repository, run_id)["status"] == "completed"


def test_recovery_honors_pause_then_resumes_without_provider_rerun(tmp_path: Path) -> None:
    repository, run_id = _interrupted_before_post(tmp_path)
    assert repository.control("run", run_id, "pause") == 1
    provider = _NoCallProvider()
    laya = _DeterministicLaya()
    asyncio.run(
        Orchestrator(
            repository,
            _NoCallRegistry(provider),
            repository.config,
            laya_runtime=laya,
        ).run(run_id)
    )
    assert repository.run_record(run_id)["status"] == "paused"
    assert CaseDecisionStore(repository).current(run_id, "real") is None
    assert laya.calls == []
    assert provider.calls == 0

    assert repository.control("run", run_id, "resume") == 1
    asyncio.run(
        Orchestrator(
            repository,
            _NoCallRegistry(provider),
            repository.config,
            laya_runtime=laya,
        ).run(run_id)
    )
    assert repository.run_record(run_id)["status"] == "completed"
    assert CaseDecisionStore(repository).current(run_id, "real") is not None
    assert [item["phase"] for item in laya.calls] == ["post_astra"]
    assert provider.calls == 0


def test_recovery_honors_cancel_and_leaves_legacy_case_unchanged(tmp_path: Path) -> None:
    repository, run_id = _interrupted_before_post(tmp_path / "prospective")
    assert repository.control("run", run_id, "cancel") == 1
    provider = _NoCallProvider()
    laya = _DeterministicLaya()
    asyncio.run(
        Orchestrator(
            repository,
            _NoCallRegistry(provider),
            repository.config,
            laya_runtime=laya,
        ).run(run_id)
    )
    assert repository.run_record(run_id)["status"] == "cancelled"
    assert CaseDecisionStore(repository).current(run_id, "real") is None
    assert laya.calls == []
    assert provider.calls == 0

    legacy_repo, legacy_decision, _legacy_output = create_manager_case(tmp_path / "legacy", reference="local")
    legacy_run_id = legacy_decision["run_id"]
    before = CaseDecisionStore(legacy_repo).current(legacy_run_id, "real")
    assert before is not None
    legacy_repo.set_run_status(legacy_run_id, "completed", event_type="completed", message="Synthetic legacy completion.")
    asyncio.run(
        Orchestrator(
            legacy_repo,
            _NoCallRegistry(_NoCallProvider()),
            legacy_repo.config,
        ).run(legacy_run_id)
    )
    assert CaseDecisionStore(legacy_repo).current(legacy_run_id, "real") == before


def test_restart_after_canonical_and_ledger_persist_is_idempotent(tmp_path: Path) -> None:
    """A crash after both decision writes must not append a second CIO row."""
    repository = _repository(tmp_path)
    original_finalize = repository.finalize_recovered_output
    interrupted = False

    def interrupt_once(task_id: str, attempt_id: str, output_id: str) -> bool:
        nonlocal interrupted
        if not interrupted:
            interrupted = True
            raise asyncio.CancelledError()
        return original_finalize(task_id, attempt_id, output_id)

    with patch.object(repository, "finalize_recovered_output", interrupt_once):
        with pytest.raises(asyncio.CancelledError):
            create_five_question_case(
                tmp_path,
                repository=repository,
                laya_runtime=_DeterministicLaya(),
            )

    run_id = _run_id(repository)
    a11 = _a11(repository, run_id)
    assert a11["status"] == "waiting_review"
    assert CaseDecisionStore(repository).current(run_id, "real") is not None
    with repository.db.operation() as connection:
        before_ledger = connection.execute(
            "SELECT COUNT(*) FROM decisions WHERE namespace=? AND run_id=? AND decision_type='cio' AND output_id=?",
            ("real", run_id, a11["output_id"]),
        ).fetchone()[0]
        before_versions = connection.execute(
            "SELECT COUNT(*) FROM case_decision_versions WHERE namespace=? AND run_id=? AND output_id=?",
            ("real", run_id, a11["output_id"]),
        ).fetchone()[0]
    assert before_ledger == 1
    assert before_versions == 1

    provider = _NoCallProvider()
    laya = _DeterministicLaya()
    asyncio.run(
        Orchestrator(
            repository,
            _NoCallRegistry(provider),
            repository.config,
            laya_runtime=laya,
        ).run(run_id)
    )

    with repository.db.operation() as connection:
        after_ledger = connection.execute(
            "SELECT COUNT(*) FROM decisions WHERE namespace=? AND run_id=? AND decision_type='cio' AND output_id=?",
            ("real", run_id, a11["output_id"]),
        ).fetchone()[0]
        after_versions = connection.execute(
            "SELECT COUNT(*) FROM case_decision_versions WHERE namespace=? AND run_id=? AND output_id=?",
            ("real", run_id, a11["output_id"]),
        ).fetchone()[0]
    assert after_ledger == 1
    assert after_versions == 1
    assert provider.calls == 0
    assert laya.calls == []
    assert _a11(repository, run_id)["status"] == "completed"

    # The repository boundary is idempotent too; a direct duplicate retry
    # cannot append a second CIO journal row for this immutable output.
    with repository.db.operation() as connection:
        existing = connection.execute(
            "SELECT id,disposition,rationale FROM decisions WHERE namespace=? AND run_id=? AND decision_type='cio' AND output_id=?",
            ("real", run_id, a11["output_id"]),
        ).fetchone()
    assert existing is not None
    duplicate = repository.commit_decision(
        namespace="real",
        run_id=run_id,
        ticker="ABC",
        decision_type="cio",
        disposition=str(existing["disposition"]),
        rationale=str(existing["rationale"]),
        dissent=[],
        risk_results=[],
        output_id=a11["output_id"],
    )
    assert duplicate["id"] == existing["id"]
    with repository.db.operation() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM decisions WHERE namespace=? AND run_id=? AND decision_type='cio' AND output_id=?",
            ("real", run_id, a11["output_id"]),
        ).fetchone()[0] == 1


def test_completed_historical_a11_projection_is_not_recovered_against_newer_current(tmp_path: Path) -> None:
    """Recovery keys use the immutable output, not the mutable current view."""
    case = create_five_question_case(tmp_path)
    repository = case.repository
    a11 = _a11(repository, case.run_id)
    assert a11["status"] == "completed"
    assert repository.run_record(case.run_id)["status"] == "completed"
    assert Orchestrator(
        repository,
        _NoCallRegistry(_NoCallProvider()),
        repository.config,
    )._contract_cio_projection_exists(case.run_id, "real", a11["output_id"])

    # A later continuation can make CaseDecisionStore.current point at a new
    # output while this older completed A11 already has its own projection.
    # The older output must not become a phantom recovery task.
    with patch.object(
        CaseDecisionStore,
        "current",
        return_value={"source_output_id": "newer-continuation-output"},
    ):
        assert not Orchestrator(
            repository,
            _NoCallRegistry(_NoCallProvider()),
            repository.config,
        )._contract_cio_recovery_needed(case.run_id)


def test_recovery_reuses_first_candidate_receipt_and_reviews_only_pending_candidate(tmp_path: Path) -> None:
    """A partial multi-candidate post review resumes without a provider rerun."""
    repository = _repository(tmp_path)
    provider = _TwoCandidateProvider()
    laya = _InterruptSecondPostLaya()
    with pytest.raises(asyncio.CancelledError):
        create_five_question_case(
            tmp_path,
            repository=repository,
            astra_adapter=_DeterministicRegistry(provider),
            laya_runtime=laya,
        )

    run_id = _run_id(repository)
    a11 = _a11(repository, run_id)
    assert a11["status"] == "waiting_review"
    before = repository.decision_model_reviews(
        run_id,
        namespace="real",
        attempt_id=a11["current_attempt_id"],
        phase="post_astra",
    )
    assert [item["candidate_key"] for item in before] == ["ABC"]
    assert CaseDecisionStore(repository).current(run_id, "real") is None
    provider_calls_before_restart = len(provider.calls)

    no_call_provider = _NoCallProvider()
    resumed_laya = _DeterministicLaya()
    asyncio.run(
        Orchestrator(
            repository,
            _NoCallRegistry(no_call_provider),
            repository.config,
            laya_runtime=resumed_laya,
        ).run(run_id)
    )

    after = repository.decision_model_reviews(
        run_id,
        namespace="real",
        attempt_id=a11["current_attempt_id"],
        phase="post_astra",
    )
    assert {item["candidate_key"] for item in after} == {"ABC", "DEF"}
    abc_before = next(item for item in before if item["candidate_key"] == "ABC")
    abc_after = next(item for item in after if item["candidate_key"] == "ABC")
    assert abc_after["id"] == abc_before["id"]
    assert [item["phase"] for item in resumed_laya.calls] == ["post_astra"]
    assert no_call_provider.calls == 0
    assert len(provider.calls) == provider_calls_before_restart
    assert CaseDecisionStore(repository).current(run_id, "real") is not None
    assert repository.run_record(run_id)["status"] == "completed"
