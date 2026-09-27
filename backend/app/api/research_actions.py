"""Explicit, namespace-owned optional research controls."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..research.research_actions import ResearchActions


class ResearchActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    namespace: Literal["real", "demo"] = "real"
    kind: Literal["challenge", "evidence_retry"]
    idempotency_key: str = Field(min_length=8, max_length=200)
    gap_id: str | None = Field(default=None, min_length=1, max_length=200)
    source_id: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def one_scope(self):
        count = bool(self.gap_id) + bool(self.source_id)
        if (self.kind == "challenge" and count) or (self.kind == "evidence_retry" and count != 1):
            raise ValueError("Choose exactly one saved gap or evidence source for retry; challenge takes neither.")
        return self


def create_research_actions_router(repo, engine, mutation_guard):
    router = APIRouter(prefix="/api/runs", tags=["research-actions"])
    service = ResearchActions(repo)

    @router.get("/{run_id}/research-actions")
    async def read(run_id: str, namespace: Literal["real", "demo"] = Query("real")):
        try:
            return service.list(run_id, namespace)
        except KeyError:
            raise HTTPException(404, "Research case was not found.")

    @router.post("/{run_id}/research-actions", status_code=202, dependencies=[Depends(mutation_guard)])
    async def create(run_id: str, body: ResearchActionRequest):
        try:
            result = service.create(run_id, body)
        except KeyError:
            raise HTTPException(404, "Research case or selected evidence was not found.")
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        if not result["reused"] and not repo.firm_paused():
            engine.schedule(result["run_id"])
        return result

    return router
