"""Codex subscription adapter.

The adapter invokes the installed ``codex exec`` binary with argv lists and a
minimal private working directory.  It deliberately never reads auth files or
API keys.  Only lifecycle and usage metadata are retained; provider reasoning
and raw tool commands are discarded.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import signal
import tempfile
import copy
import hashlib
import time
from dataclasses import dataclass
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..config import Settings, settings
from ..schemas import AgentOutputPayload, ModelConfig
from .base import EventCallback, ProviderError, ProviderEvent, ProviderHealth, ProviderModel, ProviderResult


SAFE_ENV = {
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "SHELL",
    "LANG",
    "LC_ALL",
    "TMPDIR",
    "CODEX_HOME",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "CODEX_CA_CERTIFICATE",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
}
BLOCKED_ENV_MARKERS = (
    "API_KEY",
    "API_BASE",
    "BASE_URL",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PROVIDER",
)
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,159}$")
MAX_DISCOVERY_SEARCHES = 6
MAX_DISCOVERY_WEB_ACTIONS = 12
_WEB_ACTION_TYPES = {"search", "open", "find", "open_page", "find_in_page"}
MAX_DISCOVERY_DIAGNOSTIC_EVENTS = 256


def _safe_protocol_name(value: str, allowed: set[str]) -> str:
    """Persist only known vocabulary, never arbitrary provider strings."""
    normalized = value.casefold()
    return normalized if normalized in allowed else "missing" if not value else "unsupported"


def _direct_web_url(value: Any) -> bool:
    """Recognize a complete URL display sentinel, never free-form search text."""
    if not isinstance(value, str) or not value or any(char.isspace() or ord(char) < 0x20 for char in value):
        return False
    try:
        parsed = urlsplit(value)
        _ = parsed.port  # Reject malformed ports rather than coercing them.
        return (parsed.scheme in {"http", "https"} and bool(parsed.hostname)
                and parsed.username is None and parsed.password is None)
    except ValueError:
        return False


def _query_shape(value: Any) -> str:
    """A content-free query shape for local CLI protocol diagnosis."""
    if isinstance(value, str):
        if not value.strip():
            return "empty"
        return "url" if re.match(r"https?://", value.strip(), re.I) else "text"
    if isinstance(value, (list, tuple)):
        return "batch"
    if _integer_metadata(value) is not None:
        return "count"
    return "invalid"


class _DiscoveryDiagnostics:
    """Private bounded accounting receipt, retained even on provider failure.

    Rows contain protocol types, pseudonymous operation IDs and counts only.
    They deliberately exclude prompts, queries, output, reasoning and commands.
    Each row is closed immediately so an ordinary cancellation/failure retains
    all observations recorded before it. Final counts remain available even
    when a duplicate-event flood fills the event allowance.
    """

    def __init__(self, workdir: Path, limits: "DiscoveryLimits", explicit: bool):
        self.path = workdir / "discovery-accounting.jsonl"
        self.rows = 0
        self.dropped = 0
        self.started = time.monotonic()
        descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
        os.close(descriptor)
        self._append({"type": "limits", "version": "discovery-accounting.v1", "explicit": explicit,
                      "max_search_queries": limits.max_search_queries, "max_web_actions": limits.max_web_actions})

    def _append(self, row: dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as output:
            output.write(json.dumps(row, separators=(",", ":")) + "\n")

    def record(self, row: dict[str, Any]) -> None:
        self.rows += 1
        if self.rows > MAX_DISCOVERY_DIAGNOSTIC_EVENTS:
            self.dropped += 1
            return
        self._append({"type": "web_event", "sequence": self.rows,
                      "elapsed_ms": round((time.monotonic() - self.started) * 1000), **row})

    def finish(self, outcome: str, *, searches: int, actions: int, pending: int) -> None:
        self._append({"type": "finished", "outcome": outcome, "search_queries": searches,
                      "web_actions": actions, "pending_metadata": pending,
                      "observed_events": self.rows, "omitted_events": self.dropped,
                      "elapsed_ms": round((time.monotonic() - self.started) * 1000)})


@dataclass(frozen=True, slots=True)
class DiscoveryLimits:
    """Trusted per-attempt limits for the provider's public discovery tools.

    The workflow owns these values.  ``None`` at the adapter boundary keeps
    the historical six-search/twelve-action defaults, which is important for
    callers that predate the five-question contract.
    """

    max_search_queries: int
    max_web_actions: int


@dataclass(slots=True)
class _WebOperationState:
    """Accounting state for one provider web operation."""

    action_type: str = ""
    web_actions: int = 1
    search_queries: int = 0
    search_metadata_known: bool = True
    query: str | None = None
    started: bool = False
    completed: bool = False
    emitted: bool = False
    charged_actions: int = 0
    charged_searches: int = 0
    awaiting_terminal_search_metadata: bool = False


@dataclass(frozen=True, slots=True)
class _WebEventDetails:
    """Bounded metadata extracted from one lifecycle record.

    ``search_queries`` is ``None`` when the record does not expose a bounded
    search cardinality.  An opaque ``other`` action remains compatible with
    legacy calls, but explicit five-question limits reject it because it may
    represent a batch larger than one query.
    """

    action_type: str
    web_actions: int = 1
    search_queries: int | None = None
    search_metadata_known: bool = True
    query: str | None = None


def _integer_metadata(value: Any) -> int | None:
    """Return a non-negative integer metadata value, excluding booleans."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    return None


