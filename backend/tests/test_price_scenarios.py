from datetime import datetime, timedelta, timezone
import math

import pytest

from backend.app.research.price_scenarios import build_price_scenarios


def bars(count=90, constant=False):
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    price = 100.0
    result = []
    for i in range(count):
        if not constant:
            price *= math.exp(.0003 + .013*math.sin(i*.9))
        result.append({"t":(start+timedelta(days=i)).isoformat(), "c":price})
    return result


def simulate(observations, **overrides):
    params = {"as_of":"2026-01-01T00:00:00Z", "path_count":100, "horizon_days":21, "source_refs":["source-one"], "source_hashes":{"source-one":"retained-hash"}}
    params.update(overrides)
    return build_price_scenarios("TEST", observations, **params)


def test_constant_history_has_no_fabricated_drift_or_risk():
    result = simulate(bars(constant=True))
    assert result["status"] == "complete"
    for scenario in result["scenarios"].values():
        assert set(scenario["terminal_price_quantiles"].values()) == {100.0}
        assert scenario["loss_frequency"] == 0
        assert set(scenario["worst_drawdown_quantiles"].values()) == {0.0}
        assert scenario["mean_return_in_worst_five_percent"] == 0


def test_replay_sensitivity_bounds_and_provenance():
    observations = bars()
    result = simulate(observations, seed=1234)
    assert result == simulate(list(reversed(observations)), seed=1234)
    assert result["source_refs"] == ["source-one"]
    assert result["source_hashes"] == {"source-one":"retained-hash"}
    assert result["calibration"]["return_count"] == 89
    scenarios = result["scenarios"]
    assert scenarios["bear"]["terminal_price_quantiles"]["p50"] < scenarios["base"]["terminal_price_quantiles"]["p50"] < scenarios["bull"]["terminal_price_quantiles"]["p50"]
    for scenario in scenarios.values():
        values = list(scenario["terminal_price_quantiles"].values())
        assert values == sorted(values) and min(values) > 0
        assert 0 <= scenario["loss_frequency"] <= 1
        assert all(0 <= d <= 1 for d in scenario["worst_drawdown_quantiles"].values())
        assert scenario["fan"][0]["trading_day"] == 0
        assert scenario["fan"][-1]["trading_day"] == 21
    changed = simulate(observations, seed=4321)
    assert changed["result_hash"] != result["result_hash"]


def test_short_future_incomplete_and_conflicting_history_never_predicts():
    short = bars(60)
    result = simulate(short)
    assert result["status"] == "insufficient_evidence" and result["scenarios"] == {}
    assert "59" in result["missing_reason"]
    extra = [
        {"t":"2027-01-01T00:00:00Z", "c":150},
        {"t":"2025-12-01T00:00:00Z", "c":150, "complete":False},
        {"t":"2025-12-02T00:00:00Z", "c":float("nan")},
        short[-1],
    ]
    excluded = simulate(short+extra)
    assert excluded["status"] == "insufficient_evidence"
    assert excluded["calibration"]["excluded_bars"] == {"invalid":1,"future":1,"incomplete":1,"duplicate":1}
    conflict = simulate(bars()+[{"t":bars()[0]["t"],"c":999}])
    assert conflict["status"] == "insufficient_evidence" and "Conflicting" in conflict["missing_reason"]


@pytest.mark.parametrize("options", [{"horizon_days":253},{"horizon_days":0},{"path_count":5001},{"seed":-1},{"horizon_days":True}])
def test_requests_cannot_exceed_calculation_bounds(options):
    with pytest.raises(ValueError):
        simulate(bars(), **options)


@pytest.mark.parametrize("pair", [(1e300,1e-300),(1e-300,1e300)])
def test_extreme_finite_observations_return_a_bounded_unavailable_result(pair):
    observations = bars()
    observations[30]["c"], observations[31]["c"] = pair
    result = simulate(observations, seed=123)
    assert result["status"] == "insufficient_evidence"
    assert result["scenarios"] == {}
    assert result["missing_reason"] and result["result_hash"]
