from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from backend.app.research.laya_runtime import (
    LAYA_MODEL_REVISION,
    LAYA_SOURCE_REVISION,
    LayaRuntime,
    LayaRuntimeConfig,
    validate_request,
)
from backend.app.research.laya_worker import _preflight


def _questions() -> dict[str, dict[str, object]]:
    return {
        "opportunity": {"type": "choice", "instructions": "Is the opportunity attractive?", "criteria": ["yes", "no"]},
        "valuation": {"type": "score", "instructions": "How good is valuation?", "criteria": ["poor", "fair", "good"]},
        "catalyst": {"type": "noul", "instructions": "Is a catalyst stated?"},
        "downside": {"type": "choice", "instructions": "Is downside controlled?", "criteria": ["yes", "no"]},
        "portfolio_action": {"type": "choice", "instructions": "What action is supported?", "criteria": ["watch", "avoid"]},
    }


def _fake_worker(
    tmp_path: Path,
    *,
    partial: bool = False,
    invalid_output: bool = False,
    invalid_answer: str | None = None,
    no_read: bool = False,
    malformed_elapsed: bool = False,
) -> Path:
    worker = tmp_path / "fake_worker.py"
    worker.write_text(
        textwrap.dedent(
            f"""
            import json, os, sys, time
            print(json.dumps({{"event":"ready","revision":{LAYA_MODEL_REVISION!r},"source_revision":{LAYA_SOURCE_REVISION!r}}}), flush=True)
            if {no_read!r}:
                time.sleep(3)
                raise SystemExit(0)
            calls = 0
            def make_answer(q, call):
                kind = q["type"]
                criteria = q.get("criteria")
                if kind == "choice":
                    labels = list(criteria.keys()) if isinstance(criteria, dict) else list(criteria)
                    answer = {{"type":"choice", "choice":labels[0], "probabilities":{{label: 1.0 / len(labels) for label in labels}}, "confidence":0.5, "action":{{"act_probability":0.5}}}}
                elif kind == "score":
                    labels = list(criteria)
                    answer = {{"type":"score", "score":0.0, "legend":{{str(i): label for i, label in enumerate(labels)}}, "probabilities":{{str(i): 1.0 / len(labels) for i in range(len(labels))}}, "confidence":0.5, "action":{{"act_probability":0.5}}}}
                else:
                    answer = {{"type":"noul", "noul":0.5, "confidence":0.5, "action":{{"act_probability":0.5}}}}
                answer["call"] = call
                answer["hf_token_seen"] = bool(os.environ.get("HF_TOKEN"))
                if {invalid_answer!r} == "choice" and kind == "choice":
                    answer["choice"] = "not-a-declared-choice"
                if {invalid_answer!r} == "confidence":
                    answer["confidence"] = 99.0
                return answer
            for raw in sys.stdin:
                request = json.loads(raw)
                calls += 1
                if request.get("state") == "slow":
                    sys.stdout.write("{{\\"id\\":\\"partial\\"")
                    sys.stdout.flush()
                    time.sleep(3)
                    continue
                print(json.dumps({{
                    "id": request["id"],
                    "status": "ok",
                    "answers": {{}} if {invalid_output!r} else {{qid: make_answer(question, calls) for qid, question in request["questions"].items()}},
                    "elapsed_ms": "malformed" if {malformed_elapsed!r} else 12.5,
                    "token_counts": {{"input": 4, "output": 0, "limit": 512, "overflow": False}},
                }}), flush=True)
            """
        ),
        encoding="utf-8",
    )
    return worker


def _runtime(tmp_path: Path, worker: Path, *, timeout: float = 1.0, max_state_chars: int = 24_000) -> LayaRuntime:
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    return LayaRuntime(
        LayaRuntimeConfig(
            project_root=tmp_path,
            python_executable=Path(sys.executable),
            worker_path=worker,
            model_dir=model_dir,
            call_timeout_seconds=timeout,
            startup_timeout_seconds=timeout,
            max_state_chars=max_state_chars,
        )
    )


class _BudgetTokenizer:
    mask_token = "[MASK]"
    mask_token_id = 0

    def __init__(self, *, state_tokens: int = 4, option_tokens: int = 1, instruction_tokens: int = 2) -> None:
        self.state_tokens = state_tokens
        self.option_tokens = option_tokens
        self.instruction_tokens = instruction_tokens

    def __call__(self, text: str, *, add_special_tokens: bool) -> dict[str, list[int]]:
        if text.startswith("choice question:"):
            count = self.instruction_tokens
        elif text.startswith(" "):
            count = self.option_tokens
        else:
            count = self.state_tokens
        return {"input_ids": list(range(count))}


