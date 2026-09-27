"""Optional market context; saved investment decisions remain immutable."""
import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path

from ..db import utc_now
from ..research.earnings_sources import EarningsAcquisition
from ..research.secondary_valuation_history import refresh_history, saved_history


def create_valuation_history_router(repo, registry, config, mutation_guard):
    router = APIRouter(prefix="/api/valuation-history")
    locks: dict[tuple[str, str], asyncio.Lock] = {}

    @router.get("/{ticker}")
    async def read(ticker: str = Path(pattern=r"^[A-Z][A-Z0-9.-]{0,14}$"), namespace: Literal["real", "demo", "simulation"] = "real"):
        return await asyncio.to_thread(saved_history, repo, ticker=ticker, namespace=namespace, as_of=utc_now())

    @router.post("/{ticker}/refresh", dependencies=[Depends(mutation_guard)])
    async def refresh(ticker: str = Path(pattern=r"^[A-Z][A-Z0-9.-]{0,14}$"), namespace: Literal["real", "demo", "simulation"] = "real"):
        if namespace != "real":
            raise HTTPException(400, "Live market updates belong to the real workspace; demo and simulation evidence stays fixed.")
        if not config.enable_market_connectors:
            raise HTTPException(400, "Market collection is disabled in this workspace.")
        key = namespace, ticker
        lock = locks.setdefault(key, asyncio.Lock())
        if lock.locked():
            raise HTTPException(409, "This ticker's multiple history is already updating.")
        try:
            async with lock:
                return await refresh_history(repo, EarningsAcquisition(repo, registry, config, namespace=namespace), ticker=ticker, namespace=namespace, as_of=utc_now())
        except (ValueError, OSError) as exc:
            raise HTTPException(502, str(exc)[:400]) from exc
        finally:
            if not lock.locked():
                locks.pop(key, None)

    return router
