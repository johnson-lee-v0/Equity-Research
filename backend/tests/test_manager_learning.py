"""Independent paper-return arithmetic and immutable decision baselines."""
from copy import deepcopy
from pathlib import Path
import asyncio
from dataclasses import replace
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
import json

import pytest

from backend.app.config import Settings
from backend.app.memory.repository import Repository
from backend.app.orchestration.workflow import build_research_tasks
from backend.app.research import learning as learning_module
from backend.app.research.learning import LearningJournal, OutcomeRequest, evaluate_baseline, freeze_decision, frozen_quote, source_bars
from backend.app.research.learning_refresh import refresh_paper_outcomes
from backend.app.schemas import ImportRequest, RunCreate


def identity(symbol, observed="2026-09-15T08:00:00Z"):
    return {"asset_id":"asset-"+symbol,"exchange":"TEST","symbol":symbol,"status":"active","observed_at":observed}


def market(symbol="EXMP", prices=(100, 80, 120), currency="USD", adjustment="split", captured="2026-09-17T00:00:00Z"):
    header = {"source_type": "market_bars", "status":"ok", "metadata": {"timeframe": "daily", "currency": currency, "adjustment": adjustment, "start":"2026-09-14T00:00:00Z", "end":"2026-09-17T00:00:00Z", "retrieved_at":captured, "pagination_exhausted":True,"instrument_identity":identity(symbol,captured)}}
    rows = [{"symbol": symbol, "timestamp": f"2026-09-{day:02}T00:00:00Z", "close": str(price), "complete": True} for day, price in zip((14,15,16), prices)]
    return {"id": f"source-{symbol}", "content_hash": symbol, "version":1, "retrieved_at":captured, "content": "\n".join(json.dumps(row) for row in [header, *rows])}


def baseline(**changes):
    return {"instrument_identity":identity("EXMP"),"benchmark_identity":identity("BENCH"),"decision_as_of": "2026-09-15T12:00:00Z", "reference_quote":frozen_quote([market(prices=(100,),captured="2026-09-15T08:00:00Z")],"EXMP","2026-09-15T12:00:00Z"), "benchmark_quote":frozen_quote([market("BENCH",prices=(100,),captured="2026-09-15T08:00:00Z")],"BENCH","2026-09-15T12:00:00Z"), "decision_revision": 1, "ticker": "EXMP", "direction": "long", "kind": "hypothetical", "outcome": "watchlist", "benchmark_ticker": "BENCH", "review_at": "2026-09-16T20:00:00Z", "retrospective": False, "return_basis": "price only", **changes}


def request(**changes):
    return OutcomeRequest(idempotency_key="one", evaluation_at="2026-09-17T00:00:00Z", source_ids=["source-EXMP", "source-BENCH"], **changes)


def test_known_returns_drawdown_and_benchmark_are_computed_not_supplied():
    result = evaluate_baseline(baseline(), request(), [market(), market("BENCH", (100,105,110))], now="2026-09-17T00:00:00Z")
    assert result["instrument"]["return"] == "0.200000"
    assert result["instrument"]["drawdown"] == "-0.200000"
    assert result["benchmark"]["return"] == "0.100000"
    assert result["excess_return"] == "0.100000"
    assert result["maturity"] == "matured"
    assert result["thesis_result"] == "unknown"


def test_short_reverses_payoff_and_does_not_claim_actual_execution():
    result = evaluate_baseline(baseline(direction="short"), request(), [market()])
    assert result["instrument"]["return"] == "-0.200000"
    assert result["instrument"]["drawdown"] == "-0.333333"
    assert result["kind"] == "hypothetical"
    assert result["excess_return"] is None


def test_declined_counterfactual_is_retained_with_missed_opportunity():
    result = evaluate_baseline(baseline(kind="declined_counterfactual",outcome="decline"), request(), [market(),market("BENCH",(100,100,100))])
    assert result["missed_opportunity"] is True


def test_benchmark_currency_mismatch_and_unknown_benchmark_do_not_invent_excess():
    sources = [market(),market("BENCH",currency="CAD")]
    assert evaluate_baseline(baseline(),request(),sources)["excess_return"] is None
    assert evaluate_baseline(baseline(benchmark_ticker=None),request(),sources)["benchmark"]["status"] == "unavailable"


def test_fixed_entry_cannot_cherry_pick_later_close_or_use_unadjusted_series():
    stale = baseline(reference_quote=None)
    assert evaluate_baseline(stale,request(),[market()])["status"] == "unavailable"
    assert evaluate_baseline(baseline(),request(),[market(adjustment="raw")])["status"] == "unavailable"


