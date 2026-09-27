"""Portfolio exposure and sizing context for canonical v2 decisions.

This service consumes a frozen snapshot.  It treats an absent price,
currency, issuer or classification as unknown and never turns it into zero.
All arithmetic uses Decimal and returns serialisable strings.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import calendar
import re
from typing import Any, Mapping, Sequence

from .calculations import D, _convert_amount, _currency, quantize


PORTFOLIO_CODE_VERSION = "portfolio-risk.v2"
OPPORTUNITY_COST_CODE_VERSION = "opportunity-cost.v1"


def _dict(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump(mode="python", exclude_none=False)
        except TypeError:
            return value.model_dump()
    return {}


def _refs(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, Sequence):
        return []
    return list(dict.fromkeys(str(item).strip() for item in value if str(item or "").strip()))


def _status(value: Any) -> str:
    return str(value or "").strip().lower()


def _opportunity_date(value: Any) -> date | None:
    """Parse one explicit calendar date without consulting the system clock."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except (TypeError, ValueError):
        try:
            return date.fromisoformat(text[:10])
        except (TypeError, ValueError):
            return None


def _opportunity_rate(value: Any) -> Decimal | None:
    """Read a rate as a fraction; percent syntax is explicit."""
    text = str(value or "").strip()
    if not text:
        return None
    explicit_percent = text.endswith("%")
    if explicit_percent:
        text = text[:-1].strip()
    try:
        rate = D(text)
    except (InvalidOperation, ValueError, TypeError):
        return None
    if rate is None:
        return None
    if explicit_percent:
        rate /= Decimal("100")
    return rate if rate.is_finite() and rate > Decimal("-1") and rate <= Decimal("1") else None


def _opportunity_add_months(start: date, months: int) -> date:
    month_index = start.year * 12 + (start.month - 1) + months
    year, month_index = divmod(month_index, 12)
    month = month_index + 1
    return date(year, month, min(start.day, calendar.monthrange(year, month)[1]))


_OPPORTUNITY_RANGE = re.compile(r"^\s*(\d{4}-\d{2}-\d{2})\s+(?:to|through)\s+(\d{4}-\d{2}-\d{2})\s*$", re.IGNORECASE)
_OPPORTUNITY_DURATION = re.compile(r"^\s*(\d+)\s*(d|day|days|w|week|weeks|m|month|months|q|quarter|quarters|y|year|years)\s*$", re.IGNORECASE)


def _opportunity_horizon(horizon: Any, start: date | None) -> tuple[date | None, date | None, int | None, str | None]:
    """Resolve a bounded duration or explicit date range from frozen inputs."""
    if isinstance(horizon, Mapping):
        start_value = _opportunity_date(horizon.get("start") or horizon.get("start_at") or horizon.get("from"))
        end_value = _opportunity_date(horizon.get("end") or horizon.get("end_at") or horizon.get("to"))
        if start_value is None or end_value is None or end_value <= start_value:
            return None, None, None, "A concrete opportunity horizon start and end are required."
        return start_value, end_value, (end_value - start_value).days, None
    text = str(horizon or "").strip()
    if not text:
        return None, None, None, "A bounded opportunity horizon is required."
    matched_range = _OPPORTUNITY_RANGE.fullmatch(text)
    if matched_range:
        start_value = _opportunity_date(matched_range.group(1))
        end_value = _opportunity_date(matched_range.group(2))
        if start_value is None or end_value is None or end_value <= start_value:
            return None, None, None, "The opportunity horizon date range is invalid."
        return start_value, end_value, (end_value - start_value).days, None
    duration = _OPPORTUNITY_DURATION.fullmatch(text)
    if not duration or start is None:
        return None, None, None, "A concrete start date and bounded opportunity horizon are required."
    amount = int(duration.group(1))
    unit = duration.group(2).casefold()
    if amount <= 0:
        return None, None, None, "The opportunity horizon must be positive."
    if unit.startswith("d"):
        end_value = start + timedelta(days=amount)
    elif unit.startswith("w"):
        end_value = start + timedelta(weeks=amount)
    elif unit.startswith("m"):
        end_value = _opportunity_add_months(start, amount)
    elif unit.startswith("q"):
        end_value = _opportunity_add_months(start, amount * 3)
    else:
        end_value = _opportunity_add_months(start, amount * 12)
    return start, end_value, (end_value - start).days, None


