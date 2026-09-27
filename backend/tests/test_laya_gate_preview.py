"""Pure, code-owned financial summaries supplied to the local Laya packet."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from backend.app.orchestration.workflow import Orchestrator
from backend.tests.test_five_question_commit_path import create_five_question_case


def _a11_preview_inputs(case: Any) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    run = case.repository.run_record(case.run_id)
    assert run is not None
    snapshot = json.loads(run["input_snapshot_json"])
    output = next(item for item in case.outputs if item["agent_id"] == "A11")
    attempt_inputs = case.repository.attempt_decision_inputs(output["attempt_id"])
    assert attempt_inputs is not None
    versions = case.repository.attempt_source_versions(output["attempt_id"])
    sources = case.repository.source_packet(run["namespace"], list(versions))
    return snapshot, output, sources, attempt_inputs


def test_laya_preview_contains_actual_valuation_and_supported_entry_range(tmp_path: Path) -> None:
    case = create_five_question_case(tmp_path)
    packet = case.laya_runtime.calls[0]["state"]

    assert packet["gates"]["pre_joint"] == "pass"
    assert packet["gates"]["valuation"] == {"status": "complete", "base": "120", "currency": "USD"}
    assert packet["gates"]["entry_range"] == ["90", "100"]
    assert "market" not in packet["gates"]
    assert "risk" not in packet["gates"]
    assert "payoff" not in packet["gates"]


def test_laya_preview_combined_budget_excess_does_not_pass(tmp_path: Path) -> None:
    case = create_five_question_case(tmp_path)
    snapshot, output, sources, attempt_inputs = _a11_preview_inputs(case)
    payload = copy.deepcopy(output)
    first = copy.deepcopy(payload["candidate_briefs"][0])
    second = copy.deepcopy(first)
    second["ticker"] = "XYZ"
    second["instrument"] = "ABC Inc"
    payload["candidate_briefs"] = [first, second]

    inputs = copy.deepcopy(attempt_inputs)
    portfolio = inputs["portfolio_snapshot"]
    portfolio["approved_budget"] = "100"
    portfolio["approved_budget_currency"] = "USD"
    market = copy.deepcopy(inputs["deterministic_market"])
    market["candidates"].append(
        {
            "ticker": "XYZ",
            "instrument_identity": {
                "status": "consistent",
                "asset_id": "asset-xyz",
                "symbol": "XYZ",
                "name": "ABC Inc",
            },
        }
    )
    inputs["deterministic_market"] = market

    gates = Orchestrator._laya_code_owned_gates(
        snapshot,
        deterministic_market=market,
        decision_inputs=inputs,
        ticker="ABC",
        payload=payload,
        sources=sources,
        run_id=case.run_id,
        as_of=output["created_at"],
    )

    assert gates["pre_joint"] != "pass"
    assert gates["pre_joint"].get("recommended_allocation") in {"fail", "unavailable"}


def test_laya_preview_ignores_provider_gate_and_valuation_fields(tmp_path: Path) -> None:
    case = create_five_question_case(tmp_path)
    packet = case.laya_runtime.calls[0]["state"]
    provider_candidate = next(item for item in case.outputs if item["agent_id"] == "A11")["candidate_briefs"][0]

    # The fixture provider supplies a reasoned Laya/Astra override and its
    # own narrative valuation assumptions. Neither can replace the packet's
    # code-owned financial result.
    assert provider_candidate["laya_response"]["position"] == "override"
    assert packet["gates"]["valuation"] == {"status": "complete", "base": "120", "currency": "USD"}
    assert packet["gates"]["pre_joint"] == "pass"


def test_laya_preview_does_not_write_receipts_or_infer(tmp_path: Path) -> None:
    case = create_five_question_case(tmp_path)
    snapshot, output, sources, attempt_inputs = _a11_preview_inputs(case)
    before_receipts = case.repository.decision_model_reviews(case.run_id, namespace="real")
    before_calls = len(case.laya_runtime.calls)

    Orchestrator._laya_code_owned_gates(
        snapshot,
        deterministic_market=attempt_inputs["deterministic_market"],
        decision_inputs=attempt_inputs,
        ticker="ABC",
        payload=output,
        sources=sources,
        run_id=case.run_id,
        as_of=output["created_at"],
    )

    assert case.repository.decision_model_reviews(case.run_id, namespace="real") == before_receipts
    assert len(case.laya_runtime.calls) == before_calls


def test_normal_projection_still_blocks_without_laya_receipts(tmp_path: Path) -> None:
    case = create_five_question_case(tmp_path, laya_pre_status="unavailable")
    candidate = case.candidate
    assert candidate is not None
    assert candidate["joint_review"]["status"] == "unavailable"
    assert candidate["recommendation_gate"]["status"] == "blocked"
    assert "joint_decision_review" in candidate["recommendation_gate"]["missing_inputs"]
