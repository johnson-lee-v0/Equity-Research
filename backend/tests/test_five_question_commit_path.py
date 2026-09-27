"""End-to-end five-question workflow fixtures.

The factory in this module deliberately starts a fresh lean run and lets the
orchestrator expand and dispatch it.  The provider and local classifier are
small deterministic doubles by default; callers may replace either boundary
when they want an isolated acceptance run against a real adapter.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping
from unittest.mock import patch

import pytest

from backend.app.config import Settings
from backend.app.memory.repository import Repository
from backend.app.orchestration.workflow import LEAN_WORKFLOW_MARKER, Orchestrator
from backend.app.providers.base import ProviderResult
from backend.app.research.case_store import CaseDecisionStore
from backend.app.research.decision_questions import FIVE_QUESTION_CONTRACT, FIVE_QUESTION_KEYS
from backend.app.schemas import (
    AgentOutputPayload,
    FactClaim,
    ImportRequest,
    ModelConfig,
    RunCreate,
)
from backend.tests.test_investment_engine import _snapshot, _v2_payload


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_AS_OF = "2026-09-17T00:00:00Z"
FIXTURE_PERIOD = "2025-12-31"
FIXTURE_QUOTE_DATE = "2026-09-16"


def _packet_from_prompt(prompt: str) -> dict[str, Any]:
    marker = "<untrusted_evidence_packet>"
    if marker not in prompt:
        raise AssertionError("fixture provider did not receive a structured evidence packet")
    content = prompt.split(marker, 1)[1].split("</untrusted_evidence_packet>", 1)[0].strip()
    return json.loads(content)


class _DeterministicProvider:
    """Provider double with observable stage order and exact model metadata."""

    def __init__(self, handler: Callable[[dict[str, Any]], AgentOutputPayload]) -> None:
        self.handler = handler
        self.calls: list[dict[str, Any]] = []
        self.preflight_calls: list[tuple[str, bool]] = []

    def is_preflighted(self, _config: ModelConfig) -> bool:
        return True

    async def preflight(self, config: ModelConfig, execute: bool) -> dict[str, Any]:
        self.preflight_calls.append((config.model, execute))
        return {
            "available": True,
            "status": "ready",
            "reason": None,
            "model": config.model,
            "reasoning_effort": config.reasoning_effort,
            "actual_execution": execute,
        }

    async def execute(
        self,
        attempt_id: str,
        prompt: str,
        config: ModelConfig,
        _schema: dict[str, Any],
        _workdir: Path,
        _on_event: Any = None,
    ) -> ProviderResult:
        packet = _packet_from_prompt(prompt)
        self.calls.append({"attempt_id": attempt_id, "config": config, "packet": packet})
        payload = self.handler(packet)
        return ProviderResult(payload=payload.model_dump(), usage={"fixture_calls": len(self.calls)})

    async def cancel(self, _attempt_id: str) -> dict[str, Any]:
        return {"cancelled": True}


class _DeterministicRegistry:
    def __init__(self, provider: _DeterministicProvider) -> None:
        self.provider = provider

    def adapter(self, provider: str) -> _DeterministicProvider:
        assert provider == "codex"
        return self.provider

    @asynccontextmanager
    async def generation_slot(self, _provider: str, **_kwargs: Any):
        yield

    async def preflight(self, config: ModelConfig, execute: bool) -> dict[str, Any]:
        return await self.provider.preflight(config, execute)


class _DeterministicLaya:
    """Bounded local classifier double used by the default factory."""

    def __init__(
        self,
        *,
        pre_status: str = "ok",
        pre_choice: str = "watchlist",
        post_status: str = "ok",
        post_choice: str = "accept_resolution",
    ) -> None:
        self.pre_status = pre_status
        self.pre_choice = pre_choice
        self.post_status = post_status
        self.post_choice = post_choice
        self.calls: list[dict[str, Any]] = []

    def classify(self, state: str, questions: Mapping[str, Any]) -> dict[str, Any]:
        phase = "pre_a11" if "disposition" in questions else "post_astra"
        self.calls.append({"phase": phase, "state": json.loads(state), "questions": dict(questions)})
        status = self.pre_status if phase == "pre_a11" else self.post_status
        choice = self.pre_choice if phase == "pre_a11" else self.post_choice
        if status != "ok":
            return {
                "status": status,
                "reason": f"fixture {phase} unavailable",
                "model": "fixture-laya-v1",
                "revision": "fixture-rev-1",
                "source_revision": "fixture-runtime-1",
                "device": "cpu",
                "answers": {},
            }
        question_id = "disposition" if phase == "pre_a11" else "resolution"
        return {
            "status": "ok",
            "model": "fixture-laya-v1",
            "revision": "fixture-rev-1",
            "source_revision": "fixture-runtime-1",
            "device": "cpu",
            "token_counts": {"input": 32, "output": 8, "limit": 512, "overflow": False},
            "answers": {question_id: {"choice": choice, "probabilities": {choice: 1.0}}},
        }


@dataclass
class FiveQuestionCase:
    repository: Repository
    run_id: str
    decision: dict[str, Any] | None
    provider: Any
    laya_runtime: Any
    outputs: list[dict[str, Any]]

    @property
    def candidate(self) -> dict[str, Any] | None:
        return (self.decision or {}).get("candidates", [None])[0]


def _source_claims(filing_id: str, market_id: str) -> list[dict[str, Any]]:
    """Provider-local claims used by both A03 and A11.

    Every claim has a local alias so the repository can assign a canonical
    fact ID on commit and the question proposals can remain portable across
    the two immutable outputs.
    """
    return [
        {
            "claim_id": "eps_baseline",
            "claim": "ABC diluted EPS",
            "value": "8",
            "unit": "USD/share",
            "currency": "USD",
            "period": FIXTURE_PERIOD,
            "source_ref": filing_id,
            "locator": "L1",
            "subject": "ABC Inc",
            "metric": "EPS",
            "basis": "GAAP diluted",
        },
        {
            "claim_id": "revenue_baseline",
            "claim": "ABC revenue",
            "value": "200",
            "unit": "USD",
            "currency": "USD",
            "period": FIXTURE_PERIOD,
            "source_ref": filing_id,
            "locator": "L2",
            "subject": "ABC Inc",
            "metric": "revenue",
        },
        {
            "claim_id": "catalyst_earnings",
            "claim": "ABC scheduled earnings",
            "value": "2026-10-25",
            "unit": "date",
            "period": "2026-10-25",
            "source_ref": filing_id,
            "locator": "L3",
        },
        {
            "claim_id": "market_low",
            "claim": "ABC low",
            "value": "90",
            "unit": "USD/share",
            "period": FIXTURE_QUOTE_DATE,
            "source_ref": market_id,
            "locator": "L1",
        },
        {
            "claim_id": "market_high",
            "claim": "ABC high",
            "value": "100",
            "unit": "USD/share",
            "period": FIXTURE_QUOTE_DATE,
            "source_ref": market_id,
            "locator": "L2",
        },
    ]


def _question_proposals() -> list[dict[str, Any]]:
    return [
        {
            "key": "opportunity",
            "answer": "Margin recovery creates a differentiated opportunity against flat expectations.",
            "decision_implication": "Keep the thesis eligible for review while the catalyst remains dated.",
            "supporting_claim_ids": ["eps_baseline", "revenue_baseline"],
        },
        {
            "key": "valuation",
            "answer": "The supported EPS and multiple imply value above the retained entry range.",
            "decision_implication": "Entry is acceptable inside the retained range when the valuation inputs remain current.",
            "supporting_claim_ids": ["eps_baseline", "market_high"],
        },
        {
            "key": "catalyst",
            "answer": "The scheduled earnings release is the next dated catalyst.",
            "decision_implication": "Review the thesis after the 2026-10-25 event.",
            "supporting_claim_ids": ["catalyst_earnings"],
        },
        {
            "key": "downside",
            "answer": "A break below the retained stop reference would invalidate the entry thesis.",
            "decision_implication": "Keep the proposed loss within the frozen trade policy.",
            "supporting_claim_ids": ["market_low"],
        },
        {
            "key": "portfolio_action",
            "answer": "A small long allocation is supported by the current evidence and policy envelope.",
            "decision_implication": "Use the code-owned sizing result and reassess at the catalyst.",
            "supporting_claim_ids": ["market_high", "market_low"],
        },
    ]


def _provider_payloads(filing_id: str, market_id: str, *, astra_position: str = "override") -> dict[str, AgentOutputPayload]:
    base = _v2_payload()
    candidate = json.loads(json.dumps(base["candidate_briefs"][0]))
    candidate["thesis"]["supporting_claim_ids"] = ["eps_baseline"]
    candidate["thesis"]["source_refs"] = [filing_id]
    candidate["entry_zone"]["source_refs"] = [market_id]
    candidate["entry_zone"]["as_of"] = FIXTURE_QUOTE_DATE
    candidate["entry_zone"]["basis"] = "A conditional entry within the retained daily low/high range."
    candidate["valuation_assumptions"]["methods"][0]["source_refs"] = [filing_id]
    candidate["valuation_assumptions"]["methods"][0]["fact_claim_ids"] = ["eps_baseline"]
    candidate["valuation_assumptions"]["methods"][0]["inputs"][0]["fact_claim_ids"] = ["eps_baseline"]
    candidate["valuation_assumptions"]["methods"][0]["inputs"][1].pop("source_refs", None)
    candidate["valuation_assumptions"]["methods"][0]["inputs"][0]["source_refs"] = [filing_id]
    candidate["key_questions"] = _question_proposals()
    candidate["laya_response"] = {
        "position": astra_position,
        "reason": "The retained EPS and price facts support a bounded recommendation after the local watchlist assessment.",
        "fact_claim_ids": ["eps_baseline"],
        "question_keys": ["opportunity", "valuation"],
    }
    candidate["stance"] = "enter"
    for trigger in candidate.get("action_plan", {}).get("catalyst_events", []):
        trigger["source_refs"] = [filing_id]
    claims = _source_claims(filing_id, market_id)
    common = {
        "status": "completed",
        "research_contract": FIVE_QUESTION_CONTRACT,
        "title": "Synthetic five-question review",
        "summary": "A dated synthetic case with a differentiated thesis, explicit valuation, and bounded portfolio action.",
        "analysis": "The retained filing and market observations support the five question packet; assumptions and unknowns remain explicit.",
        "fact_claims": claims,
        "source_refs": [filing_id, market_id],
        "counterarguments": ["Demand weakness could prevent the expected margin recovery."],
        "proposed_action": "recommend within the retained entry range",
        "decision_disposition": "recommend",
        "candidate_briefs": [candidate],
    }
    a03 = AgentOutputPayload.model_validate({**common, "title": "Synthetic researcher five-question synthesis"})
    a11 = AgentOutputPayload.model_validate({**common, "title": "Synthetic CIO five-question decision"})
    return {"A03": a03, "A11": a11}


def _routing_payload() -> AgentOutputPayload:
    return AgentOutputPayload(
        status="completed",
        title="Synthetic routing",
        summary="The synthetic case routes to a bounded issuer review.",
        analysis="The fixture route is explicit and contains no live discovery claim.",
        proposed_action="route to research",
        research_contract=FIVE_QUESTION_CONTRACT,
        routing_plan={
            "intent": "research",
            "horizon": "3m",
            "tickers": ["ABC"],
            "selected_analysts": ["A03"],
            "research_queries": ["ABC issuer fundamentals and upcoming earnings"],
            "rationale": "Synthetic route for the five-question commit-path fixture.",
            "research_contract": FIVE_QUESTION_CONTRACT,
        },
    )


def _discovery_payload() -> AgentOutputPayload:
    return AgentOutputPayload(
        status="completed",
        title="Synthetic discovery",
        summary="The retained source packet already identifies ABC.",
        analysis="No public fetch is needed for this isolated fixture.",
        proposed_action="continue to synthesis",
        research_contract=FIVE_QUESTION_CONTRACT,
        research_candidates=[
            {
                "ticker": "ABC",
                "name": "ABC Inc",
                "rationale": "The retained synthetic identity archive names the issuer.",
                "source_urls": [],
            }
        ],
    )


def _seed_portfolio(repository: Repository, data_dir: Path) -> None:
    seed_path = data_dir / "portfolio-seed.json"
    seed_path.write_text(
        json.dumps(
            {
                "accounts": [
                    {
                        "id": "synthetic-account",
                        "name": "Synthetic account",
                        "account_type": "nonregistered",
                        "base_currency": "USD",
                        "balances": [{"currency": "USD", "amount": "10000", "observed_at": FIXTURE_QUOTE_DATE}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    repository.load_portfolio_seed(seed_path)
    with repository.db.transaction(immediate=True) as connection:
        connection.execute("UPDATE accounts SET reconciliation_status='reconciled' WHERE id='synthetic-account'")
        connection.execute("UPDATE balance_observations SET status='confirmed',unknown_reason=NULL WHERE account_id='synthetic-account'")
        policy = {
            "status": "approved",
            "max_positions": 10,
            "tfsa_long_term_only": True,
            "allow_tfsa_outflows": False,
            "allow_chequing_to_nonregistered": True,
            "limits": {"trade": {"currency": "USD", "initial_notional": "2500", "max_notional": "5000", "planned_loss_limit": "250"}},
        }
        connection.execute(
            "INSERT INTO app_settings(key,value_json,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at",
            ("portfolio_policy:real", json.dumps(policy), FIXTURE_AS_OF),
        )


def _market_context_content() -> tuple[str, str]:
    """Return explicit synthetic identity and daily-bar archives for A03."""
    identity = "\n".join(
        [
            "Road2M canonical research source",
            json.dumps(
                {
                    "provider": "fixture",
                    "source_type": "alpaca_asset_identity",
                    "metadata": {"requested_symbol": "ABC", "retrieved_at": "2026-09-16T00:00:00Z"},
                },
                sort_keys=True,
            ),
            json.dumps(
                {
                    "instrument_identity": {
                        "status": "active",
                        "symbol": "ABC",
                        "name": "ABC Inc",
                        "asset_id": "fixture-asset-abc",
                        "exchange": "NYSE",
                        "tradable": True,
                        "fractionable": True,
                        "requested_symbol": "ABC",
                        "observed_at": "2026-09-16T00:00:00Z",
                    }
                },
                sort_keys=True,
            ),
        ]
    )
    rows: list[str] = []
    start = date(2026, 6, 1)
    for index in range(80):
        day = start + timedelta(days=index)
        close = 95 + (index % 9)
        rows.append(
            json.dumps(
                {
                    "symbol": "ABC",
                    "timestamp": f"{day.isoformat()}T20:00:00Z",
                    "open": close - 1,
                    "high": close + 2,
                    "low": close - 2,
                    "close": close,
                    "volume": 100_000 + index,
                    "currency": "USD",
                    "complete": True,
                },
                sort_keys=True,
            )
        )
    bars = "\n".join(
        [
            "Road2M canonical research source",
            json.dumps(
                {
                    "provider": "fixture",
                    "source_type": "market_bars",
                    "status": "ok",
                    "metadata": {
                        "timeframe": "1Day",
                        "symbols": ["ABC"],
                        "currency": "USD",
                        "feed": "fixture",
                        "adjustment": "raw",
                        "retrieved_at": "2026-09-16T00:00:00Z",
                    },
                },
                sort_keys=True,
            ),
            *rows,
        ]
    )
    return identity, bars


def _persist_synthetic_market_snapshot(repository: Repository, run_id: str) -> None:
    snapshot = _snapshot()
    with repository.db.transaction(immediate=True) as connection:
        row = connection.execute("SELECT input_snapshot_json FROM runs WHERE id=?", (run_id,)).fetchone()
        assert row is not None
        input_snapshot = json.loads(row[0])
        input_snapshot["deterministic_market"] = snapshot["deterministic_market"]
        connection.execute(
            "UPDATE runs SET input_snapshot_json=?,updated_at=? WHERE id=?",
            (json.dumps(input_snapshot), FIXTURE_AS_OF, run_id),
        )


def create_five_question_case(
    data_dir: str | Path,
    *,
    laya_runtime: Any | None = None,
    astra_adapter: Any | None = None,
    repository: Repository | None = None,
    as_of: str = FIXTURE_AS_OF,
    laya_pre_status: str = "ok",
    laya_post_status: str = "ok",
    laya_post_choice: str = "accept_resolution",
) -> FiveQuestionCase:
    """Create and run a fresh five-question case through every workflow gate.

    ``astra_adapter`` may be a complete provider registry (objects exposing
    ``adapter``, ``generation_slot`` and ``preflight``), or a provider adapter
    used for the A11 call.  The default remains a deterministic local
    provider, so ordinary tests never call a network or model service.
    """
    path = Path(data_dir)
    path.mkdir(parents=True, exist_ok=True)
    repository = repository or Repository(
        config=Settings(
            project_root=ROOT,
            data_dir=path,
            codex_timeout_seconds=30,
            enable_market_connectors=False,
            enable_reddit_intake=False,
        )
    )
    with patch("backend.app.memory.repository.utc_now", return_value=as_of):
        _seed_portfolio(repository, path)
        filing = repository.import_evidence(
            ImportRequest(
                namespace="real",
                kind="evidence",
                title="ABC synthetic 10-K filing",
                source_url="https://www.sec.gov/Archives/synthetic-abc-10-k",
                publication_at="2026-01-15",
                observed_at=as_of,
                idempotency_key="five-question-filing",
                content=(
                    "ABC Inc reported GAAP diluted EPS 8 USD/share for the year ended 2025-12-31.\n"
                    "ABC Inc reported revenue 200 USD for the year ended 2025-12-31.\n"
                    "ABC Inc scheduled earnings on 2026-10-25."
                ),
            )
        )["source_id"]
        market = repository.import_evidence(
            ImportRequest(
                namespace="real",
                kind="evidence",
                title="ABC synthetic market quote",
                source_url="https://data.alpaca.markets/v2/stocks/ABC/bars",
                observed_at=FIXTURE_QUOTE_DATE,
                idempotency_key="five-question-market",
                content=(
                    "ABC low price 90 USD/share on 2026-09-16.\n"
                    "ABC high price 100 USD/share on 2026-09-16."
                ),
            )
        )["source_id"]
        identity_content, bars_content = _market_context_content()
        identity = repository.import_evidence(
            ImportRequest(
                namespace="real",
                kind="evidence",
                title="ABC synthetic asset identity",
                source_url="https://data.alpaca.markets/v2/assets/ABC",
                observed_at=FIXTURE_QUOTE_DATE,
                idempotency_key="five-question-identity",
                content=identity_content,
            )
        )["source_id"]
        bars = repository.import_evidence(
            ImportRequest(
                namespace="real",
                kind="evidence",
                title="ABC synthetic daily market bars",
                source_url="https://data.alpaca.markets/v2/stocks/ABC/bars",
                observed_at=FIXTURE_QUOTE_DATE,
                idempotency_key="five-question-bars",
                content=bars_content,
            )
        )["source_id"]
        body = RunCreate(
            question="Review ABC with five decision questions and a bounded portfolio action.",
            namespace="real",
            horizon="3m",
            ticker="ABC",
            source_ids=[filing, market, identity, bars],
            research_contract=FIVE_QUESTION_CONTRACT,
            idempotency_key="five-question-commit-path",
        )
        created, reused = repository.create_run(
            body,
            [("A00", "routing", f"{LEAN_WORKFLOW_MARKER} route the synthetic five-question case.", [])],
        )
        assert reused is False
        run_id = created["run_id"]
        _persist_synthetic_market_snapshot(repository, run_id)

    payloads = _provider_payloads(filing, market)
    default_provider: _DeterministicProvider | None = None

    def provider_handler(packet: dict[str, Any]) -> AgentOutputPayload:
        agent_id = str(packet.get("agent_id") or "").upper()
        if agent_id == "A00":
            return _routing_payload()
        if agent_id == "A01":
            return _discovery_payload()
        if agent_id == "A03":
            return payloads["A03"]
        if agent_id == "A11":
            return payloads["A11"]
        raise AssertionError(f"unexpected fixture provider stage {agent_id!r}")

    if astra_adapter is not None and all(hasattr(astra_adapter, name) for name in ("adapter", "generation_slot", "preflight")):
        providers: Any = astra_adapter
    else:
        class _AdapterAwareProvider(_DeterministicProvider):
            async def execute(self, attempt_id: str, prompt: str, config: ModelConfig, schema: dict[str, Any], workdir: Path, on_event: Any = None) -> ProviderResult:
                packet = _packet_from_prompt(prompt)
                if astra_adapter is not None and str(packet.get("agent_id") or "").upper() == "A11" and hasattr(astra_adapter, "execute"):
                    value = astra_adapter.execute(attempt_id, prompt, config, schema, workdir, on_event)
                    if hasattr(value, "__await__"):
                        value = await value
                    if isinstance(value, ProviderResult):
                        return value
                    return ProviderResult(payload=value, usage={"external_astra": True})
                return await super().execute(attempt_id, prompt, config, schema, workdir, on_event)

        default_provider = _AdapterAwareProvider(provider_handler)
        providers = _DeterministicRegistry(default_provider)

    runtime = laya_runtime or _DeterministicLaya(
        pre_status=laya_pre_status,
        post_status=laya_post_status,
        post_choice=laya_post_choice,
    )
    # Keep the complete synthetic run on one frozen clock.  CaseStore uses
    # the immutable output timestamp as its decision ``as_of``; letting the
    # test process wall clock advance would turn the fixed 2026-09-16 quote
    # stale while the workflow is still being exercised.
    with patch("backend.app.memory.repository.utc_now", return_value=as_of):
        asyncio.run(Orchestrator(repository, providers, repository.config, laya_runtime=runtime).run(run_id))
    decision = CaseDecisionStore(repository).current(run_id, "real")
    return FiveQuestionCase(
        repository=repository,
        run_id=run_id,
        decision=decision,
        provider=default_provider or providers,
        laya_runtime=runtime,
        outputs=repository.latest_outputs(run_id),
    )


def _assert_stage_order(case: FiveQuestionCase, *, expected_laya_phases: tuple[str, ...] = ("pre_a11", "post_astra")) -> None:
    assert [item["packet"]["agent_id"] for item in case.provider.calls] == ["A00", "A01", "A03", "A11"]
    assert [item["phase"] for item in case.laya_runtime.calls] == list(expected_laya_phases)
    assert [item["agent_id"] for item in case.outputs] == ["A00", "A01", "A03", "A11"]


def test_five_question_factory_runs_full_commit_path_and_persists_receipts(tmp_path: Path) -> None:
    case = create_five_question_case(tmp_path)
    assert case.decision is not None
    _assert_stage_order(case)
    candidate = case.candidate
    assert candidate is not None
    assert [item["key"] for item in candidate["key_questions"]] == list(FIVE_QUESTION_KEYS)
    assert all(item["evidence_status"] == "complete" for item in candidate["key_questions"])
    assert all(item["verified_facts"] for item in candidate["key_questions"])
    assert candidate["joint_review"]["status"] == "resolved"
    assert candidate["joint_review"]["laya_outcome"] == "watchlist"
    assert candidate["joint_review"]["astra_outcome"] == "recommend"
    assert candidate["joint_review"]["astra_reasoned"] is True
    assert candidate["recommendation_gate"]["status"] == "pass"
    assert candidate["outcome"] == "recommend"
    receipts = case.repository.decision_model_reviews(case.run_id, namespace="real")
    by_phase = {item["phase"]: item for item in receipts}
    assert set(by_phase) == {"pre_a11", "post_astra"}
    assert (by_phase["pre_a11"]["status"], by_phase["pre_a11"]["result"]) == ("ok", "watchlist")
    assert (by_phase["post_astra"]["status"], by_phase["post_astra"]["result"]) == ("ok", "accept_resolution")
    assert all(item["model_id"] == "fixture-laya-v1" for item in receipts)
    assert all(item["model_revision"] == "fixture-rev-1" for item in receipts)
    assert all(item["fact_bindings"] for item in receipts)
    a11 = next(item for item in case.outputs if item["agent_id"] == "A11")
    assert a11["candidate_briefs"][0]["laya_response"]["position"] == "override"
    attempt = case.repository.attempt_decision_inputs(a11["attempt_id"])
    assert attempt is not None
    assert attempt["decision_receipt_ids"] == [by_phase["pre_a11"]["id"]]


def test_five_question_factory_blocks_when_local_laya_is_unavailable(tmp_path: Path) -> None:
    case = create_five_question_case(tmp_path, laya_pre_status="unavailable")
    assert case.decision is not None
    _assert_stage_order(case, expected_laya_phases=("pre_a11",))
    candidate = case.candidate
    assert candidate is not None
    assert candidate["outcome"] != "recommend"
    assert candidate["joint_review"]["status"] == "unavailable"
    assert candidate["recommendation_gate"]["status"] == "blocked"
    receipts = case.repository.decision_model_reviews(case.run_id, namespace="real")
    by_phase = {item["phase"]: item for item in receipts}
    assert set(by_phase) == {"pre_a11", "post_astra"}
    assert all(item["status"] == "unavailable" and item["result"] is None for item in by_phase.values())


def test_five_question_factory_keeps_disagreement_pending(tmp_path: Path) -> None:
    case = create_five_question_case(tmp_path, laya_post_choice="disagreement_remains")
    assert case.decision is not None
    candidate = case.candidate
    assert candidate is not None
    assert candidate["outcome"] != "recommend"
    assert candidate["joint_review"]["status"] == "pending"
    assert candidate["joint_review"]["disagreements"]
    assert candidate["recommendation_gate"]["status"] == "blocked"
    receipts = case.repository.decision_model_reviews(case.run_id, namespace="real")
    assert next(item for item in receipts if item["phase"] == "post_astra")["result"] == "disagreement_remains"
