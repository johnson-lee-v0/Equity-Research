"""Verify feature routers share the app's local boundary and durable archive."""
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app


def test_document_and_lab_routes_share_the_existing_local_guard(tmp_path):
    config = Settings(project_root=Path(__file__).resolve().parents[2], data_dir=tmp_path,
                      enable_market_connectors=False, enable_reddit_intake=False)
    payload = {"title": "Synthetic call", "text": "Alex Smith - CEO\nCustomer demand is strong and growth is improving."}
    with TestClient(create_app(config)) as client:
        assert client.get('/api/labs/catalog').status_code == 200
        assert client.get('/api/research-library').json()['total'] == 0
        assert client.post('/api/document-analysis/transcript', json=payload).status_code == 400
        assert client.post('/api/labs/backtests', json={}).status_code == 400
        assert client.post('/api/document-analysis/transcript', json=payload,
                           headers={'X-Road2M-Client': 'local-ui', 'Origin': 'https://foreign.example'}).status_code == 403
        assert client.get('/api/labs/catalog', headers={'Host': 'foreign.example'}).status_code == 403
        result = client.post('/api/document-analysis/transcript', json=payload,
                             headers={'X-Road2M-Client': 'local-ui'})
        assert result.status_code == 200
        record = result.json()
        assert (config.evidence_dir / 'document-analysis' / (record['id'] + '.json')).is_file()
    with TestClient(create_app(config)) as client:
        assert client.get('/api/document-analysis/history/' + record['id']).json() == record
        assert client.get('/api/document-analysis/history').json()['items'][0]['id'] == record['id']
