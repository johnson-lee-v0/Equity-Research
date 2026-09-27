from pathlib import Path
import json

from backend.app.config import Settings
from backend.app.memory.repository import Repository
from backend.app.research.case_store import CaseDecisionStore
from backend.app.schemas import AgentOutputPayload, CandidateDecisionBrief, ImportRequest, ModelConfig, RunCreate


def test_case_projection_is_idempotent_and_keeps_prior_decision(tmp_path):
    repo = Repository(config=Settings(project_root=Path(__file__).resolve().parents[2], data_dir=tmp_path, enable_market_connectors=False, enable_reddit_intake=False))
    run, _ = repo.create_run(RunCreate(question="Assess a synthetic fixture", ticker="EXMP", namespace="real", idempotency_key="case-store"), [("A11","cio_review","Review fixture",[]),("A11","cio_revision","Review changed fixture",[])])
    config = ModelConfig(provider="codex",model="gpt-6-astra",reasoning_effort="ultra")
    store = CaseDecisionStore(repo)
    outputs = []
    for index, task in enumerate(run["tasks"]):
        attempt = repo.create_attempt(task["id"], config, {})
        frozen_portfolio = {"accounts": [], "positions": [], "portfolio_policy": {"status": "proposed", "max_positions": 10}}
        repo.record_attempt_decision_inputs(attempt["attempt_id"], {"portfolio_snapshot": frozen_portfolio, "account_snapshot_id": f"fixture-snapshot-{index}", "deterministic_market": {"fixture_calculation_revision": index}})
        with repo.db.transaction(immediate=True) as conn:
            mutable = json.loads(conn.execute("SELECT input_snapshot_json FROM runs WHERE id=?", (run["run_id"],)).fetchone()[0])
            mutable["portfolio_snapshot"] = {"accounts": [], "positions": [], "portfolio_policy": {"status": "approved", "max_positions": 1}}
            conn.execute("UPDATE runs SET input_snapshot_json=? WHERE id=?", (json.dumps(mutable),run["run_id"]))
        payload = AgentOutputPayload(status="completed", title="Fixture review", summary=f"Reasoned fixture decline {index}", analysis="Synthetic research used only to test persistence.", decision_disposition="reject", candidate_briefs=[CandidateDecisionBrief(ticker="EXMP",stance="avoid",entry_advice=f"Fixture rationale {index}")])
        output = repo.commit_output(task["id"],attempt["attempt_id"],payload,"real",config)
        outputs.append(output["id"])
        saved = store.persist(run["run_id"],output["id"])
        assert saved["decision_revision"] == index+1
        assert saved["candidates"][0]["outcome"] == "decline"
        assert store.persist(run["run_id"],output["id"]) == saved
        assert store.calculations(run["run_id"])["portfolio_snapshot"] == frozen_portfolio
        assert store.calculations(run["run_id"])["fixture_calculation_revision"] == index
    assert store.current(run["run_id"],"real")["source_output_id"] == outputs[-1]
    assert len(store.history(run["run_id"],"real")) == 2
    original = store.current(run["run_id"], "real")
    with repo.db.operation() as conn:
        original_row = conn.execute(
            "SELECT payload_json,projection_key,output_id FROM case_decision_versions WHERE run_id=? AND revision=2",
            (run["run_id"],),
        ).fetchone()
        original_output_payload = json.loads(
            conn.execute("SELECT payload_json FROM outputs WHERE id=?", (outputs[-1],)).fetchone()[0]
        )
    validated_payload = AgentOutputPayload.model_validate(original_output_payload).model_copy(
        update={"summary": "Corrected projection summary from the revalidated CIO artifact."}
    )
    corrected = store.persist(
        run["run_id"],
        outputs[-1],
        correction_key="asset-identity-guard-v1",
        correction_reason="Rebuild the canonical projection after a code-only asset identity correction.",
        validated_payload=validated_payload,
        correction_input_hash="sha256:fixture-original-cio-artifact",
    )
    assert corrected is not None
    assert corrected["decision_revision"] == 3
    assert corrected["source_output_id"] == outputs[-1]
    assert corrected["projection_key"] == "asset-identity-guard-v1"
    assert corrected["correction_key"] == "asset-identity-guard-v1"
    assert corrected["correction"]["type"] == "code_only"
    assert corrected["correction_reason"].startswith("Rebuild the canonical")
    assert corrected["summary"] == "Corrected projection summary from the revalidated CIO artifact."
    assert corrected["correction_input_hash"] == "sha256:fixture-original-cio-artifact"
    assert corrected["correction"]["input_hash"] == "sha256:fixture-original-cio-artifact"
    assert store.persist(
        run["run_id"],
        outputs[-1],
        correction_key="asset-identity-guard-v1",
        correction_reason="Rebuild the canonical projection after a code-only asset identity correction.",
        validated_payload=validated_payload,
        correction_input_hash="sha256:fixture-original-cio-artifact",
    ) == corrected
    assert store.persist(run["run_id"], outputs[-1]) == corrected
    assert store.current(run["run_id"], "real") == corrected | {"stale": False}
    history = store.history(run["run_id"], "real")
    assert [item["decision_revision"] for item in history] == [3, 2, 1]
    assert original_row["projection_key"] == "original"
    assert original_row["output_id"] == outputs[-1]
    original_payload = dict(original)
    original_payload.pop("stale", None)
    assert json.loads(original_row["payload_json"]) == original_payload
    with repo.db.operation() as conn:
        rows = conn.execute(
            "SELECT revision,output_id,projection_key FROM case_decision_versions WHERE run_id=? ORDER BY revision",
            (run["run_id"],),
        ).fetchall()
        correction_event = conn.execute(
            "SELECT type,payload_json FROM events WHERE run_id=? AND type='case_decision_corrected' ORDER BY sequence_id DESC LIMIT 1",
            (run["run_id"],),
        ).fetchone()
    assert [(row["revision"], row["output_id"], row["projection_key"]) for row in rows] == [
        (1, outputs[0], "original"),
        (2, outputs[-1], "original"),
        (3, outputs[-1], "asset-identity-guard-v1"),
    ]
    assert correction_event["type"] == "case_decision_corrected"
    event_payload = json.loads(correction_event["payload_json"])
    assert event_payload["correction_key"] == "asset-identity-guard-v1"
    assert event_payload["correction_input_hash"] == "sha256:fixture-original-cio-artifact"
    assert "code-only correction" in event_payload["message"].lower()
    assert store.current(run["run_id"],"demo") is None
    assert store.watchlist("real")["items"] == []
    source = repo.import_evidence(ImportRequest(kind="evidence", title="Revised fixture", content="A synthetic revision.", idempotency_key="case-store-revision"))
    with repo.db.transaction(immediate=True) as conn:
        conn.execute("INSERT INTO invalidations(id,namespace,source_id,run_id,task_id,output_id,reason,created_at) VALUES(?,?,?,?,?,?,?,?)", ("fixture-invalidation","real",source["source_id"],run["run_id"],run["tasks"][-1]["id"],outputs[-1],"Fixture evidence changed","2026-09-13T00:00:00Z"))
    assert store.current(run["run_id"],"real")["stale"]
    assert store.list("real")[0]["stale"]
