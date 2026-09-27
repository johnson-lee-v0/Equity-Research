from copy import deepcopy
from pathlib import Path

import pytest

from backend.app.config import Settings
from backend.app.memory.repository import Repository
from backend.app.orchestration.workflow import build_research_tasks
from backend.app.research.case_store import CaseDecisionStore
from backend.app.research.learning import LifecycleRequest, candidate_key
from backend.app.research.lifecycle import review_overlay
from backend.app.schemas import RunCreate


def candidate(outcome="watchlist"):
    return {"ticker":"EXMP","direction":"long","outcome":outcome,"entry":{"lower":"90","upper":"100","currency":"USD"},"watch_triggers":[{"type":"date","condition":"Review quarterly results","trigger_date":"2026-09-15"}]}


def checks():
    return [{"status":"active","trigger":{"type":"date","trigger_date":"2026-09-15"},"checked_at":"2026-09-16T21:00:00Z","observation":{"price":"105","currency":"USD","as_of":"2026-09-16T20:00:00Z"}}]


def test_pause_keeps_last_quote_distance_and_overdue_review_visible():
    value, observations = candidate(),checks()
    before = deepcopy((value,observations))
    result = review_overlay(value,observations,paused=True,now="2026-09-17T00:00:00Z")
    assert result["paused"] and result["overdue"]
    assert result["review_due_at"] == "2026-09-15"
    assert result["current_quote"]["observation_status"] == "retained_previous"
    assert result["current_quote"]["kind"] == "close"
    assert result["distance_to_entry"]["percent"] == "5.00"
    assert result["last_checked_at"]
    assert (value,observations) == before


def test_stale_currency_mismatch_and_missing_quotes_do_not_supply_entry_distance():
    assert review_overlay(candidate(),checks(),paused=False,now="2026-09-25T00:00:00Z")["distance_to_entry"]["status"] == "unavailable"
    wrong = checks()
    wrong[0]["observation"]["currency"] = "CAD"
    assert review_overlay(candidate(),wrong,paused=False,now="2026-09-17T00:00:00Z")["distance_to_entry"]["status"] == "unavailable"
    assert review_overlay(candidate(),[],paused=False)["current_quote"]["price"] is None


def test_recommendation_does_not_become_a_holding_and_reviewed_event_is_not_overdue():
    result = review_overlay(candidate("recommend"),checks(),paused=False,now="2026-09-17T00:00:00Z")
    assert result["lifecycle_state"] == "recommended"
    reviewed = checks()
    reviewed[0]["review_state"] = "reviewed"
    assert not review_overlay(candidate(),reviewed,paused=False,now="2026-09-17T00:00:00Z")["overdue"]
    assert review_overlay(candidate(),checks(),paused=False,state="held")["lifecycle_state"] == "held"


def test_thesis_review_remains_overdue_when_an_unrelated_price_check_exists():
    value=candidate("recommend")
    value["action_plan"]={"review_at":"2026-09-15"}
    price=[{"trigger":{"type":"price","threshold":"90"},"status":"active"}]
    result=review_overlay(value,price,paused=True,now="2026-09-17T00:00:00Z")
    assert result["overdue"] and result["review_due_at"].startswith("2026-09-15")


def test_expired_setup_does_not_close_a_confirmed_holding_or_hide_review():
    value=candidate("recommend")
    value["action_plan"]={"expires_at":"2026-09-15","review_at":"2026-09-16"}
    result=review_overlay(value,checks(),paused=True,state="held",now="2026-09-17T00:00:00Z")
    assert result["setup_expired"] and result["overdue"]
    assert result["lifecycle_state"] == "held"
    assert "expired" in result["next_action"] and "existing holdings" in result["next_action"]


def test_reopenable_decline_and_explicit_holding_included_with_idempotent_state_event(tmp_path):
    config=Settings(project_root=Path(__file__).resolve().parents[2],data_dir=tmp_path,enable_market_connectors=False,enable_reddit_intake=False)
    repo=Repository(config=config)
    repo.control("firm",None,"pause")
    request=RunCreate(question="Review lifecycle fixture",namespace="real",idempotency_key="lifecycle-fixture")
    run,_=repo.create_run(request,build_research_tasks(request.question,None,None,"real",lean=True))
    store=CaseDecisionStore(repo)
    declined=candidate("decline")
    case={"run_id":run["run_id"],"decision_revision":1,"candidates":[declined]}
    store.list=lambda namespace: [case] if namespace == "real" else []
    store.current=lambda run_id,namespace: case if namespace == "real" else None
    assert store.watchlist()["items"][0]["lifecycle_state"] == "declined"
    assert store.watchlist()["items"][0]["overdue"]
    body=LifecycleRequest(candidate_key=candidate_key(declined),state="held",reason="User confirms existing holding",idempotency_key="held")
    with pytest.raises(ValueError,match="confirmation"):
        store.record_lifecycle(run["run_id"],body)
    body=body.model_copy(update={"confirmed":True})
    event=store.record_lifecycle(run["run_id"],body)
    assert store.record_lifecycle(run["run_id"],body) == event
    assert store.watchlist()["items"][0]["lifecycle_state"] == "held"
    assert declined["outcome"] == "decline"
    with pytest.raises(ValueError,match="namespace"):
        store.record_lifecycle(run["run_id"],body.model_copy(update={"namespace":"demo"}))
    with pytest.raises(ValueError,match="different"):
        store.record_lifecycle(run["run_id"],body.model_copy(update={"state":"closed"}))
