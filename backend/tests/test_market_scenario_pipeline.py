from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from backend.app.orchestration.workflow import Orchestrator
from backend.app.research.connectors import AlpacaConnector, HttpResponse, MarketBar, MarketBarsResult, ProviderCredentials
from backend.app.research.price_scenarios import build_price_scenarios


UTC = timezone.utc


def test_alpaca_stock_bars_result_declares_scoped_currency_metadata() -> None:
    def get(_url, _params, _headers, _timeout):
        return HttpResponse(
            200,
            {},
            {"bars": {"TEST": [{"t": "2026-09-11T00:00:00Z", "o": "9", "h": "11", "l": "8", "c": "10", "v": "100"}]}},
        )

    connector = AlpacaConnector(
        ProviderCredentials(alpaca_api_key_id="key", alpaca_api_secret_key="secret"),
        http_get=get,
        now=lambda: datetime(2026, 9, 12, tzinfo=UTC),
    )
    result = connector.fetch_bars("TEST", "1Day", start="2026-09-01", end="2026-09-12")

    assert result.metadata["currency"] == "USD"
    assert "stock-bars request scope" in result.metadata["currency_basis"]


def _source(
    source_id: str,
    *,
    symbols: list[str] | None = None,
    currency: str | None = "USD",
    timeframe: str = "1Day",
    feed: str | None = "sip",
    adjustment: str | None = "raw",
    rows: list[dict[str, object]] | None = None,
    title: str = "Uninformative title",
) -> dict[str, object]:
    metadata: dict[str, object] = {"timeframe": timeframe}
    if symbols is not None:
        metadata["symbols"] = symbols
    if currency is not None:
        metadata["currency"] = currency
    if feed is not None:
        metadata["feed"] = feed
    if adjustment is not None:
        metadata["adjustment"] = adjustment
    header = {
        "provider": "alpaca",
        "source_type": "market_bars",
        "metadata": metadata,
    }
    content_rows = rows or []
    content = "\n".join([
        "Road2M canonical research source",
        json.dumps(header, sort_keys=True),
        *(json.dumps(row, sort_keys=True) for row in content_rows),
    ])
    return {
        "id": source_id,
        "title": title,
        "content": content,
        "content_hash": f"hash-{source_id}",
        "version": 1,
    }


def _row(
    timestamp: datetime,
    close: float,
    *,
    symbol: str | None = "TEST",
    currency: str | None = "USD",
    complete: bool | None = True,
) -> dict[str, object]:
    row: dict[str, object] = {
        "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
        "open": f"{close - 1:.4f}",
        "high": f"{close + 1:.4f}",
        "low": f"{close - 2:.4f}",
        "close": f"{close:.4f}",
        "volume": "100",
    }
    if symbol is not None:
        row["symbol"] = symbol
    if currency is not None:
        row["currency"] = currency
    if complete is not None:
        row["complete"] = complete
    return row


def test_daily_pipeline_rejects_cross_ticker_unlabelled_bars_and_unknown_currency() -> None:
    timestamp = datetime(2026, 8, 1, tzinfo=UTC)
    uso_source = _source(
        "uso-source",
        symbols=["USO"],
        rows=[_row(timestamp, 10, symbol=None)],
        # A misleading title must not be used as an instrument/timeframe label.
        title="BWET daily market bars",
    )
    unlabeled_without_currency = _source(
        "bwet-unknown-currency",
        symbols=["BWET"],
        currency=None,
        rows=[_row(timestamp, 11, symbol=None, currency=None)],
    )
    bars, refs, currency = Orchestrator._daily_bars_from_sources(
        [uso_source, unlabeled_without_currency],
        "BWET",
    )

    assert bars == []
    assert refs == ["bwet-unknown-currency"]
    assert currency is None

    bound = _source(
        "bwet-source",
        symbols=["BWET"],
        rows=[_row(timestamp, 12, symbol=None)],
    )
    bars, refs, currency = Orchestrator._daily_bars_from_sources([bound], "BWET")
    assert len(bars) == 1
    assert bars[0]["symbol"] == "BWET"
    assert bars[0]["currency"] == "USD"
    assert refs == ["bwet-source"]
    assert currency == "USD"


def test_daily_pipeline_keeps_one_currency_feed_adjustment_snapshot() -> None:
    first = _source(
        "daily-sip-raw",
        symbols=["TEST"],
        rows=[_row(datetime(2026, 8, 1, tzinfo=UTC), 10)],
    )
    different_feed = _source(
        "daily-iex-raw",
        symbols=["TEST"],
        feed="iex",
        rows=[_row(datetime(2026, 8, 2, tzinfo=UTC), 11)],
    )
    different_currency = _source(
        "daily-eur-raw",
        symbols=["TEST"],
        currency="EUR",
        rows=[_row(datetime(2026, 8, 3, tzinfo=UTC), 12, currency="EUR")],
    )
    bars, refs, currency = Orchestrator._daily_bars_from_sources(
        [first, different_feed, different_currency],
        "TEST",
    )

    assert [item["timestamp"] for item in bars] == ["2026-08-01T00:00:00Z"]
    assert refs == ["daily-sip-raw", "daily-iex-raw", "daily-eur-raw"]
    assert currency == "USD"