def _opportunity_day_basis(account: Mapping[str, Any], period: str) -> str | None:
    values: list[Any] = []
    for key in ("interest_day_basis", "day_basis", "rate_day_basis"):
        if account.get(key) is not None:
            values.append(account.get(key))
    match = re.search(r"(?:/|per\s*)\s*(360|365|366)\b", period.casefold())
    if match:
        values.append(match.group(1))
    if not values:
        return None
    parsed: list[int] = []
    for value in values:
        try:
            candidate = int(str(value).strip())
        except (TypeError, ValueError):
            return None
        if candidate not in {360, 365, 366}:
            return None
        parsed.append(candidate)
    return str(parsed[0]) if len(set(parsed)) == 1 else None


def _opportunity_convention(value: Any) -> tuple[str | None, int | None]:
    text = " ".join(str(value or "").casefold().replace("_", " ").replace("-", " ").split())
    if not text:
        return None, None
    if text in {"simple", "simple interest", "non compounding", "noncompounding", "none"}:
        return "simple", 1
    if text in {"annual", "annually", "effective", "effective annual", "effective annual rate", "apy"}:
        return "effective_annual", 1
    frequencies = {"daily": 365, "monthly": 12, "quarterly": 4, "semiannual": 2, "semi annual": 2, "biannual": 2}
    for label, frequency in frequencies.items():
        if text == label or text == f"effective {label}" or text == f"{label} compounding":
            return f"effective_{label.replace(' ', '_')}", frequency
    return None, None


def _opportunity_default(
    *,
    principal: Any,
    currency: Any,
    horizon: Any,
    as_of: Any,
    account_id: Any,
    benchmark_ticker: Any,
    benchmark_rationale: Any,
    source_refs: Sequence[Any],
) -> dict[str, Any]:
    ticker = str(benchmark_ticker or "").strip().upper() or None
    refs = _refs(source_refs)
    return {
        "status": "unavailable",
        "alternative": "cash",
        "reason": "",
        "as_of": str(as_of or "").strip() or None,
        "start_at": None,
        "end_at": None,
        "horizon": str(horizon or "").strip() or None,
        "days": None,
        "account_id": str(account_id or "").strip() or None,
        "currency": _currency(currency),
        "principal": None,
        "interest_rate": None,
        "interest_rate_currency": None,
        "interest_rate_period": None,
        "interest_compounding": None,
        "convention": None,
        "day_basis": None,
        "rate_observed_at": None,
        "term_expires_at": None,
        "rate_assumption": None,
        "expected_return": None,
        "expected_gain": None,
        "expected_value": None,
        "benchmark_ticker": ticker,
        "benchmark_rationale": str(benchmark_rationale or "").strip() or None,
        "benchmark_status": "unavailable",
        "benchmark_expected_return": None,
        "benchmark_reason": "No supported forward benchmark analysis was supplied; expected return remains unavailable." if ticker else "No benchmark was selected; expected return remains unavailable.",
        "source_refs": refs,
        "missing_inputs": [],
        "limitations": ["Taxes, fees, inflation, financing and currency effects remain unknown; no net or excess return is inferred."],
        "code_version": OPPORTUNITY_COST_CODE_VERSION,
    }


