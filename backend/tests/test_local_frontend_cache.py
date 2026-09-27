from pathlib import Path
import shutil

from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.memory.repository import Repository


def test_local_html_revalidates_after_build_and_api_keeps_its_route(tmp_path: Path):
    dist = tmp_path / "frontend" / "dist"
    dist.mkdir(parents=True)
    shell = dist / "index.html"
    shell.write_text('<script src="/assets/build-one.js"></script>')
    shutil.copytree(Path(__file__).resolve().parents[1] / "migrations", tmp_path / "backend" / "migrations")
    config = Settings(project_root=tmp_path, data_dir=tmp_path / "data", enable_reddit_intake=False, enable_market_connectors=False)
    repo = Repository(config=config)
    repo.control("firm", None, "pause")
    with TestClient(create_app(config, repo)) as client:
        first = client.get("/")
        assert first.headers["cache-control"] == "no-cache"
        assert first.status_code == 200
        shell.write_text('<script src="/assets/build-two-longer.js"></script>')
        second = client.get("/", headers={"If-None-Match": first.headers["etag"]})
        assert second.status_code == 200
        assert "build-two-longer" in second.text
        assert client.get("/api/health").json()["status"] == "ok"