def test_weekly_pipeline_uses_connector_aggregation_and_excludes_partial_week() -> None:
    rows = [
        _row(datetime(2026, 8, 24, tzinfo=UTC), 10),
        _row(datetime(2026, 8, 25, tzinfo=UTC), 12),
        _row(datetime(2026, 8, 31, tzinfo=UTC), 20),
        _row(datetime(2026, 9, 1, tzinfo=UTC), 22),
        _row(datetime(2026, 9, 11, tzinfo=UTC), 99),
    ]
    result = Orchestrator._weekly_bars(
        rows,
        "TEST",
        source_metadata={"provider": "alpaca", "adjustment": "raw"},
        retrieved_at=datetime(2026, 9, 12, 12, tzinfo=UTC),
    )

    assert result.status == "ok"
    assert result.metadata["current_week_partial_excluded"] is True
    assert result.metadata["excluded_current_week_daily_bar_count"] == 1
    assert len(result.bars) == 2
    assert result.bars[0].open == "9.00000000"
    assert result.bars[0].close == "12.00000000"
    assert result.bars[1].open == "19.00000000"
    assert result.bars[1].close == "22.00000000"


def test_market_evidence_preserves_completion_and_shows_missing_and_weekly_metadata() -> None:
    class EvidenceRepository:
        def __init__(self) -> None:
            self.requests = []
            self.attached = []

        def import_evidence(self, request):
            self.requests.append(request)
            return {"source_id": f"source-{len(self.requests)}"}

        def append_run_sources(self, run_id, source_ids, *, reason):
            self.attached = list(source_ids)

    class Connector:
        def fetch_bars(self, symbol, timeframe, **kwargs):
            metadata = {
                "provider": "alpaca",
                "endpoint": "https://data.alpaca.markets/v2/stocks/bars",
                "symbols": [symbol],
                "timeframe": timeframe,
                "feed": kwargs.get("feed", "sip"),
                "adjustment": kwargs.get("adjustment", "raw"),
                "currency": "USD",
                "currency_basis": "test Alpaca stock-bars scope",
                "retrieved_at": "2026-09-12T12:00:00Z",
                "credential_status": {"inventory": {"paths": ["/private/credentials"]}},
            }
            bars = []
            if symbol == "AAA" and timeframe == "1Day":
                for index in range(70):
                    bars.append(
                        MarketBar(
                            "AAA",
                            (datetime(2026, 6, 1, tzinfo=UTC) + timedelta(days=index)).isoformat().replace("+00:00", "Z"),
                            str(index + 9),
                            str(index + 11),
                            str(index + 8),
                            str(index + 10),
                            "100",
                            complete=index < 69,
                        )
                    )
            return MarketBarsResult(
                provider="alpaca",
                status="ok" if bars else "no_data",
                capability="ready",
                bars=tuple(bars),
                metadata=metadata,
            )

    repository = EvidenceRepository()
    engine = Orchestrator(repository, None, SimpleNamespace(), market_connector=Connector())
    run = {
        "namespace": "real",
        "ticker": "AAA",
        "as_of": "2026-09-12T12:00:00Z",
        "input_snapshot_json": json.dumps({"research_candidates": [{"ticker": "BBB"}]}),
    }
    ids, packet = asyncio.run(engine._prepare_market_evidence("run-1", {"agent_id": "A04"}, run))

    assert ids == repository.attached
    assert len(packet) == 8  # two tickers, three requested frequencies, two weekly packets
    assert {item["timeframe"] for item in packet} >= {"1Min", "1Hour", "1Day", "1Week"}
    daily = next(item for item in packet if item["ticker"] == "AAA" and item["timeframe"] == "1Day")
    assert daily["raw_bar_count"] == 70
    assert daily["complete_bar_count"] == 69
    assert daily["incomplete_bar_count"] == 1
    assert "fresh_bar_count" in daily and "stale_bar_count" in daily
    assert daily["provenance"]["provider"] == "alpaca"
    assert daily["provenance"]["source_id"] in ids
    missing_weekly = next(item for item in packet if item["ticker"] == "BBB" and item["timeframe"] == "1Week")
    assert missing_weekly["status"] == "no_data"
    weekly = next(item for item in packet if item["ticker"] == "AAA" and item["timeframe"] == "1Week")
    assert weekly["metadata"]["current_week_partial_excluded"] is True
    assert weekly["provenance"]["daily_source_id"]
    assert all("credential_status" not in request.content for request in repository.requests)
    assert all("/private/credentials" not in request.content for request in repository.requests)


def test_scenario_input_hash_uses_last_bounded_window_for_long_history() -> None:
    start = datetime(2022, 1, 1, tzinfo=UTC)
    rows = [_row(start + timedelta(days=index), 50 + index * 0.01) for index in range(1300)]
    source = _source("long-history", symbols=["TEST"], rows=rows)
    daily, refs, currency = Orchestrator._daily_bars_from_sources([source], "TEST")
    assert len(daily) == 1300
    assert currency == "USD"

    result = build_price_scenarios(
        "TEST",
        daily,
        source_refs=refs,
        source_hashes={"long-history": "hash-long-history"},
        as_of="2026-09-12T12:00:00Z",
        horizon_days=63,
        path_count=1000,
        currency=currency,
    )
    assert result["status"] == "complete"
    assert result["calibration"]["bar_count"] == 1261
    assert result["calibration"]["omitted_older_bars"] == 39
    assert result["calibration"]["first_observation"] == daily[-1261]["timestamp"]
    replay = build_price_scenarios(
        "TEST",
        daily[-1261:],
        source_refs=refs,
        source_hashes={"long-history": "hash-long-history"},
        as_of="2026-09-12T12:00:00Z",
        horizon_days=63,
        path_count=1000,
        currency=currency,
    )
    # The persisted repository input must be the exact last-window math input
    # when the scenario result records 39 omitted observations.
    assert replay["input_hash"] == result["input_hash"]
