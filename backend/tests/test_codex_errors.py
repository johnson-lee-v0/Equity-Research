"""CLI failures remain actionable without retaining provider content."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

import backend.app.providers.codex as codex_module
from backend.app.config import Settings
from backend.app.providers.base import ProviderError
from backend.app.providers.codex import CodexAdapter
from backend.app.schemas import ModelConfig


def run_cli_result(tmp_path, monkeypatch, *, events=(), stderr="", returncode=1, result=None, observed=None):
    class Stdin:
        def write(self, data): pass
        async def drain(self): pass
        def close(self): pass

    class Stream:
        def __init__(self, lines=(), content=b""):
            self.lines = iter(lines)
            self.content = content
        async def readline(self): return next(self.lines, b"")
        async def read(self): return self.content

    class Process:
        def __init__(self):
            self.stdin = Stdin()
            self.stdout = Stream([(json.dumps(event) + "\n").encode() for event in events])
            self.stderr = Stream(content=stderr.encode())
            self.returncode = None
        async def wait(self):
            if self.returncode is None: self.returncode = returncode
            return self.returncode
        def terminate(self): self.returncode = -15
        def kill(self): self.returncode = -9

    async def create_process(*args, **kwargs): return Process()

    monkeypatch.setattr(codex_module.asyncio, "create_subprocess_exec", create_process)
    adapter = CodexAdapter(Settings(project_root=tmp_path, data_dir=tmp_path))
    monkeypatch.setattr(adapter, "_binary_path", lambda: "/test/codex")
    schema_path, result_path = tmp_path / "schema.json", tmp_path / "result.json"
    schema_path.write_text("{}")
    if result is not None: result_path.write_text(json.dumps(result))
    config = ModelConfig(provider="codex", model="gpt-5.6-luna", reasoning_effort="max", profile="researcher")
    return asyncio.run(adapter._execute_raw(
        "test-attempt", "private input", config, schema_path, result_path, tmp_path,
        observed.append if observed is not None else None,
    ))


def test_cli_character_limit_is_blocking_and_not_retried(tmp_path, monkeypatch):
    # Actual CLI turn/start failure shape: no error JSON event and exit 1.
    stderr = ('Error: turn/start: turn/start failed: Input exceeds the maximum length of 1048576 characters. '
              '(code -32602), data: {"input_error_code":"input_too_large","max_chars":1048576,"actual_chars":1768266}')
    with pytest.raises(ProviderError) as caught:
        run_cli_result(tmp_path, monkeypatch, events=[{"type": "thread.started"}], stderr=stderr)
    assert caught.value.kind == "context_limit"
    assert not caught.value.retryable
    assert "CLI input size limit" in caught.value.message


@pytest.mark.parametrize("error", [
    {"message": "Your input exceeds the context window of this model."},
    {"code": "context_length_exceeded"},
])
def test_nested_turn_failed_error_is_classified_and_never_persisted(tmp_path, monkeypatch, error):
    observed = []
    with pytest.raises(ProviderError) as caught:
        run_cli_result(tmp_path, monkeypatch, events=[{"type": "turn.failed", "error": error}], observed=observed)
    assert caught.value.kind == "context_limit"
    assert not caught.value.retryable
    assert len(observed) == 1
    assert observed[0].type == "turn.failed"
    assert observed[0].message is None


@pytest.mark.parametrize("event", [
    {"type": "error", "message": "Usage limit reached for subscription."},
    {"type": "item.completed", "item": {"type": "error", "message": "Usage limit reached for subscription."}},
    {"type": "turn.failed", "error": "Usage limit reached for subscription."},
])
def test_older_explicit_error_shapes_remain_classified(tmp_path, monkeypatch, event):
    with pytest.raises(ProviderError) as caught:
        run_cli_result(tmp_path, monkeypatch, events=[event])
    assert caught.value.kind == "quota"


def test_terminal_stderr_error_after_verbose_startup_is_retained(tmp_path, monkeypatch):
    stderr = "Startup diagnostic.\n" * 500 + "Error: input_too_large"
    with pytest.raises(ProviderError) as caught:
        run_cli_result(tmp_path, monkeypatch, stderr=stderr)
    assert caught.value.kind == "context_limit"


def test_missing_artifact_also_classifies_stderr_on_zero_exit(tmp_path, monkeypatch):
    with pytest.raises(ProviderError) as caught:
        run_cli_result(tmp_path, monkeypatch, stderr="invalid_json_schema", returncode=0)
    assert caught.value.kind == "capability"


@pytest.mark.parametrize("error", [
    "The model is not supported when using Codex with a ChatGPT account.",
    "Model metadata not found for gpt-6-luna.",
])
def test_old_cli_model_mismatch_is_not_retried_as_a_transient_failure(tmp_path, monkeypatch, error):
    with pytest.raises(ProviderError) as caught:
        run_cli_result(tmp_path, monkeypatch, stderr=error)
    assert caught.value.kind == "capability"
    assert not caught.value.retryable


def test_failed_turn_cannot_accept_an_existing_artifact(tmp_path, monkeypatch):
    with pytest.raises(ProviderError) as caught:
        run_cli_result(tmp_path, monkeypatch, events=[{"type": "turn.failed", "error": {"message": "input_too_large"}}], returncode=0, result={"stale": True})
    assert caught.value.kind == "context_limit"


def test_recovered_error_and_nonobject_event_do_not_discard_success(tmp_path, monkeypatch):
    result = run_cli_result(tmp_path, monkeypatch, events=[
        [], {"type": "error", "message": "Reconnecting after transient network failure"},
        {"type": "turn.completed", "usage": {"input_tokens": 10}},
    ], returncode=0, result={"complete": True})
    assert result.payload == {"complete": True}
    assert result.usage == {"input_tokens": 10}


def test_unclassified_failure_does_not_expose_private_text(tmp_path, monkeypatch):
    with pytest.raises(ProviderError) as caught:
        run_cli_result(tmp_path, monkeypatch, events=[{"type": "turn.failed", "error": {"message": "Private account details should never be copied to the UI"}}])
    assert caught.value.kind == "execution"
    assert "Private account" not in caught.value.message
