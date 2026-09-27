from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.app.research.connectors import (
    AlpacaConnector,
    HttpResponse,
    MarketBar,
    ProviderCredentials,
    RedditConnector,
    compute_technicals,
    credential_inventory,
    derive_weekly_bars,
    migrate_credentials_to_private,
)


UTC = timezone.utc


def creds() -> ProviderCredentials:
    return ProviderCredentials(
        alpaca_api_key_id="alpaca-id-for-test",
        alpaca_api_secret_key="alpaca-secret-for-test",
        reddit_client_id="reddit-id-for-test",
        reddit_client_secret="reddit-secret-for-test",
        reddit_user_agent="road2m-test/1.0",
    )


def bar(timestamp: str, close: str, *, symbol: str = "TEST", volume: str = "100") -> dict[str, object]:
    return {"t": timestamp, "o": close, "h": str(float(close) + 1), "l": str(float(close) - 1), "c": close, "v": volume, "n": 2, "vw": close}


def test_dotenv_discovery_is_redacted_and_migration_is_private(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "exp" / "credentials.env"
    source.parent.mkdir()
    source.write_text(
        "# values are intentionally test-only\n"
        "ALPACA_API_KEY_ID=alpaca-id\n"
        "export ALPACA_API_SECRET_KEY='alpaca-secret'\n"
        "REDDIT_CLIENT_ID=reddit-id\n"
        "REDDIT_CLIENT_SECRET=reddit-secret\n"
        "REDDIT_USER_AGENT=road2m-test/1.0\n"
        "IGNORED=$(echo should-never-run)\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("ALPACA_API_KEY_ID", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET_KEY", raising=False)
    inventory = credential_inventory(project_root=tmp_path, extra_paths=[source])
    public = inventory.to_dict()
    assert public["providers"]["alpaca"]["available"] is True
    assert public["providers"]["reddit"]["available"] is True
    assert "alpaca_api_key_id" in public["resolved_fields"]
    assert "alpaca-id" not in repr(public)
    assert "$(echo should-never-run)" not in repr(public)

    destination = tmp_path / "private-data" / "config" / "providers.json"
    report = migrate_credentials_to_private(project_root=tmp_path, data_dir=tmp_path / "private-data", destination=destination, source_paths=[source])
    assert report.written is True
    assert report.key_names == (
        "alpaca_api_key_id",
        "alpaca_api_secret_key",
        "reddit_client_id",
        "reddit_client_secret",
        "reddit_user_agent",
    )
    assert destination.exists()
    assert destination.stat().st_mode & 0o077 == 0
    assert destination.read_text(encoding="utf-8").count("alpaca-id") == 1


def test_standard_praw_ini_generic_keys_are_mapped_only_for_praw_ini(tmp_path: Path) -> None:
    praw_ini = tmp_path / "praw.ini"
    praw_ini.write_text(
        "[bot1]\n"
        "client_id=reddit-id\n"
        "client_secret=reddit-secret\n"
        "user_agent=road2m-test/1.0\n",
        encoding="utf-8",
    )
    inventory = credential_inventory(project_root=tmp_path, extra_paths=[praw_ini])
    assert inventory.reddit_available is True
    assert set(("reddit_client_id", "reddit_client_secret", "reddit_user_agent")).issubset(inventory.resolved_fields)
    assert "reddit-secret" not in repr(inventory.to_dict())

    unrelated = tmp_path / "settings.ini"
    unrelated.write_text(
        "[app]\n"
        "client_id=wrong\n"
        "client_secret=wrong\n"
        "user_agent=wrong\n",
        encoding="utf-8",
    )
    unrelated_root = tmp_path / "unrelated-project"
    unrelated_root.mkdir()
    unrelated_inventory = credential_inventory(project_root=unrelated_root, extra_paths=[unrelated])
    assert unrelated_inventory.reddit_available is False


def test_alpaca_fetches_paginated_bars_retries_rate_limit_and_emits_metadata() -> None:
    responses = [
        HttpResponse(429, {"X-RateLimit-Remaining": "0", "Retry-After": "1"}, {"error": "rate"}),
        HttpResponse(200, {"X-RateLimit-Remaining": "9"}, {"bars": {"TEST": [bar("2026-09-11T14:30:00Z", "10")]}, "next_page_token": "page-2"}),
        HttpResponse(200, {"X-RateLimit-Remaining": "8"}, {"bars": {"TEST": [bar("2026-09-11T14:45:00Z", "11")]}, "next_page_token": None}),
    ]
    calls: list[tuple[str, dict[str, str], dict[str, str], float]] = []
    sleeps: list[float] = []

    def get(url, params, headers, timeout):
        calls.append((url, dict(params), dict(headers), timeout))
        return responses.pop(0)

    connector = AlpacaConnector(creds(), http_get=get, sleep=sleeps.append, now=lambda: datetime(2026, 9, 11, 15, 0, tzinfo=UTC))
    result = connector.fetch_bars("TEST", "15minute", start="2026-09-11T14:30:00Z", end="2026-09-11T15:00:00Z", limit=1, max_pages=4, retries=1)

    assert result.status == "ok"
    assert result.capability == "ready"
    assert [item.close for item in result.bars] == ["10.00000000", "11.00000000"]
    assert len(calls) == 3
    assert calls[0][1]["timeframe"] == "15Min"
    assert "page_token" not in calls[0][1]
    assert "page_token" not in calls[1][1]
    assert calls[2][1]["page_token"] == "page-2"
    assert calls[0][2]["APCA-API-KEY-ID"] == "alpaca-id-for-test"
    assert sleeps == [1.0]
    assert result.metadata["feed"] == "sip"
    assert result.metadata["adjustment"] == "raw"
    assert result.metadata["latest_bar_timestamp"] == "2026-09-11T14:45:00Z"
    assert result.metadata["coverage"]["TEST"]["count"] == 2
    assert "alpaca-secret-for-test" not in repr(result.to_dict())
    assert "credential_status" not in result.canonical_source_text()
    assert '"source_type": "market_bars"' in result.canonical_source_text()


def test_alpaca_excludes_incomplete_bars_from_technicals_but_retains_raw_rows() -> None:
    completed = [bar(f"2026-09-{day:02d}T00:00:00Z", str(day)) for day in range(1, 20)]
    completed.append(bar("2026-09-21T00:00:00Z", "1000"))
    response = HttpResponse(200, {}, {"bars": {"TEST": completed}, "next_page_token": None})
    connector = AlpacaConnector(
        creds(),
        http_get=lambda *args, **kwargs: response,
        now=lambda: datetime(2026, 9, 21, 12, 0, tzinfo=UTC),
    )

    result = connector.fetch_bars("TEST", "daily", limit=100, max_pages=1)

    assert len(result.bars) == 20
    assert result.bars[-1].complete is False
    assert result.metadata["coverage"]["TEST"]["complete_bar_count"] == 19
    assert result.metadata["coverage"]["TEST"]["incomplete_bar_count"] == 1
    assert result.metadata["excluded_incomplete_bar_count"] == 1
    assert result.metadata["technical_sample_counts"]["TEST"] == 19
    assert result.technicals["TEST"].sma20 is None


def test_alpaca_missing_requested_symbol_and_max_bars_are_partial() -> None:
    response = HttpResponse(
        200,
        {},
        {"bars": {"AAA": [bar("2026-09-20T00:00:00Z", "10", symbol="AAA")]}, "next_page_token": "next"},
    )
    connector = AlpacaConnector(
        creds(),
        http_get=lambda *args, **kwargs: response,
        now=lambda: datetime(2026, 9, 21, 12, 0, tzinfo=UTC),
    )

    result = connector.fetch_bars("AAA,ZZZ", "daily", limit=100, max_pages=2, max_bars=1)

    assert result.status == "partial"
    assert result.metadata["pagination_stopped_reason"] == "max_bars"
    assert result.metadata["missing_symbols"] == ["ZZZ"]
    assert result.metadata["coverage"]["ZZZ"]["missing"] is True
    assert result.metadata["completed_bar"]["all_symbols"] is False


def test_alpaca_missing_credentials_is_explicit_and_does_not_call_transport() -> None:
    calls = []

    def get(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("transport must not be called")

    result = AlpacaConnector(ProviderCredentials(), http_get=get).fetch_bars("AAPL", "daily")
    assert result.status == "unavailable"
    assert result.capability == "missing_credentials"
    assert result.bars == ()
    assert calls == []
    assert result.error and "credential" in result.error.lower()


def test_alpaca_asset_identity_uses_fixed_paper_endpoint_and_returns_bounded_projection() -> None:
    response = HttpResponse(
        200,
        {"X-RateLimit-Remaining": "9"},
        {
            "id": "59576c8b-25c8-4a3c-8259-4467a3c4330c",
            "symbol": "GOLD",
            "name": "Gold.com, Inc.",
            "exchange": "NYSE",
            "status": "active",
            "class": "us_equity",
            "tradable": True,
            "maintenance_margin_requirement": 30,
        },
    )
    calls: list[tuple[str, dict[str, str], dict[str, str], float]] = []

    def get(url, params, headers, timeout):
        calls.append((url, dict(params), dict(headers), timeout))
        return response

    result = AlpacaConnector(
        creds(),
        http_get=get,
        now=lambda: datetime(2026, 9, 13, 15, 0, tzinfo=UTC),
    ).fetch_asset_identity(" gold ")

    assert len(calls) == 1
    assert calls[0][0] == "https://paper-api.alpaca.markets/v2/assets/GOLD"
    assert calls[0][1] == {}
    assert calls[0][2]["APCA-API-KEY-ID"] == "alpaca-id-for-test"
    assert calls[0][2]["APCA-API-SECRET-KEY"] == "alpaca-secret-for-test"
    assert calls[0][3] == 20.0
    assert result["result_status"] == "ok"
    assert result["capability"] == "ready"
    assert result["asset_id"] == result["id"] == "59576c8b-25c8-4a3c-8259-4467a3c4330c"
    assert result["symbol"] == "GOLD"
    assert result["name"] == "Gold.com, Inc."
    assert result["exchange"] == "NYSE"
    assert result["status"] == "active"
    assert result["class"] == result["asset_class"] == "us_equity"
    assert result["retrieved_at"] == result["observed_at"] == "2026-09-13T15:00:00Z"
    assert result["provenance"]["source_type"] == "alpaca_asset_identity"
    assert "tradable" not in result
    assert "maintenance_margin_requirement" not in result
    assert "alpaca-secret-for-test" not in repr(result)


def test_alpaca_asset_identity_requires_one_safe_symbol_and_does_not_accept_url() -> None:
    calls = []

    def get(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("transport must not be called")

    connector = AlpacaConnector(creds(), http_get=get)
    with pytest.raises(ValueError, match="symbols must be short market identifiers"):
        connector.fetch_asset_identity("https://example.invalid/GOLD")
    with pytest.raises(ValueError, match="exactly one symbol"):
        connector.fetch_asset_identity("GOLD,IAU")
    assert calls == []


@pytest.mark.parametrize("status_code", [401, 403])
def test_alpaca_asset_identity_auth_errors_are_unavailable_and_redacted(status_code: int) -> None:
    secret = "server-response-secret-must-not-escape"
    calls = []

    def get(url, params, headers, timeout):
        calls.append((url, params, headers, timeout))
        return HttpResponse(status_code, {}, {"message": secret, "key": "echoed-secret"})

    result = AlpacaConnector(creds(), http_get=get).fetch_asset_identity("GOLD")

    assert result["status"] == "unavailable"
    assert result["result_status"] == "unavailable"
    assert result["capability"] == "auth_required"
    assert result["asset_id"] is None
    assert result["error"] == "Alpaca authentication was rejected."
    assert secret not in repr(result)
    assert "echoed-secret" not in repr(result)
    assert calls[0][0] == "https://paper-api.alpaca.markets/v2/assets/GOLD"


def test_technical_snapshot_keeps_short_windows_null_and_conditions_descriptive() -> None:
    bars = tuple(
        MarketBar("TEST", f"2026-09-{day:02d}T20:00:00Z", str(day), str(day + 1), str(day - 1), str(day), "100")
        for day in range(1, 16)
    )
    snapshot = compute_technicals(bars, symbol="TEST", timeframe="daily")
    assert snapshot.sample_count == 15
    assert snapshot.sma20 is None
    assert snapshot.rsi14 is not None
    assert snapshot.atr14 is not None
    assert snapshot.missing_reasons["sma20"]
    assert all("forecast" in condition.interpretation for condition in snapshot.direction_conditions)
    assert snapshot.log_returns


class FakeSubreddit:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def new(self, *, limit, params=None):
        self.calls.append((limit, params))
        index = len(self.calls) - 1
        return iter(self.pages[min(index, len(self.pages) - 1)])


class FakeReddit:
    def __init__(self, pages):
        self.read_only = False
        self.sub = FakeSubreddit(pages)

    def subreddit(self, name):
        assert name == "wallstreetbets"
        return self.sub


class FakeSubmission:
    def __init__(self, ident, created, title="Title", body="Body", score=3):
        self.name = ident
        self.subreddit = type("Sub", (), {"display_name": "wallstreetbets"})()
        self.title = title
        self.selftext = body
        self.permalink = f"/r/wallstreetbets/comments/{ident}/"
        self.created_utc = created
        self.score = score


def test_reddit_backfill_is_read_only_bounded_and_reports_uncovered_cutoff() -> None:
    now = datetime(2026, 9, 12, tzinfo=UTC)
    pages = [
        [FakeSubmission("t3_new", (now - timedelta(days=1)).timestamp())],
        [],
    ]
    fake = FakeReddit(pages)

    def factory(**kwargs):
        assert kwargs["read_only"] is True
        assert kwargs["ratelimit_seconds"] == 0
        assert kwargs["timeout"] >= 1
        return fake

    connector = RedditConnector(creds(), reddit_factory=factory, now=lambda: now)
    result = connector.backfill(days=7, max_posts=100, max_pages=2, batch_size=100)

    assert result.status == "partial"
    assert result.capability == "ready"
    assert result.posts[0].post_id == "t3_new"
    assert result.posts[0].permalink.startswith("https://www.reddit.com/")
    assert result.metadata["listing"] == "new"
    assert result.metadata["batch_size"] == 100
    assert result.metadata["coverage"]["complete"] is False
    assert result.metadata["gap_report"]
    assert fake.sub.calls == [(100, None), (100, {"after": "t3_new"})]
    assert result.posts[0].retention_caveats
    assert "credential_status" not in result.canonical_source_text()
    assert '"source_type": "reddit_submission_listing"' in result.canonical_source_text()


def test_reddit_deleted_body_and_new_posts_default_are_canonical() -> None:
    now = datetime(2026, 9, 12, tzinfo=UTC)
    fake = FakeReddit([[FakeSubmission("abc", now.timestamp(), title="[deleted]", body="[removed]", score=0)]])
    connector = RedditConnector(creds(), reddit_factory=lambda **kwargs: fake, now=lambda: now)
    result = connector.new_posts(max_posts=1)
    post = result.posts[0]
    assert post.post_id == "t3_abc"
    assert post.deleted is True
    assert post.created_utc == now.timestamp()
    assert result.metadata["coverage"]["status"] == "bounded_new_listing"
    assert "wallstreetbets" in result.as_import_payload()["title"]


def test_reddit_cutoff_boundary_is_complete_when_listing_reaches_an_older_post() -> None:
    now = datetime(2026, 9, 12, tzinfo=UTC)
    pages = [[
        FakeSubmission("recent", (now - timedelta(days=1)).timestamp()),
        FakeSubmission("cutoff-crossing", (now - timedelta(days=8)).timestamp()),
    ]]
    fake = FakeReddit(pages)
    connector = RedditConnector(creds(), reddit_factory=lambda **kwargs: fake, now=lambda: now)

    result = connector.backfill(days=7, max_posts=100, max_pages=2, batch_size=100)

    assert result.status == "ok"
    assert result.metadata["pagination_stopped_reason"] == "cutoff_reached"
    assert result.metadata["cutoff_reached"] is True
    assert result.metadata["coverage"]["complete"] is True
    assert result.metadata["gap_report"] == []


def test_reddit_max_posts_persists_last_consumed_cursor() -> None:
    now = datetime(2026, 9, 12, tzinfo=UTC)
    fake = FakeReddit(
        [[
            FakeSubmission("t3_2", (now - timedelta(hours=1)).timestamp()),
            FakeSubmission("t3_1", (now - timedelta(hours=2)).timestamp()),
            FakeSubmission("t3_0", (now - timedelta(hours=3)).timestamp()),
        ]]
    )
    connector = RedditConnector(creds(), reddit_factory=lambda **kwargs: fake, now=lambda: now)

    result = connector.new_posts(max_posts=2, max_pages=1, batch_size=100)

    assert result.metadata["pagination_stopped_reason"] == "max_posts"
    assert result.metadata["oldest_cursor"] == "t3_1"
    assert result.metadata["next_cursor"] is None
    assert result.metadata["last_consumed_cursor"] == "t3_1"
    assert result.metadata["cursor_watermarks"]["last_consumed"] == "t3_1"


def test_weekly_derivation_uses_completed_daily_bars_and_excludes_current_week() -> None:
    now = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)
    daily = []
    for day in range(1, 12):
        daily.append(
            MarketBar(
                "TEST",
                f"2026-08-{day:02d}T00:00:00Z",
                str(day),
                str(day + 2),
                str(day - 1),
                str(day + 1),
                "10",
                trade_count=2,
                vwap=str(day + 1),
                complete=True,
            )
        )
    daily.append(MarketBar("TEST", "2026-09-10T00:00:00Z", "100", "101", "99", "100", "50", complete=True))
    daily.append(MarketBar("TEST", "2026-09-11T00:00:00Z", "100", "101", "99", "100", "50", complete=False))

    result = derive_weekly_bars(daily, retrieved_at=now, adjustment="split", source_adjustment="split")

    assert result.status == "ok"
    assert result.metadata["adjustment_consistent"] is True
    assert result.metadata["excluded_incomplete_daily_bar_count"] == 1
    assert result.metadata["current_week_partial_excluded"] is True
    assert result.metadata["excluded_current_week_daily_bar_count"] == 1
    assert len(result.bars) == 3
    assert all(item.complete is True for item in result.bars)
