from __future__ import annotations

import pytest

from backend.app.research.sec_access import SecCooldownError, SecRequestGate, denial_diagnostics


class Clock:
    def __init__(self):
        self.now = 10.0
        self.waits = []

    def sleep(self, seconds):
        self.waits.append(seconds)
        self.now += seconds


def test_request_pacing_is_shared_across_sec_hosts_and_respects_deadline():
    clock = Clock()
    gate = SecRequestGate(clock=lambda: clock.now, sleep=clock.sleep)
    gate.acquire("www.sec.gov", deadline=20)
    gate.acquire("data.sec.gov", deadline=20)
    gate.acquire("www.sec.gov", deadline=20)
    assert clock.waits == [0.25, 0.25]
    with pytest.raises(TimeoutError, match="time budget"):
        gate.acquire("data.sec.gov", deadline=10.6)
    assert clock.now == 10.5


def test_cooldown_is_host_specific_and_repeated_denials_back_off():
    clock = Clock()
    gate = SecRequestGate(clock=lambda: clock.now, sleep=clock.sleep)
    diagnostic = denial_diagnostics(403, b"Undeclared automated tool", None)
    assert gate.denied("www.sec.gov", diagnostic)["cooldown_seconds"] == 60
    with pytest.raises(SecCooldownError):
        gate.acquire("www.sec.gov", deadline=20)
    gate.succeeded("www.sec.gov")  # An older in-flight success cannot clear it.
    with pytest.raises(SecCooldownError):
        gate.acquire("www.sec.gov", deadline=20)
    gate.acquire("data.sec.gov", deadline=20)
    clock.now += 60
    gate.acquire("www.sec.gov", deadline=100)
    assert gate.denied("www.sec.gov", diagnostic)["cooldown_seconds"] == 120
    clock.now += 120
    gate.acquire("www.sec.gov", deadline=200)
    gate.succeeded("www.sec.gov")
    assert gate.denied("www.sec.gov", diagnostic)["cooldown_seconds"] == 60


def test_retry_after_is_bounded_and_unknown_denial_does_not_claim_rate_limit():
    assert denial_diagnostics(403, b"Forbidden", "100000")["retry_after_seconds"] == 3600
    assert denial_diagnostics(403, b"Forbidden", "nan")["retry_after_seconds"] is None
    assert denial_diagnostics(403, b"Forbidden", "invalid")["category"] == "access_denied"
    assert denial_diagnostics(403, b"x" * 8192 + b"undeclared automated tool", None)["category"] == "access_denied"
