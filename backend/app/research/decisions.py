"""Current decision projection built from verified research and code services.

The provider output remains an immutable working report.  This module creates
the small object consumed by decision views: one outcome per candidate, typed
entry/target/watch fields, code-owned sizing, and explicit operational state.
It is intentionally pure so the repository can persist the returned object
without giving this projection a second source of truth.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import re
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ValidationError

from ..schemas import (
    ActionPlan,
    AgentOutputPayload,
    CatalystEvent,
    CanonicalCandidateDecision,
    CanonicalCaseDecision,
    DecisionBlocker,
    DecisionEvidence,
    DecisionSizing,
    ForecastAcceptance,
    FutureTarget,
    LifecycleBlock,
    PayoffBlock,
    PortfolioCheck,
    PortfolioContext,
    PriceRange,
    RecommendationCheck,
    RecommendationGate,
    ThesisBlock,
    ValuationBlock,
    WatchTrigger,
)
from .calculations import (
    D,
    _convert_amount,
    calculate_multi_candidate_sizing,
    calculate_position_size,
    calculate_technical_indicators,
    quantize,
)
from .portfolio_risk import build_opportunity_cost, build_portfolio_context
from .valuation import _issuer_matches, build_payoff, build_valuation, source_has_primary_coverage
from .freshness import evaluate_fact_freshness, evaluate_price_freshness
from .decision_questions import (
    FIVE_QUESTION_CONTRACT,
    joint_review_allows_recommendation,
    joint_review_from_receipts,
    project_key_questions,
)


DECISION_SCHEMA_VERSION = "case-decision.v2"
_LINE_LOCATOR = re.compile(r"^[Ll](\d+)(?:\s*-\s*[Ll]?(\d+))?$")
# A short-term horizon is not a short position.  Only explicit selling
# language can infer direction when the structured payload omits it; callers
# can always set ``direction='short'`` for a deliberate short setup.
_SHORT_WORD = re.compile(r"\b(?:sell\s+short|short\s+(?:sale|position|setup|thesis)|short\s+seller|shorting)\b", re.IGNORECASE)


def _dict(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, BaseModel):
        return value.model_dump(mode="python", exclude_none=False)
    return {}


def evaluate_instrument_identity(
    ticker: str,
    claimed_name: str | None,
    asset_identity: Mapping[str, Any] | None,
    *,
    source_refs: Sequence[str] = (),
) -> dict[str, Any]:
    """Compare a candidate with an independently retrieved current asset.

    Name agreement is only consistency, never verification by fuzzy match.
    Disjoint issuer names require resolution before any numerical use; an
    alias or a shared generic industry word cannot establish an issuer link.
    The caller supplies code-owned asset metadata, not a model assertion.
    """
    asset = _dict(asset_identity)
    symbol = str(ticker or "").strip().upper()
    provider_symbol = str(asset.get("symbol") or "").strip().upper()
    issuer_name = str(claimed_name or "").strip()[:300]
    provider_name = str(asset.get("name") or "").strip()[:300]
    asset_id = str(asset.get("asset_id") or asset.get("id") or "").strip()[:200]
    result = {
        "status": "unverified", "ticker": symbol, "claimed_name": issuer_name or None,
        "provider_symbol": provider_symbol or None, "provider_name": provider_name or None,
        "asset_id": asset_id or None, "exchange": asset.get("exchange"),
        "asset_status": asset.get("status"),
        "as_of": asset.get("observed_at") or asset.get("retrieved_at") or asset.get("as_of"),
        "source_refs": _unique(source_refs),
        "reason": "Current independent asset identity is unavailable; issuer linkage is unverified.",
    }
    if not asset_id or not provider_symbol or not provider_name:
        return result
    if provider_symbol != symbol:
        result.update(status="conflict", reason=f"The asset provider returned symbol {provider_symbol} for requested {symbol}; instrument identity must be resolved before numerical use.")
        return result
    if str(asset.get("status") or "").casefold() == "inactive":
        result.update(status="conflict", reason=f"The asset provider marks {symbol} ({provider_name}) inactive; current listing identity must be resolved before numerical use.")
        return result
    if not issuer_name:
        result["reason"] = f"The asset provider identifies {symbol} as {provider_name}; no claimed issuer name was supplied for comparison."
        return result
    legal_words = {"the", "and", "inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "limited", "plc", "llc", "class", "ordinary", "common", "stock", "share", "shares"}
    generic_words = {"gold", "silver", "precious", "metals", "mining", "energy", "physical", "trust", "fund", "etf", "holding", "holdings"}

    def tokens(name: str) -> set[str]:
        # Preserve a domain-like issuer brand (e.g. example.com) as one word.
        words = re.findall(r"[a-z0-9]+", name.casefold().replace(".", ""))
        return {word for word in words if len(word) > 1 and word not in legal_words}

    claimed_tokens, provider_tokens = tokens(issuer_name), tokens(provider_name)
    # Asset names sometimes state an issuer and the exact share name as two
    # comma-separated components.  An exact component match is consistent;
    # arbitrary partial/shared-word matches remain unverified.
    provider_components = [tokens(component) for component in provider_name.split(",")]
    if claimed_tokens and (claimed_tokens == provider_tokens or (claimed_tokens - generic_words and claimed_tokens in provider_components)):
        result.update(status="consistent", reason=f"The claimed issuer name is consistent with the current asset provider name {provider_name}; name consistency alone is not independent issuer verification.")
        return result
    claimed_core, provider_core = claimed_tokens - generic_words, provider_tokens - generic_words
    if claimed_core and provider_core and not claimed_core.intersection(provider_core):
        result.update(status="conflict", reason=f"Issuer identity conflict: the research candidate names {issuer_name}, but the current asset provider identifies {symbol} as {provider_name}. Resolve the issuer and listing before using prices, sizing or scenarios; do not automatically reinterpret the research as the provider's company.")
        return result
    result["reason"] = f"The claimed name {issuer_name} and provider name {provider_name} need a dated issuer/listing or alias check; shared name words do not verify identity."
    return result


def _effective_snapshot(snapshot: Mapping[str, Any] | None) -> dict[str, Any]:
    """Accept either a portfolio snapshot or a frozen run snapshot.

    The repository passes the compact portfolio object, while a direct
    service caller often has the complete run input snapshot with the
    portfolio nested under ``portfolio_snapshot``.  Keep the nested object
    authoritative and copy only code-owned context that lives on the outer
    run record.
    """
    if not isinstance(snapshot, Mapping):
        return {}
    nested = snapshot.get("portfolio_snapshot")
    if not isinstance(nested, Mapping):
        return dict(snapshot)
    result = dict(nested)
    for key in ("deterministic_market", "fx_rates", "approved_budget", "approved_budget_currency"):
        if key in snapshot and key not in result:
            result[key] = snapshot[key]
    return result


def _payload_dict(output: AgentOutputPayload | Mapping[str, Any] | BaseModel | None) -> dict[str, Any]:
    if isinstance(output, AgentOutputPayload):
        value = output.model_dump(mode="python", exclude_none=False)
        if "allocation_mode" not in output.model_fields_set and output.decision_brief is not None and "allocation_mode" in output.decision_brief.model_fields_set:
            value["allocation_mode"] = output.decision_brief.allocation_mode
        return value
    if isinstance(output, BaseModel):
        return output.model_dump(mode="python", exclude_none=False)
    return dict(output) if isinstance(output, Mapping) else {}


def _source_map(sources: Mapping[str, Any] | Sequence[Any] | None) -> dict[str, dict[str, Any]]:
    if isinstance(sources, Mapping):
        items = sources.items()
    elif isinstance(sources, Sequence) and not isinstance(sources, (str, bytes)):
        items = ((str(getattr(item, "id", None) or _dict(item).get("id") or ""), item) for item in sources)
    else:
        items = ()
    result: dict[str, dict[str, Any]] = {}
    for key, value in items:
        item = _dict(value)
        source_id = str(item.get("id") or key or "").strip()
        if not source_id:
            continue
        result[source_id] = item
    return result


def _as_of(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _unique(values: Sequence[Any] | None) -> list[str]:
    output: list[str] = []
    for value in values or []:
        text = str(value or "").strip()
        if text and text not in output:
            output.append(text)
    return output


def _entry_price(range_value: PriceRange | None, direction: str) -> str | None:
    if range_value is None:
        return None
    # A range recommendation must remain executable at its worst price.  For
    # both directions the upper bound is the conservative notional cap; a
    # single price is represented by either bound.
    return range_value.upper or range_value.lower


def _source_refs_supported(refs: Sequence[Any], source_map: Mapping[str, Any]) -> bool:
    cleaned = _unique(refs)
    return bool(cleaned) and all(ref in source_map for ref in cleaned)


def _parse_price_range(value: Any, source_map: Mapping[str, Any]) -> tuple[PriceRange | None, str | None]:
    if value is None:
        return None, None
    raw = _dict(value)
    lower = raw.get("lower")
    upper = raw.get("upper")
    # A scalar entry value is accepted as a one-point range for ergonomic
    # callers, but still requires source/date/currency metadata.
    if lower is None and upper is None and raw.get("price") is not None:
        lower = upper = raw.get("price")
    refs = _unique(raw.get("source_refs") if isinstance(raw.get("source_refs"), Sequence) and not isinstance(raw.get("source_refs"), (str, bytes)) else [])
    currency = str(raw.get("currency") or "").strip().upper() or None
    as_of = _as_of(raw.get("as_of") or raw.get("date"))
    if lower is None and upper is None:
        return None, "The supported price range is empty."
    try:
        from .calculations import D

        low = D(lower) if lower is not None else None
        high = D(upper) if upper is not None else None
    except ValueError:
        return None, "The supported price range is not finite."
    if low is None:
        low = high
    if high is None:
        high = low
    if low is None or high is None or low <= 0 or high <= 0 or low > high:
        return None, "The supported price range must contain positive ordered values."
    if not currency:
        return None, "The supported price range has no currency."
    if not as_of:
        return None, "The supported price range has no observation date."
    if not refs:
        return None, "The supported price range has no source reference."
    missing = [ref for ref in refs if ref not in source_map]
    if missing:
        return None, f"The supported price range cites unavailable source references: {', '.join(missing)}."
    if any(_source_is_author(source_map[ref]) for ref in refs):
        return None, "An entry price requires independent evidence; an author's reported price is not verification."
    return PriceRange(lower=str(low), upper=str(high), currency=currency, as_of=as_of, source_refs=refs, basis=str(raw.get("basis") or ""), missing_reason=None), None


def _parse_future_target(value: Any, *, fallback_price: Any = None, currency: Any = None, as_of: Any = None, source_refs: Sequence[Any] | None = None, basis: Any = None, horizon: Any = None, source_map: Mapping[str, Any]) -> tuple[FutureTarget | None, str | None]:
    if value is None and fallback_price is None:
        return None, None
    raw = _dict(value)
    if fallback_price is not None and not raw:
        raw = {"price": fallback_price, "currency": currency, "as_of": as_of, "source_refs": list(source_refs or []), "basis": basis, "horizon": horizon}
    elif fallback_price is not None:
        raw.setdefault("price", fallback_price)
        raw.setdefault("currency", currency)
        raw.setdefault("as_of", as_of)
        raw.setdefault("source_refs", list(source_refs or []))
        raw.setdefault("basis", basis)
        raw.setdefault("horizon", horizon)
    refs = _unique(raw.get("source_refs") if isinstance(raw.get("source_refs"), Sequence) and not isinstance(raw.get("source_refs"), (str, bytes)) else [])
    price = raw.get("price", raw.get("value"))
    lower = raw.get("lower")
    upper = raw.get("upper")
    if price is None and lower is None and upper is None:
        return None, "The future target is empty."
    try:
        from .calculations import D

        parsed = [D(item) for item in (price, lower, upper)]
    except ValueError:
        return None, "The future target is not finite."
    if any(item is not None and item <= 0 for item in parsed):
        return None, "The future target must contain positive values."
    if parsed[1] is not None and parsed[2] is not None and parsed[1] > parsed[2]:
        return None, "The future target range is not ordered."
    target_currency = str(raw.get("currency") or "").strip().upper() or None
    target_as_of = _as_of(raw.get("as_of") or raw.get("date"))
    if not target_currency:
        return None, "The future target has no currency."
    if not target_as_of:
        return None, "The future target has no valuation date."
    if not refs:
        return None, "The future target has no source reference."
    missing = [ref for ref in refs if ref not in source_map]
    if missing:
        return None, f"The future target cites unavailable source references: {', '.join(missing)}."
    if any(_source_is_author(source_map[ref]) for ref in refs):
        return None, "A future target requires independent evidence; an author's target remains an opinion."
    return FutureTarget(price=str(parsed[0]) if parsed[0] is not None else None, lower=str(parsed[1]) if parsed[1] is not None else None, upper=str(parsed[2]) if parsed[2] is not None else None, currency=target_currency, as_of=target_as_of, source_refs=refs, basis=str(raw.get("basis") or ""), horizon=_as_of(raw.get("horizon")), missing_reason=None), None


def _candidate_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    raw_candidates = payload.get("candidate_briefs")
    if not isinstance(raw_candidates, list):
        brief = _dict(payload.get("decision_brief"))
        raw_candidates = brief.get("candidate_briefs") if isinstance(brief.get("candidate_briefs"), list) else []
    for raw in raw_candidates:
        item = _dict(raw)
        ticker = str(item.get("ticker") or item.get("symbol") or item.get("instrument") or "").strip().upper()
        if ticker:
            rows.append(item)
    if not rows:
        raw_discovery = payload.get("research_candidates")
        if isinstance(raw_discovery, list):
            for raw in raw_discovery:
                item = _dict(raw)
                ticker = str(item.get("ticker") or item.get("symbol") or "").strip().upper()
                if ticker:
                    rows.append(item)
    top_ticker = str(payload.get("ticker") or "").strip().upper()
    if top_ticker and not any(str(item.get("ticker") or "").strip().upper() == top_ticker for item in rows):
        rows.insert(0, {"ticker": top_ticker})
    if not rows:
        # A direct candidate can be carried by a structured brief without an
        # explicit ticker field only when the caller supplied instrument.
        brief = _dict(payload.get("decision_brief"))
        ticker = str(brief.get("ticker") or "").strip().upper()
        if ticker:
            rows.append(brief)
    # Merge top-level fields into the first matching candidate.  This keeps old
    # single-candidate output payloads useful without duplicating values.
    top_fields = ("entry_zone", "target_price", "target_price_currency", "target_price_as_of", "target_price_source_refs", "target_price_basis", "target_price_missing_reason", "stop_price", "stop_price_currency", "scenario_assessment", "scenario_reason", "stance", "direction", "strategy", "risks", "catalysts", "invalidation_conditions", "missing_inputs", "watch_triggers", "account_id", "currency", "entry_price", "borrow_available", "short_permission", "borrow_cost_status", "margin_terms_confirmed", "margin_available", "margin_currency", "thesis", "valuation_assumptions", "action_plan", "recommended_shares", "allocation_rationale", "asset_class", "issuer", "issuer_name", "sector")
    for key in top_fields:
        if payload.get(key) is not None and rows and key not in rows[0]:
            rows[0][key] = payload[key]
    return rows


def _direction(item: Mapping[str, Any], payload: Mapping[str, Any], text: str) -> str:
    explicit = str(item.get("direction") or payload.get("direction") or "").strip().lower()
    if explicit in {"long", "short"}:
        return explicit
    return "short" if _SHORT_WORD.search(text) else "long"


def _source_is_author(source: Mapping[str, Any]) -> bool:
    """Identify retained author material from source metadata only.

    A generic title can contain words such as ``post`` or ``author`` even
    when the source is a filing or market page.  Reddit/user provenance is
    therefore recognized from explicit metadata, its hostname, or the
    structured metadata embedded in the archived canonical source body.
    """
    metadata_values = [str(source.get(key) or "") for key in ("source_type", "kind", "provider", "publisher", "origin")]
    metadata = " ".join(metadata_values).casefold()
    if any(token in metadata for token in ("reddit", "reddit_submission", "praw", "user-provided", "user_provided")):
        return True
    url = str(source.get("url") or source.get("source_url") or "").strip()
    try:
        host = (urlparse(url).hostname or "").casefold()
    except ValueError:
        host = ""
    if host == "reddit.com" or host.endswith(".reddit.com") or host == "redd.it" or host.endswith(".redd.it"):
        return True
    content = source.get("content")
    parsed_values: list[Mapping[str, Any]] = []
    if isinstance(content, Mapping):
        parsed_values.append(content)
    elif isinstance(content, str):
        # Canonical archived sources begin with a human-readable heading and
        # then place metadata in one JSON line, followed by JSON post rows.
        # Inspect only parsed records so author wording in the body cannot
        # influence the provenance classification.
        for line in content.splitlines():
            try:
                parsed_line = json.loads(line)
            except (TypeError, ValueError):
                continue
            if isinstance(parsed_line, Mapping):
                parsed_values.append(parsed_line)
    for parsed in parsed_values:
        nested_values: list[str] = []
        for key in ("provider", "source_type", "kind", "origin", "platform"):
            nested_values.append(str(parsed.get(key) or ""))
        nested_meta = " ".join(nested_values).casefold()
        if any(token in nested_meta for token in ("reddit", "reddit_submission", "praw", "user-provided", "user_provided")):
            return True
        for key in ("metadata", "source", "record"):
            nested = parsed.get(key)
            if isinstance(nested, Mapping):
                nested_meta = " ".join(str(nested.get(field) or "") for field in ("provider", "source_type", "kind", "origin", "platform")).casefold()
                if any(token in nested_meta for token in ("reddit", "reddit_submission", "praw", "user-provided", "user_provided")):
                    return True
    return False


def _evidence(payload: Mapping[str, Any], source_map: Mapping[str, Mapping[str, Any]]) -> list[DecisionEvidence]:
    result: list[DecisionEvidence] = []
    claims = payload.get("fact_claims") if isinstance(payload.get("fact_claims"), list) else []
    for raw in claims:
        item = _dict(raw)
        claim = str(item.get("claim") or "").strip()
        if not claim:
            continue
        value = str(item.get("value") or "").strip()
        statement = f"{claim}: {value}" if value else claim
        ref = str(item.get("source_ref") or "").strip()
        source = source_map.get(ref, {})
        author = _source_is_author(source)
        validation_metadata = str(item.get("validation_status") or "").strip().lower()
        if not ref or ref not in source_map:
            kind, validation = "unknown", "unavailable"
        elif author:
            # Archive matching establishes provenance, not the truth of an
            # author's claim.  Keep Reddit/Yolo text visibly opinionated.
            kind, validation = "opinion", "proposed"
            statement = f"Author claim: {statement}"
        elif validation_metadata == "validated":
            kind, validation = "fact", "validated"
        elif validation_metadata in {"proposed", "contested", "unavailable"}:
            kind, validation = "unknown", validation_metadata if validation_metadata in {"proposed", "unavailable"} else "unknown"
        else:
            # A source ID alone proves provenance, not the truth of the
            # assertion.  Repository output normally supplies the enriched
            # validation_status; pure callers remain explicitly unknown.
            kind, validation = "unknown", "unknown"
        result.append(DecisionEvidence(kind=kind, statement=statement, source_refs=[ref] if ref else [], locator=str(item.get("locator") or "") or None, as_of=str(item.get("period") or "") or None, validation_status=validation))
    for value in payload.get("assumptions", []) if isinstance(payload.get("assumptions"), list) else []:
        text = str(value or "").strip()
        if text:
            result.append(DecisionEvidence(kind="assumption", statement=text, validation_status="unknown"))
    for value in payload.get("counterarguments", []) if isinstance(payload.get("counterarguments"), list) else []:
        text = str(value or "").strip()
        if text:
            result.append(DecisionEvidence(kind="opinion", statement=text, validation_status="unknown"))
    return _dedupe_evidence(result)


def _dedupe_evidence(values: Sequence[DecisionEvidence]) -> list[DecisionEvidence]:
    seen: set[tuple[str, str, tuple[str, ...]]] = set()
    output: list[DecisionEvidence] = []
    for item in values:
        key = (item.kind, item.statement.casefold(), tuple(item.source_refs))
        if key not in seen:
            seen.add(key)
            output.append(item)
    return output


def _typed_triggers(values: Any, source_map: Mapping[str, Any], *, supported_prices: Mapping[Any, set[str]] | None = None) -> tuple[list[WatchTrigger], list[str]]:
    if not isinstance(values, list):
        return [], []
    output: list[WatchTrigger] = []
    issues: list[str] = []
    for index, raw in enumerate(values):
        item = _dict(raw)
        refs = _unique(item.get("source_refs") if isinstance(item.get("source_refs"), Sequence) and not isinstance(item.get("source_refs"), (str, bytes)) else [])
        trigger_type = str(item.get("type") or item.get("trigger_type") or "").strip().lower()
        if trigger_type not in {"price", "catalyst", "evidence", "date"}:
            issues.append(f"watch_trigger_{index + 1}: type must be price, catalyst, evidence, or date")
            continue
        if refs and not all(ref in source_map for ref in refs):
            issues.append(f"watch_trigger_{index + 1}: source reference unavailable")
            continue
        condition = str(item.get("condition") or "").strip()
        if not condition:
            issues.append(f"watch_trigger_{index + 1}: condition is required")
            continue
        operator = item.get("operator")
        threshold = item.get("threshold")
        upper = item.get("upper_threshold")
        if trigger_type == "price":
            if operator not in {"at_or_below", "at_or_above", "between", "crosses"} or threshold is None:
                issues.append(f"watch_trigger_{index + 1}: price trigger needs operator and threshold")
                continue
            if not item.get("currency"):
                issues.append(f"watch_trigger_{index + 1}: price trigger needs currency")
                continue
            try:
                low, high = D(threshold), D(upper)
            except ValueError:
                low = high = None
            if low is None or low <= 0 or (operator == "between" and (high is None or high < low)):
                issues.append(f"watch_trigger_{index + 1}: price thresholds must be finite, positive and ordered")
                continue
            prices = [low, high] if operator == "between" else [low]
            if not refs or any(_source_is_author(source_map.get(ref, {})) for ref in refs):
                issues.append(f"watch_trigger_{index + 1}: an independent price source is required")
                continue
            if any(not (set(refs) & (supported_prices or {}).get((price, str(item["currency"]).upper()), set())) for price in prices):
                issues.append(f"watch_trigger_{index + 1}: threshold lacks a same-instrument validated price or calculation")
                continue
        elif trigger_type == "date" and not item.get("trigger_date"):
            issues.append(f"watch_trigger_{index + 1}: date trigger needs trigger_date")
            continue
        elif trigger_type == "catalyst" and not (item.get("catalyst") or condition):
            issues.append(f"watch_trigger_{index + 1}: catalyst trigger needs an event")
            continue
        if not str(item.get("reopen_when") or "").strip():
            issues.append(f"watch_trigger_{index + 1}: reopen_when is required")
            continue
        try:
            output.append(WatchTrigger(type=trigger_type, condition=condition, operator=operator, threshold=str(threshold) if threshold is not None else None, upper_threshold=str(upper) if upper is not None else None, currency=str(item.get("currency") or "").upper() or None, catalyst=str(item.get("catalyst") or "") or None, evidence_condition=str(item.get("evidence_condition") or "") or None, trigger_date=str(item.get("trigger_date") or "") or None, reopen_when=str(item.get("reopen_when") or ""), review_at=str(item.get("review_at") or "") or None, status=str(item.get("status") or "active"), source_refs=refs))
        except (ValidationError, ValueError) as exc:
            issues.append(f"watch_trigger_{index + 1}: {exc}")
    return output, issues


def _supported_trigger_prices(payload: Mapping[str, Any], ticker: str, entry: PriceRange | None, target: FutureTarget | None) -> dict[Any, set[str]]:
    """Bind a watch price to this candidate's normalized numerical evidence."""
    values: dict[Any, set[str]] = {}
    for price in (entry, target):
        if price is None:
            continue
        for key in ("price", "lower", "upper"):
            amount = D(getattr(price, key, None))
            if amount is not None:
                values.setdefault((amount, price.currency), set()).update(price.source_refs)
    validated: dict[int, tuple[Any, str, str]] = {}
    for index, claim in enumerate(payload.get("fact_claims") or []):
        item = _dict(claim)
        if item.get("validation_status") != "validated":
            continue
        names_ticker = bool(re.search(rf"(?<![A-Z0-9]){re.escape(ticker)}(?![A-Z0-9])", str(item.get("claim") or "").upper()))
        if not names_ticker:
            # The repository may bind a generic closing-price claim to an
            # exact structured bar. Its recorded excerpt preserves that
            # instrument even when the model's prose omits the ticker.
            # Accept only an unambiguous symbol from completed JSON bars;
            # source titles and arbitrary prose cannot supply this binding.
            excerpt_symbols: set[str] = set()
            for line in str(item.get("excerpt") or "").splitlines():
                try:
                    excerpt_row = json.loads(line)
                except (TypeError, ValueError):
                    continue
                if not isinstance(excerpt_row, dict) or not excerpt_row.get("symbol"):
                    continue
                if excerpt_row.get("complete", excerpt_row.get("completed")) is not True:
                    excerpt_symbols.add("__incomplete__")
                excerpt_symbols.add(str(excerpt_row["symbol"]).strip().upper())
            if excerpt_symbols != {ticker}:
                continue
        unit = str(item.get("unit") or "").lower().replace(" ", "")
        currency_match = re.fullmatch(r"([a-z]{3})(?:/share|pershare)", unit)
        if not currency_match:
            continue
        try:
            amount = D(item.get("value"))
        except ValueError:
            continue
        if amount is not None and amount > 0 and item.get("source_ref"):
            ref = str(item["source_ref"])
            currency = currency_match.group(1).upper()
            validated[index] = (amount, ref, currency)
            values.setdefault((amount, currency), set()).add(ref)
    for raw in payload.get("calculations") or []:
        item = _dict(raw)
        indices = item.get("input_fact_indices") or []
        if not indices or any(index not in validated for index in indices):
            continue
        currencies = {validated[index][2] for index in indices}
        if len(currencies) != 1:
            continue
        currency = next(iter(currencies))
        if str(item.get("unit") or "").upper().replace(" ", "") not in {f"{currency}/SHARE", f"{currency}PERSHARE"}:
            continue
        try:
            amount = D(item.get("value"))
        except ValueError:
            continue
        if amount is not None and amount > 0:
            values.setdefault((amount, currency), set()).update(validated[index][1] for index in indices)
    return values


