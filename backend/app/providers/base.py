"""Provider-neutral execution types."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


@dataclass(frozen=True, slots=True)
class ProviderModel:
    id: str
    name: str
    available: bool
    reason: str | None
    reasoning_efforts: tuple[str, ...]
    capabilities: dict[str, bool]


@dataclass(frozen=True, slots=True)
class ProviderHealth:
    provider: str
    name: str
    available: bool
    status: str
    reason: str | None
    billing_route: str
    models: tuple[ProviderModel, ...] = ()


@dataclass(frozen=True, slots=True)
class ProviderEvent:
    type: str
    message: str | None = None
    usage: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ProviderResult:
    payload: dict[str, Any]
    usage: dict[str, Any] | None
    events: tuple[ProviderEvent, ...] = ()
    actual_execution: bool = True


class ProviderError(RuntimeError):
    def __init__(self, kind: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.retryable = retryable


EventCallback = Callable[[ProviderEvent], Awaitable[None] | None]