def test_conflicting_prices_remain_unavailable_regardless_of_source_order():
    for sources in ([market(),market(prices=(100,81,120)),market()], [market(prices=(100,81,120)),market(),market()]):
        assert any(row.get("conflict") for row in source_bars(sources,"EXMP"))
        assert evaluate_baseline(baseline(),request(),sources)["status"] == "unavailable"


def test_source_bars_resolves_packet_identity_once_for_many_bars(monkeypatch):
    captured = "2026-09-17T00:00:00Z"
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    metadata = {
        "timeframe": "daily", "currency": "USD", "adjustment": "split",
        "start": start.isoformat(), "end": "2026-09-16T00:00:00Z",
        "retrieved_at": captured, "pagination_exhausted": True,
    }
    header = {"source_type": "market_bars", "status": "ok", "metadata": metadata}
    bars = [
        {"symbol": "EXMP", "timestamp": (start + timedelta(days=index)).isoformat(), "close": str(100 + index % 7), "complete": True}
        for index in range(260)
    ]
    bar_source = {
        "id": "bars-many", "content_hash": "bars-many-v1", "version": 1,
        "retrieved_at": captured, "content": "\n".join(json.dumps(item) for item in [header, *bars]),
    }
    identity_source = {
        "id": "identity-exmp", "content_hash": "identity-exmp-v1", "version": 1,
        "retrieved_at": captured, "content": json.dumps({"instrument_identity": identity("EXMP", captured)}),
    }
    sources = [bar_source, identity_source]
    expected = source_bars(sources, "EXMP")
    original = learning_module.retained_identity
    calls = []

    def counted(identity_sources, ticker, as_of):
        calls.append((tuple(source.get("id") for source in identity_sources), ticker, as_of))
        return original(identity_sources, ticker, as_of)

    monkeypatch.setattr(learning_module, "retained_identity", counted)
    actual = source_bars(sources, "EXMP")

    assert actual == expected
    assert len(calls) == 1
    assert all(row["instrument_identity"]["asset_id"] == "asset-EXMP" for row in actual)


def test_source_bars_explicit_invalid_identity_does_not_fallback_to_packet_identity():
    captured = "2026-09-17T00:00:00Z"
    header = {
        "source_type": "market_bars", "status": "ok", "metadata": {
            "timeframe": "daily", "currency": "USD", "adjustment": "split",
            "start": "2026-09-14T00:00:00Z", "end": "2026-09-16T00:00:00Z",
            "retrieved_at": captured, "pagination_exhausted": True,
            "instrument_identity": identity("OTHER", captured),
        },
    }
    bar_source = {
        "id": "bars-explicit-invalid", "content_hash": "bars-explicit-invalid-v1", "version": 1,
        "retrieved_at": captured, "content": "\n".join([
            json.dumps(header),
            json.dumps({"symbol": "EXMP", "timestamp": "2026-09-16T00:00:00Z", "close": "100", "complete": True}),
        ]),
    }
    identity_source = {
        "id": "identity-exmp-fallback", "content_hash": "identity-exmp-fallback-v1", "version": 1,
        "retrieved_at": captured, "content": json.dumps({"instrument_identity": identity("EXMP", captured)}),
    }

    rows = source_bars([bar_source, identity_source], "EXMP")

    assert len(rows) == 1
    assert rows[0]["instrument_identity"] is None


def test_future_evaluation_or_unexplained_thesis_assessment_is_rejected():
    with pytest.raises(ValueError, match="present"):
        evaluate_baseline(baseline(),request(),[market()],now="2026-09-15T00:00:00Z")
    with pytest.raises(ValueError, match="review note"):
        evaluate_baseline(baseline(),request(thesis_result="supported"),[market()])


def test_omitted_anchor_and_changed_split_vintage_cannot_manufacture_a_return():
    source=market(prices=(100,50,100))
    full=evaluate_baseline(baseline(),request(),[source])
    assert full["instrument"]["return"] == "0.000000"
    lines=source["content"].splitlines()
    source["content"]="\n".join([lines[0],*lines[2:]])
    assert evaluate_baseline(baseline(),request(),[source])["status"] == "unavailable"
    assert evaluate_baseline(baseline(),request(),[market(prices=(50,55,60))])["status"] == "unavailable"


def test_only_predecision_available_archives_can_supply_a_frozen_quote():
    assert frozen_quote([market()],"EXMP","2026-09-15T12:00:00Z") is None
    quote=frozen_quote([market(prices=(100,),captured="2026-09-15T08:00:00Z")],"EXMP","2026-09-15T12:00:00Z")
    assert quote and quote["as_of"].startswith("2026-09-14")
    # A daily bar's start at midnight does not expose its close at noon.
    result=evaluate_baseline(baseline(),request().model_copy(update={"evaluation_at":"2026-09-16T12:00:00Z"}),[market()])
    assert result["instrument"]["return"] == "-0.200000"


