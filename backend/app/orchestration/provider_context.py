"""Prepare the bounded evidence view sent to a research provider.

Call ``prepare_provider_context`` after freezing full decision inputs. This
module owns projection, source priority, budget reservation and transcript
coverage. It performs no persistence, retrieval, financial calculation or
model calls, and never mutates its inputs. Archived evidence remains complete.
"""
from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping
from typing import Any

from ..research.earnings_context import MAX_TRANSCRIPT_CHARS
from ..research.source_context import number_source_lines, structured_source_metadata


EVIDENCE_MAX_CHARS = 320_000
EVIDENCE_MIN_CHARS = 16_000


def prepare_provider_context(
    context: Mapping[str, Any],
    sources: list[dict[str, Any]],
    *,
    priority_source_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Return a bounded model view without changing the frozen research inputs.

    The caller supplies the current discovery handoff IDs; this module adds
    interim-event and latest-call priority, reserves space for earnings and
    valuation context, and reports every omitted source or partial call.
    Budgets cover evidence content, not the entire serialized provider prompt.
    Unrelated context fields pass through unchanged.
    """
    projected = dict(context)
    full_market = context.get("deterministic_market")
    projected["deterministic_market"] = _deterministic_market_projection(full_market)
    projected["current_case_decision"] = _case_decision_projection(context.get("current_case_decision"))

    earnings_context = context.get("earnings_context")
    earnings_reviews = context.get("earnings_reviews") or []
    transcript_ids = (
        [earnings_context["latest_transcript_source_id"]]
        if earnings_context and earnings_context.get("latest_transcript_source_id") else []
    )
    transcript_ids = list(dict.fromkeys([
        *transcript_ids,
        *[review["latest_transcript_source_id"] for review in earnings_reviews if review.get("latest_transcript_source_id")],
    ]))
    interim_source_ids = [
        source_id for receipt in context.get("interim_events", [])
        for source_id in receipt.get("source_ids", [])
    ]
    source_priority = list(dict.fromkeys([
        *interim_source_ids, *transcript_ids, *(priority_source_ids or []),
    ]))
    earnings_context_chars = len(json.dumps(earnings_context, ensure_ascii=False)) if earnings_context else 0
    if earnings_reviews:
        earnings_context_chars += (
            len(json.dumps(earnings_reviews, ensure_ascii=False))
            + len(json.dumps(context.get("valuation_research_contexts", {}), ensure_ascii=False))
        )
    projected["evidence"], projected["evidence_projection"] = _evidence_projection(
        sources,
        full_market if isinstance(full_market, dict) else None,
        priority_source_ids=source_priority,
        full_transcript_source_ids=transcript_ids,
        max_chars=max(EVIDENCE_MIN_CHARS, EVIDENCE_MAX_CHARS - earnings_context_chars),
    )
    if earnings_context:
        projected["evidence_projection"]["earnings_context_chars"] = earnings_context_chars
        projected["evidence_projection"]["combined_max_chars"] = EVIDENCE_MAX_CHARS
    if earnings_reviews:
        supplied = set(projected["evidence_projection"]["included_source_ids"])
        coverage = projected["evidence_projection"]["transcript_coverage"]
        projected["earnings_call_coverage"] = []
        for review in earnings_reviews:
            source_id = review.get("latest_transcript_source_id")
            call_coverage = coverage.get(source_id) or {
                "status": "included" if source_id in supplied else "omitted" if source_id else "unavailable",
            }
            projected["earnings_call_coverage"].append({
                "ticker": review["ticker"], "source_id": source_id, **call_coverage,
            })
        projected["question"] += (
            "\nCheck earnings_call_coverage: any omitted or partial call is an analysis limitation. "
            "Never claim to have read complete management answers for an omitted/partial call."
        )
    return projected


def _technical_snapshot(value: Any) -> dict[str, Any]:
    """Keep compact indicator values while omitting repeated return arrays."""
    if not isinstance(value, dict):
        return {}
    fields = (
        "symbol", "timeframe", "frequency", "sample_count", "as_of", "currency", "source_refs",
        "sma20", "sma50", "sma200", "rsi14", "atr14", "realized_volatility", "drawdown",
        "max_drawdown", "rolling_high20", "rolling_low20", "volume_sma20", "missing_reasons",
        "direction_conditions", "indicator_methods", "interpretation",
    )
    projected = {key: value[key] for key in fields if key in value}
    if isinstance(projected.get("missing_reasons"), dict):
        projected["missing_reasons"] = dict(list(projected["missing_reasons"].items())[:40])
    if isinstance(projected.get("direction_conditions"), list):
        projected["direction_conditions"] = [
            {
                key: item.get(key)
                for key in ("name", "condition", "observed", "interpretation")
                if key in item
            }
            for item in projected["direction_conditions"][:40]
            if isinstance(item, dict)
        ]
    return projected


def _scenario(value: Any) -> dict[str, Any]:
    """Project deterministic scenario aggregates without raw paths/bars."""
    if not isinstance(value, dict):
        return {}
    fields = (
        "code_version", "method", "status", "ticker", "currency", "as_of", "source_refs",
        "source_hashes", "input_hash", "parameters", "assumptions", "limitations", "missing_reason",
        "calculation_status", "data_quality_status", "model_acceptance_status", "data_quality",
        "model_acceptance", "forecast_accepted", "forecast_status", "acceptance_reasons", "result_hash",
    )
    projected = {key: value[key] for key in fields if key in value}
    calibration = value.get("calibration")
    if isinstance(calibration, dict):
        calibration_fields = (
            "bar_count", "return_count", "first_observation", "last_observation", "omitted_older_bars",
            "excluded_bars", "initial_price", "mean_daily_log_return", "annualized_realized_volatility",
            "max_observation_gap_calendar_days",
        )
        projected["calibration"] = {key: calibration[key] for key in calibration_fields if key in calibration}
        discontinuities = calibration.get("discontinuities")
        if isinstance(discontinuities, list):
            projected["calibration"]["discontinuities"] = discontinuities[:20]
            if len(discontinuities) > 20:
                projected["calibration"]["discontinuities_omitted"] = len(discontinuities) - 20
    scenarios = value.get("scenarios")
    if isinstance(scenarios, dict):
        projected["scenarios"] = {}
        for name, item in list(scenarios.items())[:5]:
            if not isinstance(item, dict):
                continue
            projected["scenarios"][str(name)] = {
                key: item[key]
                for key in (
                    "annual_log_return_shift", "terminal_price_quantiles", "loss_frequency",
                    "terminal_return_quantiles", "worst_drawdown_quantiles", "mean_return_in_worst_five_percent",
                )
                if key in item
            }
    projected["provider_projection"] = {
        "raw_daily_bars_omitted": True,
        "raw_return_samples_omitted": True,
        "scenario_path_arrays_omitted": True,
        "message": "Code-owned aggregates, assumptions, hashes and source references are retained; raw samples and paths remain in the durable run snapshot.",
    }
    return projected


def _deterministic_market_projection(value: Any) -> dict[str, Any] | None:
    """Bound the market packet sent to providers while keeping full local state."""
    if not isinstance(value, dict):
        return value if value is None else {}
    projected: dict[str, Any] = {
        key: value[key]
        for key in ("status", "as_of", "source_refs", "interpretation")
        if key in value
    }
    candidates = value.get("candidates")
    projected_candidates: list[dict[str, Any]] = []
    if isinstance(candidates, list):
        for candidate in candidates[:5]:
            if not isinstance(candidate, dict):
                continue
            technical_bundle = candidate.get("technical") if isinstance(candidate.get("technical"), dict) else {}
            frequencies = technical_bundle.get("frequencies") if isinstance(technical_bundle.get("frequencies"), dict) else {}
            if not frequencies and isinstance(candidate.get("technicals"), dict):
                frequency_names = {"1Min": "minute", "1Hour": "hourly", "1Day": "daily", "1Week": "weekly"}
                frequencies = {
                    frequency: candidate["technicals"].get(timeframe, {})
                    for timeframe, frequency in frequency_names.items()
                    if isinstance(candidate["technicals"].get(timeframe), dict)
                }
            projected_frequencies = {
                str(name): _technical_snapshot(item)
                for name, item in list(frequencies.items())[:8]
                if isinstance(item, dict)
            }
            candidate_projection: dict[str, Any] = {
                key: candidate[key]
                for key in ("ticker", "currency", "source_refs", "technical_source_refs", "latest_bars", "status", "instrument_identity")
                if key in candidate
            }
            candidate_projection["technical"] = {
                key: technical_bundle[key]
                for key in ("code_version", "method", "source_refs")
                if key in technical_bundle
            }
            candidate_projection["technical"]["frequencies"] = projected_frequencies
            if isinstance(candidate.get("scenario"), dict):
                candidate_projection["scenario"] = _scenario(candidate["scenario"])
            projected_candidates.append(candidate_projection)
    projected["candidates"] = projected_candidates
    projected["provider_projection"] = {
        "duplicate_frequency_aliases_omitted": True,
        "raw_indicator_return_arrays_omitted": True,
        "raw_scenario_paths_omitted": True,
        "message": "This is a bounded provider view. The complete deterministic market context remains durable in the run and attempt snapshots.",
    }
    return projected


def _bounded_watch_value(value: Any, *, depth: int = 0) -> Any:
    """Copy watch data with bounded text and collection sizes for providers."""
    if isinstance(value, str):
        return value[:4_000]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if depth >= 4:
        return str(value)[:4_000]
    if isinstance(value, dict):
        return {
            str(key): _bounded_watch_value(child, depth=depth + 1)
            for key, child in list(value.items())[:40]
        }
    if isinstance(value, (list, tuple)):
        return [
            _bounded_watch_value(child, depth=depth + 1)
            for child in list(value)[:20]
        ]
    return str(value)[:4_000]


def _case_technical_projection(value: Any) -> dict[str, Any]:
    """Keep decision technicals useful without replaying raw aliases/returns."""
    if not isinstance(value, dict):
        return {}
    projected = _technical_snapshot(value)
    # Canonical decisions can contain either one compact snapshot or a
    # multi-frequency bundle.  Accept the historical aliases while
    # emitting one frequency map, so a future watch review cannot carry
    # the same minute/hour/day data several times.
    frequency_values: dict[str, Any] = {}
    direct_frequencies = value.get("frequencies")
    if isinstance(direct_frequencies, dict):
        frequency_values.update(direct_frequencies)
    nested_technical = value.get("technical")
    if isinstance(nested_technical, dict):
        nested_frequencies = nested_technical.get("frequencies")
        if isinstance(nested_frequencies, dict):
            for name, item in nested_frequencies.items():
                frequency_values.setdefault(str(name), item)
    timeframe_names = {
        "1Min": "minute", "1Hour": "hourly", "1Day": "daily", "1Week": "weekly",
        "minute": "minute", "hourly": "hourly", "daily": "daily", "weekly": "weekly",
    }
    aliases = value.get("technicals")
    if isinstance(aliases, dict):
        for name, item in aliases.items():
            frequency = timeframe_names.get(str(name))
            if frequency and isinstance(item, dict):
                frequency_values.setdefault(frequency, item)
    for name, frequency in timeframe_names.items():
        item = value.get(name)
        if frequency not in frequency_values and isinstance(item, dict):
            frequency_values[frequency] = item
    if frequency_values:
        projected["frequencies"] = {
            str(name): _technical_snapshot(item)
            for name, item in list(frequency_values.items())[:8]
            if isinstance(item, dict)
        }
    for key in (
        "code_version", "method", "status", "source_hashes", "input_hash", "result_hash",
        "missing_reason", "calculation_status", "data_quality_status", "model_acceptance_status",
        "forecast_accepted", "forecast_status", "acceptance_reasons",
    ):
        if key in value:
            projected[key] = copy.deepcopy(value[key])
    return projected


def _case_decision_projection(value: Any) -> dict[str, Any] | None:
    """Project the canonical decision for a provider without changing storage."""
    if not isinstance(value, dict):
        return value if value is None else {}
    projected = copy.deepcopy(value)
    candidates = value.get("candidates")
    if isinstance(candidates, list):
        projected_candidates: list[Any] = []
        for candidate in candidates[:20]:
            if not isinstance(candidate, dict):
                projected_candidates.append(copy.deepcopy(candidate))
                continue
            candidate_projection = copy.deepcopy(candidate)
            if isinstance(candidate.get("technical_indicators"), dict):
                candidate_projection["technical_indicators"] = _case_technical_projection(
                    candidate["technical_indicators"]
                )
            # These are provider-era aliases that may survive in a
            # historical JSON payload alongside technical_indicators.
            # The compact frequency map above is the single emitted copy.
            for alias in ("technicals", "technical"):
                candidate_projection.pop(alias, None)
            for key, item in list(candidate.items()):
                if key in {"watch_triggers", "watch_review", "watch_query", "future_watch", "future_watch_packet", "watch_packet"}:
                    candidate_projection[key] = _bounded_watch_value(item)
            projected_candidates.append(candidate_projection)
        projected["candidates"] = projected_candidates
    watch_keys = {"watch_triggers", "watch_review", "watch_query", "future_watch", "future_watch_packet", "watch_packet"}
    for key, item in list(value.items()):
        if key in watch_keys:
            projected[key] = _bounded_watch_value(item)
    projected["provider_projection"] = {
        "raw_technical_return_arrays_omitted": True,
        "duplicate_technical_aliases_omitted": True,
        "watch_data_bounded": True,
        "message": "This is a bounded provider view; the complete canonical decision remains durable and unchanged.",
    }
    return projected


def _evidence_projection(
    sources: list[dict[str, Any]],
    deterministic_market: dict[str, Any] | None,
    *,
    max_chars: int = EVIDENCE_MAX_CHARS,
    priority_source_ids: list[str] | None = None,
    full_transcript_source_ids: list[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Bound provider evidence while retaining links and true locators.

    ``priority_source_ids`` is a small backend-owned set for the current
    discovery handoff.  It is considered before older candidate context;
    selected market frames and archived asset identities follow it.  The
    complete source list still belongs to the immutable attempt, and every
    provider omission is returned in the projection metadata.
    """
    selected_market_ids: set[str] = set()
    identity_source_ids: list[str] = []
    if isinstance(deterministic_market, dict):
        for candidate in deterministic_market.get("candidates", []) if isinstance(deterministic_market.get("candidates"), list) else []:
            if not isinstance(candidate, dict):
                continue
            for value in candidate.get("source_refs", []) if isinstance(candidate.get("source_refs"), list) else []:
                selected_market_ids.add(str(value).strip())
            identity = candidate.get("instrument_identity")
            if isinstance(identity, dict):
                refs = identity.get("source_refs")
                if isinstance(refs, list):
                    identity_source_ids.extend(str(value).strip() for value in refs if str(value).strip())
            refs_by_frequency = candidate.get("technical_source_refs")
            if isinstance(refs_by_frequency, dict):
                for refs in refs_by_frequency.values():
                    if isinstance(refs, list):
                        selected_market_ids.update(str(value).strip() for value in refs if str(value).strip())
            scenario = candidate.get("scenario")
            if isinstance(scenario, dict) and isinstance(scenario.get("source_refs"), list):
                selected_market_ids.update(str(value).strip() for value in scenario["source_refs"] if str(value).strip())

    # A current identity archive may be present even when an older
    # deterministic context did not carry its source reference.  Keep
    # those explicit identity records ahead of ordinary narrative pages;
    # metadata, rather than titles or prose, determines their type.
    identity_candidates: list[tuple[str, str, dict[str, Any]]] = []
    for source in sources:
        source_id = str(source.get("id") or "").strip()
        metadata = structured_source_metadata(source)
        source_type = str(metadata.get("source_type") or source.get("source_type") or "").casefold()
        if source_id and source_type == "alpaca_asset_identity":
            identity_key = str(
                metadata.get("requested_symbol")
                or metadata.get("symbol")
                or metadata.get("ticker")
                or source_id
            ).strip().upper()
            identity_candidates.append((source_id, identity_key, source))
    identity_candidates.sort(
        key=lambda item: str(
            item[2].get("retrieved_at")
            or item[2].get("observed_at")
            or structured_source_metadata(item[2]).get("retrieved_at")
            or ""
        ),
        reverse=True,
    )
    # Exact deterministic references are authoritative.  If a candidate
    # has no such reference, retain only its newest identity archive by
    # requested symbol rather than replaying every historical lookup.
    identity_source_ids = list(dict.fromkeys(identity_source_ids))
    exact_identity_ids = set(identity_source_ids)
    covered_identity_keys = {
        identity_key
        for source_id, identity_key, _source in identity_candidates
        if source_id in exact_identity_ids
    }
    for source_id, identity_key, _source in identity_candidates:
        if source_id in exact_identity_ids or identity_key in covered_identity_keys:
            continue
        identity_source_ids.append(source_id)
        covered_identity_keys.add(identity_key)

    priority_ids = list(dict.fromkeys(
        str(value).strip()
        for value in (priority_source_ids or [])
        if str(value).strip()
    ))
    identity_source_ids = list(dict.fromkeys(identity_source_ids))
    selected_market_order = [
        str(source.get("id") or "").strip()
        for source in sources
        if str(source.get("id") or "").strip() in selected_market_ids
    ]
    source_by_id = {
        str(source.get("id") or "").strip(): source
        for source in sources
        if str(source.get("id") or "").strip()
    }
    ordered_sources: list[dict[str, Any]] = []
    ordered_ids: set[str] = set()

    def append_priority(source_id: str) -> None:
        if source_id in source_by_id and source_id not in ordered_ids:
            ordered_sources.append(source_by_id[source_id])
            ordered_ids.add(source_id)

    # Fresh pages from the latest A01 handoff come first, followed by
    # identity and the exact coherent market archives used by calculations.
    for source_id in priority_ids:
        append_priority(source_id)
    for source_id in identity_source_ids:
        append_priority(source_id)
    for source_id in selected_market_order:
        append_priority(source_id)
    # Keep the caller's stable order for older fundamentals and context so
    # deterministic retry packets remain auditable.
    for source in sources:
        source_id = str(source.get("id") or "").strip()
        if source_id and source_id not in ordered_ids:
            ordered_sources.append(source)
            ordered_ids.add(source_id)

    projected: list[dict[str, Any]] = []
    omitted: list[str] = []
    coverage: dict[str, Any] = {}
    full_transcript_ids = set(full_transcript_source_ids or [])
    used = 0
    for source in ordered_sources:
        source_id = str(source.get("id") or "").strip()
        if not source_id:
            continue
        metadata = structured_source_metadata(source)
        source_type = str(metadata.get("source_type") or source.get("source_type") or "").casefold()
        is_market = source_type in {"market_bars", "derived_weekly_market_bars"}
        if is_market and selected_market_ids and source_id not in selected_market_ids:
            omitted.append(source_id)
            continue
        full_transcript = source_id in full_transcript_ids
        per_source_limit = MAX_TRANSCRIPT_CHARS if full_transcript else (8_000 if is_market else 12_000)
        original = str(source.get("content") or "")
        content = number_source_lines(original, max_chars=per_source_limit, max_lines=5000 if full_transcript else 600)
        if used + len(content) > max_chars:
            omitted.append(source_id)
            continue
        projected.append({
            "id": source_id,
            "title": source.get("title"),
            "url": source.get("url"),
            "publisher": source.get("publisher"),
            "publication_at": source.get("publication_at"),
            "observed_at": source.get("observed_at"),
            "retrieved_at": source.get("retrieved_at"),
            "version": source.get("version"),
            "content_hash": source.get("content_hash"),
            "content": content,
        })
        used += len(content)
        if full_transcript:
            original_lines = original.splitlines() or [original]
            supplied = {int(match.group(1)): match.group(2) for match in re.finditer(r"^L(\d+): (.*)$", content, re.MULTILINE)}
            omitted_lines = [index for index in range(1, len(original_lines) + 1) if index not in supplied]
            partial_lines = [index for index, line in supplied.items() if index <= len(original_lines) and line != original_lines[index - 1]]
            ranges = []
            for index in omitted_lines:
                if ranges and ranges[-1][1] + 1 == index:
                    ranges[-1][1] = index
                else:
                    ranges.append([index, index])
            coverage[source_id] = {"status": "partial" if omitted_lines or partial_lines else "complete", "original_lines": len(original_lines), "supplied_lines": len(supplied), "omitted_line_ranges": ranges, "partial_lines": partial_lines, "max_chars": per_source_limit}
    return projected, {
        "mode": "bounded_provider_evidence",
        "max_chars": max_chars,
        "included_source_ids": [item["id"] for item in projected],
        "omitted_source_ids": omitted,
        "omitted_market_archives": bool(omitted),
        "transcript_coverage": coverage,
        "priority_source_ids": priority_ids,
        "priority_included_source_ids": [
            source_id for source_id in priority_ids
            if source_id in {item["id"] for item in projected}
        ],
        "message": "Omitted archives remain available in the immutable evidence ledger; supplied locators retain their original line numbers.",
    }
