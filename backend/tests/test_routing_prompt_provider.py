"""Regression coverage for routing prompt semantics and Codex web events."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from backend.app.agents.roles import ROLE_BY_ID, role_prompt
import backend.app.providers.codex as codex_module
from backend.app.config import Settings
from backend.app.orchestration.workflow import _safe_validation_retry_feedback, constrain_source_reference_schema
from backend.app.providers.base import ProviderError
from backend.app.providers.codex import CodexAdapter, _web_event_metadata
from backend.app.schemas import AgentOutputPayload, ModelConfig


def test_a00_prompt_explains_complete_team_selection_and_role_roster() -> None:
    prompt = role_prompt(
        ROLE_BY_ID["A00"],
        "Which industry ideas are on sale over the next two years?",
        None,
        "real",
        routing_stage=True,
    )

    assert "selected_analysts is the complete downstream specialist team" in prompt
    assert "A01 discovery runs automatically first" in prompt
    assert "A01 Universe Manager" in prompt
    assert "A03 Fundamental Analyst" in prompt
    assert "A05 Entry Analyst" in prompt
    assert "A08 Ownership & Public Filings" in prompt
    assert "A09 Macro Analyst" in prompt
    assert "return [A01] as the whole team" in prompt


def test_a00_revision_prompt_has_roster_without_recursive_routing() -> None:
    prompt = role_prompt(
        ROLE_BY_ID["A00"],
        "Clarify which analyst should verify the missing filing date.",
        "two years",
        "real",
        routing_stage=False,
    )

    assert "bounded follow-up or revision" in prompt
    assert "Do not create a new routing plan" in prompt
    assert "A10 Portfolio Manager" in prompt
    assert "A11 Chief Investment Officer" in prompt


def test_discovery_prompt_keeps_public_search_terms_and_private_data_out() -> None:
    prompt = role_prompt(
        ROLE_BY_ID["A01"],
        "Find public candidates in the energy sector.",
        "unspecified",
        "real",
        discovery_stage=True,
        routing_stage=False,
    )

    assert "up to five candidate tickers" in prompt
    assert "Public issuer names and tickers are allowed" in prompt
    assert "private portfolio positions" in prompt
    assert "personal names, account names or IDs" in prompt
    assert "at most six search operations and twelve total web operations" in prompt
    assert "Return fact_claims, calculations and source_refs as empty arrays" in prompt


def test_analyst_prompt_preserves_cited_text_facts_without_inventing_units() -> None:
    prompt = role_prompt(
        ROLE_BY_ID["A03"],
        "Review the supplied issuer announcement.",
        "unspecified",
        "real",
    )

    assert "value to the exact phrase appearing at the cited lines" in prompt
    assert "retain the exact amount as text" in prompt
    assert "instead of inventing a numeric unit" in prompt
    assert "supported units and a meaningful period" in prompt


def test_pm_prompt_keeps_revision_requests_inside_the_completed_discovery_packet() -> None:
    prompt = role_prompt(
        ROLE_BY_ID["A10"],
        "Review the supplied analyst packet and decide whether a correction is needed.",
        "unspecified",
        "real",
    )

    assert "ordinary A01 discovery pass is complete" in prompt
    assert "revision roles cannot acquire new external sources" in prompt
    assert "do not request new filings, quotes, macro data or other source retrieval" in prompt
    assert "set review_disposition=defer" in prompt
    assert "preserve useful research_candidates" in prompt
    assert "concrete reopen condition" in prompt


def test_attempt_source_schema_isolated_and_empty_evidence_is_bounded() -> None:
    original = AgentOutputPayload.model_json_schema()
    scoped = constrain_source_reference_schema(original, ["src_alpha", "src_beta", "src_alpha"])

    assert original["$defs"]["FactClaim"]["properties"]["source_ref"].get("enum") is None
    assert original["properties"]["source_refs"]["items"].get("enum") is None
    assert scoped["$defs"]["FactClaim"]["properties"]["source_ref"]["enum"] == ["src_alpha", "src_beta"]
    assert scoped["properties"]["source_refs"]["items"]["enum"] == ["src_alpha", "src_beta"]
    assert scoped["properties"]["fact_claims"]["maxItems"] == 100
    assert scoped["properties"]["source_refs"]["maxItems"] == 100

    empty = constrain_source_reference_schema(original, [])
    assert empty["properties"]["fact_claims"]["maxItems"] == 0
    assert empty["properties"]["source_refs"]["maxItems"] == 0
    assert "enum" not in empty["$defs"]["FactClaim"]["properties"]["source_ref"]
    assert "enum" not in empty["properties"]["source_refs"]["items"]
    # Deep-copying prevents one task's effective packet from changing the
    # global schema used by another provider attempt.
    assert "enum" not in original["$defs"]["FactClaim"]["properties"]["source_ref"]


def test_retry_feedback_repeats_exact_effective_source_ids_without_provider_text() -> None:
    feedback = _safe_validation_retry_feedback(
        ValueError("fact claim used an unknown source reference src_typo"),
        ["src_12c2ac67a05042de8cf9e57ab327ac1d", "src_4682da9efea94ec5806d8a7f1be827dc"],
    )

    assert "src_12c2ac67a05042de8cf9e57ab327ac1d" in feedback
    assert "src_4682da9efea94ec5806d8a7f1be827dc" in feedback
    assert "src_typo" not in feedback
    assert "exact supplied source IDs" in feedback


def test_five_question_retry_preserves_missing_questions_without_echoing_output() -> None:
    feedback = _safe_validation_retry_feedback(
        ValueError("candidate key_questions invalid: private provider text"), ["src_allowed"], five_question=True,
    )
    assert "exactly five key_questions" in feedback
    assert all(key in feedback for key in ("opportunity", "valuation", "catalyst", "downside", "portfolio_action"))
    assert "private provider text" not in feedback
    assert "five key_questions" not in _safe_validation_retry_feedback(ValueError("invalid output"), [])
    generic_retry = _safe_validation_retry_feedback(ValueError("The prior attempt failed backend output validation; cite exact supplied source IDs."), ["src_allowed"], five_question=True)
    assert "exactly five key_questions" in generic_retry
    assert "src_allowed" in generic_retry


def test_typed_web_search_lifecycle_events_are_narrowly_accepted() -> None:
    started = {
        "type": "item.started",
        "item": {
            "type": "web_search",
            "id": "exec-started",
            "action": {"type": "other"},
        },
    }
    completed = {
        "type": "item.completed",
        "item": {
            "type": "web_search",
            "id": "exec-completed",
            "action": {"type": "other"},
        },
    }
    for event in (started, completed):
        web_event, valid_wrapper, valid_action, item_type, nested_type, action_type, _action = _web_event_metadata(event)
        assert (web_event, valid_wrapper, valid_action) == (True, True, True)
        assert item_type in {"item.started", "item.completed"}
        assert nested_type == "web_search"
        assert action_type == "other"

    generic = {
        "type": "item.completed",
        "item": {
            "type": "command_execution",
            "id": "exec-generic",
            "action": {"type": "other"},
        },
    }
    web_event, valid_wrapper, valid_action, _item_type, nested_type, action_type, _action = _web_event_metadata(generic)
    assert web_event is False
    assert valid_wrapper is False
    assert valid_action is True
    assert nested_type == "command_execution"
    assert action_type == "other"


def test_completed_typed_web_events_without_action_use_unique_operation_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeStdin:
        def write(self, _data: bytes) -> None:
            return None

        async def drain(self) -> None:
            return None

        def close(self) -> None:
            return None

    class FakeStream:
        def __init__(self, lines: list[bytes] | None = None) -> None:
            self.lines = iter(lines or [])

        async def readline(self) -> bytes:
            return next(self.lines, b"")

        async def read(self) -> bytes:
            return b""

    class FakeProcess:
        def __init__(self, lines: list[bytes]) -> None:
            self.stdin = FakeStdin()
            self.stdout = FakeStream(lines)
            self.stderr = FakeStream()
            self.returncode: int | None = None

        def terminate(self) -> None:
            self.returncode = -15

        def kill(self) -> None:
            self.returncode = -9

        async def wait(self) -> int:
            if self.returncode is None:
                self.returncode = 0
            return self.returncode

    def web_item(event_type: str, operation_id: str, action: dict[str, str] | None = None) -> bytes:
        item: dict[str, object] = {"type": "web_search", "id": operation_id}
        if action is not None:
            item["action"] = action
        return (json.dumps({"type": event_type, "item": item}) + "\n").encode()

    # One operation has a started/completed pair and a duplicate completed
    # lifecycle event.  A second completed operation should be the first one
    # to exceed a cap of one, even though both completed records omit action.
    process = FakeProcess(
        [
            web_item("item.started", "operation-1", {"type": "other"}),
            web_item("item.completed", "operation-1"),
            web_item("item.completed", "operation-1"),
            web_item("item.completed", "operation-2"),
        ]
    )
    async def create_process(*_args: object, **_kwargs: object) -> FakeProcess:
        return process

    monkeypatch.setattr(codex_module.asyncio, "create_subprocess_exec", create_process)
    monkeypatch.setattr(codex_module, "MAX_DISCOVERY_WEB_ACTIONS", 1)
    adapter = CodexAdapter(Settings(data_dir=tmp_path, project_root=tmp_path))
    monkeypatch.setattr(adapter, "_binary_path", lambda: "/usr/bin/codex")
    config = ModelConfig(provider="codex", model="gpt-5.6-luna", reasoning_effort="max", profile="gpt-first")
    schema_path = tmp_path / "schema.json"
    result_path = tmp_path / "result.json"
    workdir = tmp_path / "worker"
    workdir.mkdir()
    schema_path.write_text("{}", encoding="utf-8")
    result_path.write_text("{}", encoding="utf-8")

    with pytest.raises(ProviderError, match="web-action limit"):
        asyncio.run(
            adapter._execute_raw(
                "attempt-1",
                "public discovery",
                config,
                schema_path,
                result_path,
                workdir,
                None,
                discovery_stage=True,
            )
        )
