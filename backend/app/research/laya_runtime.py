"""Bounded local adapter for the optional Laya decision runtime.

The application deliberately does not import torch, transformers, or laya.  A
small line-oriented worker process owns those dependencies and remains offline
after installation.  This keeps the normal backend import path lightweight and
turns missing optional runtime assets into an explicit, inspectable result.
"""

from __future__ import annotations

import atexit
import json
import math
import os
import selectors
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence


LAYA_SOURCE_REVISION = "573e5b62696ba441230cd6be71d593331b5d23af"
LAYA_MODEL_ID = "convaiinnovations/laya"
LAYA_MODEL_REVISION = "1c5edc17a7acd8701df6fc341c0d179f1c62c982"
LAYA_MODEL_LABEL = "laya-rl-agent"
LAYA_MODEL_SHA256 = "891102d372688fc2a094dac56a384bc537b87c63f21f9f3dac0be2b7cbc8d86c"
LAYA_FILE_SHA256 = {
    "rl_agent_config.json": "ae287b56bbcf5f8c4f4541ae9dfd00c914c4c48b940b8398c3058af37ba92bbd",
    "encoder/config.json": "bf3ab80598fdccf414855a2ce80f22859e4492d06ca8a62ddd1cfb63972f8979",
    "tokenizer/tokenizer.json": "6c8aaa9a542084f2457eab775d4eeb51f92a70c0fd9de28d5edb0ddec3c08d30",
    "tokenizer/tokenizer_config.json": "50044de60daaa73df97d262e15a40d4faf0160e7d742df64b377877a1320dd12",
    "model.safetensors": LAYA_MODEL_SHA256,
}
LAYA_MANIFEST_FILENAME = "laya_runtime_manifest.json"
DEFAULT_MAX_QUESTIONS = 5
DEFAULT_MAX_STATE_CHARS = 24_000
DEFAULT_CALL_TIMEOUT_SECONDS = 45.0
DEFAULT_STARTUP_TIMEOUT_SECONDS = 180.0
MAX_WORKER_LINE_BYTES = 4 * 1024 * 1024
MAX_SERIALIZED_REQUEST_BYTES = 256_000

# The worker accepts only the root English checkpoint files downloaded by the
# installer.  These values are also written into the worker response so a
# caller cannot mistake an unpinned local directory for the approved model.
LAYA_ALLOWED_MODEL_FILES = frozenset(
    {
        "rl_agent_config.json",
        "model.safetensors",
        "encoder/config.json",
        "tokenizer/tokenizer.json",
        "tokenizer/tokenizer_config.json",
        LAYA_MANIFEST_FILENAME,
    }
)

_QUESTION_TYPES = frozenset({"choice", "score", "noul"})
_REQUIRED_QUESTION_KEYS = frozenset({"type", "instructions"})
_CREDENTIAL_ENV_NAMES = frozenset(
    {
        "HF_TOKEN",
        "HUGGINGFACE_HUB_TOKEN",
        "HUGGINGFACE_TOKEN",
        "HF_ACCESS_TOKEN",
        "HF_API_TOKEN",
    }
)
_WORKER_ENV_BLOCKLIST = _CREDENTIAL_ENV_NAMES | frozenset({"PYTHONPATH", "PYTHONHOME"})


@dataclass(frozen=True)
class LayaRuntimeConfig:
    """Paths and bounds for one local worker.

    The defaults point at the repository's ignored ``.runtime/laya`` folder.
    Tests and maintenance tools may provide an isolated directory explicitly.
    """

    project_root: Path = field(default_factory=lambda: Path(__file__).resolve().parents[3])
    python_executable: Optional[Path] = None
    worker_path: Optional[Path] = None
    model_dir: Optional[Path] = None
    model_revision: str = LAYA_MODEL_REVISION
    source_revision: str = LAYA_SOURCE_REVISION
    # MPS is opt-in: this checkpoint's SDPA path can abort the interpreter on
    # some macOS builds for batched typed decisions, while CPU is bounded and
    # deterministic.  Callers may explicitly choose MPS after validating it.
    device: str = "cpu"
    call_timeout_seconds: float = DEFAULT_CALL_TIMEOUT_SECONDS
    startup_timeout_seconds: float = DEFAULT_STARTUP_TIMEOUT_SECONDS
    max_questions: int = DEFAULT_MAX_QUESTIONS
    max_state_chars: int = DEFAULT_MAX_STATE_CHARS

    def resolved_python(self) -> Path:
        if self.python_executable is not None:
            return Path(self.python_executable)
        configured = os.environ.get("ROAD2M_LAYA_PYTHON")
        if configured:
            return Path(configured).expanduser()
        return self.project_root / ".runtime" / "laya" / "venv" / "bin" / "python"

    def resolved_worker(self) -> Path:
        if self.worker_path is not None:
            return Path(self.worker_path)
        configured = os.environ.get("ROAD2M_LAYA_WORKER")
        if configured:
            return Path(configured).expanduser()
        return self.project_root / "backend" / "app" / "research" / "laya_worker.py"

    def resolved_model_dir(self) -> Path:
        if self.model_dir is not None:
            return Path(self.model_dir).expanduser()
        configured = os.environ.get("ROAD2M_LAYA_MODEL_DIR")
        if configured:
            return Path(configured).expanduser()
        return self.project_root / ".runtime" / "laya" / "model"


