import json

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from backend.app.api.document_intelligence import create_document_router
from backend.app.config import Settings
from backend.app.research.document_intelligence import AnalysisStore, analyze_transcript


TRANSCRIPT = """Jane Doe — CEO
Our revenue and earnings improved in the latest quarter.
Questions and Answers
Alice Smith — Analyst
Will you invest in additional capacity next year?
Jane Doe
Yes.
Alice Smith
Thanks.
"""


def test_download_preserves_full_utf8_transcript_and_short_utterances(tmp_path):
    config = Settings(data_dir=tmp_path)
    app = FastAPI()
    def guard(request: Request):
        if request.headers.get("x-road2m-client") != "local-ui":
            raise HTTPException(status_code=403)
    app.include_router(create_document_router(config, guard))
    with TestClient(app) as client:
        # Read-only downloads follow the history endpoint's security contract.
        assert client.post("/api/document-analysis/transcript", json={"text": TRANSCRIPT}).status_code == 403
        record = client.post("/api/document-analysis/transcript", json={"text": TRANSCRIPT, "ticker": "COST"}, headers={"x-road2m-client": "local-ui"}).json()
        response = client.get(f"/api/document-analysis/history/{record['id']}/transcript.txt")
        assert response.status_code == 200
        assert response.content == TRANSCRIPT.encode("utf-8")
        assert response.headers["content-type"] == "text/plain; charset=utf-8"
        assert response.headers["content-disposition"] == f'attachment; filename="COST-{record["id"][:8]}-transcript.txt"'
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"


def test_legacy_retained_passages_download_is_explicitly_incomplete(tmp_path):
    config = Settings(data_dir=tmp_path)
    store = AnalysisStore(config.evidence_dir)
    result = analyze_transcript(TRANSCRIPT)
    result.pop("reading_context")
    record = store.save("transcript", {"ticker": "COST"}, result)
    app = FastAPI()
    app.include_router(create_document_router(config))
    with TestClient(app) as client:
        response = client.get(f"/api/document-analysis/history/{record['id']}/transcript.txt")
        assert response.status_code == 200
        assert response.text.startswith("Incomplete transcript: only retained analysis passages are available.\n\n")
        assert "-retained-passages.txt" in response.headers["content-disposition"]
        assert "Will you invest" in response.text
        assert "Yes." not in response.text
        assert "Thanks." not in response.text
    assert "reading_context" not in json.loads((store.directory / f"{record['id']}.json").read_text())["result"]


def test_download_rejects_unknown_or_invalid_ids_and_other_analysis_types(tmp_path):
    config = Settings(data_dir=tmp_path)
    store = AnalysisStore(config.evidence_dir)
    comparison = store.save("filing_comparison", {}, {"summary": "Comparison"})
    app = FastAPI()
    app.include_router(create_document_router(config))
    with TestClient(app) as client:
        for identifier in ("missing", "0" * 32, "not-a-path..", "%2E%2E%2Fsecret"):
            assert client.get(f"/api/document-analysis/history/{identifier}/transcript.txt").status_code == 404
        response = client.get(f"/api/document-analysis/history/{comparison['id']}/transcript.txt")
        assert response.status_code == 422
        assert "not an earnings transcript" in response.json()["detail"]


def test_download_filename_cannot_inject_headers_or_paths(tmp_path):
    config = Settings(data_dir=tmp_path)
    store = AnalysisStore(config.evidence_dir)
    record = store.save("transcript", {"ticker": '../COST"\r\nX-Evil: yes', "text": TRANSCRIPT}, analyze_transcript(TRANSCRIPT))
    app = FastAPI()
    app.include_router(create_document_router(config))
    with TestClient(app) as client:
        response = client.get(f"/api/document-analysis/history/{record['id']}/transcript.txt")
        assert response.status_code == 200
        disposition = response.headers["content-disposition"]
        assert "/" not in disposition
        assert "\r" not in disposition and "\n" not in disposition
        assert "x-evil" not in response.headers
        assert disposition.count('"') == 2
        assert response.text == TRANSCRIPT
