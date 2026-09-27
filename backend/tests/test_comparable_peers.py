import hashlib
import json
from types import SimpleNamespace

import asyncio
import pytest

from backend.app.research.comparable_peers import acquire_peers, compile_peer_comparisons
from backend.app.providers.base import ProviderError
from backend.app.research.investment_process import ProcessPaused
from backend.tests.test_comparable_financials import source


def archive(sid, url, data):
    raw = data if isinstance(data, str) else json.dumps(data)
    return {"id": sid, "url": url, "content": raw, "content_hash": hashlib.sha256(raw.encode()).hexdigest(), "version": 1}


def packet():
    identity = archive("identity", "https://data.sec.gov/submissions/CIK0000001234.json", {"cik": "1234", "tickers": ["PEER"], "name": "ABC Corporation"})
    head = {"provider": "alpaca", "source_type": "market_bars", "metadata": {"symbols": ["PEER"], "timeframe": "1Day", "adjustment": "raw", "currency": "USD", "feed": "iex"}}
    prices = archive("prices", "https://data.alpaca.markets/v2/stocks/bars", "Road2M canonical research source\n" + json.dumps(head) + "\n" + json.dumps({"symbol": "PEER", "timestamp": "2026-02-27T05:00:00Z", "close": "100", "complete": True}))
    return [identity, source(), prices]


class PeerRepo:
    def __init__(self):
        self.saved_events = []
        self.retained = []

    def emit(self, namespace, event_type, *, run_id=None, payload=None, **kwargs):
        event = {"namespace": namespace, "run_id": run_id, "type": event_type, "payload": payload,
                 "sequence_id": len(self.saved_events) + 1}
        self.saved_events.append(event)
        return event

    def events(self, namespace, *, after=0, run_id=None, limit=5000):
        return [event for event in self.saved_events if event["namespace"] == namespace
                and event["run_id"] == run_id and event["sequence_id"] > after][:limit]

    def append_run_sources(self, run_id, source_ids, **kwargs):
        self.retained.extend(source_ids)

    def run_record(self, run_id):
        return {"input_snapshot_json": json.dumps({"source_versions": [{"id": sid} for sid in self.retained]})}


def test_peer_multiples_are_computed_from_separate_frozen_issuer_financials_and_price():
    rows = compile_peer_comparisons(packet(), ticker="SUBJECT", as_of="2026-03-01")
    assert {(row["metric"], row["multiple"]) for row in rows} == {("P/S", "1.000000"), ("P/NAV", "2.000000")}
    assert all(row["ticker"] == "PEER" and row["source_refs"] == ["identity", "prices", "sec"] for row in rows)
    assert all(row["as_of"] == "2026-02-27" for row in rows)
    assert compile_peer_comparisons(packet(), ticker="PEER", as_of="2026-03-01") == []
    assert compile_peer_comparisons(packet(), ticker="SUBJECT", as_of="2026-04-01") == []
    changed = packet()
    changed[1]["content_hash"] = "changed"
    assert compile_peer_comparisons(changed, ticker="SUBJECT", as_of="2026-03-01") == []
    dual_class = packet()
    dual_class[0] = archive("identity", "https://data.sec.gov/submissions/CIK0000001234.json", {"cik": "1234", "tickers": ["PEER", "PEER.B"], "name": "ABC Corporation"})
    assert compile_peer_comparisons(dual_class, ticker="SUBJECT", as_of="2026-03-01") == []


def test_peer_discovery_is_bounded_and_disabled_without_price_collection(monkeypatch):
    class Acquisition:
        dispatch_guard = None
        calls = []
        async def _discover(self, *args):
            self.calls.append("discovery")
            return {"peers": [{"ticker": f"P{i}", "rationale": "Comparable business."} for i in range(9)]}
        async def resolve(self, ticker):
            self.calls.append(ticker)
            return {"cik": "0000001234", "submissions_url": "url"}
        async def _json(self, *args): return {}, "page"
    async def financials(*args, **kwargs): return ["financials"]
    async def prices(*args, **kwargs): return ["prices"]
    monkeypatch.setattr("backend.app.research.assessment_pipeline._acquire_comparable_financials", financials)
    monkeypatch.setattr("backend.app.research.assessment_pipeline.acquire_valuation_price_history", prices)
    monkeypatch.setattr("backend.app.research.comparable_peers.archive_public_observation", lambda *args, **kwargs: {"source_id": "identity"})
    acquisition = Acquisition()
    package = {"namespace": "test", "ticker": "ABC", "package": {"company": {"name": "ABC"}}}
    repo = PeerRepo()
    assert asyncio.run(acquire_peers(repo, acquisition, package, "run", SimpleNamespace(enable_market_connectors=False))) == []
    assert not acquisition.calls
    assert asyncio.run(acquire_peers(repo, acquisition, package, "run", SimpleNamespace(enable_market_connectors=True))) == ["identity", "financials", "prices"]
    assert acquisition.calls == ["discovery", "P0", "P1", "P2"]
    assert asyncio.run(acquire_peers(repo, acquisition, package, "run", SimpleNamespace(enable_market_connectors=True))) == ["identity", "financials", "prices"]
    assert acquisition.calls == ["discovery", "P0", "P1", "P2"]
    repo.retained.clear()
    assert asyncio.run(acquire_peers(repo, acquisition, package, "run", SimpleNamespace(enable_market_connectors=True))) == []