def build_opportunity_cost(
    snapshot: Mapping[str, Any] | None,
    *,
    principal: Any,
    currency: str | None,
    direction: str = "long",
    capital_basis: Any = None,
    account_id: str | None = None,
    horizon: Any = None,
    as_of: Any = None,
    benchmark_ticker: str | None = None,
    benchmark_rationale: str | None = None,
    source_refs: Sequence[Any] = (),
) -> dict[str, Any]:
    """Project a proposed allocation against frozen, currency-matched cash terms.

    The account balance is used only to bind the selected currency and dated
    terms.  The principal is the proposed allocation supplied by sizing; raw
    account amounts and maximum capacity never become the alternative's base.
    """
    if isinstance(snapshot, Mapping) and isinstance(snapshot.get("portfolio_snapshot"), Mapping):
        nested_snapshot = dict(snapshot.get("portfolio_snapshot") or {})
        for key, value in snapshot.items():
            if key != "portfolio_snapshot" and key not in nested_snapshot:
                nested_snapshot[key] = value
        snapshot = nested_snapshot
    direction = str(direction or "long").strip().lower()
    if direction == "short":
        # A global snapshot capital field cannot be bound safely to one short
        # candidate. Callers must pass a separately resolved, instrument-bound
        # capital/collateral requirement.
        if capital_basis is None:
            principal = None
        else:
            principal = capital_basis
    result = _opportunity_default(
        principal=principal,
        currency=currency,
        horizon=horizon,
        as_of=as_of,
        account_id=account_id,
        benchmark_ticker=benchmark_ticker,
        benchmark_rationale=benchmark_rationale,
        source_refs=source_refs,
    )

    def unavailable(reason: str, *missing: str) -> dict[str, Any]:
        result["reason"] = reason
        result["missing_inputs"] = list(dict.fromkeys([*result.get("missing_inputs", []), *missing]))
        return result

    if direction == "short" and principal is None:
        return unavailable("Short collateral/capital basis is unavailable; short notional is not cash principal.", "short_collateral_capital_basis")

    try:
        amount = D(principal)
    except (InvalidOperation, ValueError, TypeError):
        amount = None
    if amount is None or amount <= Decimal("0"):
        return unavailable("A positive proposed allocation is required for the cash alternative.", "proposed_allocation")
    result["principal"] = quantize(amount)
    target_currency = _currency(currency)
    if target_currency is None:
        return unavailable("A supported allocation currency is required for the cash alternative.", "currency")

    if not isinstance(snapshot, Mapping):
        return unavailable("A frozen portfolio snapshot is required for account cash terms.", "portfolio_snapshot")
    accounts = snapshot.get("accounts")
    if not isinstance(accounts, list):
        return unavailable("Frozen account records are unavailable.", "accounts")
    if account_id:
        selected = next((item for item in accounts if isinstance(item, Mapping) and str(item.get("id")) == str(account_id)), None)
        if selected is None:
            return unavailable("The selected account is not present in the frozen snapshot.", "account")
    else:
        candidates = [item for item in accounts if isinstance(item, Mapping)]
        if len(candidates) != 1:
            return unavailable("An explicit account is required when the frozen snapshot has zero or multiple accounts.", "account_id")
        selected = candidates[0]
    account = _dict(selected)
    result["account_id"] = str(account.get("id") or account_id or "").strip() or None

    balance_rows = account.get("balances")
    matching_balances: list[Mapping[str, Any]] = []
    if isinstance(balance_rows, list):
        for balance in balance_rows:
            if not isinstance(balance, Mapping) or _currency(balance.get("currency")) != target_currency:
                continue
            if not _valid_observation(balance):
                continue
            matching_balances.append(balance)
    account_currency = _currency(account.get("base_currency") or account.get("currency"))
    if not matching_balances and account_currency != target_currency:
        return unavailable("The selected account has no dated cash terms in the allocation currency; FX conversion is unsupported here.", "cash_currency")
    if isinstance(balance_rows, list) and balance_rows and not matching_balances and account_currency == target_currency:
        return unavailable("A dated, confirmed cash observation in the allocation currency is required.", "cash_observation")
    explicit_rate_currency = _currency(account.get("interest_rate_currency") or account.get("rate_currency"))
    rate_currency = explicit_rate_currency or account_currency
    if rate_currency is None or rate_currency != target_currency:
        return unavailable("The frozen account rate is not explicitly bound to the allocation currency; it is not applied across currencies.", "interest_rate_currency")

    rate_observation_raw = account.get("interest_rate_observed_at") or account.get("rate_observed_at") or account.get("observed_at")
    rate_observed = _opportunity_date(rate_observation_raw)
    if rate_observed is None:
        return unavailable("The cash rate has no dated observation.", "interest_rate_observed_at")
    result["rate_observed_at"] = rate_observed.isoformat()
    decision_date = _opportunity_date(as_of) or _opportunity_date(snapshot.get("as_of")) or rate_observed
    if decision_date < rate_observed:
        return unavailable("The frozen cash rate was observed after the decision as-of date.", "interest_rate_observed_at")
    account_observed = _opportunity_date(account.get("observed_at"))
    if account_observed is not None and account_observed > decision_date:
        return unavailable("The selected account observation is after the decision as-of date.", "account_observed_at")
    for balance in matching_balances:
        balance_observed = _opportunity_date(balance.get("observed_at"))
        if balance_observed is None:
            return unavailable("A dated matching-currency cash observation is required.", "cash_observation")
        if balance_observed > decision_date:
            return unavailable("The matching-currency cash observation is after the decision as-of date.", "cash_observed_at")
    result["as_of"] = str(as_of or "").strip() or decision_date.isoformat()
    start_date, end_date, days, horizon_error = _opportunity_horizon(horizon, decision_date)
    if horizon_error or start_date is None or end_date is None or days is None:
        return unavailable(horizon_error or "A bounded opportunity horizon is required.", "horizon")
    if start_date < rate_observed:
        return unavailable("The opportunity horizon starts before the frozen cash-rate observation.", "horizon")
    if start_date > decision_date:
        return unavailable("The opportunity horizon starts after the decision as-of date.", "horizon")
    result["start_at"] = start_date.isoformat()
    result["end_at"] = end_date.isoformat()
    result["days"] = days
    result["horizon"] = str(horizon or "").strip() or f"{start_date.isoformat()} to {end_date.isoformat()}"

    expiry_raw = next((account.get(key) for key in ("interest_rate_expires_at", "rate_expires_at", "term_expires_at", "expires_at") if account.get(key)), None)
    expiry = _opportunity_date(expiry_raw)
    if expiry_raw is not None and expiry is None:
        return unavailable("The explicit cash-rate term expiry is not a valid date.", "interest_rate_term")
    if expiry is not None:
        result["term_expires_at"] = expiry.isoformat()
        if end_date > expiry:
            return unavailable("The frozen cash-rate term expires before the opportunity horizon ends.", "interest_rate_term")

    period_raw = account.get("interest_rate_period")
    period = " ".join(str(period_raw or "").casefold().replace("_", " ").replace("-", " ").split())
    annual_period = period in {"annual", "annually", "annualized", "year", "yearly", "per annum", "pa", "apy"} or period.startswith("annual/") or period.startswith("annual ")
    if not annual_period:
        return unavailable("An explicit annual cash-rate period is required; periodic terms are not annualized implicitly.", "interest_rate_period")
    rate = _opportunity_rate(account.get("interest_rate"))
    if rate is None:
        return unavailable("A finite frozen account interest rate is required.", "interest_rate")
    convention, frequency = _opportunity_convention(account.get("interest_compounding"))
    if convention is None or frequency is None:
        return unavailable("An explicit cash-rate compounding convention is required; it is not inferred.", "interest_compounding")
    if period == "apy" and convention != "effective_annual":
        return unavailable("APY is already an effective annual rate and cannot be combined with periodic nominal compounding.", "interest_compounding")
    day_basis = _opportunity_day_basis(account, period)
    if day_basis is None:
        return unavailable("An explicit day basis is required for the cash-rate projection; it is not inferred.", "day_basis")
    basis = D(day_basis)
    result.update({
        "currency": target_currency,
        "interest_rate": quantize(rate),
        "interest_rate_currency": rate_currency,
        "interest_rate_period": str(period_raw).strip(),
        "interest_compounding": str(account.get("interest_compounding") or "").strip(),
        "convention": convention,
        "day_basis": day_basis,
        "rate_assumption": "conditional constant-rate assumption; future rate changes are unknown",
    })
    if basis is None or basis <= Decimal("0"):
        return unavailable("The cash-rate day basis is invalid.", "day_basis")
    year_fraction = Decimal(days) / basis
    try:
        if convention == "simple":
            expected_return = rate * year_fraction
        elif frequency == 1:
            expected_return = (Decimal("1") + rate) ** year_fraction - Decimal("1")
        else:
            expected_return = (Decimal("1") + rate / Decimal(frequency)) ** (Decimal(frequency) * year_fraction) - Decimal("1")
    except (ArithmeticError, InvalidOperation, ValueError):
        return unavailable("The cash-rate projection could not be computed from the frozen terms.", "cash_projection")
    expected_gain = amount * expected_return
    expected_value = amount + expected_gain
    result.update({
        "status": "complete",
        "reason": "Conditional cash alternative from the selected account's dated rate terms; it is not a guaranteed return.",
        "expected_return": quantize(expected_return),
        "expected_gain": quantize(expected_gain),
        "expected_value": quantize(expected_value),
    })
    for candidate in (account.get("source_refs"), account.get("source_id"), *(balance.get("source_id") for balance in matching_balances)):
        result["source_refs"] = _refs([*result["source_refs"], *(_refs(candidate) if not isinstance(candidate, str) else [candidate])])
    return result


