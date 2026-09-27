"""Typed local API for reusable Research Engine recipes."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..research.workflows import normalize_ticker


class WorkflowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ticker: str = Field(min_length=1, max_length=15)
    workflow: Literal["earnings"] = "earnings"
    namespace: Literal["real", "demo"] = "real"
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=200)

    @field_validator("ticker")
    @classmethod
    def ticker_format(cls, value):
        return normalize_ticker(value)


class PrimarySourceCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=8, max_length=3000)
    fiscal_period: str = Field(min_length=4, max_length=30)
    period_end: str = Field(min_length=10, max_length=10)
    published_at: str = Field(min_length=10, max_length=10)


class SourceRefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidates: list[PrimarySourceCandidate] | None = Field(default=None, max_length=6)


def create_workflow_router(service, mutation_guard=None):
    router = APIRouter(prefix="/api/research-workflows", tags=["research workflows"])
    mutations = [Depends(mutation_guard)] if mutation_guard else []

    def invoke(method, *args, **kwargs):
        try:
            return method(*args, **kwargs)
        except KeyError:
            raise HTTPException(404, "Research workflow was not found.") from None
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @router.get("/catalog")
    def catalog():
        return service.catalog()

    @router.get("/runs")
    def history(namespace: Literal["real", "demo"] = "real", limit: int = Query(25, ge=1, le=100)):
        return {"items": service.store.history(namespace, limit)}

    @router.post("/runs", status_code=202, dependencies=mutations)
    async def create(body: WorkflowRequest):
        identifier = invoke(service.store.create, **body.model_dump())
        if service.store.get(identifier)["status"] in {"queued", "running"}:
            service.schedule(identifier)
        return service.detail(identifier)

    @router.get("/runs/{identifier}")
    def detail(identifier: str):
        return invoke(service.detail, identifier)

    @router.post("/runs/{identifier}/retry", status_code=202, dependencies=mutations)
    async def retry(identifier: str):
        invoke(service.store.retry, identifier)
        service.schedule(identifier)
        return service.detail(identifier)

    @router.post("/runs/{identifier}/cancel", dependencies=mutations)
    async def cancel(identifier: str):
        invoke(service.store.get, identifier)
        return await service.cancel(identifier)

    @router.post("/runs/{identifier}/refresh-sources", status_code=202, dependencies=mutations)
    async def refresh_sources(identifier: str, body: SourceRefreshRequest):
        refreshed = invoke(service.store.refresh_sources, identifier, candidates=[item.model_dump() for item in body.candidates] if body.candidates is not None else None)
        service.schedule(refreshed)
        return service.detail(refreshed)

    @router.post("/runs/{identifier}/research", status_code=202, dependencies=mutations)
    async def research(identifier: str, reassess: bool = False):
        return invoke(service.handoff, identifier, reassess=reassess)

    return router
