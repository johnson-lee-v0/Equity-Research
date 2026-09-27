"""Source-bound comparable forecasts. No provider-authored target is executable.

P/S is an equity multiple; EV/EBITDA uses an explicit enterprise-to-equity
bridge. NAV/book values remain different declared accounting bases. Peer
ratios are computed from individually validated operands, never a naked ratio.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from statistics import median
from typing import Any, Mapping

from .calculations import quantize
from .freshness import evaluate_fact_freshness

METHODS = {"ps_multiple", "ev_ebitda", "nav_multiple"}
METRICS = {"ps_multiple": "revenue", "ev_ebitda": "ebitda", "nav_multiple": "nav"}
LABELS = {"ps_multiple": "P/S", "ev_ebitda": "EV/EBITDA", "nav_multiple": "P/NAV"}


def calculate_comparable(name: str, params: Mapping[str, Any], **kwargs) -> dict:
    """Malformed numeric proposals fail closed instead of crashing a run."""
    try:
        return _calculate_comparable(name, params, **kwargs)
    except (ArithmeticError, ValueError, TypeError) as exc:
        from .valuation import _base_method
        return _base_method(name, reasons=[f"Comparable arithmetic could not be represented safely ({type(exc).__name__}); review the input magnitudes, dates and units."], source_refs=params.get("source_refs") or [])


def _calculate_comparable(name: str, params: Mapping[str, Any], *, facts: Mapping[str, Mapping],
                         sources: Mapping[str, Mapping] | None, issuer: str | None,
                         as_of: str | None, currency: str | None, observed_peers=()) -> dict:
    from .valuation import _base_method, _fact_matches, _number, _refs, source_has_primary_coverage
    from .fact_validation import unit_dimension

    method = _base_method(name, source_refs=params.get("source_refs") or [])
    method.update(as_of=as_of, inputs=list(params.get("_input_rows") or []))
    rows = {row.get("key"): row for row in method["inputs"]}
    if not currency:
        denominations = {str(row["currency"]).upper() for row in rows.values() if row.get("currency")}
        currency = next(iter(denominations)) if len(denominations) == 1 else None
    reasons: list[str] = []
    if not currency:
        reasons.append("A single output currency is required for comparable valuation.")
    values: dict[str, Decimal] = {}
    baseline_key = {"ps_multiple": "baseline_revenue", "ev_ebitda": "baseline_ebitda", "nav_multiple": "baseline_nav"}[name]
    metric = METRICS[name]
    derived_ebitda = name == "ev_ebitda" and baseline_key not in rows and all(key in rows for key in ("baseline_operating_income", "baseline_da"))
    nav_basis = params.get("nav_basis")
    if name == "nav_multiple":
        if nav_basis not in {"appraised_nav", "reported_nav", "book_equity", "tangible_book_equity"}:
            reasons.append("NAV requires a declared basis: reported/appraised NAV, book equity, or tangible book equity; these are not interchangeable.")
        metric = {"book_equity": "book equity", "tangible_book_equity": "tangible book equity"}.get(nav_basis, "nav")
    required = {baseline_key: (metric, "currency"), "diluted_shares": ("shares", "shares")}
    if derived_ebitda:
        required.pop(baseline_key)
        required.update(baseline_operating_income=("operating income", "currency"), baseline_da=("depreciation and amortization", "currency"))
    if name == "ev_ebitda":
        required.update({"excess_cash": ("cash", "currency"), "debt": ("debt", "currency"),
                         "preferred_claims": ("preferred", "currency"), "minority_interest": ("minority", "currency")})
    extra_keys = set(rows) - set(required)
    if extra_keys:
        reasons.append("Unused typed inputs cannot certify this method: " + ", ".join(sorted(str(key) for key in extra_keys)))
    declared_ids = set(_refs(params.get("fact_claim_ids")))
    used_ids = {identifier for row in rows.values() for identifier in _refs(row.get("fact_claim_ids"))}
    if declared_ids - used_ids:
        reasons.append("Method-level fact references must bind to explicit calculation inputs.")

    def fact_input(row: Mapping, expected_metric: str, dimension: str, owner: str | None, *, at: str | None = as_of) -> str | None:
        ids = _refs(row.get("fact_claim_ids"))
        if row.get("kind") != "fact" or len(ids) != 1:
            return "requires exactly one source-bound fact"
        fact = facts.get(ids[0])
        if not fact:
            return "does not name a validated fact in this assessment"
        if name == "nav_multiple" and expected_metric == "nav" and str(fact.get("metric") or "").casefold().replace("_", " ") not in {"nav", "net asset value", "nav equity", "equity nav", "net equity asset value"}:
            return "requires equity NAV after liabilities; gross assets or a per-unit underlying value are not total equity NAV"
        if not row.get("period") or not row.get("basis") or row.get("scale") not in {None, "", "units", "unit", "1"}:
            return "requires an explicit period/basis in unscaled units"
        ok, reason = _fact_matches(fact, value=row.get("value"), expected_metric=expected_metric,
            expected_dimension=dimension, currency=currency if dimension != "shares" else None,
            period=row.get("period"), basis=row.get("basis"), source_refs=_refs(row.get("source_refs")), issuer=owner)
        if not ok:
            return reason
        source = (sources or {}).get(str(fact.get("source_ref")))
        if not source or not source.get("content", source.get("original_content")) or not source_has_primary_coverage(source):
            return "requires archived primary evidence"
        # Asset-equity NAV is a reported valuation operand, not a daily fund
        # NAV/market quote. Apply the quarterly reporting policy explicitly;
        # the original fact dates and metadata remain unchanged in receipts.
        freshness_fact = ({**fact, "metric": "equity asset valuation", "period": "quarterly " + str(fact.get("period") or "")}
                          if name == "nav_multiple" and expected_metric == "nav" else fact)
        if at and evaluate_fact_freshness(freshness_fact, source, as_of=at).get("status") != "fresh":
            return "is stale or has unknown freshness for this valuation date"
        method["source_refs"] = list(dict.fromkeys([*method["source_refs"], fact["source_ref"]]))
        return None

    for key, (expected_metric, dimension) in required.items():
        row = rows.get(key) or {}
        number = _number(row.get("value"))
        # Bridge assumptions must be explicit, never inferred zero liabilities.
        assumed_bridge = key in {"excess_cash", "debt", "preferred_claims", "minority_interest", "diluted_shares"} and row.get("kind") == "assumption"
        reason = (None if str(row.get("rationale") or "").strip() else "assumption requires its rationale") if assumed_bridge else fact_input(row, expected_metric, dimension, issuer)
        declared_dimension = unit_dimension(row.get("unit"))
        if not declared_dimension or declared_dimension[0] != dimension or dimension == "currency" and str(declared_dimension[1] or "").upper() != currency:
            reason = "requires matching units and currency for this bridge component"
        if reason or number is None or number < 0 or key in {baseline_key, "diluted_shares"} and number <= 0:
            reasons.append(f"{key}: {reason or 'requires a positive baseline/shares or nonnegative bridge value'}.")
        elif row.get("currency") not in {None, currency} or row.get("scale") not in {None, "", "units", "unit", "1"}:
            reasons.append(f"{key}: use the output currency and unscaled units.")
        else:
            values[key] = number
    if params.get("_input_conflicts"):
        reasons.append("Conflicting raw and typed valuation inputs: " + ", ".join(params["_input_conflicts"]))
    if derived_ebitda and all(key in values for key in ("baseline_operating_income", "baseline_da")):
        left, right = rows["baseline_operating_income"], rows["baseline_da"]
        left_fact = facts.get(_refs(left.get("fact_claim_ids"))[0], {})
        right_fact = facts.get(_refs(right.get("fact_claim_ids"))[0], {})
        if not left_fact.get("period_start") or any(left_fact.get(key) != right_fact.get(key) for key in ("period_start", "period_end", "currency")):
            reasons.append("Operating income and D&A require exactly matching reporting periods and currency.")
        else:
            values[baseline_key] = values["baseline_operating_income"] + values["baseline_da"]
            rows[baseline_key] = {"period": left["period"]}
    try:
        months = int(params.get("horizon_months") or 12)
        years = _number(params.get("forecast_years")) if params.get("forecast_years") is not None else Decimal(months) / 12
        if not 1 <= months <= 120 or years is None or years <= 0 or years > 10:
            raise ValueError
    except (ValueError, TypeError):
        reasons.append("A positive forecast span of at most ten years and a 1–120 month holding horizon are required.")
        months, years = 12, Decimal(1)
    if params.get("forecast_years") is not None and not params.get("forecast_span_rationale"):
        reasons.append("Explain the dated baseline-to-forecast span separately from the holding horizon.")
    if not params.get("period"):
        reasons.append("The forecast fiscal period is required.")
    peers = []
    rejected = []
    for peer in params.get("comparables") or []:
        peer = dict(peer)
        identity = str(peer.get("ticker") or "").strip()
        issue = None
        if not identity or identity == issuer or peer.get("metric") != LABELS[name] or not str(peer.get("rationale") or "").strip():
            issue = "A different named issuer, matching multiple and comparability rationale are required."
        if name == "nav_multiple" and peer.get("nav_basis") != nav_basis:
            issue = "The peer's NAV/book basis differs from the subject."
        peer_date = str(peer.get("as_of") or "")
        try:
            if not as_of or not 0 <= (date.fromisoformat(as_of[:10]) - date.fromisoformat(peer_date[:10])).days <= 90:
                raise ValueError
        except ValueError:
            issue = "Peer observation date must be within 90 days, without future information."
        # Numerator and denominator are compatible totals, or per-share NAV.
        numerator_metric = "enterprise value" if name == "ev_ebitda" else "market capitalization"
        dimensions = ("currency", "currency")
        operands = []
        for key, expected_metric, dimension in (("numerator", numerator_metric, dimensions[0]), ("denominator", metric, dimensions[1])):
            row = peer.get(key) or {}
            issue = issue or fact_input(row, expected_metric, dimension, identity, at=peer_date)
            operands.append(_number(row.get("value")))
        if peer.get("basis") not in {"trailing", "forward"}:
            issue = issue or "Peer basis must explicitly be trailing or forward."
        if any(value is None or value <= 0 for value in operands):
            issue = issue or "Both peer operands must be positive."
        if issue:
            rejected.append({"ticker": identity, "reason": issue})
        else:
            peers.append({"ticker": identity, "as_of": peer_date, "metric": LABELS[name], "basis": peer["basis"],
                "multiple": quantize(operands[0] / operands[1]), "rationale": peer["rationale"],
                "numerator": peer["numerator"], "denominator": peer["denominator"],
                "source_refs": list(dict.fromkeys([*_refs(peer["numerator"].get("source_refs")), *_refs(peer["denominator"].get("source_refs"))]))})
    for peer in observed_peers:
        if peer.get("metric") == LABELS[name] and (name != "nav_multiple" or peer.get("nav_basis") == nav_basis):
            if not any(saved["ticker"] == peer["ticker"] and saved["basis"] == peer["basis"] for saved in peers):
                peers.append(dict(peer))
                method["source_refs"] = list(dict.fromkeys([*method["source_refs"], *peer.get("source_refs", [])]))
    # Keep differently based peers in separate groups. Never average forward
    # and trailing multiples or treat the selected exit assumption as observed.
    def peer_group(peer):
        return peer["basis"] + (" · " + str(peer.get("ebitda_basis") or (peer.get("denominator") or {}).get("basis") or "EBITDA basis not recorded") if name == "ev_ebitda" else "")
    peer_groups = list(dict.fromkeys(peer_group(peer) for peer in peers))
    peer_medians = {basis: quantize(median([Decimal(peer["multiple"]) for peer in peers if peer_group(peer) == basis])) for basis in peer_groups}
    comparison = {"status": "available" if peers else "unavailable", "metric": LABELS[name], "peers": peers,
        "medians": peer_medians, "rejected": rejected,
        "coverage_note": "Peer observations inform, but do not set, the scenario exit assumptions. Different business mixes, leverage, margins and accounting bases limit comparability." if peers else "No validated peer observations were retained. Exit multiples are research assumptions, not claimed comparable-market observations."}
    method["intermediate_results"] = {"comparable_analysis": comparison}
    scenarios = params.get("scenarios") or {}
    for case in ("bear", "base", "bull"):
        row = scenarios.get(case) or {}
        if (_number(row.get("growth_rate")) is None or _number(row.get("growth_rate")) <= -1
                or _number(row.get("exit_multiple")) is None or _number(row.get("exit_multiple")) <= 0
                or not row.get("growth_rationale") or not row.get("multiple_rationale")):
            reasons.append(f"{case}: annual growth, positive exit multiple and both rationales are required.")
    if reasons:
        method.update(reasons=list(dict.fromkeys(reasons)), missing_inputs=list(dict.fromkeys(reasons)))
        return method
    baseline = values[baseline_key]
    shares = values.get("diluted_shares", Decimal(1))
    bridge = values.get("excess_cash", Decimal(0)) - values.get("debt", Decimal(0)) - values.get("preferred_claims", Decimal(0)) - values.get("minority_interest", Decimal(0))
    # Forecast capital structure assumptions cannot establish today's value.
    # Requiring the actual bound operands also avoids silently selecting a
    # different reporting date from other facts in the research packet.
    today_missing = [key for key in required if key != baseline_key
                     and rows.get(key, {}).get("kind") == "assumption"]
    formula = {"ps_multiple": "baseline revenue × (1 + annual growth)^forecast years × P/S ÷ diluted shares",
        "ev_ebitda": "(baseline EBITDA × (1 + annual growth)^forecast years × EV/EBITDA + cash − debt − preferred − minority) ÷ diluted shares",
        "nav_multiple": "baseline NAV/book equity × (1 + annual growth)^forecast years × P/NAV (or P/book) ÷ diluted shares"}[name]
    calculations, outputs, today = {}, {}, {}
    for case in ("bear", "base", "bull"):
        row = scenarios[case]
        growth, multiple = _number(row["growth_rate"]), _number(row["exit_multiple"])
        forecast = baseline * (Decimal(1) + growth) ** years
        price = (forecast * multiple + bridge) / shares
        current = (baseline * multiple + bridge) / shares
        if price <= 0:
            method.update(reasons=[f"{case}: equity value after the bridge is not positive."], missing_inputs=["Positive equity value"])
            return method
        outputs[case] = quantize(price)
        if current > 0 and not today_missing:
            today[case] = quantize(current)
        calculations[case] = {"formula": formula, "inputs": [*method["inputs"],
            {"key": "annual_metric_growth", "value": str(growth), "unit": "fraction", "kind": "assumption", "rationale": row["growth_rationale"]},
            {"key": "exit_multiple", "value": str(multiple), "unit": "multiple", "kind": "assumption", "rationale": row["multiple_rationale"]}],
            "steps": [f"Start with reported {metric}: {quantize(baseline)} ({rows[baseline_key]['period']}).",
                f"Apply {quantize(growth * 100)}% annual growth for {quantize(years)} years: {quantize(forecast)}.",
                f"Apply {quantize(multiple)}× {LABELS[name]}" + (f", add net bridge {quantize(bridge)} and divide by {quantize(shares)} shares." if name != "nav_multiple" else f", divide by {quantize(shares)} shares.")],
            "intermediate_results": {"baseline_metric": quantize(baseline), "annual_metric_growth": quantize(growth),
                "forecast_metric": quantize(forecast), "exit_multiple": quantize(multiple), "equity_bridge": quantize(bridge), "diluted_shares": quantize(shares),
                "horizon_months": months, "forecast_years": quantize(years), "forecast_span_rationale": params.get("forecast_span_rationale") or "The forecast span equals the holding horizon.", "nav_basis": nav_basis}, "output_price": outputs[case]}
    if derived_ebitda:
        note = "EBITDA proxy = reported operating income + reported cash-flow depreciation, depletion and amortization for the same period. This may differ from management's adjusted EBITDA; review non-operating D&A, leases and peer definitions."
        for calculation in calculations.values():
            calculation["steps"].insert(0, note)
            calculation["intermediate_results"]["ebitda_basis"] = note
    method.update(status="complete", supported=True, formula=formula, output_prices=outputs, scenario_calculations=calculations,
        steps=calculations["base"]["steps"], intermediate_results={**calculations["base"]["intermediate_results"], "comparable_analysis": comparison,
            "implied_today": {"status": "available" if today else "unavailable", "as_of": as_of, "scenarios": today, "currency": currency,
                "period_end": rows[baseline_key]["period"], "source_refs": method["source_refs"], "earnings_basis": f"Reported {metric} baseline ({nav_basis})" if nav_basis else f"Reported {metric} baseline",
                "formula": f"Reported {metric} × scenario multiple, with the reported equity/share bridge; no growth applied.",
                "missing_inputs": [f"Reported current {key.replace('_', ' ')}; a forecast assumption was supplied." for key in today_missing],
                "coverage_note": ("Today's value is unavailable because the equity/share bridge includes forecast assumptions. Supply source-bound current operands separately from the future target."
                                  if today_missing else "Value on the dated reported baseline using reported equity/share operands. Not a market quote or a discounted future target.")}})
    return method