def _query_metadata(value: Any) -> tuple[int, str | None] | None:
    """Read a query or query-batch field without reading provider content.

    Codex installations have emitted both ``query`` and ``queries``.  The
    latter is the authoritative batch cardinality when present.  A scalar
    query is one operation; a list/tuple is counted item-for-item.  Other
    values are intentionally treated as ambiguous rather than coerced.
    """
    if isinstance(value, str):
        return (1, value[:500] or None)
    if isinstance(value, (list, tuple)):
        values = [item for item in value if isinstance(item, str)]
        if len(values) != len(value):
            return None
        return (len(values), values[0][:500] if values else None)
    count = _integer_metadata(value)
    if count is not None:
        return (count, None)
    return None


def _web_event_details(
    item: dict[str, Any],
    *,
    action_type: str,
    action: dict[str, Any],
) -> _WebEventDetails:
    """Extract operation/query cardinality from trusted event metadata.

    This function never examines the prompt or result text.  It accepts the
    CLI's current ``other`` action for legacy aggregate accounting, while
    requiring explicit query cardinality whenever a trusted per-attempt
    search bound is active.
    """
    nested = item.get("item") if isinstance(item.get("item"), dict) else {}
    kind = str(action_type or "").casefold()

    # Observed CLI protocol: every web operation starts as other/query="";
    # search completions expose search + query/queries. Non-search opens
    # complete as other/query="" for a result reference, or other/query=URL
    # for a direct URL. These observed typed display sentinels are not search
    # requests. Admit only their exact envelope; explicit search + URL still
    # counts as one query. Starts remain pending until a terminal record.
    other_nonsearch_sentinel = (
        kind == "other" and set(action) == {"type"}
        and str(item.get("type") or "").casefold() in {"item.started", "item.completed"}
        and str(nested.get("type") or "").casefold() in {"web_search", "websearch"}
        and (nested.get("query") == "" or _direct_web_url(nested.get("query")))
        and not any(key in source for source in (nested, item)
                    for key in ("queries", "query_count", "num_queries", "number_of_queries"))
        and "query" not in item
    )
    if other_nonsearch_sentinel:
        return _WebEventDetails(kind, search_queries=0,
                                search_metadata_known=str(item.get("type") or "").casefold() == "item.completed")

    # Prefer action metadata, then the nested item, then the wrapper.  The
    # wrapper fallback covers older direct web_search records.
    metadata_sources: tuple[dict[str, Any], ...] = tuple(
        source
        for source in (action, nested, item)
        if isinstance(source, dict)
    )
    query: str | None = None
    query_count: int | None = None
    metadata_seen = False
    empty_query_seen = False
    for source in metadata_sources:
        for key in ("queries", "query"):
            if key not in source:
                continue
            if key == "query" and source.get(key) == "":
                # Some batched completions retain an empty scalar display
                # alias. Their nonempty queries/count field is authoritative.
                empty_query_seen = True
                continue
            metadata_seen = True
            parsed = _query_metadata(source.get(key))
            if parsed is None:
                # An explicitly supplied but malformed cardinality cannot be
                # made safe by falling back to one.
                return _WebEventDetails(
                    kind,
                    search_queries=None,
                    search_metadata_known=False,
                )
            candidate_count, candidate_query = parsed
            # A nested item can repeat the scalar query while action.queries
            # exposes the actual batch.  Retain the largest observed count.
            if query_count is None or candidate_count > query_count:
                query_count = candidate_count
            if query is None and candidate_query:
                query = candidate_query
        for key in ("query_count", "num_queries", "number_of_queries"):
            if key not in source:
                continue
            metadata_seen = True
            candidate_count = _integer_metadata(source.get(key))
            if candidate_count is None:
                return _WebEventDetails(
                    kind,
                    search_queries=None,
                    search_metadata_known=False,
                )
            query_count = max(query_count or 0, candidate_count)

    if kind in {"open", "find", "open_page", "find_in_page"}:
        return _WebEventDetails(kind, search_queries=0, query=query)
    if kind == "search":
        if query_count == 0 or (query_count is None and empty_query_seen):
            return _WebEventDetails(kind, search_queries=None, search_metadata_known=False)
        # A search action without a batch field is one observable query in the
        # legacy protocol.  If the CLI exposes a list, its exact size wins.
        return _WebEventDetails(
            kind,
            search_queries=query_count if query_count is not None else 1,
            search_metadata_known=True,
            query=query,
        )
    if kind == "other":
        # ``other`` is the current typed lifecycle action for web searches.
        # Preserve compatibility when it omits query metadata for legacy
        # aggregate accounting.  An explicit budget must fail closed because
        # one opaque event may represent an arbitrary query batch.
        return _WebEventDetails(
            kind,
            search_queries=query_count,
            search_metadata_known=metadata_seen,
            query=query,
        )
    if query_count is not None:
        # A query-bearing event without a recognized action is still a search
        # operation, but its shape is not safe enough for an explicit bound.
        return _WebEventDetails(
            kind,
            search_queries=query_count,
            search_metadata_known=False,
            query=query,
        )
    return _WebEventDetails(kind, search_queries=None, search_metadata_known=False)


