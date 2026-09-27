"""Focused regressions for durable intake and evidence-bound CIO data."""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from backend.app.config import Settings
from backend.app.db import new_id, utc_now
from backend.app.memory.repository import Repository
from backend.app.orchestration.workflow import Orchestrator, _defer_cio_brief
from backend.app.providers.base import ProviderResult
from backend.app.schemas import AgentOutputPayload, CandidateDecisionBrief, DecisionBrief, FactClaim, ImportRequest, MissingGap, ModelConfig, RunCreate


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ModelConfig(provider="codex", model="gpt-5.6-luna", reasoning_effort="max")


class _MemoryProvider:
    def __init__(self, payload_factory: Any | None = None) -> None:
        self.packets: list[dict[str, Any]] = []
        self.schemas: list[dict[str, Any]] = []
        self.payload_factory = payload_factory

    def is_preflighted(self, _config: ModelConfig) -> bool:
        return True

    async def preflight(self, config: ModelConfig, execute: bool) -> dict[str, Any]:
        return {"available": True, "status": "ready", "reason": None, "model": config.model, "reasoning_effort": config.reasoning_effort, "actual_execution": execute}

    async def execute(self, _attempt_id: str, prompt: str, _config: ModelConfig, _schema: dict[str, Any], _workdir: Path, _on_event: Any = None, **_kwargs: Any) -> ProviderResult:
        marker = "<untrusted_evidence_packet>"
        packet = json.loads(prompt.split(marker, 1)[1].split("</untrusted_evidence_packet>", 1)[0].strip())
        self.packets.append(packet)
        self.schemas.append(_schema)
        payload = self.payload_factory(packet) if self.payload_factory else AgentOutputPayload(status="completed", title="Memory integration", summary="Used the bounded packet.", analysis="The provider received source text in its evidence packet.")
        return ProviderResult(payload=payload.model_dump(), usage={})

    async def cancel(self, _attempt_id: str) -> dict[str, Any]:
        return {"cancelled": True}


class _MemoryRegistry:
    def __init__(self, provider: _MemoryProvider) -> None:
        self.provider = provider

    def adapter(self, provider: str) -> _MemoryProvider:
        assert provider == "codex"
        return self.provider

    @asynccontextmanager
    async def generation_slot(self, _provider: str):
        yield

    async def preflight(self, config: ModelConfig, execute: bool) -> dict[str, Any]:
        return await self.provider.preflight(config, execute)


def make_repo(tmp_path: Path) -> Repository:
    return Repository(config=Settings(data_dir=tmp_path, project_root=ROOT))


def reddit_post(key: str, *, score: int = 1, title: str | None = None) -> dict[str, object]:
    return {
        "post_id": f"t3_{key}",
        "subreddit": "wallstreetbets",
        "title": title or f"Synthetic {key}",
        "body": "A retained untrusted submission.",
        "created_at": "2026-09-12T00:00:00Z",
        "permalink": f"https://www.reddit.com/r/wallstreetbets/comments/{key}/",
        "score": score,
    }


def reddit_result(posts: list[dict[str, object]]) -> dict[str, object]:
    return {
        "posts": posts,
        "status": "ok",
        "metadata": {"retrieved_at": "2026-09-12T00:00:00Z", "last_consumed_cursor": posts[-1]["post_id"] if posts else None},
    }