def _blocker(key: str, kind: str, reason: str, *, owner: str | None = None, reopen_when: str | None = None, source_refs: Sequence[str] | None = None) -> DecisionBlocker:
    defaults = {
        "evidence": ("Researcher", "Collect dated retained support and rerun the affected check."),
        "data_quality": ("Researcher", "Resolve the data-quality issue with dated independent evidence and rerun the affected check."),
        "sizing": ("Portfolio inputs", "Confirm the missing account or policy inputs, then rerun sizing."),
        "constraint": ("Portfolio policy", "Revise the proposed allocation or policy constraint, then rerun the gate."),
        "execution": ("Code service", "Inspect the failed code step and retry after correction."),
        "unknown": ("Researcher", "Provide the missing input and rerun the affected check."),
    }
    default_owner, default_reopen = defaults.get(kind, defaults["unknown"])
    return DecisionBlocker(key=key[:160], kind=kind, reason=reason[:4_000], owner=owner or default_owner, reopen_when=reopen_when or default_reopen, source_refs=_unique(source_refs), status="open")


def _dedupe_blockers(values: Sequence[DecisionBlocker]) -> list[DecisionBlocker]:
    seen: set[str] = set()
    output: list[DecisionBlocker] = []
    for item in values:
        if item.key not in seen:
            seen.add(item.key)
            output.append(item)
    return output[:20]


# Recommendation checks are current decision requirements, so their owner and
# reopening action must describe the check that actually failed.  Keep this
# mapping code-owned instead of deriving it from the check's display text or
# treating every missing check as a portfolio input.
_RECOMMENDATION_BLOCKER_MAP: dict[str, tuple[str, str, str]] = {
    "fact_references": (
        "data_quality",
        "CIO",
        "Resolve each declared reference to an allowed frozen claim and rerun the gate.",
    ),
    "five_question_evidence": (
        "evidence",
        "Researcher",
        "Complete all five code-owned questions with current selected evidence and explicit unknowns.",
    ),
    "joint_decision_review": (
        "data_quality",
        "CIO",
        "Resolve the local Laya/Astra assessment and rerun the recommendation gate.",
    ),
    "fresh_material_facts": (
        "evidence",
        "Researcher",
        "Provide dated support for each material fact and rerun the gate.",
    ),
    "fresh_material_price": (
        "evidence",
        "Researcher",
        "Provide a dated retained price source and rerun the gate.",
    ),
    "instrument_identity": (
        "evidence",
        "Researcher",
        "Resolve instrument identity with dated identity support and rerun the gate.",
    ),
    "entry": (
        "evidence",
        "Researcher",
        "Provide a dated retained entry price and rerun the gate.",
    ),
    "direction": ("unknown", "CIO", "Record an explicit direction and rerun the gate."),
    "strategy": ("unknown", "CIO", "Record an explicit strategy and rerun the gate."),
    "horizon": ("unknown", "CIO", "Record an explicit bounded horizon and rerun the gate."),
    "economic_objective": (
        "evidence",
        "CIO",
        "Resolve the directional payoff or documented hedge objective and rerun the gate.",
    ),
    "thesis": ("evidence", "CIO", "Complete the candidate-specific thesis and rerun the gate."),
    "valuation": ("evidence", "CIO", "Complete a supported valuation and rerun the gate."),
    "payoff": ("evidence", "CIO", "Complete a directional payoff and rerun the gate."),
    "exit": ("evidence", "CIO", "Record an executable exit condition and rerun the gate."),
    "invalidation": ("evidence", "CIO", "Record a measurable invalidation condition and rerun the gate."),
    "catalyst_or_review": ("evidence", "CIO", "Record a dated catalyst or review and rerun the gate."),
    "portfolio_sizing": (
        "sizing",
        "Portfolio inputs",
        "Complete the applicable portfolio sizing inputs and rerun the gate.",
    ),
    "recommended_allocation": (
        "sizing",
        "Portfolio inputs",
        "Provide a positive recommended allocation and rerun the gate.",
    ),
}


