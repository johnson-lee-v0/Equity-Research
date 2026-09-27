"""Independent research acceptance tests using only local deterministic doubles.

The tests exercise the provider boundary and durable repository reads.  They
do not call Codex, use credentials, or depend on wall-clock sleeps.
"""

from __future__ import annotations

import asyncio
import json
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable

import pytest

from backend.app.config import Settings
from backend.app.db import utc_now
from backend.app.memory.repository import Repository
from backend.app.orchestration.workflow import Orchestrator
from backend.app.providers.base import ProviderResult
from backend.app.schemas import (
    AgentOutputPayload,
    AllocationProposal,
    Calculation,
    FactClaim,
    ImportRequest,
    ModelConfig,
    RiskSettingsRequest,
    RunCreate,
)


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ModelConfig(provider="codex", model="gpt-5.6-luna", reasoning_effort="max", profile="gpt-first")


@pytest.fixture()
def repository(tmp_path: Path) -> Repository:
    return Repository(config=Settings(data_dir=tmp_path, project_root=ROOT, codex_timeout_seconds=30))


def import_source(
    repository: Repository,
    key: str,
    content: str,
    *,
    title: str = "Synthetic dated 10-K filing",
    supersedes_id: str | None = None,
) -> dict[str, Any]:
    request = ImportRequest(
        namespace="real",
        kind="evidence",
        title=title,
        content=content,
        source_url="https://example.test/synthetic-filing",
        publication_at="2026-09-01",
        observed_at="2026-09-01",
        supersedes_id=supersedes_id,
        idempotency_key=key,
    )
    result = repository.import_evidence(request)
    assert result["duplicate"] is False
    return result


def create_single_task_run(
    repository: Repository,
    key: str,
    *,
    agent_id: str,
    kind: str,
    instruction: str,
    source_ids: list[str] | None = None,
    ticker: str = "TEST",
    question: str = "Review the supplied dated evidence.",
    dependencies: list[str] | None = None,
) -> tuple[dict[str, Any], RunCreate]:
    body = RunCreate(
        question=question,
        namespace="real",
        horizon="1m",
        ticker=ticker,
        source_ids=source_ids or [],
        idempotency_key=key,
    )
    created, reused = repository.create_run(body, [(agent_id, kind, instruction, dependencies or [])])
    assert reused is False
    return created, body


def packet_from_prompt(prompt: str) -> dict[str, Any]:
    marker = "<untrusted_evidence_packet>"
    assert marker in prompt
    content = prompt.split(marker, 1)[1].split("</untrusted_evidence_packet>", 1)[0].strip()
    return json.loads(content)


def fixture_payload(**updates: Any) -> AgentOutputPayload:
    values: dict[str, Any] = {
        "status": "completed",
        "title": "Synthetic provider output",
        "summary": "A deterministic provider result used for acceptance testing.",
        "analysis": "This fixture contains no live market observation.",
        "proposed_action": "defer",
    }
    values.update(updates)
    return AgentOutputPayload(**values)


class FakeProvider:
    """Provider double that returns a schema-valid payload without network I/O."""

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
        packet = packet_from_prompt(prompt)
        self.calls.append({"attempt_id": attempt_id, "config": config, "packet": packet, "prompt": prompt})
        payload = self.handler(packet)
        return ProviderResult(payload=payload.model_dump(), usage={"fake_calls": len(self.calls)})

    async def cancel(self, _attempt_id: str) -> dict[str, Any]:
        return {"cancelled": True}


class FakeRegistry:
    def __init__(self, provider: FakeProvider) -> None:
        self.provider = provider

    def adapter(self, provider: str) -> FakeProvider:
        assert provider == "codex"
        return self.provider

    @asynccontextmanager
    async def generation_slot(self, _provider: str):
        yield

    async def preflight(self, config: ModelConfig, execute: bool) -> dict[str, Any]:
        return await self.provider.preflight(config, execute)


def attempt_for(repository: Repository, task_id: str) -> Any:
    with repository.db.operation() as connection:
        row = connection.execute(
            "SELECT * FROM task_attempts WHERE task_id=? ORDER BY attempt_no DESC LIMIT 1",
            (task_id,),
        ).fetchone()
    assert row is not None
    return row


