"""Optional local Ollama adapter using its loopback HTTP API."""
from __future__ import annotations

import asyncio
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from ..config import Settings, settings
from ..schemas import ModelConfig
from .base import EventCallback, ProviderError, ProviderModel, ProviderResult


class OllamaAdapter:
    provider_id = "ollama"
    display_name = "Ollama local"
    billing_route = "local"

    def __init__(self, config: Settings | None = None):
        self.config = config or settings
        self.base_url = self.config.ollama_url.rstrip("/")
        self._cancelled: set[str] = set()
        self._preflight: set[tuple[str, str | None]] = set()
        self._lock = threading.Lock()

    def is_preflighted(self, config: ModelConfig) -> bool:
        return (config.model, config.reasoning_effort) in self._preflight

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None, timeout: float = 5) -> tuple[int, bytes]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return int(response.status), response.read(20_000_000)
        except urllib.error.HTTPError as exc:
            return int(exc.code), exc.read(4_000)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProviderError("unavailable", "Ollama is disconnected or unavailable.") from exc

    async def health(self):
        try:
            status, body = await asyncio.to_thread(self._request, "GET", "/api/version")
            if status != 200:
                raise ProviderError("unavailable", "Ollama returned an unavailable status.")
            version = json.loads(body.decode("utf-8", errors="replace")).get("version", "installed")
            models = await self.list_models()
            return {
                "provider": self.provider_id,
                "name": self.display_name,
                "available": True,
                "status": "ready",
                "reason": None,
                "billing_route": self.billing_route,
                "version": str(version),
                "models": models,
            }
        except (ProviderError, ValueError, json.JSONDecodeError):
            return {
                "provider": self.provider_id,
                "name": self.display_name,
                "available": False,
                "status": "disconnected",
                "reason": "Ollama is not running; install or start it to enable local inference.",
                "billing_route": self.billing_route,
                "models": (),
            }

    async def list_models(self) -> tuple[ProviderModel, ...]:
        try:
            status, body = await asyncio.to_thread(self._request, "GET", "/api/tags")
            if status != 200:
                return ()
            payload = json.loads(body.decode("utf-8", errors="replace"))
            models = payload.get("models", []) if isinstance(payload, dict) else []
            output = []
            for item in models:
                if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                    continue
                model_id = item["name"].strip()
                output.append(
                    ProviderModel(
                        model_id,
                        model_id,
                        True,
                        None,
                        ("none",),
                        {"structured_output": True, "streaming": True, "cancellation": False, "tools": False, "images": False},
                    )
                )
            return tuple(output)
        except (ProviderError, ValueError, json.JSONDecodeError):
            return ()

    async def preflight(self, config: ModelConfig, execute: bool) -> dict[str, Any]:
        if not execute:
            health = await self.health()
            model = next((item for item in health.get("models", ()) if item.id == config.model), None)
            return {
                "available": bool(health.get("available") and model),
                "status": "validated_installed_model" if model else health.get("status", "unsupported_model"),
                "reason": None if model else (health.get("reason") or "Model is not installed in Ollama."),
                "model": config.model,
                "reasoning_effort": config.reasoning_effort,
                "actual_execution": False,
            }
        schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False}
        try:
            result = await self.execute("preflight", 'Return exactly {"ok":true}.', config, schema, Path("."))
            if result.payload.get("ok") is not True:
                raise ProviderError("invalid_output", "Ollama probe did not return ok=true.")
            self._preflight.add((config.model, config.reasoning_effort))
            return {"available": True, "status": "executed", "reason": None, "model": config.model, "reasoning_effort": config.reasoning_effort, "actual_execution": True}
        except ProviderError as exc:
            return {"available": False, "status": exc.kind, "reason": exc.message, "model": config.model, "reasoning_effort": config.reasoning_effort, "actual_execution": True}

    async def execute(
        self,
        attempt_id: str,
        prompt: str,
        config: ModelConfig,
        schema: dict[str, Any],
        workdir: Path,
        on_event: EventCallback | None = None,
    ) -> ProviderResult:
        with self._lock:
            self._cancelled.discard(attempt_id)
        body = {
            "model": config.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "format": schema,
            "options": {"temperature": 0},
        }
        try:
            status, raw = await asyncio.to_thread(self._request, "POST", "/api/chat", body, max(30, self.config.codex_timeout_seconds))
        except ProviderError:
            raise
        with self._lock:
            cancelled = attempt_id in self._cancelled
            self._cancelled.discard(attempt_id)
        if cancelled:
            raise ProviderError("cancelled", "Ollama generation was cancelled.")
        if status in (401, 403):
            raise ProviderError("auth", "Ollama rejected the local request.")
        if status == 404:
            raise ProviderError("capability", "The selected Ollama model is not installed.")
        if status == 429:
            raise ProviderError("quota", "Ollama is busy; no hosted fallback is enabled.", retryable=True)
        if status != 200:
            raise ProviderError("execution", "Ollama did not return a valid response.", retryable=True)
        try:
            response = json.loads(raw.decode("utf-8", errors="replace"))
            content = response.get("message", {}).get("content")
            payload = json.loads(content) if isinstance(content, str) else content
            if not isinstance(payload, dict):
                raise ValueError
        except (ValueError, TypeError, json.JSONDecodeError):
            raise ProviderError("invalid_output", "Ollama returned non-JSON structured output.")
        usage = {
            key: response[key]
            for key in ("prompt_eval_count", "eval_count", "total_duration", "load_duration")
            if key in response and isinstance(response[key], (int, float))
        }
        if on_event:
            maybe = on_event(type("Event", (), {"type": "generation.completed", "message": None, "usage": usage})())
            if asyncio.iscoroutine(maybe):
                await maybe
        return ProviderResult(payload, usage or None, (), True)

    async def cancel(self, attempt_id: str) -> dict[str, Any]:
        with self._lock:
            self._cancelled.add(attempt_id)
        return {"cancelled": True, "status": "result_discard_requested", "reason": "Ollama HTTP cancellation is cooperative."}

    async def usage(self, attempt_id: str) -> dict[str, Any] | None:
        return None