def _recommendation_blocker(check: RecommendationCheck) -> DecisionBlocker:
    kind, owner, reopen_when = _RECOMMENDATION_BLOCKER_MAP.get(
        check.key,
        ("unknown", "CIO", f"Resolve the {check.key} requirement and rerun the gate."),
    )
    return _blocker(
        f"recommendation:{check.key}",
        kind,
        check.reason,
        owner=owner,
        reopen_when=reopen_when,
    )


def _scenario_forecast(
    snapshot: Any,
    *,
    force_reject: bool = False,
    assessment: Any = None,
    scenario_reason: Any = None,
) -> ForecastAcceptance | None:
    if snapshot is None:
        return None
    raw = _dict(snapshot)
    assessment_value = str(assessment or "not_assessed").strip().lower()
    if assessment_value not in {"usable", "do_not_use", "not_assessed"}:
        assessment_value = "not_assessed"
    assessment_reason = (_as_of(scenario_reason) or "")[:4_000] or None
    if not raw:
        reasons = ["No scenario snapshot was supplied."]
        if assessment_value == "do_not_use":
            reasons.append(assessment_reason or "CIO marked the scenario as unsuitable for decision use.")
        elif assessment_value == "not_assessed":
            reasons.append("CIO scenario assessment is not available.")
        return ForecastAcceptance(calculation_status="not_run", data_quality_status="unknown", model_acceptance_status="rejected" if assessment_value == "do_not_use" else "unavailable", scenario_assessment=assessment_value, scenario_reason=assessment_reason, accepted=False, reasons=_unique(reasons))
    calibration = _dict(raw.get("calibration"))
    data_quality = _dict(raw.get("data_quality"))
    model_acceptance = _dict(raw.get("model_acceptance"))
    discontinuities = calibration.get("discontinuities")
    if discontinuities:
        data_quality.setdefault("status", "invalid")
        model_acceptance.setdefault("status", "rejected")
        model_acceptance.setdefault("accepted", False)
    calculation_status = str(raw.get("calculation_status") or ("complete" if raw.get("status") == "complete" else "insufficient_evidence"))
    if calculation_status not in {"complete", "insufficient_evidence"}:
        calculation_status = "insufficient_evidence"
    data_status = str(raw.get("data_quality_status") or data_quality.get("status") or "unknown")
    if data_status not in {"valid", "degraded", "invalid", "unknown"}:
        data_status = "unknown"
    model_status = str(raw.get("model_acceptance_status") or model_acceptance.get("status") or "unavailable")
    if model_status not in {"accepted", "rejected", "unavailable", "not_applicable"}:
        model_status = "unavailable"
    reasons = _unique(raw.get("acceptance_reasons") if isinstance(raw.get("acceptance_reasons"), list) else model_acceptance.get("reasons") if isinstance(model_acceptance.get("reasons"), list) else [])
    if assessment_value == "not_assessed":
        # A code service may certify that arithmetic completed, but that does
        # not establish CIO acceptance for decision use.
        if model_status == "accepted":
            model_status = "unavailable"
        reasons.append("CIO scenario assessment is not available.")
    elif assessment_value == "do_not_use":
        model_status = "rejected"
        reasons.append(assessment_reason or "CIO marked the scenario as unsuitable for decision use.")
    elif assessment_value == "usable" and model_status in {"unavailable", "not_applicable"}:
        # Explicit CIO acceptance can promote an otherwise unlabelled but
        # code-complete scenario, while quality/rejection gates below remain
        # authoritative.
        model_status = "accepted"
    accepted = assessment_value == "usable" and bool(raw.get("forecast_accepted")) and calculation_status == "complete" and data_status == "valid" and model_status == "accepted"
    if discontinuities:
        reasons.append("Unresolved price discontinuity blocks forecast acceptance.")
        accepted = False
    if force_reject:
        model_status = "rejected"
        reasons.append("CIO rejected the forecast for decision use.")
        accepted = False
    return ForecastAcceptance(calculation_status=calculation_status, data_quality_status=data_status, model_acceptance_status=model_status, scenario_assessment=assessment_value, scenario_reason=assessment_reason, accepted=accepted, reasons=_unique(reasons), source_refs=_unique(raw.get("source_refs") if isinstance(raw.get("source_refs"), list) else []))


def _policy_inputs(snapshot: Mapping[str, Any] | None, item: Mapping[str, Any], direction: str) -> dict[str, Any]:
    """Resolve only explicitly configured policy values from a run snapshot."""
    snapshot = snapshot if isinstance(snapshot, Mapping) else {}
    policy = _dict(snapshot.get("portfolio_policy"))
    risk = _dict(snapshot.get("risk_settings"))
    result: dict[str, Any] = {"risk_settings": risk}
    result["max_positions"] = policy.get("max_positions")
    existing_symbols = {
        str(position.get("symbol") or "").strip().upper()
        for position in (snapshot.get("positions") if isinstance(snapshot.get("positions"), list) else [])
        if isinstance(position, Mapping) and str(position.get("symbol") or "").strip()
    }
    result["existing_symbols"] = existing_symbols
    strategy = str(item.get("strategy") or "").strip().lower()
    if strategy not in {"long_term", "trade"}:
        strategy = ""
    raw_account = item.get("account_id") or item.get("account")
    if not raw_account and strategy and isinstance(snapshot.get("accounts"), list):
        allowed_type = "tfsa" if strategy == "long_term" else "nonregistered"
        matching = [account for account in snapshot.get("accounts", []) if isinstance(account, Mapping) and str(account.get("account_type") or "").strip().lower() == allowed_type]
        if len(matching) == 1:
            raw_account = matching[0].get("id")
        elif len(matching) > 1:
            result["recommend_requires"] = ["account_id"]
    policy_has_limits = bool(_dict(policy.get("limits")))
    if policy_has_limits and not strategy:
        result["recommend_requires"] = ["strategy"]
        return result
    if raw_account:
        result["account_id"] = str(raw_account.get("id")) if isinstance(raw_account, Mapping) else str(raw_account)
    limits = _dict(policy.get("limits"))
    bucket = "trade" if strategy == "trade" or direction == "short" else "long_term"
    limit = _dict(limits.get(bucket))
    if limit:
        result["budget"] = limit.get("initial_notional")
        result["budget_currency"] = limit.get("currency")
        result["policy_status"] = "approved" if policy.get("status") == "approved" else "proposed"
        if limit.get("planned_loss_limit") is not None:
            result["risk_budget"] = limit.get("planned_loss_limit")
            result["risk_budget_currency"] = limit.get("currency")
    explicit_budget = snapshot.get("approved_budget")
    if explicit_budget is not None:
        result["approved_budget"] = explicit_budget
        result["budget_currency"] = snapshot.get("approved_budget_currency") or result.get("budget_currency")
        result["policy_status"] = "approved"
    # Provider output may suggest a notional, but it cannot approve its own
    # funding.  Only a repository-owned snapshot budget or explicit policy
    # limit is eligible for sizing.  Preserve any model value in the raw
    # output; simply leave it out of the calculator inputs here.
    # A snapshot may carry a user-confirmed account ID separately from the
    # private policy record.  It still needs the calculator's dated evidence.
    if snapshot.get("account_id") and "account_id" not in result:
        result["account_id"] = str(snapshot["account_id"])
    if result.get("account_id") and isinstance(snapshot.get("accounts"), list):
        selected = next((account for account in snapshot["accounts"] if isinstance(account, Mapping) and str(account.get("id")) == str(result["account_id"])), None)
        selected_type = str(selected.get("account_type") or "").strip().lower() if selected else ""
        if selected and selected_type == "tfsa" and (strategy == "trade" or direction == "short"):
            result["eligible_account"] = False
            result.setdefault("recommend_requires", []).append("tfsa_short_restriction" if direction == "short" else "tfsa_trade_restriction")
    if direction == "short" and strategy == "long_term":
        # A long-term strategy is the only envelope allowed to auto-select a
        # TFSA here; short exposure is incompatible with that preference even
        # if the account snapshot itself did not identify its type.
        result["eligible_account"] = False
        result.setdefault("recommend_requires", []).append("tfsa_short_restriction")
    result["strategy"] = strategy or None
    return result


def _short_operational_inputs(
    snapshot: Mapping[str, Any],
    *,
    ticker: str,
    account_id: str | None,
    market_packet: Mapping[str, Any] | None = None,
    asset_id: str | None = None,
    as_of: str | None = None,
) -> dict[str, Any]:
    """Resolve dated short permissions from frozen code-owned inputs only.

    Candidate rows are provider proposals and are deliberately ignored. The
    selected account and the selected instrument's frozen borrow record must
    carry their own observation date; a generic snapshot boolean or cash
    balance cannot establish borrow, margin, or carry terms.
    """
    account: Mapping[str, Any] | None = None
    accounts = snapshot.get("accounts") if isinstance(snapshot, Mapping) else None
    if isinstance(accounts, list):
        account = next((item for item in accounts if isinstance(item, Mapping) and (account_id is None or str(item.get("id")) == str(account_id))), None)
    account = account or {}
    packet = _dict(market_packet)
    symbol = str(ticker or "").strip().upper()
    requested_asset_id = str(asset_id or "").strip()
    checks: list[dict[str, Any]] = []

    def _record_matches(record: Mapping[str, Any], *, key_symbol: str | None = None) -> bool:
        record_symbol = str(record.get("ticker") or record.get("symbol") or "").strip().upper()
        record_asset = str(record.get("asset_id") or record.get("instrument_id") or record.get("id") or "").strip()
        if record_symbol and record_symbol != symbol:
            return False
        if requested_asset_id and record_asset and record_asset != requested_asset_id:
            return False
        if requested_asset_id and not record_symbol and not record_asset:
            return False
        if key_symbol and key_symbol.strip().upper() != symbol:
            return False
        # A direct instrument record must identify the instrument. This keeps
        # an account-wide borrow boolean from certifying every ticker.
        return bool(record_symbol == symbol or (requested_asset_id and record_asset == requested_asset_id) or key_symbol)

    def _extract_record(container: Any) -> dict[str, Any]:
        if isinstance(container, Mapping):
            keyed = container.get(symbol) or container.get(symbol.upper())
            if isinstance(keyed, Mapping) and _record_matches(keyed, key_symbol=symbol):
                return _dict(keyed)
            if _record_matches(container):
                return _dict(container)
            # Some snapshots key records by asset id rather than ticker.
            if requested_asset_id:
                keyed = container.get(requested_asset_id)
                if isinstance(keyed, Mapping) and _record_matches(keyed, key_symbol=requested_asset_id):
                    return _dict(keyed)
            for key, value in container.items():
                if isinstance(value, Mapping) and _record_matches(value, key_symbol=str(key)):
                    return _dict(value)
        elif isinstance(container, list):
            for raw in container:
                if isinstance(raw, Mapping) and _record_matches(raw):
                    return _dict(raw)
        return {}

    # Borrow availability and borrow cost are instrument-specific. The raw
    # deterministic market row is not a permission record; only its explicit
    # nested short_terms/borrow_terms object can be considered, and that
    # object must carry the requested symbol or asset id.
    terms: dict[str, Any] = {}
    for container in (
        packet.get("short_terms"),
        packet.get("borrow_terms"),
        account.get("short_instrument_terms"),
        account.get("borrow_terms"),
        snapshot.get("short_instrument_terms") if isinstance(snapshot, Mapping) else None,
        snapshot.get("borrow_terms") if isinstance(snapshot, Mapping) else None,
    ):
        terms = _extract_record(container)
        if terms:
            break

    def _value(source: Mapping[str, Any], *names: str) -> Any:
        for name in names:
            if name in source and source.get(name) is not None:
                return source.get(name)
        return None

    def _timestamp(source: Mapping[str, Any], names: Sequence[str], label: str, max_age_days: int) -> tuple[datetime | None, str | None]:
        raw = _value(source, *names)
        if raw is None or not str(raw).strip():
            return None, f"{label} requires its own dated observation timestamp."
        text = str(raw).strip()
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None, f"{label} observation timestamp is not a valid ISO date."
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        else:
            parsed = parsed.astimezone(timezone.utc)
        reference_text = str(as_of or "").strip()
        try:
            reference = datetime.fromisoformat(reference_text.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            reference = None
        if reference is None:
            return None, "A dated case as-of time is required to validate short operational evidence."
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=timezone.utc)
        else:
            reference = reference.astimezone(timezone.utc)
        if parsed > reference:
            return None, f"{label} observation is future-dated relative to the case as-of time."
        if reference - parsed > timedelta(days=max_age_days):
            return None, f"{label} observation is older than the {max_age_days}-day operational evidence policy."
        return parsed, None

    def _check(name: str, ok: bool, detail: str, observed_at: datetime | None = None) -> None:
        checks.append({"check": name, "status": "pass" if ok else "unavailable", "detail": detail, "observed_at": observed_at.isoformat().replace("+00:00", "Z") if observed_at else None})

    borrow = _value(terms, "borrow_available", "borrow_confirmed")
    borrow_time, borrow_reason = _timestamp(terms, ("borrow_observed_at", "borrow_terms_observed_at", "instrument_observed_at", "observed_at"), "Instrument borrow availability", 1) if terms else (None, "No frozen instrument borrow record matched this ticker.")
    _check("short_borrow_availability", borrow is not None and borrow_time is not None, borrow_reason or "Dated instrument borrow availability is current.", borrow_time)

    cost = _value(terms, "borrow_cost_status", "borrow_terms_status", "borrow_rate_status")
    cost_text = str(cost or "").strip().casefold()
    cost_valid_status = bool(cost_text and cost_text not in {"confirmed", "true", "yes", "unknown", "unavailable", "missing", "not_confirmed"})
    cost_time, cost_reason = _timestamp(terms, ("borrow_cost_observed_at", "borrow_terms_observed_at", "instrument_observed_at", "observed_at"), "Instrument borrow cost", 1) if terms else (None, "No frozen instrument borrow-cost record matched this ticker.")
    _check("borrow_terms_or_cost_status", cost_valid_status and cost_time is not None, cost_reason or ("A precise dated borrow-cost status is recorded." if cost_valid_status else "Generic confirmation is not a borrow-cost status."), cost_time)

    # Permission and margin are selected-account controls. Account-level
    # booleans are accepted only with their own field-specific observation;
    # a generic account observed_at/cash timestamp cannot certify them.
    permission = _value(account, "short_permission", "short_sale_permission", "short_sale_allowed")
    permission_time, permission_reason = _timestamp(account, ("short_permission_observed_at", "permission_observed_at", "short_sale_observed_at"), "Account short-sale permission", 30)
    _check("short_sale_permission", permission is not None and permission_time is not None, permission_reason or "Dated account short-sale permission is current.", permission_time)

    margin_confirmed = _value(account, "margin_terms_confirmed", "buying_power_confirmed")
    margin_time, margin_reason = _timestamp(account, ("margin_terms_observed_at", "margin_observed_at", "buying_power_observed_at"), "Account margin terms", 1)
    _check("margin_terms_confirmation", margin_confirmed is not None and margin_time is not None, margin_reason or "Dated account margin terms are current.", margin_time)

    if "buying_power" in account and account.get("buying_power") is not None:
        margin = account.get("buying_power")
        margin_currency = _value(account, "buying_power_currency")
        margin_amount_time, margin_amount_reason = _timestamp(account, ("buying_power_observed_at",), "Account buying power", 1)
    else:
        margin = _value(account, "margin_available", "short_margin_available")
        margin_currency = _value(account, "margin_currency")
        margin_amount_time, margin_amount_reason = _timestamp(account, ("margin_observed_at", "margin_terms_observed_at"), "Account margin availability", 1)
    if margin is not None and margin_currency is None:
        margin_amount_reason = "Account margin/buying-power currency is required; generic account currency is not sufficient."
        margin_amount_time = None
    _check("margin_available", margin is not None and margin_amount_time is not None, margin_amount_reason or "Dated account margin availability is current.", margin_amount_time)

    return {
        "borrow_available": borrow if borrow_time is not None else None,
        "short_permission": permission if permission_time is not None else None,
        "borrow_cost_status": cost if cost_valid_status and cost_time is not None else None,
        "margin_terms_confirmed": margin_confirmed if margin_time is not None else None,
        "margin_available": margin if margin_amount_time is not None else None,
        "margin_currency": margin_currency if margin_amount_time is not None else None,
        "operational_checks": checks,
    }


