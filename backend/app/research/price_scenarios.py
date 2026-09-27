"""Deterministic price scenarios from observed daily bars; no provider calls.

This is deliberately separate from the participant/paper-trading simulator.
Historical daily log returns are sampled with replacement. Scenario shifts
are explicit sensitivity assumptions, not estimates of catalyst probability.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import random
import statistics
from typing import Any

CODE_VERSION = "price-bootstrap.v1"
MIN_RETURNS = 60
MAX_BARS = 1261
TRADING_DAYS = 252
DEFAULT_DISCONTINUITY_RATIO = 5.0


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _time(value: Any) -> datetime:
    if isinstance(value, datetime):
        stamp = value
    elif isinstance(value, str):
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError("A bar timestamp must be an ISO timestamp.")
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def _number(value: float, places: int = 8) -> float:
    if not math.isfinite(value):
        raise ValueError("Scenario arithmetic exceeded its finite numeric bounds.")
    return round(value, places)


def _quantile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    low = math.floor(index)
    high = math.ceil(index)
    return _number(ordered[low] + (ordered[high] - ordered[low]) * (index - low))


def _quantiles(values: list[float]) -> dict[str, float]:
    return {label: _quantile(values, fraction) for label, fraction in (
        ("p05", .05), ("p10", .10), ("p25", .25), ("p50", .50),
        ("p75", .75), ("p90", .90), ("p95", .95),
    )}


def build_price_scenarios(
    ticker: str,
    daily_bars: list[dict[str, Any]],
    *,
    source_refs: list[str] | None = None,
    source_hashes: dict[str, str] | None = None,
    as_of: str,
    horizon_days: int = 63,
    path_count: int = 1000,
    seed: int | None = None,
    currency: str = "USD",
    discontinuity_ratio: float = DEFAULT_DISCONTINUITY_RATIO,
) -> dict[str, Any]:
    """Return bounded aggregates with stable inputs, seed, and result hash.

    Bars accept Alpaca ``t/c`` or normalized ``timestamp/close`` keys.
    The caller supplies completed, consistently adjusted daily observations.
    Known incomplete, invalid, future, or duplicate bars never add returns.
    """
    if isinstance(horizon_days, bool) or not isinstance(horizon_days, int) or not 1 <= horizon_days <= 252:
        raise ValueError("horizon_days must be an integer from 1 to 252.")
    if isinstance(path_count, bool) or not isinstance(path_count, int) or not 100 <= path_count <= 5000:
        raise ValueError("path_count must be an integer from 100 to 5000.")
    if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**64):
        raise ValueError("seed must be an unsigned 64-bit integer.")
    if isinstance(discontinuity_ratio, bool) or not isinstance(discontinuity_ratio, (int, float)) or not math.isfinite(float(discontinuity_ratio)) or float(discontinuity_ratio) <= 1:
        raise ValueError("discontinuity_ratio must be a finite number greater than 1.")
    symbol = str(ticker).strip().upper()
    if not symbol or len(symbol) > 20:
        raise ValueError("A bounded instrument symbol is required.")
    cutoff = _time(as_of)
    observations: dict[str, float] = {}
    rejected = {"invalid": 0, "future": 0, "incomplete": 0, "duplicate": 0}
    conflict = False
    for bar in daily_bars:
        try:
            stamp = _time(bar.get("timestamp", bar.get("t", bar.get("date"))))
            price = float(bar.get("close", bar.get("c")))
            if not math.isfinite(price) or price <= 0:
                raise ValueError("Nonpositive or nonfinite close.")
        except (ValueError, TypeError, OverflowError, AttributeError):
            rejected["invalid"] += 1
            continue
        if stamp > cutoff:
            rejected["future"] += 1
            continue
        if bar.get("complete", bar.get("is_complete", bar.get("completed", True))) is False:
            rejected["incomplete"] += 1
            continue
        key = stamp.isoformat().replace("+00:00", "Z")
        if key in observations:
            rejected["duplicate"] += 1
            conflict = conflict or observations[key] != price
            continue
        observations[key] = price
    all_rows = sorted(observations.items())
    rows = all_rows[-MAX_BARS:]
    discontinuities: list[dict[str, Any]] = []
    for (previous_stamp, previous_price), (stamp, price) in zip(rows, rows[1:]):
        try:
            ratio = price / previous_price
            if ratio >= float(discontinuity_ratio) or ratio <= 1 / float(discontinuity_ratio):
                discontinuities.append({
                    "previous_timestamp": previous_stamp,
                    "timestamp": stamp,
                    "previous_close": _number(previous_price),
                    "close": _number(price),
                    "simple_return": _number(ratio - 1, 12),
                    "ratio": _number(ratio, 12),
                    "reason": f"Consecutive closes differ by at least {discontinuity_ratio:g}x; verify split/corporate-action adjustment.",
                })
        except (OverflowError, ZeroDivisionError, ValueError):
            discontinuities.append({"previous_timestamp": previous_stamp, "timestamp": stamp, "reason": "Consecutive closes cannot be compared within finite numeric bounds."})
    quality_issues: list[str] = []
    if rejected["invalid"]:
        quality_issues.append(f"{rejected['invalid']} invalid bars were excluded.")
    if rejected["incomplete"]:
        quality_issues.append(f"{rejected['incomplete']} incomplete bars were excluded.")
    if rejected["future"]:
        quality_issues.append(f"{rejected['future']} future bars were excluded.")
    if rejected["duplicate"]:
        quality_issues.append(f"{rejected['duplicate']} duplicate bars were excluded.")
    if conflict:
        quality_issues.append("Conflicting closes share a timestamp.")
    if discontinuities:
        quality_issues.append("Unresolved price discontinuity detected; forecast acceptance is blocked until the adjustment policy is verified.")
    data_quality_status = "invalid" if conflict or discontinuities else "degraded" if quality_issues else "valid"
    acceptance_reasons = list(quality_issues)
    refs = sorted(set(str(ref) for ref in (source_refs or [])))
    hashes = dict(sorted((str(key), str(value)) for key, value in (source_hashes or {}).items()))
    inputs = {
        "ticker": symbol, "currency": currency, "as_of": as_of,
        "bars": [[stamp, close] for stamp, close in rows],
        "source_refs": refs, "source_hashes": hashes,
        "horizon_days": horizon_days, "path_count": path_count,
        "code_version": CODE_VERSION,
    }
    input_hash = _hash(inputs)
    effective_seed = seed if seed is not None else int(input_hash[:16], 16)
    result: dict[str, Any] = {
        "code_version": CODE_VERSION,
        "method": "empirical_daily_log_return_bootstrap",
        "status": "insufficient_evidence",
        "ticker": symbol, "currency": currency, "as_of": as_of,
        "source_refs": refs, "source_hashes": hashes,
        "input_hash": input_hash,
        "parameters": {"horizon_days": horizon_days, "path_count": path_count, "seed": effective_seed, "trading_days_per_year": TRADING_DAYS},
        "calibration": {
            "bar_count": len(rows), "return_count": max(0, len(rows)-1),
            "first_observation": rows[0][0] if rows else None,
            "last_observation": rows[-1][0] if rows else None,
            "omitted_older_bars": len(all_rows)-len(rows), "excluded_bars": rejected,
            "discontinuities": discontinuities,
        },
        "scenarios": {},
        "assumptions": [
            "The supplied observations are completed daily bars with one consistent corporate-action adjustment policy.",
            "Each sampled daily log return is independent and drawn with replacement from the retained historical window.",
            "The base case reuses the historical empirical return distribution; it does not estimate a new fundamental valuation.",
            "Bear and bull cases shift annualized log return by minus or plus half the observed annualized volatility, respectively, as sensitivity assumptions.",
            f"The scenario horizon is {horizon_days} trading days; it is not a predicted catalyst date.",
        ],
        "limitations": [
            "Scenario frequencies are conditional model outcomes, not calibrated probabilities of future market events.",
            "Independent resampling omits volatility clustering, serial correlation, new regimes, and unprecedented jumps.",
            "The calculation excludes fees, spreads, market impact, taxes, dividends not reflected by the supplied adjustment policy, and portfolio correlations.",
            "Price quantiles alone do not establish fair value, a price target, an entry recommendation, or suitable position sizing.",
        ],
        "missing_reason": None,
        # ``status`` remains the legacy calculation status.  These explicit
        # fields prevent a computable but untrusted forecast from becoming a
        # decision target (for example an unexplained ASST +2312% jump).
        "calculation_status": "insufficient_evidence",
        "data_quality_status": data_quality_status,
        "model_acceptance_status": "rejected" if conflict or discontinuities else "unavailable",
        "data_quality": {"status": data_quality_status, "issues": acceptance_reasons, "discontinuities": discontinuities},
        "model_acceptance": {"status": "rejected" if conflict or discontinuities else "unavailable", "accepted": False, "reasons": acceptance_reasons},
        "forecast_accepted": False,
        "forecast_status": "blocked" if conflict or discontinuities else "unavailable",
        "acceptance_reasons": acceptance_reasons,
    }
    if conflict:
        result["missing_reason"] = "Conflicting closes share the same timestamp; resolve the market-data snapshot before simulation."
    elif len(rows) < MIN_RETURNS + 1:
        result["missing_reason"] = f"At least {MIN_RETURNS} valid daily returns are required; {max(0,len(rows)-1)} are available."
    if result["missing_reason"]:
        result["calculation_status"] = "insufficient_evidence"
        result["model_acceptance_status"] = "rejected" if conflict or discontinuities else "unavailable"
        result["model_acceptance"]["status"] = result["model_acceptance_status"]
        result["model_acceptance"]["reasons"] = list(dict.fromkeys([*result["model_acceptance"]["reasons"], result["missing_reason"]]))
        result["data_quality"]["issues"] = result["model_acceptance"]["reasons"]
        result["acceptance_reasons"] = result["model_acceptance"]["reasons"]
        result["result_hash"] = _hash(result)
        return result

    try:
        # Subtract logs instead of dividing first: two finite positive prices
        # can still overflow or underflow when their ratio is constructed.
        returns = [math.log(rows[i][1]) - math.log(rows[i-1][1]) for i in range(1, len(rows))]
        sigma = statistics.stdev(returns) * math.sqrt(TRADING_DAYS)
        spot = rows[-1][1]
        if _number(spot) <= 0:
            raise ValueError("The initial price is below the retained numeric precision.")
        result["calibration"].update({
            "initial_price": _number(spot),
            "mean_daily_log_return": _number(statistics.mean(returns), 12),
            "annualized_realized_volatility": _number(sigma, 12),
            "max_observation_gap_calendar_days": max((_time(rows[i][0]) - _time(rows[i-1][0])).days for i in range(1,len(rows))),
        })
    except (OverflowError, ValueError):
        result["missing_reason"] = "The price snapshot cannot be calibrated within finite numeric precision; review its prices and adjustment policy."
        result["calculation_status"] = "insufficient_evidence"
        result["model_acceptance_status"] = "unavailable"
        result["model_acceptance"]["status"] = "unavailable"
        result["model_acceptance"]["accepted"] = False
        result["model_acceptance"]["reasons"] = list(dict.fromkeys([*result["model_acceptance"]["reasons"], result["missing_reason"]]))
        result["acceptance_reasons"] = result["model_acceptance"]["reasons"]
        result["result_hash"] = _hash(result)
        return result
    checkpoints = sorted({0, horizon_days, *[round(horizon_days*n/12) for n in range(1,12)]})
    for name, shift in (("bear", -sigma/2), ("base", 0.0), ("bull", sigma/2)):
        # Common draws isolate the effect of each scenario's drift assumption.
        rng = random.Random(effective_seed)
        fan: dict[int, list[float]] = {step: [] for step in checkpoints}
        terminal, drawdowns = [], []
        try:
            for _ in range(path_count):
                price, peak, worst_drawdown = spot, spot, 0.0
                fan[0].append(price)
                for step in range(1, horizon_days+1):
                    price *= math.exp(returns[rng.randrange(len(returns))] + shift/TRADING_DAYS)
                    if not math.isfinite(price) or price <= 0:
                        raise ValueError("Scenario arithmetic exceeded finite bounds.")
                    peak = max(peak, price)
                    worst_drawdown = max(worst_drawdown, 1-price/peak)
                    if step in fan:
                        fan[step].append(price)
                terminal.append(price)
                drawdowns.append(worst_drawdown)
            lowest_count = max(1, math.ceil(path_count*.05))
            result["scenarios"][name] = {
                "annual_log_return_shift": _number(shift, 12),
                "terminal_price_quantiles": _quantiles(terminal),
                "loss_frequency": _number(sum(price < spot for price in terminal)/path_count),
                "terminal_return_quantiles": _quantiles([price/spot-1 for price in terminal]),
                "worst_drawdown_quantiles": _quantiles(drawdowns),
                "mean_return_in_worst_five_percent": _number(statistics.mean(price/spot-1 for price in sorted(terminal)[:lowest_count])),
                "fan": [{"trading_day":step, **_quantiles(fan[step])} for step in checkpoints],
            }
        except (OverflowError, ValueError):
            result["scenarios"] = {}
            result["missing_reason"] = "Extreme input returns caused nonfinite scenario arithmetic; review the price/adjustment data."
            result["calculation_status"] = "insufficient_evidence"
            result["model_acceptance_status"] = "unavailable"
            result["model_acceptance"]["status"] = "unavailable"
            result["model_acceptance"]["reasons"] = list(dict.fromkeys([*result["model_acceptance"]["reasons"], result["missing_reason"]]))
            result["acceptance_reasons"] = result["model_acceptance"]["reasons"]
            result["result_hash"] = _hash(result)
            return result
    result["status"] = "complete"
    result["calculation_status"] = "complete"
    if conflict or discontinuities:
        result["model_acceptance_status"] = "rejected"
        result["model_acceptance"]["status"] = "rejected"
        result["model_acceptance"]["accepted"] = False
        result["forecast_accepted"] = False
        result["forecast_status"] = "blocked"
    else:
        result["model_acceptance_status"] = "accepted"
        result["model_acceptance"]["status"] = "accepted"
        result["model_acceptance"]["accepted"] = True
        result["forecast_accepted"] = True
        result["forecast_status"] = "accepted"
    result["result_hash"] = _hash(result)
    return result
