"""Pure, Decimal based calculations used by workflows and tests."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import math
import re
from typing import Any, Iterable, Mapping, Sequence


Q = Decimal("0.00000001")


def D(value: str | int | Decimal | None) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("invalid decimal")
    if not result.is_finite():
        raise ValueError("decimal must be finite")
    return result


def quantize(value: Decimal | None) -> str | None:
    return None if value is None else format(value.quantize(Q, rounding=ROUND_HALF_UP), "f")


def safe_ratio(numerator: str | int | Decimal | None, denominator: str | int | Decimal | None) -> str | None:
    num, den = D(numerator), D(denominator)
    if num is None or den is None or den == 0:
        return None
    return quantize(num / den)


def price_after_discount(price: str | int | Decimal | None, discount_fraction: str | int | Decimal | None) -> str | None:
    """Compute a hypothetical pullback price from one verified price fact."""
    current, discount = D(price), D(discount_fraction)
    if current is None or discount is None or current <= 0 or discount < 0 or discount >= 1:
        raise ValueError("price discount inputs must be positive and 0 <= discount < 1")
    return quantize(current * (Decimal(1) - discount))


def issuance_share_count(
    *,
    existing_assets: str | Decimal,
    existing_shares: str | Decimal,
    cash_raised: str | Decimal,
    issue_price: str | Decimal,
) -> dict[str, str | None]:
    """Apply an issuance consistently to cash/assets and share denominator."""
    assets, shares, cash, price = (D(v) for v in (existing_assets, existing_shares, cash_raised, issue_price))
    if any(value is None for value in (assets, shares, cash, price)) or price == 0 or price < 0 or shares < 0:
        raise ValueError("issuance inputs must be finite and issue price positive")
    issued = cash / price
    return {
        "assets_after": quantize(assets + cash),
        "shares_after": quantize(shares + issued),
        "issued_shares": quantize(issued),
        "price": quantize(price),
    }


def distinguish_share_counts(outstanding: str | None, weighted_average: str | None) -> dict[str, object]:
    """Never substitute EPS weighted-average shares for current shares."""
    return {
        "outstanding_shares": outstanding,
        "weighted_average_shares": weighted_average,
        "usable_for_current_market_cap": outstanding is not None,
        "missing_reason": None if outstanding is not None else "Current outstanding share count is unavailable.",
    }


def simple_returns(prices: Iterable[str | Decimal]) -> list[str | None]:
    values = [D(value) for value in prices]
    result: list[str | None] = [None]
    for previous, current in zip(values, values[1:]):
        result.append(safe_ratio(current - previous, previous) if previous is not None and current is not None else None)
    return result


def moving_average(prices: Iterable[str | Decimal], window: int) -> list[str | None]:
    if window < 1:
        raise ValueError("window must be positive")
    values = [D(value) for value in prices]
    output: list[str | None] = []
    for index in range(len(values)):
        sample = values[max(0, index - window + 1) : index + 1]
        output.append(quantize(sum(sample, Decimal(0)) / len(sample)) if sample and all(value is not None for value in sample) else None)
    return output


def _currency(value: Any) -> str | None:
    text = str(value or "").strip().upper()
    return text if re.fullmatch(r"[A-Z]{3}", text) else None


def _fx_rate(fx_rates: Mapping[Any, Any] | None, from_currency: str | None, to_currency: str | None) -> Decimal | None:
    """Read an explicitly supplied FX observation, including its inverse.

    The calculator never fetches or assumes an FX rate.  Nested mappings,
    tuple keys, and common ``CAD/USD`` string keys are accepted so callers can
    pass the shape already used by their account import or market connector.
    """
    source, target = _currency(from_currency), _currency(to_currency)
    if source is None or target is None:
        return None
    if source == target:
        return Decimal("1")
    if not isinstance(fx_rates, Mapping):
        return None

    def read(key: Any) -> Decimal | None:
        try:
            value = fx_rates.get(key)  # type: ignore[arg-type]
        except (AttributeError, TypeError):
            value = None
        if value is None:
            return None
        try:
            rate = D(value)
        except ValueError:
            return None
        return rate if rate is not None and rate > 0 else None

    direct_keys: tuple[Any, ...] = (
        (source, target),
        f"{source}/{target}",
        f"{source}_{target}",
        f"{source}-{target}",
        f"{source}{target}",
        f"{source.lower()}/{target.lower()}",
    )
    for key in direct_keys:
        rate = read(key)
        if rate is not None:
            return rate
    nested = fx_rates.get(source) if hasattr(fx_rates, "get") else None
    if isinstance(nested, Mapping):
        rate = read_nested = nested.get(target)
        try:
            parsed = D(read_nested)
        except ValueError:
            parsed = None
        if parsed is not None and parsed > 0:
            return parsed

    inverse_keys: tuple[Any, ...] = (
        (target, source),
        f"{target}/{source}",
        f"{target}_{source}",
        f"{target}-{source}",
        f"{target}{source}",
        f"{target.lower()}/{source.lower()}",
    )
    for key in inverse_keys:
        rate = read(key)
        if rate is not None:
            return Decimal("1") / rate
    nested = fx_rates.get(target) if hasattr(fx_rates, "get") else None
    if isinstance(nested, Mapping):
        try:
            parsed = D(nested.get(source))
        except ValueError:
            parsed = None
        if parsed is not None and parsed > 0:
            return Decimal("1") / parsed
    return None


def _convert_amount(
    amount: Decimal | None,
    from_currency: str | None,
    to_currency: str | None,
    fx_rates: Mapping[Any, Any] | None,
) -> Decimal | None:
    if amount is None:
        return None
    rate = _fx_rate(fx_rates, from_currency, to_currency)
    return None if rate is None else amount * rate


def _sizing_result(
    *,
    execution_state: str,
    account_id: str | None,
    direction: str,
    currency: str | None,
    entry_price: Decimal | None,
    notional_cap_price: Decimal | None = None,
    risk_entry_price: Decimal | None = None,
    planned_loss_per_share: Decimal | None = None,
    planned_loss: Decimal | None = None,
    approved_budget: Decimal | None,
    available_cash: Decimal | None,
    shares: int | None,
    notional: Decimal | None,
    resulting_cash: Decimal | None,
    missing_inputs: list[str],
    checks: list[dict[str, Any]],
    reason: str,
    borrow_available: bool | None = None,
    margin_available: Decimal | None = None,
    maximum_permitted_shares: int | None = None,
    recommended_shares: int | None = None,
    recommended_notional: Decimal | None = None,
    recommended_planned_loss: Decimal | None = None,
    allocation_rationale: str | None = None,
    binding_cap: str | None = None,
    short_permission: bool | None = None,
    borrow_cost_status: str | None = None,
    margin_terms_confirmed: bool | None = None,
) -> dict[str, Any]:
    state = execution_state if execution_state in {"ready", "awaiting_input", "failed"} else "failed"
    return {
        "execution_state": state,
        "status": state,
        "valid": state == "ready" and bool(shares and shares > 0),
        "eligible": state == "ready" and bool(shares and shares > 0),
        "account_id": account_id,
        "direction": direction,
        "currency": currency,
        "entry_price": quantize(entry_price),
        "notional_cap_price": quantize(notional_cap_price if notional_cap_price is not None else entry_price),
        "risk_entry_price": quantize(risk_entry_price if risk_entry_price is not None else entry_price),
        "planned_loss_per_share": quantize(planned_loss_per_share),
        "planned_loss": quantize(planned_loss),
        "approved_budget": quantize(approved_budget),
        "available_cash": quantize(available_cash),
        "shares": shares,
        "whole_shares": shares,
        "maximum_permitted_shares": maximum_permitted_shares if maximum_permitted_shares is not None else shares,
        "recommended_shares": recommended_shares,
        "recommended_notional": quantize(recommended_notional),
        "recommended_planned_loss": quantize(recommended_planned_loss),
        "allocation_rationale": allocation_rationale,
        "notional": quantize(notional),
        "resulting_cash": quantize(resulting_cash),
        "borrow_available": borrow_available,
        "short_permission": short_permission,
        "borrow_cost_status": borrow_cost_status,
        "margin_terms_confirmed": margin_terms_confirmed,
        "margin_available": quantize(margin_available),
        "missing_inputs": list(dict.fromkeys(str(item) for item in missing_inputs if str(item))),
        "checks": checks,
        "formula": "floor(usable_budget / entry_price)",
        "reason": reason,
        "binding_cap": binding_cap,
    }


def _account_from_snapshot(snapshot: Mapping[str, Any] | None, account_id: str | None) -> tuple[dict[str, Any] | None, list[str]]:
    if not isinstance(snapshot, Mapping):
        return None, []
    accounts = snapshot.get("accounts")
    if not isinstance(accounts, list):
        return None, []
    candidates = [item for item in accounts if isinstance(item, Mapping) and (account_id is None or str(item.get("id")) == str(account_id))]
    if account_id is not None and not candidates:
        return None, [f"account:{account_id}"]
    if account_id is None and len(candidates) != 1:
        return None, ["account_id"] if len(candidates) > 1 else ["account_snapshot"]
    return (dict(candidates[0]) if candidates else None), []


def _verified_cash_from_account(account: Mapping[str, Any] | None, currency: str, fx_rates: Mapping[Any, Any] | None) -> tuple[Decimal | None, list[str], str | None]:
    if not isinstance(account, Mapping):
        return None, [], None
    if account.get("reconciliation_status") not in {"reconciled", "confirmed", "validated"}:
        return None, ["verified_account_reconciliation"], None
    balances = account.get("balances")
    if not isinstance(balances, list):
        return None, ["verified_available_cash"], None
    target = _currency(currency)
    rows: list[tuple[Decimal, str]] = []
    for row in balances:
        if not isinstance(row, Mapping):
            continue
        row_currency = _currency(row.get("currency"))
        try:
            amount = D(row.get("amount"))
        except ValueError:
            amount = None
        if row_currency is None or amount is None:
            continue
        if row.get("status") not in {"confirmed", "validated", "reconciled"} or not row.get("observed_at"):
            continue
        rows.append((amount, row_currency))
    if not rows:
        return None, [f"available_cash:{target}"], None
    # Use same-currency cash without requiring FX for an unrelated balance.
    direct = [(amount, row_currency) for amount, row_currency in rows if row_currency == target]
    if direct:
        return sum((amount for amount, _ in direct), Decimal("0")), [], target
    # An FX quote values existing cash; it does not establish that a currency
    # exchange has occurred. Keep an independently configured budget usable
    # for illustration, but never label converted cash as already funded.
    return None, [f"available_cash:{target}", "confirm_account_currency_funding"], rows[0][1]


def _calculate_position_size(
    ticker: str | None = None,
    *,
    entry_price: str | int | Decimal | None,
    # For a permitted range, ``entry_price`` remains the executable/reference
    # value for compatibility.  The two explicit bounds below drive the
    # conservative notional and adverse-risk calculations independently.
    entry_lower_price: str | int | Decimal | None = None,
    entry_upper_price: str | int | Decimal | None = None,
    notional_cap_price: str | int | Decimal | None = None,
    risk_entry_price: str | int | Decimal | None = None,
    entry_range: Mapping[str, Any] | None = None,
    currency: str | None,
    account_id: str | None = None,
    snapshot: Mapping[str, Any] | None = None,
    account: Mapping[str, Any] | None = None,
    budget: str | int | Decimal | None = None,
    approved_budget: str | int | Decimal | None = None,
    budget_currency: str | None = None,
    available_cash: str | int | Decimal | None = None,
    available_cash_currency: str | None = None,
    existing_exposure: str | int | Decimal | None = None,
    existing_exposure_currency: str | None = None,
    total_portfolio_value: str | int | Decimal | None = None,
    total_portfolio_currency: str | None = None,
    max_position_value: str | int | Decimal | None = None,
    max_position_value_currency: str | None = None,
    max_position_weight: str | int | Decimal | None = None,
    cash_floor: str | int | Decimal | None = None,
    existing_sector_exposure: str | int | Decimal | None = None,
    existing_sector_exposure_currency: str | None = None,
    max_sector_weight: str | int | Decimal | None = None,
    risk_budget: str | int | Decimal | None = None,
    risk_budget_currency: str | None = None,
    stop_price: str | int | Decimal | None = None,
    stop_price_currency: str | None = None,
    risk_settings: Mapping[str, Any] | None = None,
    fx_rates: Mapping[Any, Any] | None = None,
    direction: str = "long",
    borrow_available: bool | None = None,
    short_permission: bool | None = None,
    borrow_cost_status: str | None = None,
    margin_terms_confirmed: bool | None = None,
    short_budget: str | int | Decimal | None = None,
    margin_available: str | int | Decimal | None = None,
    margin_currency: str | None = None,
    account_restrictions: Sequence[str] | None = None,
    eligible_account: bool | None = None,
    recommended_shares: int | None = None,
    allocation_rationale: str | None = None,
    operational_checks: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Calculate a deterministic whole-share position from explicit inputs.

    ``budget``/``approved_budget`` are caller-owned configuration values; no
    default percentage or account balance is fabricated.  A long position can
    use a verified account cash observation.  A short position has a separate
    path and requires explicit borrow permission plus short budget or margin;
    it never consumes the long cash balance.
    """
    direction = str(direction or "long").strip().lower()
    symbol = str(ticker or "").strip().upper() or None
    target_currency = _currency(currency)
    checks: list[dict[str, Any]] = [dict(check) for check in (operational_checks or ()) if isinstance(check, Mapping)]
    missing: list[str] = []
    if direction not in {"long", "short"}:
        return _sizing_result(execution_state="failed", account_id=account_id, direction=direction, currency=target_currency, entry_price=None, approved_budget=None, available_cash=None, shares=None, notional=None, resulting_cash=None, missing_inputs=[], checks=[], reason="direction must be long or short")
    # Normalize a scalar or a caller-supplied range without making a range
    # look like a single midpoint.  The upper bound is the absolute notional
    # cap for both directions.  The adverse-risk entry is direction-specific:
    # a long can be filled at the upper bound while a short's adverse loss
    # starts from the lower bound.
    range_value = dict(entry_range) if isinstance(entry_range, Mapping) else {}
    if entry_lower_price is None:
        entry_lower_price = range_value.get("lower")
    if entry_upper_price is None:
        entry_upper_price = range_value.get("upper")
    if notional_cap_price is None:
        notional_cap_price = entry_upper_price
    if risk_entry_price is None:
        risk_entry_price = entry_lower_price if direction == "short" else entry_upper_price
    try:
        price = D(entry_price)
        lower_bound = D(entry_lower_price)
        upper_bound = D(entry_upper_price)
        notional_price = D(notional_cap_price)
        risk_price = D(risk_entry_price)
    except ValueError:
        price = lower_bound = upper_bound = notional_price = risk_price = None
    if lower_bound is None and upper_bound is None and price is not None:
        lower_bound = upper_bound = price
    elif lower_bound is None:
        lower_bound = upper_bound
    elif upper_bound is None:
        upper_bound = lower_bound
    if notional_price is None:
        notional_price = upper_bound or price
    if risk_price is None:
        risk_price = lower_bound or price
    # ``price`` is retained as the compatibility/reference field.  The
    # actual share floor uses ``notional_price`` so a short range never
    # accidentally divides by its risk-entry lower bound.
    price_for_checks = notional_price or price
    # Use the conservative notional price for every notional cap and preserve
    # it in the legacy ``entry_price`` field.  The adverse-loss computation
    # below uses ``risk_price`` explicitly.
    if price_for_checks is not None:
        price = price_for_checks
    if price is None:
        missing.append("entry_price")
    elif price <= 0:
        return _sizing_result(execution_state="failed", account_id=account_id, direction=direction, currency=target_currency, entry_price=price, approved_budget=None, available_cash=None, shares=None, notional=None, resulting_cash=None, missing_inputs=[], checks=[], reason="entry_price must be positive")
    if notional_price is None or risk_price is None:
        missing.append("entry_price_range")
    elif notional_price <= 0 or risk_price <= 0 or (lower_bound is not None and upper_bound is not None and lower_bound > upper_bound):
        return _sizing_result(execution_state="failed", account_id=account_id, direction=direction, currency=target_currency, entry_price=price, notional_cap_price=notional_price, risk_entry_price=risk_price, approved_budget=None, available_cash=None, shares=None, notional=None, resulting_cash=None, missing_inputs=[], checks=[], reason="entry price range must contain positive ordered values")
    elif direction == "short" and risk_price > notional_price:
        return _sizing_result(execution_state="failed", account_id=account_id, direction=direction, currency=target_currency, entry_price=price, notional_cap_price=notional_price, risk_entry_price=risk_price, approved_budget=None, available_cash=None, shares=None, notional=None, resulting_cash=None, missing_inputs=[], checks=[], reason="short risk entry cannot exceed its notional cap price")
    if target_currency is None:
        missing.append("entry_currency")

    settings = dict(risk_settings) if isinstance(risk_settings, Mapping) else {}
    if max_position_weight is None:
        max_position_weight = settings.get("max_position_weight")
    if max_sector_weight is None:
        max_sector_weight = settings.get("max_sector_weight")
    if cash_floor is None:
        cash_floor = settings.get("cash_floor")
    if account_restrictions is None:
        account_restrictions = settings.get("account_restrictions") or []
    if isinstance(account, Mapping) and snapshot is None:
        snapshot = {"accounts": [dict(account)]}
    selected_account, account_missing = _account_from_snapshot(snapshot, account_id)
    missing.extend(account_missing)
    if selected_account is not None and account_id is None:
        account_id = str(selected_account.get("id"))
    if selected_account is None and isinstance(snapshot, Mapping) and snapshot.get("accounts"):
        # ``_account_from_snapshot`` already reported the precise missing
        # account/ambiguity condition; avoid treating the account as usable.
        pass
    if account_id is None:
        missing.append("account_id")

    if direction == "short":
        # Short eligibility is a separate operational contract.  Do not
        # infer permission, borrow terms or margin confirmation from cash or
        # from a provider's proposed quantity.
        if isinstance(selected_account, Mapping):
            def _account_short_value(primary: str, *aliases: str) -> Any:
                # A concrete account value, including False, wins over any
                # alias. Never fall back to an unscoped snapshot boolean.
                if primary in selected_account and selected_account.get(primary) is not None:
                    return selected_account.get(primary)
                for alias in aliases:
                    if alias in selected_account and selected_account.get(alias) is not None:
                        return selected_account.get(alias)
                return None
            if borrow_available is None:
                borrow_available = _account_short_value("borrow_available", "borrow_confirmed")
            if short_permission is None:
                short_permission = _account_short_value("short_permission", "short_sale_permission", "short_sale_allowed")
            if borrow_cost_status is None:
                borrow_cost_status = _account_short_value("borrow_cost_status", "borrow_terms_status", "borrow_rate_status")
            if margin_terms_confirmed is None:
                margin_terms_confirmed = _account_short_value("margin_terms_confirmed", "buying_power_confirmed")
        if borrow_available is None:
            missing.append("short_borrow_availability")
        elif borrow_available is False:
            return _sizing_result(execution_state="failed", account_id=account_id, direction=direction, currency=target_currency, entry_price=price, approved_budget=None, available_cash=None, shares=None, notional=None, resulting_cash=None, missing_inputs=[], checks=[{"check": "borrow_availability", "status": "fail", "detail": "The account cannot currently borrow the instrument for a short position."}], reason="short borrow availability is unavailable", borrow_available=False, short_permission=short_permission, borrow_cost_status=borrow_cost_status, margin_terms_confirmed=margin_terms_confirmed)
        if short_permission is not True:
            missing.append("short_sale_permission")
        if not str(borrow_cost_status or "").strip() or str(borrow_cost_status).strip().casefold() in {"unknown", "unavailable", "missing", "not_confirmed"}:
            missing.append("borrow_terms_or_cost_status")
        if margin_terms_confirmed is not True:
            missing.append("margin_terms_confirmation")
        try:
            short_limit = D(short_budget)
            margin = D(margin_available)
        except ValueError:
            return _sizing_result(execution_state="failed", account_id=account_id, direction=direction, currency=target_currency, entry_price=price, approved_budget=None, available_cash=None, shares=None, notional=None, resulting_cash=None, missing_inputs=[], checks=[], reason="short budget and margin must be finite", borrow_available=borrow_available, short_permission=short_permission, borrow_cost_status=borrow_cost_status, margin_terms_confirmed=margin_terms_confirmed)
        if short_limit is None and margin is None:
            missing.append("short_budget_or_margin")
        if margin is not None and margin < 0:
            return _sizing_result(execution_state="failed", account_id=account_id, direction=direction, currency=target_currency, entry_price=price, approved_budget=None, available_cash=None, shares=None, notional=None, resulting_cash=None, missing_inputs=[], checks=[], reason="margin_available must be nonnegative", borrow_available=borrow_available, short_permission=short_permission, borrow_cost_status=borrow_cost_status, margin_terms_confirmed=margin_terms_confirmed, margin_available=margin)
        budget_value = short_limit
        short_currency = _currency(budget_currency or margin_currency or target_currency)
        if target_currency and short_currency != target_currency and budget_value is not None:
            budget_value = _convert_amount(budget_value, short_currency, target_currency, fx_rates)
            if budget_value is None:
                missing.append(f"fx_rate:{short_currency}_{target_currency}")
        if margin is not None and target_currency:
            margin_target = _convert_amount(margin, _currency(margin_currency or target_currency), target_currency, fx_rates)
            if margin_target is None:
                missing.append(f"fx_rate:{_currency(margin_currency or target_currency)}_{target_currency}")
            elif budget_value is None:
                budget_value = margin_target
            else:
                budget_value = min(budget_value, margin_target)
        if eligible_account is False:
            missing.append("account_restriction_eligibility")
        if missing:
            return _sizing_result(execution_state="awaiting_input", account_id=account_id, direction=direction, currency=target_currency, entry_price=price, approved_budget=budget_value, available_cash=None, shares=None, notional=None, resulting_cash=None, missing_inputs=missing, checks=checks, reason="Short sizing awaits explicit borrow, permission, carry and margin inputs.", borrow_available=borrow_available, short_permission=short_permission, borrow_cost_status=borrow_cost_status, margin_terms_confirmed=margin_terms_confirmed, margin_available=margin)
        available = None
    else:
        # A model proposal is not an approved budget.  The function only sees
        # explicit ``approved_budget``/``budget`` arguments or a fully
        # reconciled, dated cash observation from the snapshot.
        raw_budget = approved_budget if approved_budget is not None else budget
        try:
            budget_value = D(raw_budget)
            direct_cash = D(available_cash)
        except ValueError:
            return _sizing_result(execution_state="failed", account_id=account_id, direction=direction, currency=target_currency, entry_price=price, approved_budget=None, available_cash=None, shares=None, notional=None, resulting_cash=None, missing_inputs=[], checks=[], reason="budget and cash must be finite")
        budget_source_currency = _currency(budget_currency or target_currency)
        if budget_value is not None:
            if budget_value < 0:
                return _sizing_result(execution_state="failed", account_id=account_id, direction=direction, currency=target_currency, entry_price=price, approved_budget=budget_value, available_cash=None, shares=None, notional=None, resulting_cash=None, missing_inputs=[], checks=[], reason="approved budget must be nonnegative")
            if target_currency:
                converted = _convert_amount(budget_value, budget_source_currency, target_currency, fx_rates)
                if converted is None:
                    missing.append(f"fx_rate:{budget_source_currency}_{target_currency}")
                else:
                    budget_value = converted
        if direct_cash is not None:
            if direct_cash < 0:
                return _sizing_result(execution_state="failed", account_id=account_id, direction=direction, currency=target_currency, entry_price=price, approved_budget=budget_value, available_cash=direct_cash, shares=None, notional=None, resulting_cash=None, missing_inputs=[], checks=[], reason="available cash must be nonnegative")
            cash_currency = _currency(available_cash_currency or target_currency)
            direct_cash = _convert_amount(direct_cash, cash_currency, target_currency, fx_rates) if target_currency else None
            if direct_cash is None and target_currency:
                missing.append(f"fx_rate:{cash_currency}_{target_currency}")
        account_cash, account_cash_missing, _ = _verified_cash_from_account(selected_account, target_currency or "UNK", fx_rates) if target_currency else (None, ["entry_currency"], None)
        if direct_cash is None and account_cash is not None:
            direct_cash = account_cash
        elif direct_cash is None and account_cash_missing:
            # An unconfirmed account is an unavailable input unless an
            # explicit approved budget was supplied independently.
            if budget_value is None:
                missing.extend(account_cash_missing)
        if budget_value is None and direct_cash is not None:
            budget_value = direct_cash
        elif budget_value is not None and direct_cash is not None:
            budget_value = min(budget_value, direct_cash)
        available = direct_cash
        if budget_value is None:
            missing.append("approved_budget_or_verified_available_cash")

    if eligible_account is False:
        missing.append("account_restriction_eligibility")
    elif account_restrictions and eligible_account is not True:
        missing.append("account_restriction_eligibility")
    if missing:
        return _sizing_result(execution_state="awaiting_input", account_id=account_id, direction=direction, currency=target_currency, entry_price=price, approved_budget=budget_value, available_cash=available, shares=None, notional=None, resulting_cash=None, missing_inputs=missing, checks=checks, reason="Sizing awaits explicit, dated account or constraint inputs.", borrow_available=borrow_available, margin_available=(margin if direction == "short" else None))
    if price is None or target_currency is None or budget_value is None:
        return _sizing_result(execution_state="awaiting_input", account_id=account_id, direction=direction, currency=target_currency, entry_price=price, approved_budget=budget_value, available_cash=available, shares=None, notional=None, resulting_cash=None, missing_inputs=["entry_price_or_currency"], checks=checks, reason="Sizing awaits an executable entry price and currency.", borrow_available=borrow_available)

    # Apply only constraints that were explicitly configured.  A configured
    # percentage constraint requires the portfolio valuation it references;
    # no total portfolio value is inferred from a cash balance.
    caps: list[tuple[str, Decimal]] = [("cash_or_budget", budget_value)]
    planned_loss_per_share: Decimal | None = None
    try:
        max_value = D(max_position_value)
        max_weight = D(max_position_weight)
        floor = D(cash_floor)
        sector_max = D(max_sector_weight)
        risk_cap = D(risk_budget)
        stop = D(stop_price)
    except ValueError:
        return _sizing_result(execution_state="failed", account_id=account_id, direction=direction, currency=target_currency, entry_price=price, approved_budget=budget_value, available_cash=available, shares=None, notional=None, resulting_cash=None, missing_inputs=[], checks=checks, reason="Configured sizing constraints must be finite", borrow_available=borrow_available)
    if max_value is not None:
        if max_value < 0:
            return _sizing_result(execution_state="failed", account_id=account_id, direction=direction, currency=target_currency, entry_price=price, approved_budget=budget_value, available_cash=available, shares=None, notional=None, resulting_cash=None, missing_inputs=[], checks=checks, reason="max_position_value must be nonnegative", borrow_available=borrow_available)
        converted = _convert_amount(max_value, _currency(max_position_value_currency or target_currency), target_currency, fx_rates)
        if converted is None:
            missing.append(f"fx_rate:{_currency(max_position_value_currency or target_currency)}_{target_currency}")
        else:
            caps.append(("max_position_value", converted))
    if max_weight is not None:
        if max_weight < 0 or max_weight > 1:
            return _sizing_result(execution_state="failed", account_id=account_id, direction=direction, currency=target_currency, entry_price=price, approved_budget=budget_value, available_cash=available, shares=None, notional=None, resulting_cash=None, missing_inputs=[], checks=checks, reason="max_position_weight must be between 0 and 1", borrow_available=borrow_available)
        try:
            total = D(total_portfolio_value)
            exposure = D(existing_exposure)
        except ValueError:
            total = exposure = None
        if total is None:
            missing.append("total_portfolio_value")
        if exposure is None:
            missing.append("existing_exposure")
        total_target = _convert_amount(total, _currency(total_portfolio_currency or target_currency), target_currency, fx_rates)
        exposure_target = _convert_amount(exposure, _currency(existing_exposure_currency or target_currency), target_currency, fx_rates)
        if total is not None and total_target is None:
            missing.append(f"fx_rate:{_currency(total_portfolio_currency or target_currency)}_{target_currency}")
        if exposure is not None and exposure_target is None:
            missing.append(f"fx_rate:{_currency(existing_exposure_currency or target_currency)}_{target_currency}")
        if total_target is not None and exposure_target is not None:
            caps.append(("max_position_weight", total_target * max_weight - exposure_target))
            checks.append({"check": "max_position_weight", "status": "configured", "detail": f"Resulting position plus existing exposure must remain at or below {quantize(max_weight)} of portfolio value."})
    if sector_max is not None:
        if sector_max < 0 or sector_max > 1:
            return _sizing_result(execution_state="failed", account_id=account_id, direction=direction, currency=target_currency, entry_price=price, approved_budget=budget_value, available_cash=available, shares=None, notional=None, resulting_cash=None, missing_inputs=[], checks=checks, reason="max_sector_weight must be between 0 and 1", borrow_available=borrow_available)
        try:
            total = D(total_portfolio_value)
            sector_exposure = D(existing_sector_exposure)
        except ValueError:
            total = sector_exposure = None
        if total is None:
            missing.append("total_portfolio_value")
        if sector_exposure is None:
            missing.append("existing_sector_exposure")
        total_target = _convert_amount(total, _currency(total_portfolio_currency or target_currency), target_currency, fx_rates)
        sector_target = _convert_amount(sector_exposure, _currency(existing_sector_exposure_currency or target_currency), target_currency, fx_rates)
        if total_target is not None and sector_target is not None:
            caps.append(("max_sector_weight", total_target * sector_max - sector_target))
    if floor is not None:
        if floor < 0 or floor > 1:
            return _sizing_result(execution_state="failed", account_id=account_id, direction=direction, currency=target_currency, entry_price=price, approved_budget=budget_value, available_cash=available, shares=None, notional=None, resulting_cash=None, missing_inputs=[], checks=checks, reason="cash_floor must be between 0 and 1", borrow_available=borrow_available)
        try:
            total = D(total_portfolio_value)
        except ValueError:
            total = None
        if total is None:
            missing.append("total_portfolio_value")
        if available is None:
            missing.append("available_cash")
        total_target = _convert_amount(total, _currency(total_portfolio_currency or target_currency), target_currency, fx_rates)
        if total_target is not None and available is not None:
            caps.append(("cash_floor", available - total_target * floor))
    if risk_cap is not None:
        if risk_cap < 0:
            return _sizing_result(execution_state="failed", account_id=account_id, direction=direction, currency=target_currency, entry_price=price, approved_budget=budget_value, available_cash=available, shares=None, notional=None, resulting_cash=None, missing_inputs=[], checks=checks, reason="risk_budget must be nonnegative", borrow_available=borrow_available)
        risk_currency = _currency(risk_budget_currency or target_currency)
        converted_risk = _convert_amount(risk_cap, risk_currency, target_currency, fx_rates)
        if converted_risk is None:
            missing.append(f"fx_rate:{risk_currency}_{target_currency}")
        else:
            risk_cap = converted_risk
        if stop is None:
            missing.append("stop_price")
        elif stop <= 0:
            return _sizing_result(execution_state="failed", account_id=account_id, direction=direction, currency=target_currency, entry_price=price, approved_budget=budget_value, available_cash=available, shares=None, notional=None, resulting_cash=None, missing_inputs=[], checks=checks, reason="stop_price must be positive", borrow_available=borrow_available)
        else:
            stop_currency = _currency(stop_price_currency or target_currency)
            if stop_currency != target_currency:
                stop = _convert_amount(stop, stop_currency, target_currency, fx_rates)
                if stop is None:
                    missing.append(f"fx_rate:{stop_currency}_{target_currency}")
            if stop is None:
                # The missing FX observation is reported below with the other
                # configured-constraint inputs.
                pass
            elif stop <= 0:
                return _sizing_result(execution_state="failed", account_id=account_id, direction=direction, currency=target_currency, entry_price=price, approved_budget=budget_value, available_cash=available, shares=None, notional=None, resulting_cash=None, missing_inputs=[], checks=checks, reason="stop_price must be positive", borrow_available=borrow_available)
            else:
                # A stop must be valid for every fill in the permitted range.
                # Long adverse risk begins at the upper bound; short adverse
                # risk begins at the lower bound while the stop must clear the
                # upper bound.
                if (direction == "long" and stop >= (lower_bound or risk_price or price)) or (direction == "short" and stop <= (notional_price or price)):
                    return _sizing_result(execution_state="awaiting_input", account_id=account_id, direction=direction, currency=target_currency, entry_price=price, notional_cap_price=notional_price, risk_entry_price=risk_price, approved_budget=budget_value, available_cash=available, shares=None, notional=None, resulting_cash=None, missing_inputs=["protective_stop_price"], checks=checks, reason="The loss-limiting stop must be below every long entry in the range or above every short entry in the range.", borrow_available=borrow_available)
                risk_per_share = (risk_price - stop) if direction == "long" else (stop - risk_price)
                if risk_per_share == 0:
                    return _sizing_result(execution_state="failed", account_id=account_id, direction=direction, currency=target_currency, entry_price=price, notional_cap_price=notional_price, risk_entry_price=risk_price, approved_budget=budget_value, available_cash=available, shares=None, notional=None, resulting_cash=None, missing_inputs=[], checks=checks, reason="stop_price must differ from the adverse-risk entry price", borrow_available=borrow_available)
                planned_loss_per_share = risk_per_share
                # Other caps are expressed as notional.  Convert the risk-unit
                # share cap back to notional before the final floor division;
                # otherwise entry price would be divided twice.
                # Keep the loss cap in share units until the final floor.  The
                # old implementation converted through the one entry price;
                # that made a short range use its upper bound for risk and
                # understated loss by construction.
                caps.append(("risk_budget", risk_cap / risk_per_share * (notional_price or price)))
    if missing:
        return _sizing_result(execution_state="awaiting_input", account_id=account_id, direction=direction, currency=target_currency, entry_price=price, approved_budget=budget_value, available_cash=available, shares=None, notional=None, resulting_cash=None, missing_inputs=missing, checks=checks, reason="Sizing awaits the valuation or exposure inputs required by configured constraints.", borrow_available=borrow_available, margin_available=(margin if direction == "short" else None))
    usable = min((cap for _, cap in caps), default=budget_value)
    if usable < 0:
        return _sizing_result(execution_state="failed", account_id=account_id, direction=direction, currency=target_currency, entry_price=price, approved_budget=budget_value, available_cash=available, shares=0, notional="0", resulting_cash=available, missing_inputs=[], checks=checks, reason="Configured exposure or cash constraint leaves no available budget.", borrow_available=borrow_available)
    # ``price`` is the conservative notional price, so this floor applies the
    # notional cap independently of the adverse-risk denominator.
    shares = int(usable / price)
    notional = price * shares
    maximum_permitted_shares = shares
    binding_cap = next((name for name, cap in caps if cap == usable), None)
    planned_loss = planned_loss_per_share * shares if planned_loss_per_share is not None else None
    if planned_loss_per_share is not None:
        checks.append({"check": "planned_loss", "status": "pass", "detail": f"{shares} shares × {quantize(planned_loss_per_share)} planned loss per share = {quantize(planned_loss)}."})
    if shares <= 0:
        return _sizing_result(execution_state="failed", account_id=account_id, direction=direction, currency=target_currency, entry_price=price, notional_cap_price=notional_price, risk_entry_price=risk_price, planned_loss_per_share=planned_loss_per_share, planned_loss=planned_loss, approved_budget=budget_value, available_cash=available, shares=0, maximum_permitted_shares=0, recommended_shares=recommended_shares, allocation_rationale=allocation_rationale, notional=notional, resulting_cash=(available - notional if available is not None and direction == "long" else None), missing_inputs=[], checks=checks, reason="The explicit budget does not cover one whole share.", borrow_available=borrow_available, short_permission=short_permission, borrow_cost_status=borrow_cost_status, margin_terms_confirmed=margin_terms_confirmed, margin_available=(margin if direction == "short" else None), binding_cap=binding_cap)
    resulting_cash = available - notional if available is not None and direction == "long" else None
    checks.append({"check": "whole_share_count", "status": "pass", "detail": f"floor({quantize(usable)} / {quantize(price)}) = {shares}."})
    # A recommendation is separate from capacity.  If a caller supplied a
    # proposed quantity, validate it against the permitted maximum; otherwise
    # leave it absent so the UI can show capacity without presenting an order.
    proposed = None
    if recommended_shares is not None:
        try:
            proposed = int(recommended_shares)
        except (TypeError, ValueError):
            proposed = None
        if proposed is None or proposed < 0 or proposed > maximum_permitted_shares:
            checks.append({"check": "recommended_shares", "status": "fail", "detail": "The proposed allocation must be a whole-share quantity within maximum permitted capacity."})
            proposed = None
    recommended_notional = price * proposed if proposed is not None else None
    recommended_planned_loss = planned_loss_per_share * proposed if proposed is not None and planned_loss_per_share is not None else None
    return _sizing_result(execution_state="ready", account_id=account_id, direction=direction, currency=target_currency, entry_price=price, notional_cap_price=notional_price, risk_entry_price=risk_price, planned_loss_per_share=planned_loss_per_share, planned_loss=planned_loss, approved_budget=budget_value, available_cash=available, shares=shares, maximum_permitted_shares=maximum_permitted_shares, recommended_shares=proposed, recommended_notional=recommended_notional, recommended_planned_loss=recommended_planned_loss, allocation_rationale=allocation_rationale, notional=notional, resulting_cash=resulting_cash, missing_inputs=[], checks=checks, reason="Deterministic whole-share sizing passed the configured cash and exposure constraints.", borrow_available=borrow_available, short_permission=short_permission, borrow_cost_status=borrow_cost_status, margin_terms_confirmed=margin_terms_confirmed, margin_available=(margin if direction == "short" else None), binding_cap=binding_cap)


