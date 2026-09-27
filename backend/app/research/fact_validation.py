"""Conservative, source-local validation for structured fact claims.

The provider supplies a claim and a source locator.  This module checks that
the cited passage contains one *bound* observation for that claim.  A number
appearing somewhere in a passage is insufficient: issuer, metric, period and
unit must resolve to the same row/field.  The result deliberately separates a
matched citation from independent corroboration; a citation is provenance,
not a second source or a truth oracle.

The functions in this module are intentionally independent from the
repository and schemas.  They accept both the current ``FactClaim`` shape and
future additive claim context fields (``issuer``, ``metric``, ``table``,
``source_version`` and ``citation_context``) without trusting provider prose.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from .freshness import evaluate_fact_freshness


_NUMERIC_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_.])[-+]?(?:(?:\d{1,3}(?:,\d{3})+)|\d+)(?:\.\d+)?%?(?![A-Za-z0-9_]|\.[0-9])"
)
# Numeric tokens that are part of an observation date are not candidate
# values.  The old prose matcher searched for a matching number anywhere in a
# sentence, which allowed the day/month in ``2026-06-30`` to satisfy a claim
# for 30.  Keep this separate from ``_NUMERIC_TOKEN`` because the latter is
# also used for the compatibility ``text_match`` projection.
_NUMERIC_DATE_TOKEN = re.compile(r"(?<!\d)(?:19|20)\d{2}[-/]\d{1,2}(?:[-/]\d{1,2})?(?!\d)")
_YEAR_TOKEN = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
_MONTH_DATE_TOKEN = re.compile(
    r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December|"
    r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}(?:,\s*|\s+)(?:19|20)\d{2}\b",
    re.IGNORECASE,
)
_LINE_LOCATOR = re.compile(r"^[Ll](\d+)(?:\s*-\s*[Ll]?(\d+))?$")
_SCALE_WORDS = {"thousand", "thousands", "million", "millions", "billion", "billions", "trillion", "trillions", "k", "m", "bn"}
_SCALE_ALIASES = {
    "thousand": "thousand", "thousands": "thousand", "k": "thousand",
    "million": "million", "millions": "million", "m": "million",
    "billion": "billion", "billions": "billion", "bn": "billion",
    "trillion": "trillion", "trillions": "trillion",
}
_CURRENCY_CODES = {"usd", "cad", "eur", "gbp", "jpy", "aud", "chf", "cny"}
_COMMON_CAPS = {
    "A", "AI", "ALL", "AND", "AS", "ATH", "BY", "CEO", "CFO", "DD", "EBITDA",
    "EPS", "ETF", "EV", "FASB", "FOR", "FY", "GDP", "GAAP", "IFRS", "IMO", "IPO", "IS",
    "IT", "LTM", "NAV", "NASDAQ", "NYSE", "OF", "ON", "OR", "P", "PE", "P/E", "SEC",
    "TA", "THE", "THIS", "TLDR", "TTM", "USD", "USA", "YOLO",
}

# The vocabulary is deliberately small.  It is used to reject a row whose
# metric is clearly different, rather than to infer an arbitrary metric from
# similar prose.  Unknown metrics remain unsupported when a cited passage is
# ambiguous.
_METRIC_ALIASES: dict[str, frozenset[str]] = {
    "revenue": frozenset({"revenue", "revenues", "sales", "turnover", "net sales"}),
    "expense": frozenset({"expense", "expenses", "cost", "costs", "operating expense", "operating expenses"}),
    "assets": frozenset({"asset", "assets", "total assets"}),
    "liabilities": frozenset({"liability", "liabilities", "total liabilities"}),
    "cash": frozenset({"cash", "cash and equivalents", "cash equivalents"}),
    "debt": frozenset({"debt", "borrowings", "total debt"}),
    "shares": frozenset({"share", "shares", "shares outstanding", "outstanding shares", "weighted average shares"}),
    "net_income": frozenset({"net income", "net earnings", "profit", "loss", "earnings"}),
    "eps": frozenset({"eps", "earnings per share", "diluted eps", "basic eps"}),
    "ebitda": frozenset({"ebitda", "adjusted ebitda"}),
    "operating_income": frozenset({"operating income", "operating profit"}),
    "depreciation_and_amortization": frozenset({"depreciation and amortization", "depreciation depletion and amortization"}),
    "book_equity": frozenset({"book equity", "book value", "stockholders equity", "shareholders equity"}),
    "tangible_book_equity": frozenset({"tangible book equity", "tangible book value"}),
    "nav": frozenset({"net asset value", "nav equity"}),
    "preferred": frozenset({"preferred", "preferred stock", "preferred claims"}),
    "minority": frozenset({"minority", "minority interest", "noncontrolling interest"}),
    "market_capitalization": frozenset({"market capitalization", "market cap"}),
    "enterprise_value": frozenset({"enterprise value", "ev"}),
    "price": frozenset({"price", "quote", "close", "closing price", "open", "opening price", "high", "highest price", "low", "lowest price", "last trade", "market price", "nav"}),
    "volume": frozenset({"volume", "trade volume", "shares traded"}),
    "dividend": frozenset({"dividend", "dividends", "distribution"}),
    "headcount": frozenset({"headcount", "employees", "employee count", "employees count"}),
}
_METRIC_BY_ALIAS = {alias: key for key, aliases in _METRIC_ALIASES.items() for alias in aliases}
_METRIC_WORDS = frozenset(alias for aliases in _METRIC_ALIASES.values() for alias in aliases)


@dataclass(frozen=True)
class _ClaimContext:
    text: str
    value: Any
    unit: str | None
    period: str | None
    source_ref: str
    locator: str
    subject: str | None = None
    issuer: str | None = None
    metric: str | None = None
    scale: str | None = None
    currency: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    basis: str | None = None
    statement_type: str | None = None
    source_quote: str | None = None
    freshness: str | None = None
    table: str | None = None
    field: str | None = None
    source_version: Any = None
    source_hash: str | None = None


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if value is None:
        return {}
    result: dict[str, Any] = {}
    for key in (
        "claim", "value", "unit", "period", "source_ref", "locator", "issuer", "issuer_name",
        "ticker", "symbol", "instrument", "security", "subject", "metric", "predicate", "measure",
        "fact_type", "scale", "currency", "currency_code", "period_start", "period_end", "basis",
        "share_basis", "statement_type", "source_quote", "table", "table_name", "statement", "section",
        "field", "column", "source_field", "source_version", "version", "source_hash", "content_hash",
        "freshness", "citation_context", "citation", "extraction_context", "context",
    ):
        if hasattr(value, key):
            result[key] = getattr(value, key)
    return result


def _first(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value is not None and str(value).strip() != "":
            return value
    return None


def _context_from_claim(claim: Any) -> _ClaimContext:
    raw = _mapping(claim)
    nested: dict[str, Any] = {}
    for key in ("citation_context", "citation", "extraction_context", "context"):
        candidate = raw.get(key)
        if isinstance(candidate, Mapping):
            nested.update(candidate)
    merged = dict(nested)
    merged.update({key: value for key, value in raw.items() if value is not None})
    subject = _first(merged, "subject", "issuer", "issuer_name", "ticker", "symbol", "instrument", "security")
    issuer = _first(merged, "issuer", "issuer_name", "ticker", "symbol", "instrument", "security", "subject")
    metric = _first(merged, "metric", "predicate", "measure", "fact_type")
    table = _first(merged, "table", "table_name", "statement", "section")
    field = _first(merged, "field", "column", "source_field")
    return _ClaimContext(
        text=str(merged.get("claim") or "").strip(),
        value=merged.get("value"),
        unit=str(merged.get("unit") or "").strip() or None,
        period=str(merged.get("period") or "").strip() or None,
        source_ref=str(merged.get("source_ref") or "").strip(),
        locator=str(merged.get("locator") or "").strip(),
        subject=str(subject).strip() if subject is not None else None,
        issuer=str(issuer).strip() if issuer is not None else None,
        metric=str(metric).strip() if metric is not None else None,
        scale=str(_first(merged, "scale") or "").strip() or None,
        currency=str(_first(merged, "currency", "currency_code") or "").strip() or None,
        period_start=str(_first(merged, "period_start") or "").strip() or None,
        period_end=str(_first(merged, "period_end") or "").strip() or None,
        basis=str(_first(merged, "basis", "share_basis") or "").strip() or None,
        statement_type=str(_first(merged, "statement_type") or "").strip() or None,
        source_quote=str(_first(merged, "source_quote") or "").strip() or None,
        freshness=str(_first(merged, "freshness") or "").strip() or None,
        table=str(table).strip() if table is not None else None,
        field=str(field).strip() if field is not None else None,
        source_version=_first(merged, "source_version", "version"),
        source_hash=str(_first(merged, "source_hash", "content_hash") or "").strip() or None,
    )


def _normal_space(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _fold(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()


def normalize_unit(unit: str | None) -> str | None:
    """Normalize a unit without accepting unsupported scale qualifiers."""
    if unit is None:
        return None
    value = re.sub(r"\s+", " ", str(unit).strip().casefold())
    if not value or any(word in value.split() for word in _SCALE_WORDS):
        return None
    if not re.fullmatch(r"[a-z0-9$%/._ -]{1,50}", value):
        return None
    return value


def normalize_scale(scale: str | None) -> str | None:
    """Return a supported source scale, without inventing conversions."""
    if scale is None:
        return None
    raw = str(scale).strip().casefold()
    exponent_match = re.fullmatch(r"10\s*(?:\^|x|\*)\s*(3|4|5|6|7|8|9|10|11|12|13|14|15)", raw)
    if exponent_match:
        exponent = int(exponent_match.group(1))
        return {3: "thousand", 6: "million", 9: "billion", 12: "trillion"}.get(exponent)
    folded = _fold(raw)
    if not folded:
        return None
    # Keep exponent notation explicit; no arithmetic conversion is performed
    # by this validator.  A caller must still supply the same scale on both
    # the claim and source row.
    tokens = folded.split()
    if len(tokens) != 1:
        return None
    return _SCALE_ALIASES.get(tokens[0])


def unit_dimension(unit: str | None) -> tuple[str, str | None] | None:
    key = normalize_unit(unit)
    if key is None:
        return None
    currencies = [code for code in _CURRENCY_CODES if re.search(rf"(?<![a-z]){re.escape(code)}(?![a-z])", key)]
    if "share" in key or "stock" in key:
        if currencies:
            return "currency_per_share", currencies[0]
        return "shares", None
    if currencies:
        return "currency", currencies[0]
    if key in {"%", "percent", "fraction", "ratio"}:
        return "fraction", None
    if key in {"count", "units", "unit"}:
        return "count", None
    return "other", None


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    cleaned = str(value).strip().replace(",", "")
    if cleaned.endswith("%"):
        cleaned = cleaned[:-1]
    try:
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None


def _token_decimal(token: str) -> Decimal | None:
    return _decimal(token)


def _numeric_match(value: Decimal, text: str) -> bool:
    return any(_token_decimal(token.group(0)) == value for token in _NUMERIC_TOKEN.finditer(text))


def _date_spans(text: str) -> tuple[tuple[int, int], ...]:
    """Return complete date spans whose component numbers are not facts."""
    spans: list[tuple[int, int]] = []
    for pattern in (_NUMERIC_DATE_TOKEN, _MONTH_DATE_TOKEN):
        spans.extend((match.start(), match.end()) for match in pattern.finditer(text))
    return tuple(sorted(spans))


def _period_token_spans(text: str) -> tuple[tuple[int, int, str], ...]:
    """Return one token for each visible period in a prose segment."""
    date_spans = _date_spans(text)
    periods: list[tuple[int, int, str]] = []
    for start, end in date_spans:
        periods.append((start, end, text[start:end]))
    for match in _YEAR_TOKEN.finditer(text):
        if any(start <= match.start() < end for start, end in date_spans):
            continue
        periods.append((match.start(), match.end(), match.group(0)))
    return tuple(sorted(periods))


def _period_token_values(text: str) -> tuple[str, ...]:
    return tuple(item[2] for item in _period_token_spans(text))


def _numeric_fact_matches(text: str) -> tuple[re.Match[str], ...]:
    """Find numeric facts while excluding every component of a date token."""
    date_spans = _date_spans(text)
    return tuple(
        match
        for match in _NUMERIC_TOKEN.finditer(text)
        if not any(start <= match.start() < end for start, end in date_spans)
    )


def _locator_passage(content: str | None, locator: str) -> tuple[str | None, int | None, int | None]:
    match = _LINE_LOCATOR.fullmatch(locator.strip())
    if not match:
        return None, None, None
    lines = str(content or "").splitlines() or ([str(content)] if content else [])
    start, end = int(match.group(1)), int(match.group(2) or match.group(1))
    if start < 1 or end < start or end > len(lines):
        return None, None, None
    return "\n".join(lines[start - 1 : end]), start, end


def _period_matches(period: str | None, text: str) -> bool:
    value = str(period or "").strip()
    if not value:
        return False
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        # ``period`` is provider input, so it may be a syntactically shaped
        # but impossible date (for example 2025-99-31).  Do not let that
        # reach the month-name lookup or accidentally match a partial row.
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            return False
        year, month, day = parsed.strftime("%Y"), parsed.strftime("%m"), parsed.strftime("%d")
        variants = {value, f"{year}/{month}/{day}"}
        # Month names are parsed without locale dependence for the common
        # English forms used by issuer filings.
        months = (
            "January", "February", "March", "April", "May", "June", "July", "August",
            "September", "October", "November", "December",
        )
        month_name = months[int(month) - 1]
        variants.update({f"{month_name} {int(day)}, {year}", f"{month_name[:3]} {int(day)}, {year}"})
        return any(re.search(rf"(?<!\d){re.escape(item)}(?!\d)", text, re.IGNORECASE) for item in variants)
    return bool(re.search(rf"(?<![A-Za-z0-9]){re.escape(value)}(?![A-Za-z0-9])", text, re.IGNORECASE))


def _period_requirements(context: _ClaimContext) -> tuple[str, ...]:
    values = [value for value in (context.period, context.period_start, context.period_end) if value]
    return tuple(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


def _periods_match(context: _ClaimContext, text: str, structured: Mapping[str, Any] | None = None) -> bool:
    requirements = _period_requirements(context)
    if not requirements:
        return False
    values: list[str] = []
    if structured:
        for key in ("period", "date", "as_of", "asof", "timestamp", "year", "fiscal_year", "reporting_period", "period_start", "period_end", "start_date", "end_date"):
            if structured.get(key) is not None:
                values.append(str(structured[key]))
    values.append(text)
    # A row can express a start/end range in separate columns.  Every
    # explicitly requested boundary must still be visible in that same row.
    return all(any(_period_matches(required, candidate) for candidate in values) for required in requirements)


def _metric_key(value: Any) -> str | None:
    text = _fold(value)
    if not text:
        return None
    # A unit such as ``USD/share`` appears after the value in common prose
    # and table rows.  Prefer metric labels before the first numeric token so
    # the unit's word "share" cannot relabel an "Issue price" observation.
    prefix_match = re.search(r"(?<![a-z])[-+]?\d", text)
    prefixes = [text[: prefix_match.start()]] if prefix_match else []
    prefixes.append(text)
    # Prefer the longest known phrase, so "net income" is not reduced to
    # "income" and "operating expenses" remains an expense row.
    for candidate in prefixes:
        for alias in sorted(_METRIC_BY_ALIAS, key=len, reverse=True):
            if re.search(rf"(?<![a-z0-9]){re.escape(_fold(alias))}(?![a-z0-9])", candidate):
                return _METRIC_BY_ALIAS[alias]
    return None


def _metric_occurrences(value: Any) -> list[tuple[int, int, str]]:
    """Return canonical metric occurrences in source order.

    ``_metric_key`` intentionally returns one best label for compatibility.
    Binding needs the full set so a sentence containing both ``revenue`` and
    ``expenses`` cannot let the first label authorize the second number.
    """
    text = str(value or "")
    occurrences: list[tuple[int, int, str]] = []
    folded_text = text.casefold()
    for alias in sorted(_METRIC_BY_ALIAS, key=len, reverse=True):
        folded_alias = _fold(alias)
        if not folded_alias:
            continue
        pattern = re.compile(rf"(?<![a-z0-9]){re.escape(folded_alias)}(?![a-z0-9])")
        for match in pattern.finditer(folded_text):
            metric = _METRIC_BY_ALIAS[alias]
            # ``share`` in ``per share`` or ``USD/share`` is a unit, not a
            # second metric competing with a price claim.
            prefix = text[: match.start()]
            if metric == "shares" and re.search(r"(?:per\s*|/\s*)$", prefix, re.IGNORECASE):
                continue
            occurrences.append((match.start(), match.end(), metric))
    # Long aliases overlap their component aliases (for example ``net income``
    # and ``income`` when the vocabulary grows).  Keep the longest occurrence
    # at each position and then restore source order.
    selected: list[tuple[int, int, str]] = []
    for occurrence in sorted(occurrences, key=lambda item: (item[0], -(item[1] - item[0]))):
        if any(start <= occurrence[0] < end or occurrence[0] <= start < occurrence[1] for start, end, _ in selected):
            continue
        selected.append(occurrence)
    return sorted(selected, key=lambda item: item[0])


def _metric_for_value(text: str, value_match: re.Match[str]) -> str | None:
    """Bind a numeric token to the closest preceding metric label."""
    occurrences = [item for item in _metric_occurrences(text) if item[1] <= value_match.start()]
    return occurrences[-1][2] if occurrences else None


def _issuer_mentions(value: Any) -> tuple[str, ...]:
    """Return likely issuer/ticker tokens in a passage.

    This helper is intentionally narrower than issuer discovery: it is only
    used to reject a cited sentence that names two possible issuers.  Common
    financial abbreviations and metric labels are excluded.
    """
    text = str(value or "")
    found: list[str] = []
    for match in re.finditer(r"(?<![A-Za-z0-9])([A-Z][A-Z0-9._-]{1,14})(?![A-Za-z0-9])", text):
        token = match.group(1)
        if token in _COMMON_CAPS or token.casefold() in _CURRENCY_CODES:
            continue
        if _metric_key(token) or token.casefold() in {"open", "high", "low", "close", "price", "revenue", "shares"}:
            continue
        folded = _fold(token)
        if folded and folded not in found:
            found.append(folded)
    return tuple(found)


def _periods_are_unambiguous(context: _ClaimContext, text: str, value_match: re.Match[str]) -> bool:
    """Ensure one cited row contains one period and a distinct fact value."""
    period_values = _period_token_values(text)
    requirements = _period_requirements(context)
    if not requirements:
        return False
    if len(period_values) > 1:
        return False
    # A value token cannot double as the only period token.  Requiring a
    # distinct period prevents a date/year coincidence from validating a
    # numeric fact whose value appears nowhere in the row.
    if any(start <= value_match.start() < end for start, end in _date_spans(text)):
        return False
    if period_values:
        return all(any(_period_matches(required, candidate) for candidate in period_values) for required in requirements)
    return all(_period_matches(required, text) for required in requirements)


def _claim_metric(context: _ClaimContext) -> str | None:
    return _metric_key(context.metric) or _metric_key(context.field) or _metric_key(context.text)


def _metric_is_explicit(context: _ClaimContext) -> bool:
    return bool(context.metric or context.field)


_BASIS_ALIASES: dict[str, tuple[str, ...]] = {
    "weighted_average": ("weighted average", "weighted-average", "weightedaverage"),
    "outstanding": ("outstanding", "period end", "period-end", "periodend"),
    "authorized": ("authorized", "authorised"),
    "public_float": ("public float", "public-float", "publicfloat"),
    "diluted": ("diluted",),
    "basic": ("basic",),
    "gaap": ("gaap",),
    "adjusted": ("adjusted", "non-gaap", "non gaap", "non_gaap"),
}


def _basis_keys(value: Any) -> set[str]:
    text = _fold(value)
    if not text:
        return set()
    result = {
        key
        for key, aliases in _BASIS_ALIASES.items()
        if any(re.search(rf"(?<![a-z0-9]){re.escape(_fold(alias))}(?![a-z0-9])", text) for alias in aliases)
    }
    if "adjusted" in result and re.search(r"\bnon[-_ ]gaap\b", str(value or ""), re.IGNORECASE):
        result.discard("gaap")
    return result or {text}


def _known_basis_keys(value: Any) -> set[str]:
    text = _fold(value)
    if not text:
        return set()
    result = {
        key
        for key, aliases in _BASIS_ALIASES.items()
        if any(re.search(rf"(?<![a-z0-9]){re.escape(_fold(alias))}(?![a-z0-9])", text) for alias in aliases)
    }
    if "adjusted" in result and re.search(r"\bnon[-_ ]gaap\b", str(value or ""), re.IGNORECASE):
        result.discard("gaap")
    return result


def _claim_basis(context: _ClaimContext) -> set[str]:
    return _basis_keys(context.basis) if context.basis else _known_basis_keys(context.text)


def _basis_matches(expected: set[str], text: str, structured: Mapping[str, Any] | None = None) -> bool:
    if not expected:
        return True
    parts = [text]
    if structured:
        for key in ("basis", "share_basis", "accounting_basis", "earnings_basis", "measurement_basis"):
            if structured.get(key) is not None:
                parts.append(str(structured[key]))
    observed_text = " ".join(parts)
    observed = _known_basis_keys(observed_text)
    # Unknown expected basis labels are accepted only when the same literal
    # phrase appears; known labels must be present in the same row/field.
    unknown = {key for key in expected if key not in _BASIS_ALIASES}
    if unknown and not all(key in _fold(observed_text) for key in unknown):
        return False
    known_expected = {key for key in expected if key in _BASIS_ALIASES}
    # GAAP and adjusted/non-GAAP are mutually exclusive qualifiers for one
    # fact.  A word-boundary match for ``gaap`` inside ``non-gaap`` used to
    # make an adjusted row satisfy a GAAP claim.
    standalone_gaap = bool(re.search(r"(?<!non-)(?<!non_)(?<!non )\bgaap\b", observed_text, re.IGNORECASE))
    if "gaap" in known_expected and "adjusted" in observed:
        return False
    if "adjusted" in known_expected and "gaap" in observed:
        return False
    if "adjusted" in known_expected and standalone_gaap and "adjusted" in observed:
        return False
    if len(known_expected) == 1 and known_expected & observed:
        opposing = {"gaap", "adjusted"} - known_expected
        if opposing & observed:
            return False
    return known_expected.issubset(observed)


def _statement_matches(expected: str | None, text: str, structured: Mapping[str, Any] | None = None) -> bool:
    if not expected:
        return True
    expected_fold = _fold(expected)
    parts = [text]
    if structured:
        for key in ("statement_type", "statement", "table", "table_name", "section"):
            if structured.get(key) is not None:
                parts.append(str(structured[key]))
    observed = _fold(" ".join(parts))
    return bool(expected_fold and re.search(rf"(?<![a-z0-9]){re.escape(expected_fold)}(?![a-z0-9])", observed))


def _requested_price_field(context: _ClaimContext) -> str | None:
    """Return the one OHLC field named by a price claim, if any."""
    text = f"{context.text} {context.field or ''}".casefold()
    fields = {
        "open": r"\b(?:open|opening price)\b",
        "high": r"\b(?:high|highest price)\b",
        "low": r"\b(?:low|lowest price)\b",
        "close": r"\b(?:close|closing price)\b",
    }
    named = [field for field, pattern in fields.items() if re.search(pattern, text)]
    return named[0] if len(named) == 1 else ("close" if not named else "")


def _explicit_issuer(context: _ClaimContext) -> str | None:
    if context.issuer:
        return _normal_space(context.issuer)
    text = context.text
    dollar = re.search(r"(?<![A-Za-z0-9])\$([A-Za-z][A-Za-z0-9.-]{1,14})(?![A-Za-z0-9])", text)
    if dollar:
        return dollar.group(1)
    # Bare all-caps identifiers are accepted only when they look like a
    # ticker/short issuer token and are not financial vocabulary.
    candidates = []
    for match in re.finditer(r"(?<![A-Za-z0-9])([A-Z][A-Z0-9._-]{1,14})(?![A-Za-z0-9])", text):
        value = match.group(1)
        if value in _COMMON_CAPS or value.casefold() in _CURRENCY_CODES or value.isdigit():
            continue
        if _metric_key(value) or value.casefold() in {"open", "high", "low", "close", "price", "revenue", "shares"}:
            continue
        candidates.append(value)
    return candidates[0] if len(candidates) == 1 else None


def _issuer_match(expected: str | None, text: str, structured: Mapping[str, Any] | None = None) -> bool:
    if not expected:
        return True
    expected_fold = _fold(expected)
    if structured:
        for key in ("issuer", "issuer_name", "company", "name", "ticker", "symbol", "instrument", "security"):
            value = structured.get(key)
            if value is not None and _fold(expected) == _fold(value):
                return True
    # Match complete words for tickers and issuer labels.  Comparing tokens
    # avoids ACME matching ACMECO while still handling "ACME Corporation".
    return bool(re.search(rf"(?<![A-Za-z0-9]){re.escape(expected_fold)}(?![A-Za-z0-9])", _fold(text)))


def _unit_from_text(text: str, structured: Mapping[str, Any] | None = None) -> tuple[str | None, str | None]:
    values: list[str] = []
    if structured:
        for key in ("unit", "units", "currency", "currency_code", "denomination", "measure_unit"):
            if structured.get(key) is not None:
                values.append(str(structured[key]))
    values.append(text)
    joined = " ".join(values)
    currencies = [code for code in _CURRENCY_CODES if re.search(rf"(?<![A-Za-z]){re.escape(code)}(?![A-Za-z])", joined, re.IGNORECASE)]
    if len(currencies) > 1:
        return None, ""
    per_share = bool(re.search(r"\b(?:per\s*share|/\s*share|pershare)\b", joined, re.IGNORECASE))
    shares = bool(re.search(r"\b(?:shares?|stock)\b", joined, re.IGNORECASE))
    if currencies:
        currency = currencies[0]
        return (f"{currency}/share" if per_share else currency), currency
    if shares:
        return "shares", None
    if re.search(r"(?:^|[^A-Za-z])%|\bpercent(?:age)?\b", joined, re.IGNORECASE):
        return "%", None
    if structured:
        for key in ("unit", "units", "denomination", "measure_unit"):
            if structured.get(key) is not None:
                return normalize_unit(str(structured[key])), None
    return None, None


def _scale_from_text(text: str, structured: Mapping[str, Any] | None = None) -> str | None:
    values: list[str] = []
    if structured:
        for key in ("scale", "magnitude", "multiplier", "denomination", "unit"):
            if structured.get(key) is not None:
                values.append(str(structured[key]))
    values.append(text)
    folded = " ".join(values).casefold()
    found: set[str] = set()
    for alias, normalized in _SCALE_ALIASES.items():
        # ``m`` and ``k`` are only accepted as standalone tokens, so a word
        # such as "market" cannot imply a scale.
        if re.search(rf"(?<![a-z]){re.escape(alias)}(?![a-z])", folded):
            found.add(normalized)
    return next(iter(found)) if len(found) == 1 else ("" if len(found) > 1 else None)


def _currency_from_text(text: str, structured: Mapping[str, Any] | None = None) -> str | None:
    values: list[str] = []
    if structured:
        for key in ("currency", "currency_code", "unit", "units", "denomination"):
            if structured.get(key) is not None:
                values.append(str(structured[key]))
    values.append(text)
    found = [code for code in _CURRENCY_CODES if re.search(rf"(?<![a-z]){re.escape(code)}(?![a-z])", " ".join(values).casefold())]
    return found[0] if len(found) == 1 else ("" if len(found) > 1 else None)


def _unit_matches(
    claim_unit: str | None,
    observed_unit: str | None,
    observed_currency: str | None,
    text: str,
    *,
    expected_currency: str | None = None,
) -> bool:
    expected = normalize_unit(claim_unit)
    if expected is None:
        return False
    expected_dim = unit_dimension(expected)
    if expected_dim is None:
        return False
    expected_currency_key = str(expected_currency or expected_dim[1] or "").strip().casefold() or None
    if expected_currency_key and observed_currency and observed_currency.casefold() != expected_currency_key:
        return False
    if expected_currency_key and not observed_currency:
        return False
    if observed_unit:
        observed = normalize_unit(observed_unit)
        if observed is None:
            return False
        observed_dim = unit_dimension(observed)
        if observed_dim and expected_dim[0] != observed_dim[0]:
            return False
        if observed_dim and expected_dim[1] and observed_dim[1] and expected_dim[1] != observed_dim[1]:
            return False
        if expected_dim[0] == "currency_per_share" and observed_dim and observed_dim[0] == "currency_per_share":
            return expected_dim[1] == observed_dim[1]
        return True
    # If no explicit unit was extracted, permit only semantic units whose
    # row label supplies the dimension (shares/count).  Currency claims must
    # have a currency in the same row/field or structured source metadata.
    if expected_dim[0] in {"shares", "count"} and re.search(r"\b(?:share|shares|count|units?)\b", text, re.IGNORECASE):
        return True
    return False


def _table_name(lines: list[str], index: int) -> str | None:
    candidates: list[str] = []
    for line in lines[max(0, index - 4) : index + 1]:
        clean = _normal_space(line).strip(" :|\t")
        if not clean or len(clean) > 160:
            continue
        if re.search(r"\b(?:statement|table|schedule|summary|income|balance sheet|cash flow|operations)\b", clean, re.IGNORECASE):
            candidates.append(clean)
    return candidates[-1] if candidates else None


def _period_header(line: str) -> list[re.Match[str]]:
    return list(re.finditer(r"(?<!\d)((?:19|20)\d{2}(?:[-/]\d{1,2}(?:[-/]\d{1,2})?)?)(?!\d)", line))


def _columns_are_bound(header: str, row: str, headers: list[re.Match[str]], values: list[re.Match[str]]) -> bool:
    """Require positional evidence before using a multi-column table.

    An ordinal guess can silently swap two fiscal columns after PDF text
    extraction.  Fixed-width extraction gives us a stronger signal: the
    selected value token must occupy the same visible column as its date
    header.  Delimited rows are handled by the explicit row matcher instead;
    this fallback intentionally declines them when alignment is uncertain.
    """
    if len(headers) < 2 or len(headers) != len(values):
        return False
    if "|" in header or "|" in row or "\t" in header or "\t" in row:
        return False
    return all(abs(header_match.start() - value_match.start()) <= 4 for header_match, value_match in zip(headers, values))


def _json_objects(text: str) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or not (stripped.startswith("{") or stripped.startswith("[")):
            continue
        try:
            parsed = json.loads(stripped)
        except (TypeError, ValueError):
            continue
        if isinstance(parsed, dict):
            objects.append(parsed)
        elif isinstance(parsed, list):
            objects.extend(item for item in parsed if isinstance(item, dict))
    return objects


def _structured_candidate(
    context: _ClaimContext,
    value: Decimal,
    text: str,
    expected_issuer: str | None,
    expected_metric: str | None,
    source_currency: str | None = None,
) -> tuple[bool, str | None]:
    """Return a bound match from newline-delimited JSON rows, if present."""
    objects = _json_objects(text)
    if not objects:
        return False, None
    # A structured row without a typed metric is not safe for numeric use:
    # accepting its first matching number would recreate the swapped-field
    # bug that this module is intended to prevent.
    if not expected_metric:
        return False, None
    for row in objects:
        row_text = json.dumps(row, ensure_ascii=False, sort_keys=True)
        if not _issuer_match(expected_issuer, row_text, row):
            continue
        if not _periods_match(context, row_text, row):
            continue
        row_table = _first(row, "table", "table_name", "statement", "section")
        if context.table and not re.search(rf"\b{re.escape(_fold(context.table))}\b", _fold(row_table or row_text)):
            continue
        if not _statement_matches(context.statement_type, row_table or row_text, row):
            continue
        if not _basis_matches(_claim_basis(context), row_text, row):
            continue
        explicit_metric = _metric_key(_first(row, "metric", "field", "measure", "predicate", "label", "name", "type"))
        # For ordinary rows only compare the field carrying the number.  This
        # prevents revenue=42 from satisfying an expenses=42 claim in the same
        # object.  Metadata keys are ignored as numeric observations.
        matching_fields: list[str] = []
        requested_price_field = _requested_price_field(context) if expected_metric == "price" else None
        if expected_metric == "price" and requested_price_field == "":
            continue
        for key, raw in row.items():
            parsed = _decimal(raw)
            if parsed is not None and parsed == value:
                key_fold = _fold(key)
                if key_fold in {"period", "date", "as of", "asof", "timestamp", "year", "fiscal year", "reporting period", "source version", "version", "volume"}:
                    continue
                field_metric = _metric_key(key)
                if field_metric is None and explicit_metric and key_fold in {"value", "amount", "observation", "number", "result"}:
                    field_metric = explicit_metric
                if expected_metric and field_metric and field_metric != expected_metric:
                    continue
                if expected_metric and not field_metric:
                    continue
                if requested_price_field and key.casefold() not in {requested_price_field, requested_price_field[0]}:
                    continue
                matching_fields.append(str(key))
        if not matching_fields:
            continue
        observed_unit, observed_currency = _unit_from_text(row_text, row)
        if not observed_currency and source_currency:
            observed_currency = source_currency.casefold()
            observed_unit = observed_currency
        # Market-bar OHLC fields are prices per share even when the row only
        # carries the currency code.  Keep this explicit field binding here;
        # arbitrary JSON numeric fields never receive the same inference.
        if expected_metric == "price" and any(field.casefold() in {"open", "high", "low", "close", "o", "h", "l", "c"} for field in matching_fields) and observed_currency:
            observed_unit = f"{observed_currency}/share"
        if context.unit and not _unit_matches(context.unit, observed_unit, observed_currency, row_text, expected_currency=context.currency):
            continue
        expected_scale = normalize_scale(context.scale)
        if context.scale and expected_scale is None:
            continue
        observed_scale = _scale_from_text(row_text, row)
        if expected_scale != observed_scale:
            continue
        if context.currency and observed_currency != context.currency.casefold():
            continue
        if context.source_version is not None:
            # Version is validated by the caller's metadata below; a JSON
            # row's own version, when present, must agree as well.
            row_version = _first(row, "source_version", "version")
            if row_version is not None and str(row_version) != str(context.source_version):
                continue
        return True, row_text
    return False, None


def _plain_candidates(
    context: _ClaimContext,
    value: Decimal,
    text: str,
    expected_issuer: str | None,
    expected_metric: str | None,
    bound_quote: str | None = None,
) -> tuple[bool, str | None, str | None]:
    """Match a prose/table row conservatively.

    A candidate is evaluated line by line.  For a year-header table, the
    header is joined only to the row's corresponding numeric column; values
    from another year or another metric row cannot satisfy the claim.
    """
    lines = text.splitlines() or [text]
    bound_text = _normal_space(bound_quote) if bound_quote else None
    # A line or sentence with its own issuer/metric/date/unit is the strongest
    # representation and is preferred over context inferred from headings.
    for index, line in enumerate(lines):
        # Keep pipe-delimited rows intact: the delimiter commonly separates
        # issuer, period, metric, unit and value columns.  Splitting those
        # cells before binding would recreate the swapped-column bug this
        # validator is meant to prevent.
        segments = [item for item in re.split(r"[;]+|(?<=[.!?])\s+", line) if item.strip()]
        for segment in segments:
            if bound_text and _normal_space(segment) not in bound_text:
                continue
            numeric_matches = _numeric_fact_matches(segment)
            matching_values = [match for match in numeric_matches if _token_decimal(match.group(0)) == value]
            # A prose segment is safe only when it has one fact value.  The
            # remaining numeric token(s), when present, must be the one
            # requested period.  This rejects ``revenue 100 and expenses
            # 200 for 2026`` and prevents the date day from becoming a value.
            if len(matching_values) != 1:
                continue
            value_match = matching_values[0]
            other_numbers = [match for match in numeric_matches if match is not value_match]
            if any(not any(start <= match.start() < end for start, end in _date_spans(segment)) and not any(
                _period_matches(required, match.group(0)) for required in _period_requirements(context)
            ) for match in other_numbers):
                continue
            if not _periods_are_unambiguous(context, segment, value_match):
                continue
            if not _issuer_match(expected_issuer, segment):
                continue
            if expected_issuer:
                issuers = _issuer_mentions(segment)
                if len(issuers) > 1 or (_fold(expected_issuer) not in issuers and not _issuer_match(expected_issuer, segment)):
                    continue
            source_metric = _metric_for_value(segment, value_match)
            if expected_metric and source_metric != expected_metric:
                continue
            if not source_metric:
                continue
            associated_metrics = {
                _metric_for_value(segment, match)
                for match in numeric_matches
                if not any(_period_matches(required, match.group(0)) for required in _period_requirements(context))
            }
            associated_metrics.discard(None)
            if associated_metrics != {source_metric}:
                continue
            if not _basis_matches(_claim_basis(context), segment):
                continue
            table_context = _table_name(lines, index) or ""
            if not _statement_matches(context.statement_type, f"{table_context} {segment}"):
                continue
            observed_unit, observed_currency = _unit_from_text(segment)
            if context.unit and not _unit_matches(context.unit, observed_unit, observed_currency, segment, expected_currency=context.currency):
                continue
            if context.currency and observed_currency != context.currency.casefold():
                continue
            expected_scale = normalize_scale(context.scale)
            if context.scale and expected_scale is None:
                continue
            if expected_scale != _scale_from_text(segment):
                continue
            if context.table:
                if not table_context or not re.search(rf"\b{re.escape(_fold(context.table))}\b", _fold(table_context)):
                    continue
            return True, segment, source_metric

    # Header/row tables: map a value to a dated column only when fixed-width
    # positions prove that the extracted layout survived.  Ordinal mapping by
    # itself is unsafe after PDF/table extraction and is deliberately rejected.
    for row_index, row in enumerate(lines):
        numbers = list(_NUMERIC_TOKEN.finditer(row))
        if not numbers:
            continue
        row_metric = _metric_key(row)
        if expected_metric and row_metric != expected_metric:
            continue
        if not row_metric and expected_metric:
            continue
        header_index: int | None = None
        header_matches: list[re.Match[str]] = []
        for candidate_index in range(row_index - 1, max(-1, row_index - 5), -1):
            candidate_matches = _period_header(lines[candidate_index])
            if len(candidate_matches) >= 2:
                header_index = candidate_index
                header_matches = candidate_matches
                break
        if header_index is None or not _columns_are_bound(lines[header_index], row, header_matches, numbers):
            continue
        period_index = next((pos for pos, header in enumerate(header_matches) if any(_period_matches(required, header.group(1)) for required in _period_requirements(context))), None)
        if period_index is None or period_index >= len(numbers):
            continue
        candidate_number = numbers[period_index]
        if _token_decimal(candidate_number.group(0)) != value:
            continue
        header_line = lines[header_index]
        # A row-level issuer is stronger than a section heading.  Do not let
        # an ``ACME`` heading certify a value whose row explicitly names
        # ``OTHERCO``; neighbouring rows and headings are context only when
        # the selected row leaves the issuer unstated.
        if expected_issuer:
            row_issuers = _issuer_mentions(row)
            expected_issuer_key = _fold(expected_issuer)
            if row_issuers and expected_issuer_key not in row_issuers:
                continue
            if row_issuers and any(item != expected_issuer_key for item in row_issuers):
                continue
        # Likewise, an explicit share/accounting qualifier on the selected
        # row must satisfy the requested basis itself.  A heading such as
        # ``outstanding shares`` cannot be inherited by a
        # ``weighted-average diluted shares`` row.
        expected_basis = _claim_basis(context)
        row_basis = _known_basis_keys(row)
        if expected_basis and row_basis and not _basis_matches(expected_basis, row):
            continue
        row_context = " ".join([header_line, row, *lines[max(0, row_index - 3) : row_index]])
        if bound_text and _normal_space(row) not in bound_text:
            continue
        if not _issuer_match(expected_issuer, row_context):
            continue
        if not _periods_match(context, row_context):
            continue
        if not _basis_matches(_claim_basis(context), row_context):
            continue
        if not _statement_matches(context.statement_type, row_context):
            continue
        observed_unit, observed_currency = _unit_from_text(row)
        if not observed_currency:
            header_currency = _currency_from_text(header_line)
            if header_currency:
                observed_currency = header_currency
                observed_unit = f"{header_currency}/share" if re.search(r"(?:per\s*share|/\s*share)", header_line, re.IGNORECASE) else header_currency
        if context.unit and not _unit_matches(context.unit, observed_unit, observed_currency, row_context, expected_currency=context.currency):
            continue
        if context.currency and observed_currency != context.currency.casefold():
            continue
        expected_scale = normalize_scale(context.scale)
        if context.scale and expected_scale is None:
            continue
        observed_scale = _scale_from_text(row, None) or _scale_from_text(header_line, None)
        if expected_scale != observed_scale:
            continue
        if context.table:
            name = _table_name(lines, row_index)
            if not name or not re.search(rf"\b{re.escape(_fold(context.table))}\b", _fold(name)):
                continue
        return True, "\n".join([header_line, row]), row_metric
    return False, None, None


def _version_match(context: _ClaimContext, source_ref: str, source_metadata: Mapping[str, Any] | None, source_versions: Mapping[str, Any] | None) -> tuple[bool, str | None]:
    if context.source_version is None and context.source_hash is None:
        return True, None
    metadata = dict(source_metadata or {})
    versions = dict(source_versions or {})
    version_item = versions.get(source_ref)
    if isinstance(version_item, Mapping):
        metadata = {**version_item, **metadata}
    elif version_item is not None:
        metadata.setdefault("version", version_item)
    if context.source_version is not None:
        observed = _first(metadata, "version", "source_version", "version_no")
        if observed is None or str(observed) != str(context.source_version):
            return False, f"The cited source version {context.source_version!s} is not the retained attempt version."
    if context.source_hash is not None:
        observed_hash = _first(metadata, "hash", "content_hash", "source_hash")
        if observed_hash is None or str(observed_hash) != context.source_hash:
            return False, "The cited source content hash is not the retained attempt version."
    return True, None


def _assertion_type(source_metadata: Mapping[str, Any] | None) -> str:
    metadata = source_metadata or {}
    source_type = str(metadata.get("source_type") or metadata.get("kind") or "").casefold()
    if any(token in source_type for token in ("reddit", "social", "user_provided", "user observation", "author")):
        return "author_assertion"
    if any(token in source_type for token in ("sec", "filing", "issuer", "submission")):
        return "issuer_assertion"
    if bool(metadata.get("is_untrusted")) and not source_type:
        return "author_assertion"
    return "reported_observation"


def _retained_version(
    context: _ClaimContext,
    source_metadata: Mapping[str, Any] | None,
    source_versions: Mapping[str, Any] | None,
) -> str | None:
    metadata = dict(source_metadata or {})
    item = (source_versions or {}).get(context.source_ref) if isinstance(source_versions, Mapping) else None
    if isinstance(item, Mapping):
        metadata = {**item, **metadata}
    elif item is not None:
        metadata.setdefault("version", item)
    value = _first(metadata, "version", "source_version", "version_no")
    return str(value) if value is not None else None


def _binding_checks(
    context: _ClaimContext,
    *,
    expected_issuer: str | None,
    expected_metric: str | None,
    status: str,
    text_match: bool,
    excerpt: str | None,
) -> list[dict[str, Any]]:
    """Produce a small, code-owned audit projection for each binding edge."""
    checks: list[dict[str, Any]] = []
    expected_values = {
        "issuer": expected_issuer,
        "metric": expected_metric or context.metric,
        "value": context.value,
        "unit": normalize_unit(context.unit),
        "scale": normalize_scale(context.scale) if context.scale else None,
        "currency": context.currency.casefold() if context.currency else None,
        "period": ", ".join(_period_requirements(context)) or None,
        "basis": ", ".join(sorted(_claim_basis(context))) or None,
        "statement_type": context.statement_type,
        "locator": context.locator or None,
    }
    for name, expected in expected_values.items():
        if status == "supported":
            check_status = "pass"
        elif name == "value":
            check_status = "pass" if text_match else "fail"
        elif expected is None:
            check_status = "unavailable"
        else:
            check_status = "ambiguous" if status == "ambiguous" else "fail"
        expected_text = "not requested" if expected is None else f"expected {expected!s}"
        checks.append({"key": name, "status": check_status, "reason": expected_text})
    return checks


def validate_fact_claim(
    claim: Any,
    source_content: Mapping[str, str],
    *,
    source_metadata: Mapping[str, Mapping[str, Any]] | None = None,
    source_versions: Mapping[str, Any] | None = None,
    as_of: str | None = None,
) -> dict[str, Any]:
    """Validate one claim against its cited, retained source passage.

    ``validation_status='validated'`` retains the existing API meaning: the
    cited passage matched the typed value.  Additional fields make the
    provenance boundary explicit: ``citation_match`` says whether the local
    citation matched, ``assertion_type`` says what kind of source statement it
    is, and ``corroboration_status`` remains ``not_assessed`` unless a caller
    supplies an independent corroboration service.  No field claims that a
    single issuer citation is corroborated truth.
    """
    context = _context_from_claim(claim)
    metadata = (source_metadata or {}).get(context.source_ref, {}) if isinstance(source_metadata, Mapping) else {}
    freshness_evaluation: dict[str, Any] | None = None
    if as_of:
        # Freshness is code-derived.  Provider ``freshness`` labels are kept
        # out of this input so a model cannot certify an old or future fact.
        freshness_evaluation = evaluate_fact_freshness(
            {
                "metric": context.metric or context.text,
                "period": context.period,
                "period_start": context.period_start,
                "period_end": context.period_end,
            },
            metadata,
            as_of=as_of,
        )
    base: dict[str, Any] = {
        "validation_status": "unavailable",
        "validation_reason": None,
        "excerpt": None,
        "matched_excerpt": None,
        "line_start": None,
        "line_end": None,
        "text_match": False,
        "semantic_status": "unavailable",
        "binding_checks": [],
        "source_version": _retained_version(context, metadata, source_versions),
        "freshness": freshness_evaluation["status"] if freshness_evaluation else "unknown",
        "freshness_status": freshness_evaluation["status"] if freshness_evaluation else "unknown",
        "freshness_evaluation": freshness_evaluation,
        "citation_match": "unavailable",
        "assertion_type": _assertion_type(metadata),
        "corroboration_status": "not_assessed",
        "extraction": {},
    }
    if context.value is None or str(context.value).strip() == "":
        base["validation_reason"] = "No claim value was recorded."
        return base
    if context.source_ref not in source_content:
        base["validation_reason"] = "The retained source is unavailable for this claim."
        return base
    passage, line_start, line_end = _locator_passage(source_content.get(context.source_ref), context.locator)
    if passage is None:
        base["validation_reason"] = "The retained source locator could not be resolved; use Lx or Lx-Ly."
        return base
    base.update({"excerpt": passage, "line_start": line_start, "line_end": line_end})
    expected_issuer = _explicit_issuer(context)
    expected_metric = _claim_metric(context)
    base["binding_checks"] = _binding_checks(
        context,
        expected_issuer=expected_issuer,
        expected_metric=expected_metric,
        status="unavailable",
        text_match=False,
        excerpt=passage,
    )
    if context.source_quote and context.source_quote not in passage:
        base.update({
            "validation_status": "proposed",
            "semantic_status": "mismatch",
            "citation_match": "unmatched",
            "validation_reason": "The supplied source quote is not present in the retained citation passage.",
        })
        return base
    market_source_currency: str | None = None
    full_source_lines = str(source_content.get(context.source_ref) or "").splitlines()
    market_source = str(metadata.get("source_type") or "").casefold() == "market_bars"
    if not market_source:
        for candidate_line in full_source_lines:
            candidates = _json_objects(candidate_line)
            if any(isinstance(candidate, Mapping) and str(candidate.get("source_type") or "").casefold() == "market_bars" for candidate in candidates):
                market_source = True
                break
    if market_source:
        # The canonical market archive stores currency in its metadata header
        # while each JSON bar row carries the symbol and OHLC fields.  Read
        # that retained header from the same immutable source only; callers
        # still perform the specialized completed-bar binding downstream.
        for candidate_line in full_source_lines:
            try:
                candidate = json.loads(candidate_line)
            except (TypeError, ValueError):
                continue
            if not isinstance(candidate, Mapping):
                continue
            nested = candidate.get("metadata") if isinstance(candidate.get("metadata"), Mapping) else {}
            currency = _first(nested, "currency", "currency_code") or _first(candidate, "currency", "currency_code")
            if currency:
                market_source_currency = str(currency).strip().casefold()
                break
    version_ok, version_reason = _version_match(context, context.source_ref, metadata, source_versions)
    if not version_ok:
        base.update({"validation_status": "proposed", "semantic_status": "mismatch", "citation_match": "unmatched", "validation_reason": version_reason})
        return base
    numeric = _decimal(context.value)
    if numeric is None:
        phrase = str(context.value).casefold()
        binding_text = context.source_quote or passage
        text_match = bool(phrase and phrase in binding_text.casefold())
        base["text_match"] = text_match
        binding_metrics = {item[2] for item in _metric_occurrences(binding_text)}
        period_values = _period_token_values(binding_text)
        issuer_bound = bool(expected_issuer and _issuer_match(expected_issuer, binding_text))
        metric_bound = bool(expected_metric and expected_metric in binding_metrics)
        period_bound = bool(_period_requirements(context)) and all(
            any(_period_matches(required, candidate) for candidate in period_values)
            for required in _period_requirements(context)
        )
        issuer_mentions = _issuer_mentions(binding_text) if expected_issuer else ()
        # A source quote is a locator aid, not proof by itself.  The quote must
        # bind the claimed issuer, metric and period in the same excerpt and
        # may not name a second issuer/metric that could own the statement.
        unambiguous = len(issuer_mentions) <= 1 and len(binding_metrics) <= 1
        if phrase and text_match and len(phrase) >= 1 and issuer_bound and metric_bound and period_bound and unambiguous:
            base.update({
                "validation_status": "validated",
                "semantic_status": "supported",
                "citation_match": "matched",
                "matched_excerpt": passage,
                "validation_reason": "Citation matched the retained text; issuer assertion and corroboration remain separate assessments.",
                "extraction": {"text_match": True},
            })
            base["binding_checks"] = _binding_checks(
                context,
                expected_issuer=expected_issuer,
                expected_metric=expected_metric,
                status="supported",
                text_match=True,
                excerpt=passage,
            )
        else:
            reason = "The exact text value was not found in the bound source passage."
            if text_match and not (issuer_bound and metric_bound and period_bound):
                reason = "Text claims require issuer, metric and period bounds in the same retained passage; an exact quote alone is not proof."
            elif text_match and not unambiguous:
                reason = "The text claim names multiple possible issuers or metrics in one passage and remains ambiguous."
            base.update({"validation_status": "proposed", "semantic_status": "mismatch", "citation_match": "unmatched", "validation_reason": reason})
        return base
    base["text_match"] = _numeric_match(numeric, passage)
    if not context.unit or not _period_requirements(context):
        base.update({
            "validation_status": "proposed",
            "semantic_status": "ambiguous",
            "citation_match": "unmatched",
            "validation_reason": "Numeric claims require an explicit unit and period before source binding.",
        })
        return base
    if normalize_unit(context.unit) is None:
        base.update({"validation_status": "proposed", "semantic_status": "mismatch", "citation_match": "unmatched", "validation_reason": "The claim unit is unsupported or scaled."})
        return base
    if context.scale and normalize_scale(context.scale) is None:
        base.update({"validation_status": "proposed", "semantic_status": "mismatch", "citation_match": "unmatched", "validation_reason": "The claim scale is unsupported."})
        return base
    # These exact operating metrics use their own bounded earnings parser.
    # A mixed sentence may report members/cardholders or both renewal regions;
    # matching any percentage in that sentence would not bind its meaning.
    from .earnings_operating_facts import OPERATING_BASES, bind_operating_claim
    if context.metric in OPERATING_BASES:
        operating = bind_operating_claim(dict(_mapping(claim)), str(source_content.get(context.source_ref) or ""), metadata)
        if operating is None:
            base.update({"validation_status": "proposed", "semantic_status": "mismatch", "citation_match": "unmatched",
                         "validation_reason": "The operating observation did not bind its exact issuer, current reporting quarter/date, metric clause, geography, percentage unit, basis and source quote."})
            return base
        base.update({"validation_status": "validated", "semantic_status": "supported", "citation_match": "matched", "text_match": True,
                     "matched_excerpt": context.source_quote,
                     "validation_reason": "The exact operating percentage matched a current-quarter earnings statement, issuer header, reporting date and metric/geography clause; source assertion, not independent corroboration.",
                     "extraction": {key: operating[key] for key in ("issuer", "metric", "value", "unit", "currency", "basis", "period", "period_start", "period_end", "proof")}})
        base["binding_checks"] = _binding_checks(context, expected_issuer=operating["issuer"], expected_metric=operating["metric"],
                                                 status="supported", text_match=True, excerpt=passage)
        return base
    if _metric_is_explicit(context) and not expected_metric:
        base.update({"validation_status": "proposed", "semantic_status": "mismatch", "citation_match": "unmatched", "validation_reason": "The explicit claim metric is unsupported."})
        return base
    if not expected_metric:
        base.update({"validation_status": "proposed", "semantic_status": "ambiguous", "citation_match": "unmatched", "validation_reason": "Numeric claims require an explicit or recognizable metric before source binding."})
        return base
    # Comparative issuer earnings releases often put the issuer, reporting
    # durations and dates above a diluted-EPS row. Reconstruct those exact
    # columns before falling back to the conservative single-passage binder.
    # The parser never trusts provider-supplied proof or status fields and
    # deliberately refuses ambiguous dollar signs and unidentified columns.
    from .comparable_financials import bind_claim as bind_comparable_claim
    comparable = bind_comparable_claim(dict(_mapping(claim)), source_content, source_metadata or {}, source_versions or {}, as_of=as_of)
    if comparable:
        base.update(validation_status="validated", semantic_status="supported", citation_match="matched", text_match=True,
            matched_excerpt=context.source_quote,
            validation_reason="Financial operand matched its exact SEC taxonomy, issuer, unit, period and archived field; source assertion, not independent corroboration.",
            extraction={key: comparable[key] for key in ("issuer", "metric", "value", "unit", "currency", "basis", "period", "period_start", "period_end", "proof")})
        base["binding_checks"] = _binding_checks(context, expected_issuer=expected_issuer, expected_metric=expected_metric, status="supported", text_match=True, excerpt=passage)
        return base
    from .earnings_financials import bind_release_eps_claim, bind_sec_eps_claim, bind_cross_source_release_eps_claim
    release_eps = bind_release_eps_claim(dict(_mapping(claim)), str(source_content.get(context.source_ref) or ""))
    if release_eps is None:
        release_eps = bind_sec_eps_claim(dict(_mapping(claim)), source_content, source_metadata or {}, source_versions or {}, as_of=as_of)
    if release_eps is None:
        release_eps = bind_cross_source_release_eps_claim(dict(_mapping(claim)), source_content, source_metadata or {}, source_versions or {}, as_of=as_of)
    if release_eps:
        base.update({
            "validation_status": "validated", "semantic_status": "supported",
            "citation_match": "matched", "text_match": True,
            "matched_excerpt": context.source_quote,
            "validation_reason": "Diluted EPS matched an exact source field, issuer identity, reporting period, currency and accounting/share basis; source assertion, not independent corroboration.",
            "extraction": {key: release_eps[key] for key in ("issuer", "metric", "value", "unit", "currency", "basis", "period", "period_start", "period_end", "proof")},
        })
        base["binding_checks"] = _binding_checks(context, expected_issuer=expected_issuer,
            expected_metric=expected_metric, status="supported", text_match=True, excerpt=passage)
        return base
    # When a bounded source quote is supplied, bind the value to that exact
    # retained excerpt.  Searching the whole locator after checking that the
    # quote merely occurs would let an unrelated row authorize the claim.
    binding_passage = context.source_quote or passage
    structured_ok, structured_excerpt = _structured_candidate(
        context,
        numeric,
        binding_passage,
        expected_issuer,
        expected_metric,
        market_source_currency,
    )
    if structured_ok:
        matched, excerpt, source_metric = True, structured_excerpt, expected_metric
    elif _json_objects(binding_passage):
        # Structured rows are authoritative for JSON evidence.  Falling back
        # to prose token matching here could relabel one numeric field as a
        # different field merely because both keys occur in the object.
        matched, excerpt, source_metric = False, None, None
    else:
        matched, excerpt, source_metric = _plain_candidates(
            context,
            numeric,
            passage,
            expected_issuer,
            expected_metric,
            context.source_quote,
        )
    if not matched:
        # A numeric token without a same-row issuer/metric/unit/period match is
        # deliberately proposed rather than promoted.  This is the key
        # fail-closed behavior for swapped issuer/year/unit/metric/table rows.
        base.update({
            "validation_status": "proposed",
            "semantic_status": "ambiguous" if base["text_match"] and len(list(_NUMERIC_TOKEN.finditer(passage))) > 1 else "mismatch",
            "citation_match": "unmatched",
            "validation_reason": "The numeric value was present, but no single cited row/field bound its issuer, metric, unit and period.",
            "extraction": {"issuer": expected_issuer, "metric": expected_metric},
        })
        return base
    base.update({
        "validation_status": "validated",
        "semantic_status": "supported",
        "citation_match": "matched",
        "matched_excerpt": excerpt or passage,
        "validation_reason": "Citation matched one bound source row/field; this is an issuer/source assertion, not independent corroboration.",
        "extraction": {
            "issuer": expected_issuer,
            "metric": source_metric,
            "period": context.period,
            "period_start": context.period_start,
            "period_end": context.period_end,
            "unit": normalize_unit(context.unit),
            "scale": normalize_scale(context.scale) if context.scale else None,
            "currency": context.currency,
            "basis": context.basis,
            "statement_type": context.statement_type,
            "table": context.table,
            "field": context.field,
            "source_version": base.get("source_version") or context.source_version,
        },
    })
    base["binding_checks"] = _binding_checks(
        context,
        expected_issuer=expected_issuer,
        expected_metric=source_metric,
        status="supported",
        text_match=True,
        excerpt=excerpt or passage,
    )
    return base


__all__ = ["normalize_scale", "normalize_unit", "unit_dimension", "validate_fact_claim"]
