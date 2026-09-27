"""Local Obsidian vault inspection; GET requests never regenerate notes."""
import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from ..memory.shared import SharedMemoryService


def create_memory_router(repo, mutation_guard):
    router = APIRouter(prefix="/api/memory")
    service = SharedMemoryService(repo, repo.config.shared_memory_vault_path)

    @router.get("/graph")
    async def graph(namespace: Literal["real", "demo", "simulation"] = "real",
                    ticker: str | None = Query(None, max_length=30),
                    kind: str | None = Query(None, max_length=40),
                    query: str = Query("", max_length=300),
                    limit: int = Query(300, ge=1, le=1000)):
        return await asyncio.to_thread(service.graph, namespace=namespace,
            ticker=ticker or None, kind=kind or None, query=query, limit=limit)

    @router.get("/notes/{note_id:path}")
    async def note(note_id: str, namespace: Literal["real", "demo", "simulation"] = "real"):
        result = await asyncio.to_thread(service.note, note_id, namespace=namespace)
        if result is None:
            raise HTTPException(404, "This memory note is unavailable in the selected workspace.")
        return result

    @router.post("/sync", dependencies=[Depends(mutation_guard)])
    async def sync(namespace: Literal["real", "demo", "simulation"] = "real"):
        return await asyncio.to_thread(service.sync, namespace=namespace)

    return router