def test_commit_recomputes_decimal_calculations_and_rejects_ambiguous_inputs(repository: Repository, monkeypatch: pytest.MonkeyPatch) -> None:
    # The synthetic observations end on 2026-09-17.  Freeze only this test's
    # repository clock so the freshness assertions remain deterministic as
    # wall time advances.
    monkeypatch.setattr("backend.app.memory.repository.utc_now", lambda: "2026-09-17T00:00:00Z")
    source_content = "\n".join(
        [
            "Existing assets: 100 USD for year ended 2025-12-31.",
            "Current outstanding shares: 10 shares for year ended 2025-12-31.",
            "Cash raised: 20 USD for year ended 2025-12-31.",
            "Issue price: 2 USD/share on 2026-09-16.",
            "EPS weighted-average diluted shares: 9 shares for year ended 2025-12-31.",
            "Price observation one: 10 USD on 2026-09-15.",
            "Price observation two: 20 USD on 2026-09-16.",
            "Price observation three: 30 USD on 2026-09-17.",
            "Unverified source fact: 100 USD for year ended 2025-12-31.",
        ]
    )
    source = import_source(repository, "research-calculations-source", source_content)
    source_id = source["source_id"]

    facts = [
        FactClaim(claim="Existing assets", value="100", unit="USD", period="2025", period_end="2025-12-31", source_ref=source_id, locator="L1"),
        FactClaim(claim="Current outstanding shares", value="10", unit="shares", period="2025", period_end="2025-12-31", source_ref=source_id, locator="L2"),
        FactClaim(claim="Cash raised", value="20", unit="USD", period="2025", period_end="2025-12-31", source_ref=source_id, locator="L3"),
        FactClaim(claim="Issue price", value="2", unit="USD/share", period="2026-09-16", source_ref=source_id, locator="L4"),
        # The source says weighted-average shares.  Relabeling it in the
        # model claim must not make it usable as current outstanding shares.
        FactClaim(claim="Current outstanding shares", value="9", unit="shares", period="2025", period_end="2025-12-31", source_ref=source_id, locator="L5"),
        FactClaim(claim="Price observation one", value="10", unit="USD", period="2026-09-15", source_ref=source_id, locator="L6"),
        FactClaim(claim="Price observation two", value="20", unit="USD", period="2026-09-16", source_ref=source_id, locator="L7"),
        FactClaim(claim="Price observation three", value="30", unit="USD", period="2026-09-17", source_ref=source_id, locator="L8"),
        # The numeric value is deliberately absent from the cited passage.
        FactClaim(claim="Unverified source fact", value="999", unit="USD", period="2025", period_end="2025-12-31", source_ref=source_id, locator="L9"),
    ]
    calculations = [
        Calculation(label="asset to cash ratio", value="999", unit="fraction", formula="model supplied 999", operation="ratio", input_fact_indices=[0, 2]),
        Calculation(label="full window SMA", value="777", unit="USD", formula="model supplied 777", operation="moving_average", input_fact_indices=[5, 6, 7], window=3),
        Calculation(label="partial window SMA", value="888", unit="USD", formula="model supplied 888", operation="moving_average", input_fact_indices=[5, 6], window=3),
        Calculation(label="issuance shares", value="555", unit="shares", formula="model supplied 555", operation="issuance_shares", input_fact_indices=[0, 1, 2, 3]),
        Calculation(label="issuance assets", value="666", unit="USD", formula="model supplied 666", operation="issuance_assets", input_fact_indices=[0, 1, 2, 3]),
        Calculation(label="issuance per share", value="777", unit="USD/share", formula="model supplied 777", operation="issuance_per_share", input_fact_indices=[0, 1, 2, 3]),
        Calculation(label="weighted average is not current shares", value="444", unit="shares", formula="model supplied 444", operation="issuance_shares", input_fact_indices=[0, 4, 2, 3]),
        Calculation(label="unspecified operation", value="333", unit="USD", formula="model supplied 333", operation=None, input_fact_indices=[0, 2]),
        Calculation(label="unverified input", value="222", unit="fraction", formula="model supplied 222", operation="ratio", input_fact_indices=[8, 0]),
    ]
    payload = fixture_payload(fact_claims=facts, calculations=calculations, source_refs=[source_id])
    created, _ = create_single_task_run(
        repository,
        "research-calculations-run",
        agent_id="A03",
        kind="fundamental_review",
        instruction="Review the supplied filing calculations.",
        source_ids=[source_id],
    )
    provider = FakeProvider(lambda _packet: payload)
    asyncio.run(Orchestrator(repository, FakeRegistry(provider), repository.config).run(created["run_id"]))

    outputs = repository.latest_outputs(created["run_id"])
    assert len(outputs) == 1
    output = outputs[0]
    assert output["status"] == "needs_review"
    by_label = {item["label"]: item for item in output["calculations"]}

    # Model-supplied values and formulas are replaced by backend Decimal work.
    assert by_label["asset to cash ratio"]["value"] == "5.00000000"
    assert by_label["asset to cash ratio"]["formula"] == "backend.ratio(fact[0], fact[2])"
    assert by_label["full window SMA"]["value"] == "20.00000000"
    assert by_label["full window SMA"]["formula"] == "backend.moving_average(facts[5:7], window=3)"
    assert by_label["issuance shares"]["value"] == "20.00000000"
    assert by_label["issuance shares"]["formula"] == "backend.issuance_shares(fact[1], fact[2], fact[3])"
    assert by_label["issuance assets"]["value"] == "120.00000000"
    assert by_label["issuance per share"]["value"] == "6.00000000"

    for label in ("partial window SMA", "weighted average is not current shares", "unspecified operation", "unverified input"):
        assert by_label[label]["value"] is None
        assert by_label[label]["missing_reason"]
    assert any("Calculation" in issue for issue in output["missing_data"])