def _valid_observation(row: Mapping[str, Any]) -> bool:
    return bool(row.get("observed_at")) and _status(row.get("status")) in {"confirmed", "validated", "reconciled", "user_reported"}


def _fx(snapshot: Mapping[str, Any], value: Decimal | None, source: str | None, target: str | None) -> Decimal | None:
    return _convert_amount(value, source, target, snapshot.get("fx_rates"))


def _cash(snapshot: Mapping[str, Any], base: str | None) -> tuple[Decimal | None, list[str]]:
    total = Decimal("0")
    missing: list[str] = []
    accounts = snapshot.get("accounts")
    if not isinstance(accounts, list):
        return None, ["accounts"]
    seen = False
    for account in accounts:
        if not isinstance(account, Mapping):
            missing.append("account_record")
            continue
        balances = account.get("balances")
        if not isinstance(balances, list):
            missing.append(f"balances:{account.get('id') or 'account'}")
            continue
        for balance in balances:
            if not isinstance(balance, Mapping):
                missing.append("balance_record")
                continue
            currency = _currency(balance.get("currency"))
            amount = D(balance.get("amount"))
            if currency is None or amount is None:
                missing.append("cash_currency_or_amount")
                continue
            if not _valid_observation(balance):
                missing.append(f"cash_observation:{currency}")
                continue
            converted = _fx(snapshot, amount, currency, base)
            if converted is None:
                missing.append(f"fx_rate:{currency}_{base}")
                continue
            total += converted
            seen = True
    return (total if seen else None), missing if not seen or missing else []


