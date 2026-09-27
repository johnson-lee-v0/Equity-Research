"""The memory reader stays local, read-only on GET and replayable per attempt."""
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.memory.repository import Repository
from backend.app.memory.shared import SharedMemoryService
from backend.tests.test_shared_memory_vault import setup_company


def test_memory_routes_preserve_namespace_and_only_explicit_sync_writes(tmp_path):
    config = Settings(project_root=Path(__file__).resolve().parents[2], data_dir=tmp_path,
        enable_market_connectors=False, enable_reddit_intake=False)
    repo = Repository(config=config)
    setup_company(repo)
    repo.control("firm", None, "pause")
    with TestClient(create_app(config, repo)) as client:
        vault = config.shared_memory_vault_path
        before = {p: p.stat().st_mtime_ns for p in vault.rglob("*") if p.is_file()}
        graph = client.get("/api/memory/graph?ticker=COST").json()
        assert graph["nodes"] and graph["edges"]
        note_id = graph["nodes"][0]["id"]
        detail = client.get(f"/api/memory/notes/{note_id}")
        assert detail.status_code == 200
        assert detail.json()["markdown"]
        assert client.get(f"/api/memory/notes/{note_id}?namespace=demo").status_code == 404
        assert client.get("/api/memory/graph?namespace=invalid").status_code == 422
        assert client.get("/api/memory/graph?ticker=META").json()["nodes"] == []
        assert client.post("/api/memory/sync").status_code == 400
        assert before == {p: p.stat().st_mtime_ns for p in vault.rglob("*") if p.is_file()}
        refreshed = client.post("/api/memory/sync", headers={"X-Road2M-Client": "local-ui"})
        assert refreshed.status_code == 200
        assert refreshed.json()["written"] == 0


def test_attempt_retains_exact_shared_memory_after_the_vault_changes(tmp_path):
    repo = Repository(config=Settings(project_root=Path(__file__).resolve().parents[2], data_dir=tmp_path))
    fixture = setup_company(repo)
    with repo.db.operation() as conn:
        attempt_id = conn.execute("SELECT current_attempt_id FROM tasks WHERE id=?", (fixture["task"],)).fetchone()[0]
    packet = repo.prepare_shared_memory(fixture["task"])
    assert packet["items"]
    first = repo.record_attempt_decision_inputs(attempt_id, {"shared_memory": packet})
    assert first["shared_memory"] == packet
    notes = repo.config.shared_memory_vault_path / "User Notes"
    notes.mkdir(exist_ok=True)
    (notes / "later.md").write_text("---\nnamespace: real\nticker: COST\n---\nA later opinion about renewal.\n")
    SharedMemoryService(repo).sync()
    repo.record_attempt_decision_inputs(attempt_id, {"shared_memory": {"items": []}})
    assert repo.attempt_decision_inputs(attempt_id)["shared_memory"] == packet


def test_vault_location_environment_is_honored_without_changing_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("ROAD2M_SHARED_MEMORY_VAULT_PATH", str(tmp_path / "my-vault"))
    config = Settings(data_dir=tmp_path / "data")
    assert config.shared_memory_vault_path == tmp_path / "my-vault"
    assert config.data_dir == tmp_path / "data"