def _decision_outcome(item: Mapping[str, Any], payload: Mapping[str, Any], *, sizing: DecisionSizing, entry: PriceRange | None, target: FutureTarget | None, triggers: Sequence[WatchTrigger], direction: str) -> str | None:
    stance = str(item.get("stance") or payload.get("stance") or payload.get("decision_disposition") or "").strip().lower()
    if stance in {"avoid", "reject", "decline"}:
        return "decline"
    watchable = bool(triggers)
    if stance in {"watch", "watchlist"}:
        return "watchlist" if watchable else None
    if stance in {"enter", "recommend", "accept"}:
        if sizing.execution_state == "ready" and sizing.shares and sizing.shares > 0 and entry is not None:
            return "recommend"
        return "watchlist" if watchable else None
    # A neutral/defer output must earn a watchlist entry with an actionable
    # typed condition.  A supported target alone is not a watch condition.
    if watchable and (entry is not None or target is not None or stance in {"watch", "watchlist"}):
        return "watchlist"
    return None


def _v2_payload_present(item: Mapping[str, Any], payload: Mapping[str, Any], brief: Mapping[str, Any]) -> bool:
    """Identify a newly-authored v2 candidate without reinterpreting v1 rows."""
    return any(
        value is not None
        for value in (
            item.get("thesis"), item.get("valuation_assumptions"), item.get("action_plan"),
            brief.get("thesis"), brief.get("valuation_assumptions"), brief.get("action_plan"),
            payload.get("thesis"), payload.get("valuation_assumptions"), payload.get("action_plan"),
        )
    )


def _thesis_block(item: Mapping[str, Any], payload: Mapping[str, Any], brief: Mapping[str, Any]) -> ThesisBlock:
    raw = item.get("thesis")
    if raw is None:
        raw = brief.get("thesis")
    if raw is None:
        raw = payload.get("thesis")
    values = _dict(raw)
    try:
        values.setdefault("supporting_claim_ids", values.get("supporting_claim_ids") or [])
        values.setdefault("disconfirming_evidence", values.get("disconfirming_evidence") or [])
        values.setdefault("decision_change_conditions", values.get("decision_change_conditions") or [])
        values.setdefault("source_refs", values.get("source_refs") or [])
        return ThesisBlock.model_validate(values)
    except (ValidationError, ValueError, TypeError):
        return ThesisBlock(status="unavailable", missing_inputs=["thesis"])


