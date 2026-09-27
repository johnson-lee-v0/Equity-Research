from pathlib import Path
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from dataclasses import replace
import asyncio
import json

from backend.app.config import Settings
from backend.app.memory.repository import Repository
from backend.app.orchestration.workflow import build_research_tasks
from backend.app.research.watch_service import WatchMonitor
from backend.app.research.watchlist import watch_key
from backend.app.schemas import ImportRequest, RunCreate


def ready_case(tmp_path):
    repo = Repository(config=Settings(project_root=Path(__file__).resolve().parents[2], data_dir=tmp_path, enable_market_connectors=False, enable_reddit_intake=False))
    request = RunCreate(question="Review the synthetic example", namespace="real", idempotency_key="watch-queue-test")
    result, _ = repo.create_run(request, build_research_tasks(request.question, None, None, "real"))
    with repo.db.transaction(immediate=True) as conn:
        conn.execute("UPDATE tasks SET status='completed' WHERE run_id=?", (result["run_id"],))
        conn.execute("UPDATE runs SET status='completed' WHERE id=?", (result["run_id"],))
    return repo, result["run_id"]


def test_watch_event_queues_one_targeted_same_case_graph(tmp_path):
    repo, run_id = ready_case(tmp_path)
    monitor = WatchMonitor(repo, SimpleNamespace(schedule=lambda _: None), repo.config)
    trigger = {"type": "date", "condition": "Review after results", "trigger_date": "2026-09-01"}
    key = watch_key(run_id, "EXMP", trigger)
    item = {"run_id": run_id, "candidate": {"ticker": "EXMP"}}
    check = {"reason": "Review is due; event needs verification.", "checked_at": "2026-09-13T00:00:00Z", "status": "fired", "fired": True}
    first = monitor._queue_review(item, trigger, key, None, check)
    assert first
    assert monitor._queue_review(item, trigger, key, None, check) == first
    with repo.db.operation() as conn:
        assert conn.execute("SELECT count(*) FROM runs").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM tasks WHERE run_id=?", (run_id,)).fetchone()[0] == 4
        assert conn.execute("SELECT count(*) FROM research_repairs").fetchone()[0] == 0
        assert [r[0] for r in conn.execute("SELECT agent_id FROM tasks WHERE run_id=? ORDER BY sequence_no", (run_id,))] == ["A00", "A01", "A03", "A11"]


def test_firm_pause_prevents_watch_dispatch(tmp_path):
    repo, run_id = ready_case(tmp_path)
    repo.control("firm", None, "pause")
    monitor = WatchMonitor(repo, SimpleNamespace(schedule=lambda _: None), repo.config)
    trigger = {"type": "date", "condition": "Review", "trigger_date": "2026-09-01"}
    assert monitor._queue_review({"run_id": run_id, "candidate": {"ticker": "EXMP"}}, trigger, watch_key(run_id,"EXMP",trigger), None, {"reason":"Review", "checked_at":"2026-09-13"}) is None


def test_old_fired_conditions_do_not_starve_active_watches(tmp_path):
    repo, run_id = ready_case(tmp_path)
    monitor = WatchMonitor(repo, SimpleNamespace(schedule=lambda _: None), repo.config)
    trigger = {"type": "date", "condition": "Review after results", "trigger_date": "2026-09-01"}
    items = [{"run_id": run_id, "candidate": {"ticker": f"FIX{i}", "watch_triggers": [trigger]}} for i in range(11)]
    monitor.store.watchlist = lambda _: {"items": items}
    with repo.db.transaction(immediate=True) as conn:
        for item in items[:10]:
            ticker = item["candidate"]["ticker"]
            conn.execute("INSERT INTO watch_checks(trigger_key,run_id,ticker,trigger_json,status,result_json,source_fingerprint,checked_at) VALUES(?,?,?,?,?,?,?,?)", (watch_key(run_id,ticker,trigger),run_id,ticker,json.dumps(trigger),"fired","{}","","2026-01-01"))
    result = asyncio.run(monitor.check_once())
    assert result["checked"] == 1
    assert result["dispatched"][0]["ticker"] == "FIX10"


def test_evidence_event_survives_capacity_wait(tmp_path, monkeypatch):
    repo, run_id = ready_case(tmp_path)
    monitor = WatchMonitor(repo, SimpleNamespace(schedule=lambda _: None), repo.config)
    trigger = {"type": "evidence", "condition": "Review revised filing", "source_refs": ["fixture-source"]}
    item = {"run_id": run_id, "candidate": {"ticker": "EXMP", "watch_triggers": [trigger]}}
    key = watch_key(run_id,"EXMP",trigger)
    monitor.store.watchlist = lambda _: {"items": [item]}
    with repo.db.transaction(immediate=True) as conn:
        conn.execute("INSERT INTO watch_checks(trigger_key,run_id,ticker,trigger_json,status,result_json,source_fingerprint,checked_at) VALUES(?,?,?,?,?,?,?,?)", (key,run_id,"EXMP",json.dumps(trigger),"active","{}","before","2026-01-01"))
    monkeypatch.setattr(repo, "source_fingerprint", lambda *_: "after")
    real_queue = monitor._queue_review
    monkeypatch.setattr(monitor, "_queue_review", lambda *_: None)
    assert asyncio.run(monitor.check_once())["dispatched"] == []
    with repo.db.operation() as conn:
        row = conn.execute("SELECT * FROM watch_checks WHERE trigger_key=?", (key,)).fetchone()
    assert row["source_fingerprint"] == "before"
    assert json.loads(row["result_json"])["pending_review"]
    monkeypatch.setattr(monitor, "_queue_review", real_queue)
    assert len(asyncio.run(monitor.check_once())["dispatched"]) == 1
    assert asyncio.run(monitor.check_once())["dispatched"] == []


