"""The case reader defers repeated archive text without losing research."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.api.run_reader import project_run_reader
from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.memory.repository import Repository
from backend.app.schemas import RunCreate


def _snapshot() -> dict:
    archive = "Archived source paragraph.\n" * 8_000
    return {
        "id": "run_reader", "namespace": "real", "status": "completed",
        "outputs": [{
            "id": "output_reader", "source_refs": ["source_reader"],
            "analysis": "The saved analysis remains complete.",
            "fact_claims": [{
                "claim_index": index, "claim": "Reported sales increased.",
                "source_ref": "source_reader", "source_version": "2",
                "locator": "lines 12–14", "line_start": 12, "line_end": 14,
                "excerpt": archive, "matched_excerpt": "Reported sales increased 5%.",
                "source_quote": "Reported sales increased 5%.",
                "validation_status": "validated", "recorded_validation_status": "validated",
                "semantic_status": "supported", "binding_checks": [{"key": "period", "status": "matched"}],
                "freshness": "current", "text_match": True,
            } for index in range(6)],
        }],
        "sources": [{"id": "source_reader", "content_hash": "retained-hash", "version": 2}],
        "current_decision": {"outcome": "watchlist", "candidates": [{"ticker": "ABC"}]},
        "decision_history": [{"revision": 1, "reason": "Earlier conclusion"}],
        "calculation_context": {"formula": "5 × 20", "value": "100"},
        "tasks": [{"id": "task_reader", "status": "completed"}],
        "events": [{"type": "task_completed", "payload": {"message": "Done"}}],
    }


def test_reader_keeps_all_research_and_visible_claim_text_without_mutating_history() -> None:
    full = _snapshot()
    before = deepcopy(full)
    reader = project_run_reader(full)
    assert full == before
    assert project_run_reader(reader) == reader
    for field in full.keys() - {"outputs"}:
        assert reader[field] == full[field]
    original_output, reader_output = full["outputs"][0], reader["outputs"][0]
    for field in original_output.keys() - {"fact_claims"}:
        assert reader_output[field] == original_output[field]
    for original, projected in zip(original_output["fact_claims"], reader_output["fact_claims"]):
        assert projected == {**original, "excerpt": original["matched_excerpt"]}
        assert (projected["matched_excerpt"] or projected["excerpt"]) == (original["matched_excerpt"] or original["excerpt"])
    deferred = reader["reader_projection"]["deferred_claim_excerpts"]
    assert [item["claim_index"] for item in deferred] == list(range(6))
    assert all(item["full_output_url"] == "/api/outputs/output_reader?namespace=real" for item in deferred)
    assert len(json.dumps(reader)) < len(json.dumps(full)) / 20
    # The returned view is independent even if a downstream reader enriches it.
    reader["current_decision"]["outcome"] = "decline"
    reader_output["fact_claims"][0]["binding_checks"].append({"key": "extra"})
    assert full == before


def test_reader_never_shortens_the_only_available_excerpt_or_small_excerpts() -> None:
    full = _snapshot()
    claims = full["outputs"][0]["fact_claims"]
    for claim, matched in zip(claims, [None, "", "   ", 12, {"text": "untrusted shape"}, "Recorded excerpt"]):
        claim["matched_excerpt"] = matched
    claims[-1]["excerpt"] = "Short contextual excerpt."
    reader = project_run_reader(full)
    assert reader["outputs"] == full["outputs"]
    assert reader["reader_projection"]["deferred_claim_excerpts"] == []
    assert project_run_reader({"outputs": [None, {"fact_claims": claims}]})["outputs"] == [None, {"fact_claims": claims}]


def test_reader_endpoint_is_opt_in_preserves_full_detail_and_namespace_isolation(tmp_path: Path, monkeypatch) -> None:
    config = Settings(
        project_root=Path(__file__).resolve().parents[2], data_dir=tmp_path,
        enable_market_connectors=False, enable_reddit_intake=False,
    )
    repository = Repository(config=config)
    repository.control("firm", None, "pause")
    created, _ = repository.create_run(RunCreate(namespace="real", question="Read saved ABC research.", idempotency_key="reader-fixture"), [])
    run_id = created["run_id"]
    stored_snapshot = repository.run_snapshot
    full = _snapshot()
    full["id"] = run_id

    def snapshot(identifier, namespace=None):
        persisted = stored_snapshot(identifier, namespace)
        return {**persisted, **deepcopy(full)} if persisted else None

    monkeypatch.setattr(repository, "run_snapshot", snapshot)
    original_output = deepcopy(full["outputs"][0])
    monkeypatch.setattr(repository, "output_with_sources", lambda identifier, namespace=None: deepcopy(original_output) if identifier == "output_reader" and namespace == "real" else None)
    with TestClient(create_app(config, repository)) as client:
        default = client.get(f"/api/runs/{run_id}?namespace=real")
        explicit_full = client.get(f"/api/runs/{run_id}?namespace=real&view=full")
        reader = client.get(f"/api/runs/{run_id}?namespace=real&view=reader")
        assert default.status_code == explicit_full.status_code == reader.status_code == 200
        assert default.json() == explicit_full.json()
        assert "reader_projection" not in default.json()
        assert reader.json() == project_run_reader(default.json())
        assert client.get("/api/outputs/output_reader?namespace=real").json() == original_output
        assert client.get(f"/api/runs/{run_id}?namespace=demo&view=reader").status_code == 404
        assert client.get(f"/api/runs/{run_id}?namespace=real&view=unsupported").status_code == 422
        assert client.get(f"/api/runs/{run_id}?namespace=real").json() == default.json()
    assert full["outputs"][0] == original_output
