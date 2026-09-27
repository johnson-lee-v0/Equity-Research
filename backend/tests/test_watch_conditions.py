from datetime import datetime, timezone

from backend.app.research.watchlist import evaluate_watch_trigger, watch_key

NOW = datetime(2026, 9, 13, tzinfo=timezone.utc)


def test_current_price_can_fire_but_stale_or_other_currency_cannot():
    trigger = {"type": "price", "operator": "at_or_below", "threshold": "100", "currency": "USD"}
    observed = {"price": "99", "as_of": "2026-09-11T20:00:00Z", "currency": "USD"}
    assert evaluate_watch_trigger(trigger, now=NOW, observation=observed)["fired"]
    assert not evaluate_watch_trigger(trigger, now=NOW, observation=observed | {"currency": "CAD"})["fired"]
    assert not evaluate_watch_trigger(trigger, now=NOW, observation=observed | {"as_of": "2026-08-31T20:00:00Z"})["fired"]


def test_review_due_does_not_claim_catalyst_happened():
    result = evaluate_watch_trigger({"type": "catalyst", "condition": "Funding closes", "review_at": "2026-09-12"}, now=NOW)
    assert result["fired"]
    assert "still needs verification" in result["reason"]
    assert not evaluate_watch_trigger({"type": "catalyst", "condition": "Funding closes"}, now=NOW)["fired"]


def test_same_condition_across_decision_revisions_has_same_key():
    trigger = {"type": "price", "condition": "Review at 100", "operator": "at_or_below", "threshold": "100", "currency": "USD"}
    assert watch_key("case", "AAA", trigger) == watch_key("case", "AAA", trigger | {"decision_revision": 2, "source_refs": ["new-source"], "status": "fired"})
    assert watch_key("case", "AAA", trigger) != watch_key("case", "AAA", trigger | {"threshold": "90"})


def test_pause_and_already_fired_prevent_repeated_dispatch():
    trigger = {"type": "date", "trigger_date": "2026-09-01"}
    assert not evaluate_watch_trigger(trigger, now=NOW, paused=True)["fired"]
    assert not evaluate_watch_trigger(trigger | {"status": "fired"}, now=NOW)["fired"]


def test_crossing_saves_first_observation_and_fires_on_next_sample():
    trigger = {"type": "price", "operator": "crosses", "threshold": "100", "currency": "USD"}
    first = {"price": "99", "currency": "USD", "as_of": "2026-09-11T20:00:00Z"}
    initial = evaluate_watch_trigger(trigger, now=NOW, observation=first)
    assert initial["observation"] == first
    assert not initial["fired"]
    following = evaluate_watch_trigger(trigger, now=NOW, previous_observation=initial["observation"], observation=first | {"price": "101", "as_of": "2026-09-12T20:00:00Z"})
    assert following["fired"]