def test_stale_case_cannot_fire_an_old_price_or_date_condition(tmp_path):
    repo, run_id = ready_case(tmp_path)
    monitor = WatchMonitor(repo, SimpleNamespace(schedule=lambda _: None), repo.config)
    monitor.store.watchlist = lambda _: {"items": [{"run_id": run_id, "stale": True, "candidate": {"ticker": "EXMP", "watch_triggers": [{"type": "date", "trigger_date": "2026-01-01"}]}}]}
    assert asyncio.run(monitor.check_once())["checked"] == 0


def test_watch_review_refreshes_local_balances_without_redating_observations(tmp_path, monkeypatch):
    repo, run_id = ready_case(tmp_path)
    before = repo.run_record(run_id)
    current = {"accounts": [{"id": "fixture-account", "balances": [{"amount": "1000", "currency": "USD", "status": "unconfirmed", "observed_at": None}]}], "positions": [], "portfolio_policy": {"status": "approved"}}
    monkeypatch.setattr(repo, "_portfolio_snapshot_conn", lambda *_: current)
    monitor = WatchMonitor(repo, SimpleNamespace(schedule=lambda _: None), repo.config)
    trigger = {"type": "date", "condition": "Review", "trigger_date": "2026-09-01"}
    monitor._queue_review({"run_id": run_id, "candidate": {"ticker": "EXMP"}}, trigger, watch_key(run_id,"EXMP",trigger), None, {"reason":"Review", "checked_at":"2026-09-13"})
    after = repo.run_record(run_id)
    snapshot = json.loads(after["input_snapshot_json"])
    balance = snapshot["portfolio_snapshot"]["accounts"][0]["balances"][0]
    assert balance["amount"] == "1000"
    assert balance["observed_at"] is None and balance["status"] == "unconfirmed"
    assert snapshot["portfolio_snapshot_as_of"] is None
    assert snapshot["portfolio_snapshot_captured_at"]
    assert after["account_snapshot_id"] != before["account_snapshot_id"]
    assert snapshot["portfolio_snapshot"]["portfolio_policy"] == json.loads(before["input_snapshot_json"])["portfolio_snapshot"]["portfolio_policy"]


def test_price_rearms_only_after_later_false_then_later_true_observations(tmp_path):
    repo,run_id=ready_case(tmp_path)
    now=datetime.now(timezone.utc)
    quote={"price":"95","stamp":(now-timedelta(days=3)).isoformat()}
    def fetch(*args,**kwargs):
        return SimpleNamespace(capability="ready",bars=[SimpleNamespace(complete=True,symbol="EXMP",timestamp=quote["stamp"],close=Decimal(quote["price"]))],metadata={"currency":"USD"},as_import_request=lambda **kw:ImportRequest(namespace="real",kind="evidence",title="Synthetic completed price",content=json.dumps(quote),idempotency_key=json.dumps(quote)))
    config=replace(repo.config,enable_market_connectors=True)
    monitor=WatchMonitor(repo,SimpleNamespace(schedule=lambda _:None),config,connector=SimpleNamespace(fetch_bars=fetch))
    trigger={"type":"price","condition":"Review entry","operator":"at_or_below","threshold":"100","currency":"USD"}
    monitor.store.watchlist=lambda _:{"items":[{"run_id":run_id,"candidate":{"ticker":"EXMP","watch_triggers":[trigger]}}]}
    assert len(asyncio.run(monitor.check_once())["dispatched"]) == 1
    with repo.db.transaction(immediate=True) as conn:
        conn.execute("UPDATE tasks SET status='completed' WHERE run_id=?",(run_id,))
        conn.execute("UPDATE runs SET status='completed' WHERE id=?",(run_id,))
    quote["price"]="105"
    assert asyncio.run(monitor.check_once())["dispatched"] == []
    quote["price"]="95"
    assert asyncio.run(monitor.check_once())["dispatched"] == []
    quote.update(price="105",stamp=(now-timedelta(days=2)).isoformat())
    assert asyncio.run(monitor.check_once())["dispatched"] == []
    quote["price"]="95"
    assert asyncio.run(monitor.check_once())["dispatched"] == []
    quote["stamp"]=(now-timedelta(days=1)).isoformat()
    assert len(asyncio.run(monitor.check_once())["dispatched"]) == 1
    with repo.db.operation() as conn:
        assert [row[0] for row in conn.execute("SELECT episode FROM watch_review_episodes ORDER BY episode")] == [1,2]
    assert asyncio.run(monitor.check_once())["dispatched"] == []