def _valuation_assumptions(item: Mapping[str, Any], payload: Mapping[str, Any], brief: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = item.get("valuation_assumptions")
    if raw is None:
        raw = brief.get("valuation_assumptions")
    if raw is None:
        raw = payload.get("valuation_assumptions")
    return _dict(raw)


def _validated_fact_map(payload: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Expose only repository-enriched semantic facts to valuation/thesis code."""
    result: dict[str, dict[str, Any]] = {}
    raw_claims = payload.get("fact_claims")
    if not isinstance(raw_claims, Sequence) or isinstance(raw_claims, (str, bytes)):
        return result
    for raw in raw_claims:
        claim = _dict(raw)
        identifier = str(claim.get("fact_id") or claim.get("claim_id") or claim.get("id") or "").strip()
        if not identifier:
            continue
        if str(claim.get("validation_status") or "").strip().lower() != "validated":
            continue
        if str(claim.get("semantic_status") or "").strip().lower() != "supported":
            continue
        result[identifier] = claim
    return result


def _fact_is_shared_macro(fact: Mapping[str, Any]) -> bool:
    """Return true only for an explicit typed shared-macro declaration."""
    if fact.get("shared_macro") is True or fact.get("is_shared_macro") is True:
        return True
    for key in ("scope", "claim_scope", "fact_scope", "evidence_scope", "binding_scope", "shared_scope", "claim_type"):
        value = str(fact.get(key) or "").strip().casefold().replace("-", "_").replace(" ", "_")
        if value in {"shared_macro", "macro_shared", "shared_macro_fact", "macro_fact"}:
            return True
    return False


def _thesis_fact_is_candidate_bound(
    fact: Mapping[str, Any],
    *,
    candidate_issuer: str,
    thesis_source_refs: Sequence[Any],
    source_map: Mapping[str, Any],
) -> bool:
    """Require each supporting claim to bind to this issuer and its sources."""
    source_ref = str(fact.get("source_ref") or "").strip()
    if not source_ref or source_ref not in set(_unique(thesis_source_refs)):
        return False
    source = _dict(source_map.get(source_ref))
    if not str(source.get("content") or source.get("original_content") or "").strip():
        return False
    if _fact_is_shared_macro(fact):
        return True
    observed = fact.get("issuer") or fact.get("issuer_name") or fact.get("subject") or fact.get("ticker") or fact.get("symbol") or fact.get("instrument") or fact.get("security")
    return bool(candidate_issuer and _issuer_matches(candidate_issuer, observed))


def _action_plan_block(item: Mapping[str, Any], payload: Mapping[str, Any], brief: Mapping[str, Any], *, entry: PriceRange | None, target: FutureTarget | None, invalidation: Sequence[str], stop_price: Any, catalysts: Sequence[str]) -> ActionPlan:
    raw = item.get("action_plan")
    if raw is None:
        raw = brief.get("action_plan")
    if raw is None:
        raw = payload.get("action_plan")
    values = _dict(raw)
    # Compatibility projections are copied into a v2 plan only when they are
    # explicit.  They do not make the recommendation gate pass by themselves.
    entry_condition = str(values.get("entry_condition") or item.get("entry_plan") or item.get("entry_advice") or payload.get("entry_plan") or "").strip()
    exit_condition = str(values.get("exit_condition") or "").strip()
    if not exit_condition and target is not None:
        exit_condition = str(target.basis or "Valuation target")
    invalidation_condition = str(values.get("invalidation_condition") or "").strip()
    if not invalidation_condition and invalidation:
        invalidation_condition = "; ".join(str(value) for value in invalidation if str(value))
    if values.get("max_loss_basis") not in {"stop", "scenario", "unavailable"}:
        values["max_loss_basis"] = "stop" if stop_price is not None else "scenario" if values.get("invalidation_condition") else "unavailable"
    events: list[dict[str, Any]] = []
    raw_events = values.get("catalyst_events")
    if isinstance(raw_events, Sequence) and not isinstance(raw_events, (str, bytes)):
        for event in raw_events:
            event_values = _dict(event)
            if event_values.get("description"):
                events.append(event_values)
    values["catalyst_events"] = events
    values["entry_condition"] = entry_condition
    values["exit_condition"] = exit_condition
    values["invalidation_condition"] = invalidation_condition
    values["review_at"] = values.get("review_at") or payload.get("next_review_at")
    values["benchmark_ticker"] = values.get("benchmark_ticker") or payload.get("benchmark_ticker")
    values["benchmark_rationale"] = values.get("benchmark_rationale") or payload.get("benchmark_rationale")
    values["hedge_objective"] = values.get("hedge_objective") or payload.get("hedge_objective")
    values["hedge_source_refs"] = values.get("hedge_source_refs") or payload.get("hedge_source_refs") or []
    missing: list[str] = []
    if not entry_condition:
        missing.append("entry_condition")
    if not exit_condition:
        missing.append("exit_condition")
    if not invalidation_condition:
        missing.append("invalidation_condition")
    if not values.get("review_at") and not events:
        missing.append("catalyst_or_review")
    values["missing_inputs"] = list(dict.fromkeys([*(values.get("missing_inputs") or []), *missing]))
    values["status"] = "complete" if not missing else "partial" if values.get("entry_condition") or values.get("exit_condition") else "unavailable"
    try:
        return ActionPlan.model_validate(values)
    except (ValidationError, ValueError, TypeError):
        return ActionPlan(status="unavailable", missing_inputs=missing or ["action_plan"])


def _dated_catalyst_or_review(plan: ActionPlan) -> bool:
    def concrete(value: Any) -> bool:
        text = str(value or "").strip()
        if not text:
            return False
        try:
            datetime.fromisoformat(text.replace("Z", "+00:00"))
            return True
        except (TypeError, ValueError):
            try:
                datetime.strptime(text, "%Y-%m-%d")
                return True
            except (TypeError, ValueError):
                return False

    if concrete(plan.review_at):
        return True
    return any(concrete(event.event_date) and event.date_kind in {"confirmed", "expected", "review"} for event in plan.catalyst_events)


_BOUNDED_HORIZON = re.compile(
    r"^(?:\d+\s*(?:d|day|days|w|week|weeks|m|month|months|q|quarter|quarters|y|year|years)|"
    r"\d{4}-\d{2}-\d{2}\s*(?:to|through|-)\s*\d{4}-\d{2}-\d{2})$",
    re.IGNORECASE,
)
_COMPARISON = re.compile(r"(?:<=|>=|<|>|=|below|above|under|over|at\s+least|at\s+most|falls?\s+to|rises?\s+to)", re.IGNORECASE)
_NUMBER = re.compile(r"(?<![A-Za-z0-9_])\d+(?:\.\d+)?(?:\s*[A-Za-z]{3})?(?![A-Za-z0-9_])")


def _bounded_horizon(value: Any) -> bool:
    text = " ".join(str(value or "").strip().split())
    if not text or text.casefold() in {"whenever", "indefinite", "soon", "long term", "as needed"}:
        return False
    return bool(_BOUNDED_HORIZON.fullmatch(text))


def _observable_condition(
    condition: Any,
    *,
    kind: str,
    target: FutureTarget | None,
    target_available: bool = False,
    stop_price: Any,
) -> bool:
    """Require an exit/invalidation rule that code can observe.

    Free-form prose such as ``Bad news`` is retained for the memo but cannot
    establish a measurable gate. A code-owned target or stop can make a
    clearly linked rule executable; otherwise a comparison and numeric/event
    observable must be present in the plan text.
    """
    text = " ".join(str(condition or "").strip().split())
    if not text:
        return False
    lower = text.casefold()
    if kind == "exit" and (target is not None or target_available) and any(token in lower for token in ("target", "valuation", "price", "multiple")):
        return True
    has_numeric = bool(_NUMBER.search(text))
    has_comparison = bool(_COMPARISON.search(text))
    has_event = any(token in lower for token in ("event", "earnings", "guidance", "catalyst", "review", "date", "filing"))
    metric = any(token in lower for token in ("eps", "revenue", "margin", "cash flow", "fcf", "debt", "leverage", "price", "quote", "drawdown", "volatility"))
    if kind == "invalidation" and stop_price is not None:
        # A stop rule may use the stop value supplied to the sizing service,
        # but the plan must still identify that stop/price boundary.
        try:
            stop_text = str(D(stop_price))
        except (TypeError, ValueError):
            stop_text = ""
        if ("stop" in lower or "price" in lower) and (has_numeric or stop_text and stop_text in text):
            return True
        if has_numeric and has_comparison:
            return True
    return has_numeric and has_comparison and (metric or has_event)


def _fresh(result: Mapping[str, Any] | Any) -> bool:
    if not isinstance(result, Mapping):
        return False
    return str(result.get("status") or result.get("freshness") or "").casefold() == "fresh"


def _recommendation_gate(
    *,
    item: Mapping[str, Any],
    payload: Mapping[str, Any],
    brief: Mapping[str, Any],
    v2: bool,
    instrument_identity: Mapping[str, Any],
    direction: str,
    entry: PriceRange | None,
    target: FutureTarget | None,
    valuation: ValuationBlock,
    payoff: PayoffBlock,
    portfolio_context: PortfolioContext,
    thesis: ThesisBlock,
    action_plan: ActionPlan,
    sizing: DecisionSizing,
    source_map: Mapping[str, Any],
    validated_facts: Mapping[str, Mapping[str, Any]] | None = None,
    as_of: str | None = None,
    research_contract: str | None = None,
    key_questions: Sequence[Mapping[str, Any]] | None = None,
    joint_review: Mapping[str, Any] | None = None,
    advisory_mode: str | None = None,
) -> RecommendationGate:
    """Run the authoritative v2 publishability gate.

    Every newly computed candidate is evaluated against this gate. Historical
    v1 rows remain readable because their stored projection is served without
    rebuilding it; a new projection cannot bypass the v2 requirements.
    """
    checks: list[RecommendationCheck] = []
    missing: list[str] = []

    def add(key: str, passed: bool, reason: str, *, unavailable: bool = False) -> None:
        status = "pass" if passed else "unavailable" if unavailable else "fail"
        checks.append(RecommendationCheck(key=key, status=status, reason=reason))
        if not passed:
            missing.append(key)

    identity_status = str(instrument_identity.get("status") or "").lower()
    add("instrument_identity", identity_status in {"consistent", "verified", "validated", "supported"}, "Instrument identity is code-validated." if identity_status in {"consistent", "verified", "validated", "supported"} else "A current code-validated instrument identity is required.", unavailable=not bool(instrument_identity))
    explicit_direction = str(item.get("direction") or payload.get("direction") or "").strip().lower() in {"long", "short"}
    add("direction", explicit_direction, "Direction is explicit." if explicit_direction else "Direction must be explicit; text inference cannot publish a recommendation.")
    strategy = str(item.get("strategy") or brief.get("strategy") or payload.get("strategy") or "").strip().lower()
    add("strategy", strategy in {"long_term", "trade"}, "Strategy is explicit." if strategy in {"long_term", "trade"} else "An explicit long_term or trade strategy is required.")
    horizon = str(item.get("horizon") or brief.get("horizon") or payload.get("horizon") or "").strip()
    horizon_ok = _bounded_horizon(horizon)
    add("horizon", horizon_ok, "A bounded decision horizon is recorded." if horizon_ok else "A bounded decision horizon such as 3m or 12 months is required.")
    add("entry", entry is not None, "Entry is a supported dated price range." if entry is not None else "A supported entry condition and price are required.", unavailable=entry is None)
    usable_methods = [method for method in valuation.methods if method.status == "complete" and (getattr(method, "supported", True) is not False)]
    add("valuation", valuation.status == "complete" and bool(usable_methods), "At least one supported asset-appropriate valuation method is complete." if valuation.status == "complete" and usable_methods else "At least one supported valuation method is required.", unavailable=valuation.status == "unavailable")
    fact_index = validated_facts or {}
    reference_errors = item.get("_fact_reference_errors") or brief.get("_fact_reference_errors") or payload.get("_fact_reference_errors")
    reference_errors_present = bool(reference_errors)
    add("fact_references", not reference_errors_present, "All declared fact references resolve to repository-supported claims." if not reference_errors_present else "One or more declared fact references did not resolve to a repository-supported claim.")
    if research_contract == FIVE_QUESTION_CONTRACT:
        questions = list(key_questions or [])
        question_keys = {str(_dict(item).get("key") or "") for item in questions}
        question_evidence_ok = (
            len(questions) == 5
            and question_keys == {"opportunity", "valuation", "catalyst", "downside", "portfolio_action"}
            and all(str(_dict(item).get("answer") or "").strip() for item in questions)
            and all(str(_dict(item).get("evidence_status") or "") == "complete" for item in questions)
        )
        add("five_question_evidence", question_evidence_ok, "All five decision questions have current selected evidence." if question_evidence_ok else "All five decision questions require concise answers and complete current evidence.", unavailable=not questions)
        # The local Laya packet is built before either receipt exists.  Its
        # deterministic preview must retain every ordinary five-question and
        # portfolio gate while omitting only this circular receipt-backed
        # check.  Persistence callers leave ``advisory_mode`` unset and keep
        # the normal joint-review requirement.
        if advisory_mode != "laya_preview":
            review = _dict(joint_review)
            review_ok = joint_review_allows_recommendation(review)
            add("joint_decision_review", review_ok, "The local Laya/Astra review is receipt-backed and resolved." if review_ok else "A receipt-backed Laya/Astra review must agree or accept a reasoned override before Recommend.", unavailable=not review or str(review.get("status") or "") == "unavailable")
    candidate_issuer = str(item.get("issuer") or item.get("issuer_name") or item.get("instrument") or item.get("name") or item.get("ticker") or "").strip()
    thesis_ids_ok = bool(thesis.supporting_claim_ids) and all(
        identifier in fact_index
        and _thesis_fact_is_candidate_bound(
            fact_index[identifier],
            candidate_issuer=candidate_issuer,
            thesis_source_refs=thesis.source_refs,
            source_map=source_map,
        )
        for identifier in thesis.supporting_claim_ids
    )
    thesis_refs_ok = bool(thesis.source_refs) and all(ref in source_map and str(_dict(source_map.get(ref)).get("content") or _dict(source_map.get(ref)).get("original_content") or "").strip() for ref in thesis.source_refs)
    thesis_ok = thesis.status == "complete" and bool(thesis.variant_view.strip()) and bool(thesis.market_expectation.strip()) and bool(thesis.why_now.strip()) and bool(thesis.strongest_opposing_explanation.strip()) and thesis_ids_ok and thesis_refs_ok
    add("thesis", thesis_ok, "Thesis, market expectation, why-now mechanism, opposing explanation and code-validated candidate facts are present." if thesis_ok else "A differentiated thesis, opposing explanation and code-validated candidate-specific supporting facts are required.")
    payoff_ok = payoff.status in {"complete", "partial"} and bool(payoff.scenarios)
    add("payoff", payoff_ok, "Directional scenario payoff is available." if payoff_ok else "A usable directional scenario payoff is required.", unavailable=payoff.status == "unavailable")
    base_scenario = next((scenario for scenario in payoff.scenarios if scenario.name == "base"), None)
    base_pnl = D(base_scenario.pnl_per_share) if base_scenario is not None else None
    directional_benefit = base_pnl is not None and base_pnl > 0
    hedge_refs_ok = bool(action_plan.hedge_objective and action_plan.hedge_source_refs) and all(ref in source_map and str(_dict(source_map.get(ref)).get("content") or _dict(source_map.get(ref)).get("original_content") or "").strip() for ref in action_plan.hedge_source_refs)
    economic_ok = bool(directional_benefit or hedge_refs_ok)
    add("economic_objective", economic_ok, "The base case has favorable directional payoff or an evidenced portfolio hedge objective." if economic_ok else "A favorable directional base payoff or an evidenced portfolio hedge objective is required.")
    exit_ok = action_plan.status in {"complete", "partial"} and _observable_condition(action_plan.exit_condition, kind="exit", target=target, target_available=bool(valuation.scenarios), stop_price=None)
    add("exit", exit_ok, "Executable exit condition is recorded." if exit_ok else "An executable price, valuation or dated event exit condition is required.")
    stop_value = item.get("stop_price") or brief.get("stop_price") or payload.get("stop_price")
    invalidation_ok = _observable_condition(action_plan.invalidation_condition, kind="invalidation", target=target, stop_price=stop_value)
    add("invalidation", invalidation_ok, "Measurable invalidation condition is recorded." if invalidation_ok else "A measurable invalidation condition or explicit observable stop rule is required.")
    add("catalyst_or_review", _dated_catalyst_or_review(action_plan), "A dated catalyst or thesis review is recorded." if _dated_catalyst_or_review(action_plan) else "A dated catalyst or explicit dated thesis review is required.")
    def retained_material_source(ref: str) -> bool:
        source = _dict(source_map.get(ref))
        content = source.get("content") or source.get("original_content")
        freshness = str(source.get("freshness") or source.get("freshness_status") or "").casefold()
        return bool(str(content or "").strip()) and freshness not in {"stale", "old", "expired", "unavailable"}

    fresh = bool(
        entry
        and entry.as_of
        and entry.source_refs
        and all(
            ref in source_map
            and retained_material_source(ref)
            and _fresh(evaluate_price_freshness(entry.as_of, as_of=as_of or "", source=_dict(source_map.get(ref))))
            for ref in entry.source_refs
        )
    )
    add("fresh_material_price", fresh, "Entry price carries a dated retained source." if fresh else "A dated retained material price is required.", unavailable=not fresh)
    fact_refs = list(thesis.supporting_claim_ids)
    # Every code-owned valuation input may carry a different material fact
    # (for example FCF, debt, cash and diluted shares in a DCF bridge). A
    # fresh thesis EPS claim cannot silently refresh a stale bridge component.
    for method in valuation.methods:
        for raw_input in method.inputs:
            if not isinstance(raw_input, Mapping):
                continue
            if str(raw_input.get("kind") or "assumption").casefold() != "fact":
                continue
            fact_refs.extend(str(identifier) for identifier in raw_input.get("fact_claim_ids", []) if str(identifier).strip())
    fact_refs = list(dict.fromkeys(fact_refs))
    fresh_fact_failures: list[str] = []
    for identifier in fact_refs:
        fact = fact_index.get(identifier)
        source = _dict(source_map.get(str(fact.get("source_ref") or ""))) if fact else {}
        evaluation = evaluate_fact_freshness(fact, source, as_of=as_of or "") if fact else {"status": "unknown"}
        if not fact or not _fresh(evaluation) or not str(source.get("content") or source.get("original_content") or "").strip() or not source_has_primary_coverage(source):
            fresh_fact_failures.append(identifier)
    fresh_facts = bool(fact_refs) and not fresh_fact_failures
    fact_reason = "Supporting and valuation facts have dated observations within policy." if fresh_facts else "Every supporting and valuation fact requires an exact dated retained observation that remains fresh for this decision."
    add("fresh_material_facts", fresh_facts, fact_reason, unavailable=not fresh_facts)
    # A configured constraint with missing exposure data is material.  An
    # explicitly unconfigured optional limit is not applicable and should not
    # block an otherwise complete recommendation.
    def _material_portfolio_unavailable(check: PortfolioCheck) -> bool:
        if check.status != "unavailable":
            return False
        reason = check.reason.casefold()
        return not (reason.startswith("no configured") or reason.startswith("no maximum holding count"))

    portfolio_ok = (
        portfolio_context.status != "unavailable"
        and not portfolio_context.missing_inputs
        and not any(check.status == "fail" or _material_portfolio_unavailable(check) for check in portfolio_context.checks)
    )
    add("portfolio_sizing", portfolio_ok, "Applicable portfolio and eligibility checks pass." if portfolio_ok else "Complete applicable portfolio sizing and eligibility checks are required.", unavailable=portfolio_context.status == "unavailable")
    proposed = sizing.recommended_shares is not None and sizing.recommended_shares > 0 and bool((sizing.allocation_rationale or "").strip())
    add("recommended_allocation", proposed and (sizing.maximum_permitted_shares is None or sizing.recommended_shares <= sizing.maximum_permitted_shares), "A positive proposed allocation has a rationale and fits code capacity." if proposed else "Capacity alone is not an order quantity; a positive proposed allocation and rationale are required.", unavailable=sizing.maximum_permitted_shares is None)
    return RecommendationGate(status="pass" if not missing else "blocked", checks=checks, missing_inputs=list(dict.fromkeys(missing)))


def _lifecycle_block(outcome: str | None, action_plan: ActionPlan) -> LifecycleBlock:
    state = {"recommend": "recommended", "watchlist": "watchlist", "decline": "declined"}.get(outcome)
    return LifecycleBlock(state=state, status="complete" if state else "partial", reopen_when=list(action_plan.missing_inputs))


def _enforce_case_resources(decisions: list[CanonicalCandidateDecision], snapshot: Mapping[str, Any]) -> None:
    """Reserve cash and holding slots once across all policy groups."""
    def identity_key(item: Mapping[str, Any]) -> tuple[str, str]:
        identity = _dict(item.get("instrument_identity"))
        stable = str(identity.get("asset_id") or identity.get("id") or item.get("ticker") or "").strip().upper()
        direction = str(item.get("direction") or "long").strip().lower()
        return stable, direction

    policy = _dict(snapshot.get("portfolio_policy"))
    try:
        max_positions = int(policy["max_positions"]) if policy.get("max_positions") is not None else None
    except (ValueError, TypeError):
        max_positions = None
    symbols = {
        (str(item.get("asset_id") or item.get("instrument_id") or item.get("symbol") or item.get("ticker") or "").strip().upper(), str(item.get("direction") or item.get("side") or ("short" if str(item.get("quantity") or "").startswith("-") else "long")).strip().lower())
        for item in snapshot.get("positions", []) if isinstance(item, Mapping) and (item.get("symbol") or item.get("ticker") or item.get("asset_id") or item.get("instrument_id"))
    }
    cash_remaining: dict[tuple[str | None, str | None], Any] = {}
    approved_remaining = D(snapshot.get("approved_budget"))
    approved_currency = str(snapshot.get("approved_budget_currency") or "").upper()
    for candidate in decisions:
        sizing = candidate.sizing
        if candidate.outcome == "decline" or not sizing.shares or not sizing.entry_price:
            continue
        candidate_identity = identity_key(candidate.model_dump(mode="python", exclude_none=False))
        if max_positions is not None and candidate_identity not in symbols and len(symbols) >= max_positions:
            candidate.material_blockers.append(_blocker("max_positions", "constraint", f"The combined candidate allocation exceeds the configured maximum of {max_positions} distinct holdings.", owner="Portfolio policy"))
            sizing.execution_state = candidate.execution_state = "awaiting_input"
            sizing.missing_inputs = _unique([*sizing.missing_inputs, "available_holding_slot"])
            sizing.recommend_requires = _unique([*sizing.recommend_requires, "available_holding_slot"])
            candidate.outcome = "watchlist" if candidate.watch_triggers else None
            continue
        available = D(sizing.available_cash)
        price = D(sizing.entry_price)
        if approved_remaining is not None and approved_currency and price is not None:
            native_remaining = _convert_amount(approved_remaining, approved_currency, sizing.currency, snapshot.get("fx_rates"))
            if native_remaining is None:
                sizing.execution_state = candidate.execution_state = "awaiting_input"
                sizing.missing_inputs = _unique([*sizing.missing_inputs, f"fx_rate:{approved_currency}_{sizing.currency}"])
                candidate.outcome = "watchlist" if candidate.watch_triggers else None
                candidate.material_blockers.append(_blocker("shared_budget_fx", "sizing", "The combined allocation requires an explicit FX observation.", owner="Portfolio inputs"))
                continue
            max_shares = max(0, int(native_remaining / price))
            if max_shares < sizing.shares:
                sizing.shares = max_shares
                sizing.notional = str(price * max_shares)
                sizing.checks = [check for check in sizing.checks if check.get("check") != "whole_share_count"]
                sizing.checks.append({"check": "shared_approved_budget", "status": "pass" if max_shares else "unavailable", "detail": f"The remaining shared approved budget supports {max_shares} whole shares."})
                sizing.formula = "min(individual_size, floor(remaining_approved_budget / entry_price))"
            if not sizing.shares:
                sizing.execution_state = candidate.execution_state = "awaiting_input"
                sizing.missing_inputs = _unique([*sizing.missing_inputs, "unallocated_approved_budget"])
                candidate.material_blockers.append(_blocker("shared_approved_budget", "sizing", "Other candidates use the shared approved budget.", owner="Portfolio inputs"))
                candidate.outcome = "watchlist" if candidate.watch_triggers else None
                continue
        if sizing.direction == "long" and available is not None and price is not None:
            key = (sizing.account_id, sizing.currency)
            remaining = cash_remaining.setdefault(key, available)
            shares = min(sizing.shares, max(0, int(remaining / price)))
            notional = price * shares
            if shares != sizing.shares:
                sizing.shares = shares
                sizing.notional = str(notional)
                sizing.checks = [check for check in sizing.checks if check.get("check") != "whole_share_count"]
                sizing.checks.append({"check": "shared_account_cash", "status": "pass" if shares else "unavailable", "detail": f"The remaining {remaining} {sizing.currency} in this account supports {shares} whole shares at {price}."})
                sizing.formula = "min(individual_size, floor(remaining_account_cash / entry_price))"
            cash_remaining[key] = remaining - notional
            sizing.resulting_cash = str(cash_remaining[key])
            if not shares:
                sizing.execution_state = candidate.execution_state = "awaiting_input"
                sizing.missing_inputs = _unique([*sizing.missing_inputs, "unallocated_account_cash"])
                candidate.material_blockers.append(_blocker("shared_account_cash", "sizing", "Other candidates use the available cash in this account; additional funding or a smaller combined allocation is required.", owner="Portfolio inputs"))
                candidate.outcome = "watchlist" if candidate.watch_triggers else None
                continue
        if approved_remaining is not None and approved_currency:
            allocated = _convert_amount(D(sizing.notional), sizing.currency, approved_currency, snapshot.get("fx_rates"))
            if allocated is not None:
                approved_remaining -= allocated
        symbols.add(candidate_identity)
    # Reconcile the basket-wide clipping back into the canonical capacity and
    # proposed-allocation fields. A user-proposed quantity that no longer fits
    # shared cash or holding limits is blocked; it is never silently rewritten.
    for candidate in decisions:
        sizing = candidate.sizing
        if sizing.shares is not None:
            sizing.maximum_permitted_shares = min(sizing.maximum_permitted_shares if sizing.maximum_permitted_shares is not None else sizing.shares, sizing.shares)
            if sizing.planned_loss_per_share is not None:
                sizing.planned_loss = quantize(D(sizing.planned_loss_per_share) * sizing.shares)
        if sizing.recommended_shares is not None and sizing.entry_price is not None:
            entry = D(sizing.entry_price)
            sizing.recommended_notional = quantize(entry * sizing.recommended_shares) if entry is not None else None
            if sizing.planned_loss_per_share is not None:
                sizing.recommended_planned_loss = quantize(D(sizing.planned_loss_per_share) * sizing.recommended_shares)
        if sizing.recommended_shares is not None and sizing.maximum_permitted_shares is not None and sizing.recommended_shares > sizing.maximum_permitted_shares:
            candidate.recommendation_gate.status = "blocked"
            candidate.recommendation_gate.missing_inputs = _unique([*candidate.recommendation_gate.missing_inputs, "recommended_allocation"])
            candidate.material_blockers.append(_blocker("recommendation:recommended_allocation", "constraint", "The proposed allocation exceeds the combined basket capacity after shared limits.", owner="Portfolio policy"))
            if candidate.outcome == "recommend":
                candidate.outcome = "watchlist" if candidate.watch_triggers else None
            candidate.execution_state = "awaiting_input"
            sizing.execution_state = "awaiting_input"
    # Basket-level position, sector and short-margin checks use the aggregate
    # proposed allocations. Per-candidate checks alone would let two ideas
    # each pass a 40% limit while the combined case exceeds it.
    settings = _dict(snapshot.get("risk_settings"))
    fx_rates = snapshot.get("fx_rates") if isinstance(snapshot, Mapping) else None
    base_currency = str(snapshot.get("base_currency") or snapshot.get("portfolio_currency") or snapshot.get("currency") or "").upper() or None
    reference = next((item.portfolio_context.before for item in decisions if item.portfolio_context.before), {})
    if base_currency is None and isinstance(reference, Mapping):
        base_currency = str(reference.get("base_currency") or "").upper() or None
    total_value = D(reference.get("total_value")) if isinstance(reference, Mapping) else None
    if total_value is not None and total_value > 0 and base_currency:
        existing_positions = {str(key).strip().upper(): D(value) for key, value in (reference.get("position_weights") or {}).items() if D(value) is not None}
        existing_sectors = {str(key).strip(): D(value) for key, value in (reference.get("sector_weights") or {}).items() if D(value) is not None}
        basket_positions: dict[str, Decimal] = {}
        basket_sectors: dict[str, Decimal] = {}
        basket_short = D(reference.get("gross_short")) or Decimal("0")
        active = [item for item in decisions if item.outcome == "recommend" and item.sizing.recommended_notional is not None]
        for item in active:
            amount = D(item.sizing.recommended_notional)
            converted = _convert_amount(amount, item.sizing.currency, base_currency, fx_rates) if amount is not None else None
            if converted is None:
                continue
            stable = str(item.instrument or item.ticker).strip().upper()
            basket_positions[stable] = basket_positions.get(stable, Decimal("0")) + converted / total_value
            if item.sector:
                basket_sectors[item.sector] = basket_sectors.get(item.sector, Decimal("0")) + converted / total_value
            if item.direction == "short":
                basket_short += converted
        max_position = D(settings.get("max_position_weight"))
        max_sector = D(settings.get("max_sector_weight"))
        if max_position is not None:
            for item in active:
                stable = str(item.instrument or item.ticker).strip().upper()
                resulting = (existing_positions.get(stable) or Decimal("0")) + basket_positions.get(stable, Decimal("0"))
                if resulting > max_position:
                    item.recommendation_gate.status = "blocked"
                    item.recommendation_gate.missing_inputs = _unique([*item.recommendation_gate.missing_inputs, "max_position_weight"])
                    item.material_blockers.append(_blocker("recommendation:max_position_weight", "constraint", "The combined proposed basket exceeds the configured maximum position weight.", owner="Portfolio policy"))
                    item.outcome = "watchlist" if item.watch_triggers else None
        if max_sector is not None:
            for item in active:
                if not item.sector:
                    continue
                resulting = (existing_sectors.get(item.sector) or Decimal("0")) + basket_sectors.get(item.sector, Decimal("0"))
                if resulting > max_sector:
                    item.recommendation_gate.status = "blocked"
                    item.recommendation_gate.missing_inputs = _unique([*item.recommendation_gate.missing_inputs, "max_sector_weight"])
                    item.material_blockers.append(_blocker("recommendation:max_sector_weight", "constraint", "The combined proposed basket exceeds the configured maximum sector weight.", owner="Portfolio policy"))
                    item.outcome = "watchlist" if item.watch_triggers else None
        max_short = D(settings.get("max_short_notional") or settings.get("max_gross_short") or settings.get("max_margin_notional"))
        if max_short is not None:
            max_short_base = _convert_amount(max_short, str(settings.get("max_short_notional_currency") or base_currency).upper(), base_currency, fx_rates)
            if max_short_base is not None and basket_short > max_short_base:
                for item in active:
                    if item.direction != "short":
                        continue
                    item.recommendation_gate.status = "blocked"
                    item.recommendation_gate.missing_inputs = _unique([*item.recommendation_gate.missing_inputs, "max_short_notional"])
                    item.material_blockers.append(_blocker("recommendation:max_short_notional", "constraint", "The combined proposed short basket exceeds the configured short margin/notional limit.", owner="Portfolio policy"))
                    item.outcome = "watchlist" if item.watch_triggers else None

    # Resource clipping changes the proposed quantity and therefore the
    # payoff, portfolio after-state, and lifecycle projection. Rebuild those
    # dependent blocks from the final sizing values so the canonical object
    # cannot show a pre-clipping quantity beside a post-clipping gate.
    for candidate in decisions:
        sizing = candidate.sizing
        portfolio_sizing = sizing.model_dump(mode="python", exclude_none=False)
        if sizing.recommended_notional is not None:
            portfolio_sizing["notional"] = sizing.recommended_notional
        if sizing.recommended_planned_loss is not None:
            portfolio_sizing["planned_loss"] = sizing.recommended_planned_loss
        try:
            portfolio_raw = build_portfolio_context(
                snapshot,
                candidate={
                    "ticker": candidate.ticker,
                    "instrument": candidate.instrument or candidate.ticker,
                    "issuer": candidate.issuer,
                    "sector": candidate.sector,
                    "direction": candidate.direction,
                    "currency": sizing.currency,
                    "borrow_available": sizing.borrow_available,
                    "planned_loss": sizing.planned_loss,
                },
                sizing=portfolio_sizing,
                as_of=candidate.as_of,
            )
            candidate.portfolio_context = PortfolioContext.model_validate(portfolio_raw)
        except (ValidationError, ValueError, TypeError):
            candidate.portfolio_context = PortfolioContext(status="unavailable", missing_inputs=["portfolio_projection"])
        try:
            payoff_raw = build_payoff(
                candidate.valuation.model_dump(mode="python", exclude_none=False),
                sizing.risk_entry_price or (candidate.entry.lower if candidate.entry and candidate.direction == "short" else _entry_price(candidate.entry, candidate.direction)),
                direction=candidate.direction,
                horizon=candidate.horizon,
                stop_price=sizing.stop_price,
                shares=sizing.recommended_shares if sizing.recommended_shares is not None else sizing.shares,
                opportunity_cost=build_opportunity_cost(
                    snapshot,
                    principal=sizing.recommended_notional,
                    account_id=sizing.account_id,
                    currency=sizing.currency or (candidate.entry.currency if candidate.entry else None),
                    direction=candidate.direction,
                    horizon=candidate.horizon,
                    as_of=candidate.as_of,
                    benchmark_ticker=candidate.action_plan.benchmark_ticker,
                    benchmark_rationale=candidate.action_plan.benchmark_rationale,
                    source_refs=candidate.entry.source_refs if candidate.entry else [],
                ),
                source_refs=candidate.entry.source_refs if candidate.entry else [],
            )
            candidate.payoff = PayoffBlock.model_validate(payoff_raw)
        except (ValidationError, ValueError, TypeError):
            candidate.payoff = PayoffBlock(status="unavailable", missing_inputs=["payoff_projection"])
        portfolio_ok = (
            candidate.portfolio_context.status != "unavailable"
            and not candidate.portfolio_context.missing_inputs
            and not any(check.status == "fail" for check in candidate.portfolio_context.checks)
        )
        if not portfolio_ok:
            candidate.recommendation_gate.status = "blocked"
            candidate.recommendation_gate.missing_inputs = _unique([*candidate.recommendation_gate.missing_inputs, "portfolio_sizing"])
        candidate.lifecycle = _lifecycle_block(candidate.outcome, candidate.action_plan)


def build_case_decision(
    run_id: str,
    output: AgentOutputPayload | Mapping[str, Any] | BaseModel | None,
    snapshot: Mapping[str, Any] | None = None,
    sources: Mapping[str, Any] | Sequence[Any] | None = None,
    previous_revision: int = 0,
    *,
    as_of: str | None = None,
    review_receipts: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    advisory_mode: str | None = None,
    valuation_research_contexts: Mapping[str, Mapping[str, Any]] | None = None,
) -> CanonicalCaseDecision:
    """Build one current case decision; repository code persists it later.

    ``advisory_mode='laya_preview'`` is an internal pure projection used to
    prepare the bounded local classifier packet before review receipts exist.
    It omits only the circular joint-review check; all other gates and the
    basket-wide resource enforcement remain authoritative.
    """
    payload = _payload_dict(output)
    snapshot = _effective_snapshot(snapshot)
    source_map = _source_map(sources)
    try:
        revision = max(1, int(previous_revision) + 1)
    except (TypeError, ValueError):
        revision = 1
    case_as_of = _as_of(as_of) or _as_of(payload.get("created_at")) or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    research_contract = str(payload.get("research_contract") or snapshot.get("research_contract") or "").strip() or None
    review_receipts = review_receipts or {}
    shared_evidence = _evidence(payload, source_map)
    validated_facts = _validated_fact_map(payload)
    rows = _candidate_rows(payload)
    deterministic_market = snapshot.get("deterministic_market")
    deterministic_by_ticker: dict[str, dict[str, Any]] = {}
    if isinstance(deterministic_market, Mapping) and isinstance(deterministic_market.get("candidates"), list):
        for raw_market in deterministic_market["candidates"]:
            item_market = _dict(raw_market)
            symbol = str(item_market.get("ticker") or item_market.get("symbol") or "").strip().upper()
            if symbol:
                deterministic_by_ticker[symbol] = item_market
    conflicted_price_sources = {
        str(ref)
        for market in deterministic_by_ticker.values()
        if _dict(market.get("instrument_identity")).get("status") == "conflict"
        for ref in (market.get("source_refs") or [])
        if ref not in (_dict(market.get("instrument_identity")).get("source_refs") or [])
    }
    if conflicted_price_sources:
        # Quote matching can establish the contents of a bar, but cannot
        # establish that a reused ticker belongs to the claimed issuer.
        # Retain the statement/history while removing current fact status.
        shared_evidence = [
            item.model_copy(update={"kind": "unknown", "validation_status": "unavailable"})
            if conflicted_price_sources.intersection(item.source_refs) else item
            for item in shared_evidence
        ]
    allocation_mode = payload.get("allocation_mode") or _dict(payload.get("decision_brief")).get("allocation_mode") or "combined"
    if allocation_mode not in {"alternatives", "combined"}:
        allocation_mode = "combined"
    # Resolve each candidate's own policy first. Only candidates with the
    # same account and spending envelope share an allocation budget; every
    # row retains its loss, stop and account eligibility requirements.
    # Ticker symbols are not stable identities: duplicate rows may represent
    # different accounts, directions or listings. Keep shared sizing keyed by
    # the frozen asset identity so one row cannot overwrite another.
    shared_sizing_by_identity: dict[tuple[Any, ...], dict[str, Any]] = {}
    sizing_groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    brief = _dict(payload.get("decision_brief"))
    for row in rows:
        stance = str(row.get("stance") or payload.get("stance") or payload.get("decision_disposition") or "").strip().lower()
        row_ticker = str(row.get("ticker") or row.get("symbol") or row.get("instrument") or "").strip().upper()
        identity_conflict = _dict(deterministic_by_ticker.get(row_ticker, {}).get("instrument_identity")).get("status") == "conflict"
        if allocation_mode == "alternatives" or stance in {"avoid", "reject", "decline"} or identity_conflict:
            continue
        direction_text = " ".join(str(row.get(key) or "") for key in ("rationale", "entry_advice", "entry_plan", "stance"))
        direction = _direction(row, payload, direction_text)
        policy = _policy_inputs(snapshot, row, direction)
        budget = policy.get("approved_budget", policy.get("budget"))
        currency = policy.get("budget_currency")
        if budget is None or not currency:
            continue
        entry, _ = _parse_price_range(row.get("entry_zone") or brief.get("entry_zone") or payload.get("entry_zone"), source_map)
        key = (policy.get("account_id"), policy.get("strategy"), str(budget), currency, policy.get("policy_status"))
        batch_row = {
            "ticker": str(row.get("ticker") or row.get("symbol") or row.get("instrument") or "").strip().upper(),
            "entry_price": _entry_price(entry, direction),
            "entry_lower_price": entry.lower if entry else None,
            "entry_upper_price": entry.upper if entry else None,
            "notional_cap_price": entry.upper if entry else None,
            "risk_entry_price": (entry.lower if direction == "short" else entry.upper) if entry else None,
            "currency": entry.currency if entry else row.get("currency") or currency,
            "direction": direction,
            "allocation_weight": row.get("allocation_weight", row.get("weight")),
            "account_id": policy.get("account_id"),
            "risk_budget": policy.get("risk_budget"),
            "risk_budget_currency": policy.get("risk_budget_currency") or currency,
            "stop_price": row.get("stop_price") or brief.get("stop_price") or payload.get("stop_price"),
            "stop_price_currency": row.get("stop_price_currency") or brief.get("stop_price_currency") or payload.get("stop_price_currency"),
            "eligible_account": policy.get("eligible_account"),
            "recommend_requires": policy.get("recommend_requires", []),
            "borrow_available": None,
            "short_permission": None,
            "borrow_cost_status": None,
            "margin_terms_confirmed": None,
            "margin_available": None,
            "margin_currency": None,
            "recommended_shares": row.get("recommended_shares"),
            "allocation_rationale": row.get("allocation_rationale"),
        }
        asset_identity = _dict(deterministic_by_ticker.get(batch_row["ticker"], {}).get("instrument_identity"))
        if direction == "short":
            short_inputs = _short_operational_inputs(
                snapshot,
                ticker=batch_row["ticker"],
                account_id=policy.get("account_id"),
                market_packet=deterministic_by_ticker.get(batch_row["ticker"], {}),
                asset_id=asset_identity.get("asset_id") or asset_identity.get("id"),
                as_of=case_as_of,
            )
            batch_row.update(short_inputs)
        batch_row["_sizing_identity"] = (batch_row["ticker"], direction, policy.get("account_id"), asset_identity.get("asset_id") or asset_identity.get("id") or row.get("instrument") or batch_row["ticker"])
        sizing_groups.setdefault(key, []).append(batch_row)
    for key, batch_rows in sizing_groups.items():
        if len(batch_rows) < 2:
            continue
        account_id, _strategy, budget, currency, policy_status = key
        shared_result = calculate_multi_candidate_sizing(batch_rows, budget=budget, budget_currency=currency, account_id=account_id, snapshot=snapshot, risk_settings=_dict(snapshot.get("risk_settings")), fx_rates=snapshot.get("fx_rates"), policy_status=policy_status)
        if not shared_result.get("items"):
            for row in batch_rows:
                shared_sizing_by_identity[row["_sizing_identity"]] = {"execution_state": "failed", "direction": row["direction"], "currency": row["currency"], "reason": shared_result.get("reason", "The combined allocation is invalid."), "missing_inputs": shared_result.get("missing_inputs", []), "policy_status": policy_status or "unconfigured"}
        for row, item in zip(batch_rows, shared_result.get("items", [])):
            if item.get("ticker"):
                shared_sizing_by_identity[row["_sizing_identity"]] = item
    decisions: list[CanonicalCandidateDecision] = []
    for row in rows:
        ticker = str(row.get("ticker") or row.get("symbol") or row.get("instrument") or "").strip().upper()
        if not ticker:
            continue
        if research_contract == FIVE_QUESTION_CONTRACT:
            # The provider proposal is retained in the payload, while this
            # projection replaces every evidence/verification field with the
            # repository's current validated fact view and immutable receipts.
            aggregate_questions = _dict(payload.get("decision_brief")).get("key_questions") if len(rows) == 1 else []
            if not row.get("laya_response") and len(rows) == 1 and payload.get("laya_response"):
                row["laya_response"] = payload.get("laya_response")
            row["key_questions"] = project_key_questions(row.get("key_questions") or aggregate_questions, validated_facts)
            row["joint_review"] = joint_review_from_receipts(
                candidate=row,
                receipts=review_receipts.get(ticker, []),
                reviewed_at=case_as_of,
            )
        market_packet = deterministic_by_ticker.get(ticker, {})
        # Only the frozen code-owned market context can supply identity
        # validation.  Provider output cannot clear or manufacture this gate.
        instrument_identity = _dict(market_packet.get("instrument_identity"))
        identity_conflict = instrument_identity.get("status") == "conflict"
        identity_reason = str(instrument_identity.get("reason") or "Instrument identity must be resolved before numerical use.")[:4_000]
        brief = _dict(payload.get("decision_brief"))
        merged_text = " ".join(str(row.get(key) or "") for key in ("rationale", "entry_advice", "entry_plan", "target_price_basis", "stance")) + " " + str(payload.get("summary") or "") + " " + str(payload.get("analysis") or "")
        direction = _direction(row, payload, merged_text)
        source_entry = row.get("entry_zone") if row.get("entry_zone") is not None else brief.get("entry_zone") if brief.get("entry_zone") is not None else payload.get("entry_zone")
        entry, entry_issue = _parse_price_range(source_entry, source_map)
        source_target = row.get("future_target")
        if source_target is None:
            source_target = row.get("target")
        if source_target is None:
            source_target = brief.get("future_target") or brief.get("target")
        target_price = row.get("target_price")
        if target_price is None:
            target_price = brief.get("target_price", payload.get("target_price"))
        target_currency = row.get("target_price_currency")
        if target_currency is None:
            target_currency = brief.get("target_price_currency", payload.get("target_price_currency"))
        target_as_of = row.get("target_price_as_of")
        if target_as_of is None:
            target_as_of = brief.get("target_price_as_of", payload.get("target_price_as_of"))
        target_refs = row.get("target_price_source_refs")
        if target_refs is None:
            target_refs = brief.get("target_price_source_refs", payload.get("target_price_source_refs", []))
        target_basis = row.get("target_price_basis")
        if target_basis is None:
            target_basis = brief.get("target_price_basis", payload.get("target_price_basis"))
        target_horizon = row.get("horizon")
        if target_horizon is None:
            target_horizon = brief.get("horizon", payload.get("horizon"))
        target, target_issue = _parse_future_target(source_target, fallback_price=target_price, currency=target_currency, as_of=target_as_of, source_refs=target_refs, basis=target_basis, horizon=target_horizon, source_map=source_map)
        blockers: list[DecisionBlocker] = []
        if identity_conflict:
            entry = None
            target = None
            blockers.append(_blocker("instrument_identity", "data_quality", identity_reason, owner="Researcher", reopen_when="Verify the current issuer and listing from dated primary or independent asset evidence, then obtain the matching issuer's price history.", source_refs=instrument_identity.get("source_refs") or []))
        if entry_issue:
            blockers.append(_blocker("entry_price_support", "evidence", entry_issue, owner="Researcher"))
        if target_issue:
            blockers.append(_blocker("future_target_support", "evidence", target_issue, owner="CIO"))
        policy = _policy_inputs(snapshot, row, direction)
        entry_currency = entry.currency if entry else str(row.get("currency") or "").strip().upper() or None
        sizing_kwargs: dict[str, Any] = {
            "entry_price": _entry_price(entry, direction),
            "entry_lower_price": entry.lower if entry else None,
            "entry_upper_price": entry.upper if entry else None,
            "notional_cap_price": entry.upper if entry else None,
            "risk_entry_price": (entry.lower if direction == "short" else entry.upper) if entry else None,
            "currency": entry_currency,
            "account_id": policy.pop("account_id", None),
            "snapshot": snapshot,
            "direction": direction,
            "risk_settings": policy.pop("risk_settings", {}),
            "fx_rates": _dict(snapshot).get("fx_rates") if isinstance(snapshot, Mapping) else None,
            "risk_budget": policy.pop("risk_budget", None),
            "risk_budget_currency": policy.pop("risk_budget_currency", None) or policy.get("budget_currency"),
            "stop_price": row.get("stop_price") or brief.get("stop_price") or payload.get("stop_price"),
            "stop_price_currency": row.get("stop_price_currency") or brief.get("stop_price_currency") or payload.get("stop_price_currency"),
            "policy_status": policy.pop("policy_status", None),
            "recommend_requires": policy.pop("recommend_requires", []),
            "recommended_shares": row.get("recommended_shares"),
            "allocation_rationale": row.get("allocation_rationale") or brief.get("allocation_rationale"),
        }
        candidate_strategy = policy.pop("strategy", None) or row.get("strategy") or payload.get("strategy")
        max_positions = policy.pop("max_positions", None)
        existing_symbols = policy.pop("existing_symbols", set())
        sizing_kwargs.update(policy)
        asset_identity = _dict(instrument_identity)
        sizing_identity = (ticker, direction, sizing_kwargs.get("account_id"), asset_identity.get("asset_id") or asset_identity.get("id") or row.get("instrument") or ticker)
        # For short setups, the candidate must explicitly carry borrow/margin
        # evidence.  The calculator then follows the separate short path.
        if direction == "short":
            short_inputs = _short_operational_inputs(
                snapshot,
                ticker=ticker,
                account_id=sizing_kwargs.get("account_id"),
                market_packet=market_packet,
                asset_id=asset_identity.get("asset_id") or asset_identity.get("id"),
                as_of=case_as_of,
            )
            sizing_kwargs["operational_checks"] = short_inputs.pop("operational_checks", [])
            sizing_kwargs.update(short_inputs)
            sizing_kwargs["short_budget"] = sizing_kwargs.pop("budget", sizing_kwargs.pop("approved_budget", None))
            sizing_kwargs["margin_currency"] = short_inputs.get("margin_currency") or entry_currency
        try:
            shared_item = shared_sizing_by_identity.get(sizing_identity)
            if identity_conflict:
                raw_sizing = {"execution_state": "awaiting_input", "account_id": sizing_kwargs.get("account_id"), "direction": direction, "currency": entry_currency, "missing_inputs": ["instrument_identity"], "reason": identity_reason, "policy_status": sizing_kwargs.get("policy_status") or "unconfigured"}
            elif shared_item is not None:
                raw_sizing = shared_item
            else:
                raw_sizing = calculate_position_size(ticker, **{key: value for key, value in sizing_kwargs.items() if key not in {"policy_status", "recommend_requires"}}, policy_status=sizing_kwargs.pop("policy_status", None), recommend_requires=sizing_kwargs.pop("recommend_requires", []))
            sizing_data = {key: value for key, value in raw_sizing.items() if key in DecisionSizing.model_fields}
            sizing = DecisionSizing.model_validate(sizing_data)
        except (ValidationError, ValueError, TypeError) as exc:
            sizing = DecisionSizing(execution_state="failed", direction=direction, currency=entry_currency, missing_inputs=[], reason=f"Sizing service failed: {exc}", policy_status="unconfigured")
            blockers.append(_blocker("sizing_service", "execution", str(exc), owner="Code service"))
        if sizing.execution_state == "awaiting_input":
            for missing_input in sizing.missing_inputs:
                blockers.append(_blocker(f"sizing:{missing_input}", "sizing", f"Sizing requires {missing_input}.", owner="Portfolio inputs"))
        # Policy requirements can be decisive even when the calculator exits
        # early (for example a short setup with unknown borrow availability).
        # Keep those account restrictions visible alongside the first missing
        # numeric input instead of letting an operational failure hide them.
        for requirement in sizing.recommend_requires:
            if requirement not in sizing.missing_inputs:
                blockers.append(_blocker(f"sizing:{requirement}", "constraint", f"Recommendation requires {requirement}.", owner="Portfolio policy"))
        if sizing.execution_state == "failed":
            blockers.append(_blocker("sizing_constraints", "constraint", sizing.reason or "Sizing constraints failed.", owner="Code service"))
        position_limit_blocked = False
        try:
            max_position_count = int(max_positions) if max_positions is not None else None
        except (TypeError, ValueError):
            max_position_count = None
        if max_position_count is not None and ticker not in existing_symbols and len(existing_symbols) >= max_position_count:
            blockers.append(_blocker("max_positions", "constraint", f"The configured maximum of {max_position_count} distinct holdings is already reached.", owner="Portfolio policy"))
            position_limit_blocked = True
        # The workflow's deterministic packet is authoritative.  A present
        # packet entry may be empty/failed, and must not silently fall back to
        # model-authored simulation fields in that case.
        if "scenario" in market_packet:
            forecast_raw = market_packet.get("scenario")
        elif "forecast" in market_packet:
            forecast_raw = market_packet.get("forecast")
        else:
            forecast_raw = row.get("forecast") or row.get("simulation_snapshot") or payload.get("simulation_snapshot")
        force_forecast_reject = bool(payload.get("forecast_rejected")) or str(payload.get("model_acceptance_status") or "").strip().lower() == "rejected"
        scenario_assessment = row.get("scenario_assessment")
        if scenario_assessment is None:
            scenario_assessment = brief.get("scenario_assessment", payload.get("scenario_assessment"))
        scenario_reason = row.get("scenario_reason")
        if scenario_reason is None:
            scenario_reason = brief.get("scenario_reason", payload.get("scenario_reason"))
        forecast = _scenario_forecast(forecast_raw, force_reject=force_forecast_reject, assessment=scenario_assessment, scenario_reason=scenario_reason)
        if identity_conflict:
            forecast = ForecastAcceptance(calculation_status="not_run", data_quality_status="invalid", model_acceptance_status="rejected", scenario_assessment="do_not_use", scenario_reason=identity_reason, accepted=False, reasons=[identity_reason], source_refs=instrument_identity.get("source_refs") or [])
        if forecast is not None and (forecast.model_acceptance_status == "rejected" or forecast.data_quality_status == "invalid"):
            blockers.append(_blocker("forecast_data_quality", "data_quality", "; ".join(forecast.reasons) or "Forecast input data quality is not accepted.", owner="Researcher", source_refs=forecast.source_refs))
        trigger_values = row.get("watch_triggers") if row.get("watch_triggers") is not None else payload.get("watch_triggers")
        triggers, trigger_issues = _typed_triggers(trigger_values, source_map, supported_prices=_supported_trigger_prices(payload, ticker, entry, target))
        if identity_conflict:
            triggers = [trigger for trigger in triggers if trigger.type != "price"]
        explicit_watch_intent = str(row.get("stance") or payload.get("stance") or payload.get("decision_disposition") or "").strip().lower() in {"watch", "watchlist", "defer"}
        if entry is not None and not triggers and explicit_watch_intent and direction == "long":
            # A range is a watch setup only when the CIO explicitly says to
            # wait.  Keep its lower and upper bounds together; a blind
            # at-or-below trigger would be wrong for breakouts and shorts.
            threshold = entry.lower or entry.upper
            upper_threshold = entry.upper if entry.lower and entry.upper and entry.lower != entry.upper else None
            if threshold:
                if upper_threshold:
                    triggers.append(WatchTrigger(type="price", condition=f"Price enters the supported range {entry.lower}–{entry.upper} {entry.currency}.", operator="between", threshold=entry.lower, upper_threshold=entry.upper, currency=entry.currency, reopen_when="Re-evaluate sizing and evidence when price enters the supported entry range.", review_at=_as_of(payload.get("next_review_at")), status="active", source_refs=entry.source_refs))
                else:
                    triggers.append(WatchTrigger(type="price", condition=f"Price reaches the supported entry level {threshold} {entry.currency}.", operator="at_or_below", threshold=threshold, currency=entry.currency, reopen_when="Re-evaluate sizing and evidence when price reaches the supported entry level.", review_at=_as_of(payload.get("next_review_at")), status="active", source_refs=entry.source_refs))
        for issue in trigger_issues:
            blockers.append(_blocker("watch_trigger_support", "evidence", issue, owner="Researcher"))
        risks = _unique(row.get("risks") if isinstance(row.get("risks"), list) else brief.get("risks") if isinstance(brief.get("risks"), list) else payload.get("risks") if isinstance(payload.get("risks"), list) else [])
        catalysts = _unique(row.get("catalysts") if isinstance(row.get("catalysts"), list) else brief.get("catalysts") if isinstance(brief.get("catalysts"), list) else payload.get("catalysts") if isinstance(payload.get("catalysts"), list) else [])
        invalidation = _unique(row.get("invalidation_conditions") if isinstance(row.get("invalidation_conditions"), list) else brief.get("invalidation_conditions") if isinstance(brief.get("invalidation_conditions"), list) else payload.get("invalidation_conditions") if isinstance(payload.get("invalidation_conditions"), list) else [])
        for missing in _unique(row.get("missing_inputs") if isinstance(row.get("missing_inputs"), list) else payload.get("missing_inputs") if isinstance(payload.get("missing_inputs"), list) else []):
            blockers.append(_blocker(f"input:{missing}", "sizing" if "account" in missing.casefold() or "budget" in missing.casefold() else "unknown", missing))
        # Model missing_data is retained only as current blockers.  Historical
        # gap ledger attempts stay outside this projection.
        for missing in _unique(payload.get("missing_data") if isinstance(payload.get("missing_data"), list) else []):
            lowered = missing.casefold()
            if any(token in lowered for token in ("historical", "optional", "already resolved")):
                continue
            blockers.append(_blocker(f"evidence:{missing[:120]}", "evidence", missing, owner="Researcher"))
        # v2 code-owned blocks. Provider proposals are inputs only; the
        # valuation, payoff, portfolio context and publishability gate are
        # recalculated here from the frozen packet and sizing result.
        v2 = _v2_payload_present(row, payload, brief)
        thesis = _thesis_block(row, payload, brief)
        valuation_raw = build_valuation(
            _valuation_assumptions(row, payload, brief),
            asset_class=row.get("asset_class") or market_packet.get("asset_class") or payload.get("asset_class"),
            currency=entry_currency,
            horizon=str(row.get("horizon") or payload.get("horizon") or "") or None,
            as_of=case_as_of,
            source_refs=entry.source_refs if entry else [],
            validated_facts=validated_facts,
            issuer=row.get("issuer") or row.get("issuer_name") or row.get("instrument") or row.get("name"),
            source_records=source_map,
            research_context=(valuation_research_contexts or {}).get(ticker),
        )
        try:
            valuation = ValuationBlock.model_validate(valuation_raw)
        except (ValidationError, ValueError, TypeError):
            valuation = ValuationBlock(status="unavailable", missing_inputs=["valuation_projection"])
        # The selected supported calculator owns the price target. Legacy
        # provider target fields are normalized before this calculation and
        # may legitimately be empty: forecast assumptions are not price
        # facts. A missing account or position can block sizing without
        # erasing an independently supported research valuation.
        selected_valuation = next((method for method in valuation.methods if method.name == valuation.selected_method and method.status == "complete" and method.supported), None)
        computed_base = valuation.scenarios.get("base")
        if not identity_conflict and valuation.status == "complete" and selected_valuation and computed_base and valuation.currency and valuation.as_of:
            numeric_scenarios = [D(value) for value in valuation.scenarios.values()]
            numeric_scenarios = [value for value in numeric_scenarios if value is not None and value > 0]
            computed_target, computed_issue = _parse_future_target({
                "price": computed_base,
                "lower": str(min(numeric_scenarios)) if numeric_scenarios else None,
                "upper": str(max(numeric_scenarios)) if numeric_scenarios else None,
                "currency": valuation.currency,
                "as_of": valuation.as_of,
                "horizon": valuation.horizon or "12 months",
                "source_refs": selected_valuation.source_refs,
                "basis": f"Code-calculated {selected_valuation.name}: {selected_valuation.formula}. {selected_valuation.rationale}".strip(),
            }, source_map=source_map)
            if computed_target is not None and computed_issue is None:
                target = computed_target
                blockers = [blocker for blocker in blockers if blocker.key != "future_target_support"]
        decision_horizon = str(row.get("horizon") or payload.get("horizon") or "") or None
        action_plan = _action_plan_block(item=row, payload=payload, brief=brief, entry=entry, target=target, invalidation=invalidation, stop_price=row.get("stop_price") or brief.get("stop_price") or payload.get("stop_price"), catalysts=catalysts)
        opportunity_cost = build_opportunity_cost(
            snapshot,
            principal=sizing.recommended_notional,
            account_id=sizing.account_id,
            currency=sizing.currency or entry_currency,
            direction=direction,
            horizon=decision_horizon,
            as_of=case_as_of,
            benchmark_ticker=action_plan.benchmark_ticker,
            benchmark_rationale=action_plan.benchmark_rationale,
            source_refs=entry.source_refs if entry else [],
        )
        payoff_raw = build_payoff(
            valuation_raw,
            sizing.risk_entry_price or (entry.lower if entry and direction == "short" else _entry_price(entry, direction)),
            direction=direction,
            horizon=decision_horizon,
            stop_price=row.get("stop_price") or brief.get("stop_price") or payload.get("stop_price"),
            shares=sizing.recommended_shares if sizing.recommended_shares is not None else sizing.shares,
            opportunity_cost=opportunity_cost,
            source_refs=entry.source_refs if entry else [],
        )
        try:
            payoff = PayoffBlock.model_validate(payoff_raw)
        except (ValidationError, ValueError, TypeError):
            payoff = PayoffBlock(status="unavailable", missing_inputs=["payoff_projection"])
        portfolio_sizing = sizing.model_dump(mode="python", exclude_none=False)
        # Portfolio impact and limit checks describe the proposed allocation
        # when one is supplied. Capacity remains visible on ``sizing`` but is
        # never silently treated as the order quantity.
        if sizing.recommended_notional is not None:
            portfolio_sizing["notional"] = sizing.recommended_notional
        if sizing.recommended_planned_loss is not None:
            portfolio_sizing["planned_loss"] = sizing.recommended_planned_loss
        portfolio_raw = build_portfolio_context(
            snapshot,
            candidate={
                "ticker": ticker,
                "instrument": row.get("instrument") or row.get("name") or ticker,
                "issuer": row.get("issuer") or row.get("issuer_name"),
                "sector": row.get("sector"),
                "direction": direction,
                "currency": entry_currency,
                "borrow_available": sizing.borrow_available,
                "planned_loss": sizing.planned_loss,
            },
            sizing=portfolio_sizing,
            as_of=case_as_of,
        )
        try:
            portfolio_context = PortfolioContext.model_validate(portfolio_raw)
        except (ValidationError, ValueError, TypeError):
            portfolio_context = PortfolioContext(status="unavailable", missing_inputs=["portfolio_projection"])
        gate = _recommendation_gate(item=row, payload=payload, brief=brief, v2=v2, instrument_identity=instrument_identity, direction=direction, entry=entry, target=target, valuation=valuation, payoff=payoff, portfolio_context=portfolio_context, thesis=thesis, action_plan=action_plan, sizing=sizing, source_map=source_map, validated_facts=validated_facts, as_of=case_as_of, research_contract=research_contract, key_questions=row.get("key_questions"), joint_review=row.get("joint_review"), advisory_mode=advisory_mode)
        if gate.status == "blocked":
            for check in gate.checks:
                if check.status in {"fail", "unavailable"}:
                    blockers.append(_recommendation_blocker(check))
        blockers = _dedupe_blockers(blockers)
        outcome = _decision_outcome(row, payload, sizing=sizing, entry=entry, target=target, triggers=triggers, direction=direction)
        if outcome == "recommend" and gate.status != "pass":
            outcome = "watchlist" if triggers else None
        validation_blocked = identity_conflict or payload.get("status") not in {None, "completed"} or any(blocker.kind in {"evidence", "unknown"} for blocker in blockers)
        if gate.status != "pass":
            validation_blocked = True
        if payload.get("status") not in {None, "completed"}:
            blockers.append(_blocker("output_validation", "evidence", "The CIO output has unresolved validation or research requirements.", owner="CIO"))
        if len(rows) == 1 and str(payload.get("decision_disposition") or "").lower() in {"defer", "reject"} and outcome == "recommend":
            validation_blocked = True
            blockers.append(_blocker("cio_disposition", "evidence", "The CIO has not approved this investment outcome.", owner="CIO"))
        if validation_blocked and outcome == "recommend":
            outcome = "watchlist" if triggers else None
        if position_limit_blocked and outcome == "recommend":
            outcome = "watchlist" if triggers else None
        explicit_decline = str(row.get("stance") or payload.get("stance") or payload.get("decision_disposition") or "").strip().lower() in {"avoid", "reject", "decline"}
        if explicit_decline:
            # A reasoned decline remains a completed decision even when no
            # entry price exists.  Operational sizing errors do not overwrite
            # it with a connector failure.
            sizing_state = "ready"
        else:
            sizing_state = sizing.execution_state
        rationale = str(row.get("rationale") or row.get("entry_advice") or row.get("entry_plan") or payload.get("summary") or payload.get("proposed_action") or "").strip()
        if not rationale:
            rationale = "Research did not provide a supported investment rationale yet."
        if outcome is None and sizing_state == "awaiting_input":
            rationale = f"Research is awaiting inputs before an investment outcome can be published. {rationale}"
        if outcome == "watchlist" and not triggers:
            outcome = None
        execution_state = sizing_state
        if validation_blocked and not explicit_decline and execution_state == "ready":
            execution_state = "awaiting_input"
        if any(item.kind == "execution" for item in blockers):
            execution_state = "failed"
        technical = _dict(market_packet.get("technical") or market_packet.get("technical_indicators"))
        if technical and "frequencies" not in technical:
            # The lean workflow currently supplies a validated daily
            # ``TechnicalSnapshot``.  Keep its provenance and expose the
            # missing weekly frame explicitly rather than presenting daily
            # values as multi-frequency evidence.
            technical = {
                "code_version": "technical-indicators.v1",
                "method": "connector_snapshot",
                "frequencies": {
                    "daily": technical,
                    "weekly": {"frequency": "weekly", "observations": 0, "indicators": {}, "missing_inputs": ["weekly_bars"], "source_refs": _unique(market_packet.get("source_refs") if isinstance(market_packet.get("source_refs"), list) else [])},
                },
                "daily": technical,
                "weekly": {"frequency": "weekly", "observations": 0, "indicators": {}, "missing_inputs": ["weekly_bars"], "source_refs": _unique(market_packet.get("source_refs") if isinstance(market_packet.get("source_refs"), list) else [])},
            }
        market_data = _dict(snapshot).get("market_data") if isinstance(snapshot, Mapping) else None
        if not technical and isinstance(market_data, Mapping):
            technical = calculate_technical_indicators(market_data.get("daily_bars") or market_data.get("bars") or [], weekly_bars=market_data.get("weekly_bars"), source_refs=market_data.get("source_refs") or [])
        if identity_conflict:
            technical = {}
        # Validation and disposition checks can add service blockers after the
        # earlier evidence pass.  Apply the schema limit to the final list.
        blockers = _dedupe_blockers(blockers)
        try:
            candidate_refs = _unique([*(entry.source_refs if entry else []), *(target.source_refs if target else []), *(_unique(market_packet.get("source_refs") if isinstance(market_packet.get("source_refs"), list) else [])), *(instrument_identity.get("source_refs") or []), *(ref for item in shared_evidence for ref in item.source_refs)])
            decision = CanonicalCandidateDecision(schema_version=DECISION_SCHEMA_VERSION, decision_revision=revision, ticker=ticker, instrument=str(row.get("instrument") or row.get("name") or ticker), issuer=row.get("issuer") or row.get("issuer_name"), sector=row.get("sector"), asset_class=row.get("asset_class") or market_packet.get("asset_class") or payload.get("asset_class"), direction=direction, strategy=str(candidate_strategy or "") or None, outcome=outcome, execution_state=execution_state, rationale=rationale, as_of=case_as_of, horizon=str(row.get("horizon") or payload.get("horizon") or "") or None, entry=entry, future_target=target, sizing=sizing, risks=risks, catalysts=catalysts, invalidation=invalidation, source_refs=candidate_refs, evidence=shared_evidence, material_blockers=blockers, watch_triggers=triggers, technical_indicators=technical, forecast=forecast, thesis=thesis, valuation=valuation, payoff=payoff, portfolio_context=portfolio_context, action_plan=action_plan, recommendation_gate=gate, lifecycle=_lifecycle_block(outcome, action_plan), key_questions=row.get("key_questions") or [], joint_review=row.get("joint_review") or {})
            decision.instrument_identity = instrument_identity
            decision.source_refs = candidate_refs
        except ValidationError as exc:
            # This is a projection failure, which may originate in the code's
            # transformation or a provider field. Report only field names and
            # error types, never the private input values in Pydantic messages.
            invalid_fields = [
                f"{'.'.join(str(part) for part in error['loc'])} ({error['type']})"
                for error in exc.errors(include_input=False, include_url=False)[:5]
            ]
            failure_reason = "Canonical decision projection failed validation: " + "; ".join(invalid_fields) + "."
            fallback_sizing = DecisionSizing(execution_state="failed", direction=direction, currency=entry_currency, missing_inputs=[], reason=failure_reason, policy_status="unconfigured")
            decision = CanonicalCandidateDecision(schema_version=DECISION_SCHEMA_VERSION, decision_revision=revision, ticker=ticker, instrument=ticker, direction=direction, outcome=None, execution_state="failed", rationale=failure_reason, as_of=case_as_of, sizing=fallback_sizing, evidence=shared_evidence, material_blockers=_dedupe_blockers([_blocker("canonical_validation", "execution", failure_reason, owner="Code service"), *blockers]))
        decisions.append(decision)
    if allocation_mode == "alternatives":
        # These quantities describe mutually exclusive possible positions.
        # Each must fit the account and policy on its own; they do not reserve
        # cash or holding slots against another comparison candidate.
        for candidate in decisions:
            _enforce_case_resources([candidate], snapshot)
    else:
        _enforce_case_resources(decisions, snapshot)
    if not decisions:
        return CanonicalCaseDecision(schema_version=DECISION_SCHEMA_VERSION, run_id=run_id, research_contract=research_contract, allocation_mode=allocation_mode, decision_revision=revision, as_of=case_as_of, outcome=None, execution_state="awaiting_input", candidates=[], material_blockers=[_blocker("candidate", "evidence", "No candidate instrument was identified in the research output.", owner="Researcher")])
    outcomes = {item.outcome for item in decisions if item.outcome is not None}
    # A case with one reasoned outcome plus an operationally incomplete
    # candidate is mixed: the incomplete candidate must not disappear behind
    # the other candidate's conclusion.
    has_unresolved_outcome = any(item.outcome is None for item in decisions)
    case_outcome = None if not outcomes else "mixed" if has_unresolved_outcome or len(outcomes) > 1 else next(iter(outcomes), None)
    if any(item.execution_state == "failed" for item in decisions):
        state = "failed"
    elif any(item.execution_state == "awaiting_input" for item in decisions):
        state = "awaiting_input"
    else:
        state = "ready"
    aggregate = _dedupe_blockers([blocker for item in decisions for blocker in item.material_blockers])
    return CanonicalCaseDecision(schema_version=DECISION_SCHEMA_VERSION, run_id=run_id, research_contract=research_contract, allocation_mode=allocation_mode, decision_revision=revision, as_of=case_as_of, outcome=case_outcome, execution_state=state, candidates=decisions, material_blockers=aggregate)


def build_candidate_decisions(*args: Any, **kwargs: Any) -> list[CanonicalCandidateDecision]:
    """Return the candidate list for callers that do not need the case envelope."""
    return build_case_decision(*args, **kwargs).candidates


project_case_decision = build_case_decision


__all__ = ["DECISION_SCHEMA_VERSION", "build_case_decision", "build_candidate_decisions", "project_case_decision", "evaluate_instrument_identity"]
