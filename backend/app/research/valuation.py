"""Auditable, Decimal-only valuation and payoff services.

Provider output may propose assumptions, but this module owns every numeric
output.  It intentionally returns plain dictionaries so it can be used by the
projection layer without allowing a provider-authored target to become a
canonical price by itself.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
from urllib.parse import urlparse
from typing import Any, Mapping, Sequence

from .calculations import D, quantize
from .fact_validation import unit_dimension
from .freshness import evaluate_fact_freshness


VALUATION_CODE_VERSION = "valuation.v6"
_ZERO = Decimal("0")
_ONE = Decimal("1")


def _number(value: Any) -> Decimal | None:
    try:
        return D(value)
    except (TypeError, ValueError, InvalidOperation):
        return None


def _refs(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, Sequence):
        return []
    result: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _as_of(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def source_has_primary_coverage(source: Mapping[str, Any] | None) -> bool:
    """Return whether a retained source can support a material baseline.

    A source label such as ``filing`` or a title containing ``10-K`` is only
    a classification. Primary coverage must come from the repository's
    explicit policy projection, a trusted regulator/venue host, or a
    provider-verification marker. Generic URL/text provenance remains
    contextual and cannot make a fabricated financial number executable.
    """
    if not isinstance(source, Mapping):
        return False
    nested: dict[str, Any] = {}
    for key in ("provenance", "metadata", "freshness_policy", "source_policy"):
        value = source.get(key)
        if isinstance(value, Mapping):
            nested.update(value)
    explicit = source.get("primary_evidence")
    if explicit is None:
        explicit = nested.get("primary_evidence")
    if explicit is False:
        return False
    coverage = str(source.get("primary_coverage") or source.get("coverage") or nested.get("primary_coverage") or nested.get("coverage") or "").strip().casefold()
    if explicit is True and coverage not in {"unknown", "unknown_external_document", "secondary_opinion", "user_owned", "imported content"}:
        return True
    if bool(source.get("provider_verified") or source.get("verified_primary") or nested.get("provider_verified") or nested.get("verified_primary")):
        return True
    source_type = str(source.get("source_type") or source.get("kind") or "").strip().casefold()
    if source_type in {"market_data", "market_bars", "derived_weekly_market_bars", "asset_identity", "alpaca_asset_identity", "exchange_identity"}:
        return True
    url = str(source.get("url") or source.get("source_url") or "").strip()
    try:
        host = (urlparse(url).hostname or "").casefold()
    except ValueError:
        host = ""
    trusted = bool(
        host == "sec.gov"
        or host.endswith(".sec.gov")
        or host.endswith(".gov")
        or host.endswith(".gc.ca")
        or host in {"bankofcanada.ca", "federalreserve.gov", "treasury.gov", "nyse.com", "nasdaq.com"}
        or host.endswith(".bankofcanada.ca")
        or host.endswith(".federalreserve.gov")
        or host.endswith(".treasury.gov")
        or host.endswith(".nyse.com")
        or host.endswith(".nasdaq.com")
    )
    return trusted and source_type in {"filing", "sec", "sec_filing", "sec_submission", "issuer", "issuer_release", "submission", "document", "web"}


def _method_input(
    key: str,
    value: Any,
    *,
    unit: str | None = None,
    currency: str | None = None,
    period: str | None = None,
    scale: str | None = None,
    basis: str | None = None,
    statement_type: str | None = None,
    kind: str = "assumption",
    fact_claim_ids: Sequence[Any] = (),
    source_refs: Sequence[Any] = (),
    rationale: str = "",
) -> dict[str, Any]:
    numeric = _number(value)
    return {
        "key": key,
        "value": quantize(numeric) if numeric is not None else (str(value) if value is not None else None),
        "unit": unit,
        "currency": currency,
        "period": period,
        "scale": scale,
        "basis": basis,
        "statement_type": statement_type,
        "kind": kind if kind in {"fact", "assumption"} else "assumption",
        "fact_claim_ids": _refs(fact_claim_ids),
        "source_refs": _refs(source_refs),
        "rationale": rationale or "",
    }


def _base_method(name: str, *, status: str = "unavailable", reasons: Sequence[str] = (), source_refs: Sequence[Any] = ()) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "inputs": [],
        "formula": "",
        "steps": [],
        "intermediate_results": {},
        "output_prices": {},
        "reasons": list(dict.fromkeys(str(item) for item in reasons if str(item))),
        "source_refs": _refs(source_refs),
        "supported": False,
    }


def _support_status(*, source_refs: Sequence[Any], fact_claim_ids: Sequence[Any], supported: bool | None, name: str) -> tuple[bool, str | None]:
    """Return a deliberately conservative default for direct calculators.

    Provider ``supported`` flags and source IDs establish provenance only. A
    method becomes supported in ``build_valuation`` after code validates the
    referenced fact claims and their semantic bindings.
    """
    if supported is True:
        return False, f"{name} provider support flag is not authoritative; code validation is required."
    if supported is False:
        return False, f"{name} was marked unsupported by the caller."
    return False, f"{name} has no code-validated fact binding."


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump(mode="python", exclude_none=False)
        except TypeError:
            return value.model_dump()
    return {}


def _fact_index(values: Mapping[str, Any] | Sequence[Any] | None) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """Index only repository-enriched, semantically supported fact claims."""
    valid: dict[str, dict[str, Any]] = {}
    rejected: dict[str, str] = {}
    if isinstance(values, Mapping):
        rows = []
        for key, raw in values.items():
            item = _mapping(raw)
            if item:
                item.setdefault("fact_id", key)
            rows.append(item)
    elif isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
        rows = [_mapping(raw) for raw in values]
    else:
        rows = []
    for item in rows:
        identifier = str(item.get("fact_id") or item.get("claim_id") or item.get("id") or "").strip()
        if not identifier:
            continue
        status = str(item.get("validation_status") or "").strip().lower()
        semantic = str(item.get("semantic_status") or "").strip().lower()
        if status != "validated" or semantic != "supported":
            rejected[identifier] = "The fact is not repository-validated with semantic_status=supported."
            continue
        valid[identifier] = item
    return valid, rejected


def _normal_text(value: Any) -> str:
    return " ".join(str(value or "").casefold().replace("_", " ").split())


def _issuer_matches(expected: str | None, observed: Any) -> bool:
    if not expected:
        return True
    observed_text = _normal_text(observed)
    expected_text = _normal_text(expected)
    if not observed_text:
        return False
    legal = {"inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "limited", "plc", "llc", "class", "ordinary", "common", "stock", "share", "shares"}
    left = {token for token in expected_text.replace("/", " ").split() if token not in legal}
    right = {token for token in observed_text.replace("/", " ").split() if token not in legal}
    return bool(left and right and (left == right or left.issubset(right) or right.issubset(left)))


def _metric_matches(expected: str | None, observed: Any) -> bool:
    """Compare a method's requested metric with the bound fact metric.

    A source-bound EPS value cannot be substituted with revenue (or another
    nearby number) merely because its numeric value and unit happen to fit.
    Keep aliases deliberately small and fail closed for an explicit unknown
    metric.
    """
    if not expected:
        return True
    observed_text = _normal_text(observed)
    if not observed_text:
        return False
    expected_text = _normal_text(expected)
    aliases = {
        "eps": {"eps", "earnings per share", "diluted eps", "basic eps"},
        "revenue": {"revenue", "revenues", "sales", "net sales", "turnover"},
        "ebitda": {"ebitda", "adjusted ebitda"},
        "operating income": {"operating income", "operating profit"},
        "depreciation and amortization": {"depreciation and amortization", "depreciation depletion and amortization"},
        "book equity": {"book equity", "book value", "stockholders equity", "shareholders equity"},
        "tangible book equity": {"tangible book equity", "tangible book value"},
        "market capitalization": {"market capitalization", "market cap", "equity market value"},
        "enterprise value": {"enterprise value", "ev"},
        "cash": {"cash", "cash and equivalents", "cash equivalents"},
        "debt": {"debt", "borrowings", "total debt"},
        "preferred": {"preferred", "preferred claims", "preferred stock"},
        "minority": {"minority", "minority interest", "noncontrolling interest"},
        "minority interest": {"minority", "minority interest", "noncontrolling interest"},
        "shares": {"shares", "shares outstanding", "weighted average shares", "diluted shares"},
        "free cash flow": {"free cash flow", "fcf", "unlevered free cash flow"},
        "fcf": {"free cash flow", "fcf", "unlevered free cash flow"},
        # A market quote is an entry/reference observation, not a fund's NAV
        # or an underlying value.  Keep those semantic categories separate so
        # a quoted price cannot certify a fair-value method.
        "nav": {"nav", "nav per unit", "nav per share", "net asset value", "net asset value per unit", "net asset value per share", "underlying value", "underlying asset value", "asset value", "unit value"},
        "price": {"price", "quote", "close", "closing price", "nav"},
    }
    accepted = aliases.get(expected_text, {expected_text})
    normalized_accepted = {_normal_text(alias) for alias in accepted}
    return observed_text in normalized_accepted


def _fact_currency(fact: Mapping[str, Any]) -> str | None:
    explicit = str(fact.get("currency") or "").strip().upper()
    if explicit:
        return explicit
    dimension = unit_dimension(str(fact.get("unit") or ""))
    return str(dimension[1]).upper() if dimension and dimension[1] else None


def _fact_period(fact: Mapping[str, Any]) -> str | None:
    return str(fact.get("period") or fact.get("period_end") or fact.get("period_start") or "").strip() or None


def _fact_matches(
    fact: Mapping[str, Any],
    *,
    value: Any = None,
    expected_metric: str | None = None,
    expected_dimension: str | None = None,
    currency: str | None = None,
    period: str | None = None,
    scale: str | None = None,
    basis: str | None = None,
    source_refs: Sequence[Any] = (),
    issuer: str | None = None,
) -> tuple[bool, str | None]:
    observed_value = _number(fact.get("value"))
    expected_value = _number(value)
    if expected_value is not None and observed_value != expected_value:
        return False, f"Fact value {fact.get('value')!s} does not match proposed input {value!s}."
    observed_metric = fact.get("metric") or fact.get("predicate") or fact.get("measure") or fact.get("fact_type")
    if expected_metric and not _metric_matches(expected_metric, observed_metric):
        return False, f"Fact metric {observed_metric or 'unknown'} does not match required {expected_metric}."
    dimension = unit_dimension(str(fact.get("unit") or ""))
    if expected_dimension and (dimension is None or dimension[0] != expected_dimension):
        return False, f"Fact unit {fact.get('unit')!s} does not match required {expected_dimension}."
    expected_currency = str(currency or "").strip().upper() or None
    if expected_currency and _fact_currency(fact) != expected_currency:
        return False, f"Fact currency {_fact_currency(fact) or 'unknown'} does not match required {expected_currency}."
    expected_period = str(period or "").strip() or None
    if expected_period and _fact_period(fact) != expected_period:
        return False, f"Fact period {_fact_period(fact) or 'unknown'} does not match required {expected_period}."
    expected_scale = str(scale or "").strip().casefold() or None
    observed_scale = str(fact.get("scale") or "").strip().casefold() or None
    if expected_scale and observed_scale != expected_scale:
        return False, f"Fact scale {observed_scale or 'unknown'} does not match required {expected_scale}."
    expected_basis = str(basis or "").strip().casefold() or None
    observed_basis = str(fact.get("basis") or fact.get("share_basis") or "").strip().casefold() or None
    if expected_basis and observed_basis != expected_basis:
        return False, f"Fact accounting/share basis {observed_basis or 'unknown'} does not match required {basis}."
    refs = set(_refs(source_refs))
    if refs and str(fact.get("source_ref") or "").strip() not in refs:
        return False, "Fact source reference is outside the valuation method's retained source set."
    observed_issuer = fact.get("issuer") or fact.get("issuer_name") or fact.get("subject") or fact.get("ticker") or fact.get("symbol")
    if issuer and not _issuer_matches(issuer, observed_issuer):
        return False, "Fact issuer/instrument binding does not match the candidate issuer."
    return True, None


def _input_rows(spec: Mapping[str, Any], root: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    raw_rows = spec.get("inputs")
    if isinstance(raw_rows, Sequence) and not isinstance(raw_rows, (str, bytes)):
        rows.extend(_mapping(row) for row in raw_rows if _mapping(row).get("key"))
    if not rows:
        raw_rows = root.get("inputs")
        if isinstance(raw_rows, Sequence) and not isinstance(raw_rows, (str, bytes)):
            rows.extend(_mapping(row) for row in raw_rows if _mapping(row).get("key"))
    return rows


def _historical_eps_baseline_age(params: Mapping[str, Any], fact: Mapping[str, Any], identifier: str, as_of: str) -> int | None:
    """Permit a dated historical anchor without asserting it is current EPS."""
    baseline = _input_row_for(params, "baseline_eps")
    years = _number(params.get("forecast_years"))
    if not params.get("_derived_eps_calculations") or baseline.get("kind") != "fact" or _refs(baseline.get("fact_claim_ids")) != [identifier] or years is None or years <= 0 or not str(params.get("forecast_span_rationale") or "").strip():
        return None
    try:
        observed = date.fromisoformat(str(fact.get("period_end") or fact.get("period") or "")[:10])
        evaluated = date.fromisoformat(as_of[:10])
    except ValueError:
        return None
    age = (evaluated - observed).days
    return age if 0 <= age <= 731 else None


def _validated_method_support(
    method: dict[str, Any],
    *,
    params: Mapping[str, Any],
    facts: Mapping[str, Mapping[str, Any]],
    rejected_facts: Mapping[str, str],
    source_refs: Sequence[Any],
    issuer: str | None,
    source_records: Mapping[str, Mapping[str, Any]] | None = None,
    as_of: str | None = None,
) -> tuple[bool, str | None]:
    """Validate method fact IDs and binding metadata before publishing support."""
    method_name = str(method.get("name") or "").casefold()
    if method_name == "eps_multiple":
        expected_metric = "eps"
    elif method_name in {"ev_multiple", "enterprise_multiple", "ebitda_multiple"}:
        expected_metric = str(params.get("metric_name") or "EBITDA")
    elif method_name in {"dcf", "discounted_cash_flow"}:
        expected_metric = "free cash flow"
    elif method_name in {"nav", "underlying", "asset_value"}:
        expected_metric = "nav"
    else:
        expected_metric = None
    ids = _refs(params.get("fact_claim_ids"))
    rows = params.get("_input_rows") if isinstance(params.get("_input_rows"), Sequence) else []
    conflicts = _refs(params.get("_input_conflicts"))
    if conflicts:
        return False, "Conflicting raw, alias and typed inputs were supplied for: " + ", ".join(conflicts) + "."
    metadata_issues = _refs(params.get("_eps_metadata_issues")) if method_name == "eps_multiple" else []
    # DCF and EV bridges cannot use a method-level fact ID as a blanket
    # attestation for cash, debt, claims and diluted shares.  Every material
    # bridge value must appear in an ordered typed input row, where a fact row
    # names its claim IDs and an assumption row carries a rationale.  This
    # keeps an omitted debt value from becoming an implicit zero and prevents
    # one FCF claim from certifying unrelated bridge components.
    bridge_aliases: dict[str, tuple[str, ...]] = {}
    if method_name in {"dcf", "discounted_cash_flow"}:
        bridge_aliases = {
            "forecast_unlevered_fcf": ("forecast_unlevered_fcf", "forecast_fcf", "fcf", "free_cash_flow"),
            "discount_rate": ("discount_rate", "wacc"),
            "terminal_growth": ("terminal_growth", "g"),
            "excess_cash": ("excess_cash", "cash"),
            "debt": ("debt",),
            "preferred_claims": ("preferred_claims", "preferred"),
            "minority_interest": ("minority_interest", "minority"),
            "diluted_shares": ("diluted_shares", "shares"),
        }
    elif method_name in {"ev_multiple", "enterprise_multiple", "ebitda_multiple"}:
        bridge_aliases = {
            "forecast_metric": ("forecast_metric", "ebitda", "metric"),
            "exit_ev_multiple": ("exit_ev_multiple", "ev_multiple", "multiple", "exit_multiple"),
            "excess_cash": ("excess_cash", "cash"),
            "debt": ("debt",),
            "preferred_claims": ("preferred_claims", "preferred"),
            "minority_interest": ("minority_interest", "minority"),
            "diluted_shares": ("diluted_shares", "shares"),
        }
    if bridge_aliases:
        normalized_rows = {
            str(row.get("key") or "").strip().casefold(): row
            for row in rows
            if isinstance(row, Mapping) and str(row.get("key") or "").strip()
        }
        for component, aliases_for_component in bridge_aliases.items():
            row = next((normalized_rows.get(alias) for alias in aliases_for_component if normalized_rows.get(alias) is not None), None)
            if row is None:
                return False, f"Explicit typed bridge input {component} is required; raw provider parameters cannot certify it."
            if row.get("value") is None or not str(row.get("value")).strip():
                return False, f"Bridge input {component} must carry an explicit value; absence is not zero."
            kind = str(row.get("kind") or "assumption").strip().lower()
            if kind == "fact" and not _refs(row.get("fact_claim_ids")):
                return False, f"Factual bridge input {component} must name its validated fact claim IDs."
            if kind == "assumption" and not str(row.get("rationale") or "").strip():
                return False, f"Assumption bridge input {component} requires a rationale."
    row_ids: list[str] = []
    for row in rows:
        # A declared claim reference is material even when the row is an
        # assumption. It still has to resolve to a supported, source/issuer
        # bound fact; otherwise an unrelated or missing ID can hide beside the
        # method-level reference.
        row_ids.extend(_refs(row.get("fact_claim_ids")))
    if not ids:
        ids = list(dict.fromkeys(row_ids))
    else:
        ids = list(dict.fromkeys([*ids, *row_ids]))
    if not ids:
        return False, "No code-validated fact_claim_ids were supplied for the method's factual inputs."
    target_currency = str(params.get("currency") or "").strip().upper() or None
    for row in rows:
        row_currency = str(row.get("currency") or "").strip().upper() or None
        unit = str(row.get("unit") or "").strip()
        dimension = unit_dimension(unit) if unit else None
        unit_currency = str(dimension[1] or "").strip().upper() if dimension and len(dimension) > 1 and dimension[1] else None
        if target_currency and row_currency and unit_currency and row_currency != unit_currency:
            return False, f"Input {row.get('key') or 'unknown'} declares currency {row_currency} but its unit declares {unit_currency}."
        effective_currency = row_currency or unit_currency
        if target_currency and effective_currency and dimension and dimension[0] in {"currency", "currency_per_share"} and effective_currency != target_currency:
            return False, f"Input {row.get('key') or 'unknown'} is denominated in {effective_currency}, but the valuation output is {target_currency}."
    for identifier in ids:
        fact = facts.get(identifier)
        if fact is None:
            return False, rejected_facts.get(identifier) or f"Fact claim {identifier} is not code-validated in this attempt."
        # A method-level ID must at least bind to the retained source set and
        # candidate issuer. Numeric/value checks are applied to explicit input
        # rows below when available.
        # DCF/EV bridge methods commonly bind several heterogeneous facts
        # (FCF, cash, debt, shares).  Their per-input rows below carry the
        # metric binding; method-level validation only checks provenance and
        # issuer so one bridge component cannot be mistaken for another.
        method_level_metric = expected_metric if method_name in {"nav", "underlying", "asset_value"} or method_name == "eps_multiple" and not rows else None
        ok, reason = _fact_matches(
            fact,
            expected_metric=method_level_metric,
            source_refs=source_refs,
            issuer=issuer,
        )
        if not ok:
            return False, reason
        if source_records is not None:
            source_id = str(fact.get("source_ref") or "").strip()
            source = source_records.get(source_id) if isinstance(source_records, Mapping) else None
            content = source.get("content") or source.get("original_content") if isinstance(source, Mapping) else None
            if not str(content or "").strip():
                return False, f"Retained source {source_id or 'unknown'} has no archived content for this fact."
            if not source_has_primary_coverage(source):
                return False, f"Retained source {source_id or 'unknown'} does not provide verified primary coverage for a material financial fact."
            if as_of:
                freshness = evaluate_fact_freshness(fact, source if isinstance(source, Mapping) else None, as_of=as_of)
                if freshness.get("status") != "fresh":
                    age = _historical_eps_baseline_age(params, fact, identifier, as_of) if freshness.get("status") == "stale" else None
                    if age is None:
                        return False, f"Fact claim {identifier} is {freshness.get('status') or 'unknown'} under the {freshness.get('kind') or 'material'} evidence policy."
                    caveat = f"Historical EPS baseline is {age} days old and remains stale as a current observation. It is used only as a dated forecast anchor; the explicit growth assumption spans the stated fiscal periods."
                    method["intermediate_results"].update(historical_baseline_age_days=age, baseline_freshness="stale", historical_baseline_caveat=caveat)
                    method.setdefault("steps", []).append(caveat)
                    for calculation in method.get("scenario_calculations", {}).values():
                        calculation.setdefault("intermediate_results", {}).update(historical_baseline_age_days=age, baseline_freshness="stale", historical_baseline_caveat=caveat)
                        calculation.setdefault("steps", []).append(caveat)
    for row in rows:
        if str(row.get("kind") or "assumption").strip().lower() != "fact":
            continue
        row_value = row.get("value")
        key = str(row.get("key") or "").strip().casefold()
        expected_dimension = None
        if any(token in key for token in ("eps", "nav", "per_share", "price", "value_per_unit")):
            expected_dimension = "currency_per_share" if "share" in key or "eps" in key or "price" in key else None
        elif "share" in key:
            expected_dimension = "shares"
        elif any(token in key for token in ("fcf", "cash", "debt", "minority", "preferred", "metric", "ebitda")):
            expected_dimension = "currency"
        row_ids = _refs(row.get("fact_claim_ids")) or ids
        if not row_ids:
            return False, f"Factual input {row.get('key') or 'unknown'} has no code-validated fact claim ID."
        matched = False
        reasons: list[str] = []
        for identifier in row_ids:
            fact = facts.get(identifier)
            if fact is None:
                reasons.append(rejected_facts.get(identifier) or f"Fact claim {identifier} is not code-validated in this attempt.")
                continue
            ok, reason = _fact_matches(
                fact,
                value=row_value,
                expected_metric=str(row.get("metric") or _metric_for_input_key(row.get("key"), expected_metric) or "") or None,
                expected_dimension=expected_dimension,
                currency=row.get("currency"),
                period=row.get("period"),
                scale=row.get("scale"),
                basis=row.get("basis") or row.get("share_basis"),
                source_refs=source_refs,
                issuer=issuer,
            )
            if ok:
                matched = True
                break
            if reason:
                reasons.append(reason)
        if not matched:
            return False, "; ".join(dict.fromkeys(reasons)) or f"Factual input {row.get('key') or 'unknown'} has no matching validated fact."
    if method_name == "eps_multiple" and rows and not any(
        identifier in facts and _metric_matches("eps", facts[identifier].get("metric")) for identifier in ids
    ):
        return False, "An EPS method requires a validated EPS baseline; unrelated driver facts cannot certify earnings."
    if not rows:
        if method_name == "eps_multiple":
            expected_value = params.get("forecast_eps", params.get("forecast_diluted_eps", params.get("eps", params.get("forward_diluted_eps"))))
            expected_dimension = "currency_per_share"
        elif method_name == "nav":
            expected_value = params.get("nav_per_unit", params.get("underlying_value"))
            expected_dimension = None
        elif method_name == "ev_multiple":
            expected_value = params.get("forecast_metric", params.get("ebitda", params.get("metric")))
            expected_dimension = "currency"
        else:
            expected_value = None
            expected_dimension = None
        if expected_value is not None:
            if not any(
                _fact_matches(
                    fact,
                    value=expected_value,
                    expected_metric=expected_metric,
                    expected_dimension=expected_dimension,
                    currency=params.get("currency"),
                    period=params.get("period"),
                    scale=params.get("scale"),
                    basis=params.get("basis") or params.get("share_basis"),
                    source_refs=source_refs,
                    issuer=issuer,
                )[0]
                for fact in (facts.get(identifier) for identifier in ids)
                if fact is not None
            ):
                return False, "No validated fact matches the method's numeric input, unit, currency and issuer binding."
    if metadata_issues:
        return False, "EPS input requirements are incomplete: " + " ".join(metadata_issues)
    return True, None


def _scenario_value(raw: Any, *keys: str) -> Any:
    if isinstance(raw, Mapping):
        values = [raw[key] for key in keys if key in raw and raw[key] is not None]
        if not values:
            return None
        first = values[0]
        if any(not _values_agree(first, value) for value in values[1:]):
            return None
        return first
    return raw


def _scenario_aliases_conflict(raw: Any, *keys: str) -> bool:
    """Detect disagreement among explicitly supplied scenario aliases."""
    if not isinstance(raw, Mapping):
        return False
    values = [raw[key] for key in keys if key in raw and raw[key] is not None]
    return len(values) > 1 and any(not _values_agree(values[0], value) for value in values[1:])


def calculate_eps_multiple(
    forecast_eps: Any = None,
    exit_multiple: Any = None,
    *,
    scenarios: Mapping[str, Any] | None = None,
    currency: str | None = None,
    period: str | None = None,
    basis: str | None = None,
    share_basis: str | None = None,
    forecast_eps_kind: str = "fact",
    forecast_eps_unit: str | None = None,
    forecast_eps_currency: str | None = None,
    forecast_eps_period: str | None = None,
    forecast_eps_scale: str | None = None,
    forecast_eps_basis: str | None = None,
    forecast_eps_statement_type: str | None = None,
    forecast_eps_derivation: str = "",
    multiple_rationale: str = "",
    source_refs: Sequence[Any] = (),
    fact_claim_ids: Sequence[Any] = (),
    supported: bool | None = None,
    as_of: str | None = None,
) -> dict[str, Any]:
    """Calculate forward diluted EPS times an exit P/E for named scenarios."""
    method = _base_method("eps_multiple", source_refs=source_refs)
    method["formula"] = "forecast_diluted_EPS × exit_P/E"
    method["source_refs"] = _refs(source_refs)
    method["inputs"] = [
        _method_input(
            "forecast_diluted_eps",
            forecast_eps,
            unit=forecast_eps_unit or "currency/share",
            currency=forecast_eps_currency or currency,
            period=forecast_eps_period or period,
            scale=forecast_eps_scale,
            basis=forecast_eps_basis or basis or share_basis,
            statement_type=forecast_eps_statement_type,
            kind=forecast_eps_kind,
            fact_claim_ids=fact_claim_ids,
            source_refs=source_refs,
            rationale=forecast_eps_derivation,
        ),
        _method_input("exit_pe", exit_multiple, unit="multiple", period=period, kind="assumption", source_refs=source_refs, rationale=multiple_rationale),
    ]
    eps = _number(forecast_eps)
    multiple = _number(exit_multiple)
    if eps is None or multiple is None:
        method["reasons"] = ["Forward diluted EPS and exit multiple are required."]
        return method
    if eps <= _ZERO:
        method["reasons"] = ["A positive P/E cannot be silently applied to zero or negative EPS."]
        return method
    if multiple <= _ZERO:
        method["reasons"] = ["Exit multiple must be positive."]
        return method
    supported_ok, support_reason = _support_status(source_refs=source_refs, fact_claim_ids=fact_claim_ids, supported=supported, name="EPS multiple")
    method["supported"] = supported_ok
    if support_reason:
        method["reasons"].append(support_reason)
    method["intermediate_results"] = {
        "forecast_diluted_eps": quantize(eps),
        "exit_pe": quantize(multiple),
        "forecast_eps_derivation": forecast_eps_derivation,
        "fiscal_period": forecast_eps_period or period,
        "basis": forecast_eps_basis or basis,
        "share_basis": share_basis,
        "forecast_eps_kind": forecast_eps_kind if forecast_eps_kind in {"fact", "assumption"} else "assumption",
    }
    method["steps"] = ["Use a forward diluted EPS forecast with its fiscal period and GAAP/adjusted basis.", "Apply the explicitly justified exit P/E."]
    scenario_rows = scenarios if isinstance(scenarios, Mapping) else {}
    if not scenario_rows:
        scenario_rows = {"base": {"forecast_eps": eps, "exit_multiple": multiple}}
    outputs: dict[str, str] = {}
    scenario_calculations: dict[str, dict[str, Any]] = {}
    for name in ("bear", "base", "bull"):
        raw = scenario_rows.get(name)
        if raw is None and name == "base":
            raw = {"forecast_eps": eps, "exit_multiple": multiple}
        if raw is None:
            continue
        if _scenario_aliases_conflict(raw, "forecast_eps", "eps", "diluted_eps") or _scenario_aliases_conflict(raw, "exit_multiple", "pe", "multiple"):
            method["reasons"].append(f"{name} scenario contains conflicting aliases.")
            continue
        row_eps = _number(_scenario_value(raw, "forecast_eps", "eps", "diluted_eps"))
        row_multiple = _number(_scenario_value(raw, "exit_multiple", "pe", "multiple"))
        if row_eps is None or row_multiple is None or row_eps <= _ZERO or row_multiple <= _ZERO:
            method["reasons"].append(f"{name} scenario requires positive EPS and multiple.")
            continue
        outputs[name] = quantize(row_eps * row_multiple)
        scenario_calculations[name] = {
            "inputs": [
                _method_input("forecast_diluted_eps", row_eps, unit=forecast_eps_unit or "currency/share", currency=currency, period=period, basis=forecast_eps_basis or basis, kind=forecast_eps_kind if row_eps == eps else "assumption", fact_claim_ids=fact_claim_ids, source_refs=source_refs, rationale=forecast_eps_derivation),
                _method_input("exit_pe", row_multiple, unit="multiple", kind="assumption", rationale=str(raw.get("multiple_rationale") or multiple_rationale) if isinstance(raw, Mapping) else multiple_rationale),
            ],
            "formula": method["formula"],
            "steps": [f"{quantize(row_eps)} × {quantize(row_multiple)} = {outputs[name]}"],
            "intermediate_results": {"forecast_diluted_eps": quantize(row_eps), "exit_pe": quantize(row_multiple)},
            "output_price": outputs[name],
        }
    if not outputs:
        method["reasons"].append("No named scenario produced a positive price.")
        return method
    method["status"] = "complete"
    method["output_prices"] = outputs
    method["scenario_calculations"] = scenario_calculations
    method["as_of"] = _as_of(as_of)
    return method


def _normalise_fcf_rows(forecast_fcf: Sequence[Any] | Mapping[Any, Any] | None) -> list[tuple[int, Decimal]]:
    if isinstance(forecast_fcf, Mapping):
        values = list(forecast_fcf.items())
    else:
        values = list(enumerate(forecast_fcf or [], start=1))
    parsed: list[tuple[int, Decimal, int]] = []
    for index, raw in values:
        year = index
        value = raw
        explicit_year = isinstance(raw, Mapping)
        if isinstance(raw, Mapping):
            year = raw.get("year", raw.get("period", index))
            value = raw.get("fcf", raw.get("free_cash_flow", raw.get("value")))
        try:
            year_number = int(str(year).split("-")[0])
        except (TypeError, ValueError):
            # A malformed explicit period cannot be safely compressed into a
            # neighboring forecast year. Reject the schedule instead.
            if explicit_year or isinstance(forecast_fcf, Mapping):
                return []
            year_number = index
        amount = _number(value)
        if amount is None:
            # Dropping one malformed FCF changes every later discount weight;
            # reject the complete schedule rather than silently shortening it.
            return []
        if year_number <= 0:
            return []
        parsed.append((year_number, amount, index))
    if not parsed:
        return []
    # Calendar-year keys (2026, 2027 or ISO dates) are periods, not literal
    # discount exponents. Normalize them to one-based forecast offsets.
    calendar = all(year >= 1900 for year, _, _ in parsed)
    if any((year >= 1900) != calendar for year, _, _ in parsed):
        return []
    if calendar:
        first_year = min(year for year, _, _ in parsed)
        parsed.sort(key=lambda row: row[0])
        rows = [(year - first_year + 1, amount) for year, amount, _ in parsed]
    else:
        # Relative forecast offsets are already discount exponents. Preserve
        # gaps such as years 1 and 3 so the terminal value remains at year 3.
        parsed.sort(key=lambda row: row[0])
        rows = [(year, amount) for year, amount, _ in parsed]
    years = [year for year, _ in rows]
    if len(years) != len(set(years)) or any(year <= 0 for year in years):
        return []
    return rows


def _dcf_price_components(
    rows: Sequence[tuple[int, Decimal]],
    rate: Decimal,
    growth: Decimal,
    cash: Decimal,
    debt: Decimal,
    preferred: Decimal,
    minority: Decimal,
    shares: Decimal,
    ratio: Decimal,
    convention: str,
) -> dict[str, Any]:
    discount_rows: list[dict[str, str]] = []
    pv_fcf = _ZERO
    for year, value in rows:
        exponent = Decimal(year) - (Decimal("0.5") if convention == "mid_year" else _ZERO)
        discount = (_ONE + rate) ** exponent
        pv = value / discount
        pv_fcf += pv
        discount_rows.append({"year": str(year), "fcf": quantize(value), "discount_factor": quantize(discount), "pv": quantize(pv), "discount_exponent": format(exponent, "f")})
    final_year, final_fcf = rows[-1]
    terminal_value = final_fcf * (_ONE + growth) / (rate - growth)
    terminal_exponent = Decimal(final_year) - (Decimal("0.5") if convention == "mid_year" else _ZERO)
    pv_terminal = terminal_value / ((_ONE + rate) ** terminal_exponent)
    enterprise_value = pv_fcf + pv_terminal
    equity_value = enterprise_value + cash - debt - preferred - minority
    price = equity_value / shares * ratio
    return {
        "discounted_fcf": discount_rows,
        "pv_fcf": pv_fcf,
        "terminal_value": terminal_value,
        "pv_terminal": pv_terminal,
        "enterprise_value": enterprise_value,
        "equity_value": equity_value,
        "price": price,
    }


def calculate_dcf(
    forecast_fcf: Sequence[Any] | Mapping[Any, Any] | None = None,
    discount_rate: Any = None,
    terminal_growth: Any = None,
    *,
    excess_cash: Any = None,
    debt: Any = None,
    preferred_claims: Any = None,
    minority_interest: Any = None,
    diluted_shares: Any = None,
    adr_ratio: Any = "1",
    scenarios: Mapping[str, Any] | None = None,
    currency: str | None = None,
    source_refs: Sequence[Any] = (),
    fact_claim_ids: Sequence[Any] = (),
    supported: bool | None = None,
    discount_convention: str = "year_end",
    as_of: str | None = None,
) -> dict[str, Any]:
    """Discount unlevered FCF and terminal value, then bridge EV to equity."""
    method = _base_method("dcf", source_refs=source_refs)
    method["formula"] = "Σ(FCF_t/(1+r)^t) + TV/(1+r)^N; equity = EV + cash - debt - preferred - minority"
    convention = str(discount_convention or "").strip().lower().replace("-", "_")
    if convention not in {"year_end", "mid_year"}:
        method["reasons"] = ["discount_convention must be year_end or mid_year."]
        method["missing_inputs"] = ["discount_convention"]
        return method
    rows = _normalise_fcf_rows(forecast_fcf)
    rate = _number(discount_rate)
    growth = _number(terminal_growth)
    cash = _number(excess_cash)
    debt_value = _number(debt)
    preferred = _number(preferred_claims)
    minority = _number(minority_interest)
    shares = _number(diluted_shares)
    ratio = _number(adr_ratio)
    method["inputs"] = [
        _method_input("forecast_unlevered_fcf", json.dumps({str(year): quantize(value) for year, value in rows}, sort_keys=True), unit=currency, currency=currency, kind="fact", fact_claim_ids=fact_claim_ids, source_refs=source_refs),
        _method_input("discount_rate", discount_rate, unit="fraction", kind="assumption", source_refs=source_refs),
        _method_input("terminal_growth", terminal_growth, unit="fraction", kind="assumption", source_refs=source_refs),
        _method_input("excess_cash", excess_cash, unit=currency, currency=currency, kind="fact", fact_claim_ids=fact_claim_ids, source_refs=source_refs),
        _method_input("debt", debt, unit=currency, currency=currency, kind="fact", fact_claim_ids=fact_claim_ids, source_refs=source_refs),
        _method_input("preferred_claims", preferred_claims, unit=currency, currency=currency, kind="fact", fact_claim_ids=fact_claim_ids, source_refs=source_refs),
        _method_input("minority_interest", minority_interest, unit=currency, currency=currency, kind="fact", fact_claim_ids=fact_claim_ids, source_refs=source_refs),
        _method_input("diluted_shares", diluted_shares, unit="shares", fact_claim_ids=fact_claim_ids, source_refs=source_refs),
        _method_input("adr_ratio", adr_ratio, unit="ADR/ordinary", kind="assumption", source_refs=source_refs),
    ]
    missing: list[str] = []
    if not rows:
        missing.append("forecast_unlevered_fcf")
    if rate is None:
        missing.append("discount_rate")
    if growth is None:
        missing.append("terminal_growth")
    if rate is not None and rate <= -_ONE:
        missing.append("discount_rate_above_minus_one")
    if rate is not None and growth is not None and growth >= rate:
        method["reasons"] = ["Terminal growth must be strictly below the discount rate."]
        return method
    for key, value in (("excess_cash", cash), ("debt", debt_value), ("preferred_claims", preferred), ("minority_interest", minority), ("diluted_shares", shares), ("adr_ratio", ratio)):
        if value is None:
            missing.append(key)
    if shares is not None and shares <= _ZERO:
        missing.append("positive_diluted_shares")
    if ratio is not None and ratio <= _ZERO:
        missing.append("positive_adr_ratio")
    if missing:
        method["reasons"] = list(dict.fromkeys(f"Missing or invalid {item}." for item in missing))
        method["missing_inputs"] = list(dict.fromkeys(missing))
        return method
    supported_ok, support_reason = _support_status(source_refs=source_refs, fact_claim_ids=fact_claim_ids, supported=supported, name="DCF")
    method["supported"] = supported_ok
    if support_reason:
        method["reasons"].append(support_reason)
    components = _dcf_price_components(rows, rate, growth, cash, debt_value, preferred, minority, shares, ratio, convention)
    discount_rows = components["discounted_fcf"]
    pv_fcf = components["pv_fcf"]
    terminal_value = components["terminal_value"]
    pv_terminal = components["pv_terminal"]
    enterprise_value = components["enterprise_value"]
    equity_value = components["equity_value"]
    price = components["price"]
    method["status"] = "complete"
    method["steps"] = [f"Discount each forecast FCF using {convention} convention.", "Calculate a Gordon-growth terminal value below the discount rate.", "Bridge enterprise value to equity value with dated cash, debt, preferred and minority inputs.", "Divide by diluted shares and apply the documented ADR ratio."]
    method["intermediate_results"] = {"discounted_fcf": discount_rows, "pv_fcf": quantize(pv_fcf), "terminal_value": quantize(terminal_value), "pv_terminal": quantize(pv_terminal), "enterprise_value": quantize(enterprise_value), "equity_value": quantize(equity_value), "negative_equity": equity_value < _ZERO, "diluted_shares": quantize(shares), "adr_ratio": quantize(ratio)}
    if equity_value < _ZERO:
        # Keep the bridge diagnostic for review, but do not expose a negative
        # per-share value as an executable target.
        method["status"] = "partial"
        method["reasons"] = ["Equity value is negative; no executable per-share target was emitted."]
        method["output_prices"] = {}
    else:
        outputs: dict[str, str] = {}
        scenario_calculations: dict[str, Any] = {}
        scenario_rows = scenarios if isinstance(scenarios, Mapping) else {}
        explicit_scenario_names = {
            name for name in ("bear", "base", "bull")
            if scenario_rows.get(name) is not None
        }
        # A supplied scenario row may override FCF, discount rate and/or
        # terminal growth. Missing dimensions intentionally inherit the
        # explicit base assumptions; no bear/bull value is fabricated when a
        # named row is absent or malformed.
        for name in ("bear", "base", "bull"):
            raw = scenario_rows.get(name)
            if raw is None and name == "base":
                raw = {"forecast_fcf": forecast_fcf, "discount_rate": discount_rate, "terminal_growth": terminal_growth}
            if raw is None:
                continue
            if isinstance(raw, Mapping):
                if (
                    _scenario_aliases_conflict(raw, "forecast_fcf", "fcf", "free_cash_flow")
                    or _scenario_aliases_conflict(raw, "discount_rate", "wacc")
                    or _scenario_aliases_conflict(raw, "terminal_growth", "g")
                ):
                    continue
                scenario_fcf = _scenario_value(raw, "forecast_fcf", "fcf", "free_cash_flow")
                if scenario_fcf is None:
                    scenario_fcf = forecast_fcf
                scenario_rate_value = _scenario_value(raw, "discount_rate", "wacc")
                scenario_growth_value = _scenario_value(raw, "terminal_growth", "g")
                scenario_rate = _number(rate if scenario_rate_value is None else scenario_rate_value)
                scenario_growth = _number(growth if scenario_growth_value is None else scenario_growth_value)
            else:
                scenario_fcf, scenario_rate, scenario_growth = forecast_fcf, rate, growth
            scenario_rows_normalized = _normalise_fcf_rows(scenario_fcf)
            if not scenario_rows_normalized or scenario_rate is None or scenario_growth is None or scenario_rate <= -_ONE or scenario_growth >= scenario_rate:
                continue
            scenario_components = _dcf_price_components(scenario_rows_normalized, scenario_rate, scenario_growth, cash, debt_value, preferred, minority, shares, ratio, convention)
            if scenario_components["equity_value"] >= _ZERO:
                outputs[name] = quantize(scenario_components["price"])
                reason = str(raw.get("rationale") or "") if isinstance(raw, Mapping) else ""
                scenario_inputs = [
                    _method_input("forecast_unlevered_fcf", json.dumps({str(year): quantize(value) for year, value in scenario_rows_normalized}, sort_keys=True), unit=currency, currency=currency, kind="assumption", rationale=reason),
                    _method_input("discount_rate", scenario_rate, unit="fraction", kind="assumption", rationale=reason),
                    _method_input("terminal_growth", scenario_growth, unit="fraction", kind="assumption", rationale=reason),
                    *method["inputs"][3:],
                ]
                scenario_calculations[name] = {"inputs": scenario_inputs, "formula": method["formula"], "steps": list(method["steps"]), "intermediate_results": {key: quantize(value) if isinstance(value, Decimal) else value for key, value in scenario_components.items()}, "output_price": outputs[name]}
        method["output_prices"] = outputs or {"base": quantize(price)}
        method["scenario_calculations"] = scenario_calculations
        if explicit_scenario_names:
            missing_scenarios = [name for name in ("bear", "base", "bull") if name not in explicit_scenario_names or name not in outputs]
            if missing_scenarios:
                method["status"] = "partial"
                method["reasons"].append(
                    "Explicit DCF scenario set is incomplete: " + ", ".join(missing_scenarios) + "."
                )
    method["as_of"] = _as_of(as_of)
    return method


def calculate_ev_multiple(
    forecast_metric: Any = None,
    exit_multiple: Any = None,
    *,
    metric_name: str = "EBITDA",
    excess_cash: Any = None,
    debt: Any = None,
    preferred_claims: Any = None,
    minority_interest: Any = None,
    diluted_shares: Any = None,
    adr_ratio: Any = "1",
    currency: str | None = None,
    scenarios: Mapping[str, Any] | None = None,
    source_refs: Sequence[Any] = (),
    fact_claim_ids: Sequence[Any] = (),
    supported: bool | None = None,
    as_of: str | None = None,
) -> dict[str, Any]:
    """Apply an EV multiple, then use the same enterprise-to-equity bridge."""
    method = _base_method("ev_multiple", source_refs=source_refs)
    method["formula"] = f"forecast_{metric_name} × exit_EV_multiple; equity = EV + cash - debt - preferred - minority"
    metric = _number(forecast_metric)
    multiple = _number(exit_multiple)
    cash, debt_value = _number(excess_cash), _number(debt)
    preferred, minority = _number(preferred_claims), _number(minority_interest)
    shares, ratio = _number(diluted_shares), _number(adr_ratio)
    method["inputs"] = [_method_input(f"forecast_{metric_name.casefold()}", forecast_metric, unit=currency, currency=currency, kind="fact", fact_claim_ids=fact_claim_ids, source_refs=source_refs), _method_input("exit_ev_multiple", exit_multiple, unit="multiple", kind="assumption", source_refs=source_refs)]
    missing = [key for key, value in ((f"forecast_{metric_name.casefold()}", metric), ("exit_ev_multiple", multiple), ("excess_cash", cash), ("debt", debt_value), ("preferred_claims", preferred), ("minority_interest", minority), ("diluted_shares", shares), ("adr_ratio", ratio)) if value is None]
    if metric is not None and metric <= _ZERO:
        missing.append(f"positive_{metric_name.casefold()}")
    if multiple is not None and multiple <= _ZERO:
        missing.append("positive_exit_ev_multiple")
    if shares is not None and shares <= _ZERO:
        missing.append("positive_diluted_shares")
    if ratio is not None and ratio <= _ZERO:
        missing.append("positive_adr_ratio")
    if missing:
        method["reasons"] = list(dict.fromkeys(f"Missing or invalid {item}." for item in missing))
        method["missing_inputs"] = list(dict.fromkeys(missing))
        return method
    supported_ok, support_reason = _support_status(source_refs=source_refs, fact_claim_ids=fact_claim_ids, supported=supported, name="EV multiple")
    method["supported"] = supported_ok
    if support_reason:
        method["reasons"].append(support_reason)
    enterprise_value = metric * multiple
    equity_value = enterprise_value + cash - debt_value - preferred - minority
    price = equity_value / shares * ratio
    method["status"] = "complete"
    method["steps"] = [f"Multiply forecast {metric_name} by the exit EV multiple.", "Bridge enterprise value to equity value and divide by diluted shares."]
    method["intermediate_results"] = {"enterprise_value": quantize(enterprise_value), "equity_value": quantize(equity_value), "negative_equity": equity_value < _ZERO, "diluted_shares": quantize(shares), "adr_ratio": quantize(ratio)}
    rows = scenarios if isinstance(scenarios, Mapping) else {}
    if not rows:
        rows = {"base": {"forecast_metric": metric, "exit_multiple": multiple}}
    outputs: dict[str, str] = {}
    scenario_calculations: dict[str, Any] = {}
    for name in ("bear", "base", "bull"):
        raw = rows.get(name)
        if raw is None and name == "base":
            raw = {"forecast_metric": metric, "exit_multiple": multiple}
        if raw is None:
            continue
        if _scenario_aliases_conflict(raw, "forecast_metric", "metric", metric_name.casefold()) or _scenario_aliases_conflict(raw, "exit_multiple", "multiple", "ev_multiple"):
            continue
        row_metric = _number(_scenario_value(raw, "forecast_metric", "metric", metric_name.casefold()))
        row_multiple = _number(_scenario_value(raw, "exit_multiple", "multiple", "ev_multiple"))
        if row_metric is None or row_multiple is None or row_metric <= _ZERO or row_multiple <= _ZERO:
            continue
        outputs[name] = quantize((row_metric * row_multiple + cash - debt_value - preferred - minority) / shares * ratio)
        reason = str(raw.get("rationale") or "") if isinstance(raw, Mapping) else ""
        scenario_calculations[name] = {
            "inputs": [
                _method_input("forecast_metric", row_metric, unit=currency, currency=currency, kind="assumption", rationale=reason),
                _method_input("exit_ev_multiple", row_multiple, unit="multiple", kind="assumption", rationale=str(raw.get("multiple_rationale") or reason) if isinstance(raw, Mapping) else reason),
                *[_method_input(key, value, kind="fact", fact_claim_ids=fact_claim_ids, source_refs=source_refs) for key, value in (("excess_cash", cash), ("debt", debt_value), ("preferred_claims", preferred), ("minority_interest", minority), ("diluted_shares", shares))],
                _method_input("adr_ratio", ratio, unit="ADR/ordinary", kind="assumption"),
            ],
            "formula": method["formula"], "steps": list(method["steps"]),
            "intermediate_results": {"forecast_metric": quantize(row_metric), "exit_ev_multiple": quantize(row_multiple), "enterprise_value": quantize(row_metric * row_multiple), "equity_value": quantize(row_metric * row_multiple + cash - debt_value - preferred - minority), "diluted_shares": quantize(shares), "adr_ratio": quantize(ratio)},
            "output_price": outputs[name],
        }
    if equity_value < _ZERO:
        method["status"] = "partial"
        method["reasons"] = ["Equity value is negative; no executable per-share target was emitted."]
        method["output_prices"] = {}
    else:
        method["output_prices"] = outputs or {"base": quantize(price)}
        method["scenario_calculations"] = scenario_calculations
    method["as_of"] = _as_of(as_of)
    return method


def calculate_nav(
    nav_per_unit: Any = None,
    *,
    scenarios: Mapping[str, Any] | None = None,
    currency: str | None = None,
    source_refs: Sequence[Any] = (),
    fact_claim_ids: Sequence[Any] = (),
    supported: bool | None = None,
    as_of: str | None = None,
) -> dict[str, Any]:
    """Asset-appropriate NAV/underlying value method for funds and trusts."""
    method = _base_method("nav", source_refs=source_refs)
    method["formula"] = "supported NAV per unit (or underlying asset value per unit)"
    value = _number(nav_per_unit)
    if value is None or value <= _ZERO:
        method["reasons"] = ["A positive supported NAV per unit is required."]
        return method
    supported_ok, support_reason = _support_status(source_refs=source_refs, fact_claim_ids=fact_claim_ids, supported=supported, name="NAV")
    method["supported"] = supported_ok
    if support_reason:
        method["reasons"].append(support_reason)
    method["inputs"] = [_method_input("nav_per_unit", nav_per_unit, unit=currency + "/unit" if currency else "currency/unit", currency=currency, kind="fact", fact_claim_ids=fact_claim_ids, source_refs=source_refs)]
    rows = scenarios if isinstance(scenarios, Mapping) else {}
    if not rows:
        rows = {"base": value}
    outputs: dict[str, str] = {}
    scenario_calculations: dict[str, Any] = {}
    for name in ("bear", "base", "bull"):
        raw = rows.get(name)
        if raw is None and name == "base":
            raw = value
        if _scenario_aliases_conflict(raw, "nav_per_unit", "underlying_value", "value"):
            continue
        row_value = _number(_scenario_value(raw, "nav_per_unit", "underlying_value", "value"))
        if row_value is not None and row_value > _ZERO:
            outputs[name] = quantize(row_value)
            scenario_calculations[name] = {"inputs": [_method_input("nav_per_unit", row_value, unit=currency + "/unit" if currency else "currency/unit", currency=currency, kind="fact" if row_value == value else "assumption", fact_claim_ids=fact_claim_ids, source_refs=source_refs, rationale=str(raw.get("rationale") or "") if isinstance(raw, Mapping) else "")], "formula": method["formula"], "steps": ["Use the explicitly supplied per-unit scenario value."], "intermediate_results": {"nav_per_unit": quantize(row_value)}, "output_price": outputs[name]}
    method["status"] = "complete" if outputs else "partial"
    method["output_prices"] = outputs
    method["scenario_calculations"] = scenario_calculations
    method["as_of"] = _as_of(as_of)
    method["steps"] = ["Use the dated NAV or underlying asset value per unit; do not invent issuer EPS or DCF inputs."]
    return method


def _assumption_mapping(assumptions: Any) -> dict[str, Any]:
    if isinstance(assumptions, Mapping):
        return dict(assumptions)
    if hasattr(assumptions, "model_dump"):
        try:
            return assumptions.model_dump(mode="python", exclude_none=False)
        except TypeError:
            return assumptions.model_dump()
    return {}


def _method_specs(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, str):
        return [{"name": raw}]
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return []
    output: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, str):
            output.append({"name": item})
        elif isinstance(item, Mapping):
            value = dict(item)
            value.setdefault("name", value.get("method", value.get("type", "")))
            output.append(value)
    return output


def _normal_key(value: Any) -> str:
    return str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")


def _values_agree(left: Any, right: Any) -> bool:
    """Compare aliases without letting formatting create a false conflict."""
    left_number, right_number = _number(left), _number(right)
    if left_number is not None and right_number is not None:
        return left_number == right_number
    if isinstance(left, Mapping) or isinstance(right, Mapping) or isinstance(left, Sequence) or isinstance(right, Sequence):
        try:
            return json.dumps(left, sort_keys=True, default=str) == json.dumps(right, sort_keys=True, default=str)
        except (TypeError, ValueError):
            return left == right
    return str(left).strip() == str(right).strip()


# Provider adapters use a few ergonomic aliases for each method.  The
# calculator must consume one canonical value after reconciling all of them;
# a typed row and a parallel scalar are never allowed to silently disagree.
_METHOD_INPUT_ALIASES: dict[str, tuple[str, ...]] = {
    "baseline_revenue": ("baseline_revenue",),
    "baseline_ebitda": ("baseline_ebitda",),
    "baseline_operating_income": ("baseline_operating_income",),
    "baseline_da": ("baseline_da",),
    "baseline_nav": ("baseline_nav",),
    "forecast_eps": ("forecast_eps", "forecast_diluted_eps", "eps", "forward_diluted_eps"),
    "exit_multiple": ("exit_multiple", "exit_pe", "pe"),
    "forecast_fcf": ("forecast_fcf", "forecast_unlevered_fcf", "fcf", "free_cash_flow"),
    "discount_rate": ("discount_rate", "wacc"),
    "terminal_growth": ("terminal_growth", "g"),
    "excess_cash": ("excess_cash", "cash"),
    "debt": ("debt",),
    "preferred_claims": ("preferred_claims", "preferred"),
    "minority_interest": ("minority_interest", "minority"),
    "diluted_shares": ("diluted_shares", "shares"),
    "adr_ratio": ("adr_ratio",),
    "forecast_metric": ("forecast_metric", "ebitda", "metric"),
    "exit_ev_multiple": ("exit_ev_multiple", "ev_multiple", "multiple", "exit_multiple"),
    "nav_per_unit": ("nav_per_unit", "underlying_value"),
}


def _merge_params(spec: Mapping[str, Any], root: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    rows = _input_rows(spec, root)
    result["_input_rows"] = rows
    for key in ("inputs", "assumptions", "parameters"):
        nested = spec.get(key)
        if isinstance(nested, Mapping):
            result.update(nested)
    result.update({key: value for key, value in spec.items() if key not in {"name", "method", "type", "inputs", "assumptions", "parameters"}})
    for key, value in root.items():
        if key not in {"methods", "scenarios", "inputs", "asset_class", "rationale"} and key not in result:
            result[key] = value
    root_inputs = root.get("inputs")
    if isinstance(root_inputs, Sequence) and not isinstance(root_inputs, (str, bytes)):
        for raw in root_inputs:
            if isinstance(raw, Mapping) and raw.get("key"):
                result.setdefault(str(raw["key"]), raw.get("value"))
    conflicts: list[str] = []
    method_aliases = _METHOD_INPUT_ALIASES
    if _normal_key(spec.get("name")) in {"eps", "eps_multiple", "pe", "earnings_multiple"}:
        # EPS consumes forecast EPS and P/E, not EV/DCF operands. Descriptive
        # labels such as forecast_metric="GAAP diluted EPS" and metric="EPS"
        # must not be reconciled as competing numeric EBITDA observations.
        # Keep every typed row and declared fact reference below: unused
        # labels cannot waive factual validation or a real EPS input conflict.
        method_aliases = {key: _METHOD_INPUT_ALIASES[key] for key in ("forecast_eps", "exit_multiple")}
    # Resolve each scalar alias group from both raw parameters and typed input
    # rows.  ``None`` is an omitted Pydantic field, not an authoritative value
    # that should mask the typed row.
    for canonical, aliases in method_aliases.items():
        alias_keys = {_normal_key(alias) for alias in aliases}
        observations: list[tuple[str, Any]] = []
        for key, value in result.items():
            if _normal_key(key) in alias_keys and value is not None:
                observations.append((str(key), value))
        for row in rows:
            row_key = _normal_key(row.get("key"))
            value = row.get("value")
            if row_key in alias_keys and value is not None:
                observations.append((f"typed:{row.get('key')}", value))
        if not observations:
            continue
        first_value = observations[0][1]
        if any(not _values_agree(first_value, value) for _, value in observations[1:]):
            conflicts.append(canonical)
            result[canonical] = None
        else:
            # Prefer the typed row when present, then use its value as the one
            # canonical input consumed by the method calculation.
            typed = next((value for label, value in observations if label.startswith("typed:")), None)
            result[canonical] = first_value if typed is None else typed
    for row in rows:
        key = str(row.get("key") or "").strip()
        if key:
            normalized = _normal_key(key)
            if normalized not in {_normal_key(alias) for aliases in method_aliases.values() for alias in aliases}:
                result.setdefault(key, row.get("value"))
    declared_fact_ids = _refs(result.get("fact_claim_ids"))
    declared_source_refs = _refs(result.get("source_refs"))
    for row in rows:
        declared_fact_ids.extend(_refs(row.get("fact_claim_ids")))
        declared_source_refs.extend(_refs(row.get("source_refs")))
    if declared_fact_ids:
        result["fact_claim_ids"] = list(dict.fromkeys(declared_fact_ids))
    if declared_source_refs:
        result["source_refs"] = list(dict.fromkeys(declared_source_refs))
    result["_input_conflicts"] = list(dict.fromkeys(conflicts))
    return result


def _input_row_for(params: Mapping[str, Any], *keys: str) -> dict[str, Any]:
    wanted = {_normal_key(key) for key in keys}
    rows = params.get("_input_rows")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        return {}
    for raw in rows:
        row = _mapping(raw)
        if _normal_key(row.get("key")) in wanted:
            return row
    return {}


def _metric_for_input_key(key: Any, fallback: str | None = None) -> str | None:
    normalized = str(key or "").strip().casefold().replace("_", " ")
    if "eps" in normalized or "per share" in normalized:
        return "eps"
    if "free cash flow" in normalized or normalized in {"fcf", "forecast fcf", "forecast unlevered fcf"}:
        return "free cash flow"
    if "ebitda" in normalized:
        return "ebitda"
    if normalized in {"cash", "excess cash", "cash and equivalents"}:
        return "cash"
    if normalized in {"debt", "total debt"}:
        return "debt"
    if "share" in normalized:
        return "shares"
    if "nav" in normalized or "underlying" in normalized:
        return "nav"
    return fallback


def _eps_basis_parts(value: Any) -> tuple[str | None, str | None, bool]:
    """Extract accounting and share bases from one explicit basis label."""
    text = _normal_text(value)
    if not text:
        return None, None, False
    # "non-GAAP adjusted" is one adjusted accounting basis; classify it
    # before the broader standalone GAAP token so it is not marked ambiguous.
    if "adjusted" in text or "non gaap" in text:
        accounting = ["adjusted"]
    else:
        accounting = [label for label, tokens in {
            "gaap": ("gaap",),
            "ifrs": ("ifrs",),
        }.items() if any(token in text for token in tokens)]
    shares = [label for label in ("diluted", "basic") if label in text]
    ambiguous = len(set(accounting)) > 1 or len(shares) > 1
    return (accounting[0] if len(accounting) == 1 else None, shares[0] if len(shares) == 1 else None, ambiguous)


def _resolve_eps_metadata(params: Mapping[str, Any], facts: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Resolve the typed EPS contract before a method can become supported."""
    eps_row = _input_row_for(params, "forecast_eps", "forecast_diluted_eps", "eps", "forward_diluted_eps")
    pe_row = _input_row_for(params, "exit_pe", "exit_multiple", "pe")
    issues: list[str] = []
    period = str(eps_row.get("period") or params.get("period") or "").strip() or None
    if not period:
        issues.append("Forward EPS requires an explicit fiscal period.")

    accounting, row_share, ambiguous = _eps_basis_parts(eps_row.get("basis"))
    if ambiguous:
        issues.append("Forward EPS accounting/share basis is ambiguous.")
    method_accounting, method_share, method_ambiguous = _eps_basis_parts(params.get("basis"))
    if method_ambiguous:
        issues.append("Forward EPS accounting/share basis is ambiguous.")
    explicit_share = str(eps_row.get("share_basis") or params.get("share_basis") or "").strip().casefold() or None
    if explicit_share and explicit_share not in {"basic", "diluted"}:
        explicit_share = None
        issues.append("Forward EPS share basis must be basic or diluted.")
    if accounting and method_accounting and accounting != method_accounting:
        issues.append("Forward EPS accounting basis aliases disagree.")
    if row_share and explicit_share and row_share != explicit_share:
        issues.append("Forward EPS share basis aliases disagree.")
    accounting = accounting or method_accounting
    share_basis = explicit_share or row_share or method_share

    # When the proposal omits basis fields, derive them only from the exact
    # retained EPS baseline it names.  A generic claim or the key spelling is
    # not enough to invent GAAP or diluted accounting.
    declared_ids = _refs(eps_row.get("fact_claim_ids")) or _refs(params.get("fact_claim_ids"))
    baseline_parts: set[tuple[str, str]] = set()
    eps_value = _number(eps_row.get("value") or params.get("forecast_eps"))
    for identifier in declared_ids:
        fact = facts.get(identifier)
        if not fact or not _metric_matches("eps", fact.get("metric") or fact.get("predicate") or fact.get("measure") or fact.get("fact_type")):
            continue
        if eps_value is not None and _number(fact.get("value")) != eps_value:
            continue
        fact_accounting, fact_share, fact_ambiguous = _eps_basis_parts(fact.get("basis") or fact.get("share_basis"))
        if not fact_ambiguous and fact_accounting and fact_share:
            baseline_parts.add((fact_accounting, fact_share))
    if not accounting:
        if len({part[0] for part in baseline_parts}) == 1:
            accounting = next(iter({part[0] for part in baseline_parts}))
        else:
            issues.append("Forward EPS requires an explicit or unambiguous validated accounting basis.")
    if not share_basis:
        if len({part[1] for part in baseline_parts}) == 1:
            share_basis = next(iter({part[1] for part in baseline_parts}))
        else:
            issues.append("Forward EPS requires an explicit or unambiguous validated share basis.")
    if baseline_parts and (accounting, share_basis) not in baseline_parts:
        issues.append("Forward EPS basis does not match the validated EPS baseline.")
    if not str(params.get("forecast_eps_derivation") or eps_row.get("rationale") or "").strip():
        issues.append("Forward EPS requires a derivation or typed EPS rationale.")
    # The multiple rationale must live on the typed PE row. A generic method
    # rationale cannot certify why this exact exit multiple was chosen.
    if not pe_row or not str(pe_row.get("rationale") or "").strip():
        issues.append("Exit P/E requires a typed rationale.")
    return {
        "period": period,
        "accounting_basis": accounting,
        "share_basis": share_basis,
        "derivation": str(params.get("forecast_eps_derivation") or eps_row.get("rationale") or "").strip(),
        "multiple_rationale": str(pe_row.get("rationale") or params.get("multiple_rationale") or "").strip(),
        "issues": list(dict.fromkeys(issues)),
    }