def test_source_amendment_preserves_history_refreshes_only_dependents_and_reuses_unrelated_run(repository: Repository) -> None:
    original_source = import_source(repository, "research-amendment-original", "Revenue: 100 USD for year ended 2025-12-31.")
    original_source_id = original_source["source_id"]
    unrelated_source = import_source(repository, "research-amendment-unrelated", "Revenue: 200 USD for year ended 2025-12-31.")
    unrelated_source_id = unrelated_source["source_id"]

    dependent, _ = create_single_task_run(
        repository,
        "research-amendment-dependent",
        agent_id="A03",
        kind="fundamental_review",
        instruction="Review the original source.",
        source_ids=[original_source_id],
        ticker="DEPEND",
    )
    dependent_task_id = dependent["tasks"][0]["id"]
    original_source_head = repository.sources("real", original_source_id)[0]
    dependent_attempt = repository.create_attempt(
        dependent_task_id,
        CONFIG,
        {original_source_id: {"version": original_source_head["version"], "hash": original_source_head["content_hash"]}},
    )
    dependent_payload = fixture_payload(
        title="Original source output",
        fact_claims=[FactClaim(claim="Revenue", value="100", unit="USD", period="2025", period_end="2025-12-31", source_ref=original_source_id, locator="L1")],
        source_refs=[original_source_id],
    )
    old_output = repository.commit_output(dependent_task_id, dependent_attempt["attempt_id"], dependent_payload, "real", CONFIG)

    unrelated, unrelated_body = create_single_task_run(
        repository,
        "research-amendment-unrelated-run",
        agent_id="A03",
        kind="fundamental_review",
        instruction="Review an unrelated source.",
        source_ids=[unrelated_source_id],
        ticker="OTHER",
    )
    unrelated_task_id = unrelated["tasks"][0]["id"]
    unrelated_source_head = repository.sources("real", unrelated_source_id)[0]
    unrelated_attempt = repository.create_attempt(
        unrelated_task_id,
        CONFIG,
        {unrelated_source_id: {"version": unrelated_source_head["version"], "hash": unrelated_source_head["content_hash"]}},
    )
    unrelated_payload = fixture_payload(
        title="Unrelated source output",
        fact_claims=[FactClaim(claim="Revenue", value="200", unit="USD", period="2025", period_end="2025-12-31", source_ref=unrelated_source_id, locator="L1")],
        source_refs=[unrelated_source_id],
    )
    unrelated_output = repository.commit_output(unrelated_task_id, unrelated_attempt["attempt_id"], unrelated_payload, "real", CONFIG)

    amended = import_source(
        repository,
        "research-amendment-new-head",
        "Revenue: 125 USD for year ended 2025-12-31.",
        supersedes_id=original_source_id,
    )
    amended_source_id = amended["source_id"]
    refresh_ids = amended["refresh_run_ids"]
    assert len(refresh_ids) == 1

    historical = repository.latest_outputs(dependent["run_id"])
    assert len(historical) == 1
    assert historical[0]["id"] == old_output["id"]
    assert historical[0]["status"] == "completed"
    assert historical[0]["source_refs"] == [original_source_id]
    assert repository.task(dependent_task_id)["status"] == "completed"

    refresh = repository.run_record(refresh_ids[0])
    assert refresh is not None
    refresh_snapshot = json.loads(refresh["input_snapshot_json"])
    assert refresh_snapshot["source_ids"] == [amended_source_id]
    assert refresh_snapshot["supersedes_run_id"] == dependent["run_id"]
    refresh_tasks = repository.tasks_for_run(refresh_ids[0])
    assert len(refresh_tasks) == 1
    assert json.loads(refresh_tasks[0]["input_refs_json"]) == [amended_source_id]
    assert refresh_tasks[0]["status"] == "queued"

    with repository.db.operation() as connection:
        invalidations = connection.execute(
            "SELECT run_id,task_id,output_id,source_id,supersedes_source_id FROM invalidations ORDER BY rowid"
        ).fetchall()
    assert len(invalidations) == 1
    assert invalidations[0]["run_id"] == dependent["run_id"]
    assert invalidations[0]["task_id"] == dependent_task_id
    assert invalidations[0]["output_id"] == old_output["id"]
    assert invalidations[0]["source_id"] == amended_source_id
    assert invalidations[0]["supersedes_source_id"] == original_source_id

    # A completed run whose source lineage is unrelated remains idempotently
    # reusable and is not pulled into the amendment refresh graph.
    reused, was_reused = repository.create_run(
        unrelated_body,
        [("A03", "fundamental_review", "Review an unrelated source.", [])],
    )
    assert was_reused is True
    assert reused["run_id"] == unrelated["run_id"]
    assert repository.latest_outputs(unrelated["run_id"])[0]["id"] == unrelated_output["id"]
    assert repository.sources("real", amended_source_id)[0]["content"] == "Revenue: 125 USD for year ended 2025-12-31."


