"""Focused provider accounting for five-question public discovery limits."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

import backend.app.providers.codex as codex_module
from backend.app.config import Settings
from backend.app.providers.base import ProviderError
from backend.app.providers.codex import CodexAdapter
from backend.app.schemas import ModelConfig


class _Stdin:
    def write(self, _data: bytes) -> None:
        return None

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        return None


class _Stream:
    def __init__(self, lines: list[bytes] | None = None) -> None:
        self.lines = iter(lines or [])

    async def readline(self) -> bytes:
        return next(self.lines, b"")

    async def read(self) -> bytes:
        return b""


class _Process:
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self.stdin = _Stdin()
        self.stdout = _Stream([(json.dumps(event) + "\n").encode() for event in events])
        self.stderr = _Stream()
        self.returncode: int | None = None

    def terminate(self) -> None:
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


def _web(
    event_type: str,
    operation_id: str | None,
    *,
    action_type: str | None = None,
    queries: Any = None,
    include_queries: bool = False,
    nested_type: str = "web_search",
) -> dict[str, Any]:
    item: dict[str, Any] = {"type": nested_type}
    if operation_id is not None:
        item["id"] = operation_id
    if action_type is not None or include_queries:
        action: dict[str, Any] = {}
        if action_type is not None:
            action["type"] = action_type
        if include_queries:
            action["queries"] = queries
        item["action"] = action
    return {"type": event_type, "item": item}


def _run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    events: list[dict[str, Any]],
    *,
    limits: dict[str, int] | None = None,
    observed: list[Any] | None = None,
) -> Any:
    tmp_path.mkdir(parents=True, exist_ok=True)
    process = _Process(events)

    async def create_process(*_args: Any, **_kwargs: Any) -> _Process:
        return process

    monkeypatch.setattr(codex_module.asyncio, "create_subprocess_exec", create_process)
    adapter = CodexAdapter(Settings(data_dir=tmp_path, project_root=tmp_path))
    monkeypatch.setattr(adapter, "_binary_path", lambda: "/usr/bin/codex")
    config = ModelConfig(provider="codex", model="gpt-5.6-luna", reasoning_effort="max", profile="researcher")
    schema_path = tmp_path / "schema.json"
    result_path = tmp_path / "result.json"
    schema_path.write_text("{}", encoding="utf-8")
    result_path.write_text("{}", encoding="utf-8")
    return asyncio.run(
        adapter._execute_raw(
            "limit-attempt",
            "prompt text is never an authority for this budget",
            config,
            schema_path,
            result_path,
            tmp_path,
            observed.append if observed is not None else None,
            discovery_stage=True,
            discovery_limits=limits,
        )
    )


def test_initial_and_continuation_limits_are_explicit_and_independent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    initial: list[dict[str, Any]] = []
    for index in range(5):
        initial.extend(
            [
                _web("item.started", f"search-{index}", action_type="search", queries=[f"q-{index}"], include_queries=True),
                _web("item.completed", f"search-{index}", action_type="search", queries=[f"q-{index}"], include_queries=True),
            ]
        )
    result = _run(
        tmp_path / "initial",
        monkeypatch,
        initial,
        limits={"max_search_queries": 5, "max_web_actions": 11},
    )
    assert [event.type for event in result.events].count("web_search") == 5

    continuation = [
        _web("item.started", "continuation-search", action_type="search", queries=["gap"], include_queries=True),
        _web("item.completed", "continuation-search", action_type="search", queries=["gap"], include_queries=True),
        _web("item.completed", "page-1", action_type="open"),
        _web("item.completed", "page-2", action_type="open"),
        _web("item.completed", "page-3", action_type="open"),
    ]
    continuation_result = _run(
        tmp_path / "continuation",
        monkeypatch,
        continuation,
        limits={"max_search_queries": 1, "max_web_actions": 4},
    )
    assert [event.type for event in continuation_result.events].count("web_search") == 1


def test_batched_queries_charge_cardinality_once_and_stop_at_started(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    events = [
        _web("item.started", "batch-1", action_type="search", queries=["a", "b", "c"], include_queries=True),
        _web("item.completed", "batch-1", action_type="search", queries=["a", "b", "c"], include_queries=True),
    ]
    result = _run(
        tmp_path / "batch-ok",
        monkeypatch,
        events,
        limits={"max_search_queries": 3, "max_web_actions": 1},
    )
    assert len([event for event in result.events if event.type == "web_search"]) == 1

    with pytest.raises(ProviderError, match="search-query limit.*started"):
        _run(
            tmp_path / "batch-too-large",
            monkeypatch,
            [_web("item.started", "batch-2", action_type="search", queries=["a", "b"], include_queries=True)],
            limits={"max_search_queries": 1, "max_web_actions": 1},
        )


def test_duplicate_lifecycle_records_are_deduplicated_by_operation_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    events = [
        _web("item.started", "same", action_type="search", queries=["one"], include_queries=True),
        _web("item.completed", "same", action_type="search", queries=["one"], include_queries=True),
        _web("item.completed", "same", action_type="search", queries=["one"], include_queries=True),
    ]
    result = _run(
        tmp_path,
        monkeypatch,
        events,
        limits={"max_search_queries": 1, "max_web_actions": 1},
    )
    assert len([event for event in result.events if event.type == "web_search"]) == 1


def test_terminal_batch_metadata_reconciles_legacy_other_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    events = [
        _web("item.started", "legacy", action_type="other", queries=["a"], include_queries=True),
        _web("item.completed", "legacy", action_type="other", queries=["a", "b"], include_queries=True),
    ]
    with pytest.raises(ProviderError, match="search-query limit.*terminal"):
        _run(
            tmp_path,
            monkeypatch,
            events,
            limits={"max_search_queries": 1, "max_web_actions": 1},
        )


def test_ambiguous_event_shape_fails_closed_only_for_explicit_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ambiguous = [_web("item.completed", "ambiguous", action_type="other")]
    with pytest.raises(ProviderError, match="ambiguous web-search event"):
        _run(
            tmp_path / "explicit",
            monkeypatch,
            ambiguous,
            limits={"max_search_queries": 1, "max_web_actions": 1},
        )

    # Legacy callers keep the old aggregate-only behavior for a terminal web
    # item with no action metadata.
    result = _run(tmp_path / "legacy", monkeypatch, ambiguous)
    assert result.payload == {}


def test_opaque_other_event_is_ambiguous_under_explicit_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ProviderError, match="ambiguous web-search event"):
        _run(
            tmp_path,
            monkeypatch,
            [_web("item.started", "opaque", action_type="other")],
            limits={"max_search_queries": 5, "max_web_actions": 11},
        )


def test_live_cli_opaque_start_is_resolved_by_countable_terminal_search(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result = _run(
        tmp_path,
        monkeypatch,
        [
            {
                "type": "item.started",
                "item": {
                    "type": "WebSearch",
                    "id": "live-search",
                    "action": {"type": "other"},
                },
            },
            {
                "type": "item.completed",
                "item": {
                    "type": "WebSearch",
                    "id": "live-search",
                    "query": "site:example.test issuer",
                    "action": {"type": "search", "query": "site:example.test issuer"},
                },
            },
        ],
        limits={"max_search_queries": 1, "max_web_actions": 4},
    )
    assert len([event for event in result.events if event.type == "web_search"]) == 1


def test_opaque_other_completion_cannot_certify_an_explicit_query_bound(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ProviderError, match="opaque web-search lifecycle event"):
        _run(
            tmp_path,
            monkeypatch,
            [
                _web("item.started", "opaque-completion", action_type="other"),
                _web("item.completed", "opaque-completion", action_type="other"),
            ],
            limits={"max_search_queries": 1, "max_web_actions": 4},
        )


def test_legacy_other_and_uppercase_websearch_shapes_remain_accepted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    events = [
        _web("item.started", "other-1", action_type="other"),
        _web("item.completed", "other-1", action_type="other"),
        _web("item.completed", "upper-1", action_type="search", queries=["one", "two"], include_queries=True, nested_type="WebSearch"),
    ]
    result = _run(tmp_path, monkeypatch, events)
    assert len([event for event in result.events if event.type == "web_search"]) == 1


def test_legacy_other_keeps_search_counter_semantics(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(codex_module, "MAX_DISCOVERY_SEARCHES", 1)
    events: list[dict[str, Any]] = []
    for index in range(2):
        events.extend(
            [
                _web("item.started", f"other-{index}", action_type="other"),
                _web("item.completed", f"other-{index}", action_type="other"),
            ]
        )
    result = _run(tmp_path, monkeypatch, events)
    assert result.payload == {}


def _diagnostics(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (path / "discovery-accounting.jsonl").read_text().splitlines()]


@pytest.mark.parametrize("name", ["web_search", "websearch", "WebSearch"])
def test_direct_websearch_aliases_have_identical_explicit_accounting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    _run(tmp_path, monkeypatch, [
        {"type": name, "id": "direct", "action": {"type": "search", "queries": ["one", "two"]}},
        {"type": name, "id": "direct", "action": {"type": "search", "queries": ["one", "two"]}},
    ], limits={"max_search_queries": 2, "max_web_actions": 1})
    records = _diagnostics(tmp_path)
    assert records[-1]["search_queries"] == 2
    assert records[-1]["web_actions"] == 1
    assert records[1]["search_delta"] == 2
    assert records[2]["search_delta"] == 0
    assert records[1]["operation_id"] == records[2]["operation_id"]


@pytest.mark.parametrize("action_type", ["open", "find", "open_page", "find_in_page"])
def test_actionless_completion_inherits_nonsearch_operation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action_type: str) -> None:
    _run(tmp_path, monkeypatch, [
        _web("item.started", "page", action_type=action_type),
        _web("item.completed", "page"),
    ], limits={"max_search_queries": 0, "max_web_actions": 1})
    rows = _diagnostics(tmp_path)
    assert rows[-1]["search_queries"] == 0
    assert rows[2]["inherited_action"] is True
    assert rows[2]["search_delta"] == 0


def test_actionless_completion_reconciles_fresh_count_before_budget_guard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    completed = _web("item.completed", "search")
    completed["item"]["query_count"] = 3
    with pytest.raises(ProviderError, match="search-query limit.*terminal"):
        _run(tmp_path, monkeypatch, [
            _web("item.started", "search", action_type="search", queries=["one"], include_queries=True),
            completed,
        ], limits={"max_search_queries": 2, "max_web_actions": 1})
    rows = _diagnostics(tmp_path)
    assert rows[2]["search_delta"] == 2
    assert rows[2]["search_queries_after"] == 3
    assert rows[2]["outcome"] == "search_query_limit"
    assert rows[-1]["outcome"] == "capability"


@pytest.mark.parametrize("field", ["query_count", "num_queries", "number_of_queries"])
def test_explicit_count_wins_over_repeated_scalar_and_deduplicates_lifecycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str) -> None:
    start = _web("item.started", "counted", action_type="search")
    start["item"]["action"][field] = 3
    start["item"]["query"] = "first query only"
    complete = _web("item.completed", "counted", action_type="search", queries=["one", "two", "three"], include_queries=True)
    _run(tmp_path, monkeypatch, [start, complete, complete], limits={"max_search_queries": 3, "max_web_actions": 1})
    rows = _diagnostics(tmp_path)
    assert [row["search_delta"] for row in rows if row["type"] == "web_event"] == [3, 0, 0]
    assert rows[-1]["search_queries"] == 3


@pytest.mark.parametrize("bad_count", [None, True, -1, "2", {"private": "content"}])
def test_malformed_terminal_count_cannot_inherit_safe_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad_count: Any) -> None:
    completed = _web("item.completed", "search")
    completed["item"]["query_count"] = bad_count
    with pytest.raises(ProviderError, match="malformed discovery query cardinality"):
        _run(tmp_path, monkeypatch, [
            _web("item.started", "search", action_type="search", queries=["one"], include_queries=True),
            completed,
        ], limits={"max_search_queries": 1, "max_web_actions": 1})
    assert _diagnostics(tmp_path)[-1]["outcome"] == "capability"


def test_opaque_start_can_resolve_actionless_count_at_terminal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    completed = _web("item.completed", "search")
    completed["item"]["query_count"] = 2
    _run(tmp_path, monkeypatch, [
        _web("item.started", "search", action_type="other"), completed,
    ], limits={"max_search_queries": 2, "max_web_actions": 1})
    rows = _diagnostics(tmp_path)
    assert rows[1]["search_delta"] == 0
    assert rows[2]["search_delta"] == 2
    assert rows[-1]["pending_metadata"] == 0


def test_failed_diagnostics_retain_safe_shape_and_never_provider_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    private = "DO-NOT-RETAIN-private-command-or-query"
    event = _web("item.started", private, action_type="search", queries=[private] * 6, include_queries=True)
    event["item"]["reasoning"] = private
    event["command"] = private
    with pytest.raises(ProviderError, match="search-query limit"):
        _run(tmp_path, monkeypatch, [event], limits={"max_search_queries": 5, "max_web_actions": 11})
    log = tmp_path / "discovery-accounting.jsonl"
    assert private not in log.read_text()
    assert (log.stat().st_mode & 0o777) == 0o600
    rows = _diagnostics(tmp_path)
    assert rows[0]["max_search_queries"] == 5
    assert rows[1]["observed_search_queries"] == 6
    assert rows[1]["search_delta"] == 6
    assert rows[1]["search_queries_after"] == 6
    assert rows[-1]["search_queries"] == 6


def test_diagnostic_event_cap_preserves_final_totals_and_omission_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(codex_module, "MAX_DISCOVERY_DIAGNOSTIC_EVENTS", 3)
    event = _web("item.completed", "duplicate", action_type="search", queries=["one"], include_queries=True)
    _run(tmp_path, monkeypatch, [event] * 8, limits={"max_search_queries": 1, "max_web_actions": 1})
    rows = _diagnostics(tmp_path)
    assert len(rows) == 5  # limits, three event rows, final totals
    assert rows[-1]["observed_events"] == 8
    assert rows[-1]["omitted_events"] == 5
    assert rows[-1]["search_queries"] == 1


def test_unmatched_opaque_start_has_a_durable_failure_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ProviderError, match="opaque start"):
        _run(tmp_path, monkeypatch, [_web("item.started", "unfinished", action_type="other")],
             limits={"max_search_queries": 5, "max_web_actions": 11})
    rows = _diagnostics(tmp_path)
    assert rows[-1]["outcome"] == "capability"
    assert rows[-1]["pending_metadata"] == 1


def _empty_cli_other(event_type: str, identifier: str) -> dict[str, Any]:
    return {"type": event_type, "item": {"type": "web_search", "id": identifier,
                                         "query": "", "action": {"type": "other"}}}


def test_live_search_then_open_empty_sentinel_charges_one_query_two_actions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    search = _web("item.completed", "search", action_type="search", queries=["official result"], include_queries=True)
    search["item"]["query"] = "official result"
    opened = _empty_cli_other("item.completed", "open")
    _run(tmp_path, monkeypatch, [_empty_cli_other("item.started", "search"), search,
                                _empty_cli_other("item.started", "open"), opened, opened],
         limits={"max_search_queries": 1, "max_web_actions": 2})
    rows = _diagnostics(tmp_path)
    assert [row["search_delta"] for row in rows if row["type"] == "web_event"] == [0, 1, 0, 0, 0]
    assert rows[1]["awaiting_terminal_metadata"] is True
    assert rows[-1]["search_queries"] == 1 and rows[-1]["web_actions"] == 2
    assert rows[-1]["pending_metadata"] == 0
    assert rows[3]["query_shapes"] == {"item.query": "empty"}


def test_empty_start_batch_terminal_counts_every_query_even_with_empty_display_alias(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    terminal = _web("item.completed", "batch", action_type="search", queries=["a", "b", "c", "d"], include_queries=True)
    terminal["item"]["query"] = ""
    _run(tmp_path, monkeypatch, [_empty_cli_other("item.started", "batch"), terminal, terminal],
         limits={"max_search_queries": 4, "max_web_actions": 1})
    rows = _diagnostics(tmp_path)
    assert [row["search_delta"] for row in rows if row["type"] == "web_event"] == [0, 4, 0]
    assert rows[-1]["search_queries"] == 4

    with pytest.raises(ProviderError, match="search-query limit.*terminal"):
        _run(tmp_path / "over", monkeypatch, [_empty_cli_other("item.started", "batch"), terminal],
             limits={"max_search_queries": 3, "max_web_actions": 1})


def test_unfinished_empty_start_still_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ProviderError, match="opaque start"):
        _run(tmp_path, monkeypatch, [_empty_cli_other("item.started", "pending")],
             limits={"max_search_queries": 1, "max_web_actions": 2})
    assert _diagnostics(tmp_path)[-1]["pending_metadata"] == 1


@pytest.mark.parametrize("terminal", [
    {"type": "item.completed", "item": {"type": "web_search", "id": "pending", "action": {"type": "other"}}},
    {"type": "item.completed", "item": {"type": "web_search", "id": "pending", "query": None, "action": {"type": "other"}}},
    {"type": "item.completed", "item": {"type": "web_search", "id": "pending", "query": "", "action": {"type": "search"}}},
    {"type": "item.completed", "item": {"type": "web_search", "id": "pending", "query": "", "queries": None, "action": {"type": "other"}}},
])
def test_missing_malformed_or_empty_explicit_search_cannot_claim_nonsearch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, terminal: dict[str, Any]) -> None:
    with pytest.raises(ProviderError):
        _run(tmp_path, monkeypatch, [_empty_cli_other("item.started", "pending"), terminal],
             limits={"max_search_queries": 1, "max_web_actions": 2})
    assert _diagnostics(tmp_path)[-1]["outcome"] == "capability"


def test_empty_other_sentinel_is_only_accepted_in_observed_typed_envelope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ProviderError):
        _run(tmp_path, monkeypatch, [{"type": "web_search", "id": "direct", "query": "", "action": {"type": "other"}}],
             limits={"max_search_queries": 1, "max_web_actions": 1})


def _direct_cli_other(event_type: str, identifier: str, url: str = "https://investors.example.test/results") -> dict[str, Any]:
    event = _empty_cli_other(event_type, identifier)
    event["item"]["query"] = url
    return event


def test_live_direct_url_open_consumes_no_searches_and_deduplicates_terminal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    terminal = _direct_cli_other("item.completed", "open")
    _run(tmp_path, monkeypatch, [_empty_cli_other("item.started", "open"), terminal, terminal],
         limits={"max_search_queries": 0, "max_web_actions": 1})
    rows = _diagnostics(tmp_path)
    assert [row["search_delta"] for row in rows if row["type"] == "web_event"] == [0, 0, 0]
    assert rows[1]["awaiting_terminal_metadata"] is True
    assert rows[2]["query_shapes"] == {"item.query": "url"}
    assert rows[-1]["search_queries"] == 0 and rows[-1]["web_actions"] == 1
    assert rows[-1]["pending_metadata"] == 0


def test_six_queries_then_direct_url_open_stay_inside_original_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    events = []
    for identifier, queries in (("batch-a", ["a", "b", "c", "d"]), ("batch-b", ["e", "f"])):
        events.extend([_empty_cli_other("item.started", identifier),
                       _web("item.completed", identifier, action_type="search", queries=queries, include_queries=True)])
    events.extend([_empty_cli_other("item.started", "open"), _direct_cli_other("item.completed", "open")])
    _run(tmp_path, monkeypatch, events, limits={"max_search_queries": 6, "max_web_actions": 12})
    assert _diagnostics(tmp_path)[-1]["search_queries"] == 6
    assert _diagnostics(tmp_path)[-1]["web_actions"] == 3


def test_explicit_search_with_url_is_still_a_search(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    terminal = _direct_cli_other("item.completed", "search")
    terminal["item"]["action"] = {"type": "search", "query": terminal["item"]["query"]}
    with pytest.raises(ProviderError, match="search-query limit.*terminal"):
        _run(tmp_path, monkeypatch, [_empty_cli_other("item.started", "search"), terminal],
             limits={"max_search_queries": 0, "max_web_actions": 1})
    assert _diagnostics(tmp_path)[-1]["search_queries"] == 1


@pytest.mark.parametrize("url", ["https://", "https://example.test bad", "https://example.test:invalid/path", "https://user:password@example.test/path", "file:///private/path", "find official results"])
def test_malformed_url_or_search_text_cannot_claim_zero_queries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    with pytest.raises(ProviderError):
        _run(tmp_path, monkeypatch, [_empty_cli_other("item.started", "unknown"), _direct_cli_other("item.completed", "unknown", url)],
             limits={"max_search_queries": 0, "max_web_actions": 1})


def test_direct_url_display_cannot_hide_additional_query_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    terminal = _direct_cli_other("item.completed", "search")
    terminal["item"]["query_count"] = 3
    with pytest.raises(ProviderError, match="search-query limit"):
        _run(tmp_path, monkeypatch, [_empty_cli_other("item.started", "search"), terminal],
             limits={"max_search_queries": 2, "max_web_actions": 1})
    assert _diagnostics(tmp_path)[-1]["search_queries"] == 3