def _period_contains(baseline: Mapping[str, Any], adjustment: Mapping[str, Any]) -> bool:
    """A quarterly adjustment must belong to the annual earnings baseline."""
    def bounds(fact: Mapping[str, Any]) -> tuple[date, date] | None:
        try:
            start = date.fromisoformat(str(fact.get("period_start") or "")[:10])
            end = date.fromisoformat(str(fact.get("period_end") or "")[:10])
            return (start, end) if start <= end else None
        except ValueError:
            return None
    outer, inner = bounds(baseline), bounds(adjustment)
    if outer and inner:
        return outer[0] <= inner[0] <= inner[1] <= outer[1]
    return bool(_fact_period(baseline) and _fact_period(baseline) == _fact_period(adjustment))


def _derive_eps_forecasts(params: dict[str, Any], facts: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """Compile explicit growth assumptions from a retained EPS observation.

    No model-authored forecast arithmetic is trusted. A missing future
    consensus estimate is not a reason to omit a research forecast, but an
    unverified historical baseline remains an evidence error.
    """
    row = _input_row_for(params, "baseline_eps")
    if not row and params.get("baseline_eps") is None:
        return []
    if not row or row.get("kind") != "fact" or len(_refs(row.get("fact_claim_ids"))) != 1:
        return ["Derived EPS requires one explicit baseline_eps fact row and its validated fact ID."]
    identifier = _refs(row.get("fact_claim_ids"))[0]
    baseline = facts.get(identifier)
    if not baseline:
        return ["The EPS baseline is not a validated fact in this attempt."]
    value = _number(row.get("value"))
    if value is None or value <= 0:
        return ["Positive baseline EPS is required; use an appropriate EV, DCF or asset method for a loss-making issuer."]
    if params.get("baseline_eps") is not None and _number(params["baseline_eps"]) != value:
        return ["The baseline EPS scalar and factual row disagree."]
    ok, reason = _fact_matches(baseline, value=value, expected_metric="eps", expected_dimension="currency_per_share", currency=row.get("currency"), period=row.get("period"), basis=row.get("basis"))
    if not ok:
        return [str(reason)]
    accounting, share, ambiguous = _eps_basis_parts(row.get("basis") or baseline.get("basis"))
    if ambiguous or accounting not in {"gaap", "ifrs", "adjusted"} or share != "diluted":
        return ["The baseline requires an explicit accounting basis and diluted share basis."]
    currency = _fact_currency(baseline)
    if not currency or params.get("currency") and str(params["currency"]).upper() != currency:
        return ["The baseline currency must match the valuation output currency."]
    period = str(params.get("period") or "").strip()
    if not period:
        return ["The derived forecast requires an explicit fiscal period."]
    raw_months = params.get("horizon_months")
    try:
        months = 12 if raw_months is None else int(raw_months)
        if str(months) != str(raw_months) and raw_months is not None or not 1 <= months <= 120:
            raise ValueError
    except (TypeError, ValueError):
        return ["The valuation horizon must be an integer from 1 to 120 months."]
    normalized = value
    audit_rows = [dict(row)]
    adjustment_steps: list[str] = []
    used = {identifier}
    for raw in params.get("normalization_adjustments") or []:
        adjustment = _mapping(raw)
        adjustment_ids = _refs(adjustment.get("fact_claim_ids"))
        fact_id = adjustment_ids[0] if len(adjustment_ids) == 1 else ""
        fact = facts.get(fact_id)
        operation = str(adjustment.get("operation") or "")
        rationale = str(adjustment.get("rationale") or "").strip()
        amount = _number(fact.get("value")) if fact else None
        if not fact or fact_id in used or amount is None or amount < 0 or operation not in {"add", "subtract"} or not rationale:
            return ["Each EPS adjustment requires a distinct validated nonnegative per-share fact, operation and rationale."]
        if not _fact_matches(fact, expected_metric="eps", expected_dimension="currency_per_share", currency=currency)[0]:
            return ["Every EPS adjustment must be a validated earnings-per-share impact in the baseline currency."]
        adj_accounting, adj_share, adj_ambiguous = _eps_basis_parts(fact.get("basis"))
        if adj_ambiguous or (adj_accounting, adj_share) != (accounting, share) or not _period_contains(baseline, fact):
            return ["EPS adjustment accounting/share basis must match and its fiscal period must be contained in the baseline."]
        normalized = normalized + amount if operation == "add" else normalized - amount
        used.add(fact_id)
        audit_rows.append(_method_input(f"eps_adjustment_{len(adjustment_steps) + 1}", amount, unit=fact.get("unit"), currency=currency, period=_fact_period(fact), basis=fact.get("basis"), kind="fact", fact_claim_ids=[fact_id], source_refs=[fact.get("source_ref")], rationale=rationale))
        adjustment_steps.append(f"{operation} {quantize(amount)}: {rationale}")
    if normalized <= 0:
        return ["EPS after the specified adjustments is not positive; use an appropriate EV, DCF or asset method."]
    label = "Excludes only the specified items; this is not a claim that earnings are fully normalized." if adjustment_steps else "Reported baseline; no normalization adjustments assumed."
    scenarios = params.get("scenarios")
    if not isinstance(scenarios, Mapping) or any(not isinstance(scenarios.get(name), Mapping) for name in ("bear", "base", "bull")):
        return ["Derived EPS requires explicit bear, base and bull growth and multiple assumptions."]
    compiled: dict[str, Any] = {}
    calculations: dict[str, Any] = {}
    forecast_basis = "adjusted diluted" if adjustment_steps else f"{accounting} diluted"
    years = _number(params.get("forecast_years")) if params.get("forecast_years") is not None else Decimal(months) / Decimal(12)
    span_rationale = str(params.get("forecast_span_rationale") or "").strip()
    if years is None or years <= 0 or years > 10 or params.get("forecast_years") is not None and not span_rationale:
        return ["Explicit forecast_years must be positive, no more than ten years, and explain the baseline-to-forecast fiscal span."]
    # The holding horizon and the baseline-to-forecast earnings span are
    # separate clocks, but an explanation cannot override contradictory
    # fiscal labels. Do not infer a fiscal year from a calendar end date or
    # force a fractional/NTM forecast into an annual fiscal-year convention.
    def annual_fiscal_year(label: Any) -> int | None:
        import re

        text = str(label or "")
        if re.search(r"\b(?:Q[1-4]|[1-4]Q|quarter\w*|TTM|NTM|LTM)\b|(?:next|trailing|last)\s+(?:12|twelve)\s+months?", text, re.I):
            return None
        labels = re.findall(r"\b(?:FY\s*|fiscal\s+year\s+)(20\d{2})(?:[EA])?\b", text, re.I)
        return int(labels[0]) if labels and len(set(labels)) == 1 else None

    baseline_year = annual_fiscal_year(row.get("period") or _fact_period(baseline))
    forecast_year = annual_fiscal_year(period)
    if baseline_year is not None and forecast_year is not None:
        fiscal_span = forecast_year - baseline_year
        if fiscal_span <= 0 or years != Decimal(fiscal_span):
            return [f"The annual EPS span from FY{baseline_year} to FY{forecast_year} requires {fiscal_span} annual growth steps; forecast_years {years} is inconsistent. The holding horizon is separate from this fiscal earnings span."]
    for name in ("bear", "base", "bull"):
        scenario = scenarios[name]
        growth = _number(scenario.get("growth_rate"))
        multiple = _number(_scenario_value(scenario, "exit_multiple", "pe", "multiple"))
        growth_reason = str(scenario.get("growth_rationale") or "").strip()
        multiple_reason = str(scenario.get("multiple_rationale") or "").strip()
        if growth is None or growth <= -_ONE or multiple is None or multiple <= 0 or not growth_reason or not multiple_reason:
            return [f"{name} requires annual EPS growth > -100%, positive exit P/E and a rationale for each research assumption."]
        try:
            forecast = normalized * ((_ONE + growth) ** years)
            forecast_text = quantize(forecast)
            target = quantize(Decimal(forecast_text) * multiple)
        except (InvalidOperation, OverflowError):
            return [f"{name} forecast is outside supported finite arithmetic."]
        supplied = _scenario_value(scenario, "forecast_eps", "eps", "diluted_eps")
        if _scenario_aliases_conflict(scenario, "forecast_eps", "eps", "diluted_eps") or supplied is not None and _number(supplied) != _number(forecast_text):
            return [f"{name} supplied forward EPS disagrees with the code-derived forecast."]
        derivation = f"{quantize(normalized)} × (1 + {quantize(growth)})^{quantize(years)}. {span_rationale} {label} Research assumption: {growth_reason}"
        inputs = [*audit_rows,
            _method_input("annual_eps_growth", growth, unit="fraction", period=period, kind="assumption", rationale=growth_reason),
            _method_input("forecast_diluted_eps", forecast_text, unit=f"{currency}/share", currency=currency, period=period, basis=forecast_basis, kind="assumption", source_refs=params.get("source_refs") or [], rationale=derivation),
            _method_input("exit_pe", multiple, unit="multiple", period=period, kind="assumption", rationale=multiple_reason),
        ]
        compiled[name] = {"forecast_eps": forecast_text, "exit_multiple": str(multiple), "multiple_rationale": multiple_reason}
        calculations[name] = {"inputs": inputs, "formula": "(baseline_EPS ± specified_EPS_adjustments) × (1 + annual_EPS_growth)^forecast_years × exit_P/E", "steps": [f"Reported EPS: {quantize(value)}", *adjustment_steps, f"EPS after specified adjustments: {quantize(normalized)}", derivation, f"{forecast_text} × {quantize(multiple)} = {target}"], "intermediate_results": {"reported_eps": quantize(value), "adjusted_baseline_eps": quantize(normalized), "normalization_label": label, "annual_eps_growth": quantize(growth), "forecast_diluted_eps": forecast_text, "exit_pe": quantize(multiple), "horizon_months": months, "forecast_years": quantize(years), "forecast_span_rationale": span_rationale}, "output_price": target}
    prices = [_number(calculations[name]["output_price"]) for name in ("bear", "base", "bull")]
    if prices != sorted(prices):
        return ["Bear, base and bull targets must be ordered; revise the explicit assumptions."]
    base = compiled["base"]
    if params.get("forecast_eps") is not None and _number(params["forecast_eps"]) != _number(base["forecast_eps"]):
        return ["The supplied base forward EPS disagrees with the code-derived forecast."]
    if params.get("exit_multiple") is not None and _number(params["exit_multiple"]) != _number(base["exit_multiple"]):
        return ["The supplied base exit multiple disagrees with the scenario assumption."]
    base_inputs = calculations["base"]["inputs"]
    params.update(forecast_eps=base["forecast_eps"], exit_multiple=base["exit_multiple"], scenarios=compiled, basis=forecast_basis, share_basis="diluted", forecast_eps_derivation=base_inputs[-2]["rationale"], horizon_months=months)
    params["_input_rows"] = [*base_inputs, *[row for row in params.get("_input_rows", []) if row.get("key") not in {item["key"] for item in base_inputs}]]
    params["fact_claim_ids"] = _refs([*params.get("fact_claim_ids", []), *sorted(used)])
    params["source_refs"] = _refs([*params.get("source_refs", []), *[facts[key].get("source_ref") for key in sorted(used)]])
    params["_derived_eps_calculations"] = calculations
    return []


def _method_input_number(method: Mapping[str, Any], *keys: str) -> Decimal | None:
    """Read one numeric input from the code-generated method record.

    Sensitivities are calculated from the values that this module emitted,
    rather than from a provider's parallel copy of the assumptions.  This is
    deliberately a small helper: it does not make a missing or malformed
    input executable.
    """
    wanted = {str(key).strip().casefold() for key in keys}
    raw_inputs = method.get("inputs")
    if not isinstance(raw_inputs, Sequence) or isinstance(raw_inputs, (str, bytes)):
        return None
    for raw in raw_inputs:
        row = _mapping(raw)
        if str(row.get("key") or "").strip().casefold() in wanted:
            return _number(row.get("value"))
    return None


def _sensitivity_row(value: Decimal, output: Any) -> dict[str, Any]:
    """Return one conditional, probability-free sensitivity point."""
    output_value = _number(output)
    return {
        "value": quantize(value),
        "output_prices": {"base": quantize(output_value)} if output_value is not None else {},
    }


def _conditional_method_sensitivities(
    method: Mapping[str, Any],
    params: Mapping[str, Any],
    *,
    source_refs: Sequence[Any],
    fact_claim_ids: Sequence[Any],
    currency: str | None,
    as_of: str | None,
) -> list[dict[str, Any]]:
    """Build a bounded, code-owned sensitivity grid for one method.

    These rows are conditional arithmetic around the supplied base inputs;
    they are not probabilities, forecasts, or alternative target selection.
    A sensitivity is only emitted for a method that has already passed the
    semantic/source gate.  The method's selected base target is never changed
    by this grid.
    """
    if method.get("supported") is not True or method.get("status") != "complete":
        return []
    name = str(method.get("name") or "").casefold()
    if name == "eps_multiple":
        eps = _method_input_number(method, "forecast_diluted_eps", "forecast_eps", "eps")
        multiple = _method_input_number(method, "exit_pe", "exit_multiple", "pe")
        if eps is None or multiple is None or eps <= _ZERO or multiple <= _ZERO:
            return []
        points: list[dict[str, Any]] = []
        # The exit multiple is an explicit assumption in this method.  Keep a
        # fixed, narrow ±20% conditional range and make the lack of weighting
        # explicit to callers.
        for factor in (Decimal("0.8"), _ONE, Decimal("1.2")):
            candidate_multiple = multiple * factor
            points.append(_sensitivity_row(candidate_multiple, eps * candidate_multiple))
        result = [{
            "method": "eps_multiple",
            "kind": "conditional_assumption_sensitivity",
            "parameter": "exit_pe",
            "base_value": quantize(multiple),
            "values": points,
            "status": "complete",
            "probability": None,
            "rationale": "Conditional ±20% exit-multiple arithmetic; it is not a probability-weighted forecast or target selection.",
            "source_refs": _refs(source_refs),
        }]
        derived = params.get("_derived_eps_calculations")
        if isinstance(derived, Mapping):
            base_results = derived["base"]["intermediate_results"]
            normalized = _number(base_results["adjusted_baseline_eps"])
            years = Decimal(base_results["forecast_years"])
            grid: list[dict[str, str]] = []
            for growth_case in ("bear", "base", "bull"):
                growth = _number(derived[growth_case]["intermediate_results"]["annual_eps_growth"])
                forecast = Decimal(quantize(normalized * ((_ONE + growth) ** years)))
                for multiple_case in ("bear", "base", "bull"):
                    pe = _number(derived[multiple_case]["intermediate_results"]["exit_pe"])
                    grid.append({"growth_case": growth_case, "multiple_case": multiple_case, "annual_eps_growth": quantize(growth), "exit_pe": quantize(pe), "output_price": quantize(forecast * pe)})
            result.append({"method": "eps_multiple", "kind": "conditional_assumption_sensitivity", "parameter": "annual_eps_growth_and_exit_pe", "values": grid, "status": "complete", "probability": None, "rationale": "Nine combinations of the explicitly proposed growth and multiple assumptions; these are conditional outcomes, not probabilities.", "source_refs": _refs(source_refs)})
        return result
    if name in {"dcf", "discounted_cash_flow"}:
        discount_rate = _number(params.get("discount_rate", params.get("wacc")))
        terminal_growth = _number(params.get("terminal_growth", params.get("g")))
        if discount_rate is None or terminal_growth is None:
            return []
        forecast_fcf = params.get("forecast_fcf", params.get("fcf", params.get("free_cash_flow")))
        bridge = {
            "excess_cash": params.get("excess_cash", params.get("cash")),
            "debt": params.get("debt"),
            "preferred_claims": params.get("preferred_claims", params.get("preferred")),
            "minority_interest": params.get("minority_interest", params.get("minority")),
            "diluted_shares": params.get("diluted_shares", params.get("shares")),
            "adr_ratio": params.get("adr_ratio", "1"),
        }
        convention = str(params.get("discount_convention") or "year_end")
        rows: list[dict[str, Any]] = []
        # Keep the grid small and explicit: each axis is a one percentage
        # point conditional perturbation, with a validity check for r > g.
        for key, base in (("discount_rate", discount_rate), ("terminal_growth", terminal_growth)):
            points: list[dict[str, Any]] = []
            for delta in (Decimal("-0.01"), _ZERO, Decimal("0.01")):
                candidate_rate = discount_rate + delta if key == "discount_rate" else discount_rate
                candidate_growth = terminal_growth + delta if key == "terminal_growth" else terminal_growth
                if candidate_rate <= -_ONE or candidate_growth >= candidate_rate:
                    continue
                trial = calculate_dcf(
                    forecast_fcf,
                    candidate_rate,
                    candidate_growth,
                    **bridge,
                    currency=currency,
                    source_refs=source_refs,
                    fact_claim_ids=fact_claim_ids,
                    discount_convention=convention,
                    as_of=as_of,
                )
                candidate_price = (trial.get("output_prices") or {}).get("base")
                points.append(_sensitivity_row(base + delta, candidate_price))
            if points:
                rows.append({
                    "method": "dcf",
                    "kind": "conditional_assumption_sensitivity",
                    "parameter": key,
                    "base_value": quantize(base),
                    "values": points,
                    "status": "complete",
                    "probability": None,
                    "rationale": "Conditional one percentage-point discount/growth arithmetic; it is not a probability-weighted forecast or target selection.",
                    "source_refs": _refs(source_refs),
                })
        return rows
    return []


def build_valuation(
    assumptions: Mapping[str, Any] | Any | None,
    *,
    asset_class: str | None = None,
    currency: str | None = None,
    horizon: str | None = None,
    as_of: str | None = None,
    source_refs: Sequence[Any] = (),
    validated_facts: Mapping[str, Any] | Sequence[Any] | None = None,
    fact_claims: Mapping[str, Any] | Sequence[Any] | None = None,
    issuer: str | None = None,
    source_records: Mapping[str, Any] | None = None,
    research_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the canonical valuation block from explicit method proposals."""
    root = _assumption_mapping(assumptions)
    asset = str(asset_class or root.get("asset_class") or "equity").strip().lower()
    fact_values, rejected_facts = _fact_index(validated_facts if validated_facts is not None else fact_claims)
    # Corporate methods require issuer financial statements.  Funds, broad
    # macro views and commodity/underlying cases must use a supported NAV or
    # underlying asset value instead of accepting fabricated EPS/DCF inputs.
    non_corporate_asset = any(token in asset for token in ("fund", "etf", "trust", "commodity", "macro", "index", "fx", "currency"))
    specs = _method_specs(root.get("methods"))
    if not specs:
        # Funds and macro/commodity cases still need an asset-appropriate
        # method; never fabricate corporate accounts just to fill a field.
        if any(token in asset for token in ("fund", "etf", "trust", "commodity", "macro", "index")):
            specs = [{"name": "nav" if any(token in asset for token in ("fund", "etf", "trust")) else "underlying"}]
    methods: list[dict[str, Any]] = []
    method_contexts: list[tuple[dict[str, Any], dict[str, Any], list[str], list[str]]] = []
    for spec in specs:
        name = str(spec.get("name") or "").strip().lower().replace("-", "_").replace(" ", "_")
        params = _merge_params(spec, root)
        # The candidate's requested output currency is code-owned. A method
        # or provider row cannot switch the bridge into JPY/EUR while the
        # entry/portfolio is denominated in USD.
        if currency:
            params["currency"] = currency
        else:
            params.setdefault("currency", currency)
        refs = _refs(params.get("source_refs") or root.get("source_refs") or source_refs)
        facts = _refs(params.get("fact_claim_ids") or root.get("fact_claim_ids"))
        scenario_rows = params.get("scenarios") or root.get("scenarios")
        # Provider ``supported`` is intentionally ignored.  Only the
        # repository-enriched fact resolver below can mark a method usable.
        supported = None
        if non_corporate_asset and name in {"eps", "eps_multiple", "pe", "earnings_multiple", "dcf", "discounted_cash_flow", "ev", "ev_multiple", "enterprise_multiple", "ebitda_multiple", "ps_multiple", "ev_ebitda"}:
            method = _base_method(
                name,
                status="not_applicable",
                reasons=["Corporate issuer valuation is not asset-appropriate here; use supported NAV or underlying asset value."],
                source_refs=refs,
            )
            methods.append(method)
            continue
        if name in {"ps_multiple", "ev_ebitda", "nav_multiple"}:
            from .comparable_valuation import calculate_comparable
            params["scenarios"] = scenario_rows
            method = calculate_comparable(name, params, facts=fact_values, sources=source_records,
                                         issuer=issuer, as_of=as_of, currency=currency,
                                         observed_peers=(research_context or {}).get("peer_comparisons") or [])
        elif name in {"eps", "eps_multiple", "pe", "earnings_multiple"}:
            if scenario_rows:
                params["scenarios"] = scenario_rows
            derivation_issues = _derive_eps_forecasts(params, fact_values)
            if derivation_issues:
                methods.append(_base_method("eps_multiple", reasons=derivation_issues, source_refs=refs))
                continue
            refs = _refs(params.get("source_refs") or refs)
            facts = _refs(params.get("fact_claim_ids") or facts)
            scenario_rows = params.get("scenarios") or scenario_rows
            eps_row = _input_row_for(params, "forecast_eps", "forecast_diluted_eps", "eps", "forward_diluted_eps")
            eps_metadata = _resolve_eps_metadata(params, fact_values)
            params["_eps_metadata_issues"] = eps_metadata["issues"]
            method = calculate_eps_multiple(
                params.get("forecast_eps"),
                params.get("exit_multiple"),
                scenarios=scenario_rows,
                currency=params.get("currency", currency),
                period=params.get("period"),
                basis=eps_metadata.get("accounting_basis") or params.get("basis"),
                share_basis=eps_metadata.get("share_basis") or params.get("share_basis"),
                forecast_eps_kind=str(eps_row.get("kind") or "fact"),
                forecast_eps_unit=eps_row.get("unit"),
                forecast_eps_currency=eps_row.get("currency"),
                forecast_eps_period=eps_metadata.get("period") or eps_row.get("period") or params.get("period"),
                forecast_eps_scale=eps_row.get("scale") or params.get("scale"),
                forecast_eps_basis=eps_metadata.get("accounting_basis") or eps_row.get("basis") or params.get("basis") or params.get("share_basis"),
                forecast_eps_statement_type=eps_row.get("statement_type"),
                forecast_eps_derivation=eps_metadata.get("derivation") or str(params.get("forecast_eps_derivation") or eps_row.get("rationale") or ""),
                multiple_rationale=eps_metadata.get("multiple_rationale") or str(params.get("multiple_rationale") or params.get("rationale") or ""),
                source_refs=refs,
                fact_claim_ids=facts,
                supported=supported,
                as_of=as_of,
            )
        elif name in {"dcf", "discounted_cash_flow"}:
            method = calculate_dcf(params.get("forecast_fcf", params.get("fcf", params.get("free_cash_flow"))), params.get("discount_rate", params.get("wacc")), params.get("terminal_growth", params.get("g")), excess_cash=params.get("excess_cash", params.get("cash")), debt=params.get("debt"), preferred_claims=params.get("preferred_claims", params.get("preferred")), minority_interest=params.get("minority_interest", params.get("minority")), diluted_shares=params.get("diluted_shares", params.get("shares")), adr_ratio=params.get("adr_ratio", "1"), scenarios=scenario_rows, currency=params.get("currency", currency), source_refs=refs, fact_claim_ids=facts, supported=supported, discount_convention=str(params.get("discount_convention") or "year_end"), as_of=as_of)
        elif name in {"ev", "ev_multiple", "enterprise_multiple", "ebitda_multiple"}:
            method = calculate_ev_multiple(params.get("forecast_metric", params.get("ebitda", params.get("metric"))), params.get("exit_ev_multiple", params.get("exit_multiple", params.get("ev_multiple", params.get("multiple")))), metric_name=str(params.get("metric_name") or "EBITDA"), excess_cash=params.get("excess_cash", params.get("cash")), debt=params.get("debt"), preferred_claims=params.get("preferred_claims", params.get("preferred")), minority_interest=params.get("minority_interest", params.get("minority")), diluted_shares=params.get("diluted_shares", params.get("shares")), adr_ratio=params.get("adr_ratio", "1"), currency=params.get("currency", currency), scenarios=scenario_rows, source_refs=refs, fact_claim_ids=facts, supported=supported, as_of=as_of)
        elif name in {"nav", "underlying", "underlying_asset", "asset_value"}:
            method = calculate_nav(params.get("nav_per_unit", params.get("underlying_value")), scenarios=scenario_rows, currency=params.get("currency", currency), source_refs=refs, fact_claim_ids=facts, supported=supported, as_of=as_of)
            if name != "nav":
                method["name"] = "underlying"
        else:
            method = _base_method(name or "unknown", status="not_applicable", reasons=[f"Valuation method {name or 'unknown'} is not supported for this asset."])
        method["rationale"] = str(params.get("rationale") or root.get("rationale") or "")
        # Retain the exact factual baseline and every declared assumption.
        # Calculator-generated values supplement, rather than erase, the
        # typed audit rows supplied to the support validator.
        typed_rows = list(params.get("_input_rows") or [])
        typed_keys = {str(row.get("key")) for row in typed_rows}
        if typed_rows:
            method["inputs"] = [*typed_rows, *[row for row in method.get("inputs", []) if str(row.get("key")) not in typed_keys]]
            def audit_key(value: Any) -> str:
                normalized = _normal_key(value)
                return next((canonical for canonical, aliases in _METHOD_INPUT_ALIASES.items() if normalized in {_normal_key(alias) for alias in aliases}), normalized)
            by_key = {audit_key(row.get("key")): row for row in typed_rows}
            for calculation in method.get("scenario_calculations", {}).values():
                audited: list[dict[str, Any]] = []
                for calculated_input in calculation.get("inputs", []):
                    original = by_key.get(audit_key(calculated_input.get("key")))
                    if original and _values_agree(original.get("value"), calculated_input.get("value")):
                        audited.append({**calculated_input, **original})
                    elif original:
                        audited.append({**original, **calculated_input, "kind": "assumption", "rationale": calculated_input.get("rationale") or original.get("rationale") or "Explicit scenario assumption relative to the dated baseline."})
                    else:
                        audited.append(calculated_input)
                calculation["inputs"] = audited
        if params.get("_derived_eps_calculations"):
            method["scenario_calculations"] = params["_derived_eps_calculations"]
            method["intermediate_results"].update(params["_derived_eps_calculations"]["base"]["intermediate_results"])
            method["steps"] = params["_derived_eps_calculations"]["base"]["steps"]
            method["formula"] = params["_derived_eps_calculations"]["base"]["formula"]
        conflicts = _refs(params.get("_input_conflicts"))
        if conflicts:
            # A calculation that became unavailable because aliases disagree
            # still needs an explicit audit reason; otherwise the gate only
            # sees the downstream missing-value message and loses the cause.
            method["supported"] = False
            if method.get("status") != "complete":
                method.setdefault("reasons", []).append(
                    "Conflicting raw, alias and typed inputs were supplied for: "
                    + ", ".join(conflicts)
                    + "."
                )
        if method.get("status") == "complete" and name not in {"ps_multiple", "ev_ebitda", "nav_multiple"}:
            bound, support_reason = _validated_method_support(method, params=params, facts=fact_values, rejected_facts=rejected_facts, source_refs=refs, issuer=issuer, source_records=source_records, as_of=as_of)
            method["supported"] = bound
            if bound:
                method["reasons"] = [reason for reason in method.get("reasons", []) if "code-validated fact binding" not in reason and "provider support flag is not authoritative" not in reason]
            elif support_reason:
                method.setdefault("reasons", []).append(support_reason)
        methods.append(method)
        method_contexts.append((method, params, refs, facts))
    complete = [item for item in methods if item.get("status") == "complete" and item.get("supported", True)]
    arithmetic_complete = [item for item in methods if item.get("status") == "complete"]
    status = "complete" if complete else "partial" if arithmetic_complete or methods else "unavailable"
    missing: list[str] = []
    for method in methods:
        missing.extend(str(item) for item in method.get("missing_inputs", []) if str(item))
        if method.get("status") != "complete":
            missing.extend(str(item) for item in method.get("reasons", []) if str(item))
    selected = complete[0] if complete else None
    if selected and not currency:
        currencies = {str(row.get("currency")).upper() for row in selected.get("inputs", []) if row.get("currency")}
        if len(currencies) == 1:
            currency = next(iter(currencies))
    scenario_values: dict[str, str] = {}
    if selected:
        scenario_values.update(selected.get("output_prices") or {})
    base_prices: dict[str, str] = {}
    for method in methods:
        if method.get("supported") is not True:
            continue
        for name, value in (method.get("output_prices") or {}).items():
            base_prices.setdefault(name, value)
    method_bases: dict[str, str] = {str(item.get("name")): str((item.get("output_prices") or {}).get("base")) for item in methods if item.get("supported") is True and (item.get("output_prices") or {}).get("base") is not None}
    dispersion: dict[str, Any] = {"base_prices": method_bases, "unsupported_methods": [str(item.get("name")) for item in methods if item.get("status") == "complete" and item.get("supported") is not True]}
    if len(method_bases) >= 2:
        numbers = [_number(value) for value in method_bases.values()]
        numbers = [value for value in numbers if value is not None]
        if numbers:
            dispersion.update({"minimum": quantize(min(numbers)), "maximum": quantize(max(numbers)), "range": quantize(max(numbers) - min(numbers))})
    sensitivities: list[dict[str, Any]] = []
    for method, params, refs, facts in method_contexts:
        sensitivities.extend(
            _conditional_method_sensitivities(
                method,
                params,
                source_refs=refs,
                fact_claim_ids=facts,
                currency=currency,
                as_of=as_of,
            )
        )
    if not sensitivities:
        sensitivities = [{
            "method": selected.get("name") if selected else None,
            "kind": "conditional_assumption_sensitivity",
            "status": "unavailable",
            "values": [],
            "probability": None,
            "reason": "No supported method supplied enough explicit numeric assumptions for a bounded sensitivity grid.",
        }]
    rationale_candidate = root.get("reconciliation_rationale")
    if not rationale_candidate and selected:
        rationale_candidate = selected.get("reconciliation_rationale")
    if not rationale_candidate and selected:
        if len(complete) > 1:
            rationale_candidate = f"Selected {selected.get('name')} as the displayed base method; other supported methods remain separate cross-checks and are not averaged."
        else:
            rationale_candidate = f"Selected {selected.get('name')} as the sole supported method; no cross-method reconciliation was performed."
    reconciliation_rationale = str(rationale_candidate).strip() if rationale_candidate is not None else None
    if selected and selected.get("intermediate_results", {}).get("horizon_months"):
        horizon = f"{selected['intermediate_results']['horizon_months']} months"
    # This context is a code-owned argument, never a provider assumption.
    # Today's implied price applies the chosen multiples to currently
    # reported earnings. It is not a present value of the future target.
    context = dict(research_context) if isinstance(research_context, Mapping) else None
    comparable_today = (selected or {}).get("intermediate_results", {}).get("implied_today")
    if comparable_today:
        context = {**(context or {}), "implied_today": comparable_today}
    if context is not None and not comparable_today:
        current = context.get("current_earnings") or {}
        current_eps = _number(current.get("value"))
        today = {"status": "unavailable", "as_of": as_of, "scenarios": {},
                 "earnings_basis": "Latest reported trailing GAAP diluted EPS", "source_refs": current.get("source_refs") or [],
                 "formula": "Latest reported trailing diluted EPS × selected scenario P/E", 
                 "coverage_note": "Value on current reported earnings using the selected scenario multiples; no growth or discount rate is applied. This is not a market quote or a discounted future price target."}
        if selected and selected.get("name") in {"eps", "eps_multiple", "pe", "earnings_multiple"} and current.get("status") == "available" and current_eps is not None and current_eps > 0 and current.get("currency") == currency:
            for name, calculation in (selected.get("scenario_calculations") or {}).items():
                multiple = _number((calculation.get("intermediate_results") or {}).get("exit_pe"))
                if multiple is not None and multiple > 0:
                    today["scenarios"][name] = quantize(current_eps * multiple)
            today.update(status="available" if today["scenarios"] else "unavailable", eps=quantize(current_eps),
                         period_end=current.get("period_end"), earnings_formula=current.get("formula"), currency=currency)
        if today["status"] == "unavailable":
            today["coverage_note"] = current.get("reason") or "A supported P/E method and a currency-matched current reported earnings series are required for today's implied value."
        context["implied_today"] = today
    payload = {"methods": methods, "scenarios": scenario_values, "sensitivities": sensitivities, "reconciliation_rationale": reconciliation_rationale, "asset_class": asset, "currency": currency, "horizon": horizon, "as_of": as_of, "research_context": context}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
    return {"status": status, "as_of": _as_of(as_of), "horizon": _as_of(horizon), "currency": currency, "methods": methods, "scenarios": scenario_values or base_prices, "sensitivities": sensitivities, "missing_inputs": list(dict.fromkeys(missing)), "code_version": VALUATION_CODE_VERSION, "input_hash": digest, "dispersion": dispersion, "selected_method": selected.get("name") if selected else None, "reconciliation_rationale": reconciliation_rationale, "rationale": str(root.get("rationale") or (selected or {}).get("rationale") or ""), "research_context": context}


def calculate_payoff(
    valuation: Mapping[str, Any] | Any | None,
    reference_entry: Any = None,
    *,
    direction: str = "long",
    horizon: str | None = None,
    stop_price: Any = None,
    shares: int | None = None,
    costs: Mapping[str, Any] | None = None,
    opportunity_cost: Mapping[str, Any] | None = None,
    source_refs: Sequence[Any] = (),
) -> dict[str, Any]:
    """Translate scenario target prices into directional, price-only payoff."""
    entry = _number(reference_entry)
    direction = str(direction or "long").lower()
    block: dict[str, Any] = {"status": "unavailable", "reference_entry": quantize(entry), "direction": direction if direction in {"long", "short"} else None, "horizon": horizon, "scenarios": [], "reward_to_risk": None, "breakeven_price": quantize(entry), "opportunity_cost": dict(opportunity_cost) if isinstance(opportunity_cost, Mapping) else {"status": "unavailable", "alternative": "cash", "reason": "Opportunity-cost terms were not supplied.", "benchmark_status": "unavailable", "benchmark_expected_return": None, "source_refs": [], "missing_inputs": ["opportunity_cost"], "limitations": [], "code_version": "opportunity-cost.v1"}, "missing_inputs": [], "limitations": []}
    if direction not in {"long", "short"}:
        block["missing_inputs"] = ["direction"]
        return block
    if entry is None or entry <= _ZERO:
        block["missing_inputs"] = ["reference_entry"]
        return block
    raw = valuation if isinstance(valuation, Mapping) else getattr(valuation, "model_dump", lambda **_: {})()
    scenario_values = raw.get("scenarios") if isinstance(raw, Mapping) else {}
    if not isinstance(scenario_values, Mapping):
        scenario_values = {}
    rows: list[dict[str, Any]] = []
    has_unknown_cost = False
    for name in ("bear", "base", "bull"):
        target = _number(scenario_values.get(name))
        if target is None:
            continue
        price_return = (target - entry) / entry if direction == "long" else (entry - target) / entry
        pnl_per_share = target - entry if direction == "long" else entry - target
        row_costs: dict[str, str | None] = {}
        cost_total = _ZERO
        cost_spec = costs.get(name, costs) if isinstance(costs, Mapping) else None
        if isinstance(cost_spec, Mapping):
            for key in ("fees", "dividends", "borrow_cost", "fx_cost"):
                # An empty cost mapping is not evidence that costs are zero.
                # Every omitted component remains unknown and keeps the
                # payoff explicitly price-only.
                if key not in cost_spec:
                    has_unknown_cost = True
                    row_costs[key] = None
                    continue
                value = _number(cost_spec.get(key))
                row_costs[key] = quantize(value) if value is not None else None
                if value is None:
                    has_unknown_cost = True
                elif value is not None:
                    cost_total += value
        else:
            for key in ("fees", "dividends", "borrow_cost", "fx_cost"):
                row_costs[key] = None
            has_unknown_cost = True
        net_return = None if has_unknown_cost else quantize(price_return - cost_total / entry)
        planned_pnl = pnl_per_share * shares if isinstance(shares, int) and shares >= 0 else None
        rows.append({"name": name, "target_price": quantize(target), "price_return": quantize(price_return), "estimated_costs": row_costs, "net_return": net_return, "pnl_per_share": quantize(pnl_per_share), "planned_pnl": quantize(planned_pnl), "source_refs": _refs(source_refs)})
    if not rows:
        block["missing_inputs"] = ["valuation_scenarios"]
        return block
    block["status"] = "complete"
    block["scenarios"] = rows
    if has_unknown_cost:
        block["limitations"].append("Unsupported fees, dividends, borrow or FX costs leave net return unavailable; price return remains shown.")
    stop = _number(stop_price)
    if stop is not None and stop > _ZERO:
        risk = (entry - stop) if direction == "long" else (stop - entry)
        if risk > _ZERO:
            base = next((row for row in rows if row["name"] == "base"), rows[0])
            reward = _number(base.get("pnl_per_share"))
            if reward is not None:
                block["reward_to_risk"] = quantize(reward / risk)
        else:
            block["missing_inputs"].append("valid_stop_for_reward_risk")
    else:
        block["missing_inputs"].append("stop_price_for_reward_risk")
    return block


# Friendly aliases used by the decision projection and focused tests.
compute_eps_multiple = calculate_eps_multiple
compute_dcf = calculate_dcf
compute_ev_multiple = calculate_ev_multiple
compute_nav = calculate_nav
compute_valuation = build_valuation
build_payoff = calculate_payoff
calculate_payoff_scenarios = calculate_payoff


__all__ = [
    "VALUATION_CODE_VERSION",
    "source_has_primary_coverage",
    "calculate_eps_multiple",
    "calculate_dcf",
    "calculate_ev_multiple",
    "calculate_nav",
    "build_valuation",
    "calculate_payoff",
    "compute_eps_multiple",
    "compute_dcf",
    "compute_ev_multiple",
    "compute_nav",
    "compute_valuation",
    "build_payoff",
    "calculate_payoff_scenarios",
]