def test_journal_freezes_revisions_and_idempotency_without_mutating_the_call(tmp_path, monkeypatch):
    # This fixture tests revision ordering, not the passage of wall-clock time.
    # The retained identity has a seven-day freshness window at import time.
    monkeypatch.setattr("backend.app.memory.repository.utc_now", lambda: "2026-09-17T00:00:00Z")
    monkeypatch.setattr(learning_module, "utc_now", lambda: "2026-09-17T00:00:00Z")
    repo = Repository(config=Settings(project_root=Path(__file__).resolve().parents[2],data_dir=tmp_path,enable_market_connectors=False,enable_reddit_intake=False))
    body = RunCreate(question="Review a synthetic company",namespace="real",idempotency_key="paper-case")
    run,_ = repo.create_run(body,build_research_tasks(body.question,None,None,"real",lean=True))
    candidate = {"ticker":"EXMP","direction":"long","outcome":"decline","rationale":"Synthetic declining thesis","action_plan":{"benchmark_ticker":"BENCH","benchmark_rationale":"Broad alternative","review_at":"2026-09-16T20:00:00Z"}}
    case = {"run_id":run["run_id"],"decision_revision":1,"as_of":"2026-09-15T12:00:00Z","candidates":[candidate]}
    original = deepcopy(case)
    with repo.db.transaction(immediate=True) as conn:
        freeze_decision(conn,case,"real",now="2026-09-15T12:00:01Z",sources=[market(prices=(100,),captured="2026-09-15T08:00:00Z"),market("BENCH",prices=(100,),captured="2026-09-15T08:00:00Z")])
        freeze_decision(conn,case,"real",now="2026-09-17T00:00:00Z")
    journal = LearningJournal(repo)
    frozen = journal.list("real")["items"]
    assert len(frozen) == 1 and frozen[0]["retrospective"] is False
    assert case == original
    source_ids = [repo.import_evidence(ImportRequest(namespace="real",kind="evidence",idempotency_key=f"bars-{symbol}",title=f"Synthetic {symbol}",content=market(symbol)["content"]))["source_id"] for symbol in ("EXMP","BENCH")]
    body = request().model_copy(update={"source_ids":source_ids})
    first = journal.record(frozen[0]["id"],body)
    assert journal.record(frozen[0]["id"],body) == first
    with pytest.raises(ValueError,match="different"):
        journal.record(frozen[0]["id"],body.model_copy(update={"review_note":"changed"}))
    with pytest.raises(ValueError,match="namespace"):
        journal.record(frozen[0]["id"],body.model_copy(update={"namespace":"demo"}))
    saved = journal.list("real")
    assert saved["summary"]["declined_baselines"] == 1
    assert saved["items"][0]["candidate"] == candidate
    assert len(saved["items"][0]["observations"]) == 1
    # A late-arriving earlier evaluation cannot replace the matured result.
    journal.record(frozen[0]["id"],body.model_copy(update={"idempotency_key":"earlier","evaluation_at":"2026-09-16T00:00:00Z"}))
    assert journal.list("real")["summary"]["matured_comparable"] == 1
    revised=deepcopy(case)
    revised.update({"decision_revision":2,"correction_key":"test-code-correction"})
    with repo.db.transaction(immediate=True) as conn:
        freeze_decision(conn,revised,"real",now="2026-09-15T12:00:02Z")
    records=journal.list("real")
    assert records["summary"]["evaluated"] == 1
    assert records["items"][0]["retrospective"] is True


def test_paper_refresh_obeys_pause_and_preserves_missing_frozen_anchor(tmp_path):
    config=Settings(project_root=Path(__file__).resolve().parents[2],data_dir=tmp_path,enable_market_connectors=False,enable_reddit_intake=False)
    repo=Repository(config=config)
    body=RunCreate(question="Synthetic missing quote baseline",namespace="real",idempotency_key="paper-refresh")
    run,_=repo.create_run(body,build_research_tasks(body.question,None,None,"real",lean=True))
    with repo.db.transaction(immediate=True) as conn:
        freeze_decision(conn,{"run_id":run["run_id"],"decision_revision":1,"as_of":"2026-01-01T00:00:00Z","candidates":[{"ticker":"EXMP","outcome":"decline"}]},"real")
    calls=[]
    connector=SimpleNamespace(fetch_bars=lambda *a,**k:calls.append(a))
    enabled=replace(config,enable_market_connectors=True)
    repo.control("firm",None,"pause")
    assert asyncio.run(refresh_paper_outcomes(repo,enabled,"real",connector=connector))["checked"] == 0

    assert LearningJournal(repo).list("real")["summary"]["evaluated"] == 0
    repo.control("firm",None,"resume")
    assert asyncio.run(refresh_paper_outcomes(repo,enabled,"real",connector=connector))["checked"] == 1
    saved=LearningJournal(repo).list("real")
    assert saved["items"][0]["observations"][0]["status"] == "unavailable"
    assert saved["items"][0]["reference_quote"] is None
    assert calls == []
    assert asyncio.run(refresh_paper_outcomes(repo,enabled,"real",connector=connector))["checked"] == 0