def _position_exposure(position: Mapping[str, Any], snapshot: Mapping[str, Any], base: str | None) -> tuple[dict[str, Any], Decimal | None, list[str]]:
    instrument = str(position.get("instrument") or position.get("symbol") or position.get("ticker") or "").strip().upper()
    account_id = str(position.get("account_id") or position.get("account") or "").strip() or None
    missing: list[str] = []
    if not instrument:
        instrument = "UNKNOWN"
        missing.append("instrument")
    issuer = str(position.get("issuer") or position.get("issuer_name") or "").strip() or None
    sector = str(position.get("sector") or "").strip() or None
    currency = _currency(position.get("market_value_currency") or position.get("currency"))
    if currency is None:
        missing.append(f"currency:{instrument}")
    if not sector:
        missing.append(f"classification:{instrument}")
    if not issuer:
        missing.append(f"issuer:{instrument}")
    if not _valid_observation(position):
        missing.append(f"price_observation:{instrument}")
    value = D(position.get("market_value"))
    if value is None:
        price = D(position.get("price"))
        quantity = D(position.get("quantity"))
        if price is not None and quantity is not None and position.get("price_as_of") and position.get("price_source_id"):
            value = price * abs(quantity)
        else:
            missing.append(f"market_value:{instrument}")
    converted = _fx(snapshot, value, currency, base) if value is not None and currency is not None else None
    if converted is None and value is not None:
        missing.append(f"fx_rate:{currency}_{base}")
    quantity = D(position.get("quantity"))
    direction = str(position.get("direction") or position.get("side") or "").strip().lower()
    if direction not in {"long", "short"}:
        direction = "short" if quantity is not None and quantity < 0 else "long"
    known = not missing and converted is not None
    exposure = {
        "instrument": instrument,
        "account_id": account_id,
        "issuer": issuer,
        "sector": sector,
        "currency": currency,
        "direction": direction,
        "market_value": quantize(converted),
        "weight": None,
        "known_common_theme": position.get("known_common_theme") or position.get("theme"),
        "status": "known" if known else "unknown" if converted is None else "partial",
        "missing_inputs": list(dict.fromkeys(missing)),
        "source_refs": _refs(position.get("source_refs") or position.get("source_id")),
    }
    return exposure, converted, missing


