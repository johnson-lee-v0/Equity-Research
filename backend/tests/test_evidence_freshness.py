from backend.app.research.freshness import evaluate_fact_freshness, evaluate_price_freshness


def test_old_data_does_not_become_fresh_by_recent_retrieval_or_provider_status():
    source={"source_type":"market_data","retrieved_at":"2026-09-17T00:00:00Z","freshness":"fresh"}
    result=evaluate_fact_freshness({"metric":"close","period":"2026-08-01","freshness":"fresh"},source,as_of="2026-09-17T01:00:00Z")
    assert result["status"] == "stale"
    assert result["policy_version"] and result["max_age_days"] == 4


def test_reporting_periods_require_exact_end_and_future_facts_cannot_pass():
    source={"source_type":"filing"}
    fact={"metric":"diluted EPS","period":"FY2025"}
    assert evaluate_fact_freshness(fact,source,as_of="2026-09-17")["status"] == "unknown"
    fact["period_end"]="2025-12-31"
    assert evaluate_fact_freshness(fact,source,as_of="2026-09-17")["status"] == "fresh"
    fact["period_end"]="2026-12-31"
    assert evaluate_fact_freshness(fact,source,as_of="2026-09-17")["status"] == "future"
    assert evaluate_price_freshness("2026-09-16",as_of="2026-09-17",source={"retrieved_at":"2026-09-18"})["status"] == "future"


def test_catalyst_schedule_is_not_occurrence_and_supersession_overrides_age():
    fact={"metric":"Scheduled earnings date","period":"2026-10-01"}
    assert evaluate_fact_freshness(fact,as_of="2026-09-17")["status"] == "fresh"
    assert evaluate_fact_freshness(fact,as_of="2026-10-02")["status"] == "stale"
    assert evaluate_price_freshness("2026-09-16",as_of="2026-09-17",source={"is_superseded":True})["status"] == "superseded"