class _BudgetAgent:
    def __init__(self, tokenizer: _BudgetTokenizer, *, max_len: int = 512, head_max_len: int = 192) -> None:
        self.tok = tokenizer
        self.cfg = {"max_len": max_len, "head_max_len": head_max_len}


def _preflight_question(criteria: object | None = None) -> dict[str, dict[str, object]]:
    if criteria is None:
        criteria = ["yes", "no"]
    return {"q": {"type": "choice", "instructions": "short", "criteria": criteria}}


def test_validate_request_keeps_five_question_contract_and_rejects_non_text_state() -> None:
    assert validate_request("compact state", _questions()) is None
    too_many = dict(_questions())
    too_many["sixth"] = {"type": "noul", "instructions": "Another question"}
    assert validate_request("compact state", too_many)["code"] == "question_count"
    assert validate_request({"state": "not compact"}, _questions())["code"] == "state_type"


def test_worker_is_reused_and_hf_credentials_are_not_inherited(tmp_path: Path, monkeypatch) -> None:
    worker = _fake_worker(tmp_path)
    runtime = _runtime(tmp_path, worker)
    monkeypatch.setenv("HF_TOKEN", "synthetic-secret-must-not-cross-process")
    try:
        first = runtime.classify("first state", _questions())
        second = runtime.classify("second state", _questions())
    finally:
        runtime.close()

    assert first["status"] == "ok"
    assert first["answers"]["opportunity"]["call"] == 1
    assert first["answers"]["opportunity"]["hf_token_seen"] is False
    assert second["answers"]["opportunity"]["call"] == 2
    assert second["answers"]["opportunity"]["hf_token_seen"] is False
    assert first["elapsed_ms"] >= 0
    assert first["token_counts"] == {"input": 4, "output": 0, "limit": 512, "overflow": False}


def test_partial_worker_line_is_bounded_by_timeout(tmp_path: Path) -> None:
    worker = _fake_worker(tmp_path, partial=True)
    runtime = _runtime(tmp_path, worker, timeout=0.08)
    started = time.monotonic()
    try:
        result = runtime.classify("slow", _questions())
    finally:
        runtime.close()
    elapsed = time.monotonic() - started

    assert result["status"] == "unavailable"
    assert result["reason"] == "worker_timeout"
    assert result["timed_out"] is True
    assert elapsed < 1.0


def test_state_overflow_is_rejected_before_worker_start(tmp_path: Path) -> None:
    # A nonexistent executable proves the bound is checked before process
    # startup; no optional dependency is needed for this path.
    worker = tmp_path / "missing-worker.py"
    runtime = _runtime(tmp_path, worker, max_state_chars=10)
    result = runtime.classify("12345678901", _questions())

    assert result["status"] == "unavailable"
    assert result["overflow"] == {"kind": "state_chars", "observed": 11, "limit": 10}
    assert result["token_counts"]["overflow"] is True


def test_malformed_ok_response_is_unavailable(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, _fake_worker(tmp_path, invalid_output=True))
    try:
        result = runtime.classify("compact state", _questions())
    finally:
        runtime.close()

    assert result["status"] == "unavailable"
    assert result["reason"] == "worker_output_invalid"
    assert result["answers"] == {}


def test_malformed_elapsed_metadata_is_unavailable(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, _fake_worker(tmp_path, malformed_elapsed=True))
    try:
        result = runtime.classify("compact state", _questions())
    finally:
        runtime.close()

    assert result["status"] == "unavailable"
    assert result["reason"] == "worker_output_invalid"
    assert result["answers"] == {}


def test_tokenizer_preflight_rejects_state_overflow() -> None:
    # fixed sequence is 10 tokens, leaving 502 of the 512-token context.
    tokens, overflow = _preflight(_BudgetAgent(_BudgetTokenizer(state_tokens=504)), "STATE", _preflight_question())

    assert tokens == 0
    assert overflow == {"kind": "state_tokens", "question": "q", "observed": 504, "limit": 502}


def test_tokenizer_preflight_rejects_single_option_overflow() -> None:
    tokens, overflow = _preflight(_BudgetAgent(_BudgetTokenizer(option_tokens=49)), "STATE", _preflight_question())

    assert tokens == 0
    assert overflow == {"kind": "option_tokens", "question": "q", "observed": 49, "limit": 48}


def test_tokenizer_preflight_rejects_instruction_overflow() -> None:
    tokens, overflow = _preflight(_BudgetAgent(_BudgetTokenizer(instruction_tokens=190)), "STATE", _preflight_question())

    assert tokens == 0
    assert overflow == {"kind": "instruction_tokens", "question": "q", "observed": 190, "limit": 188}