def _coerce_discovery_limits(
    value: DiscoveryLimits | Mapping[str, Any] | None,
) -> tuple[DiscoveryLimits, bool]:
    """Resolve explicit workflow limits while preserving legacy defaults.

    The canonical mapping keys are ``max_search_queries`` and
    ``max_web_actions``.  A few descriptive aliases are accepted so a
    workflow can pass an immutable policy object or a plain JSON-like dict.
    The second return value indicates whether limits were explicitly supplied.
    """
    if value is None:
        return DiscoveryLimits(MAX_DISCOVERY_SEARCHES, MAX_DISCOVERY_WEB_ACTIONS), False
    if isinstance(value, DiscoveryLimits):
        limits = value
    elif isinstance(value, Mapping):
        search_value = next(
            (
                value.get(key)
                for key in (
                    "max_search_queries",
                    "max_searches",
                    "search_queries",
                    "search_limit",
                    "query_limit",
                    "max_queries",
                )
                if key in value
            ),
            MAX_DISCOVERY_SEARCHES,
        )
        action_value = next(
            (
                value.get(key)
                for key in (
                    "max_web_actions",
                    "max_actions",
                    "web_actions",
                    "web_action_limit",
                    "action_limit",
                )
                if key in value
            ),
            MAX_DISCOVERY_WEB_ACTIONS,
        )
        limits = DiscoveryLimits(search_value, action_value)  # type: ignore[arg-type]
    else:
        raise ProviderError("capability", "Codex discovery limits must be a trusted mapping or DiscoveryLimits value.")
    if (
        isinstance(limits.max_search_queries, bool)
        or not isinstance(limits.max_search_queries, int)
        or limits.max_search_queries < 0
        or isinstance(limits.max_web_actions, bool)
        or not isinstance(limits.max_web_actions, int)
        or limits.max_web_actions < 0
    ):
        raise ProviderError("capability", "Codex discovery limits must be non-negative integer counts.")
    return limits, True


def _web_event_metadata(item: dict[str, Any]) -> tuple[bool, bool, bool, str, str, str, dict[str, Any]]:
    """Extract only safe protocol metadata from a potential web event.

    The return value intentionally excludes query text and all provider
    payload content.  It is used both for the narrow event gate and for safe
    capability diagnostics when the CLI sends an unsupported shape.
    """
    item_type = str(item.get("type") or "")
    nested = item.get("item") if isinstance(item.get("item"), dict) else {}
    nested_type = str(nested.get("type") or "")
    item_type_key = item_type.casefold()
    nested_type_key = nested_type.casefold()
    # The local adapter historically used ``web_search`` while newer CLI
    # builds have emitted ``WebSearch``.  Compare protocol type names without
    # changing the diagnostic values returned to callers.
    web_event = item_type_key in {"web_search", "websearch"} or nested_type_key in {"web_search", "websearch"}
    action = nested.get("action") if isinstance(nested.get("action"), dict) else item.get("action") if isinstance(item.get("action"), dict) else {}
    action_type = str(action.get("type") or "")
    valid_wrapper = item_type_key in {"web_search", "websearch", "item.started", "item.completed"} and (not nested_type or nested_type_key in {"web_search", "websearch"})
    # ``other`` is the typed lifecycle action used by the CLI for both
    # started and completed batched web-search records.  The exact nested
    # type check above prevents this from becoming a generic-tool bypass.
    valid_action = not action or action_type.casefold() in _WEB_ACTION_TYPES or action_type.casefold() == "other"
    return web_event, valid_wrapper, valid_action, item_type, nested_type, action_type, action


def _safe_environment() -> dict[str, str]:
    output: dict[str, str] = {}
    for key in SAFE_ENV:
        value = os.environ.get(key)
        if isinstance(value, str):
            output[key] = value
    # Prevent inherited provider overrides even if a future allow-list grows.
    for key in list(output):
        if any(marker in key.upper() for marker in BLOCKED_ENV_MARKERS):
            output.pop(key, None)
    output["CODEX_HOME"] = os.environ.get("CODEX_HOME", os.path.expanduser("~/.codex"))
    return output


def _classify_failure(text: str, returncode: int) -> ProviderError:
    lower = (text or "").lower()
    if any(term in lower for term in ("input_too_large", "input exceeds the maximum length")):
        # This is the CLI's turn/start character limit, before the model
        # receives the packet.  Retrying the identical input cannot help.
        return ProviderError("context_limit", "Codex rejected this evidence packet because it exceeds the CLI input size limit; reduce the provider packet before retrying.")
    if any(term in lower for term in ("not logged in", "login required", "authentication", "unauthorized", "auth error")):
        return ProviderError("auth", "Codex ChatGPT authentication is unavailable; sign in with the Codex CLI.")
    if any(term in lower for term in ("usage limit", "rate limit", "quota", "too many requests")):
        return ProviderError("quota", "Codex subscription usage limit reached; no paid fallback is enabled.", retryable=True)
    if any(term in lower for term in ("context window", "context length", "context_length_exceeded", "too long")):
        return ProviderError("context_limit", "The Codex context limit rejected this evidence packet.")
    if any(term in lower for term in ("invalid_json_schema", "invalid json schema", "required must include", "schema validation")):
        return ProviderError("capability", "Codex rejected the structured output schema for this installation.")
    if any(term in lower for term in ("unknown option", "unrecognized option", "invalid value", "unknown config", "unsupported", "not supported", "model metadata")):
        return ProviderError("capability", "This Codex installation does not support the requested execution setting.")
    if returncode == -signal.SIGTERM:
        return ProviderError("cancelled", "Codex execution was cancelled.")
    return ProviderError("execution", "Codex did not produce a valid structured result.", retryable=True)


def _bounded_diagnostic_text(text: str, limit: int = 4000) -> str:
    """Keep both startup and terminal diagnostics, only for classification."""
    if len(text) <= limit:
        return text
    half = limit // 2
    return text[:half] + "\n" + text[-half:]


def _provider_error_text(item: dict[str, Any]) -> str:
    """Extract explicit protocol errors without traversing model content.

    Modern CLI failures use ``turn.failed.error.message``; older versions
    expose a top-level message or an ``item`` of type ``error``.  This text
    stays inside the classifier, never in persisted lifecycle events.
    """
    nested = item.get("item") if isinstance(item.get("item"), dict) else {}
    records = [item] if item.get("type") in {"error", "turn.failed"} else []
    if nested.get("type") == "error":
        records.append(nested)
    values: list[str] = []
    for record in records:
        error = record.get("error")
        for field in (record, error if isinstance(error, dict) else {}):
            for key in ("message", "code", "input_error_code"):
                value = field.get(key)
                if isinstance(value, str):
                    values.append(_bounded_diagnostic_text(value))
        if isinstance(error, str):
            values.append(_bounded_diagnostic_text(error))
    return " ".join(values)


