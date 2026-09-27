"""API-boundary tests for the prospective five-question contract.

These tests stop at durable run creation.  They use temporary repositories or
small scheduler doubles, so no provider, network, or application data store
is touched.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import _monitoring_loop, create_app
from backend.app.memory.repository import Repository
from backend.app.orchestration.workflow import build_research_tasks
from backend.app.research.decision_questions import FIVE_QUESTION_CONTRACT
from backend.app.schemas import MonitoringRequest, RunCreate


ROOT = Path(__file__).resolve().parents[2]


def _config(data_dir: Path) -> Settings:
    return Settings(
        project_root=ROOT,
        data_dir=data_dir,
        enable_market_connectors=False,
        enable_reddit_intake=False,
    )


def _raw_input_snapshot(repository: Repository, run_id: str) -> dict[str, Any]:
    row = repository.run_record(run_id)
    assert row is not None
    return json.loads(row["input_snapshot_json"])


def _post_run(client: TestClient, idempotency_key: str, **extra: Any):
    payload: dict[str, Any] = {
        "namespace": "real",
        "question": "Review the retained ABC evidence.",
        "idempotency_key": idempotency_key,
    }
    payload.update(extra)
    return client.post("/api/runs", json=payload, headers={"X-Road2M-Client": "local-ui"})


def test_new_api_root_owns_five_question_marker_when_omitted_or_null(tmp_path: Path) -> None:
    config = _config(tmp_path)
    repository = Repository(config=config)
    # Keep the test at the API persistence boundary; no workflow task should
    # start a provider call while the firm is paused.
    repository.control("firm", None, "pause")

    with TestClient(create_app(config, repository)) as client:
        omitted = _post_run(client, "api-five-omitted")
        explicit_null = _post_run(client, "api-five-null", research_contract=None)

    assert omitted.status_code == 202
    assert explicit_null.status_code == 202
    for response in (omitted, explicit_null):
        run_id = response.json()["run_id"]
        snapshot = _raw_input_snapshot(repository, run_id)
        assert snapshot["research_contract"] == FIVE_QUESTION_CONTRACT


def test_scheduled_new_root_uses_marker_and_firm_pause_blocks_dispatch() -> None:
    class ScheduledRepository:
        def __init__(self) -> None:
            self.stop = asyncio.Event()
            self.claimed = False
            self.created_body: RunCreate | None = None
            self.created_tasks: list[tuple[str, str, str, list[str]]] | None = None
            self.scheduled_for: str | None = None
            self.emitted: list[dict[str, Any]] = []

        def claim_due_schedules(self) -> list[dict[str, Any]]:
            if self.claimed:
                return []
            self.claimed = True
            return [{
                "id": "schedule-fixture",
                "namespace": "real",
                "name": "Synthetic scan",
                "request": "Review the scheduled ABC evidence.",
                "source_ids": [],
                "scheduled_for": "2026-09-17T00:00:00Z",
                "mode": "interval_research",
            }]

        def source_head_ids(self, _namespace: str, source_ids: list[str]) -> list[str]:
            return source_ids

        def source_packet(self, _namespace: str, _source_ids: list[str]) -> list[dict[str, Any]]:
            return []

        def source_fingerprint(self, _namespace: str, _source_ids: list[str]) -> str:
            return "fixture-fingerprint"

        def create_run(self, body: RunCreate, task_plan: list[tuple[str, str, str, list[str]]], **_kwargs: Any):
            self.created_body = body
            self.created_tasks = task_plan
            return {"run_id": "scheduled-fixture-run"}, False

        def ack_schedule(self, _schedule_id: str, scheduled_for: str, *_args: Any, **_kwargs: Any) -> bool:
            self.scheduled_for = scheduled_for
            self.stop.set()
            return True

        def firm_paused(self) -> bool:
            return True

        def emit(self, namespace: str, event_type: str, *, payload: dict[str, Any]) -> None:
            self.emitted.append({"namespace": namespace, "type": event_type, "payload": payload})

    class Engine:
        def __init__(self) -> None:
            self.scheduled: list[str] = []

        def schedule(self, run_id: str) -> None:
            self.scheduled.append(run_id)

    repository = ScheduledRepository()
    engine = Engine()
    asyncio.run(_monitoring_loop(repository, engine, object(), repository.stop))

    assert repository.created_body is not None
    assert repository.created_body.research_contract == FIVE_QUESTION_CONTRACT
    assert repository.created_body.origin == "user"
    assert repository.created_tasks == build_research_tasks(
        repository.created_body.question,
        repository.created_body.horizon,
        repository.created_body.ticker,
        repository.created_body.namespace,
        lean=True,
    )
    assert engine.scheduled == []
    assert repository.scheduled_for == "2026-09-17T00:00:00Z"
    assert repository.emitted[-1]["type"] == "paused"


def test_historical_legacy_run_remains_readable_and_get_does_not_infer_laya(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    repository = Repository(config=config)
    body = RunCreate(
        namespace="real",
        question="Historical legacy case",
        idempotency_key="historical-legacy-api",
    )
    created, reused = repository.create_run(
        body,
        build_research_tasks(body.question, body.horizon, body.ticker, body.namespace, lean=True),
    )
    assert not reused
    before = _raw_input_snapshot(repository, created["run_id"])
    assert before.get("research_contract") is None

    import backend.app.research.laya_runtime as laya_runtime

    def no_get_runtime():
        raise AssertionError("GET must not infer or invoke Laya")

    monkeypatch.setattr(laya_runtime, "get_runtime", no_get_runtime)
    with TestClient(create_app(config, repository)) as client:
        response = client.get(f"/api/runs/{created['run_id']}", params={"namespace": "real"})

    assert response.status_code == 200
    assert response.json()["id"] == created["run_id"]
    assert _raw_input_snapshot(repository, created["run_id"]) == before
