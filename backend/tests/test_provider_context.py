"""Behavior at the public boundary between frozen evidence and model input."""
from __future__ import annotations

from copy import deepcopy
import json

import pytest

from backend.app.orchestration.provider_context import prepare_provider_context


def source(source_id, content="Archived observation", **metadata):
    return {
        "id": source_id, "title": source_id, "url": f"https://issuer.example/{source_id}",
        "version": 3, "content_hash": f"hash-{source_id}", "content": content, **metadata,
    }


def test_current_material_survives_old_evidence_pressure_without_changing_inputs():
    sources = [source(f"old-{index}", "Old reported result. " * 700) for index in range(40)]
    sources.extend([
        source("market-old", source_type="market_bars"),
        source("market-current", source_type="market_bars"),
        source("identity", source_type="alpaca_asset_identity", symbol="ALFA"),
        source("discovery"),
        source("transcript", "Prepared remarks\n" + "Management answer. " * 900),
        source("interim"),
    ])
    context = {
        "question": "Should ALFA enter the watchlist?",
        "earnings_reviews": [{"ticker": "ALFA", "latest_transcript_source_id": "transcript"}],
        "interim_events": [{"source_ids": ["interim", "interim"]}],
        "deterministic_market": {"candidates": [{
            "ticker": "ALFA", "source_refs": ["market-current"],
            "technical": {"frequencies": {"daily": {"sma20": "10", "log_returns": ["0.1"] * 50}}},
        }]},
        "current_case_decision": {"candidates": [{
            "ticker": "ALFA", "watch_triggers": [{"condition": "Await update"}] * 30,
        }]},
        "financial_seeds": [{"fact_id": "fact-1", "source_ref": "interim"}],
    }
    before_context, before_sources = deepcopy(context), deepcopy(sources)
    packet = prepare_provider_context(context, sources, priority_source_ids=["discovery", "transcript"])
    projection = packet["evidence_projection"]

    assert context == before_context and sources == before_sources
    assert packet is not context
    assert projection["included_source_ids"][:5] == ["interim", "transcript", "discovery", "identity", "market-current"]
    assert "market-old" in projection["omitted_source_ids"]
    assert any(source_id.startswith("old-") for source_id in projection["omitted_source_ids"])
    assert set(projection["included_source_ids"]) | set(projection["omitted_source_ids"]) == {item["id"] for item in sources}
    assert projection["transcript_coverage"]["transcript"]["status"] == "complete"
    transcript = next(item for item in packet["evidence"] if item["id"] == "transcript")
    assert len(transcript["content"]) > 12_000
    assert transcript["content"].startswith("L1: Prepared remarks\nL2: Management answer.")
    assert (transcript["url"], transcript["version"], transcript["content_hash"]) == (
        "https://issuer.example/transcript", 3, "hash-transcript",
    )
    assert "log_returns" not in json.dumps(packet["deterministic_market"])
    assert len(packet["current_case_decision"]["candidates"][0]["watch_triggers"]) == 20
    assert packet["financial_seeds"] == context["financial_seeds"]


@pytest.mark.parametrize("legacy_handoff", [True, False], ids=["saved-earnings", "investment-process"])
def test_earnings_reservation_keeps_combined_evidence_within_existing_budget(legacy_handoff):
    review = {"ticker": "ALFA", "latest_transcript_source_id": "call", "analysis": "é" * 190_000}
    context = {"question": "Review ALFA"}
    if legacy_handoff:
        context["earnings_context"] = review
        reserved_chars = len(json.dumps(review, ensure_ascii=False))
    else:
        context["earnings_reviews"] = [review]
        context["valuation_research_contexts"] = {"ALFA": {"basis": "Reported annual results"}}
        reserved_chars = len(json.dumps([review], ensure_ascii=False)) + len(json.dumps(context["valuation_research_contexts"], ensure_ascii=False))
    sources = [source("call", "Latest management answer. " * 800)]
    sources.extend(source(f"old-{index}", "Earlier reported result. " * 200) for index in range(70))

    packet = prepare_provider_context(context, sources)

    assert packet["evidence_projection"]["max_chars"] == 320_000 - reserved_chars
    assert sum(len(item["content"]) for item in packet["evidence"]) + reserved_chars <= 320_000
    assert packet["evidence_projection"]["transcript_coverage"]["call"]["status"] == "complete"
    assert packet["evidence_projection"]["omitted_source_ids"]


def test_multiple_calls_report_complete_partial_omitted_and_unavailable_separately():
    context = {
        "question": "Compare these companies",
        "earnings_reviews": [
            {"ticker": "ALFA", "latest_transcript_source_id": "complete"},
            {"ticker": "BETA", "latest_transcript_source_id": "partial"},
            {"ticker": "GAMA", "latest_transcript_source_id": "not-in-packet"},
            {"ticker": "DELT", "latest_transcript_source_id": None},
        ],
    }
    packet = prepare_provider_context(context, [
        source("complete", "Analyst: Why?\nManagement: Because demand grew."),
        source("partial", "Analyst: What changed?\n" + "Long management answer. " * 8_000),
    ])

    assert [item["status"] for item in packet["earnings_call_coverage"]] == ["complete", "partial", "omitted", "unavailable"]
    partial = packet["earnings_call_coverage"][1]
    assert partial["partial_lines"] or partial["omitted_line_ranges"]
    assert "Never claim to have read complete management answers" in packet["question"]
    assert context["question"] == "Compare these companies"


def test_exhausted_reservation_preserves_evidence_floor_and_reports_dropped_call():
    context = {
        "question": "Review ALFA",
        "earnings_reviews": [{"ticker": "ALFA", "latest_transcript_source_id": "call", "analysis": "x" * 320_000}],
    }
    packet = prepare_provider_context(context, [
        source("call", "Long management answer. " * 1_000),
        source("small", "Brief primary-source observation"),
    ])

    assert packet["evidence_projection"]["max_chars"] == 16_000
    assert packet["evidence_projection"]["included_source_ids"] == ["small"]
    assert packet["evidence_projection"]["omitted_source_ids"] == ["call"]
    assert packet["earnings_call_coverage"] == [{"ticker": "ALFA", "source_id": "call", "status": "omitted"}]