def _summary(exposures: Sequence[Mapping[str, Any]], *, cash: Decimal | None, total_value: Decimal | None, base_currency: str | None, missing: Sequence[str]) -> dict[str, Any]:
    gross_long = Decimal("0")
    gross_short = Decimal("0")
    sectors: dict[str, Decimal] = {}
    instruments: dict[str, Decimal] = {}
    for row in exposures:
        value = D(row.get("market_value"))
        if value is None:
            continue
        if row.get("direction") == "short":
            gross_short += value
        else:
            gross_long += value
        instrument = str(row.get("instrument") or "UNKNOWN").strip().upper()
        instruments[instrument] = instruments.get(instrument, Decimal("0")) + value
        sector = str(row.get("sector") or "").strip()
        if sector:
            sectors[sector] = sectors.get(sector, Decimal("0")) + value
    net = gross_long - gross_short
    denominator = total_value or (gross_long + (cash or Decimal("0")))
    position_weights = {instrument: quantize(value / denominator) for instrument, value in instruments.items() if denominator > 0}
    sector_weights = {key: quantize(value / denominator) for key, value in sectors.items() if denominator > 0}
    return {
        "base_currency": base_currency,
        "gross_long": quantize(gross_long),
        "gross_short": quantize(gross_short),
        "net_exposure": quantize(net),
        "cash_headroom": quantize(cash),
        "total_value": quantize(denominator),
        "holding_count": len(exposures),
        "position_weights": position_weights,
        "sector_weights": sector_weights,
        "incremental_planned_loss": None,
        "missing_inputs": list(dict.fromkeys(str(item) for item in missing if str(item))),
    }


def _check(key: str, status: str, reason: str) -> dict[str, str]:
    allowed = {"pass", "fail", "unavailable", "not_applicable"}
    return {"key": key, "status": status if status in allowed else "unavailable", "reason": reason}


