"""Public market headlines; no account state or model work is involved."""
import asyncio
from fastapi import APIRouter
from ..research.market_news import MarketNewsService


def create_market_news_router(service: MarketNewsService | None = None):
    router = APIRouter(prefix="/api")
    reader = service or MarketNewsService()

    @router.get("/market-news")
    async def market_news():
        return await asyncio.to_thread(reader.snapshot)

    return router
