"""Offline worker for :mod:`laya_runtime`.

This file intentionally owns the optional imports.  It speaks one JSON object
per line on stdin/stdout and never writes model diagnostics or exception text
to the protocol stream.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Mapping

# When invoked as a script Python puts this directory on ``sys.path`` rather
# than the repository root.  Add only the known project root so the worker can
# import the stdlib-only adapter constants; this does not load application
# configuration or optional ML dependencies.
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from backend.app.research.laya_runtime import (
    LAYA_MANIFEST_FILENAME,
    LAYA_MODEL_ID,
    LAYA_MODEL_LABEL,
    LAYA_MODEL_SHA256,
    LAYA_MODEL_REVISION,
    LAYA_SOURCE_REVISION,
    LAYA_ALLOWED_MODEL_FILES,
    LAYA_FILE_SHA256,
    validate_request,
)


def _write(payload: Mapping[str, Any]) -> None:
    # The worker's stdout is a protocol channel.  ``flush`` is required for
    # the parent's bounded selector read to make progress.
    sys.stdout.write(json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _relative_files(model_dir: Path) -> set[str]:
    found: set[str] = set()
    for path in model_dir.rglob("*"):
        if path.is_symlink():
            raise ValueError("model_symlink_not_allowed")
        if path.is_file() and ".cache" not in path.parts:
            found.add(path.relative_to(model_dir).as_posix())
    return found


def _safe_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError("json_object_required")
    # A local checkpoint is not allowed to introduce Python code through
    # transformers' remote-code mechanism.
    if any(key in value for key in ("auto_map", "code_revision", "custom_code")):
        raise ValueError("remote_code_metadata")
    return value


def _verify_model(model_dir: Path, expected_revision: str) -> tuple[dict[str, Any], str]:
    if expected_revision != LAYA_MODEL_REVISION:
        raise ValueError("model_revision_not_approved")
    if not model_dir.is_dir() or model_dir.is_symlink():
        raise ValueError("model_directory_missing")
    manifest_path = model_dir / LAYA_MANIFEST_FILENAME
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise ValueError("model_manifest_missing")
    manifest = _safe_json(manifest_path)
    if manifest.get("model") != LAYA_MODEL_ID or manifest.get("revision") != expected_revision:
        raise ValueError("model_manifest_mismatch")
    if manifest.get("source_revision") != LAYA_SOURCE_REVISION:
        raise ValueError("source_manifest_mismatch")

    files = _relative_files(model_dir)
    unsafe_suffixes = (".bin", ".pt", ".pth", ".pickle", ".pkl", ".py", ".pyc")
    if any(path.endswith(unsafe_suffixes) for path in files):
        raise ValueError("unsafe_model_file")
    required = set(LAYA_ALLOWED_MODEL_FILES)
    if not required.issubset(files):
        raise ValueError("model_files_missing")
    # Allow only the installer manifest plus the approved English checkpoint.
    if files.difference(required):
        raise ValueError("unexpected_model_file")

    manifest_files = manifest.get("files")
    if manifest_files != LAYA_FILE_SHA256:
        raise ValueError("model_file_manifest_mismatch")
    for relative, expected_hash in LAYA_FILE_SHA256.items():
        if relative != "model.safetensors" and _digest(model_dir / relative) != expected_hash:
            raise ValueError("model_file_hash_mismatch")

    config = _safe_json(model_dir / "rl_agent_config.json")
    _safe_json(model_dir / "encoder/config.json")
    _safe_json(model_dir / "tokenizer/tokenizer_config.json")
    if config.get("max_len") != 512 or config.get("head_max_len") != 192:
        raise ValueError("model_context_not_approved")
    weights = model_dir / "model.safetensors"
    expected_hash = manifest.get("model_sha256", LAYA_MODEL_SHA256)
    weights_hash = _digest(weights)
    if expected_hash != LAYA_MODEL_SHA256 or weights_hash != LAYA_MODEL_SHA256:
        raise ValueError("model_weights_hash_mismatch")
    return config, weights_hash


def _normalise_question(question: Mapping[str, Any]) -> dict[str, Any]:
    qtype = question["type"]
    criteria = question.get("criteria")
    if qtype == "choice" and isinstance(criteria, list):
        criteria = {label: None for label in criteria}
    return {"t": qtype, "ins": question["instructions"], "crit": criteria}


def _render_criterion(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(", ", ": "), default=str)


def _render_options(question: Mapping[str, Any]) -> list[str]:
    """Mirror the pinned SDK's option rendering before importing its model."""

    question_type = question["t"]
    criteria = question.get("crit")
    if question_type == "choice":
        return [
            label if value is None or value == "" else "%s: %s" % (label, _render_criterion(value))
            for label, value in criteria.items()
        ]
    if question_type == "score":
        return ["level %d: %s" % (index, _render_criterion(value)) for index, value in enumerate(criteria)]
    criteria = criteria or {}
    false_criterion, true_criterion = criteria.get("false"), criteria.get("true")
    return [
        "false: " + (_render_criterion(false_criterion) if false_criterion not in (None, "") else "no, the statement does not hold"),
        "true: " + (_render_criterion(true_criterion) if true_criterion not in (None, "") else "yes, the statement holds"),
    ]