def check_portfolio_limits(
    *,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    candidate: Mapping[str, Any] | None = None,
) -> list[dict[str, str]]:
    """Apply configured cash, position, sector and holding limits."""
    settings = _dict(snapshot.get("risk_settings"))
    policy = _dict(snapshot.get("portfolio_policy"))
    checks: list[dict[str, str]] = []
    candidate = candidate or {}
    candidate_direction = str(candidate.get("direction") or "long").lower()
    if candidate_direction == "short":
        if candidate.get("borrow_available") is not True:
            checks.append(_check("short_borrow_availability", "unavailable", "Short borrow availability is not explicitly confirmed."))
        else:
            checks.append(_check("short_borrow_availability", "pass", "Short borrow availability is explicitly confirmed."))
    total = D(after.get("total_value"))
    if total is None or total <= 0:
        checks.append(_check("portfolio_value", "unavailable", "A dated valued portfolio snapshot is required for percentage limits."))
    max_position = D(settings.get("max_position_weight"))
    candidate_value = D(candidate.get("notional") or candidate.get("market_value"))
    if max_position is None:
        checks.append(_check("max_position_weight", "unavailable", "No configured maximum position weight is available."))
    elif total is None or candidate_value is None:
        checks.append(_check("max_position_weight", "unavailable", "Position and portfolio values are required to test the maximum position weight."))
    else:
        existing = D(before.get("position_weights", {}).get(str(candidate.get("instrument") or candidate.get("ticker") or ""))) if isinstance(before.get("position_weights"), Mapping) else None
        resulting = (existing or Decimal("0")) + candidate_value / total
        checks.append(_check("max_position_weight", "pass" if resulting <= max_position else "fail", f"Resulting position weight {quantize(resulting)} {'is within' if resulting <= max_position else 'exceeds'} configured maximum {quantize(max_position)}."))
    max_sector = D(settings.get("max_sector_weight"))
    sector = str(candidate.get("sector") or "").strip()
    if max_sector is None:
        checks.append(_check("max_sector_weight", "unavailable", "No configured maximum sector weight is available."))
    elif not sector or total is None or candidate_value is None:
        checks.append(_check("max_sector_weight", "unavailable", "Sector classification and valued portfolio inputs are required."))
    else:
        current_sector = D(before.get("sector_weights", {}).get(sector)) if isinstance(before.get("sector_weights"), Mapping) else None
        if current_sector is None:
            checks.append(_check("max_sector_weight", "unavailable", f"Existing {sector} exposure is unknown; it is not treated as zero."))
        else:
            resulting = current_sector + candidate_value / total
            checks.append(_check("max_sector_weight", "pass" if resulting <= max_sector else "fail", f"Resulting sector weight {quantize(resulting)} {'is within' if resulting <= max_sector else 'exceeds'} configured maximum {quantize(max_sector)}."))
    cash_floor = D(settings.get("cash_floor"))
    cash_after = D(after.get("cash_headroom"))
    if cash_floor is None:
        checks.append(_check("cash_floor", "unavailable", "No configured cash floor is available."))
    elif cash_after is None or total is None:
        checks.append(_check("cash_floor", "unavailable", "Dated cash and portfolio values are required to test the cash floor."))
    else:
        weight = cash_after / total
        checks.append(_check("cash_floor", "pass" if weight >= cash_floor else "fail", f"Resulting cash weight {quantize(weight)} {'meets' if weight >= cash_floor else 'falls below'} configured floor {quantize(cash_floor)}."))
    max_positions = policy.get("max_positions")
    try:
        max_positions_int = int(max_positions) if max_positions is not None else None
    except (TypeError, ValueError):
        max_positions_int = None
    if max_positions_int is None:
        checks.append(_check("holding_count", "unavailable", "No maximum holding count is configured."))
    else:
        count = int(after.get("holding_count") or 0)
        checks.append(_check("holding_count", "pass" if count <= max_positions_int else "fail", f"Resulting holding count {count} {'is within' if count <= max_positions_int else 'exceeds'} configured maximum {max_positions_int}."))
    return checks


