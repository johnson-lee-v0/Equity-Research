"""Historical valuation prices reuse validated archives, never quote substitutes."""
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import pytest

from backend.app.config import Settings
from backend.app.memory.repository import Repository
from backend.app.research.assessment_pipeline import acquire_valuation_price_history
from backend.app.research.connectors import MarketBar, MarketBarsResult


@pytest.fixture
def history_acquisition(tmp_path, monkeypatch):
    config = Settings(project_root=Path(__file__).resolve().parents[2], data_dir=tmp_path, enable_market_connectors=True, enable_reddit_intake=False)
    repo = Repository(config=config)
    calls = []
    now = datetime.now(timezone.utc)
    metadata = {"symbols": ["ACME"], "timeframe": "1Day", "adjustment": "raw", "currency": "USD", "feed": config.alpaca_data_feed, "start": (now - timedelta(days=1840)).isoformat(), "end": now.isoformat(), "retrieved_at": now.isoformat(), "latest_bar_timestamp": (now - timedelta(days=1)).isoformat()}
    response = MarketBarsResult(provider="alpaca", status="ok", capability="ready", bars=(MarketBar(symbol="ACME", timestamp=(now - timedelta(days=1)).isoformat(), open="99", high="101", low="98", close="100", volume="1000", complete=True),), metadata=metadata)

    class Connector:
        error = None
        result = response

        def __init__(self, **kwargs):
            pass

        def fetch_bars(self, *args, **kwargs):
            calls.append((args, kwargs))
            if self.error:
                raise self.error
            return self.result

    monkeypatch.setattr("backend.app.research.connectors.AlpacaConnector", Connector)
    return repo, config, calls, Connector


def fetch(repo, config):
    return asyncio.run(acquire_valuation_price_history(repo, config, "ACME", namespace="real"))


def test_acquisition_requests_raw_five_year_data_and_reuses_verified_same_day_archive(history_acquisition):
    repo, config, calls, _ = history_acquisition
    first = fetch(repo, config)
    assert len(first) == 1
    args, kwargs = calls[0]
    assert args == ("ACME", "1Day")
    assert kwargs["adjustment"] == "raw" and kwargs["include_technicals"] is False
    assert (kwargs["end"] - kwargs["start"]).days >= 5 * 365
    assert kwargs["max_pages"] == 2 and kwargs["max_bars"] == 2500
    assert fetch(repo, config) == first
    assert len(calls) == 1


@pytest.mark.parametrize("change", ["hash", "symbols", "timeframe", "adjustment", "currency", "feed", "start", "wrong_day"])
def test_invalid_or_incompatible_cached_price_sources_are_fetched_again(history_acquisition, change):
    repo, config, calls, _ = history_acquisition
    sid = fetch(repo, config)[0]
    with repo.db.transaction(immediate=True) as conn:
        row = conn.execute("SELECT original_content FROM sources WHERE id=?", (sid,)).fetchone()
        lines = row[0].splitlines()
        header = json.loads(lines[1])
        if change not in {"hash", "wrong_day"}:
            header["metadata"][change] = {"symbols": ["OTHER"], "timeframe": "1Hour", "adjustment": "split", "currency": "CAD", "feed": "other", "start": "2099-01-01"}[change]
            lines[1] = json.dumps(header)
        content = "\n".join(lines)
        content_hash = "tampered" if change == "hash" else hashlib.sha256(content.encode()).hexdigest()
        conn.execute("UPDATE sources SET original_content=?,content_hash=? WHERE id=?", (content, content_hash, sid))
        if change == "wrong_day":
            conn.execute("UPDATE sources SET retrieval_at='2020-01-01T00:00:00Z' WHERE id=?", (sid,))
    fetch(repo, config)
    assert len(calls) == 2


@pytest.mark.parametrize("failure", ["disabled", "network", "unavailable", "empty"])
def test_optional_history_failure_does_not_invent_or_import_prices(history_acquisition, failure):
    repo, config, calls, connector = history_acquisition
    if failure == "disabled":
        config = replace(config, enable_market_connectors=False)
    elif failure == "network":
        connector.error = OSError("Network unavailable")
    elif failure == "unavailable":
        connector.result = MarketBarsResult(provider="alpaca", status="unavailable", capability="unavailable", bars=(), metadata={})
    else:
        connector.result = MarketBarsResult(provider="alpaca", status="ok", capability="ready", bars=(), metadata={})
    assert fetch(repo, config) == []
    assert len(calls) == (0 if failure == "disabled" else 1)
    assert repo.sources("real") == []