def test_tokenizer_preflight_rejects_combined_option_overflow() -> None:
    criteria = [f"option-{index}" for index in range(5)]
    tokens, overflow = _preflight(
        _BudgetAgent(_BudgetTokenizer(option_tokens=40)),
        "STATE",
        _preflight_question(criteria),
    )

    assert tokens == 0
    assert overflow == {"kind": "option_head_tokens", "question": "q", "observed": 205, "limit": 176}


def test_pinned_tokenizer_preflight_rejects_each_real_context_overflow() -> None:
    """Exercise preflight with the installed checkpoint tokenizer itself."""

    project_root = Path(__file__).resolve().parents[2]
    runtime_python = project_root / ".runtime" / "laya" / "venv" / "bin" / "python"
    model_tokenizer = project_root / ".runtime" / "laya" / "model" / "tokenizer"
    if not runtime_python.is_file() or not model_tokenizer.is_dir():
        pytest.skip("pinned Laya tokenizer is not installed")
    script = r'''
import json
from pathlib import Path
from transformers import AutoTokenizer
from backend.app.research.laya_worker import _preflight

class Agent:
    def __init__(self, tok):
        self.tok = tok
        self.cfg = {"max_len": 512, "head_max_len": 192}

def question(criteria=("yes", "no"), instructions="short"):
    return {"q": {"type": "choice", "instructions": instructions, "criteria": list(criteria)}}

tok = AutoTokenizer.from_pretrained(Path("TOKENIZER"), local_files_only=True)
agent = Agent(tok)
cases = {
    "state_tokens": _preflight(agent, "state " * 1000, question())[1],
    "option_tokens": _preflight(agent, "state", question(("token " * 60, "no")))[1],
    "instruction_tokens": _preflight(agent, "state", question(instructions="instruction " * 260))[1],
    "option_head_tokens": _preflight(agent, "state", question(tuple(f"option{i} " + "token " * 39 for i in range(5))))[1],
}
print(json.dumps({name: value["kind"] if value else None for name, value in cases.items()}))
'''.replace("TOKENIZER", str(model_tokenizer).replace("\\", "\\\\").replace('"', '\\"'))
    environment = os.environ.copy()
    environment.update({"PYTHONPATH": str(project_root), "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})
    for name in ("HF_TOKEN", "HUGGINGFACE_HUB_TOKEN", "HUGGINGFACE_TOKEN", "HF_ACCESS_TOKEN", "HF_API_TOKEN"):
        environment.pop(name, None)
    completed = subprocess.run(
        [str(runtime_python), "-c", script],
        cwd=project_root,
        env=environment,
        text=True,
        capture_output=True,
        timeout=30,
        check=True,
    )
    assert json.loads(completed.stdout.strip()) == {
        "state_tokens": "state_tokens",
        "option_tokens": "option_tokens",
        "instruction_tokens": "instruction_tokens",
        "option_head_tokens": "option_head_tokens",
    }


def test_choice_outside_declared_options_is_unavailable(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, _fake_worker(tmp_path, invalid_answer="choice"))
    try:
        result = runtime.classify("compact state", _questions())
    finally:
        runtime.close()

    assert result["status"] == "unavailable"
    assert result["reason"] == "worker_output_invalid"
    assert result["answers"] == {}


def test_nonfinite_or_out_of_range_answer_numeric_is_unavailable(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, _fake_worker(tmp_path, invalid_answer="confidence"))
    try:
        result = runtime.classify("compact state", _questions())
    finally:
        runtime.close()

    assert result["status"] == "unavailable"
    assert result["reason"] == "worker_output_invalid"
    assert result["answers"] == {}


def test_request_write_obeys_call_deadline_when_worker_does_not_read(tmp_path: Path) -> None:
    questions = _questions()
    questions["opportunity"] = {
        "type": "choice",
        "instructions": "Choose one.",
        "criteria": {f"option-{index}": "x" * 7_000 for index in range(32)},
    }
    runtime = _runtime(tmp_path, _fake_worker(tmp_path, no_read=True), timeout=0.08)
    started = time.monotonic()
    try:
        result = runtime.classify("compact state", questions)
    finally:
        runtime.close()
    elapsed = time.monotonic() - started

    assert result["status"] == "unavailable"
    assert result["reason"] == "worker_write_timeout"
    assert result["timed_out"] is True
    assert elapsed < 1.0


def test_serialized_request_limit_is_checked_before_worker_start(tmp_path: Path) -> None:
    questions = _questions()
    questions["opportunity"] = {
        "type": "choice",
        "instructions": "Choose one.",
        "criteria": {f"option-{index}": "x" * 9_000 for index in range(32)},
    }
    runtime = _runtime(tmp_path, tmp_path / "missing-worker.py")
    result = runtime.classify("compact state", questions)

    assert result["status"] == "unavailable"
    assert result["reason"] == "request_too_large"
    assert result["request_bytes"] > result["request_limit_bytes"]