def test_paper_refresh_collects_full_window_once_per_day_without_model_work(tmp_path):
    clock=datetime.now(timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0)
    decision=clock-timedelta(days=3)+timedelta(hours=12)
    def source(symbol,prices,captured,end):
        start=clock-timedelta(days=4)
        header={"source_type":"market_bars","status":"ok","metadata":{"timeframe":"daily","currency":"USD","adjustment":"split","start":start.isoformat(),"end":end.isoformat(),"retrieved_at":captured.isoformat(),"pagination_exhausted":True,"instrument_identity":identity(symbol,captured.isoformat())}}
        rows=[{"symbol":symbol,"timestamp":(start+timedelta(days=i)).isoformat(),"close":str(price),"complete":True} for i,price in enumerate(prices)]
        return {"id":"initial-"+symbol,"content_hash":"synthetic-"+symbol,"version":1,"retrieved_at":captured.isoformat(),"content":"\n".join(json.dumps(row) for row in [header,*rows])}
    config=Settings(project_root=Path(__file__).resolve().parents[2],data_dir=tmp_path,enable_market_connectors=False,enable_reddit_intake=False)
    repo=Repository(config=config)
    body=RunCreate(question="Synthetic prospective paper review",namespace="real",idempotency_key="paper-full-refresh")
    run,_=repo.create_run(body,build_research_tasks(body.question,None,None,"real",lean=True))
    candidate={"ticker":"EXMP","direction":"long","outcome":"watchlist","action_plan":{"benchmark_ticker":"BENCH","review_at":(clock-timedelta(days=1)).isoformat()}}
    with repo.db.transaction(immediate=True) as conn:
        freeze_decision(conn,{"run_id":run["run_id"],"decision_revision":1,"as_of":decision.isoformat(),"candidates":[candidate]},"real",now=(decision+timedelta(seconds=1)).isoformat(),sources=[source(s,[100],decision-timedelta(hours=1),decision) for s in ("EXMP","BENCH")])
    calls=[]
    def fetch(symbol,*args,**kwargs):
        calls.append(symbol)
        content=source(symbol,[100,90,110,120] if symbol == "EXMP" else [100,101,105,110],datetime.now(timezone.utc),kwargs["end"])["content"]
        return SimpleNamespace(capability="ready",bars=[1],metadata={},as_import_request=lambda **kw:ImportRequest(namespace=kw["namespace"],kind="evidence",title=kw["title"],content=content,idempotency_key="refresh-source-"+symbol))
    enabled=replace(config,enable_market_connectors=True)
    connector=SimpleNamespace(fetch_bars=fetch,fetch_asset_identity=lambda symbol,**kw:identity(symbol,datetime.now(timezone.utc).isoformat()))
    assert asyncio.run(refresh_paper_outcomes(repo,enabled,"real",connector=connector))["checked"] == 1
    saved=LearningJournal(repo).list("real")
    outcome=saved["items"][0]["observations"][0]
    assert outcome["instrument"]["return"] == "0.200000"
    assert outcome["excess_return"] == "0.100000"
    assert outcome["thesis_result"] == "unknown"
    assert sorted(calls) == ["BENCH","EXMP"]
    assert asyncio.run(refresh_paper_outcomes(repo,enabled,"real",connector=connector))["checked"] == 0


def test_paper_return_rejects_changed_or_missing_asset_identity():
    altered=market()
    rows=altered["content"].splitlines()
    header=json.loads(rows[0])
    header["metadata"]["instrument_identity"]["asset_id"]="reassigned-symbol"
    altered["content"]="\n".join([json.dumps(header),*rows[1:]])
    assert evaluate_baseline(baseline(),request(),[altered])["status"] == "unavailable"
    assert evaluate_baseline(baseline(instrument_identity=None),request(),[market()])["status"] == "unavailable"
    assert evaluate_baseline(baseline(benchmark_identity=None),request(),[market(),market("BENCH")])["benchmark"]["status"] == "unavailable"
