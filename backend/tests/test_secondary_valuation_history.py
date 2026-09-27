import hashlib
import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.valuation_history import create_valuation_history_router
from backend.app.research.comparable_financials import observations
from backend.app.research.secondary_valuation_history import parse_history, refresh_history, history_url
from backend.tests.test_comparable_financials import source as financial_source


def source(content=None, **changes):
    text = content or "\n".join([
        "Example Company (ABC)", "NASDAQ: ABC · Real-Time Price · USD", "Fiscal Quarter",
        "Current Q2 2026 Q1 2026 Q4 2025", "Period Ending",
        "Sep '26Sep 25, 2026 Jun '26Jun 30, 2026 Mar '26Mar 31, 2026 Dec '25Dec 31, 2025",
        "PS Ratio", "8.5 8.1 7.5 7.1", "EV/EBITDA Ratio", "17.5 16.8 - 15.7",
        "PB Ratio", "7.3 7.1 6.8 6.5", "P/TBV Ratio", "8.5 8.2 8.0 7.8",
    ])
    return {"id": "ratio_source", "content": text, "content_hash": hashlib.sha256(text.encode()).hexdigest(),
            "url": history_url("ABC"), "retrieval_at": "2026-09-26T12:00:00Z", **changes}


def test_provider_series_retains_dates_gaps_attribution_and_book_nav_distinction():
    series = parse_history(source(), ticker="ABC", as_of="2026-09-26T12:00:00.500000Z")
    assert set(series) == {"P/S", "EV/EBITDA", "P/book", "P/tangible book"}
    assert series["P/S"]["points"][-1]["multiple"] == "8.500000"
    assert series["P/S"]["points"][-1]["source_refs"] == ["ratio_source"]
    assert len(series["EV/EBITDA"]["points"]) == 3
    assert series["EV/EBITDA"]["gaps"][0]["date"] == "2026-03-31"
    assert series["P/S"]["sampling"] == "quarterly"
    assert series["P/S"]["provenance"] == "secondary_provider"
    assert "not appraised NAV" in series["P/book"]["basis"]
    assert "not point-in-time backtest" in series["P/S"]["coverage_note"]


@pytest.mark.parametrize("change", [
    {"url": history_url("OTHER")}, {"content_hash": "corrupt"}, {"retrieval_at": "2026-09-27T12:00:00Z"},
])
def test_unbound_or_future_provider_archives_are_rejected(change):
    assert parse_history(source(**change), ticker="ABC", as_of="2026-09-26") == {}


def test_shifted_columns_mismatched_ticker_future_dates_and_duplicate_dates_fail_closed():
    content = source()["content"]
    for changed in (content.replace("NASDAQ: ABC", "NASDAQ: XYZ"),
                    content.replace("Current Q2", "Unexpected Current Q2"),
                    content.replace("Jun 30, 2026", "Sep 25, 2026"),
                    content.replace("Sep 25, 2026", "Oct 25, 2026")):
        assert parse_history(source(changed), ticker="ABC", as_of="2026-09-26") == {}
    partial = parse_history(source(content.replace("8.5 8.1 7.5 7.1", "8.5 8.1 7.5")), ticker="ABC", as_of="2026-09-26")
    assert "P/S" not in partial
    assert "EV/EBITDA" in partial


def test_dei_actual_shares_are_supported_without_substituting_weighted_average():
    archive = financial_source()
    data = json.loads(archive["content"])
    actual = data["facts"]["us-gaap"].pop("CommonStockSharesOutstanding")
    data["facts"]["dei"] = {"EntityCommonStockSharesOutstanding": actual}
    data["facts"]["us-gaap"]["WeightedAverageNumberOfDilutedSharesOutstanding"] = actual
    rows = observations(json.dumps(data), archive, ticker="ABC", cik="1234", as_of="2026-03-01")
    shares = [row for row in rows if row["metric"] == "shares"]
    assert len(shares) == 1
    assert shares[0]["proof"]["taxonomy"] == "dei"
    assert shares[0]["value"] == "10.000000"


def test_refresh_checks_robots_reuses_same_day_and_keeps_prior_decisions_untouched(monkeypatch):
    import backend.app.research.secondary_valuation_history as module
    from backend.app.research.discovery import FetchedSource
    calls, archived, appended = [], [], []
    store = {"provider_multiples": {}}
    def saved(*args, **kwargs): return store.copy()
    monkeypatch.setattr(module, "saved_history", saved)
    async def fetch(url):
        calls.append(url)
        return FetchedSource(url, url, "User-agent: *\nDisallow: /private/" if url.endswith("robots.txt") else source()["content"], "ABC ratios", "2026-09-26T12:00:00Z")
    def archive(repo, page, **kwargs):
        archived.append(kwargs)
        store["provider_multiples"] = parse_history(source(), ticker="ABC", as_of="2026-09-26")
        return {"source_id": "ratio_source"}
    monkeypatch.setattr(module, "archive_public_observation", archive)
    repo = SimpleNamespace(append_run_sources=lambda *args, **kwargs: appended.append((args, kwargs)))
    acquisition = SimpleNamespace(_fetch=fetch)
    asyncio.run(refresh_history(repo, acquisition, ticker="ABC", namespace="real", as_of="2026-09-26T11:59:59Z", run_id="new_run"))
    asyncio.run(refresh_history(repo, acquisition, ticker="ABC", namespace="real", as_of="2026-09-26T12:01:00Z", run_id="new_run"))
    assert len(calls) == 2 and len(archived) == 1
    assert len(appended) == 2
    assert archived[0] == {"namespace": "real", "scope": "valuation-history:ABC:2026-09-26"}


def test_robots_denial_does_not_fetch_page_or_archive(monkeypatch):
    import backend.app.research.secondary_valuation_history as module
    monkeypatch.setattr(module, "saved_history", lambda *args, **kwargs: {"provider_multiples": {}})
    calls = []
    async def fetch(url):
        calls.append(url)
        return SimpleNamespace(error=None, content="User-agent: *\nDisallow: /")
    with pytest.raises(ValueError, match="does not allow"):
        asyncio.run(refresh_history(object(), SimpleNamespace(_fetch=fetch), ticker="ABC", namespace="real", as_of="2026-09-26"))
    assert calls == ["https://stockanalysis.com/robots.txt"]


def test_api_get_is_read_only_namespace_scoped_and_live_refresh_excludes_demo(monkeypatch):
    import backend.app.api.valuation_history as module
    calls = []
    def saved(repo, **kwargs):
        calls.append(kwargs)
        return {"provider_multiples": {}, **kwargs}
    monkeypatch.setattr(module, "saved_history", saved)
    app = FastAPI()
    app.include_router(create_valuation_history_router(object(), object(), SimpleNamespace(enable_market_connectors=True), lambda: None))
    client = TestClient(app)
    assert client.get("/api/valuation-history/ABC?namespace=demo").status_code == 200
    assert calls[-1]["namespace"] == "demo"
    assert client.post("/api/valuation-history/ABC/refresh?namespace=demo").status_code == 400
    assert client.get("/api/valuation-history/../../secret?namespace=real").status_code != 200
    assert len(calls) == 1