PACKAGE = {"namespace": "test", "ticker": "ABC", "package": {"company": {"name": "ABC", "cik": "0000001234"}}}
CONFIG = SimpleNamespace(enable_market_connectors=True)


def test_optional_provider_failure_retains_bounded_provenance_and_is_not_replayed():
    class Acquisition:
        dispatch_guard = None
        def __init__(self):
            self.calls = 0
            self.discovery_records = []
        async def _discover(self, *args):
            self.calls += 1
            self.discovery_records.append({"attempt_id": "failed-discovery", "stage": "valuation_peers", "status": "failed",
                "record_path": "research-workflows/discovery/failed-discovery/provenance.json", "limits": {"search_queries": 6, "web_actions": 12}, "error_kind": "capability"})
            raise ProviderError("capability", "Query budget exhausted. " * 100, retryable=False)
    repo, acquisition = PeerRepo(), Acquisition()
    for _ in range(2):
        assert asyncio.run(acquire_peers(repo, acquisition, PACKAGE, "run", CONFIG)) == []
    assert acquisition.calls == 1
    receipt = repo.saved_events[-1]["payload"]
    assert receipt["status"] == "failed" and len(receipt["gaps"][0]) <= 400
    assert receipt["discovery"]["attempt_id"] == "failed-discovery"
    assert receipt["discovery"]["error_kind"] == "capability"
    assert receipt["discovery"]["limits"]["search_queries"] == 6
    assert asyncio.run(acquire_peers(repo, acquisition, PACKAGE, "different-run", CONFIG)) == []
    assert acquisition.calls == 2  # Only this case owns its failure receipt.


def test_optional_peer_resolution_provider_error_keeps_successful_peers(monkeypatch):
    class Acquisition:
        dispatch_guard = None
        calls = 0
        async def _discover(self, *args):
            self.calls += 1
            return {"peers": [{"ticker": "BAD", "rationale": "Unavailable"}, {"ticker": "GOOD", "rationale": "Comparable"}]}
        async def resolve(self, ticker):
            if ticker == "BAD":
                raise ProviderError("capability", "Peer identity discovery failed")
            return {"cik": "0000004321", "submissions_url": "url"}
        async def _json(self, *args): return {}, "page"
    async def financials(*args, **kwargs): return ["financials"]
    async def prices(*args, **kwargs): return ["prices"]
    monkeypatch.setattr("backend.app.research.assessment_pipeline._acquire_comparable_financials", financials)
    monkeypatch.setattr("backend.app.research.assessment_pipeline.acquire_valuation_price_history", prices)
    monkeypatch.setattr("backend.app.research.comparable_peers.archive_public_observation", lambda *args, **kwargs: {"source_id": "identity"})
    repo, acquisition = PeerRepo(), Acquisition()
    for _ in range(2):
        assert asyncio.run(acquire_peers(repo, acquisition, PACKAGE, "run", CONFIG)) == ["identity", "financials", "prices"]
    assert acquisition.calls == 1
    assert repo.saved_events[-1]["payload"]["status"] == "partial"
    assert "BAD" in repo.saved_events[-1]["payload"]["gaps"][0]


@pytest.mark.parametrize("exception", [asyncio.CancelledError, ProcessPaused])
@pytest.mark.parametrize("stage", ["guard", "discovery", "resolve"])
def test_cancellation_and_pause_propagate_without_terminal_failure_receipt(exception, stage):
    class Acquisition:
        def dispatch_guard(self):
            if stage == "guard": raise exception()
        async def _discover(self, *args):
            if stage == "discovery": raise exception()
            return {"peers": [{"ticker": "PEER", "rationale": "Comparable"}]}
        async def resolve(self, ticker): raise exception()
    repo = PeerRepo()
    with pytest.raises(exception):
        asyncio.run(acquire_peers(repo, Acquisition(), PACKAGE, "run", CONFIG))
    assert not repo.saved_events


def test_empty_completed_peer_search_is_not_replayed():
    class Acquisition:
        dispatch_guard = None
        calls = 0
        async def _discover(self, *args):
            self.calls += 1
            return {"peers": []}
    repo, acquisition = PeerRepo(), Acquisition()
    for _ in range(2):
        assert asyncio.run(acquire_peers(repo, acquisition, PACKAGE, "run", CONFIG)) == []
    assert acquisition.calls == 1 and repo.saved_events[-1]["payload"]["status"] == "completed"