def normalize_output_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Codex JSON-schema mode requires every declared property to be required.

    Nullable fields remain nullable.  Defaults are removed because a provider
    must return the complete artifact instead of relying on schema defaults.
    """
    schema = copy.deepcopy(schema)
    if schema.get("title") == "AgentOutputPayload":
        # Scenario snapshots are produced by the local calculation engine.
        # Asking a model to emit them both allows fabricated calculations and
        # introduces arbitrary-key maps unsupported by strict JSON output.
        # Keep the response fields explicit, but permit only their empty
        # forms. The stored/API model retains its complete snapshot schema.
        properties = schema.get("properties", {})
        if "simulation_snapshot" in properties:
            properties["simulation_snapshot"] = {"type": "null"}
        if "simulation_snapshots" in properties:
            properties["simulation_snapshots"] = {"type": "array", "items": {"type": "null"}, "maxItems": 0}
        schema.get("$defs", {}).pop("PriceScenarioSnapshot", None)

    def visit(value: Any) -> Any:
        if isinstance(value, list):
            return [visit(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {key: visit(item) for key, item in value.items() if key != "default"}
        if result.get("type") == "object" and isinstance(result.get("properties"), dict):
            result["properties"] = {key: visit(item) for key, item in result["properties"].items()}
            result["required"] = list(result["properties"])
        for key in ("anyOf", "oneOf", "allOf"):
            if key in result:
                result[key] = visit(result[key])
        return result
    return visit(schema)


class CodexAdapter:
    provider_id = "codex"
    display_name = "Codex subscription"
    billing_route = "chatgpt_subscription"

    def __init__(self, config: Settings | None = None):
        self.config = config or settings
        self.binary = self.config.codex_binary
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._preflight: dict[tuple[str, str | None], bool] = {}

    def _binary_path(self) -> str | None:
        if Path(self.binary).is_file() and os.access(self.binary, os.X_OK):
            return self.binary
        return shutil.which(self.binary)

    async def _run_simple(self, args: list[str], timeout: float = 30) -> tuple[int, str]:
        binary = self._binary_path()
        if not binary:
            return 127, "Codex executable is not installed."
        try:
            proc = await asyncio.create_subprocess_exec(
                binary,
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_safe_environment(),
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            try:
                proc.kill()  # type: ignore[union-attr]
            except Exception:
                pass
            return 124, "Codex status check timed out."
        except OSError:
            return 127, "Codex executable could not be started."
        return proc.returncode or 0, (stdout + stderr).decode("utf-8", errors="replace")[:4000]

    async def health(self) -> ProviderHealth:
        if not self._binary_path():
            return ProviderHealth(self.provider_id, self.display_name, False, "missing", "Codex executable is not installed.", self.billing_route)
        rc_version, version_text = await self._run_simple(["--version"])
        if rc_version:
            return ProviderHealth(self.provider_id, self.display_name, False, "unavailable", "Codex executable could not be validated.", self.billing_route)
        rc_login, login_text = await self._run_simple(["login", "status"])
        if rc_login or "chatgpt" not in login_text.lower():
            return ProviderHealth(
                self.provider_id,
                self.display_name,
                False,
                "auth_required",
                "Codex is installed but ChatGPT subscription authentication is unavailable.",
                self.billing_route,
            )
        version = version_text.strip().splitlines()[0] if version_text.strip() else "installed"
        models = self._models(True, f"{version}")
        return ProviderHealth(self.provider_id, self.display_name, True, "ready", None, self.billing_route, models)

    def _models(self, available: bool, reason: str | None) -> tuple[ProviderModel, ...]:
        def state(model_id: str, candidates: tuple[str, ...]) -> tuple[bool, str | None, tuple[str, ...]]:
            if not available:
                return False, reason, candidates
            validated = {
                effort
                for (candidate_model, effort), ok in self._preflight.items()
                if ok and candidate_model == model_id
            }
            if not validated:
                # Keep the supported candidate controls visible so the user
                # can run an explicit preflight.  ``available`` remains false
                # until that exact model/effort pair has passed.
                return False, "Model is authenticated but has not passed a schema preflight on this installation.", candidates
            # ``None`` is the provider's default and is intentionally not
            # advertised as a named reasoning control.  The candidate list
            # describes installation support; pair validation is represented
            # by the model-level available/reason fields above.
            return True, None, candidates

        catalog = (
            ("gpt-6-luna", "GPT-6 Luna", ("low", "medium", "high", "xhigh", "max")),
            ("gpt-6-sol", "GPT-6 Sol", ("low", "medium", "high", "xhigh", "max", "ultra")),
            ("gpt-6-astra", "GPT-6 Astra", ("low", "medium", "high", "xhigh", "max", "ultra")),
            ("gpt-5.6-luna", "GPT-5.6 Luna (legacy)", ("low", "medium", "high", "xhigh", "max")),
        )
        return tuple(ProviderModel(model, title, *state(model, efforts),
            {"structured_output": True, "streaming": True, "cancellation": True, "tools": False, "images": False})
            for model, title, efforts in catalog)

    def is_preflighted(self, config: ModelConfig) -> bool:
        """Whether this exact model/reasoning pair passed an execution probe."""
        return bool(self._preflight.get((config.model, config.reasoning_effort), False))

    async def list_models(self) -> tuple[ProviderModel, ...]:
        health = await self.health()
        return health.models if health.models else self._models(False, health.reason)

    async def preflight(self, config: ModelConfig, execute: bool, schema: dict[str, Any] | None = None) -> dict[str, Any]:
        if not MODEL_RE.fullmatch(config.model):
            return {"available": False, "status": "invalid_model", "reason": "Model identifier is invalid.", "actual_execution": False, "validated": False}
        if config.provider != "codex":
            return {"available": False, "status": "wrong_provider", "reason": "Codex adapter received another provider.", "actual_execution": False, "validated": False}
        health = await self.health()
        if not health.available:
            return {"available": False, "status": health.status, "reason": health.reason, "actual_execution": False, "validated": False}
        supported = {model.id: model for model in health.models}
        descriptor = supported.get(config.model)
        if descriptor is None:
            return {"available": False, "status": "unsupported_model", "reason": "Model is not a supported Codex candidate on this installation.", "actual_execution": False, "validated": False}
        if config.reasoning_effort and config.reasoning_effort not in descriptor.reasoning_efforts:
            return {"available": False, "status": "unsupported_reasoning", "reason": "Reasoning effort is not supported for this model.", "actual_execution": False, "validated": False}
        if not execute:
            return {"available": True, "status": "candidate_ready", "reason": None, "model": config.model, "reasoning_effort": config.reasoning_effort, "actual_execution": False, "validated": self.is_preflighted(config)}
        probe_schema = schema or {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False}
        with tempfile.TemporaryDirectory(prefix="road2m-preflight-") as temp:
            work = Path(temp)
            schema_path = work / "schema.json"
            result_path = work / "result.json"
            schema_path.write_text(json.dumps(normalize_output_schema(probe_schema)), encoding="utf-8")
            prompt = "Return exactly the JSON object {\"ok\": true}. Do not use tools or access files."
            try:
                await asyncio.wait_for(self._execute_raw("preflight", prompt, config, schema_path, result_path, work, None), timeout=max(30, self.config.codex_timeout_seconds))
                parsed = json.loads(result_path.read_text(encoding="utf-8"))
                if parsed.get("ok") is not True:
                    raise ValueError("probe did not return ok=true")
            except ProviderError as exc:
                return {"available": False, "status": exc.kind, "reason": exc.message, "model": config.model, "reasoning_effort": config.reasoning_effort, "actual_execution": True, "validated": False}
            except asyncio.TimeoutError:
                return {"available": False, "status": "timeout", "reason": "Codex schema probe exceeded its configured timeout.", "model": config.model, "reasoning_effort": config.reasoning_effort, "actual_execution": True, "validated": False}
            except (OSError, ValueError, json.JSONDecodeError):
                return {"available": False, "status": "invalid_probe", "reason": "Codex exited without the required probe artifact.", "model": config.model, "reasoning_effort": config.reasoning_effort, "actual_execution": True, "validated": False}
        self._preflight[(config.model, config.reasoning_effort)] = True
        return {"available": True, "status": "executed", "reason": None, "model": config.model, "reasoning_effort": config.reasoning_effort, "actual_execution": True, "validated": True}

    def _argv(
        self,
        config: ModelConfig,
        schema_path: Path,
        result_path: Path,
        workdir: Path,
        *,
        discovery_stage: bool = False,
    ) -> list[str]:
        if not MODEL_RE.fullmatch(config.model):
            raise ProviderError("capability", "Model identifier is invalid.")
        args = [
            "-a",
            "never",
            "exec",
            "--json",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--skip-git-repo-check",
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(result_path),
            "-m",
            config.model,
            "-s",
            "read-only",
            "-C",
            str(workdir),
            "-c",
            'forced_login_method="chatgpt"',
            "-c",
            f'model_reasoning_effort="{config.reasoning_effort or "high"}"',
            "-c",
            "features.shell_tool=false",
            "-c",
            "features.multi_agent=false",
            "-c",
            "features.plugins=false",
            "-c",
            "features.apps=false",
            "-c",
            "features.hooks=false",
            "-c",
            "features.browser_use=false",
            "-c",
            "features.browser_use_external=false",
            "-c",
            "features.computer_use=false",
            "-c",
            f"features.code_mode_host={'true' if discovery_stage else 'false'}",
            "-c",
            "features.code_mode=false",
            "-c",
            "features.unified_exec=false",
            "-c",
            "features.view_image=false",
            "-c",
            "features.image_generation=false",
            "-c",
            f'web_search="{"live" if discovery_stage else "disabled"}"',
            "-c",
            "project_doc_max_bytes=0",
            "-",
        ]
        return args

    async def _execute_raw(
        self,
        attempt_id: str,
        prompt: str,
        config: ModelConfig,
        schema_path: Path,
        result_path: Path,
        workdir: Path,
        on_event: EventCallback | None,
        *,
        discovery_stage: bool = False,
        discovery_limits: DiscoveryLimits | Mapping[str, Any] | None = None,
    ) -> ProviderResult:
        resolved_discovery_limits, explicit_discovery_limits = _coerce_discovery_limits(discovery_limits)
        binary = self._binary_path()
        if not binary:
            raise ProviderError("unavailable", "Codex executable is not installed.")
        argv = self._argv(config, schema_path, result_path, workdir, discovery_stage=discovery_stage)
        diagnostics = _DiscoveryDiagnostics(workdir, resolved_discovery_limits, explicit_discovery_limits) if discovery_stage else None
        diagnostics_outcome = "interrupted"
        try:
            proc = await asyncio.create_subprocess_exec(
                binary,
                *argv,
                cwd=str(workdir),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_safe_environment(),
            )
        except OSError:
            raise ProviderError("unavailable", "Codex process could not be started.")
        self._processes[attempt_id] = proc
        events: list[ProviderEvent] = []
        provider_messages: list[str] = []
        turn_failed = False
        discovery_searches = 0
        discovery_web_actions = 0
        web_operations: dict[str, _WebOperationState] = {}
        anonymous_web_operation = 0
        stderr_task = asyncio.create_task(proc.stderr.read() if proc.stderr else asyncio.sleep(0, result=b""))
        try:
            if proc.stdin:
                proc.stdin.write(prompt.encode("utf-8"))
                await proc.stdin.drain()
                proc.stdin.close()
            while True:
                line = await proc.stdout.readline() if proc.stdout else b""
                if not line:
                    break
                try:
                    item = json.loads(line.decode("utf-8", errors="replace"))
                except (TypeError, ValueError):
                    continue
                if not isinstance(item, dict):
                    continue
                # The backend only accepts lifecycle/usage events. A00 and
                # normal analyst calls reject every capability event. A01's
                # one discovery pass may emit the exact typed web-search
                # lifecycle event; its URLs are still fetched and verified by
                # the backend before becoming evidence.
                nested = item.get("item") if isinstance(item.get("item"), dict) else {}
                web_event, valid_wrapper, valid_action, item_type, nested_type, action_type, action = _web_event_metadata(item)
                if web_event:
                    raw_operation_id = str(nested.get("id") or item.get("id") or "").strip()
                    diagnostic_row: dict[str, Any] = {
                        "event_type": _safe_protocol_name(item_type, {"item.started", "item.completed", "web_search", "websearch"}),
                        "nested_type": _safe_protocol_name(nested_type, {"web_search", "websearch"}),
                        "action_type": _safe_protocol_name(action_type, _WEB_ACTION_TYPES | {"other"}),
                        "operation_id": hashlib.sha256(raw_operation_id.encode()).hexdigest()[:24] if raw_operation_id else None,
                        "known_operation": raw_operation_id in web_operations if raw_operation_id else False,
                        "query_shapes": {f"{label}.{key}": _query_shape(source[key])
                            for label, source in (("action", action), ("item", nested), ("wrapper", item))
                            for key in ("query", "queries", "query_count", "num_queries", "number_of_queries") if key in source},
                        "action_fields": sorted(set(action) & {"type", "query", "queries", "query_count", "num_queries", "number_of_queries", "url", "urls", "ref_id", "pattern", "lineno"}),
                        "item_fields": sorted(set(nested) & {"type", "id", "action", "query", "queries", "query_count", "num_queries", "number_of_queries", "status", "url", "urls", "ref_id", "pattern"}),
                        "search_queries_before": discovery_searches,
                        "web_actions_before": discovery_web_actions,
                        "action_delta": 0, "search_delta": 0, "outcome": "rejected_protocol",
                    }
                    try:
                        if not discovery_stage or not valid_wrapper or not valid_action:
                            proc.terminate()
                            raise ProviderError(
                                "capability",
                                "Codex rejected a web event outside the bounded discovery protocol "
                                f"(event_type={item_type[:80]!r}, nested_type={nested_type[:80]!r}, action_type={action_type[:80]!r}).",
                            )
                        # The CLI emits one ``item.started`` and one
                        # ``item.completed`` record for a web operation.  Keep
                        # accounting keyed by the stable provider id so a pair or
                        # duplicate terminal record consumes one slot.  A direct
                        # ``web_search`` item is already terminal in older
                        # protocol versions.
                        item_type_key = item_type.casefold()
                        is_started = item_type_key == "item.started"
                        is_completed = item_type_key in {"item.completed", "web_search", "websearch"}
                        operation_id = str(nested.get("id") or item.get("id") or "").strip()
                        if not operation_id and is_started and explicit_discovery_limits:
                            proc.terminate()
                            raise ProviderError(
                                "capability",
                                "Codex emitted a web-search lifecycle event without a stable operation id; "
                                "the explicit discovery budget cannot deduplicate this event safely.",
                            )
                        if operation_id:
                            operation_key = operation_id
                        else:
                            anonymous_web_operation += 1
                            operation_key = f"anonymous-{anonymous_web_operation}"
                        # A completion can omit action while adding a batch count.
                        # Inherit only its known action kind; still parse all fresh
                        # terminal metadata before reconciling cardinality.
                        prior_state = web_operations.get(operation_key)
                        inherited_action = bool(prior_state is not None and is_completed and not action)
                        effective_action_type = prior_state.action_type if inherited_action else action_type
                        details = _web_event_details(item, action_type=effective_action_type, action=action)
                        count_fields = [f"{label}.{key}" for label, source in (("action", action), ("item", nested), ("wrapper", item))
                                        for key in ("queries", "query", "query_count", "num_queries", "number_of_queries") if key in source]
                        diagnostic_row.update({"inherited_action": inherited_action, "cardinality_fields": count_fields,
                                               "observed_search_queries": details.search_queries,
                                               "search_metadata_known": details.search_metadata_known})
                        if explicit_discovery_limits and count_fields and details.search_queries is None:
                            proc.terminate()
                            raise ProviderError("capability", "Codex emitted malformed discovery query cardinality metadata; the explicit search-query bound cannot be certified.")
                        if not explicit_discovery_limits and details.action_type == "other":
                            # Keep the pre-contract behavior for legacy callers:
                            # ``other`` contributes to the aggregate web-action
                            # cap but only an explicit ``search`` action consumes
                            # the historical search counter.
                            details = _WebEventDetails(
                                details.action_type,
                                web_actions=details.web_actions,
                                search_queries=0,
                                search_metadata_known=details.search_metadata_known,
                                query=details.query,
                            )
                        elif not explicit_discovery_limits and details.action_type == "search" and details.search_queries is None:
                            # The historical path charged each explicit search
                            # action once even when an older CLI omitted or
                            # malformed its query field.
                            details = _WebEventDetails(
                                details.action_type,
                                web_actions=details.web_actions,
                                search_queries=1,
                                search_metadata_known=False,
                                query=details.query,
                            )
                        state = web_operations.get(operation_key)

                        # A terminal record with no action metadata completes a
                        # known started operation.  Retain the started metadata;
                        # do not reinterpret an open/find operation as a search.
                        action_metadata_present = bool(action)
                        if state is not None and is_completed and not action_metadata_present:
                            if explicit_discovery_limits and state.awaiting_terminal_search_metadata and details.search_queries is None:
                                proc.terminate()
                                raise ProviderError(
                                    "capability",
                                    "Codex completed an ambiguous web-search event (opaque web-search lifecycle "
                                    "event) without countable query metadata; the explicit discovery search-query "
                                    "bound cannot be certified.",
                                )
                            # Missing metadata inherits the start's cardinality;
                            # explicitly supplied terminal metadata can increase it.
                            if details.search_queries is None and not details.search_metadata_known:
                                details = _WebEventDetails(
                                    state.action_type,
                                    web_actions=state.web_actions,
                                    search_queries=state.search_queries,
                                    search_metadata_known=state.search_metadata_known,
                                    query=state.query,
                                )
                        elif details.search_queries is None and explicit_discovery_limits:
                            # The current CLI can start a web search with an
                            # opaque ``other`` action and identify it as a
                            # countable search only in the terminal record.  Keep
                            # one provisional web-action reservation at start,
                            # then require that terminal metadata before success.
                            if is_started and details.action_type == "other":
                                details = _WebEventDetails(
                                    details.action_type,
                                    web_actions=details.web_actions,
                                    search_queries=0,
                                    search_metadata_known=False,
                                    query=details.query,
                                )
                            else:
                                proc.terminate()
                                raise ProviderError(
                                    "capability",
                                    "Codex emitted an ambiguous web-search event (opaque web-search lifecycle "
                                    "event); the explicit discovery search-query bound cannot be enforced from "
                                    "this event shape (missing action/query cardinality metadata).",
                                )

                        if state is None:
                            state = _WebOperationState(
                                action_type=effective_action_type.casefold(),
                                web_actions=details.web_actions,
                                search_queries=max(0, details.search_queries or 0),
                                search_metadata_known=details.search_metadata_known,
                                query=details.query,
                                started=is_started,
                                completed=is_completed,
                                awaiting_terminal_search_metadata=(
                                    explicit_discovery_limits
                                    and is_started
                                    and details.action_type == "other"
                                    and not details.search_metadata_known
                                ),
                            )
                            web_operations[operation_key] = state
                        else:
                            # Use the maximum cardinality observed across the
                            # lifecycle pair.  This avoids double charging a
                            # duplicate, while a terminal batch can still add
                            # queries that were not exposed at start.
                            if action_type:
                                state.action_type = action_type.casefold()
                            state.web_actions = max(state.web_actions, details.web_actions)
                            state.search_queries = max(state.search_queries, details.search_queries or 0)
                            if is_completed:
                                state.search_metadata_known = details.search_metadata_known
                            else:
                                state.search_metadata_known = state.search_metadata_known and details.search_metadata_known
                            if state.query is None and details.query:
                                state.query = details.query
                            state.started = state.started or is_started
                            state.completed = state.completed or is_completed
                            if is_completed and details.search_queries is not None:
                                state.awaiting_terminal_search_metadata = False

                        # Charge only the delta observed for this operation.  A
                        # start record charges before the next provider action when
                        # metadata is available; a completed-only record can only
                        # be checked after the CLI has already performed it.
                        # ``charged`` totals live separately from the lifecycle
                        # state so a later terminal record can reconcile a larger
                        # batch without charging a duplicate pair twice.
                        action_delta = max(0, state.web_actions - state.charged_actions)
                        search_delta = max(0, state.search_queries - state.charged_searches)
                        if action_delta:
                            discovery_web_actions += action_delta
                            state.charged_actions += action_delta
                        if search_delta:
                            discovery_searches += search_delta
                            state.charged_searches += search_delta
                        diagnostic_row.update({"action_delta": action_delta, "search_delta": search_delta,
                                               "operation_search_queries": state.search_queries,
                                               "operation_web_actions": state.web_actions,
                                               "awaiting_terminal_metadata": state.awaiting_terminal_search_metadata})
                        if discovery_web_actions > resolved_discovery_limits.max_web_actions:
                            diagnostic_row["outcome"] = "web_action_limit"
                            proc.terminate()
                            phase = "started" if is_started and not is_completed else "terminal"
                            raise ProviderError(
                                "capability",
                                "Codex exceeded the bounded discovery web-action limit "
                                f"({resolved_discovery_limits.max_web_actions}) at {phase}; "
                                "the CLI event stream cannot undo work already reported by a terminal event.",
                            )
                        if discovery_searches > resolved_discovery_limits.max_search_queries:
                            diagnostic_row["outcome"] = "search_query_limit"
                            proc.terminate()
                            phase = "started" if is_started and not is_completed else "terminal"
                            raise ProviderError(
                                "capability",
                                "Codex exceeded the bounded discovery search-query limit "
                                f"({resolved_discovery_limits.max_search_queries}) at {phase}; "
                                "the CLI event stream cannot undo work already reported by a terminal event.",
                            )

                        diagnostic_row["outcome"] = "accepted"
                        # Surface one lifecycle event per completed search
                        # operation.  The query is protocol metadata only and is
                        # bounded before it can enter the provider event stream.
                        if is_completed and state.search_queries > 0 and not state.emitted:
                            state.emitted = True
                            events.append(ProviderEvent("web_search", state.query, None))
                            if on_event:
                                maybe = on_event(events[-1])
                                if asyncio.iscoroutine(maybe):
                                    await maybe
                        continue
                    finally:
                        diagnostic_row.update({"search_queries_after": discovery_searches, "web_actions_after": discovery_web_actions})
                        if diagnostics:
                            diagnostics.record(diagnostic_row)
                if any(term in (item_type + " " + nested_type).lower() for term in ("tool", "command_execution", "function_call", "file_search", "local_shell", "mcp", "computer", "shell", "browser", "image_generation")):
                    proc.terminate()
                    raise ProviderError("capability", "Codex requested a disabled tool; attempt rejected.")
                if item_type in {"error", "turn.failed"} or nested_type == "error":
                    message = _provider_error_text(item)
                    if message:
                        provider_messages.append(message)
                        provider_messages = provider_messages[-20:]
                    if item_type == "turn.failed":
                        turn_failed = True
                if item_type in {"turn.started", "turn.completed", "turn.failed", "thread.started", "error"}:
                    usage = item.get("usage") if isinstance(item.get("usage"), dict) else None
                    event = ProviderEvent(item_type, None, usage)
                    events.append(event)
                    if on_event:
                        maybe = on_event(event)
                        if asyncio.iscoroutine(maybe):
                            await maybe
            returncode = await proc.wait()
            stderr = _bounded_diagnostic_text((await stderr_task).decode("utf-8", errors="replace"))
            failure_text = stderr + " " + " ".join(provider_messages)
            if explicit_discovery_limits and any(
                state.awaiting_terminal_search_metadata
                for state in web_operations.values()
            ):
                raise ProviderError(
                    "capability",
                    "Codex ended an ambiguous web-search event (opaque web-search lifecycle event) after an "
                    "opaque start without countable terminal query metadata; the explicit discovery "
                    "search-query bound cannot be certified.",
                )
            if returncode or turn_failed:
                raise _classify_failure(failure_text, returncode)
            if not result_path.is_file():
                raise _classify_failure(failure_text, 0)
            try:
                payload = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raise ProviderError("invalid_output", "Codex output artifact is not valid JSON.")
            if not isinstance(payload, dict):
                raise ProviderError("invalid_output", "Codex output artifact must be a JSON object.")
            usage = next((event.usage for event in reversed(events) if event.usage), None)
            diagnostics_outcome = "completed"
            return ProviderResult(payload, usage, tuple(events), True)
        except ProviderError as exc:
            diagnostics_outcome = _safe_protocol_name(exc.kind, {"capability", "auth", "quota", "context_limit", "cancelled", "execution", "invalid_output", "unavailable"})
            raise
        finally:
            # A cancelled coroutine must reap the child before releasing the
            # attempt slot, otherwise a late CLI result could still consume
            # subscription allowance and race the durable cancellation state.
            if proc.returncode is None:
                try:
                    proc.terminate()
                    await asyncio.wait_for(proc.wait(), timeout=2)
                except (asyncio.TimeoutError, ProcessLookupError):
                    try:
                        proc.kill()
                        await asyncio.wait_for(proc.wait(), timeout=2)
                    except Exception:
                        pass
            if not stderr_task.done():
                stderr_task.cancel()
                try:
                    await stderr_task
                except BaseException:
                    pass
            self._processes.pop(attempt_id, None)
            if diagnostics:
                diagnostics.finish(diagnostics_outcome, searches=discovery_searches, actions=discovery_web_actions,
                                   pending=sum(state.awaiting_terminal_search_metadata for state in web_operations.values()))

    async def execute(
        self,
        attempt_id: str,
        prompt: str,
        config: ModelConfig,
        schema: dict[str, Any],
        workdir: Path,
        on_event: EventCallback | None = None,
        *,
        discovery_stage: bool = False,
        discovery_limits: DiscoveryLimits | Mapping[str, Any] | None = None,
    ) -> ProviderResult:
        workdir.mkdir(parents=True, exist_ok=True)
        schema_path = workdir / "output-schema.json"
        result_path = workdir / "result.json"
        schema_path.write_text(json.dumps(normalize_output_schema(schema)), encoding="utf-8")
        try:
            result = await asyncio.wait_for(
                self._execute_raw(
                    attempt_id,
                    prompt,
                    config,
                    schema_path,
                    result_path,
                    workdir,
                    on_event,
                    discovery_stage=discovery_stage,
                    discovery_limits=discovery_limits,
                ),
                timeout=max(30, self.config.codex_timeout_seconds),
            )
            # A successful real structured generation is stronger evidence
            # than the small probe and validates this exact pair for the
            # provider menu and subsequent dispatches.
            self._preflight[(config.model, config.reasoning_effort)] = True
            return result
        except asyncio.TimeoutError:
            await self.cancel(attempt_id)
            raise ProviderError("timeout", "Codex execution exceeded its configured timeout.", retryable=True)

    async def cancel(self, attempt_id: str) -> dict[str, Any]:
        proc = self._processes.get(attempt_id)
        if not proc:
            return {"cancelled": False, "status": "not_running", "reason": "Provider process is no longer running."}
        try:
            proc.terminate()
        except ProcessLookupError:
            return {"cancelled": True, "status": "terminated"}
        return {"cancelled": True, "status": "termination_requested"}

    async def usage(self, attempt_id: str) -> dict[str, Any] | None:
        return None
