from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app.config import Settings
from backend.app.orchestration.workflow import Orchestrator
from backend.app.research.connectors import MarketBarsResult

def _clear_operational_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "ROAD2M_ENABLE_MARKET_CONNECTORS",
        "ROAD2M_ENABLE_REDDIT_INTAKE",
        "ROAD2M_ALPACA_DATA_FEED",
    ):
        monkeypatch.delenv(name, raising=False)


def test_settings_reads_allowlisted_root_dotenv_without_executing_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_operational_environment(monkeypatch)
    marker = tmp_path / "should-not-exist"
    (tmp_path / ".env").write_text(
        "export ROAD2M_ENABLE_MARKET_CONNECTORS='1'\n"
        "ROAD2M_ENABLE_REDDIT_INTAKE=on\n"
        "ROAD2M_ALPACA_DATA_FEED=iex\n"
        f"UNTRUSTED=$(touch {marker})\n",
        encoding="utf-8",
    )

    config = Settings(project_root=tmp_path, data_dir=tmp_path / "data")

    assert config.enable_market_connectors is True
    assert config.enable_reddit_intake is True
    assert config.alpaca_data_feed == "iex"
    assert not marker.exists()
    assert "UNTRUSTED" not in os.environ


def test_settings_environment_values_override_root_dotenv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / ".env").write_text(
        "ROAD2M_ENABLE_MARKET_CONNECTORS=0\n"
        "ROAD2M_ENABLE_REDDIT_INTAKE=1\n"
        "ROAD2M_ALPACA_DATA_FEED=sip\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ROAD2M_ENABLE_MARKET_CONNECTORS", "yes")
    monkeypatch.setenv("ROAD2M_ENABLE_REDDIT_INTAKE", "off")
    monkeypatch.setenv("ROAD2M_ALPACA_DATA_FEED", "IEX")

    config = Settings(project_root=tmp_path, data_dir=tmp_path / "data")

    assert config.enable_market_connectors is True
    assert config.enable_reddit_intake is False
    assert config.alpaca_data_feed == "iex"


def test_settings_rejects_an_invalid_alpaca_feed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ROAD2M_ALPACA_DATA_FEED", "boats")

    with pytest.raises(ValueError, match="ROAD2M_ALPACA_DATA_FEED"):
        Settings(project_root=tmp_path, data_dir=tmp_path / "data")


def test_sec_contact_reads_private_dotenv_and_environment_takes_precedence(tmp_path, monkeypatch):
    monkeypatch.delenv("ROAD2M_SEC_USER_AGENT", raising=False)
    (tmp_path / ".env").write_text('ROAD2M_SEC_USER_AGENT="fixture application contact"\n')
    assert Settings(project_root=tmp_path, data_dir=tmp_path).sec_user_agent == "fixture application contact"
    monkeypatch.setenv("ROAD2M_SEC_USER_AGENT", "process contact")
    assert Settings(project_root=tmp_path, data_dir=tmp_path).sec_user_agent == "process contact"
    assert Settings(project_root=tmp_path, data_dir=tmp_path, sec_user_agent="explicit contact").sec_user_agent == "explicit contact"


class _EvidenceRepository:
    def __init__(self) -> None:
        self.requests = []
        self.attached = []

    def import_evidence(self, request):
        self.requests.append(request)
        return {"source_id": f"source-{len(self.requests)}"}

    def append_run_sources(self, run_id, source_ids, *, reason):
        self.attached = list(source_ids)


class _MarketConnector:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def fetch_bars(self, symbol: str, timeframe: str, **kwargs):
        self.calls.append((symbol, timeframe, dict(kwargs)))
        if self.fail:
            raise RuntimeError("fixture provider failure")
        metadata = {
            "provider": "alpaca",
            "endpoint": "https://data.alpaca.markets/v2/stocks/bars",
            "symbols": [symbol],
            "timeframe": timeframe,
            "feed": kwargs.get("feed"),
            "adjustment": kwargs.get("adjustment"),
            "currency": "USD",
            "retrieved_at": "2026-09-13T04:00:00Z",
        }
        return MarketBarsResult(
            provider="alpaca",
            status="no_data",
            capability="ready",
            bars=tuple(),
            metadata=metadata,
        )


def _market_run() -> dict[str, object]:
    return {
        "namespace": "real",
        "ticker": "USO",
        "as_of": "2026-09-13T04:05:29Z",
        "input_snapshot_json": json.dumps({"research_candidates": []}),
    }


def test_workflow_forwards_configured_feed_and_date_only_asof() -> None:
    repository = _EvidenceRepository()
    connector = _MarketConnector()
    config = SimpleNamespace(alpaca_data_feed="iex")
    engine = Orchestrator(repository, None, config, market_connector=connector)

    _, packet = asyncio.run(engine._prepare_market_evidence("run-1", {"agent_id": "A04"}, _market_run()))

    assert len(connector.calls) == 3
    assert {call[2]["feed"] for call in connector.calls} == {"iex"}
    assert {call[2]["asof"] for call in connector.calls} == {"2026-09-13"}
    assert {item["feed"] for item in packet if item["timeframe"] != "1Week"} == {"iex"}


def test_workflow_exception_metadata_uses_configured_feed() -> None:
    repository = _EvidenceRepository()
    connector = _MarketConnector(fail=True)
    config = SimpleNamespace(alpaca_data_feed="iex")
    engine = Orchestrator(repository, None, config, market_connector=connector)

    _, packet = asyncio.run(engine._prepare_market_evidence("run-1", {"agent_id": "A04"}, _market_run()))

    assert len(packet) == 3
    assert {item["feed"] for item in packet} == {"iex"}
    assert {item["error_type"] for item in packet} == {"RuntimeError"}