def build_portfolio_context(
    snapshot: Mapping[str, Any] | None,
    *,
    candidate: Mapping[str, Any] | None = None,
    sizing: Mapping[str, Any] | None = None,
    as_of: str | None = None,
) -> dict[str, Any]:
    """Build code-owned before/after portfolio context from a frozen snapshot."""
    if not isinstance(snapshot, Mapping):
        return {"status": "unavailable", "snapshot_id": None, "as_of": as_of, "base_currency": None, "before": {}, "after": {}, "exposures": [], "checks": [], "missing_inputs": ["portfolio_snapshot"]}
    base = _currency(snapshot.get("base_currency") or snapshot.get("portfolio_currency") or snapshot.get("currency"))
    if base is None:
        # A single observed currency is safe to select; mixed currencies still
        # require an explicit base and FX map.
        currencies = {_currency(item.get("currency") or item.get("market_value_currency")) for item in (snapshot.get("positions") or []) if isinstance(item, Mapping)}
        for account in snapshot.get("accounts") or []:
            if isinstance(account, Mapping):
                for balance in account.get("balances") or []:
                    if isinstance(balance, Mapping):
                        currencies.add(_currency(balance.get("currency")))
        currencies.discard(None)
        if len(currencies) == 1:
            base = next(iter(currencies))
    missing: list[str] = []
    if base is None:
        missing.append("base_currency")
    exposures: list[dict[str, Any]] = []
    positions = snapshot.get("positions")
    if not isinstance(positions, list):
        # A frozen account snapshot may legitimately have no positions.  An
        # individual unknown position is represented as an unknown exposure;
        # an omitted empty list is not silently converted into an exposure.
        positions = []
    for position in positions:
        if not isinstance(position, Mapping):
            missing.append("position_record")
            continue
        exposure, _value, issues = _position_exposure(position, snapshot, base)
        exposures.append(exposure)
        missing.extend(issues)
    cash, cash_missing = _cash(snapshot, base)
    missing.extend(cash_missing)
    total_value = D(snapshot.get("total_portfolio_value"))
    if total_value is not None and base is not None:
        total_value = _fx(snapshot, total_value, _currency(snapshot.get("total_portfolio_currency") or base), base)
    before = _summary(exposures, cash=cash, total_value=total_value, base_currency=base, missing=missing)
    after_exposures = list(exposures)
    after_cash = cash
    candidate_map = _dict(candidate)
    sizing_map = _dict(sizing)
    if candidate_map or sizing_map:
        merged = {**candidate_map, **sizing_map}
        instrument = str(merged.get("instrument") or merged.get("ticker") or merged.get("symbol") or "").strip().upper()
        value = D(merged.get("notional") or merged.get("market_value"))
        direction = str(merged.get("direction") or "long").lower()
        if value is None:
            missing.append("candidate_notional")
        else:
            candidate_currency = _currency(merged.get("currency") or base)
            converted = _fx(snapshot, value, candidate_currency, base)
            if converted is None:
                missing.append(f"fx_rate:{candidate_currency}_{base}")
            else:
                after_exposures.append({"instrument": instrument or "UNKNOWN", "account_id": merged.get("account_id"), "issuer": merged.get("issuer"), "sector": merged.get("sector"), "currency": candidate_currency, "direction": direction, "market_value": quantize(converted), "weight": None, "known_common_theme": merged.get("known_common_theme") or merged.get("theme"), "status": "known", "missing_inputs": [], "source_refs": _refs(merged.get("source_refs"))})
                # Long purchases consume cash.  Short sale proceeds are not
                # available long cash and therefore must not inflate the
                # cash headroom used by portfolio checks.
                if direction == "long" and after_cash is not None:
                    after_cash -= converted
    after = _summary(after_exposures, cash=after_cash, total_value=total_value, base_currency=base, missing=missing)
    if candidate_map or sizing_map:
        planned = D({**candidate_map, **sizing_map}.get("planned_loss"))
        after["incremental_planned_loss"] = quantize(planned) if planned is not None else None
    checks = check_portfolio_limits(before=before, after=after, snapshot=snapshot, candidate={**candidate_map, **sizing_map})
    missing.extend(str(item) for item in before.get("missing_inputs", []))
    status = "complete" if not missing and all(item.get("status") not in {"unavailable", "fail"} for item in checks) else "partial" if exposures or checks else "unavailable"
    return {"status": status, "snapshot_id": str(snapshot.get("snapshot_id") or snapshot.get("id") or "") or None, "as_of": as_of or snapshot.get("as_of") or snapshot.get("observed_at"), "base_currency": base, "before": before, "after": after, "exposures": exposures, "checks": checks, "missing_inputs": list(dict.fromkeys(str(item) for item in missing if str(item))), "code_version": PORTFOLIO_CODE_VERSION}


def compute_exposures(snapshot: Mapping[str, Any] | None, *, base_currency: str | None = None) -> dict[str, Any]:
    """Compatibility helper returning just the frozen exposure rows/summary."""
    if not isinstance(snapshot, Mapping):
        return {"status": "unavailable", "exposures": [], "missing_inputs": ["portfolio_snapshot"]}
    value = dict(snapshot)
    if base_currency:
        value["base_currency"] = base_currency
    context = build_portfolio_context(value)
    return {"status": context["status"], "exposures": context["exposures"], "summary": context["before"], "missing_inputs": context["missing_inputs"]}


calculate_portfolio_context = build_portfolio_context
build_context = build_portfolio_context


__all__ = ["PORTFOLIO_CODE_VERSION", "OPPORTUNITY_COST_CODE_VERSION", "build_portfolio_context", "calculate_portfolio_context", "compute_exposures", "check_portfolio_limits", "build_context", "build_opportunity_cost"]