def test_pm_revisions_call_named_analyst_then_pm_with_two_round_cap_and_gate_cio(repository: Repository) -> None:
    def review_handler(packet: dict[str, Any]) -> AgentOutputPayload:
        agent_id = packet["agent_id"]
        question = packet["question"]
        match = re.search(r"Targeted PM revision round (\d+)", question)
        round_no = int(match.group(1)) if match else 0
        if agent_id == "A10" and round_no == 0:
            return fixture_payload(
                title="PM requests targeted revision",
                review_disposition="revise",
                revision_requests=["A03: Recheck the dated fundamental fact."],
            )
        if agent_id == "A03" and round_no in {1, 2}:
            return fixture_payload(title=f"A03 revision round {round_no}")
        if agent_id == "A10" and round_no == 1:
            return fixture_payload(
                title="PM requests final targeted revision",
                review_disposition="revise",
                revision_requests=["A03: Resolve the remaining share-count question."],
            )
        if agent_id == "A10" and round_no == 2:
            return fixture_payload(title="PM rejects unresolved proposal", review_disposition="reject")
        if agent_id == "A11":
            return fixture_payload(title="CIO proposal", decision_disposition="recommend", proposed_action="recommend allocation")
        return fixture_payload(title=f"Initial {agent_id} output")

    body = RunCreate(
        question="Review this fundamental business thesis.",
        namespace="real",
        horizon="1m",
        ticker="REVISION",
        source_ids=[],
        idempotency_key="research-pm-revisions-run",
    )
    created, reused = repository.create_run(
        body,
        [
            ("A03", "fundamental_review", "Review the fundamental evidence.", []),
            ("A10", "pm_review", "Review the analyst packet.", ["fundamental_review"]),
            ("A11", "cio_review", "Review the PM disposition.", ["pm_review"]),
        ],
    )
    assert reused is False
    provider = FakeProvider(review_handler)
    asyncio.run(Orchestrator(repository, FakeRegistry(provider), repository.config).run(created["run_id"]))

    calls = [(call["packet"]["agent_id"], int(re.search(r"Targeted PM revision round (\d+)", call["packet"]["question"]).group(1)) if re.search(r"Targeted PM revision round (\d+)", call["packet"]["question"]) else 0) for call in provider.calls]
    assert calls == [("A03", 0), ("A10", 0), ("A03", 1), ("A10", 1), ("A03", 2), ("A10", 2), ("A11", 0)]
    revision_calls = [call for call in calls if call[1] > 0]
    assert len(revision_calls) == 4
    assert max(round_no for _agent, round_no in revision_calls) == 2
    assert all(revision_calls[index][0] == "A03" for index in (0, 2))
    assert all(revision_calls[index][0] == "A10" for index in (1, 3))

    outputs = repository.latest_outputs(created["run_id"])
    pm_outputs = [output for output in outputs if output["agent_id"] == "A10"]
    cio_outputs = [output for output in outputs if output["agent_id"] == "A11"]
    assert len(pm_outputs) == 3
    assert pm_outputs[-1]["review_disposition"] == "reject"
    assert len(cio_outputs) == 1
    assert cio_outputs[0]["decision_disposition"] in {"reject", "defer"}
    assert cio_outputs[0]["decision_disposition"] != "recommend"
    assert cio_outputs[0]["proposed_action"].split(":", 1)[0] in {"reject", "defer"}
    with repository.db.operation() as connection:
        decisions = connection.execute(
            "SELECT decision_type,disposition FROM decisions WHERE run_id=? ORDER BY rowid",
            (created["run_id"],),
        ).fetchall()
    assert [row["disposition"] for row in decisions if row["decision_type"] == "pm"] == ["defer", "defer", "reject"]
    assert [row["disposition"] for row in decisions if row["decision_type"] == "cio"] == [cio_outputs[0]["decision_disposition"]]