def calculate_position_size(
    ticker: str | None = None,
    *,
    policy_status: str | None = None,
    recommend_requires: Sequence[str] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Public policy-aware wrapper around the pure sizing calculator.

    ``policy_status='proposed'`` is useful for an advisor preview: the same
    deterministic quantity is retained for illustration, while the result
    remains operationally ``awaiting_input`` and cannot become Recommend until
    the user accepts the policy/funding inputs.
    """
    effective = policy_status
    if effective is None:
        effective = "approved" if any(kwargs.get(key) is not None for key in ("approved_budget", "short_budget")) else "unconfigured"
    if effective not in {"approved", "proposed", "unconfigured"}:
        effective = "unconfigured"
    result = _calculate_position_size(ticker, **kwargs)
    result["policy_status"] = effective
    try:
        result["risk_budget"] = quantize(_convert_amount(D(kwargs.get("risk_budget")), _currency(kwargs.get("risk_budget_currency") or kwargs.get("currency")), _currency(kwargs.get("currency")), kwargs.get("fx_rates"))) if kwargs.get("risk_budget") is not None else None
        result["stop_price"] = quantize(D(kwargs.get("stop_price"))) if kwargs.get("stop_price") is not None else None
    except ValueError:
        result["risk_budget"] = None
        result["stop_price"] = None
    result["stop_price_currency"] = _currency(kwargs.get("stop_price_currency") or kwargs.get("currency")) if kwargs.get("stop_price") is not None else None
    if effective != "approved" and result.get("approved_budget") is not None:
        # Keep an illustrative policy envelope visible without labelling it as
        # user-approved funding.  The canonical schema carries both fields so
        # UI consumers can render the distinction directly.
        result["proposed_budget"] = result.get("approved_budget")
        result["approved_budget"] = None
    else:
        result.setdefault("proposed_budget", None)
    required = list(recommend_requires or [])
    selected_account, _ = _account_from_snapshot(kwargs.get("snapshot"), kwargs.get("account_id"))
    if selected_account is not None and result.get("direction") == "long" and result.get("available_cash") is None and result.get("shares"):
        required.append("confirm_account_currency_funding")
    if effective == "proposed":
        required.append("confirm_proposed_budget_and_funding")
    elif effective == "unconfigured" and result.get("shares"):
        required.append("configure_or_confirm_budget")
    result["recommend_requires"] = list(dict.fromkeys(required))
    if (effective != "approved" or result["recommend_requires"]) and result.get("execution_state") == "ready":
        result["execution_state"] = "awaiting_input"
        result["status"] = "awaiting_input"
        result["valid"] = False
        result["eligible"] = False
        result["reason"] = "Deterministic quantity is illustrative until the sizing policy and funding are confirmed."
        result["missing_inputs"] = list(dict.fromkeys([*result.get("missing_inputs", []), *result["recommend_requires"]]))
    return result


# Friendly aliases used by research and test callers.
calculate_sizing = calculate_position_size
size_position = calculate_position_size


def calculate_multi_candidate_sizing(
    candidates: Sequence[Mapping[str, Any]],
    *,
    budget: str | int | Decimal | None = None,
    approved_budget: str | int | Decimal | None = None,
    budget_currency: str | None = None,
    account_id: str | None = None,
    snapshot: Mapping[str, Any] | None = None,
    risk_settings: Mapping[str, Any] | None = None,
    fx_rates: Mapping[Any, Any] | None = None,
    allocation_weights: Mapping[str, Any] | None = None,
    policy_status: str | None = None,
) -> dict[str, Any]:
    """Size candidates against one shared cash/margin budget.

    Explicit candidate weights are honored.  With no weights the budget is
    split equally as a deterministic review default and any rounding residue
    remains unallocated; no candidate can spend the full shared budget.
    """
    target_currency = _currency(budget_currency)
    try:
        shared = D(approved_budget if approved_budget is not None else budget)
    except ValueError:
        shared = None
    if shared is None or target_currency is None:
        missing = ["shared_budget" if shared is None else "budget_currency"]
        return {"execution_state": "awaiting_input", "status": "awaiting_input", "shared_budget": quantize(shared), "shared_currency": target_currency, "items": [], "total_notional": "0.00000000", "unallocated_budget": quantize(shared), "missing_inputs": missing, "reason": "A multi-candidate allocation requires one explicit shared budget and currency."}
    if shared < 0:
        return {"execution_state": "failed", "status": "failed", "shared_budget": quantize(shared), "shared_currency": target_currency, "items": [], "total_notional": "0.00000000", "unallocated_budget": quantize(shared), "missing_inputs": [], "reason": "The shared budget must be nonnegative."}
    rows = [dict(item) for item in candidates if isinstance(item, Mapping)]
    if not rows:
        return {"execution_state": "awaiting_input", "status": "awaiting_input", "shared_budget": quantize(shared), "shared_currency": target_currency, "items": [], "total_notional": "0.00000000", "unallocated_budget": quantize(shared), "missing_inputs": ["candidates"], "reason": "At least one candidate is required."}
    requested_shared = shared
    selected_account, _ = _account_from_snapshot(snapshot, account_id)
    account_cash, _, _ = _verified_cash_from_account(selected_account, target_currency, fx_rates)
    if account_cash is not None and all(str(row.get("direction") or "long").lower() == "long" for row in rows):
        shared = min(shared, max(Decimal("0"), account_cash))
    explicit: dict[str, Decimal] = {}
    for row in rows:
        symbol = str(row.get("ticker") or row.get("symbol") or row.get("instrument") or "").strip().upper()
        raw = row.get("allocation_weight", row.get("weight"))
        if allocation_weights and symbol in allocation_weights:
            raw = allocation_weights[symbol]
        if raw is not None:
            try:
                value = D(raw)
            except ValueError:
                value = None
            if value is None or value < 0:
                return {"execution_state": "failed", "status": "failed", "shared_budget": quantize(shared), "shared_currency": target_currency, "items": [], "total_notional": "0.00000000", "unallocated_budget": quantize(shared), "missing_inputs": [], "reason": f"Allocation weight for {symbol or 'candidate'} is invalid."}
            explicit[symbol] = value
    explicit_total = sum(explicit.values(), Decimal("0"))
    if explicit_total > 1:
        return {"execution_state": "failed", "status": "failed", "shared_budget": quantize(shared), "shared_currency": target_currency, "items": [], "total_notional": "0.00000000", "unallocated_budget": quantize(shared), "missing_inputs": [], "reason": "Candidate allocation weights exceed the shared budget."}
    missing_weight_rows = [row for row in rows if str(row.get("ticker") or row.get("symbol") or row.get("instrument") or "").strip().upper() not in explicit]
    remainder = Decimal("1") - explicit_total
    default_weight = remainder / len(missing_weight_rows) if missing_weight_rows else Decimal("0")
    items: list[dict[str, Any]] = []
    total_native = Decimal("0")
    total_shared = Decimal("0")
    missing: list[str] = []
    for row in rows:
        symbol = str(row.get("ticker") or row.get("symbol") or row.get("instrument") or "").strip().upper() or None
        weight = explicit.get(symbol or "", default_weight)
        candidate_currency = _currency(row.get("currency") or target_currency)
        try:
            native_budget = _convert_amount(shared * weight, target_currency, candidate_currency, fx_rates)
        except (ArithmeticError, ValueError):
            native_budget = None
        direction = str(row.get("direction") or "long").lower()
        constraints = {
            key: row[key] for key in (
                "risk_budget", "risk_budget_currency", "stop_price", "stop_price_currency",
                "entry_lower_price", "entry_upper_price", "notional_cap_price", "risk_entry_price", "entry_range",
                "eligible_account", "account_restrictions", "recommend_requires",
                "max_position_value", "max_position_value_currency", "max_position_weight",
                "existing_exposure", "existing_exposure_currency", "total_portfolio_value",
                "total_portfolio_currency", "existing_sector_exposure", "existing_sector_exposure_currency",
                "max_sector_weight", "cash_floor",
                "operational_checks",
            ) if key in row
        }
        candidate_account = row.get("account_id") or account_id
        if native_budget is None:
            result = _sizing_result(execution_state="awaiting_input", account_id=account_id, direction=direction, currency=candidate_currency, entry_price=None, approved_budget=None, available_cash=None, shares=None, notional=None, resulting_cash=None, missing_inputs=[f"fx_rate:{target_currency}_{candidate_currency}"], checks=[], reason="An explicit FX observation is required for this candidate.")
        elif direction == "short":
            result = calculate_position_size(symbol, entry_price=row.get("entry_price"), currency=candidate_currency, account_id=candidate_account, snapshot=snapshot, short_budget=native_budget, budget_currency=candidate_currency, risk_settings=risk_settings, fx_rates=fx_rates, direction="short", borrow_available=row.get("borrow_available"), short_permission=row.get("short_permission"), borrow_cost_status=row.get("borrow_cost_status"), margin_terms_confirmed=row.get("margin_terms_confirmed"), margin_available=row.get("margin_available"), margin_currency=row.get("margin_currency"), policy_status=policy_status, recommended_shares=row.get("recommended_shares"), allocation_rationale=row.get("allocation_rationale"), **constraints)
        else:
            result = calculate_position_size(symbol, entry_price=row.get("entry_price"), currency=candidate_currency, account_id=candidate_account, snapshot=snapshot, approved_budget=native_budget, budget_currency=candidate_currency, risk_settings=risk_settings, fx_rates=fx_rates, direction="long", policy_status=policy_status, recommended_shares=row.get("recommended_shares"), allocation_rationale=row.get("allocation_rationale"), **constraints)
        item = dict(result)
        item.update({"ticker": symbol, "weight": quantize(weight), "budget_share": quantize(native_budget), "budget_share_currency": candidate_currency})
        items.append(item)
        if result.get("notional") is not None:
            try:
                native_notional = D(result["notional"]) or Decimal("0")
            except ValueError:
                native_notional = Decimal("0")
            total_native += native_notional
            converted_notional = _convert_amount(native_notional, candidate_currency, target_currency, fx_rates)
            if converted_notional is None:
                missing.append(f"fx_rate:{candidate_currency}_{target_currency}")
            else:
                total_shared += converted_notional
        missing.extend(str(value) for value in result.get("missing_inputs", []))
    state = "failed" if any(item.get("execution_state") == "failed" for item in items) else "awaiting_input" if any(item.get("execution_state") == "awaiting_input" for item in items) else "ready"
    if total_shared > shared:
        state = "failed"
        missing.append("shared_budget_overrun")
    return {"execution_state": state, "status": state, "shared_budget": quantize(shared), "requested_budget": quantize(requested_shared), "shared_currency": target_currency, "items": items, "total_notional": quantize(total_shared), "unallocated_budget": quantize(max(Decimal("0"), shared - total_shared)), "missing_inputs": list(dict.fromkeys(missing)), "reason": "Candidate positions were calculated against one shared budget and the account's available cash."}


calculate_candidate_allocations = calculate_multi_candidate_sizing
allocate_candidates = calculate_multi_candidate_sizing


def _bar_timestamp(bar: Mapping[str, Any]) -> datetime | None:
    value = bar.get("timestamp", bar.get("t", bar.get("date")))
    try:
        if isinstance(value, datetime):
            stamp = value
        else:
            stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def _bar_number(bar: Mapping[str, Any], *names: str) -> Decimal | None:
    for name in names:
        if name in bar:
            try:
                value = D(bar.get(name))
            except ValueError:
                return None
            if value is not None and value.is_finite():
                return value
    return None


def _normalized_bar_rows(bars: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for raw in bars or []:
        if not isinstance(raw, Mapping):
            continue
        stamp = _bar_timestamp(raw)
        close = _bar_number(raw, "close", "c")
        if stamp is None or close is None or close <= 0:
            continue
        key = stamp.isoformat().replace("+00:00", "Z")
        rows[key] = {
            "timestamp": key,
            "close": close,
            "high": _bar_number(raw, "high", "h") or close,
            "low": _bar_number(raw, "low", "l") or close,
            "volume": _bar_number(raw, "volume", "v"),
        }
    return [rows[key] for key in sorted(rows)]


def _weekly_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[int, int], list[Mapping[str, Any]]] = {}
    for row in rows:
        stamp = _bar_timestamp(row)
        if stamp is not None:
            iso = stamp.isocalendar()
            grouped.setdefault((int(iso.year), int(iso.week)), []).append(row)
    output: list[dict[str, Any]] = []
    for values in grouped.values():
        ordered = sorted(values, key=lambda item: str(item.get("timestamp", "")))
        closes = [_bar_number(item, "close") for item in ordered]
        highs = [_bar_number(item, "high") for item in ordered]
        lows = [_bar_number(item, "low") for item in ordered]
        volumes = [_bar_number(item, "volume") for item in ordered]
        close = closes[-1] if closes else None
        if close is None:
            continue
        output.append({
            "timestamp": ordered[-1]["timestamp"],
            "close": close,
            "high": max((value for value in highs if value is not None), default=close),
            "low": min((value for value in lows if value is not None), default=close),
            "volume": sum((value for value in volumes if value is not None), Decimal("0")) if any(value is not None for value in volumes) else None,
        })
    return output


def _decimal_sma(values: Sequence[Decimal], window: int) -> Decimal | None:
    if len(values) < window:
        return None
    return sum(values[-window:], Decimal("0")) / Decimal(window)


def _decimal_ema_series(values: Sequence[Decimal], window: int) -> list[Decimal | None]:
    output: list[Decimal | None] = [None] * len(values)
    if len(values) < window:
        return output
    current = sum(values[:window], Decimal("0")) / Decimal(window)
    output[window - 1] = current
    alpha = Decimal("2") / Decimal(window + 1)
    for index in range(window, len(values)):
        current = (values[index] - current) * alpha + current
        output[index] = current
    return output


def _decimal_rsi(values: Sequence[Decimal], window: int = 14) -> Decimal | None:
    if len(values) <= window:
        return None
    gains: list[Decimal] = []
    losses: list[Decimal] = []
    for previous, current in zip(values, values[1:]):
        delta = current - previous
        gains.append(max(delta, Decimal("0")))
        losses.append(max(-delta, Decimal("0")))
    average_gain = sum(gains[:window], Decimal("0")) / Decimal(window)
    average_loss = sum(losses[:window], Decimal("0")) / Decimal(window)
    for gain, loss in zip(gains[window:], losses[window:]):
        average_gain = (average_gain * Decimal(window - 1) + gain) / Decimal(window)
        average_loss = (average_loss * Decimal(window - 1) + loss) / Decimal(window)
    if average_loss == 0:
        return Decimal("100") if average_gain > 0 else Decimal("50")
    relative_strength = average_gain / average_loss
    return Decimal("100") - Decimal("100") / (Decimal("1") + relative_strength)


def _decimal_atr(rows: Sequence[Mapping[str, Any]], window: int = 14) -> Decimal | None:
    if len(rows) <= window:
        return None
    true_ranges: list[Decimal] = []
    previous_close: Decimal | None = None
    for row in rows:
        high = _bar_number(row, "high")
        low = _bar_number(row, "low")
        close = _bar_number(row, "close")
        if high is None or low is None or close is None:
            previous_close = close
            continue
        true_range = high - low
        if previous_close is not None:
            true_range = max(true_range, abs(high - previous_close), abs(low - previous_close))
        true_ranges.append(true_range)
        previous_close = close
    if len(true_ranges) < window:
        return None
    current = sum(true_ranges[:window], Decimal("0")) / Decimal(window)
    for value in true_ranges[window:]:
        current = (current * Decimal(window - 1) + value) / Decimal(window)
    return current


def _indicator_snapshot(rows: Sequence[Mapping[str, Any]], frequency: str, source_refs: list[str]) -> dict[str, Any]:
    closes = [value for value in (_bar_number(row, "close") for row in rows) if value is not None]
    ema12 = _decimal_ema_series(closes, 12)
    ema26 = _decimal_ema_series(closes, 26)
    macd_values: list[Decimal] = [left - right for left, right in zip(ema12, ema26) if left is not None and right is not None]
    signal_values = _decimal_ema_series(macd_values, 9)
    macd_line = macd_values[-1] if macd_values else None
    macd_signal = signal_values[-1] if signal_values else None
    macd_histogram = macd_line - macd_signal if macd_line is not None and macd_signal is not None else None
    latest = closes[-1] if closes else None
    indicators: dict[str, Any] = {
        "sma": {str(window): quantize(_decimal_sma(closes, window)) for window in (20, 50, 200)},
        "ema": {str(window): quantize((ema12 if window == 12 else ema26)[-1] if (ema12 if window == 12 else ema26) else None) for window in (12, 26)},
        "rsi14": quantize(_decimal_rsi(closes, 14)),
        "macd": {"line": quantize(macd_line), "signal": quantize(macd_signal), "histogram": quantize(macd_histogram)},
        "atr14": quantize(_decimal_atr(rows, 14)),
    }
    missing: list[str] = []
    for window in (20, 50, 200):
        if indicators["sma"][str(window)] is None:
            missing.append(f"{frequency}.sma{window}")
    if indicators["rsi14"] is None:
        missing.append(f"{frequency}.rsi14")
    if indicators["macd"]["histogram"] is None:
        missing.append(f"{frequency}.macd")
    if indicators["atr14"] is None:
        missing.append(f"{frequency}.atr14")
    trend = "unknown"
    sma20 = _decimal_sma(closes, 20)
    if latest is not None and sma20 is not None:
        trend = "above_sma20" if latest >= sma20 else "below_sma20"
    return {
        "frequency": frequency,
        "as_of": rows[-1].get("timestamp") if rows else None,
        "observations": len(closes),
        "latest_close": quantize(latest),
        "indicators": indicators,
        "trend": trend,
        "source_refs": source_refs,
        "missing_inputs": missing,
    }


def calculate_technical_indicators(
    daily_bars: Sequence[Mapping[str, Any]] | None,
    *,
    weekly_bars: Sequence[Mapping[str, Any]] | None = None,
    source_refs: Sequence[str] | None = None,
    frequencies: Sequence[str] = ("daily", "weekly"),
) -> dict[str, Any]:
    """Return a compact code-calculated indicator packet at multiple frequencies."""
    refs = list(dict.fromkeys(str(item) for item in (source_refs or []) if str(item)))
    daily = _normalized_bar_rows(daily_bars)
    result: dict[str, Any] = {"code_version": "technical-indicators.v1", "method": "decimal_ohlcv_indicators", "source_refs": refs, "frequencies": {}}
    requested = [str(item).strip().lower() for item in frequencies]
    for frequency in requested:
        if frequency == "daily":
            rows = daily
        elif frequency == "weekly":
            rows = _normalized_bar_rows(weekly_bars) if weekly_bars is not None else _weekly_rows(daily)
        else:
            result["frequencies"][frequency] = {"frequency": frequency, "observations": 0, "indicators": {}, "missing_inputs": [f"unsupported_frequency:{frequency}"], "source_refs": refs}
            continue
        result["frequencies"][frequency] = _indicator_snapshot(rows, frequency, refs)
    # ``daily``/``weekly`` keys are convenient for packet consumers while the
    # nested mapping remains a stable versioned representation.
    for key in ("daily", "weekly"):
        if key in result["frequencies"]:
            result[key] = result["frequencies"][key]
    return result


compute_technical_indicators = calculate_technical_indicators
technical_indicators = calculate_technical_indicators
compute_technicals = calculate_technical_indicators


def snapshot_values_available(snapshot: dict | None) -> bool:
    """Return whether a frozen portfolio has enough verified values to size.

    A cost basis is a historical accounting input and cannot stand in for a
    current market value.  Sizing is therefore enabled only for a complete,
    reconciled snapshot with dated confirmed observations.  Position rows
    must carry an explicitly verified market value and all values must already
    be expressed in one currency; the backend has no implicit FX feed.
    """
    if not isinstance(snapshot, dict):
        return False
    accounts = snapshot.get("accounts")
    if not isinstance(accounts, list) or not accounts:
        return False
    if any(not isinstance(account, dict) for account in accounts):
        return False
    currencies: set[str] = set()
    has_balance = False
    for account in accounts:
        if account.get("reconciliation_status") not in {"reconciled", "confirmed"}:
            return False
        balances = account.get("balances")
        if not isinstance(balances, list) or not balances:
            return False
        account_has_balance = False
        for balance in balances:
            if not isinstance(balance, dict):
                return False
            currency = str(balance.get("currency") or "").strip().upper()
            try:
                amount = D(balance.get("amount"))
            except ValueError:
                amount = None
            if not currency or currency == "UNK" or amount is None:
                return False
            if balance.get("status") not in {"confirmed", "validated", "reconciled"}:
                return False
            if not balance.get("observed_at"):
                return False
            currencies.add(currency)
            account_has_balance = True
        has_balance = has_balance or account_has_balance
    if not has_balance:
        return False
    positions = snapshot.get("positions")
    if not isinstance(positions, list):
        return False
    for position in positions:
        if not isinstance(position, dict):
            return False
        try:
            quantity = D(position.get("quantity"))
            market_value = D(position.get("market_value"))
        except ValueError:
            return False
        if quantity is None or market_value is None:
            return False
        if not position.get("observed_at") or not position.get("source_id"):
            return False
        if position.get("status") not in {"confirmed", "validated", "reconciled"}:
            return False
        value_currency = str(position.get("market_value_currency") or position.get("currency") or "").strip().upper()
        if not value_currency or value_currency == "UNK":
            return False
        currencies.add(value_currency)
    # There is no implicit conversion service in this local backend.  Mixed
    # currencies remain a deliberate defer until an explicit conversion
    # observation is provided.
    if len(currencies) != 1:
        return False
    return True


def risk_checks(
    *,
    positions: list[dict],
    risk_settings: dict,
    account_values_available: bool,
    proposal: dict | None = None,
    accounts: list[dict] | None = None,
) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []
    if not account_values_available:
        checks.append({"check": "account_snapshot", "status": "defer", "detail": "Account values are unavailable; sizing is deferred."})
    max_position = risk_settings.get("max_position_weight")
    if max_position is None:
        checks.append({"check": "max_position_weight", "status": "unknown", "detail": "No user-configured maximum position weight."})
    else:
        checks.append({"check": "max_position_weight", "status": "configured", "detail": f"Configured limit {max_position}; position valuation requires dated prices."})
    max_sector = risk_settings.get("max_sector_weight")
    checks.append({"check": "max_sector_weight", "status": "unknown" if max_sector is None else "configured", "detail": "No sector limit configured." if max_sector is None else f"Configured limit {max_sector}."})
    cash_floor = risk_settings.get("cash_floor")
    checks.append({"check": "cash_floor", "status": "unknown" if cash_floor is None else "configured", "detail": "No cash floor configured." if cash_floor is None else f"Configured floor {cash_floor}."})
    if not positions:
        checks.append({"check": "existing_positions", "status": "unknown", "detail": "No position observations are available."})
    if proposal is None:
        checks.append({"check": "allocation_proposal", "status": "defer", "detail": "No structured allocation proposal was supplied; prose cannot determine sizing."})
        return checks

    account_id = proposal.get("account_id")
    if account_id and accounts is not None:
        known_accounts = {str(item.get("id")) for item in accounts if isinstance(item, dict)}
        if str(account_id) not in known_accounts:
            checks.append({"check": "proposal_account", "status": "defer", "detail": "Structured proposal names an account that is absent from the frozen portfolio snapshot."})
    restrictions = risk_settings.get("account_restrictions") or []
    if restrictions:
        checks.append({"check": "account_restrictions", "status": "defer", "detail": "Account restrictions require an explicit deterministic eligibility mapping before sizing."})

    # Parse only structured fractions. Never infer weights by reading model
    # prose. An invalid proposal is an explicit defer, not a zero.
    parsed: dict[str, Decimal] = {}
    for field in ("target_position_weight", "sector_weight_after", "cash_weight_after"):
        value = proposal.get(field)
        if value is None:
            checks.append({"check": field, "status": "defer", "detail": "Structured proposal field is missing."})
            continue
        try:
            parsed[field] = D(value)  # type: ignore[assignment]
        except ValueError:
            checks.append({"check": field, "status": "defer", "detail": "Structured proposal field is not a finite fraction."})
            continue
        if parsed[field] is None or parsed[field] < 0 or parsed[field] > 1:
            checks.append({"check": field, "status": "defer", "detail": "Structured proposal fraction must be between 0 and 1."})
    max_position_value = risk_settings.get("max_position_weight")
    if max_position_value is not None and "target_position_weight" in parsed:
        maximum = D(max_position_value)
        if maximum is None or parsed["target_position_weight"] > maximum:
            checks.append({"check": "max_position_weight", "status": "fail", "detail": f"Proposed position weight {quantize(parsed['target_position_weight'])} exceeds configured hard limit {max_position_value}."})
        else:
            checks.append({"check": "max_position_weight", "status": "pass", "detail": "Structured proposed position weight is within the configured limit."})
    elif "target_position_weight" in parsed:
        checks.append({"check": "max_position_weight", "status": "unknown", "detail": "No maximum position weight is configured."})
    max_sector_value = risk_settings.get("max_sector_weight")
    if max_sector_value is not None and "sector_weight_after" in parsed:
        maximum = D(max_sector_value)
        if maximum is None or parsed["sector_weight_after"] > maximum:
            checks.append({"check": "max_sector_weight", "status": "fail", "detail": f"Proposed sector weight {quantize(parsed['sector_weight_after'])} exceeds configured hard limit {max_sector_value}."})
        else:
            checks.append({"check": "max_sector_weight", "status": "pass", "detail": "Structured proposed sector weight is within the configured limit."})
    elif "sector_weight_after" in parsed:
        checks.append({"check": "max_sector_weight", "status": "unknown", "detail": "No maximum sector weight is configured."})
    cash_floor_value = risk_settings.get("cash_floor")
    if cash_floor_value is not None and "cash_weight_after" in parsed:
        floor = D(cash_floor_value)
        if floor is None or parsed["cash_weight_after"] < floor:
            checks.append({"check": "cash_floor", "status": "fail", "detail": f"Proposed cash weight {quantize(parsed['cash_weight_after'])} is below configured hard floor {cash_floor_value}."})
        else:
            checks.append({"check": "cash_floor", "status": "pass", "detail": "Structured proposed cash weight meets the configured floor."})
    elif "cash_weight_after" in parsed:
        checks.append({"check": "cash_floor", "status": "unknown", "detail": "No cash floor is configured."})
    if "sector_weight_after" in parsed and "target_position_weight" in parsed and parsed["sector_weight_after"] < parsed["target_position_weight"]:
        checks.append({"check": "sector_position_consistency", "status": "fail", "detail": "Proposed sector weight cannot be below the proposed position weight."})
    if "sector_weight_after" in parsed and "cash_weight_after" in parsed and parsed["sector_weight_after"] + parsed["cash_weight_after"] > 1:
        checks.append({"check": "portfolio_weight_consistency", "status": "fail", "detail": "Proposed sector and cash weights exceed the available portfolio fraction."})
    return checks