def _metadata(config: LayaRuntimeConfig) -> dict[str, Any]:
    return {
        "model": LAYA_MODEL_ID,
        "model_label": LAYA_MODEL_LABEL,
        "revision": config.model_revision,
        "source_revision": config.source_revision,
    }


def _base_result(config: LayaRuntimeConfig, *, status: str, started: float) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": status,
        **_metadata(config),
        "answers": {},
        "elapsed_ms": round(max(0.0, (time.monotonic() - started) * 1000.0), 3),
        "token_counts": {
            "input": None,
            "output": 0,
            "limit": None,
            "overflow": False,
        },
        "overflow": None,
        "reason": None,
    }
    # A stable usage-shaped alias makes this result convenient beside other
    # research artifacts without changing the explicit token_counts contract.
    result["usage"] = {"input_tokens": None, "output_tokens": 0}
    return result


def _invalid_result(
    config: LayaRuntimeConfig,
    started: float,
    *,
    code: str,
    reason: str,
    overflow: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    # Oversize input is unavailable for this bounded model.  It must not look
    # like a negative investment decision or a successful classification.
    status = "unavailable" if overflow is not None else "invalid_input"
    result = _base_result(config, status=status, started=started)
    result["reason"] = "input_overflow" if overflow is not None else code
    result["error_code"] = code
    result["detail"] = reason
    result["overflow"] = overflow
    result["token_counts"]["overflow"] = overflow is not None
    return result


def _validate_questions(
    questions: Any,
    *,
    max_questions: int,
) -> Optional[tuple[str, dict[str, dict[str, Any]]] | dict[str, str]]:
    if not isinstance(questions, Mapping):
        return {"code": "questions_not_mapping", "detail": "questions must be a mapping"}
    if not questions:
        return {"code": "questions_empty", "detail": "at least one question is required"}
    if len(questions) > max_questions:
        return {"code": "question_count", "detail": f"at most {max_questions} questions are allowed"}

    normalized: dict[str, dict[str, Any]] = {}
    for question_id, raw in questions.items():
        if not isinstance(question_id, str) or not question_id or len(question_id) > 80:
            return {"code": "question_id", "detail": "question IDs must be short non-empty strings"}
        if not isinstance(raw, Mapping):
            return {"code": "question_definition", "detail": f"question {question_id!r} is not a mapping"}
        missing = _REQUIRED_QUESTION_KEYS.difference(raw.keys())
        if missing:
            return {"code": "question_definition", "detail": f"question {question_id!r} is missing a required field"}
        qtype = raw.get("type")
        if qtype not in _QUESTION_TYPES:
            return {"code": "question_type", "detail": f"question {question_id!r} has an unsupported type"}
        instructions = raw.get("instructions")
        if not isinstance(instructions, str) or not instructions.strip():
            return {"code": "question_instructions", "detail": f"question {question_id!r} needs text instructions"}
        if len(instructions) > 8_000:
            return {"code": "question_instructions", "detail": f"question {question_id!r} instructions are too long"}

        q = {"type": qtype, "instructions": instructions}
        criteria = raw.get("criteria")
        if qtype == "choice":
            if isinstance(criteria, Mapping):
                if not criteria or len(criteria) > 32:
                    return {"code": "question_criteria", "detail": f"question {question_id!r} needs 1-32 choice options"}
                # Keys become the published answer labels.  Keep values JSON
                # serializable because Laya renders structured criteria.
                if any(not isinstance(key, str) or not key for key in criteria):
                    return {"code": "question_criteria", "detail": f"question {question_id!r} has an invalid choice label"}
                q["criteria"] = dict(criteria)
            elif isinstance(criteria, Sequence) and not isinstance(criteria, (str, bytes, bytearray)):
                if (
                    not criteria
                    or len(criteria) > 32
                    or any(not isinstance(value, str) or not value for value in criteria)
                    or len(set(criteria)) != len(criteria)
                ):
                    return {"code": "question_criteria", "detail": f"question {question_id!r} needs 1-32 choice labels"}
                q["criteria"] = list(criteria)
            else:
                return {"code": "question_criteria", "detail": f"question {question_id!r} needs choice criteria"}
        elif qtype == "score":
            if not isinstance(criteria, Sequence) or isinstance(criteria, (str, bytes, bytearray)):
                return {"code": "question_criteria", "detail": f"question {question_id!r} needs score criteria"}
            if not criteria or len(criteria) > 32:
                return {"code": "question_criteria", "detail": f"question {question_id!r} needs 1-32 score levels"}
            q["criteria"] = list(criteria)
        else:
            if criteria is not None and not isinstance(criteria, Mapping):
                return {"code": "question_criteria", "detail": f"question {question_id!r} has invalid noul criteria"}
            if criteria is not None:
                q["criteria"] = dict(criteria)
        try:
            json.dumps(q, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError, OverflowError):
            return {"code": "question_json", "detail": f"question {question_id!r} contains a non-serializable value"}
        normalized[question_id] = q
    return "ok", normalized


def validate_request(
    state: Any,
    questions: Any,
    *,
    max_questions: int = DEFAULT_MAX_QUESTIONS,
    max_state_chars: int = DEFAULT_MAX_STATE_CHARS,
) -> Optional[dict[str, str]]:
    """Validate the public request without importing optional ML packages."""

    if not isinstance(state, str):
        return {"code": "state_type", "detail": "state must be a compact string"}
    if not state.strip():
        return {"code": "state_empty", "detail": "state must not be empty"}
    if len(state) > max_state_chars:
        return {"code": "state_chars", "detail": f"state exceeds the {max_state_chars}-character bound"}
    validated = _validate_questions(questions, max_questions=max_questions)
    if isinstance(validated, dict):
        return validated
    return None


def _finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(float(value))
    except (OverflowError, TypeError, ValueError):
        return False


def _probability_map(value: Any, expected_keys: Sequence[str]) -> bool:
    if not isinstance(value, Mapping) or set(value) != set(expected_keys):
        return False
    probabilities = []
    for key in expected_keys:
        probability = value.get(key)
        if not _finite_number(probability) or not 0.0 <= float(probability) <= 1.0:
            return False
        probabilities.append(float(probability))
    return abs(sum(probabilities) - 1.0) <= 0.002


def _action_is_valid(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    probability = value.get("act_probability")
    return _finite_number(probability) and 0.0 <= float(probability) <= 1.0


def _answer_is_valid(answer: Any, question: Mapping[str, Any]) -> bool:
    if not isinstance(answer, Mapping) or answer.get("type") != question.get("type"):
        return False
    if not _action_is_valid(answer.get("action")):
        return False
    confidence = answer.get("confidence")
    if not _finite_number(confidence) or not 0.0 <= float(confidence) <= 1.0:
        return False
    question_type = question.get("type")
    criteria = question.get("criteria")
    if question_type == "choice":
        labels = list(criteria.keys()) if isinstance(criteria, Mapping) else list(criteria or [])
        choice = answer.get("choice")
        return choice in labels and _probability_map(answer.get("probabilities"), labels)
    if question_type == "score":
        levels = list(criteria or [])
        keys = [str(index) for index in range(len(levels))]
        score = answer.get("score")
        legend = answer.get("legend")
        if not _finite_number(score) or not 0.0 <= float(score) <= max(0, len(levels) - 1):
            return False
        if not isinstance(legend, Mapping) or set(legend) != set(keys):
            return False
        if any(legend[key] != levels[int(key)] for key in keys):
            return False
        return _probability_map(answer.get("probabilities"), keys)
    if question_type == "noul":
        noul = answer.get("noul")
        return _finite_number(noul) and 0.0 <= float(noul) <= 1.0
    return False


def _answers_are_valid(answers: Any, questions: Mapping[str, Mapping[str, Any]]) -> bool:
    return isinstance(answers, Mapping) and set(answers) == set(questions) and all(
        _answer_is_valid(answers[question_id], questions[question_id]) for question_id in questions
    )


def _elapsed_is_valid(value: Any) -> bool:
    if not _finite_number(value):
        return False
    return float(value) >= 0.0


def _token_counts_are_valid(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    if value.get("overflow") is not False:
        return False
    for key in ("input", "output", "limit"):
        number = value.get(key)
        if isinstance(number, bool) or not isinstance(number, int) or number < 0:
            return False
    return value.get("limit") == 512


class LayaRuntime:
    """Serialized, reusable controller for the isolated Laya worker."""

    def __init__(self, config: Optional[LayaRuntimeConfig] = None) -> None:
        self.config = config or LayaRuntimeConfig()
        self._process: Optional[subprocess.Popen[str]] = None
        self._lock = threading.RLock()
        self._request_counter = 0
        self._ready: Optional[dict[str, Any]] = None
        self._stdout_buffer = bytearray()

    def close(self) -> None:
        with self._lock:
            process = self._process
            self._process = None
            self._ready = None
            self._stdout_buffer.clear()
            if process is None:
                return
            try:
                process.stdin.close()
            except Exception:
                pass
            try:
                process.terminate()
                process.wait(timeout=1.0)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass

    def _clear_process(self) -> None:
        process = self._process
        self._process = None
        self._ready = None
        self._stdout_buffer.clear()
        if process is None:
            return
        try:
            process.stdin.close()
        except Exception:
            pass
        try:
            process.kill()
        except Exception:
            pass
        try:
            process.wait(timeout=1.0)
        except Exception:
            pass

    def _worker_environment(self) -> dict[str, str]:
        # Do not inherit bearer tokens into a subprocess that is intended to
        # operate offline.  The environment is never printed or returned.
        env = {key: value for key, value in os.environ.items() if key not in _WORKER_ENV_BLOCKLIST}
        env.update(
            {
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "HF_DATASETS_OFFLINE": "1",
                "TOKENIZERS_PARALLELISM": "false",
                "PYTHONUNBUFFERED": "1",
                "PYTHONNOUSERSITE": "1",
            }
        )
        return env

    def _read_line(self, process: subprocess.Popen[str], timeout: float) -> Optional[str]:
        if process.stdout is None:
            return None
        try:
            fd = process.stdout.fileno()
            os.set_blocking(fd, False)
        except (OSError, ValueError):
            return None
        deadline = time.monotonic() + max(0.0, timeout)
        selector = selectors.DefaultSelector()
        try:
            selector.register(fd, selectors.EVENT_READ)
            while True:
                newline = self._stdout_buffer.find(b"\n")
                if newline >= 0:
                    raw = bytes(self._stdout_buffer[:newline])
                    del self._stdout_buffer[: newline + 1]
                    if len(raw) > MAX_WORKER_LINE_BYTES:
                        return "\x00worker_response_oversize"
                    try:
                        return raw.decode("utf-8")
                    except UnicodeDecodeError:
                        return "\x00worker_response_encoding"
                if len(self._stdout_buffer) > MAX_WORKER_LINE_BYTES:
                    self._stdout_buffer.clear()
                    return "\x00worker_response_oversize"
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                if not selector.select(remaining):
                    return None
                try:
                    chunk = os.read(fd, 64 * 1024)
                except BlockingIOError:
                    continue
                except OSError:
                    return None
                if not chunk:
                    return None
                self._stdout_buffer.extend(chunk)
        finally:
            selector.close()

    @staticmethod
    def _write_request(
        process: subprocess.Popen[str],
        payload: bytes,
        deadline: float,
    ) -> tuple[bool, Optional[str]]:
        if process.stdin is None:
            return False, "worker_pipe_failed"
        try:
            fd = process.stdin.fileno()
            os.set_blocking(fd, False)
        except (OSError, ValueError):
            return False, "worker_pipe_failed"

        offset = 0
        selector = selectors.DefaultSelector()
        try:
            selector.register(fd, selectors.EVENT_WRITE)
            while offset < len(payload):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False, "worker_write_timeout"
                if not selector.select(remaining):
                    return False, "worker_write_timeout"
                try:
                    written = os.write(fd, payload[offset:])
                except BlockingIOError:
                    continue
                except (BrokenPipeError, OSError):
                    return False, "worker_pipe_failed"
                if written <= 0:
                    return False, "worker_pipe_failed"
                offset += written
        finally:
            selector.close()
        return True, None

    def _ensure_started(self) -> tuple[bool, Optional[str]]:
        if self._process is not None and self._process.poll() is None and self._ready is not None:
            return True, None

        self._clear_process()
        python = self.config.resolved_python()
        worker = self.config.resolved_worker()
        model_dir = self.config.resolved_model_dir()
        if not python.is_file():
            return False, "runtime_python_missing"
        if not worker.is_file():
            return False, "worker_missing"
        if not model_dir.is_dir():
            return False, "model_missing"

        command = [
            str(python),
            "-u",
            str(worker),
            "--model-dir",
            str(model_dir),
            "--model-revision",
            self.config.model_revision,
            "--source-revision",
            self.config.source_revision,
            "--device",
            self.config.device,
        ]
        try:
            process = subprocess.Popen(
                command,
                cwd=str(self.config.project_root),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                env=self._worker_environment(),
            )
        except (OSError, ValueError):
            return False, "worker_start_failed"
        self._process = process
        ready_line = self._read_line(process, self.config.startup_timeout_seconds)
        if not ready_line:
            self._clear_process()
            return False, "worker_start_timeout"
        try:
            ready = json.loads(ready_line)
        except (TypeError, ValueError):
            self._clear_process()
            return False, "worker_protocol_invalid"
        if not isinstance(ready, dict) or ready.get("event") != "ready":
            self._clear_process()
            return False, str(ready.get("reason", "worker_unavailable")) if isinstance(ready, dict) else "worker_unavailable"
        if ready.get("revision") != self.config.model_revision or ready.get("source_revision") != self.config.source_revision:
            self._clear_process()
            return False, "worker_revision_mismatch"
        self._ready = ready
        return True, None

    def classify(self, state: str, questions: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
        started = time.monotonic()
        invalid = validate_request(
            state,
            questions,
            max_questions=self.config.max_questions,
            max_state_chars=self.config.max_state_chars,
        )
        if invalid is not None:
            overflow = None
            if invalid["code"] == "state_chars":
                overflow = {
                    "kind": "state_chars",
                    "observed": len(state) if isinstance(state, str) else None,
                    "limit": self.config.max_state_chars,
                }
            return _invalid_result(
                self.config,
                started,
                code=invalid["code"],
                reason=invalid["detail"],
                overflow=overflow,
            )

        # Send a normalized copy so a custom Mapping implementation cannot
        # mutate while the worker is serializing the request.
        validated = _validate_questions(questions, max_questions=self.config.max_questions)
        assert isinstance(validated, tuple)
        _, normalized = validated
        prepared_request_id = str(self._request_counter + 1)
        try:
            request_payload = (
                json.dumps(
                    {"id": prepared_request_id, "state": state, "questions": normalized},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                + b"\n"
            )
        except (TypeError, ValueError, OverflowError):
            return _invalid_result(
                self.config,
                started,
                code="request_json",
                reason="request could not be serialized",
            )
        if len(request_payload) > MAX_SERIALIZED_REQUEST_BYTES:
            result = _base_result(self.config, status="unavailable", started=started)
            result["reason"] = "request_too_large"
            result["request_bytes"] = len(request_payload)
            result["request_limit_bytes"] = MAX_SERIALIZED_REQUEST_BYTES
            return result

        with self._lock:
            ready, reason = self._ensure_started()
            if not ready:
                result = _base_result(self.config, status="unavailable", started=started)
                result["reason"] = reason or "worker_unavailable"
                return result
            assert self._process is not None and self._process.stdin is not None
            self._request_counter += 1
            request_id = str(self._request_counter)
            # The request id is assigned before the lock for sizing, then
            # corrected after acquisition in case another serialized call ran.
            if prepared_request_id != request_id:
                request_payload = request_payload.replace(
                    f'"id":"{prepared_request_id}"'.encode("utf-8"),
                    f'"id":"{self._request_counter}"'.encode("utf-8"),
                    1,
                )
            deadline = time.monotonic() + max(0.0, self.config.call_timeout_seconds)
            wrote, write_reason = self._write_request(self._process, request_payload, deadline)
            if not wrote:
                self._clear_process()
                result = _base_result(self.config, status="unavailable", started=started)
                result["reason"] = write_reason or "worker_pipe_failed"
                result["timed_out"] = write_reason == "worker_write_timeout"
                return result

            line = self._read_line(self._process, max(0.0, deadline - time.monotonic()))
            if not line:
                worker_exited = self._process.poll() is not None
                self._clear_process()
                result = _base_result(self.config, status="unavailable", started=started)
                result["reason"] = "worker_exited" if worker_exited else "worker_timeout"
                result["timed_out"] = not worker_exited
                return result
            try:
                response = json.loads(line)
            except (TypeError, ValueError):
                self._clear_process()
                result = _base_result(self.config, status="unavailable", started=started)
                result["reason"] = "worker_protocol_invalid"
                return result
            if not isinstance(response, dict) or response.get("id") != request_id:
                self._clear_process()
                result = _base_result(self.config, status="unavailable", started=started)
                result["reason"] = "worker_response_mismatch"
                return result
            # Only copy the defined result fields.  In particular, do not
            # expose arbitrary exception text, paths, environment, or stdout.
            status = response.get("status")
            if status not in {"ok", "unavailable", "invalid_input"}:
                status = "unavailable"
            result = _base_result(
                self.config,
                status=status,
                started=started,
            )
            raw_answers = response.get("answers")
            expected_question_ids = set(normalized)
            reported_elapsed = response.get("elapsed_ms")
            raw_token_counts = response.get("token_counts")
            if status == "ok" and (
                not isinstance(raw_answers, dict)
                or set(raw_answers) != expected_question_ids
                or not _answers_are_valid(raw_answers, normalized)
                or not _elapsed_is_valid(reported_elapsed)
                or not _token_counts_are_valid(raw_token_counts)
            ):
                result["status"] = "unavailable"
                result["reason"] = "worker_output_invalid"
                result["answers"] = {}
            else:
                result["answers"] = raw_answers if status == "ok" and isinstance(raw_answers, dict) else {}
            result["device"] = response.get("device") if isinstance(response.get("device"), str) else None
            if _elapsed_is_valid(reported_elapsed):
                result["elapsed_ms"] = round(max(0.0, float(reported_elapsed)), 3)
            if isinstance(raw_token_counts, dict):
                result["token_counts"] = {
                    "input": raw_token_counts.get("input") if isinstance(raw_token_counts.get("input"), int) else None,
                    "output": raw_token_counts.get("output") if isinstance(raw_token_counts.get("output"), int) else 0,
                    "limit": raw_token_counts.get("limit") if isinstance(raw_token_counts.get("limit"), int) else None,
                    "overflow": bool(raw_token_counts.get("overflow", False)),
                }
                result["usage"] = {
                    "input_tokens": result["token_counts"]["input"],
                    "output_tokens": result["token_counts"]["output"],
                }
            result["overflow"] = response.get("overflow") if isinstance(response.get("overflow"), dict) else None
            if result["reason"] is None:
                result["reason"] = response.get("reason") if isinstance(response.get("reason"), str) else None
            return result


_DEFAULT_RUNTIME: Optional[LayaRuntime] = None
_DEFAULT_RUNTIME_LOCK = threading.Lock()


def get_runtime() -> LayaRuntime:
    global _DEFAULT_RUNTIME
    with _DEFAULT_RUNTIME_LOCK:
        if _DEFAULT_RUNTIME is None:
            _DEFAULT_RUNTIME = LayaRuntime()
        return _DEFAULT_RUNTIME


def close_runtime() -> None:
    global _DEFAULT_RUNTIME
    with _DEFAULT_RUNTIME_LOCK:
        runtime = _DEFAULT_RUNTIME
        _DEFAULT_RUNTIME = None
    if runtime is not None:
        runtime.close()


def localclassify(state: str, questions: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Classify up to five typed questions using the local Laya runtime.

    The function never falls back to cloud inference.  If the isolated
    installation is absent, stale, over budget, or times out, the result has a
    non-``ok`` status and no answers.
    """

    return get_runtime().classify(state, questions)


atexit.register(close_runtime)


__all__ = [
    "LayaRuntime",
    "LayaRuntimeConfig",
    "LAYA_MODEL_ID",
    "LAYA_MODEL_LABEL",
    "LAYA_FILE_SHA256",
    "LAYA_MODEL_SHA256",
    "LAYA_MANIFEST_FILENAME",
    "LAYA_MODEL_REVISION",
    "LAYA_SOURCE_REVISION",
    "close_runtime",
    "get_runtime",
    "localclassify",
    "validate_request",
]