def _preflight(agent: Any, state: str, questions: Mapping[str, Mapping[str, Any]]) -> tuple[int, dict[str, Any] | None]:
    """Count exact sequences before Laya's truncating sequence builder runs."""

    tok = agent.tok
    max_len = int(agent.cfg.get("max_len", 512))
    head_max_len = int(agent.cfg.get("head_max_len", 192))
    state_ids = tok(state.replace(tok.mask_token, " "), add_special_tokens=False)["input_ids"]
    total_tokens = 0
    for question_id, raw in questions.items():
        question = _normalise_question(raw)
        options = _render_options(question)
        option_ids: list[list[int]] = []
        for option in options:
            raw_ids = tok(" " + option.replace(tok.mask_token, " "), add_special_tokens=False)["input_ids"]
            if len(raw_ids) > 48:
                return total_tokens, {
                    "kind": "option_tokens",
                    "question": question_id,
                    "observed": len(raw_ids),
                    "limit": 48,
                }
            option_ids.append([tok.mask_token_id] + raw_ids)

        option_tokens = sum(len(value) for value in option_ids)
        if head_max_len < 16 or option_tokens > head_max_len - 16:
            return total_tokens, {
                "kind": "option_head_tokens",
                "question": question_id,
                "observed": option_tokens,
                "limit": max(0, head_max_len - 16),
            }
        option_budget = head_max_len - option_tokens
        head_ids = tok(
            "%s question: %s" % (question["t"], str(question["ins"]).replace(tok.mask_token, " ")),
            add_special_tokens=False,
        )["input_ids"]
        head_limit = max(8, option_budget)
        if len(head_ids) > head_limit:
            return total_tokens, {
                "kind": "instruction_tokens",
                "question": question_id,
                "observed": len(head_ids),
                "limit": head_limit,
            }
        fixed = 1 + len(head_ids) + 1 + option_tokens + 1 + 1
        if fixed > max_len:
            return total_tokens, {
                "kind": "question_tokens",
                "question": question_id,
                "observed": fixed,
                "limit": max_len,
            }
        room = max(0, max_len - fixed)
        if len(state_ids) > room:
            return total_tokens, {
                "kind": "state_tokens",
                "question": question_id,
                "observed": len(state_ids),
                "limit": room,
            }
        total_tokens += fixed + len(state_ids)
    return total_tokens, None


def _load_agent(model_dir: Path, device: str) -> tuple[Any, dict[str, Any]]:
    # Importing laya is deliberately contained here, inside the subprocess.
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        import importlib.metadata

        distribution = importlib.metadata.distribution("laya")
        direct_url_text = distribution.read_text("direct_url.json")
        if not direct_url_text:
            raise RuntimeError("laya_source_unverified")
        direct_url = json.loads(direct_url_text)
        vcs = direct_url.get("vcs_info") or {}
        if vcs.get("commit_id") != LAYA_SOURCE_REVISION:
            raise RuntimeError("laya_source_revision_mismatch")
        import laya

        agent = laya.load(str(model_dir), device=None if device == "auto" else device)
    device_name = getattr(getattr(agent, "device", None), "type", None)
    return agent, {"device": device_name if isinstance(device_name, str) else "unknown"}


def _run(args: argparse.Namespace) -> int:
    model_dir = Path(args.model_dir).expanduser()
    try:
        if args.source_revision != LAYA_SOURCE_REVISION:
            raise ValueError("source_revision_not_approved")
        config, weights_hash = _verify_model(model_dir, args.model_revision)
        agent, runtime_meta = _load_agent(model_dir, args.device)
    except Exception:
        _write(
            {
                "event": "unavailable",
                "reason": "model_or_runtime_unavailable",
                "revision": args.model_revision,
                "source_revision": args.source_revision,
            }
        )
        return 2

    _write(
        {
            "event": "ready",
            "model": LAYA_MODEL_ID,
            "model_label": LAYA_MODEL_LABEL,
            "revision": args.model_revision,
            "source_revision": args.source_revision,
            "device": runtime_meta["device"],
            "context_tokens": int(config.get("max_len", 512)),
            "weights_sha256": weights_hash,
        }
    )
    for raw_line in sys.stdin:
        try:
            request = json.loads(raw_line)
        except (TypeError, ValueError):
            _write({"id": None, "status": "invalid_input", "reason": "request_json"})
            continue
        if not isinstance(request, dict) or not isinstance(request.get("id"), str):
            _write({"id": None, "status": "invalid_input", "reason": "request_shape"})
            continue
        request_id = request["id"]
        state = request.get("state")
        questions = request.get("questions")
        invalid = validate_request(state, questions)
        if invalid is not None:
            _write({"id": request_id, "status": "invalid_input", "reason": invalid["code"]})
            continue
        started = time.monotonic()
        try:
            token_count, overflow = _preflight(agent, state, questions)
        except Exception:
            _write({"id": request_id, "status": "unavailable", "reason": "tokenizer_unavailable"})
            continue
        if overflow is not None:
            _write(
                {
                    "id": request_id,
                    "status": "unavailable",
                    "answers": {},
                    "elapsed_ms": round((time.monotonic() - started) * 1000.0, 3),
                    "token_counts": {"input": token_count, "output": 0, "limit": 512, "overflow": True},
                    "overflow": overflow,
                    "reason": "input_overflow",
                }
            )
            continue
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                output = agent.predict(state, questions)
        except Exception:
            _write(
                {
                    "id": request_id,
                    "status": "unavailable",
                    "answers": {},
                    "elapsed_ms": round((time.monotonic() - started) * 1000.0, 3),
                    "token_counts": {"input": token_count, "output": 0, "limit": 512, "overflow": False},
                    "reason": "inference_failed",
                }
            )
            continue
        answers = output.get("answers") if isinstance(output, dict) and isinstance(output.get("answers"), dict) else {}
        _write(
            {
                "id": request_id,
                "status": "ok",
                "answers": answers,
                "elapsed_ms": round((time.monotonic() - started) * 1000.0, 3),
                "token_counts": {"input": token_count, "output": 0, "limit": 512, "overflow": False},
                "device": runtime_meta["device"],
            }
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--model-revision", required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--device", default="cpu", choices=("auto", "cpu", "mps"))
    return _run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