def test_hard_risk_cap_gates_cio_and_leaves_transaction_ledger_unchanged(repository: Repository) -> None:
    source = import_source(repository, "research-risk-observation", "Portfolio observation dated 2026-09-01.")
    source_id = source["source_id"]
    now = utc_now()
    with repository.db.transaction(immediate=True) as connection:
        connection.execute(
            "INSERT INTO accounts(id,label,account_type,base_currency,namespace,reconciliation_status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            ("acct-risk-fixture", "Synthetic account", "brokerage", "USD", "real", "reconciled", now, now),
        )
        connection.execute(
            "INSERT INTO balance_observations(id,account_id,amount,currency,observed_at,published_at,source_id,status,unknown_reason,namespace,import_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("bal-risk-fixture", "acct-risk-fixture", "1000", "USD", "2026-09-01", None, source_id, "confirmed", None, "real", None, now),
        )
        connection.execute(
            "INSERT INTO positions(id,account_id,symbol,quantity,cost_basis,currency,observed_at,source_id,status,unknown_reason,namespace,created_at,market_value,market_value_currency) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("pos-risk-fixture", "acct-risk-fixture", "TEST", "10", "50", "USD", "2026-09-01", source_id, "confirmed", None, "real", now, "1000", "USD"),
        )
    repository.set_risk_settings(
        RiskSettingsRequest(namespace="real", max_position_weight="0.10", max_sector_weight="1", cash_floor="0", account_restrictions=[])
    )
    before = {}
    with repository.db.operation() as connection:
        before["transactions"] = [tuple(row) for row in connection.execute("SELECT * FROM transactions ORDER BY rowid")]
        before["positions"] = [tuple(row) for row in connection.execute("SELECT * FROM positions ORDER BY rowid")]

    created, _ = create_single_task_run(
        repository,
        "research-risk-cap-run",
        agent_id="A11",
        kind="cio_review",
        instruction="Apply the PM and deterministic risk gates.",
        ticker="TEST",
    )
    proposal = AllocationProposal(
        account_id="acct-risk-fixture",
        symbol="TEST",
        sector="Information Technology",
        target_position_weight="0.25",
        sector_weight_after="0.25",
        cash_weight_after="0.75",
    )
    provider = FakeProvider(
        lambda _packet: fixture_payload(
            title="CIO attempted allocation",
            proposed_action="recommend buy TEST",
            decision_disposition="recommend",
            proposal=proposal,
        )
    )
    asyncio.run(Orchestrator(repository, FakeRegistry(provider), repository.config).run(created["run_id"]))

    outputs = repository.latest_outputs(created["run_id"])
    assert len(outputs) == 1
    saved = outputs[0]
    assert saved["decision_disposition"] == "reject"
    assert saved["proposed_action"].startswith("reject:")
    assert saved["proposal"]["target_position_weight"] == "0.25"
    with repository.db.operation() as connection:
        risk_results = connection.execute(
            "SELECT constraints_json FROM decisions WHERE run_id=? AND decision_type='cio' ORDER BY rowid DESC LIMIT 1",
            (created["run_id"],),
        ).fetchone()
        after_transactions = [tuple(row) for row in connection.execute("SELECT * FROM transactions ORDER BY rowid")]
        after_positions = [tuple(row) for row in connection.execute("SELECT * FROM positions ORDER BY rowid")]
    assert risk_results is not None
    assert any(item["check"] == "max_position_weight" and item["status"] == "fail" for item in json.loads(risk_results[0]))
    assert after_transactions == before["transactions"]
    assert after_positions == before["positions"]
