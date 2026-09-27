from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.memory.repository import Repository
from backend.app.schemas import RoutingPlan


def test_open_question_api_creates_only_the_lean_research_graph(tmp_path):
    config = Settings(project_root=Path(__file__).resolve().parents[2], data_dir=tmp_path, enable_market_connectors=False, enable_reddit_intake=False)
    repo = Repository(config=config)
    repo.control("firm", None, "pause")
    with TestClient(create_app(config, repo)) as client:
        response = client.post('/api/runs', json={'namespace':'real','question':'What gold instruments should I consider given current macro conditions?','idempotency_key':'lean-gold-question-api'}, headers={'X-Road2M-Client':'local-ui','Origin':'http://127.0.0.1:8000'})
        assert response.status_code == 202
        run_id = response.json()['run_id']
        assert repo.is_lean_run(run_id)
        repo.consume_routing_plan(run_id, RoutingPlan(intent='research', horizon='3m', selected_analysts=['A02','A03','A04','A05','A09'], research_queries=['Find current primary gold-market evidence.']))
        assert [t['kind'] for t in repo.tasks_for_run(run_id)] == ['routing','universe_discovery','research_synthesis','cio_review']
