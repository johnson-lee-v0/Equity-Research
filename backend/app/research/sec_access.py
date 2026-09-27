"""Process-wide SEC pacing and bounded access-denial diagnostics.

The rate limit is shared across SEC hosts and all local worker threads. A
denied archive host cools down independently of the structured-data host.
Denial HTML is inspected only for known signals, never archived as evidence.
"""
from __future__ import annotations

import math
import threading
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Callable


DENIAL_MAX_BYTES = 8192


def is_sec_host(host: str) -> bool:
    host = host.casefold().rstrip(".")
    return host == "sec.gov" or host.endswith(".sec.gov")


def denial_diagnostics(status: int, body: bytes, retry_after: str | None) -> dict:
    """Classify only explicit SEC denial language; retain no remote prose."""
    text = body[:DENIAL_MAX_BYTES].decode("utf-8", errors="replace").casefold()
    if "undeclared automated tool" in text:
        category = "undeclared_automation"
    elif status == 429 or "request rate threshold exceeded" in text or "rate limit threshold" in text:
        category = "rate_limited"
    else:
        category = "access_denied"
    seconds = None
    if retry_after:
        try:
            seconds = float(retry_after)
        except ValueError:
            try:
                when = parsedate_to_datetime(retry_after)
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
                seconds = (when - datetime.now(timezone.utc)).total_seconds()
            except (ValueError, TypeError, OverflowError):
                pass
    if seconds is not None:
        seconds = max(0, min(3600, math.ceil(seconds))) if math.isfinite(seconds) else None
    return {"http_status": status, "category": category, "retry_after_seconds": seconds}


def denial_message(diagnostic: dict, *, suppressed: bool = False) -> str:
    explanation = {
        "undeclared_automation": "SEC identified an undeclared automated tool; verify the configured contact identity",
        "rate_limited": "SEC reported a request-rate limit",
        "access_denied": "SEC denied access; the response did not establish whether identity, network policy or rate caused it",
    }[diagnostic["category"]]
    prefix = "SEC request suppressed during cooldown after" if suppressed else "source returned"
    return f"{prefix} HTTP {diagnostic['http_status']}: {explanation}. Retry after {diagnostic['cooldown_seconds']} seconds."


class SecCooldownError(ValueError):
    def __init__(self, diagnostic: dict):
        self.diagnostic = diagnostic
        super().__init__(denial_message(diagnostic, suppressed=True))


class SecRequestGate:
    def __init__(self, *, interval: float = 0.25, clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        self._interval = interval
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._next_request = 0.0
        self._denials: dict[str, tuple[float, int, dict]] = {}

    def acquire(self, host: str, *, deadline: float) -> None:
        """Reserve one request start; fail fast during a host cooldown."""
        host = host.casefold().rstrip(".")
        while True:
            with self._lock:
                now = self._clock()
                denied = self._denials.get(host)
                if denied and denied[0] > now:
                    raise SecCooldownError({**denied[2], "cooldown_seconds": math.ceil(denied[0] - now)})
                delay = max(0.0, self._next_request - now)
                if now + delay >= deadline:
                    raise TimeoutError("SEC request pacing exceeded the page fetch time budget")
                if not delay:
                    self._next_request = now + self._interval
                    return
            self._sleep(delay)

    def denied(self, host: str, diagnostic: dict) -> dict:
        host = host.casefold().rstrip(".")
        with self._lock:
            prior = self._denials.get(host)
            count = min(5, prior[1] + 1) if prior else 1
            # No immediate retry for access denials. Repeated failures back
            # off to ten minutes; a valid Retry-After may extend to one hour.
            delay = max(min(600, 60 * 2 ** (count - 1)), diagnostic.get("retry_after_seconds") or 0)
            diagnostic = {**diagnostic, "cooldown_seconds": delay}
            self._denials[host] = (self._clock() + delay, count, diagnostic)
            return diagnostic

    def succeeded(self, host: str) -> None:
        with self._lock:
            host = host.casefold().rstrip(".")
            denied = self._denials.get(host)
            # An earlier request may finish after another thread was denied.
            # Its success must not cancel that newer, still-active cooldown.
            if denied and denied[0] <= self._clock():
                self._denials.pop(host, None)


sec_request_gate = SecRequestGate()