def test_intake_pages_and_score_observations_are_durable_without_requeue(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    repo.ingest_reddit_result(reddit_result([reddit_post("a"), reddit_post("b"), reddit_post("c")]))
    first = repo.intake_status(limit=2, offset=0)
    second = repo.intake_status(limit=2, offset=2)
    assert first["total"] == 3
    assert first["has_more"] is True
    assert second["offset"] == 2
    assert len(second["items"]) == 1
    assert second["has_more"] is False

    item_before = next(item for item in first["items"] if item["external_id"] == "t3_a")
    source_before = item_before["versions"][-1]["source_id"]
    repo.ingest_reddit_result(reddit_result([reddit_post("a", score=99)]))
    item_after = next(item for item in repo.intake_status(limit=10)["items"] if item["external_id"] == "t3_a")
    assert item_after["status"] == "queued"
    assert item_after["versions"][-1]["score"] == 99
    assert item_after["versions"][-1]["source_id"] == source_before
    assert len(item_after["versions"]) == 2


def test_item_dispatch_is_namespace_scoped_capped_and_reusable(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    repo.ingest_reddit_result(reddit_result([reddit_post("a"), reddit_post("b")]))
    item = next(item for item in repo.intake_status(limit=10)["items"] if item["external_id"] == "t3_b")
    first = repo.dispatch_reddit_backlog(max_dispatches=5, max_cost=5, item_id=item["id"])
    assert len(first["dispatched"]) == 1
    assert first["dispatched"][0]["item_id"] == item["id"]
    reused = repo.dispatch_reddit_backlog(max_dispatches=5, max_cost=5, item_id=item["id"])
    assert reused["reused"][0]["run_id"] == first["dispatched"][0]["run_id"]
    pending = repo.dispatch_reddit_backlog(max_dispatches=5, max_cost=5)
    assert pending["active"] is True
    assert len(pending["dispatched"]) == 1
    assert pending["active_post_count"] == 2
    assert pending["available_slots"] == 1


def test_fresh_reddit_reservation_counts_toward_the_parallel_bound(tmp_path: Path) -> None:
    """A committed reservation occupies one of the bounded Reddit groups."""
    repo = make_repo(tmp_path)
    repo.ingest_reddit_result(reddit_result([reddit_post("reserved"), reddit_post("queued")]))
    with repo.db.transaction(immediate=True) as conn:
        item = conn.execute(
            "SELECT id FROM intake_items WHERE namespace='real' AND external_id='t3_reserved'"
        ).fetchone()
        now = utc_now()
        conn.execute(
            "UPDATE intake_items SET status='processing',updated_at=? WHERE id=?",
            (now, item["id"]),
        )
        conn.execute(
            "INSERT INTO intake_dispatches(id,item_id,run_id,status,cost_units,created_at,updated_at) "
            "VALUES(?,?,NULL,'queued',1,?,?)",
            (new_id("dispatch_"), item["id"], now, now),
        )
    result = repo.dispatch_reddit_backlog(max_dispatches=5, max_cost=5)
    assert result["active"] is True
    assert result["reserved_count"] == 1
    assert len(result["dispatched"]) == 1
    assert result["active_post_count"] == 2


def test_run_lineage_cannot_cross_namespace(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    real_root, _ = repo.create_run(
        RunCreate(question="A real research root", namespace="real", idempotency_key="lineage-real-root"),
        [],
        allow_semantic_reuse=False,
    )
    with pytest.raises(ValueError, match="another namespace"):
        repo.create_run(
            RunCreate(
                question="A demo child",
                namespace="demo",
                root_run_id=real_root["run_id"],
                idempotency_key="lineage-demo-child",
            ),
            [],
            parent_run_id=real_root["run_id"],
            allow_semantic_reuse=False,
        )


def test_price_brief_requires_symbol_currency_date_and_value_in_same_passage() -> None:
    source = "USO close 80 USD/share, BWET close 100 USD/share as of 2026-09-01"
    payload = AgentOutputPayload(
        status="completed",
        title="CIO",
        summary="Candidate prices",
        analysis="Review",
        fact_claims=[FactClaim(claim="USO close", value="100", unit="USD/share", period="2026-09-01", source_ref="src", locator="L1")],
        candidate_briefs=[CandidateDecisionBrief(ticker="USO", target_price="100", target_price_currency="USD", target_price_as_of="2026-09-01", target_price_source_refs=["src"], target_price_basis="fundamental valuation")],
    )
    normalized = Repository._normalize_decision_brief(payload, {"src"}, source_content={"src": source})
    assert normalized.candidate_briefs[0].target_price is None
    assert normalized.candidate_briefs[0].target_price_missing_reason

    valid_payload = payload.model_copy(update={"fact_claims": [payload.fact_claims[0].model_copy(update={"claim": "USO close", "value": "80"})], "candidate_briefs": [payload.candidate_briefs[0].model_copy(update={"target_price": "80"})]})
    valid = Repository._normalize_decision_brief(valid_payload, {"src"}, source_content={"src": source})
    assert valid.candidate_briefs[0].target_price is None


def test_natural_low_price_phrase_does_not_infer_low_as_another_symbol() -> None:
    source = "ABC low price 90 USD/share on 2026-09-16."
    payload = AgentOutputPayload(
        status="completed",
        title="CIO",
        summary="Candidate price",
        analysis="Review",
        source_refs=["src"],
        fact_claims=[FactClaim(
            claim="ABC low",
            subject="ABC",
            metric="low",
            value="90",
            unit="USD/share",
            currency="USD",
            period="2026-09-16",
            source_ref="src",
            locator="L1",
        )],
        candidate_briefs=[CandidateDecisionBrief(
            ticker="ABC",
            target_price="90",
            target_price_currency="USD",
            target_price_as_of="2026-09-16",
            target_price_source_refs=["src"],
            target_price_basis="fundamental valuation",
        )],
    )
    normalized = Repository._normalize_decision_brief(
        payload,
        {"src"},
        source_content={"src": source},
        source_metadata={"src": {"source_type": "market_data", "url": "https://data.alpaca.markets/v2/stocks/ABC/bars"}},
        run_ticker="ABC",
        as_of="2026-09-17T00:00:00Z",
    )
    assert normalized.candidate_briefs[0].target_price == "90.00000000"
    assert normalized.candidate_briefs[0].target_price_source_refs == ["src"]


def test_cio_gate_clears_active_candidate_entry_and_target() -> None:
    candidate = CandidateDecisionBrief(
        ticker="USO",
        stance="enter",
        entry_plan="Buy now",
        target_price="80",
        target_price_currency="USD",
        target_price_as_of="2026-09-01",
        target_price_source_refs=["src"],
        target_price_basis="fundamental valuation",
    )
    payload = AgentOutputPayload(
        status="completed", title="CIO", summary="Enter", analysis="Review", proposed_action="enter", stance="enter",
        entry_plan="Buy now", target_price="80", candidate_briefs=[candidate], decision_brief=DecisionBrief(
            ticker="USO", horizon="1m", stance="enter", entry_plan="Buy now", target_price="80",
            target_price_currency="USD", target_price_as_of="2026-09-01", target_price_source_refs=["src"],
            target_price_basis="fundamental valuation", candidate_briefs=[candidate],
        ),
    )
    gated = _defer_cio_brief(payload, "PM acceptance is unresolved.")
    assert gated.stance == "defer"
    assert gated.entry_plan is None and gated.target_price is None
    assert gated.decision_brief is not None and gated.decision_brief.stance == "defer"
    assert gated.decision_brief.candidate_briefs[0].target_price is None
    assert "PM acceptance is unresolved." in gated.missing_inputs


def _repair_root(repo: Repository, key: str) -> tuple[dict[str, object], dict[str, str]]:
    created, _ = repo.create_run(
        RunCreate(
            question="Bounded repair lifecycle fixture",
            namespace="real",
            ticker="USO",
            idempotency_key=key,
        ),
        [
            ("A02", "filing_review", "Filing review", []),
            ("A03", "fundamental_review", "Fundamental review", []),
            ("A09", "macro_review", "Macro review", []),
        ],
        allow_semantic_reuse=False,
    )
    task_ids = {str(task["agent_id"]): str(task["id"]) for task in created["tasks"]}
    return created, task_ids


def _commit_gap(repo: Repository, task_id: str, key: str, description: str, *, source_id: str | None = None) -> dict[str, object]:
    source_versions = {}
    if source_id:
        source = repo.sources("real", source_id)[0]
        source_versions[source_id] = {"version": source["version"], "hash": source["content_hash"]}
    attempt = repo.create_attempt(task_id, CONFIG, source_versions)
    claims = []
    refs = []
    if source_id:
        claims = [FactClaim(
            claim="ACME 2025 revenue",
            subject="ACME",
            metric="revenue",
            value="100",
            unit="USD",
            period="2025",
            period_end="2025-12-31",
            source_ref=source_id,
            locator="L1",
        )]
        refs = [source_id]
    return repo.commit_output(
        task_id,
        attempt["attempt_id"],
        AgentOutputPayload(
            status="needs_review",
            title=f"Unresolved {key}",
            summary="The bounded packet still has an explicit evidence gap.",
            analysis="No evidence was supplied in this fixture.",
            fact_claims=claims,
            source_refs=refs,
            missing_gaps=[MissingGap(key=key, description=description, relevant_role="A00", reopen_when="A dated primary source is archived.")],
        ),
        "real",
        CONFIG,
    )


def test_repairs_are_root_bounded_pause_safe_and_continue_through_committee(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    root, task_ids = _repair_root(repo, "repair-lifecycle-root-1")
    first = _commit_gap(repo, task_ids["A02"], "market-price-history", "A dated USO price history is missing.")
    first_repair = first["repair_run_ids"][0]

    # A root pause freezes the already queued child and prevents later
    # specialist outputs from fanning out additional children.
    assert repo.control("run", root["run_id"], "pause") == 1
    _commit_gap(repo, task_ids["A03"], "inventory-balance", "A dated public crude inventory balance is missing.")
    _commit_gap(repo, task_ids["A09"], "tanker-forward-rates", "A dated public tanker forward-rate series is missing.")
    with repo.db.operation() as conn:
        repairs = conn.execute("SELECT * FROM research_repairs WHERE root_run_id=? ORDER BY round_no", (root["run_id"],)).fetchall()
        paused_child = conn.execute("SELECT status FROM runs WHERE id=?", (first_repair,)).fetchone()
        gaps = conn.execute("SELECT normalized_gap,status,repair_run_id FROM research_gaps WHERE root_run_id=? ORDER BY normalized_gap", (root["run_id"],)).fetchall()
    assert len(repairs) == 1
    assert paused_child["status"] == "paused"
    assert [row["normalized_gap"] for row in gaps] == ["inventory_balance", "market_price_history", "tanker_forward_rates"]
    assert sum(row["repair_run_id"] == first_repair for row in gaps) == 1

    assert repo.control("run", root["run_id"], "resume") == 1
    child_a00 = next(task for task in repo.tasks_for_run(first_repair) if task["agent_id"] == "A00")
    repo.set_run_status(first_repair, "running")
    _commit_gap(repo, str(child_a00["id"]), "market-price-history", "A dated USO price history is still missing.")
    next_repairs = repo.set_run_status(first_repair, "completed")
    assert len(next_repairs) == 1

    with repo.db.operation() as conn:
        repairs = conn.execute("SELECT round_no,status,repair_run_id FROM research_repairs WHERE root_run_id=? ORDER BY round_no", (root["run_id"],)).fetchall()
        child_kinds = conn.execute("SELECT agent_id,kind FROM tasks WHERE run_id=? ORDER BY sequence_no", (next_repairs[0],)).fetchall()
    assert [(row["round_no"], row["status"]) for row in repairs] == [(1, "completed"), (2, "queued")]
    assert {row["agent_id"] for row in child_kinds} >= {"A00", "A01", "A04", "A09", "A10", "A11"}
    assert any(row["agent_id"] == "A10" and row["kind"] == "pm_review" for row in child_kinds)
    assert any(row["agent_id"] == "A11" and row["kind"] == "cio_review" for row in child_kinds)

    child2_a00 = next(task for task in repo.tasks_for_run(next_repairs[0]) if task["agent_id"] == "A00")
    repo.set_run_status(next_repairs[0], "running")
    _commit_gap(repo, str(child2_a00["id"]), "market-price-history", "The final bounded round still lacks a dated price history.")
    assert repo.set_run_status(next_repairs[0], "completed") == []
    with repo.db.operation() as conn:
        repairs = conn.execute("SELECT round_no,status FROM research_repairs WHERE root_run_id=? ORDER BY round_no", (root["run_id"],)).fetchall()
        gaps = conn.execute("SELECT status,terminal_reason,repair_run_id FROM research_gaps WHERE root_run_id=?", (root["run_id"],)).fetchall()
    assert [(row["round_no"], row["status"]) for row in repairs] == [(1, "completed"), (2, "completed")]
    assert all(row["status"] == "terminal" and row["terminal_reason"] == "budget" and row["repair_run_id"] is None for row in gaps)


def test_root_cancel_marks_repair_child_terminal_and_reopens_gaps(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    root, task_ids = _repair_root(repo, "repair-lifecycle-cancel-1")
    result = _commit_gap(repo, task_ids["A02"], "public-price-history", "A dated public price history is required.")
    repair_run_id = result["repair_run_ids"][0]
    assert repo.control("run", root["run_id"], "cancel") == 1
    with repo.db.operation() as conn:
        child = conn.execute("SELECT status FROM runs WHERE id=?", (repair_run_id,)).fetchone()
        repair = conn.execute("SELECT status FROM research_repairs WHERE repair_run_id=?", (repair_run_id,)).fetchone()
        gap = conn.execute("SELECT status,terminal_reason,repair_run_id FROM research_gaps WHERE root_run_id=?", (root["run_id"],)).fetchone()
    assert child["status"] == "cancelled"
    assert repair["status"] == "cancelled"
    assert gap["status"] == "open" and gap["terminal_reason"] is None and gap["repair_run_id"] is None


def test_legacy_missing_data_opens_a_gap_and_cio_defer_does_not_close_it(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    root, _ = repo.create_run(
        RunCreate(question="Legacy missing-data compatibility", namespace="real", idempotency_key="missing-data-fallback-1"),
        [("A03", "fundamental_review", "Review", []), ("A11", "cio_review", "CIO", ["fundamental_review"])],
        allow_semantic_reuse=False,
    )
    analyst = next(task for task in root["tasks"] if task["agent_id"] == "A03")
    attempt = repo.create_attempt(analyst["id"], CONFIG, {})
    committed = repo.commit_output(
        analyst["id"],
        attempt["attempt_id"],
        AgentOutputPayload(
            status="needs_review",
            title="Legacy gap output",
            summary="A legacy provider omitted structured gaps.",
            analysis="The missing-data field still identifies a public evidence request.",
            missing_data=["USO current issuer holdings are missing."],
        ),
        "real",
        CONFIG,
    )
    assert len(committed["repair_run_ids"]) == 1
    with repo.db.operation() as conn:
        gap = conn.execute("SELECT normalized_gap,status,assigned_agent_id FROM research_gaps WHERE root_run_id=?", (root["run_id"],)).fetchone()
    assert gap["normalized_gap"] == "uso_current_issuer_holdings_are_missing"
    assert gap["status"] == "in_progress" and gap["assigned_agent_id"] == "A06"

    cio = next(task for task in root["tasks"] if task["agent_id"] == "A11")
    cio_attempt = repo.create_attempt(cio["id"], CONFIG, {})
    repo.commit_output(
        cio["id"],
        cio_attempt["attempt_id"],
        AgentOutputPayload(
            status="completed",
            title="Deferred CIO",
            summary="The evidence remains unresolved.",
            analysis="The committee defers pending the missing public record.",
            decision_disposition="defer",
        ),
        "real",
        CONFIG,
    )
    with repo.db.operation() as conn:
        gap = conn.execute("SELECT status,resolved_by_output_id FROM research_gaps WHERE root_run_id=?", (root["run_id"],)).fetchone()
    assert gap["status"] == "in_progress" and gap["resolved_by_output_id"] is None


def test_workflow_merges_bounded_memory_sources_into_attempt_packet(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    imported = repo.import_evidence(
        ImportRequest(
            namespace="real",
            kind="evidence",
            title="Dated USO observation",
            content="USO public observation retained from an earlier question.",
            source_url="https://issuer.example.test/uso",
            publication_at="2026-09-01",
            observed_at="2026-09-01",
            idempotency_key="workflow-memory-source-1",
        )
    )
    source_id = str(imported["source_id"])
    first, _ = repo.create_run(
        RunCreate(question="Assess the USO observation.", namespace="real", ticker="USO", source_ids=[source_id], idempotency_key="workflow-memory-first-1"),
        [("A03", "fundamental_review", "Review", [])],
        allow_semantic_reuse=False,
    )
    first_task = first["tasks"][0]
    source = repo.sources("real", source_id)[0]
    first_attempt = repo.create_attempt(first_task["id"], CONFIG, {source_id: {"version": source["version"], "hash": source["content_hash"]}})
    prior = repo.commit_output(
        first_task["id"],
        first_attempt["attempt_id"],
        AgentOutputPayload(status="completed", title="Prior USO opinion", summary="The saved USO evidence is available.", analysis="Historical reasoning tied to the retained public observation.", source_refs=[source_id]),
        "real",
        CONFIG,
    )
    second, _ = repo.create_run(
        RunCreate(question="Recheck USO risks.", namespace="real", ticker="USO", idempotency_key="workflow-memory-second-1"),
        [("A03", "fundamental_review", "Review", [])],
        allow_semantic_reuse=False,
    )
    provider = _MemoryProvider()
    asyncio.run(Orchestrator(repo, _MemoryRegistry(provider), repo.config).run(second["run_id"]))

    assert provider.packets
    packet = provider.packets[0]
    assert source_id in packet["source_ids"]
    assert any(item["record_id"] == prior["id"] for item in packet["memory"]["reused"])
    assert packet["evidence"][0]["id"] == source_id
    second_task = repo.tasks_for_run(second["run_id"])[0]
    with repo.db.operation() as conn:
        attempt_row = conn.execute("SELECT source_versions_json FROM task_attempts WHERE task_id=?", (second_task["id"],)).fetchone()
    assert source_id in json.loads(attempt_row["source_versions_json"])


def test_repair_candidate_pipeline_reuses_role_task_and_keeps_committee_order(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    root, task_ids = _repair_root(repo, "repair-candidate-pipeline-1")
    result = _commit_gap(repo, task_ids["A02"], "market-price-history", "A dated public price history is required.")
    repair_run_id = result["repair_run_ids"][0]
    imported = repo.import_evidence(
        ImportRequest(
            namespace="real",
            kind="evidence",
            title="USO dated market page",
            content="USO close 80 USD/share as of 2026-09-01.",
            source_url="https://market.example.test/uso",
            publication_at="2026-09-01",
            observed_at="2026-09-01",
            idempotency_key="repair-candidate-source-1",
        )
    )
    source_id = str(imported["source_id"])
    discovery = next(task for task in repo.tasks_for_run(repair_run_id) if task["agent_id"] == "A01")
    repo.record_discovery(
        repair_run_id,
        discovery["id"],
        [{"ticker": "USO", "name": "United States Oil Fund", "rationale": "A dated public page.", "source_ids": [source_id]}],
        [source_id],
    )
    with repo.db.operation() as conn:
        rows = conn.execute("SELECT id,agent_id,kind,sequence_no FROM tasks WHERE run_id=? ORDER BY sequence_no,id", (repair_run_id,)).fetchall()
        pm = next(row for row in rows if row["agent_id"] == "A10")
        pm_deps = conn.execute("SELECT t.agent_id,t.kind FROM task_dependencies d JOIN tasks t ON t.id=d.depends_on_task_id WHERE d.task_id=? ORDER BY t.sequence_no", (pm["id"],)).fetchall()
    assert len({int(row["sequence_no"]) for row in rows}) == len(rows)
    assert sum(row["agent_id"] == "A04" for row in rows) == 1
    assert sum(row["agent_id"] == "A07" for row in rows) == 1
    assert {row["agent_id"] for row in pm_deps} >= {"A04", "A07"}


def test_workflow_resolves_superseded_explicit_source_to_current_head(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    old = repo.import_evidence(
        ImportRequest(
            namespace="real", kind="evidence", title="USO old page", content="USO close 70 USD/share as of 2026-09-01.",
            source_url="https://market.example.test/uso", publication_at="2026-09-01", observed_at="2026-09-01", idempotency_key="workflow-head-old-1",
        )
    )
    amended = repo.import_evidence(
        ImportRequest(
            namespace="real", kind="evidence", title="USO amended page", content="USO close 73 USD/share as of 2026-09-12.",
            source_url="https://market.example.test/uso", publication_at="2026-09-12", observed_at="2026-09-12", supersedes_id=old["source_id"], idempotency_key="workflow-head-new-1",
        )
    )
    root, _ = repo.create_run(
        RunCreate(question="Recheck amended USO evidence.", namespace="real", ticker="USO", source_ids=[old["source_id"]], idempotency_key="workflow-head-run-1"),
        [("A03", "fundamental_review", "Review", [])],
        allow_semantic_reuse=False,
    )
    provider = _MemoryProvider()
    asyncio.run(Orchestrator(repo, _MemoryRegistry(provider), repo.config).run(root["run_id"]))
    packet = provider.packets[0]
    assert packet["source_ids"] == [amended["source_id"]]
    assert old["source_id"] not in packet["source_ids"]
    assert packet["evidence"][0]["id"] == amended["source_id"]
    assert "73 USD" in packet["evidence"][0]["content"]
    fact_enum = provider.schemas[0]["$defs"]["FactClaim"]["properties"]["source_ref"]["enum"]
    assert fact_enum == [amended["source_id"]]
    task = repo.tasks_for_run(root["run_id"])[0]
    with repo.db.operation() as conn:
        attempt = conn.execute("SELECT source_versions_json FROM task_attempts WHERE task_id=?", (task["id"],)).fetchone()
        snapshot = json.loads(conn.execute("SELECT input_snapshot_json FROM runs WHERE id=?", (root["run_id"],)).fetchone()[0])
    assert set(json.loads(attempt["source_versions_json"])) == {amended["source_id"]}
    assert snapshot["requested_source_ids"] == [old["source_id"]]


def test_repair_child_runs_pm_and_cio_gates_before_bounded_followup(tmp_path: Path, monkeypatch: Any) -> None:
    repo = make_repo(tmp_path)
    root, task_ids = _repair_root(repo, "repair-committee-child-1")
    result = _commit_gap(repo, task_ids["A02"], "public-filing", "A dated public filing is required.")
    repair_run_id = result["repair_run_ids"][0]

    def repair_payload(packet: dict[str, Any]) -> AgentOutputPayload:
        if packet["agent_id"] == "A10":
            return AgentOutputPayload(
                status="completed", title="Repair PM acceptance", summary="The repaired evidence is ready for CIO review.",
                analysis="The named specialist completed the bounded evidence check.", review_disposition="accept",
            )
        if packet["agent_id"] == "A11":
            return AgentOutputPayload(
                status="completed", title="Repair CIO brief", summary="The updated committee brief is waiting on account gates.",
                analysis="The repaired filing was reviewed, but sizing remains gated by the frozen account packet.",
                decision_disposition="recommend", stance="enter", entry_plan="Enter after the account and risk inputs are confirmed.",
                decision_brief=DecisionBrief(ticker="USO", horizon="1m", stance="enter", entry_plan="Enter after the account and risk inputs are confirmed."),
            )
        return AgentOutputPayload(status="completed", title=f"Repair {packet['agent_id']}", summary="Bounded repair stage completed.", analysis="The stage returned no additional gap.")

    provider = _MemoryProvider(repair_payload)
    engine = Orchestrator(repo, _MemoryRegistry(provider), repo.config)
    # The test inspects the durable next round; do not start it before the
    # current child assertions run.
    monkeypatch.setattr(engine, "schedule", lambda _run_id: None)
    asyncio.run(engine.run(repair_run_id))

    child_outputs = repo.latest_outputs(repair_run_id)
    cio = next(item for item in child_outputs if item["agent_id"] == "A11")
    assert cio["decision_disposition"] == "defer"
    assert cio["decision_brief"]["stance"] == "defer"
    assert any(item["agent_id"] == "A10" and item["review_disposition"] == "accept" for item in child_outputs)
    with repo.db.operation() as conn:
        repairs = conn.execute("SELECT round_no,status FROM research_repairs WHERE root_run_id=? ORDER BY round_no", (root["run_id"],)).fetchall()
        gap = conn.execute("SELECT status,resolved_by_output_id FROM research_gaps WHERE root_run_id=?", (root["run_id"],)).fetchone()
    assert [(row["round_no"], row["status"]) for row in repairs] == [(1, "completed"), (2, "queued")]
    assert gap["status"] in {"open", "in_progress"} and gap["resolved_by_output_id"] is None


def test_accepted_repair_cio_closes_its_linked_gap(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    root, task_ids = _repair_root(repo, "repair-accepted-cio-1")
    result = _commit_gap(repo, task_ids["A02"], "public-filing", "A dated public filing is required.")
    repair_run_id = result["repair_run_ids"][0]
    cio = next(task for task in repo.tasks_for_run(repair_run_id) if task["agent_id"] == "A11")
    repo.set_run_status(repair_run_id, "running")
    attempt = repo.create_attempt(cio["id"], CONFIG, {})
    saved = repo.commit_output(
        cio["id"],
        attempt["attempt_id"],
        AgentOutputPayload(
            status="completed", title="Accepted repair CIO", summary="The repaired evidence is accepted.",
            analysis="The bounded repair packet resolved the named public filing gap.", decision_disposition="recommend",
        ),
        "real",
        CONFIG,
    )
    with repo.db.operation() as conn:
        gap = conn.execute("SELECT status,resolved_by_output_id,repair_run_id FROM research_gaps WHERE root_run_id=?", (root["run_id"],)).fetchone()
    assert saved["status"] == "completed"
    assert gap["status"] in {"open", "in_progress"} and gap["resolved_by_output_id"] is None


def test_accepted_repair_cio_requires_and_uses_bound_primary_evidence(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    root, task_ids = _repair_root(repo, "repair-accepted-cio-evidence-1")
    source = repo.import_evidence(
        ImportRequest(
            namespace="real",
            kind="evidence",
            title="ACME synthetic 10-K filing",
            content="ACME revenue was USD 100 for year ended 2025-12-31.",
            source_url="https://www.sec.gov/Archives/synthetic-acme-2025",
            publication_at="2026-09-01",
            observed_at="2026-09-01",
            idempotency_key="repair-accepted-cio-evidence-source",
        )
    )
    result = _commit_gap(repo, task_ids["A02"], "public-filing", "A dated public filing is required.", source_id=source["source_id"])
    repair_run_id = result["repair_run_ids"][0]
    cio = next(task for task in repo.tasks_for_run(repair_run_id) if task["agent_id"] == "A11")
    repo.set_run_status(repair_run_id, "running")
    source_head = repo.sources("real", source["source_id"])[0]
    attempt = repo.create_attempt(cio["id"], CONFIG, {source["source_id"]: {"version": source_head["version"], "hash": source_head["content_hash"]}})
    saved = repo.commit_output(
        cio["id"],
        attempt["attempt_id"],
        AgentOutputPayload(
            status="completed", title="Accepted repair CIO", summary="The repaired evidence is accepted.",
            analysis="The bounded repair packet resolved the named public filing gap.", decision_disposition="recommend",
            fact_claims=[FactClaim(
                claim="ACME 2025 revenue", subject="ACME", metric="revenue", value="100", unit="USD",
                period="2025", period_end="2025-12-31", source_ref=source["source_id"], locator="L1",
            )],
            source_refs=[source["source_id"]],
        ),
        "real",
        CONFIG,
    )
    with repo.db.operation() as conn:
        gap = conn.execute("SELECT status,resolved_by_output_id,repair_run_id FROM research_gaps WHERE root_run_id=?", (root["run_id"],)).fetchone()
    assert saved["status"] == "completed"
    assert gap["status"] == "resolved"
    assert gap["resolved_by_output_id"] == saved["id"]
    assert gap["repair_run_id"] is None


def test_accepted_repair_cio_does_not_close_named_requirement_with_wrong_fact_binding(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    root, task_ids = _repair_root(repo, "repair-accepted-cio-wrong-binding-1")
    source = repo.import_evidence(
        ImportRequest(
            namespace="real",
            kind="evidence",
            title="ACME synthetic 10-K filing",
            content="ACME revenue was USD 100 for year ended 2025-12-31.",
            source_url="https://www.sec.gov/Archives/synthetic-acme-wrong-binding",
            publication_at="2026-09-01",
            observed_at="2026-09-01",
            idempotency_key="repair-accepted-cio-wrong-binding-source",
        )
    )
    result = _commit_gap(repo, task_ids["A02"], "acme-debt-2024", "A dated ACME debt fact for 2024 is required.", source_id=source["source_id"])
    repair_run_id = result["repair_run_ids"][0]
    cio = next(task for task in repo.tasks_for_run(repair_run_id) if task["agent_id"] == "A11")
    repo.set_run_status(repair_run_id, "running")
    source_head = repo.sources("real", source["source_id"])[0]
    attempt = repo.create_attempt(cio["id"], CONFIG, {source["source_id"]: {"version": source_head["version"], "hash": source_head["content_hash"]}})
    saved = repo.commit_output(
        cio["id"],
        attempt["attempt_id"],
        AgentOutputPayload(
            status="completed", title="Accepted repair CIO", summary="The repaired evidence is accepted.",
            analysis="The bounded repair packet still names the wrong metric and period for the gap.", decision_disposition="recommend",
            fact_claims=[FactClaim(
                # The free-form claim deliberately mentions the gap metric;
                # closure must use validator-owned extraction instead.
                claim="ACME debt and revenue", subject="ACME", metric="revenue", value="100", unit="USD",
                period="2025", period_end="2025-12-31", source_ref=source["source_id"], locator="L1",
            )],
            source_refs=[source["source_id"]],
        ),
        "real",
        CONFIG,
    )
    with repo.db.operation() as conn:
        gap = conn.execute("SELECT status,resolved_by_output_id,repair_run_id FROM research_gaps WHERE root_run_id=?", (root["run_id"],)).fetchone()
    assert saved["status"] == "completed"
    assert gap["status"] in {"open", "in_progress"}
    assert gap["resolved_by_output_id"] is None


def test_accepted_repair_cio_does_not_close_named_requirement_with_stale_fact(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    root, task_ids = _repair_root(repo, "repair-accepted-cio-stale-fact-1")
    source = repo.import_evidence(
        ImportRequest(
            namespace="real",
            kind="evidence",
            title="ACME synthetic historical 10-K filing",
            content="ACME debt was USD 100 for year ended 2020-12-31.",
            source_url="https://www.sec.gov/Archives/synthetic-acme-2020",
            publication_at="2021-01-15",
            observed_at="2020-12-31",
            idempotency_key="repair-accepted-cio-stale-fact-source",
        )
    )
    result = _commit_gap(repo, task_ids["A02"], "acme-debt-2020", "A dated ACME debt fact for 2020 is required.", source_id=source["source_id"])
    repair_run_id = result["repair_run_ids"][0]
    cio = next(task for task in repo.tasks_for_run(repair_run_id) if task["agent_id"] == "A11")
    repo.set_run_status(repair_run_id, "running")
    source_head = repo.sources("real", source["source_id"])[0]
    attempt = repo.create_attempt(cio["id"], CONFIG, {source["source_id"]: {"version": source_head["version"], "hash": source_head["content_hash"]}})
    saved = repo.commit_output(
        cio["id"],
        attempt["attempt_id"],
        AgentOutputPayload(
            status="completed", title="Accepted repair CIO", summary="The repaired evidence is accepted.",
            analysis="The source is historical and should remain review-only.", decision_disposition="recommend",
            fact_claims=[FactClaim(
                claim="ACME 2020 debt", subject="ACME", metric="debt", value="100", unit="USD",
                period="2020", period_end="2020-12-31", source_ref=source["source_id"], locator="L1",
            )],
            source_refs=[source["source_id"]],
        ),
        "real",
        CONFIG,
    )
    with repo.db.operation() as conn:
        gap = conn.execute("SELECT status,resolved_by_output_id FROM research_gaps WHERE root_run_id=?", (root["run_id"],)).fetchone()
    assert saved["status"] == "needs_review"
    assert gap["status"] in {"open", "in_progress"}
    assert gap["resolved_by_output_id"] is None


def test_accepted_repair_cio_does_not_close_requirement_from_claim_only_issuer(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    root, task_ids = _repair_root(repo, "repair-accepted-cio-claim-only-issuer-1")
    source = repo.import_evidence(
        ImportRequest(
            namespace="real",
            kind="evidence",
            title="ACME synthetic 10-K filing",
            content="ACME revenue was USD 100 for year ended 2025-12-31.",
            source_url="https://www.sec.gov/Archives/synthetic-acme-2025-issuer-binding",
            publication_at="2026-09-01",
            observed_at="2026-09-01",
            idempotency_key="repair-accepted-cio-claim-only-issuer-source",
        )
    )
    result = _commit_gap(repo, task_ids["A02"], "otherco-revenue-2025", "A dated OTHERCO revenue fact for 2025 is required.", source_id=source["source_id"])
    repair_run_id = result["repair_run_ids"][0]
    cio = next(task for task in repo.tasks_for_run(repair_run_id) if task["agent_id"] == "A11")
    repo.set_run_status(repair_run_id, "running")
    source_head = repo.sources("real", source["source_id"])[0]
    attempt = repo.create_attempt(cio["id"], CONFIG, {source["source_id"]: {"version": source_head["version"], "hash": source_head["content_hash"]}})
    saved = repo.commit_output(
        cio["id"],
        attempt["attempt_id"],
        AgentOutputPayload(
            status="completed", title="Accepted repair CIO", summary="The repaired evidence is accepted.",
            analysis="The free-form claim mentions another issuer, but the retained row is ACME.", decision_disposition="recommend",
            fact_claims=[FactClaim(
                claim="OTHERCO and ACME revenue", subject="ACME", metric="revenue", value="100", unit="USD",
                period="2025", period_end="2025-12-31", source_ref=source["source_id"], locator="L1",
            )],
            source_refs=[source["source_id"]],
        ),
        "real",
        CONFIG,
    )
    with repo.db.operation() as conn:
        gap = conn.execute("SELECT status,resolved_by_output_id FROM research_gaps WHERE root_run_id=?", (root["run_id"],)).fetchone()
    assert saved["status"] == "completed"
    assert gap["status"] in {"open", "in_progress"}
    assert gap["resolved_by_output_id"] is None
