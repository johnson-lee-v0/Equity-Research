"""Export boundaries for shared scenario storage and private connector setup."""
from __future__ import annotations

import json
import tarfile
from pathlib import Path

from backend.app.config import Settings
from backend.app.memory.repository import Repository
from backend.app.research.price_scenarios import build_price_scenarios
from backend.app.schemas import ImportRequest, ModelConfig, RunCreate
from backend.app.research.learning import LearningJournal, OutcomeRequest, freeze_decision
from backend.app.simulation.engine import SimulationService
from scripts.road2m_data import backup_command, collect_export


ROOT = Path(__file__).resolve().parents[2]


def test_price_results_follow_research_namespace_in_export(tmp_path: Path) -> None:
    repo = Repository(config=Settings(project_root=ROOT, data_dir=tmp_path))
    saved = {}
    for namespace in ("real", "demo"):
        run, _ = repo.create_run(
            RunCreate(question=f"{namespace} private question", namespace=namespace,
                      idempotency_key=f"export-{namespace}"),
            [("A07", "simulation_review", "Calculate scenarios", [])],
        )
        result = build_price_scenarios("TEST", [], as_of="2026-09-01T00:00:00Z")
        saved[namespace] = repo.record_candidate_simulation(
            run["tasks"][0]["id"], "TEST", result,
        )["simulation_id"]
    with repo.db.transaction() as conn:
        conn.execute(
            "INSERT INTO simulations(id,name,horizon,participants_json,initial_state_json,"
            "constraints_json,shocks_json,rounds,seed,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("participant-only", "Public fixture", "1m", "[]", "{}", "{}", "{}", 1, 1, "2026-09-01T00:00:00Z"),
        )
        for simulation_id in [*saved.values(), "participant-only"]:
            conn.execute(
                "INSERT INTO simulation_events(id,simulation_id,round_no,event_type,state_json,created_at) VALUES(?,?,?,?,?,?)",
                (f"event-{simulation_id}", simulation_id, 0, "fixture", "{}", "2026-09-01T00:00:00Z"),
            )
    with repo.db.operation() as conn:
        for namespace, expected in {**saved, "simulation": "participant-only"}.items():
            result = collect_export(conn, tmp_path, namespace)
            assert {row["id"] for row in result["tables"]["simulations"]} == {expected}
            assert {row["simulation_id"] for row in result["tables"]["simulation_events"]} == {expected}
            assert {row["simulation_id"] for row in result["tables"]["candidate_simulations"]} == (
                {expected} if namespace != "simulation" else set()
            )
            serialized = json.dumps(result)
            for other_namespace, other_id in saved.items():
                if other_namespace != namespace:
                    assert other_id not in serialized
    legacy = SimulationService(repo)
    assert {row["id"] for row in legacy.list()} == {"participant-only"}
    for simulation_id in saved.values():
        assert legacy.get(simulation_id) is None
        assert legacy.replay(simulation_id) is None


def test_backup_and_export_exclude_connector_configuration(tmp_path: Path) -> None:
    repo = Repository(config=Settings(project_root=ROOT, data_dir=tmp_path))
    private_config = tmp_path / "config" / "providers.json"
    private_config.parent.mkdir()
    marker = "SYNTHETIC_PRIVATE_CONNECTOR_VALUE"
    private_config.write_text(json.dumps({"reddit_client_secret": marker}))
    evidence = tmp_path / "evidence" / "real" / "fixture.txt"
    evidence.parent.mkdir(exist_ok=True)
    evidence.write_text("Public fixture source")
    archive_path = backup_command(tmp_path, tmp_path / "backups" / "test.tar.gz")
    with tarfile.open(archive_path) as archive:
        assert "evidence/real/fixture.txt" in archive.getnames()
        assert not any("providers.json" in name or name.startswith("config/") for name in archive.getnames())
        for member in archive.getmembers():
            if member.isfile():
                assert marker.encode() not in archive.extractfile(member).read()
    with repo.db.operation() as conn:
        assert marker not in json.dumps(collect_export(conn, tmp_path, "real"))


def test_manager_records_and_pdf_metadata_are_namespace_scoped_and_json_safe(tmp_path):
    repo=Repository(config=Settings(project_root=ROOT,data_dir=tmp_path,enable_market_connectors=False,enable_reddit_intake=False))
    expected={}
    for namespace in ("real","demo"):
        run,_=repo.create_run(RunCreate(question=f"Synthetic {namespace} manager export",namespace=namespace,idempotency_key=f"manager-{namespace}"),[("A00","route","Route",[])])
        attempt=repo.create_attempt(run["tasks"][0]["id"],ModelConfig(provider="codex",model="fixture"),{})
        repo.record_attempt_decision_inputs(attempt["attempt_id"],{"account_snapshot_id":f"snapshot-{namespace}"})
        source=repo.import_evidence(ImportRequest(namespace=namespace,kind="evidence",title=f"{namespace} document",content=f"Synthetic {namespace} extracted document",idempotency_key=f"document-{namespace}"))["source_id"]
        with repo.db.transaction(immediate=True) as conn:
            conn.execute("INSERT INTO source_documents(source_id,namespace,original_hash,original_bytes,metadata_json,created_at) VALUES(?,?,?,?,?,?)",(source,namespace,f"hash-{namespace}",b"%PDF-binary-fixture",json.dumps({"status":"unavailable","reason":"synthetic extraction failure"}),"2026-01-01"))
            freeze_decision(conn,{"run_id":run["run_id"],"decision_revision":1,"as_of":"2026-01-01T00:00:00Z","candidates":[{"ticker":"EXMP","outcome":"decline"}]},namespace)
        baseline=LearningJournal(repo).list(namespace)["items"][0]
        observation=LearningJournal(repo).record(baseline["id"],OutcomeRequest(namespace=namespace,idempotency_key="unavailable",evaluation_at="2026-01-02T00:00:00Z"))
        expected[namespace]={"source":source,"attempt":attempt["attempt_id"],"baseline":baseline["id"],"outcome":observation["id"]}
    with repo.db.operation() as conn:
        for namespace in ("real","demo"):
            exported=collect_export(conn,tmp_path,namespace)
            tables=exported["tables"]
            assert [row["source_id"] for row in tables["source_documents"]] == [expected[namespace]["source"]]
            assert "original_bytes" not in tables["source_documents"][0]
            assert [row["attempt_id"] for row in tables["attempt_decision_inputs"]] == [expected[namespace]["attempt"]]
            assert [row["id"] for row in tables["idea_baselines"]] == [expected[namespace]["baseline"]]
            assert [row["id"] for row in tables["idea_outcomes"]] == [expected[namespace]["outcome"]]
            serialized=json.dumps(exported)
            other="demo" if namespace == "real" else "real"
            assert all(value not in serialized for value in expected[other].values())