def test_action_plan_review_is_scheduled_and_identical_completed_date_does_not_loop(tmp_path):
    repo,run_id=ready_case(tmp_path)
    monitor=WatchMonitor(repo,SimpleNamespace(schedule=lambda _:None),repo.config)
    monitor.store.watchlist=lambda _:{"items":[{"run_id":run_id,"candidate":{"ticker":"EXMP","action_plan":{"review_at":"2026-01-01"}}}]}
    assert len(asyncio.run(monitor.check_once())["dispatched"]) == 1
    with repo.db.transaction(immediate=True) as conn:
        conn.execute("UPDATE tasks SET status='completed' WHERE run_id=?",(run_id,))
        conn.execute("UPDATE runs SET status='completed' WHERE id=?",(run_id,))
    assert asyncio.run(monitor.check_once())["dispatched"] == []


def test_legacy_review_is_not_refired_when_candidate_identity_is_added(tmp_path):
    repo,run_id=ready_case(tmp_path)
    monitor=WatchMonitor(repo,SimpleNamespace(schedule=lambda _:None),repo.config)
    trigger={"type":"date","condition":"Review results","trigger_date":"2026-01-01"}
    legacy={"run_id":run_id,"candidate":{"ticker":"EXMP","watch_triggers":[trigger]}}
    first=monitor._queue_review(legacy,trigger,watch_key(run_id,"EXMP",trigger),None,{"reason":"review date","checked_at":"2026-01-02"})
    assert first
    with repo.db.transaction(immediate=True) as conn:
        conn.execute("UPDATE tasks SET status='completed' WHERE run_id=?",(run_id,))
        conn.execute("UPDATE runs SET status='completed' WHERE id=?",(run_id,))
    current={"run_id":run_id,"candidate":legacy["candidate"] | {"direction":"long","sizing":{"account_id":"fixture-account"}}}
    monitor.store.watchlist=lambda _:{"items":[current]}
    assert asyncio.run(monitor.check_once())["dispatched"] == []
    with repo.db.operation() as conn:
        assert conn.execute("SELECT count(*) FROM watch_review_episodes").fetchone()[0] == 1


def test_pause_during_first_price_fetch_stops_subsequent_requests(tmp_path):
    repo,run_id=ready_case(tmp_path)
    calls=[]
    def fetch(symbol,*args,**kwargs):
        calls.append(symbol)
        repo.control("firm",None,"pause")
        return SimpleNamespace(capability="unavailable",bars=[])
    monitor=WatchMonitor(repo,SimpleNamespace(schedule=lambda _:None),replace(repo.config,enable_market_connectors=True),connector=SimpleNamespace(fetch_bars=fetch))
    trigger={"type":"price","condition":"Review entry","operator":"at_or_below","threshold":"100","currency":"USD"}
    monitor.store.watchlist=lambda _:{"items":[{"run_id":run_id,"candidate":{"ticker":symbol,"watch_triggers":[trigger]}} for symbol in ("ONE","TWO","THREE")]}
    result=asyncio.run(monitor.check_once())
    assert calls == ["ONE"]
    assert result["paused"] and result["dispatched"] == []


def test_nonfiring_quote_is_retained_and_survives_failure_and_pause(tmp_path):
    repo,run_id=ready_case(tmp_path)
    mode={"value":"ready"}
    stamp=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat()
    def fetch(symbol,*args,**kwargs):
        if mode["value"] == "failed":
            raise TimeoutError("synthetic")
        if mode["value"] == "pause":
            repo.control("firm",None,"pause")
        return SimpleNamespace(capability="ready",bars=[SimpleNamespace(complete=True,symbol=symbol,timestamp=stamp,close=Decimal("105"))],metadata={"currency":"USD"},as_import_request=lambda **kw:ImportRequest(namespace="real",kind="evidence",title="Synthetic price",content="Synthetic retained price 105",idempotency_key="last-good-quote"))
    monitor=WatchMonitor(repo,SimpleNamespace(schedule=lambda _:None),replace(repo.config,enable_market_connectors=True),connector=SimpleNamespace(fetch_bars=fetch))
    trigger={"type":"price","condition":"Review entry","operator":"at_or_below","threshold":"100","currency":"USD"}
    candidate={"ticker":"EXMP","watch_triggers":[trigger]}
    monitor.store.watchlist=lambda _:{"items":[{"run_id":run_id,"candidate":candidate}]}
    def saved():
        with repo.db.operation() as conn:
            return json.loads(conn.execute("SELECT result_json FROM watch_checks").fetchone()[0])
    assert asyncio.run(monitor.check_once())["dispatched"] == []
    prior=saved()["observation"]
    assert prior["source_ref"] and prior["source_hash"] and prior["source_version"]
    mode["value"]="failed"
    asyncio.run(monitor.check_once())
    assert saved()["observation"] == prior and saved()["status"] == "unavailable"
    mode["value"]="pause"
    asyncio.run(monitor.check_once())
    assert saved()["observation"] == prior and saved()["status"] == "paused"
