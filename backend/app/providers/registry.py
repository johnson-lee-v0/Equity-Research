"""Provider discovery and policy boundary."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
import asyncio

from ..config import Settings, settings
from ..schemas import ModelConfig
from .base import ProviderError, ProviderHealth, ProviderModel
from .codex import CodexAdapter
from .ollama import OllamaAdapter


class ProviderRegistry:
    def __init__(self, config: Settings | None = None):
        self.config = config or settings
        self.codex = CodexAdapter(self.config)
        self.ollama = OllamaAdapter(self.config)
        # Keep one process-wide generation pool per provider.  Reddit and
        # other background work get their own admission gate first; acquiring
        # that gate before the global slot prevents a waiting background task
        # from parking a user-capable global slot.
        codex_global = max(1, int(getattr(self.config, "codex_global_concurrency", 4) or 4))
        configured_background = max(0, int(getattr(self.config, "codex_background_concurrency", 3) or 0))
        codex_background = min(configured_background, max(0, codex_global - 1))
        self.codex_global_limit = codex_global
        self.codex_background_limit = codex_background
        # ``_slots`` remains the compatibility surface used by older tests and
        # injected tooling.  Its Codex semaphore is now the four-generation
        # global pool.
        self._slots = {"codex": asyncio.Semaphore(codex_global), "ollama": asyncio.Semaphore(1)}
        self._background_slots = {"codex": asyncio.Semaphore(codex_background)}

    def adapter(self, provider: str):
        if provider == "codex":
            return self.codex
        if provider == "ollama":
            return self.ollama
        raise ProviderError("unsupported_provider", "Only the Codex subscription and optional Ollama adapters are enabled.")

    @staticmethod
    def _is_background(origin: str | None, background: bool | None) -> bool:
        """Resolve the admission lane without changing model/role policy."""
        if background is not None:
            return bool(background)
        # The orchestrator passes the canonical root origin.  A missing
        # origin is the ordinary user route, preserving compatibility for
        # provider preflight and simulation callers.
        return str(origin or "").strip().casefold() in {"reddit", "background", "monitor"}

    @asynccontextmanager
    async def generation_slot(
        self,
        provider: str,
        origin: str | None = None,
        *,
        background: bool | None = None,
    ):
        """Bound generations with a reserved user lane for Codex.

        Background admission acquires its bounded lane before the global
        provider semaphore.  Since the default background bound is three and
        the global bound is four, one global slot remains available for user
        work even while Reddit generations are running.
        """
        slot = self._slots.get(provider)
        if slot is None:
            raise ProviderError("unsupported_provider", "Provider is not enabled.")
        background_slot = self._background_slots.get(provider) if self._is_background(origin, background) else None
        if background_slot is not None:
            await background_slot.acquire()
        try:
            await slot.acquire()
            try:
                yield
            finally:
                slot.release()
        finally:
            if background_slot is not None:
                background_slot.release()

    async def health(self) -> list[ProviderHealth | dict[str, Any]]:
        # Keep local status useful even when Ollama is absent.  The two checks
        # are independent, but are awaited sequentially to avoid waking a
        # sleeping local daemon while a hosted auth probe is running.
        codex = await self.codex.health()
        ollama = await self.ollama.health()
        return [codex, ollama]

    @staticmethod
    def _model_dict(model: ProviderModel) -> dict[str, Any]:
        return {
            "id": model.id,
            "name": model.name,
            "available": model.available,
            "reason": model.reason,
            "reasoning_efforts": list(model.reasoning_efforts),
            "capabilities": model.capabilities,
        }

    async def describe(self) -> dict[str, Any]:
        statuses = await self.health()
        items = []
        for status in statuses:
            if isinstance(status, ProviderHealth):
                items.append(
                    {
                        "id": status.provider,
                        "name": status.name,
                        "available": status.available,
                        "status": status.status,
                        "reason": status.reason,
                        "billing_route": status.billing_route,
                        "models": [self._model_dict(item) for item in status.models],
                    }
                )
            else:
                items.append(
                    {
                        "id": status["provider"],
                        "name": status["name"],
                        "available": status["available"],
                        "status": status["status"],
                        "reason": status["reason"],
                        "billing_route": status["billing_route"],
                        "models": [self._model_dict(item) for item in status.get("models", ())],
                    }
                )
        return {"items": items}

    async def preflight(self, config: ModelConfig, execute: bool) -> dict[str, Any]:
        return await self.adapter(config.provider).preflight(config, execute)
