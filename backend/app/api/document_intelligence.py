"""HTTP boundary for the private, offline document research workspace."""
from __future__ import annotations

import re
from typing import Callable, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field

from ..config import Settings
from ..research.document_intelligence import AnalysisStore, analyze_transcript, compare_filings, enrich_transcript, import_document, MAX_BYTES
from ..research.library_store import register_ticker
from ..research.transcript_briefs import TranscriptBriefs


class _Metadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(default="", max_length=240)
    ticker: str = Field(default="", max_length=30)


class TranscriptRequest(_Metadata):
    period: str = Field(default="", max_length=80)
    text: str = Field(min_length=1, max_length=MAX_BYTES)


class ComparisonRequest(_Metadata):
    previous_period: str = Field(default="", max_length=80)
    current_period: str = Field(default="", max_length=80)
    previous_text: str = Field(min_length=1, max_length=MAX_BYTES)
    current_text: str = Field(min_length=1, max_length=MAX_BYTES)


class DocumentImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str = Field(min_length=1, max_length=255)
    content_base64: str = Field(min_length=1, max_length=6_666_668)


def create_document_router(config: Settings, mutation_guard: Callable | None = None, *, repo=None, briefs=None) -> APIRouter:
    router = APIRouter(prefix="/api/document-analysis", tags=["document analysis"])
    store = AnalysisStore(config.evidence_dir)
    explanations = briefs or TranscriptBriefs(config, repo)
    mutations = [Depends(mutation_guard)] if mutation_guard else []

    def retain(kind, body, result):
        record = store.save(kind, body.model_dump(), result)
        if repo is not None and body.ticker:
            with repo.db.transaction(immediate=True) as conn:
                register_ticker(conn, "real", body.ticker, origin="document", origin_ref=record["id"], created_at=record["created_at"])
        return record

    @router.post("/transcript", dependencies=mutations)
    def transcript(body: TranscriptRequest):
        try:
            result = analyze_transcript(body.text, body.ticker)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        record = retain("transcript", body, result)
        record["result"]["plain_language"] = explanations.load(result, record.get("namespace", "real"))
        return record

    @router.post("/compare", dependencies=mutations)
    def compare(body: ComparisonRequest):
        try:
            result = compare_filings(body.previous_text, body.current_text)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return retain("filing_comparison", body, result)

    @router.post("/import", dependencies=mutations)
    def document_import(body: DocumentImportRequest):
        try:
            return import_document(body.filename, body.content_base64)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get("/history")
    def history(limit: int = Query(default=100, ge=1, le=1000), namespace: Literal["real", "demo"] = "real"):
        return {"items": store.history(limit, namespace=namespace)}

    @router.get("/history/{identifier}")
    def detail(identifier: str):
        record = store.get(identifier)
        if record is None:
            raise HTTPException(status_code=404, detail="Document analysis was not found.")
        if record.get("kind") == "transcript":
            record["result"] = enrich_transcript(record["result"], record.get("inputs", {}).get("text"))
            record["result"]["plain_language"] = explanations.load(record["result"], record.get("namespace", "real"))
        return record

    @router.post("/history/{identifier}/explain", dependencies=mutations)
    async def explain(identifier: str):
        record = detail(identifier)
        if record.get("kind") != "transcript":
            raise HTTPException(status_code=422, detail="Only earnings transcripts have call explanations.")
        def guard():
            if repo is not None and repo.firm_paused():
                raise ValueError("Research is paused. Resume processing before preparing new explanations.")
        try:
            return await explanations.generate(record["result"], record.get("namespace", "real"), guard=guard)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail="The local reading-aid generation failed. Saved quotes remain available; retry when the provider is ready.") from exc

    @router.get("/history/{identifier}/transcript.txt", response_class=PlainTextResponse)
    def transcript_download(identifier: str):
        record = detail(identifier)
        if record.get("kind") != "transcript":
            raise HTTPException(status_code=422, detail="This document analysis is not an earnings transcript.")
        context = record["result"]["reading_context"]
        complete = context["full_text_available"]
        content = context["transcript_text"]
        if not complete:
            content = "Incomplete transcript: only retained analysis passages are available.\n\n" + content
        ticker = re.sub(r"[^A-Z0-9._-]", "-", str(record.get("ticker") or "").upper()[:30]).strip(".-_") or "earnings"
        suffix = "transcript" if complete else "retained-passages"
        filename = f"{ticker}-{identifier[:8]}-{suffix}.txt"
        return PlainTextResponse(content, headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    return router
