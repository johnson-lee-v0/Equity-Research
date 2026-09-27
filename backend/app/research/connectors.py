"""Bounded, read-only market and Reddit research connectors.

The connector boundary deliberately does not know about the workflow or the
repository.  It turns provider observations into small typed records, keeps
provider metadata (coverage, freshness, pagination and errors) beside those
records, and exposes deterministic source text that a later orchestration
step can pass to :class:`backend.app.schemas.ImportRequest`.

Credentials are resolved from the process environment and private local
configuration files only.  Values never appear in public result objects,
exception text, or log messages.  The default providers are read-only:
Alpaca uses the historical market-data endpoint and Reddit uses PRAW's
``subreddit.new`` listing; no order, vote, comment, submit, or moderation
operation is present in this module.
"""
from __future__ import annotations

import configparser
import hashlib
import json
import math
import os
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import asyncio
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from statistics import pstdev
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence


# Provider limits and defaults are kept as constants so a caller can inspect
# the boundary without importing either SDK.
ALPACA_MAX_PAGE_LIMIT = 10_000
ALPACA_DEFAULT_PAGE_LIMIT = 1_000
REDDIT_MAX_PAGE_LIMIT = 100
REDDIT_MAX_LISTING_ITEMS = 1_000
REDDIT_MAX_FLAIR_CHARS = 200
DEFAULT_REDDIT_SUBREDDIT = "wallstreetbets"
DEFAULT_BACKFILL_DAYS = 7
DEFAULT_MAX_PAGES = 10
DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_RETRIES = 2
DEFAULT_BACKOFF_SECONDS = 0.25
DEFAULT_MAX_BACKOFF_SECONDS = 5.0
DEFAULT_MAX_BODY_CHARS = 100_000
MAX_EXPERIMENT_CONFIG_SCAN_FILES = 5_000
MAX_EXPERIMENT_CONFIG_SCAN_DEPTH = 4

_UTC = timezone.utc
_DECIMAL_QUANTUM = Decimal("0.00000001")
_TOKEN_KEY_NAMES = {
    "alpaca_api_key_id",
    "alpaca_api_secret_key",
    "reddit_client_id",
    "reddit_client_secret",
    "reddit_user_agent",
}


def _utc_now() -> datetime:
    return datetime.now(_UTC)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    current = value if value.tzinfo is not None else value.replace(tzinfo=_UTC)
    return current.astimezone(_UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_datetime(value: datetime | str | float | int | None) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, (float, int)):
        try:
            return datetime.fromtimestamp(float(value), tz=_UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=_UTC)
    candidate = str(value).strip()
    if not candidate:
        return None
    try:
        parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=_UTC)


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not result.is_finite():
        return None
    return result


def _decimal_text(value: Any) -> str | None:
    parsed = _decimal(value)
    if parsed is None:
        return None
    return format(parsed.quantize(_DECIMAL_QUANTUM, rounding=ROUND_HALF_UP), "f")


def _safe_text(value: Any, *, max_chars: int = DEFAULT_MAX_BODY_CHARS) -> tuple[str, bool]:
    if value is None:
        return "", False
    text = str(value)
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


def _redacted_exception_kind(exc: BaseException) -> str:
    """Classify an exception without returning provider exception text.

    Provider exception messages can contain request URLs, response bodies, or
    accidentally echoed credentials.  A stable class category is sufficient
    for UI and retry decisions.
    """

    name = type(exc).__name__.lower()
    module = type(exc).__module__.lower()
    combined = f"{module}.{name}"
    if isinstance(exc, TimeoutError) or "timeout" in combined:
        return "timeout"
    if isinstance(exc, (ConnectionError, OSError)) or any(term in combined for term in ("connection", "requestexception", "network")):
        return "network_error"
    if "ratelimit" in combined or "rate" in name:
        return "rate_limited"
    if "forbidden" in combined or "unauthorized" in combined or "authentication" in combined:
        return "auth_or_forbidden"
    if "notfound" in combined or "not_found" in combined:
        return "not_found"
    return "provider_error"


def _status_from_http(status: int) -> str:
    if status in {401, 403}:
        return "auth_required"
    if status == 429:
        return "rate_limited"
    if 400 <= status < 500:
        return "invalid_request"
    if status >= 500:
        return "provider_error"
    return "ok"


def _credential_alias(key: Any) -> str | None:
    """Map known env/config names to one canonical credential field."""

    if not isinstance(key, str):
        return None
    normalized = re.sub(r"[^A-Z0-9]", "", key.upper())
    aliases = {
        "ALPACAAPIKEY": "alpaca_api_key_id",
        "ALPACAAPIKEYID": "alpaca_api_key_id",
        "APCAAPIKEYID": "alpaca_api_key_id",
        "ALPACASECRETKEY": "alpaca_api_secret_key",
        "ALPACAAPISECRET": "alpaca_api_secret_key",
        "ALPACAAPISECRETKEY": "alpaca_api_secret_key",
        "APCAAPISECRETKEY": "alpaca_api_secret_key",
        "REDDITCLIENTID": "reddit_client_id",
        "PRAWCLIENTID": "reddit_client_id",
        "REDDITCLIENTSECRET": "reddit_client_secret",
        "PRAWCLIENTSECRET": "reddit_client_secret",
        "REDDITUSERAGENT": "reddit_user_agent",
        "PRAWUSERAGENT": "reddit_user_agent",
    }
    return aliases.get(normalized)


@dataclass(frozen=True)
class CredentialSource:
    """Safe inventory entry; it intentionally contains no credential values."""

    path: str
    format: str
    key_names: tuple[str, ...] = ()
    provider_fields: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "format": self.format,
            "key_names": list(self.key_names),
            "provider_fields": list(self.provider_fields),
        }


@dataclass(frozen=True)
class CredentialInventory:
    """Redacted credential discovery information suitable for diagnostics."""

    sources: tuple[CredentialSource, ...] = ()
    environment_key_names: tuple[str, ...] = ()
    resolved_fields: tuple[str, ...] = ()

    @property
    def alpaca_available(self) -> bool:
        return {"alpaca_api_key_id", "alpaca_api_secret_key"}.issubset(self.resolved_fields)

    @property
    def reddit_available(self) -> bool:
        return {"reddit_client_id", "reddit_client_secret", "reddit_user_agent"}.issubset(self.resolved_fields)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sources": [source.to_dict() for source in self.sources],
            "environment_key_names": list(self.environment_key_names),
            "resolved_fields": list(self.resolved_fields),
            "providers": {
                "alpaca": {"available": self.alpaca_available},
                "reddit": {"available": self.reddit_available},
            },
        }


@dataclass(frozen=True, repr=False)
class ProviderCredentials:
    """Private in-memory credentials.

    ``repr`` is intentionally redacted because this object may be attached to
    a long-lived connector and could otherwise leak through a debug trace.
    """

    alpaca_api_key_id: str | None = field(default=None, repr=False)
    alpaca_api_secret_key: str | None = field(default=None, repr=False)
    reddit_client_id: str | None = field(default=None, repr=False)
    reddit_client_secret: str | None = field(default=None, repr=False)
    reddit_user_agent: str | None = field(default=None, repr=False)
    inventory: CredentialInventory = field(default_factory=CredentialInventory)

    def __repr__(self) -> str:  # pragma: no cover - defensive safeguard
        return "ProviderCredentials(<redacted>)"

    @property
    def alpaca_available(self) -> bool:
        return bool(self.alpaca_api_key_id and self.alpaca_api_secret_key)

    @property
    def reddit_available(self) -> bool:
        return bool(self.reddit_client_id and self.reddit_client_secret and self.reddit_user_agent)

    def redacted(self) -> dict[str, Any]:
        return {
            "inventory": self.inventory.to_dict(),
            "providers": {
                "alpaca": {"available": self.alpaca_available},
                "reddit": {"available": self.reddit_available},
            },
        }


@dataclass(frozen=True)
class CredentialMigrationReport:
    destination: str
    source_paths: tuple[str, ...]
    key_names: tuple[str, ...]
    written: bool
    inventory: CredentialInventory

    def to_dict(self) -> dict[str, Any]:
        return {
            "destination": self.destination,
            "source_paths": list(self.source_paths),
            "key_names": list(self.key_names),
            "written": self.written,
            "inventory": self.inventory.to_dict(),
        }


def _parse_dotenv(path: Path) -> dict[str, str]:
    """Parse simple ``KEY=value`` lines without executing shell syntax."""

    result: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeError):
        return result
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        value = value.strip()
        # Remove only matching quote wrappers.  No interpolation, command
        # substitution, escapes, or shell parsing is performed.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        result[key] = value
    return result


def _flatten_config(value: Any) -> Iterator[tuple[str, Any]]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            yield str(key), child
            yield from _flatten_config(child)
    elif isinstance(value, list):
        for child in value:
            yield from _flatten_config(child)


def _parse_private_config(path: Path) -> tuple[dict[str, str], str]:
    """Read known credential keys from JSON/TOML/INI/dotenv safely."""

    suffix = path.suffix.lower()
    if path.name == ".env" or path.name.startswith(".env.") or suffix in {".env", ".dotenv"}:
        return _parse_dotenv(path), "dotenv"
    try:
        if suffix == ".json":
            loaded = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            return {key: str(value) for key, value in _flatten_config(loaded) if value is not None and not isinstance(value, (Mapping, list))}, "json"
        if suffix in {".toml"}:
            try:
                import tomllib
            except ImportError:  # pragma: no cover - Python 3.10 fallback
                return {}, "toml"
            loaded = tomllib.loads(path.read_text(encoding="utf-8", errors="replace"))
            return {key: str(value) for key, value in _flatten_config(loaded) if value is not None and not isinstance(value, (Mapping, list))}, "toml"
        if suffix in {".ini", ".cfg", ".conf"}:
            parser = configparser.ConfigParser(interpolation=None)
            parser.read(path, encoding="utf-8")
            values: dict[str, str] = {}
            values.update({str(k): str(v) for k, v in parser.defaults().items()})
            generic_aliases = {
                "client_id": "REDDIT_CLIENT_ID",
                "client_secret": "REDDIT_CLIENT_SECRET",
                "user_agent": "REDDIT_USER_AGENT",
            }
            # PRAW's conventional ``praw.ini`` uses generic names in a
            # selected site section (often ``[bot1]`` or ``[DEFAULT]``).
            # Accept those names only from a file whose basename is exactly
            # ``praw.ini``; generic ``client_id`` keys in unrelated INI files
            # remain ignored.  First occurrence wins to avoid silently
            # combining credentials from multiple PRAW sites.
            if path.name.casefold() == "praw.ini":
                selected = next(
                    (
                        section
                        for section in parser.sections()
                        if "client_id" in parser[section] or "client_secret" in parser[section]
                    ),
                    None,
                )
                selected_values: Mapping[str, str] = parser[selected] if selected is not None else parser.defaults()
                for generic_name, canonical_name in generic_aliases.items():
                    if generic_name in selected_values:
                        values[canonical_name] = str(selected_values[generic_name])
            for section in parser.sections():
                values.update({str(k): str(v) for k, v in parser[section].items()})
            return values, "ini"
    except (OSError, UnicodeError, ValueError, configparser.Error):
        return {}, suffix.lstrip(".") or "unknown"
    return {}, suffix.lstrip(".") or "unknown"


def _candidate_private_paths(
    *,
    project_root: Path | None = None,
    data_dir: Path | None = None,
    extra_paths: Iterable[str | os.PathLike[str]] = (),
) -> list[Path]:
    root = Path(project_root or Path.cwd()).expanduser().resolve()
    private_data = Path(data_dir or root / "data").expanduser().resolve()
    paths: list[Path] = []

    for env_name in ("ROAD2M_CREDENTIALS_FILE", "ROAD2M_PRIVATE_CONFIG"):
        configured = os.environ.get(env_name)
        if configured and configured.strip():
            paths.append(Path(configured.strip()).expanduser().resolve())
    for candidate in extra_paths:
        paths.append(Path(candidate).expanduser().resolve())

    for base in (root, private_data / "config", root / "data" / "config"):
        paths.extend(
            [
                base / ".env",
                base / ".env.local",
                base / "credentials.json",
                base / "providers.json",
                base / "praw.ini",
            ]
        )

    # The experimental directory may contain a credential file supplied by a
    # developer.  Only filenames that strongly indicate configuration are
    # inspected, and only known credential keys are retained.  This scan is
    # deliberately shallow and capped: ``exp`` can contain large generated
    # research archives and should never make connector construction walk an
    # unbounded tree.  A deeper source can always be supplied explicitly via
    # ``extra_paths``/``source_paths``.
    experiment = root / "exp"
    if experiment.is_dir():
        try:
            root_depth = len(experiment.parts)
            inspected = 0
            for current, directories, filenames in os.walk(experiment):
                depth = len(Path(current).parts) - root_depth
                directories[:] = [
                    directory
                    for directory in directories
                    if directory not in {"node_modules", "dist", "public", "outputs", ".git", "tmp", "runtime"}
                ]
                if depth >= MAX_EXPERIMENT_CONFIG_SCAN_DEPTH:
                    directories[:] = []
                for filename in filenames:
                    inspected += 1
                    if inspected > MAX_EXPERIMENT_CONFIG_SCAN_FILES:
                        directories[:] = []
                        break
                    lower_name = filename.lower()
                    if (
                        lower_name in {".env", ".env.local", ".env.production", "credentials.json", "providers.json", "config.toml", "praw.ini"}
                        or any(token in lower_name for token in ("credential", "secret", "alpaca", "reddit", "praw"))
                    ):
                        paths.append((Path(current) / filename).resolve())
                if inspected > MAX_EXPERIMENT_CONFIG_SCAN_FILES:
                    break
        except OSError:
            pass

    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def _inventory_and_values(
    *,
    project_root: Path | None = None,
    data_dir: Path | None = None,
    extra_paths: Iterable[str | os.PathLike[str]] = (),
) -> tuple[dict[str, str], CredentialInventory]:
    values: dict[str, str] = {}
    source_entries: list[CredentialSource] = []
    environment_names: list[str] = []

    for name, value in os.environ.items():
        canonical = _credential_alias(name)
        if canonical is None:
            continue
        environment_names.append(name)
        if value.strip() and canonical not in values:
            values[canonical] = value.strip()

    for path in _candidate_private_paths(project_root=project_root, data_dir=data_dir, extra_paths=extra_paths):
        if not path.is_file():
            continue
        raw, format_name = _parse_private_config(path)
        found_names: set[str] = set()
        found_fields: set[str] = set()
        for name, value in raw.items():
            canonical = _credential_alias(name)
            if canonical is None:
                continue
            found_names.add(name)
            found_fields.add(canonical)
            if str(value).strip() and canonical not in values:
                values[canonical] = str(value).strip()
        if found_fields:
            source_entries.append(
                CredentialSource(
                    path=str(path),
                    format=format_name,
                    key_names=tuple(sorted(found_names)),
                    provider_fields=tuple(sorted(found_fields)),
                )
            )

    resolved = tuple(sorted(key for key, value in values.items() if value))
    inventory = CredentialInventory(
        sources=tuple(source_entries),
        environment_key_names=tuple(sorted(set(environment_names))),
        resolved_fields=resolved,
    )
    return values, inventory


def resolve_provider_credentials(
    *,
    project_root: Path | None = None,
    data_dir: Path | None = None,
    extra_paths: Iterable[str | os.PathLike[str]] = (),
) -> ProviderCredentials:
    """Resolve credentials with environment values taking precedence.

    This function returns the private object needed by a connector.  Call
    ``redacted()`` or inspect ``inventory`` when presenting status to a user.
    """

    values, inventory = _inventory_and_values(project_root=project_root, data_dir=data_dir, extra_paths=extra_paths)
    return ProviderCredentials(
        alpaca_api_key_id=values.get("alpaca_api_key_id"),
        alpaca_api_secret_key=values.get("alpaca_api_secret_key"),
        reddit_client_id=values.get("reddit_client_id"),
        reddit_client_secret=values.get("reddit_client_secret"),
        reddit_user_agent=values.get("reddit_user_agent"),
        inventory=inventory,
    )


# Short aliases keep the public boundary easy to discover and preserve a
# natural name for callers that only need credential diagnostics.
load_provider_credentials = resolve_provider_credentials


def credential_inventory(
    *,
    project_root: Path | None = None,
    data_dir: Path | None = None,
    extra_paths: Iterable[str | os.PathLike[str]] = (),
) -> CredentialInventory:
    """Return credential paths/key names/presence without returning values."""

    return resolve_provider_credentials(project_root=project_root, data_dir=data_dir, extra_paths=extra_paths).inventory


def migrate_credentials_to_private(
    *,
    destination: str | os.PathLike[str] | None = None,
    project_root: Path | None = None,
    data_dir: Path | None = None,
    source_paths: Iterable[str | os.PathLike[str]] = (),
) -> CredentialMigrationReport:
    """Copy recognized credentials to a private JSON destination atomically.

    The destination defaults to ``data/config/providers.json``.  The file and
    parent directory are owner-only.  Unknown fields from source files are not
    copied, and the returned report contains names and presence only.
    """

    root = Path(project_root or Path.cwd()).expanduser().resolve()
    private_data = Path(data_dir or root / "data").expanduser().resolve()
    target = Path(destination or private_data / "config" / "providers.json").expanduser().resolve()
    values, inventory = _inventory_and_values(project_root=root, data_dir=private_data, extra_paths=source_paths)
    source_paths_seen = tuple(source.path for source in inventory.sources)

    # A credential destination must be local private application state.  This
    # prevents an accidental migration into the checkout, frontend, exp, or a
    # generated source archive.
    forbidden_parts = {".git", "frontend", "exp", "node_modules"}
    if any(part in forbidden_parts for part in target.parts):
        raise ValueError("credential destination must be private application data")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(target.parent, 0o700)
    except OSError:
        pass
    payload = {key: values[key] for key in sorted(_TOKEN_KEY_NAMES) if values.get(key)}
    if not payload:
        return CredentialMigrationReport(
            destination=str(target),
            source_paths=source_paths_seen,
            key_names=tuple(),
            written=False,
            inventory=inventory,
        )

    fd, temporary_name = tempfile.mkstemp(prefix=".providers.", suffix=".tmp", dir=str(target.parent), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, target)
    finally:
        try:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)
        except OSError:
            pass
    return CredentialMigrationReport(
        destination=str(target),
        source_paths=source_paths_seen,
        key_names=tuple(sorted(payload)),
        written=True,
        inventory=inventory,
    )


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    headers: Mapping[str, str]
    payload: Any


HttpGet = Callable[[str, Mapping[str, str], Mapping[str, str], float], HttpResponse]


def _default_http_get(url: str, params: Mapping[str, str], headers: Mapping[str, str], timeout: float) -> HttpResponse:
    query = urllib.parse.urlencode([(key, value) for key, value in params.items() if value is not None and value != ""])
    request_url = f"{url}?{query}" if query else url
    request = urllib.request.Request(request_url, method="GET", headers=dict(headers))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(20_000_001)
            payload = json.loads(raw.decode("utf-8", errors="replace"))
            return HttpResponse(
                status_code=int(getattr(response, "status", 200)),
                headers={str(key): str(value) for key, value in response.headers.items()},
                payload=payload,
            )
    except urllib.error.HTTPError as exc:
        # Parse an error body only to determine whether it is JSON.  The body
        # is never copied into an error message or result object.
        try:
            raw_error = exc.read(1_000_001)
            payload = json.loads(raw_error.decode("utf-8", errors="replace")) if raw_error else None
        except (OSError, ValueError, UnicodeError):
            payload = None
        return HttpResponse(
            status_code=int(exc.code),
            headers={str(key): str(value) for key, value in exc.headers.items()} if exc.headers else {},
            payload=payload,
        )


def _header(headers: Mapping[str, str], name: str) -> str | None:
    wanted = name.lower()
    for key, value in headers.items():
        if str(key).lower() == wanted:
            return str(value)
    return None


def _rate_metadata(headers: Mapping[str, str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for header_name, output_name in (
        ("x-ratelimit-limit", "limit"),
        ("x-ratelimit-remaining", "remaining"),
        ("x-ratelimit-reset", "reset"),
        ("retry-after", "retry_after"),
    ):
        value = _header(headers, header_name)
        if value is not None:
            # Preserve only numeric rate headers.  A provider can return
            # arbitrary text in Retry-After; that text is not needed here.
            try:
                result[output_name] = int(value) if re.fullmatch(r"-?\d+", value.strip()) else float(value)
            except ValueError:
                continue
    return result


def _normalise_symbols(symbols: str | Iterable[str]) -> list[str]:
    if isinstance(symbols, str):
        values = symbols.split(",")
    else:
        values = list(symbols)
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        symbol = str(value).strip().upper()
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9._-]{0,14}", symbol):
            raise ValueError("symbols must be short market identifiers")
        if symbol not in seen:
            seen.add(symbol)
            normalized.append(symbol)
    if not normalized:
        raise ValueError("at least one symbol is required")
    if len(normalized) > 100:
        raise ValueError("at most 100 symbols are allowed per request")
    return normalized


def normalize_timeframe(timeframe: str) -> str:
    """Normalize friendly names while preserving Alpaca's documented values."""

    raw = str(timeframe or "").strip().lower().replace(" ", "")
    aliases = {
        "daily": "1Day",
        "day": "1Day",
        "1d": "1Day",
        "1day": "1Day",
        "hourly": "1Hour",
        "hour": "1Hour",
        "1h": "1Hour",
        "1hour": "1Hour",
        "15minute": "15Min",
        "15minutes": "15Min",
        "15min": "15Min",
        "15m": "15Min",
        "15t": "15Min",
        "weekly": "1Week",
        "week": "1Week",
        "1w": "1Week",
        "1week": "1Week",
    }
    if raw in aliases:
        return aliases[raw]
    match = re.fullmatch(r"(\d{1,2})(min|m|t|hour|h)", raw)
    if match:
        amount = int(match.group(1))
        unit = match.group(2)
        if unit in {"min", "m", "t"} and 1 <= amount <= 59:
            return f"{amount}Min"
        if unit in {"hour", "h"} and 1 <= amount <= 23:
            return f"{amount}Hour"
    raise ValueError("timeframe must be daily, hourly, 15minute, or a documented Alpaca timeframe")


def _timeframe_delta(timeframe: str) -> timedelta | None:
    normalized = normalize_timeframe(timeframe)
    match = re.fullmatch(r"(\d+)(Min|Hour)", normalized)
    if match:
        return timedelta(minutes=int(match.group(1))) if match.group(2) == "Min" else timedelta(hours=int(match.group(1)))
    if normalized == "1Day":
        return timedelta(days=1)
    if normalized == "1Week":
        return timedelta(days=7)
    return None


def _validate_limit(limit: int | None, *, maximum: int, default: int) -> int:
    value = default if limit is None else int(limit)
    if value < 1 or value > maximum:
        raise ValueError(f"limit must be between 1 and {maximum}")
    return value


@dataclass(frozen=True)
class MarketBar:
    symbol: str
    timestamp: str
    open: str | None
    high: str | None
    low: str | None
    close: str | None
    volume: str | None
    trade_count: int | None = None
    vwap: str | None = None
    # Completeness is evaluated at collection time from the bar timestamp,
    # requested timeframe, and retrieval time.  ``None`` is retained for bars
    # constructed outside a provider result where that context is unavailable.
    complete: bool | None = None

    @property
    def completed(self) -> bool | None:
        """Alias for consumers that use the adjective form."""

        return self.complete

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "trade_count": self.trade_count,
            "vwap": self.vwap,
            "complete": self.complete,
            "completed": self.complete,
        }


def _bar_completion_from_context(bar: MarketBar, timeframe: str, retrieved_at: datetime) -> bool:
    """Determine completion from provider timestamp and collection context."""

    timestamp = _parse_datetime(bar.timestamp)
    delta = _timeframe_delta(timeframe)
    if timestamp is None or delta is None:
        return False
    current = retrieved_at if retrieved_at.tzinfo is not None else retrieved_at.replace(tzinfo=_UTC)
    return timestamp + delta <= current


def _bar_is_complete(bar: MarketBar, timeframe: str, retrieved_at: datetime) -> bool:
    if bar.complete is not None:
        return bool(bar.complete)
    return _bar_completion_from_context(bar, timeframe, retrieved_at)


@dataclass(frozen=True)
class DirectionCondition:
    name: str
    condition: str
    observed: bool | None
    interpretation: str = "descriptive condition from observed bars; not a forecast"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "condition": self.condition,
            "observed": self.observed,
            "interpretation": self.interpretation,
        }


@dataclass(frozen=True)
class TechnicalSnapshot:
    symbol: str | None
    timeframe: str | None
    sample_count: int
    as_of: str | None
    sma20: str | None
    sma50: str | None
    sma200: str | None
    rsi14: str | None
    atr14: str | None
    log_returns: tuple[str, ...]
    realized_volatility: str | None
    drawdown: str | None
    max_drawdown: str | None
    rolling_high20: str | None
    rolling_low20: str | None
    volume_sma20: str | None
    missing_reasons: Mapping[str, str]
    direction_conditions: tuple[DirectionCondition, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "sample_count": self.sample_count,
            "as_of": self.as_of,
            "sma20": self.sma20,
            "sma50": self.sma50,
            "sma200": self.sma200,
            "rsi14": self.rsi14,
            "atr14": self.atr14,
            "log_returns": list(self.log_returns),
            "realized_volatility": self.realized_volatility,
            "drawdown": self.drawdown,
            "max_drawdown": self.max_drawdown,
            "rolling_high20": self.rolling_high20,
            "rolling_low20": self.rolling_low20,
            "volume_sma20": self.volume_sma20,
            "missing_reasons": dict(self.missing_reasons),
            "direction_conditions": [item.to_dict() for item in self.direction_conditions],
            "indicator_methods": {
                "rsi14": "simple trailing 14-period average gains/losses; Wilder smoothing is not used",
                "atr14": "simple trailing 14-period mean true range; Wilder smoothing is not used",
                "realized_volatility": "population standard deviation of observed log returns; no annualization",
            },
            "interpretation": "descriptive technical conditions from the supplied bars; indicators do not establish future outcomes",
        }


def _full_mean(values: Sequence[Decimal], window: int) -> Decimal | None:
    if len(values) < window:
        return None
    return sum(values[-window:], Decimal(0)) / Decimal(window)


def _format_metric(value: Decimal | float | None, *, places: str = "0.00000000") -> str | None:
    if value is None:
        return None
    try:
        decimal = value if isinstance(value, Decimal) else Decimal(str(value))
        return format(decimal.quantize(Decimal(places), rounding=ROUND_HALF_UP), "f")
    except (InvalidOperation, ValueError):
        return None


def compute_technicals(
    bars: Sequence[MarketBar],
    *,
    symbol: str | None = None,
    timeframe: str | None = None,
    completed_only: bool = False,
) -> TechnicalSnapshot:
    """Compute descriptive indicators from complete, ordered close bars.

    Indicators requiring more observations remain ``None`` with an explicit
    reason.  This prevents a short sample from being presented as a complete
    SMA/RSI/ATR history.
    """

    # An explicitly incomplete provider bar is never eligible for a
    # descriptive indicator.  ``completed_only`` is available to callers
    # that have annotated bars with a collection-time completion decision;
    # bars with ``complete=None`` remain usable for standalone calculations.
    eligible = [item for item in bars if item.complete is True] if completed_only else [item for item in bars if item.complete is not False]
    ordered = sorted(eligible, key=lambda item: item.timestamp)
    closes: list[Decimal] = []
    volumes: list[Decimal] = []
    timestamps: list[str] = []
    for item in ordered:
        close = _decimal(item.close)
        if close is None or close <= 0:
            continue
        closes.append(close)
        volume = _decimal(item.volume)
        volumes.append(volume if volume is not None and volume >= 0 else Decimal(0))
        timestamps.append(item.timestamp)
    count = len(closes)
    missing: dict[str, str] = {}

    def metric_mean(name: str, window: int) -> str | None:
        value = _full_mean(closes, window)
        if value is None:
            missing[name] = f"requires at least {window} valid close bars; received {count}"
        return _format_metric(value)

    sma20 = metric_mean("sma20", 20)
    sma50 = metric_mean("sma50", 50)
    sma200 = metric_mean("sma200", 200)

    changes = [current - previous for previous, current in zip(closes, closes[1:])]
    gains = [change if change > 0 else Decimal(0) for change in changes]
    losses = [-change if change < 0 else Decimal(0) for change in changes]
    rsi14: str | None = None
    if len(changes) < 14:
        missing["rsi14"] = f"requires at least 15 valid close bars; received {count}"
    else:
        average_gain = sum(gains[-14:], Decimal(0)) / Decimal(14)
        average_loss = sum(losses[-14:], Decimal(0)) / Decimal(14)
        if average_loss == 0:
            rsi = Decimal(100) if average_gain > 0 else Decimal(50)
        else:
            rsi = Decimal(100) - (Decimal(100) / (Decimal(1) + average_gain / average_loss))
        rsi14 = _format_metric(rsi, places="0.00000001")

    atr14: str | None = None
    true_ranges: list[Decimal] = []
    for index, item in enumerate(ordered):
        high, low, close = _decimal(item.high), _decimal(item.low), _decimal(item.close)
        previous_close = _decimal(ordered[index - 1].close) if index else None
        if high is None or low is None or close is None or high < low:
            continue
        values = [high - low]
        if previous_close is not None:
            values.extend([abs(high - previous_close), abs(low - previous_close)])
        true_ranges.append(max(values))
    if len(true_ranges) < 14:
        missing["atr14"] = f"requires at least 14 valid true ranges; received {len(true_ranges)}"
    else:
        atr14 = _format_metric(sum(true_ranges[-14:], Decimal(0)) / Decimal(14))

    log_returns: list[str] = []
    numeric_returns: list[float] = []
    for previous, current in zip(closes, closes[1:]):
        try:
            value = math.log(float(current / previous))
        except (ValueError, ZeroDivisionError, OverflowError):
            continue
        numeric_returns.append(value)
        log_returns.append(_format_metric(value, places="0.00000001") or "0.00000000")
    realized_volatility: str | None = None
    if len(numeric_returns) < 2:
        missing["realized_volatility"] = f"requires at least 3 valid close bars; received {count}"
    else:
        realized_volatility = _format_metric(pstdev(numeric_returns))

    running_high: Decimal | None = None
    drawdowns: list[Decimal] = []
    for close in closes:
        running_high = close if running_high is None else max(running_high, close)
        drawdowns.append((close - running_high) / running_high if running_high else Decimal(0))
    drawdown = _format_metric(drawdowns[-1] if drawdowns else None)
    max_drawdown = _format_metric(min(drawdowns) if drawdowns else None)
    if not drawdowns:
        missing["drawdown"] = "requires at least one valid close bar"
        missing["max_drawdown"] = "requires at least one valid close bar"

    rolling_high20: str | None = None
    rolling_low20: str | None = None
    if count < 20:
        reason = f"requires at least 20 valid close bars; received {count}"
        missing["rolling_high20"] = reason
        missing["rolling_low20"] = reason
    else:
        rolling_high20 = _format_metric(max(closes[-20:]))
        rolling_low20 = _format_metric(min(closes[-20:]))

    volume_sma20: str | None = None
    if len(volumes) < 20:
        missing["volume_sma20"] = f"requires at least 20 valid volume bars; received {len(volumes)}"
    else:
        volume_sma20 = _format_metric(sum(volumes[-20:], Decimal(0)) / Decimal(20))

    last_close = closes[-1] if closes else None
    conditions: list[DirectionCondition] = []

    def comparison(name: str, expression: str, indicator: str | None, predicate: bool | None) -> None:
        conditions.append(DirectionCondition(name=name, condition=expression, observed=predicate if indicator is not None else None))

    comparison("close_vs_sma20", "latest close > SMA20", sma20, bool(last_close is not None and _decimal(sma20) is not None and last_close > _decimal(sma20)))
    comparison("close_vs_sma50", "latest close > SMA50", sma50, bool(last_close is not None and _decimal(sma50) is not None and last_close > _decimal(sma50)))
    comparison("close_vs_sma200", "latest close > SMA200", sma200, bool(last_close is not None and _decimal(sma200) is not None and last_close > _decimal(sma200)))
    comparison("sma20_vs_sma50", "SMA20 > SMA50", sma20 if sma50 is not None else None, bool(_decimal(sma20) is not None and _decimal(sma50) is not None and _decimal(sma20) > _decimal(sma50)))
    comparison("rsi14_overbought", "RSI14 >= 70", rsi14, bool(_decimal(rsi14) is not None and _decimal(rsi14) >= Decimal("70")))
    comparison("rsi14_oversold", "RSI14 <= 30", rsi14, bool(_decimal(rsi14) is not None and _decimal(rsi14) <= Decimal("30")))

    return TechnicalSnapshot(
        symbol=symbol or (ordered[0].symbol if ordered else None),
        timeframe=normalize_timeframe(timeframe) if timeframe else None,
        sample_count=count,
        as_of=timestamps[-1] if timestamps else None,
        sma20=sma20,
        sma50=sma50,
        sma200=sma200,
        rsi14=rsi14,
        atr14=atr14,
        log_returns=tuple(log_returns),
        realized_volatility=realized_volatility,
        drawdown=drawdown,
        max_drawdown=max_drawdown,
        rolling_high20=rolling_high20,
        rolling_low20=rolling_low20,
        volume_sma20=volume_sma20,
        missing_reasons=missing,
        direction_conditions=tuple(conditions),
    )


def _research_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Keep local credential diagnostics out of durable research source text."""

    return {key: value for key, value in metadata.items() if key != "credential_status"}


@dataclass(frozen=True)
class MarketBarsResult:
    provider: str
    status: str
    capability: str
    bars: tuple[MarketBar, ...]
    metadata: Mapping[str, Any]
    error: str | None = None
    technicals: Mapping[str, TechnicalSnapshot] = field(default_factory=dict)

    @property
    def items(self) -> tuple[MarketBar, ...]:
        """Compatibility alias for callers that consume generic result items."""

        return self.bars

    def __iter__(self) -> Iterator[MarketBar]:
        return iter(self.bars)

    def __len__(self) -> int:
        return len(self.bars)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "status": self.status,
            "capability": self.capability,
            "bars": [bar.to_dict() for bar in self.bars],
            "metadata": dict(self.metadata),
            "error": self.error,
            "technicals": {key: value.to_dict() for key, value in self.technicals.items()},
        }

    def canonical_source_text(self) -> str:
        lines = [
            "Road2M canonical research source",
            json.dumps(
                {
                    "provider": self.provider,
                    "source_type": "market_bars",
                    "status": self.status,
                    "metadata": _research_metadata(self.metadata),
                },
                sort_keys=True,
                ensure_ascii=False,
            ),
        ]
        lines.extend(json.dumps(bar.to_dict(), sort_keys=True, ensure_ascii=False) for bar in self.bars)
        if self.technicals:
            lines.append(json.dumps({"technicals": {key: value.to_dict() for key, value in self.technicals.items()}}, sort_keys=True, ensure_ascii=False))
        if self.error:
            lines.append(json.dumps({"error": self.error}, sort_keys=True, ensure_ascii=False))
        return "\n".join(lines)

    def to_source_text(self) -> str:
        return self.canonical_source_text()

    def as_import_payload(self, *, namespace: str | None = None, title: str | None = None, source_url: str | None = None, idempotency_key: str | None = None) -> dict[str, Any]:
        body = self.canonical_source_text()
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        payload: dict[str, Any] = {
            "kind": "evidence",
            "title": title or f"Alpaca {self.metadata.get('timeframe', 'market')} bars",
            "content": body,
            "source_url": source_url or "https://data.alpaca.markets/v2/stocks/bars",
            "publication_at": self.metadata.get("latest_bar_timestamp"),
            "observed_at": self.metadata.get("retrieved_at"),
            "idempotency_key": idempotency_key or f"alpaca:bars:{digest}",
        }
        if namespace is not None:
            payload["namespace"] = namespace
        return payload

    def as_import_request(self, *, namespace: str = "real", title: str | None = None, source_url: str | None = None, idempotency_key: str | None = None) -> Any:
        from ..schemas import ImportRequest

        return ImportRequest(**self.as_import_payload(namespace=namespace, title=title, source_url=source_url, idempotency_key=idempotency_key))


def _parse_market_bar(symbol: str, raw: Mapping[str, Any]) -> MarketBar | None:
    timestamp = _parse_datetime(raw.get("t") if "t" in raw else raw.get("timestamp"))
    if timestamp is None:
        return None
    open_value = _decimal_text(raw.get("o", raw.get("open")))
    high_value = _decimal_text(raw.get("h", raw.get("high")))
    low_value = _decimal_text(raw.get("l", raw.get("low")))
    close_value = _decimal_text(raw.get("c", raw.get("close")))
    volume_value = _decimal_text(raw.get("v", raw.get("volume")))
    if any(value is None for value in (open_value, high_value, low_value, close_value, volume_value)):
        return None
    trade_count_raw = raw.get("n", raw.get("trade_count"))
    try:
        trade_count = int(trade_count_raw) if trade_count_raw is not None else None
    except (TypeError, ValueError):
        trade_count = None
    return MarketBar(
        symbol=symbol,
        timestamp=_iso(timestamp) or "",
        open=open_value,
        high=high_value,
        low=low_value,
        close=close_value,
        volume=volume_value,
        trade_count=trade_count,
        vwap=_decimal_text(raw.get("vw", raw.get("vwap"))),
    )


def _market_coverage(
    bars: Sequence[MarketBar],
    timeframe: str,
    retrieved_at: datetime,
    *,
    requested_symbols: Sequence[str] = (),
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    per_symbol: dict[str, list[MarketBar]] = {}
    for bar in bars:
        per_symbol.setdefault(bar.symbol, []).append(bar)
    coverage: dict[str, dict[str, Any]] = {}
    gaps: list[dict[str, Any]] = []
    delta = _timeframe_delta(timeframe)
    symbols = sorted(set(requested_symbols) | set(per_symbol))
    for symbol in symbols:
        symbol_bars = per_symbol.get(symbol, [])
        ordered = sorted(symbol_bars, key=lambda item: item.timestamp)
        timestamps = [_parse_datetime(item.timestamp) for item in ordered]
        valid_times = [item for item in timestamps if item is not None]
        latest = valid_times[-1] if valid_times else None
        oldest = valid_times[0] if valid_times else None
        complete_flags = [_bar_is_complete(item, timeframe, retrieved_at) for item in ordered]
        complete_count = sum(1 for item in complete_flags if item)
        incomplete_timestamps = [
            item.timestamp for item, is_complete in zip(ordered, complete_flags) if not is_complete
        ][:100]
        coverage[symbol] = {
            "count": len(ordered),
            "oldest_bar_timestamp": _iso(oldest),
            "latest_bar_timestamp": _iso(latest),
            "missing": not bool(ordered),
            "complete_bar_count": complete_count,
            "incomplete_bar_count": len(ordered) - complete_count,
            "incomplete_bar_timestamps": incomplete_timestamps,
            "latest_bar_completed": bool(ordered and complete_flags[-1]),
            "completion_rule": "bar timestamp plus timeframe duration is before retrieval time; exchange session calendars are not inferred",
        }
        if delta is None:
            continue
        for previous, current in zip(valid_times, valid_times[1:]):
            elapsed = current - previous
            # A one-step deviation can come from provider aggregation or a
            # partial session.  Report only a clear multi-step hole.
            if elapsed > delta * 2:
                missing = max(1, int(elapsed.total_seconds() // delta.total_seconds()) - 1)
                gaps.append(
                    {
                        "symbol": symbol,
                        "from": _iso(previous),
                        "to": _iso(current),
                        "missing_intervals_estimate": missing,
                        "reason": "no bar was returned in the observed interval; session and holiday gaps are not classified",
                    }
                )
    return coverage, gaps[:100]


@dataclass(frozen=True)
class WeeklyBarsResult:
    """Weekly bars derived from completed, consistently adjusted daily bars."""

    provider: str
    status: str
    capability: str
    bars: tuple[MarketBar, ...]
    metadata: Mapping[str, Any]
    error: str | None = None

    @property
    def items(self) -> tuple[MarketBar, ...]:
        return self.bars

    def __iter__(self) -> Iterator[MarketBar]:
        return iter(self.bars)

    def __len__(self) -> int:
        return len(self.bars)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "status": self.status,
            "capability": self.capability,
            "bars": [bar.to_dict() for bar in self.bars],
            "metadata": dict(self.metadata),
            "error": self.error,
        }

    def canonical_source_text(self) -> str:
        lines = [
            "Road2M canonical research source",
            json.dumps(
                {
                    "provider": self.provider,
                    "source_type": "derived_weekly_market_bars",
                    "status": self.status,
                    "metadata": self.metadata,
                },
                sort_keys=True,
                ensure_ascii=False,
            ),
        ]
        lines.extend(json.dumps(bar.to_dict(), sort_keys=True, ensure_ascii=False) for bar in self.bars)
        if self.error:
            lines.append(json.dumps({"error": self.error}, sort_keys=True, ensure_ascii=False))
        return "\n".join(lines)

    def to_source_text(self) -> str:
        return self.canonical_source_text()

    def as_import_payload(
        self,
        *,
        namespace: str | None = None,
        title: str | None = None,
        source_url: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        body = self.canonical_source_text()
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        payload: dict[str, Any] = {
            "kind": "evidence",
            "title": title or "Derived weekly market bars",
            "content": body,
            "source_url": source_url or "https://data.alpaca.markets/v2/stocks/bars",
            "publication_at": self.metadata.get("latest_bar_timestamp"),
            "observed_at": self.metadata.get("retrieved_at"),
            "idempotency_key": idempotency_key or f"derived:weekly-bars:{digest}",
        }
        if namespace is not None:
            payload["namespace"] = namespace
        return payload

    def as_import_request(
        self,
        *,
        namespace: str = "real",
        title: str | None = None,
        source_url: str | None = None,
        idempotency_key: str | None = None,
    ) -> Any:
        from ..schemas import ImportRequest

        return ImportRequest(
            **self.as_import_payload(
                namespace=namespace,
                title=title,
                source_url=source_url,
                idempotency_key=idempotency_key,
            )
        )


def derive_weekly_bars(
    daily_bars: Sequence[MarketBar] | MarketBarsResult,
    *,
    retrieved_at: datetime | None = None,
    adjustment: str | None = None,
    source_adjustment: str | None = None,
    include_current_week: bool = False,
) -> WeeklyBarsResult:
    """Aggregate completed daily bars into UTC ISO weeks.

    The current ISO week is excluded by default because it is a partial
    trading week at collection time.  A caller may pass the adjustment mode
    used by the source result; a mismatch is surfaced as an unavailable
    derived result rather than silently mixing adjusted and unadjusted bars.
    Daily rows remain the source of truth and are never mutated.
    """

    source_metadata: Mapping[str, Any] = {}
    if isinstance(daily_bars, MarketBarsResult):
        source_metadata = daily_bars.metadata
        if source_adjustment is None:
            source_adjustment = source_metadata.get("adjustment")
        if adjustment is None:
            adjustment = source_adjustment
        daily_bars = daily_bars.bars
    adjustment_name = str(adjustment or "raw").strip().lower()
    source_name = None if source_adjustment is None else str(source_adjustment).strip().lower()
    now = retrieved_at or _utc_now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=_UTC)
    metadata: dict[str, Any] = {
        "provider": "derived",
        "source_provider": source_metadata.get("provider"),
        "source_endpoint": source_metadata.get("endpoint"),
        "source_timeframe": "1Day",
        "timeframe": "1Week",
        "retrieved_at": _iso(now),
        "adjustment": adjustment_name,
        "source_adjustment": source_name,
        "adjustment_consistent": source_name is None or source_name == adjustment_name,
        "include_current_week": include_current_week,
        "current_week_partial_excluded": not include_current_week,
        "raw_daily_bar_count": len(daily_bars),
        "complete_daily_bar_count": 0,
        "excluded_incomplete_daily_bar_count": 0,
        "excluded_current_week_daily_bar_count": 0,
        "invalid_daily_bar_count": 0,
        "gap_report": [],
        "coverage": {},
        "caveats": [
            "Weeks use UTC ISO Monday boundaries; exchange session calendars and holidays are not inferred.",
            "Only complete daily bars are eligible; the current ISO week is excluded by default.",
        ],
    }
    if not metadata["adjustment_consistent"]:
        return WeeklyBarsResult(
            provider="derived",
            status="unavailable",
            capability="inconsistent_adjustment",
            bars=tuple(),
            metadata=metadata,
            error="Daily bars use an adjustment mode different from the requested weekly derivation.",
        )

    grouped: dict[tuple[str, int, int], list[tuple[datetime, MarketBar]]] = {}
    current_week = (now.isocalendar().year, now.isocalendar().week)
    for bar in daily_bars:
        timestamp = _parse_datetime(bar.timestamp)
        if timestamp is None:
            metadata["invalid_daily_bar_count"] += 1
            continue
        is_complete = _bar_is_complete(bar, "1Day", now)
        if not is_complete:
            metadata["excluded_incomplete_daily_bar_count"] += 1
            continue
        metadata["complete_daily_bar_count"] += 1
        week = timestamp.isocalendar()
        week_key = (week.year, week.week)
        if not include_current_week and (week.year, week.week) == current_week:
            metadata["excluded_current_week_daily_bar_count"] += 1
            continue
        grouped.setdefault((bar.symbol, week.year, week.week), []).append((timestamp, bar))

    result_bars: list[MarketBar] = []
    for (symbol, year, week), values in sorted(grouped.items()):
        ordered = sorted(values, key=lambda item: item[0])
        valid_rows: list[tuple[datetime, MarketBar, Decimal, Decimal, Decimal, Decimal, Decimal]] = []
        for timestamp, bar in ordered:
            opened = _decimal(bar.open)
            high = _decimal(bar.high)
            low = _decimal(bar.low)
            closed = _decimal(bar.close)
            volume = _decimal(bar.volume)
            if any(value is None for value in (opened, high, low, closed, volume)):
                metadata["invalid_daily_bar_count"] += 1
                continue
            valid_rows.append((timestamp, bar, opened, high, low, closed, volume))
        if not valid_rows:
            continue
        first = valid_rows[0]
        last = valid_rows[-1]
        highs = [row[3] for row in valid_rows]
        lows = [row[4] for row in valid_rows]
        total_volume = sum((row[6] for row in valid_rows), Decimal(0))
        trade_counts = [row[1].trade_count for row in valid_rows if row[1].trade_count is not None]
        vwap_numerator = Decimal(0)
        vwap_denominator = Decimal(0)
        for _, daily, _, _, _, _, volume in valid_rows:
            vwap = _decimal(daily.vwap)
            if vwap is not None and volume > 0:
                vwap_numerator += vwap * volume
                vwap_denominator += volume
        week_start = datetime.fromisocalendar(year, week, 1).replace(tzinfo=_UTC)
        result_bars.append(
            MarketBar(
                symbol=symbol,
                timestamp=_iso(week_start) or "",
                open=_decimal_text(first[2]),
                high=_decimal_text(max(highs)),
                low=_decimal_text(min(lows)),
                close=_decimal_text(last[5]),
                volume=_decimal_text(total_volume),
                trade_count=sum(trade_counts) if trade_counts else None,
                vwap=_decimal_text(vwap_numerator / vwap_denominator) if vwap_denominator else None,
                complete=True,
            )
        )
        metadata["coverage"].setdefault(symbol, []).append(
            {
                "iso_year": year,
                "iso_week": week,
                "daily_bars_used": len(valid_rows),
                "week_start": _iso(week_start),
            }
        )

    metadata["derived_week_count"] = len(result_bars)
    weekly_times = [_parse_datetime(bar.timestamp) for bar in result_bars]
    valid_weekly_times = [item for item in weekly_times if item is not None]
    metadata["oldest_bar_timestamp"] = _iso(min(valid_weekly_times) if valid_weekly_times else None)
    metadata["latest_bar_timestamp"] = _iso(max(valid_weekly_times) if valid_weekly_times else None)
    metadata["excluded_total_daily_bar_count"] = (
        metadata["excluded_incomplete_daily_bar_count"]
        + metadata["excluded_current_week_daily_bar_count"]
    )
    status = "ok" if result_bars else "no_data"
    error = None if result_bars else "No complete daily bars were available for weekly derivation."
    return WeeklyBarsResult(
        provider="derived",
        status=status,
        capability="derived",
        bars=tuple(result_bars),
        metadata=metadata,
        error=error,
    )


aggregate_weekly_bars = derive_weekly_bars


class AlpacaConnector:
    """Read-only historical Alpaca stock-bars connector."""

    provider = "alpaca"
    endpoint = "https://data.alpaca.markets/v2/stocks/bars"
    asset_endpoint = "https://paper-api.alpaca.markets/v2/assets"

    def __init__(
        self,
        credentials: ProviderCredentials | None = None,
        *,
        project_root: Path | None = None,
        data_dir: Path | None = None,
        http_get: HttpGet | None = None,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = _utc_now,
        endpoint: str | None = None,
    ) -> None:
        self.credentials = credentials or resolve_provider_credentials(project_root=project_root, data_dir=data_dir)
        self.http_get = http_get or _default_http_get
        self.sleep = sleep
        self.now = now
        self.endpoint = endpoint or self.endpoint

    def fetch_asset_identity(self, symbol: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> dict[str, Any]:
        """Fetch one bounded asset identity from Alpaca's paper asset API.

        The asset endpoint is deliberately fixed to Alpaca's documented
        paper API.  Only the validated symbol is interpolated into the path;
        callers cannot provide an arbitrary URL or invoke a trading endpoint.
        The returned mapping contains the small identity projection used by
        code-owned verification and no provider response extras.
        """

        if not isinstance(symbol, str):
            raise ValueError("symbol must be a short market identifier")
        normalized_symbols = _normalise_symbols(symbol)
        if len(normalized_symbols) != 1:
            raise ValueError("exactly one symbol is required")
        normalized_symbol = normalized_symbols[0]
        if timeout <= 0:
            raise ValueError("timeout must be positive")

        retrieved_at = _iso(self.now()) or _iso(_utc_now()) or ""
        url = f"{self.asset_endpoint}/{urllib.parse.quote(normalized_symbol, safe='')}"
        provenance = {
            "provider": self.provider,
            "source_type": "alpaca_asset_identity",
            "endpoint": url,
            "requested_symbol": normalized_symbol,
            "retrieved_at": retrieved_at,
        }

        def result(*, capability: str, status: str = "unavailable", error: str | None = None, **identity: Any) -> dict[str, Any]:
            asset_id = identity.get("asset_id")
            asset_class = identity.get("class")
            return {
                "provider": self.provider,
                "status": status,
                "capability": capability,
                "result_status": "ok" if capability == "ready" else "unavailable",
                "asset_id": asset_id,
                "id": asset_id,
                "symbol": identity.get("symbol"),
                "name": identity.get("name"),
                "exchange": identity.get("exchange"),
                "class": asset_class,
                "asset_class": asset_class,
                "retrieved_at": retrieved_at,
                "observed_at": retrieved_at,
                "provenance": dict(provenance),
                "error": error,
            }

        if not self.credentials.alpaca_available:
            return result(capability="missing_credentials", error="Alpaca credentials are unavailable.")

        try:
            response = self.http_get(
                url,
                {},
                {
                    "APCA-API-KEY-ID": self.credentials.alpaca_api_key_id or "",
                    "APCA-API-SECRET-KEY": self.credentials.alpaca_api_secret_key or "",
                    "Accept": "application/json",
                },
                float(timeout),
            )
        except Exception as exc:
            return result(
                capability=_redacted_exception_kind(exc),
                error="Alpaca asset identity could not be retrieved.",
            )

        if response.status_code != 200:
            if response.status_code in {401, 403}:
                capability, error = "auth_required", "Alpaca authentication was rejected."
            elif response.status_code == 404:
                capability, error = "not_found", "Alpaca could not find the requested asset."
            else:
                status = _status_from_http(response.status_code)
                capability = "rate_limited" if status == "rate_limited" else "provider_error" if status == "provider_error" else "invalid_request"
                error = {
                    "rate_limited": "Alpaca rate limit reached.",
                    "invalid_request": "Alpaca rejected the asset request.",
                }.get(status, "Alpaca asset identity could not be retrieved.")
            return result(capability=capability, error=error)

        payload = response.payload
        if not isinstance(payload, Mapping):
            return result(capability="invalid_response", error="Alpaca returned an invalid asset response.")

        def bounded(value: Any, maximum: int) -> str | None:
            if value is None or isinstance(value, (Mapping, list, tuple, set)):
                return None
            text = str(value).strip()
            return text[:maximum] if text else None

        asset_id = bounded(payload.get("id", payload.get("asset_id")), 128)
        asset_symbol = bounded(payload.get("symbol"), 32) or normalized_symbol
        asset_name = bounded(payload.get("name"), 500)
        exchange = bounded(payload.get("exchange"), 64)
        asset_status = bounded(payload.get("status"), 64)
        asset_class = bounded(payload.get("class", payload.get("asset_class")), 64)
        if not asset_id or not asset_name or not asset_status or not asset_class:
            return result(capability="invalid_response", error="Alpaca returned an incomplete asset response.")
        return result(
            capability="ready",
            status=asset_status,
            asset_id=asset_id,
            symbol=asset_symbol,
            name=asset_name,
            exchange=exchange,
            **{"class": asset_class},
        )

    def fetch_bars(
        self,
        symbols: str | Iterable[str],
        timeframe: str,
        *,
        start: datetime | str | None = None,
        end: datetime | str | None = None,
        limit: int | None = None,
        max_pages: int = DEFAULT_MAX_PAGES,
        max_bars: int | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        retries: int = DEFAULT_RETRIES,
        backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
        max_backoff_seconds: float = DEFAULT_MAX_BACKOFF_SECONDS,
        feed: str = "sip",
        adjustment: str = "raw",
        asof: str | None = None,
        include_technicals: bool = True,
    ) -> MarketBarsResult:
        normalized_symbols = _normalise_symbols(symbols)
        normalized_timeframe = normalize_timeframe(timeframe)
        page_limit = _validate_limit(limit, maximum=ALPACA_MAX_PAGE_LIMIT, default=ALPACA_DEFAULT_PAGE_LIMIT)
        if max_pages < 1 or max_pages > 100:
            raise ValueError("max_pages must be between 1 and 100")
        if max_bars is None:
            max_bars = page_limit * max_pages
        if max_bars < 1 or max_bars > ALPACA_MAX_PAGE_LIMIT * 100:
            raise ValueError("max_bars must be between 1 and 1000000")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        retries = max(0, min(int(retries), 5))
        feed = str(feed or "sip").strip().lower()
        if feed not in {"sip", "iex", "boats", "otc"}:
            raise ValueError("feed must be sip, iex, boats, or otc")
        adjustment = str(adjustment or "raw").strip().lower()
        allowed_adjustments = {"raw", "split", "dividend", "spin-off", "all"}
        if adjustment not in allowed_adjustments and not all(part in allowed_adjustments - {"raw", "all"} for part in adjustment.split(",")):
            raise ValueError("adjustment is not supported by Alpaca")
        start_dt, end_dt = _parse_datetime(start), _parse_datetime(end)
        if start is not None and start_dt is None:
            raise ValueError("start must be RFC-3339 or YYYY-MM-DD")
        if end is not None and end_dt is None:
            raise ValueError("end must be RFC-3339 or YYYY-MM-DD")
        if start_dt and end_dt and start_dt > end_dt:
            raise ValueError("start must be before end")

        retrieved_at = self.now()
        if retrieved_at.tzinfo is None:
            retrieved_at = retrieved_at.replace(tzinfo=_UTC)
        metadata: dict[str, Any] = {
            "provider": self.provider,
            "endpoint": self.endpoint,
            "symbols": normalized_symbols,
            "timeframe": normalized_timeframe,
            "start": _iso(start_dt),
            "end": _iso(end_dt),
            "page_limit": page_limit,
            "max_pages": max_pages,
            "max_bars": max_bars,
            "feed": feed,
            "adjustment": adjustment,
            # The stock-bars endpoint is scoped to US equity market data;
            # preserve the denomination as an explicit provider/request fact
            # so downstream scenario code does not guess USD from a bare bar.
            "currency": "USD",
            "currency_basis": "Alpaca stock-bars request scope (US equity bars); provider response does not repeat a per-bar currency field.",
            "asof": asof,
            "sort": "asc",
            "retrieved_at": _iso(retrieved_at),
            "credential_status": self.credentials.redacted(),
            "pages_fetched": 0,
            "pagination_exhausted": False,
            "pagination_stopped_reason": None,
            "rate_limit": {},
        }
        if not self.credentials.alpaca_available:
            metadata["pagination_stopped_reason"] = "missing_credentials"
            return MarketBarsResult(
                provider=self.provider,
                status="unavailable",
                capability="missing_credentials",
                bars=tuple(),
                metadata=self._complete_market_metadata(metadata, (), normalized_timeframe, retrieved_at),
                error="Alpaca credentials are unavailable.",
            )

        bars: list[MarketBar] = []
        invalid_records = 0
        page_token: str | None = None
        seen_tokens: set[str] = set()
        last_rate_metadata: dict[str, Any] = {}
        for page_number in range(max_pages):
            params: dict[str, str] = {
                "symbols": ",".join(normalized_symbols),
                "timeframe": normalized_timeframe,
                "limit": str(page_limit),
                "feed": feed,
                "adjustment": adjustment,
                "sort": "asc",
            }
            if start_dt:
                params["start"] = _iso(start_dt) or ""
            if end_dt:
                params["end"] = _iso(end_dt) or ""
            if asof:
                params["asof"] = asof
            if page_token:
                params["page_token"] = page_token
            response: HttpResponse | None = None
            attempt_error: str | None = None
            for attempt in range(retries + 1):
                try:
                    response = self.http_get(
                        self.endpoint,
                        params,
                        {
                            "APCA-API-KEY-ID": self.credentials.alpaca_api_key_id or "",
                            "APCA-API-SECRET-KEY": self.credentials.alpaca_api_secret_key or "",
                            "Accept": "application/json",
                        },
                        float(timeout),
                    )
                except Exception as exc:
                    attempt_error = _redacted_exception_kind(exc)
                    if attempt >= retries:
                        break
                    delay = min(max_backoff_seconds, max(0.0, backoff_seconds) * (2**attempt))
                    self.sleep(delay)
                    continue
                last_rate_metadata = _rate_metadata(response.headers)
                retryable = response.status_code == 429 or 500 <= response.status_code <= 599
                if retryable and attempt < retries:
                    retry_after = last_rate_metadata.get("retry_after")
                    delay = float(retry_after) if isinstance(retry_after, (int, float)) and retry_after >= 0 else min(max_backoff_seconds, max(0.0, backoff_seconds) * (2**attempt))
                    self.sleep(min(max_backoff_seconds, delay))
                    continue
                break
            metadata["pages_fetched"] = page_number + (1 if response is not None else 0)
            metadata["rate_limit"] = last_rate_metadata
            if response is None:
                metadata["pagination_stopped_reason"] = attempt_error or "network_error"
                return MarketBarsResult(
                    provider=self.provider,
                    status="unavailable" if not bars else "partial",
                    capability="network_error",
                    bars=tuple(bars),
                    metadata=self._complete_market_metadata(metadata, bars, normalized_timeframe, retrieved_at),
                    error="Alpaca data could not be retrieved.",
                )
            status = _status_from_http(response.status_code)
            if response.status_code != 200:
                metadata["pagination_stopped_reason"] = status
                capability = "rate_limited" if status == "rate_limited" else ("auth_required" if status == "auth_required" else "provider_error")
                return MarketBarsResult(
                    provider=self.provider,
                    status=status if not bars else "partial",
                    capability=capability,
                    bars=tuple(bars),
                    metadata=self._complete_market_metadata(metadata, bars, normalized_timeframe, retrieved_at),
                    error={
                        "auth_required": "Alpaca authentication was rejected.",
                        "rate_limited": "Alpaca rate limit reached.",
                        "invalid_request": "Alpaca rejected the market-data request.",
                    }.get(status, "Alpaca data provider returned an error."),
                )
            payload = response.payload
            if not isinstance(payload, Mapping):
                metadata["pagination_stopped_reason"] = "invalid_response"
                return MarketBarsResult(
                    provider=self.provider,
                    status="invalid_response" if not bars else "partial",
                    capability="invalid_response",
                    bars=tuple(bars),
                    metadata=self._complete_market_metadata(metadata, bars, normalized_timeframe, retrieved_at),
                    error="Alpaca returned an invalid bars response.",
                )
            raw_bars = payload.get("bars")
            if not isinstance(raw_bars, Mapping):
                # Single-symbol responses from adjacent Alpaca endpoints are
                # tolerated for callers using a test endpoint.
                raw_bars = {normalized_symbols[0]: raw_bars} if isinstance(raw_bars, list) else {}
            for symbol_key, records in raw_bars.items():
                symbol = str(symbol_key).upper()
                if symbol not in normalized_symbols or not isinstance(records, list):
                    continue
                for record in records:
                    if not isinstance(record, Mapping):
                        invalid_records += 1
                        continue
                    parsed = _parse_market_bar(symbol, record)
                    if parsed is None:
                        invalid_records += 1
                        continue
                    # Keep every provider bar in the raw result, while
                    # annotating each one so indicators and downstream return
                    # consumers can exclude the still-forming interval.
                    bars.append(
                        replace(
                            parsed,
                            complete=_bar_completion_from_context(parsed, normalized_timeframe, retrieved_at),
                        )
                    )
                    if len(bars) >= max_bars:
                        break
                if len(bars) >= max_bars:
                    break
            if len(bars) >= max_bars:
                metadata["pagination_stopped_reason"] = "max_bars"
                break
            next_token = payload.get("next_page_token")
            if not next_token:
                metadata["pagination_exhausted"] = True
                metadata["pagination_stopped_reason"] = "provider_exhausted"
                break
            next_token = str(next_token)
            if next_token in seen_tokens or next_token == page_token:
                metadata["pagination_stopped_reason"] = "cursor_stalled"
                break
            seen_tokens.add(next_token)
            page_token = next_token
        else:
            metadata["pagination_stopped_reason"] = "max_pages"
        metadata["invalid_records"] = invalid_records
        completed_metadata = self._complete_market_metadata(metadata, bars, normalized_timeframe, retrieved_at)
        technicals: dict[str, TechnicalSnapshot] = {}
        if include_technicals:
            by_symbol: dict[str, list[MarketBar]] = {}
            for bar in bars:
                by_symbol.setdefault(bar.symbol, []).append(bar)
            technicals = {
                symbol: compute_technicals(
                    values,
                    symbol=symbol,
                    timeframe=normalized_timeframe,
                    completed_only=True,
                )
                for symbol, values in sorted(by_symbol.items())
            }
            completed_metadata["technical_sample_counts"] = {
                symbol: snapshot.sample_count for symbol, snapshot in technicals.items()
            }
        complete_fetch = bool(
            not completed_metadata.get("missing_symbols")
            and (
                metadata["pagination_exhausted"]
                or metadata["pagination_stopped_reason"] == "provider_exhausted"
            )
        )
        status = "ok" if bars and complete_fetch else ("partial" if bars else "no_data")
        return MarketBarsResult(provider=self.provider, status=status, capability="ready", bars=tuple(bars), metadata=completed_metadata, error=None if bars else "Alpaca returned no market bars.", technicals=technicals)

    async def fetch_bars_async(self, *args: Any, **kwargs: Any) -> MarketBarsResult:
        """Async facade for the existing async workflow boundary."""

        return await asyncio.to_thread(self.fetch_bars, *args, **kwargs)

    async def fetch_async(self, *args: Any, **kwargs: Any) -> MarketBarsResult:
        return await self.fetch_bars_async(*args, **kwargs)

    def fetch_multi_frequency(
        self,
        symbols: str | Iterable[str],
        *,
        frequencies: Iterable[str] = ("daily", "hourly", "15minute"),
        **kwargs: Any,
    ) -> dict[str, MarketBarsResult]:
        """Fetch a small, named set of frequencies with independent metadata."""

        requested = list(frequencies)
        if not requested or len(requested) > 6:
            raise ValueError("frequencies must contain between 1 and 6 timeframes")
        output: dict[str, MarketBarsResult] = {}
        for frequency in requested:
            normalized = normalize_timeframe(frequency)
            output[normalized] = self.fetch_bars(symbols, normalized, **kwargs)
        return output

    async def fetch_multi_frequency_async(
        self,
        symbols: str | Iterable[str],
        *,
        frequencies: Iterable[str] = ("daily", "hourly", "15minute"),
        **kwargs: Any,
    ) -> dict[str, MarketBarsResult]:
        requested = list(frequencies)
        if not requested or len(requested) > 6:
            raise ValueError("frequencies must contain between 1 and 6 timeframes")
        results = await asyncio.gather(*(self.fetch_bars_async(symbols, normalize_timeframe(item), **kwargs) for item in requested))
        return {normalize_timeframe(item): result for item, result in zip(requested, results)}

    @staticmethod
    def _complete_market_metadata(metadata: dict[str, Any], bars: Sequence[MarketBar], timeframe: str, retrieved_at: datetime) -> dict[str, Any]:
        metadata = dict(metadata)
        requested_symbols = tuple(str(item).upper() for item in metadata.get("symbols", ()) if str(item).strip())
        coverage, gaps = _market_coverage(
            bars,
            timeframe,
            retrieved_at,
            requested_symbols=requested_symbols,
        )
        metadata["coverage"] = coverage
        metadata["gap_report"] = gaps
        metadata["missing_symbols"] = [symbol for symbol in requested_symbols if coverage.get(symbol, {}).get("missing")]
        metadata["coverage_complete"] = not metadata["missing_symbols"]
        metadata["bar_eligibility"] = {
            symbol: {
                "raw_count": details.get("count", 0),
                "complete_count": details.get("complete_bar_count", 0),
                "excluded_incomplete_count": details.get("incomplete_bar_count", 0),
                "excluded_timestamps": details.get("incomplete_bar_timestamps", []),
                "technical_input": "complete_bars_only",
            }
            for symbol, details in coverage.items()
        }
        metadata["raw_bar_count"] = len(bars)
        metadata["completed_bar_count"] = sum(
            int(details.get("complete_bar_count", 0)) for details in coverage.values()
        )
        metadata["excluded_incomplete_bar_count"] = sum(
            int(details.get("incomplete_bar_count", 0)) for details in coverage.values()
        )
        all_times = [_parse_datetime(bar.timestamp) for bar in bars]
        valid_times = [item for item in all_times if item is not None]
        metadata["oldest_bar_timestamp"] = _iso(min(valid_times) if valid_times else None)
        metadata["latest_bar_timestamp"] = _iso(max(valid_times) if valid_times else None)
        metadata["freshness"] = {
            "retrieved_at": metadata.get("retrieved_at"),
            "latest_observed_at": metadata.get("latest_bar_timestamp"),
            "latest_age_seconds": (retrieved_at - max(valid_times)).total_seconds() if valid_times else None,
            "status": "observed" if valid_times else "unknown",
        }
        completed_by_symbol = {
            symbol: bool(details.get("latest_bar_completed"))
            for symbol, details in coverage.items()
        }
        metadata["completed_bar"] = {
            "by_symbol": completed_by_symbol,
            "all_symbols": bool(completed_by_symbol) and all(completed_by_symbol.values()),
            "rule": "bar timestamp plus timeframe duration is before retrieval time; exchange session calendars are not inferred",
        }
        metadata["completed_session"] = {
            "status": "unknown",
            "reason": "Alpaca bar response does not provide a per-record regular/extended session label in this connector.",
        }
        metadata["feed_metadata"] = {
            "requested": metadata.get("feed"),
            "entitlement": "unknown",
            "reason": "Alpaca entitlement is provider/account specific and is not reported in historical bar payloads.",
        }
        metadata["adjustment_metadata"] = {
            "requested": metadata.get("adjustment"),
            "applied": "provider_response",
            "source": "Alpaca adjustment request parameter",
        }
        metadata["freshness_status"] = metadata["freshness"]["status"]
        metadata["session"] = {
            "status": "unknown",
            "reason": "Alpaca bar response does not provide a per-record regular/extended session label in this connector.",
        }
        return metadata


# Alias used by callers that prefer the data-domain name.
MarketDataConnector = AlpacaConnector


@dataclass(frozen=True)
class RedditPost:
    post_id: str
    subreddit: str
    title: str
    body: str
    permalink: str
    created_utc: float | None
    score: int | None
    deleted: bool = False
    body_truncated: bool = False
    retention_caveats: tuple[str, ...] = (
        "Reddit posts can be edited or deleted after collection; this is a point-in-time observation.",
        "Listing pagination and Reddit retention limits can leave coverage incomplete.",
    )
    flair: str | None = None

    @property
    def id(self) -> str:
        return self.post_id

    @property
    def created_at(self) -> str | None:
        return _iso(_parse_datetime(self.created_utc))

    @property
    def createdutc(self) -> float | None:
        """Alias matching the provider's canonical ``created_utc`` spelling."""

        return self.created_utc

    def to_dict(self) -> dict[str, Any]:
        result = {
            "id": self.post_id,
            "post_id": self.post_id,
            "subreddit": self.subreddit,
            "title": self.title,
            "body": self.body,
            "permalink": self.permalink,
            "created_utc": self.created_utc,
            "created_at": self.created_at,
            "score": self.score,
            "deleted": self.deleted,
            "body_truncated": self.body_truncated,
            "retention_caveats": list(self.retention_caveats),
        }
        # Keep absent flair out of the canonical payload so previously
        # collected posts retain their existing source hashes.
        if self.flair:
            result["flair"] = self.flair
        return result


@dataclass(frozen=True)
class RedditPostsResult:
    provider: str
    status: str
    capability: str
    posts: tuple[RedditPost, ...]
    metadata: Mapping[str, Any]
    error: str | None = None

    @property
    def items(self) -> tuple[RedditPost, ...]:
        """Compatibility alias for callers that consume generic result items."""

        return self.posts

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "status": self.status,
            "capability": self.capability,
            "posts": [post.to_dict() for post in self.posts],
            "metadata": dict(self.metadata),
            "error": self.error,
        }

    def canonical_source_text(self) -> str:
        lines = [
            "Road2M canonical research source",
            json.dumps(
                {
                    "provider": self.provider,
                    "source_type": "reddit_submission_listing",
                    "status": self.status,
                    "metadata": _research_metadata(self.metadata),
                },
                sort_keys=True,
                ensure_ascii=False,
            ),
        ]
        lines.extend(json.dumps(post.to_dict(), sort_keys=True, ensure_ascii=False) for post in self.posts)
        if self.error:
            lines.append(json.dumps({"error": self.error}, sort_keys=True, ensure_ascii=False))
        return "\n".join(lines)

    def to_source_text(self) -> str:
        return self.canonical_source_text()

    def as_import_payload(self, *, namespace: str | None = None, title: str | None = None, source_url: str | None = None, idempotency_key: str | None = None) -> dict[str, Any]:
        body = self.canonical_source_text()
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        payload: dict[str, Any] = {
            "kind": "evidence",
            "title": title or f"Reddit r/{self.metadata.get('subreddit', DEFAULT_REDDIT_SUBREDDIT)} new posts",
            "content": body,
            "source_url": source_url or f"https://www.reddit.com/r/{self.metadata.get('subreddit', DEFAULT_REDDIT_SUBREDDIT)}/new/",
            "publication_at": self.metadata.get("oldest_covered_at"),
            "observed_at": self.metadata.get("retrieved_at"),
            "idempotency_key": idempotency_key or f"reddit:new:{self.metadata.get('subreddit', DEFAULT_REDDIT_SUBREDDIT)}:{digest}",
        }
        if namespace is not None:
            payload["namespace"] = namespace
        return payload

    def as_import_request(self, *, namespace: str = "real", title: str | None = None, source_url: str | None = None, idempotency_key: str | None = None) -> Any:
        from ..schemas import ImportRequest

        return ImportRequest(**self.as_import_payload(namespace=namespace, title=title, source_url=source_url, idempotency_key=idempotency_key))


def _submission_attr(submission: Any, name: str, default: Any = None) -> Any:
    try:
        return getattr(submission, name, default)
    except Exception:
        return default


def _canonical_post_id(submission: Any) -> str | None:
    raw = _submission_attr(submission, "name") or _submission_attr(submission, "fullname") or _submission_attr(submission, "id")
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    return text if text.lower().startswith("t3_") else f"t3_{text}"


def _canonical_permalink(permalink: Any) -> str:
    text = str(permalink or "").strip()
    if text.startswith("http://") or text.startswith("https://"):
        return text
    return "https://www.reddit.com" + (text if text.startswith("/") else f"/{text}")


def _reddit_flair(value: Any) -> str | None:
    """Return a bounded, safe representation of PRAW's optional flair text."""

    if value is None:
        return None
    try:
        text = str(value)
    except Exception:
        return None
    if not text:
        return None
    return text[:REDDIT_MAX_FLAIR_CHARS]


class RedditConnector:
    """Bounded, PRAW-backed read-only ``new`` listing connector."""

    provider = "praw"

    def __init__(
        self,
        credentials: ProviderCredentials | None = None,
        *,
        project_root: Path | None = None,
        data_dir: Path | None = None,
        reddit_factory: Callable[..., Any] | None = None,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.credentials = credentials or resolve_provider_credentials(project_root=project_root, data_dir=data_dir)
        self.reddit_factory = reddit_factory
        self.now = now

    def _make_reddit(self, timeout: float) -> Any:
        factory = self.reddit_factory
        if factory is None:
            try:
                import praw
            except ImportError as exc:
                raise RuntimeError("praw_dependency_missing") from exc
            factory = praw.Reddit
        # PRAW's documented timeout is passed through its config; a zero
        # ratelimit_seconds makes an unexpected provider wait explicit and
        # keeps this bounded collector from sleeping for an unknown duration.
        reddit = factory(
            client_id=self.credentials.reddit_client_id,
            client_secret=self.credentials.reddit_client_secret,
            user_agent=self.credentials.reddit_user_agent,
            read_only=True,
            check_for_updates=False,
            ratelimit_seconds=0,
            timeout=max(1, int(math.ceil(timeout))),
        )
        try:
            reddit.read_only = True
        except Exception:
            pass
        return reddit

    def fetch_post(self, url: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> RedditPostsResult:
        """Read one explicitly selected submission without advancing the monitor."""
        from urllib.parse import urlsplit

        parsed = urlsplit(str(url).strip())
        match = re.fullmatch(r"/r/([A-Za-z0-9_]{2,50})/comments/([a-z0-9]+)(?:/[^/]*)?/?", parsed.path)
        if parsed.scheme != "https" or parsed.hostname not in {"reddit.com", "www.reddit.com", "old.reddit.com"} or parsed.username or parsed.password or parsed.port or not match:
            raise ValueError("Use an HTTPS Reddit submission link containing /r/community/comments/post-id/.")
        community, requested_id = match.groups()
        metadata = {"subreddit": community.lower(), "retrieved_at": _iso(self.now()), "selection": "explicit_post", "requested_post_id": f"t3_{requested_id}", "read_only": True}
        if not self.credentials.reddit_available:
            return RedditPostsResult(self.provider, "unavailable", "missing_credentials", tuple(), metadata, "Reddit credentials are unavailable.")
        reddit = None
        try:
            reddit = self._make_reddit(timeout)
            submission = reddit.submission(id=requested_id)
            # Force the provider read before retaining fields; do not turn a
            # failed lazy load into a successfully archived empty post.
            title, _ = _safe_text(submission.title, max_chars=20_000)
            post_id = _canonical_post_id(submission)
            actual_community = str(submission.subreddit.display_name)
            if post_id != f"t3_{requested_id}" or actual_community.casefold() != community.casefold():
                raise ValueError("Reddit returned a different submission.")
            body, truncated = _safe_text(submission.selftext, max_chars=DEFAULT_MAX_BODY_CHARS)
            created = float(submission.created_utc)
            post = RedditPost(post_id=post_id, subreddit=actual_community, title=title, body=body,
                permalink=_canonical_permalink(submission.permalink), created_utc=created,
                score=int(submission.score), deleted=body.strip().lower() in {"[removed]", "[deleted]"},
                body_truncated=truncated, flair=_reddit_flair(submission.link_flair_text))
            metadata.update({"oldest_covered_at": post.created_at, "latest_covered_at": post.created_at, "posts_retained": 1})
            return RedditPostsResult(self.provider, "complete", "ready", (post,), metadata)
        except Exception as exc:
            kind = _redacted_exception_kind(exc)
            capability = "auth_required" if kind == "auth_or_forbidden" else "rate_limited" if kind == "rate_limited" else "provider_error"
            return RedditPostsResult(self.provider, "unavailable", capability, tuple(), metadata, "The selected Reddit post could not be retrieved.")
        finally:
            close = getattr(reddit, "close", None)
            if callable(close):
                close()

    def fetch_new_posts(
        self,
        subreddit: str = DEFAULT_REDDIT_SUBREDDIT,
        *,
        cutoff: datetime | str | None = None,
        backfill_days: int | None = None,
        after_cursor: str | None = None,
        max_posts: int = REDDIT_MAX_LISTING_ITEMS,
        max_pages: int = DEFAULT_MAX_PAGES,
        batch_size: int = REDDIT_MAX_PAGE_LIMIT,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> RedditPostsResult:
        name = str(subreddit or DEFAULT_REDDIT_SUBREDDIT).strip()
        if name.lower().startswith("r/"):
            name = name[2:]
        if not re.fullmatch(r"[A-Za-z0-9_]{2,50}", name):
            raise ValueError("subreddit must be a valid name")
        name = name.lower()
        if backfill_days is not None:
            if backfill_days < 1 or backfill_days > 30:
                raise ValueError("backfill_days must be between 1 and 30")
            cutoff = self.now() - timedelta(days=int(backfill_days))
        cutoff_dt = _parse_datetime(cutoff)
        if cutoff is not None and cutoff_dt is None:
            raise ValueError("cutoff must be RFC-3339 or a datetime")
        if max_posts < 1 or max_posts > REDDIT_MAX_LISTING_ITEMS:
            raise ValueError("max_posts must be between 1 and 1000")
        if max_pages < 1 or max_pages > REDDIT_MAX_LISTING_ITEMS // REDDIT_MAX_PAGE_LIMIT:
            raise ValueError("max_pages must be between 1 and 10")
        batch_size = _validate_limit(batch_size, maximum=REDDIT_MAX_PAGE_LIMIT, default=REDDIT_MAX_PAGE_LIMIT)
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        retrieved_at = self.now()
        if retrieved_at.tzinfo is None:
            retrieved_at = retrieved_at.replace(tzinfo=_UTC)
        metadata: dict[str, Any] = {
            "provider": self.provider,
            "subreddit": name,
            "listing": "new",
            "cutoff": _iso(cutoff_dt),
            "backfill_days": backfill_days,
            "cursor_in": after_cursor,
            "batch_size": batch_size,
            "max_pages": max_pages,
            "max_posts": max_posts,
            "listing_item_cap": REDDIT_MAX_LISTING_ITEMS,
            "retrieved_at": _iso(retrieved_at),
            "pages_fetched": 0,
            "pagination_stopped_reason": None,
            "cutoff_reached": False,
            "listing_cap_reached": False,
            "latest_cursor": None,
            "oldest_cursor": None,
            "last_consumed_cursor": after_cursor,
            "durable_cursor": after_cursor,
            # PRAW's ListingGenerator does not expose a provider ``after``
            # token.  Keep this field for a future provider token and persist
            # the durable local watermark separately in last_consumed_cursor.
            "next_cursor": None,
            "latest_covered_at": None,
            "oldest_covered_at": None,
            "gap_report": [],
            "credential_status": self.credentials.redacted(),
            "retention_caveats": [
                "Reddit posts can be edited or deleted after collection; this is a point-in-time observation.",
                "Reddit new listings are paginated at up to 100 items per request and are commonly capped near 1,000 items.",
            ],
        }
        if not self.credentials.reddit_available:
            metadata["pagination_stopped_reason"] = "missing_credentials"
            self._complete_reddit_metadata(metadata, (), None, None, cutoff_dt)
            return RedditPostsResult(provider=self.provider, status="unavailable", capability="missing_credentials", posts=tuple(), metadata=metadata, error="Reddit credentials are unavailable.")
        try:
            reddit = self._make_reddit(timeout)
        except RuntimeError as exc:
            reason = str(exc)
            if reason == "praw_dependency_missing":
                metadata["pagination_stopped_reason"] = "dependency_missing"
                self._complete_reddit_metadata(metadata, (), None, None, cutoff_dt)
                return RedditPostsResult(provider=self.provider, status="unavailable", capability="dependency_missing", posts=tuple(), metadata=metadata, error="PRAW is not installed.")
            metadata["pagination_stopped_reason"] = "provider_error"
            self._complete_reddit_metadata(metadata, (), None, None, cutoff_dt)
            return RedditPostsResult(provider=self.provider, status="unavailable", capability="provider_error", posts=tuple(), metadata=metadata, error="Reddit read-only client could not be created.")
        except Exception as exc:
            reason = _redacted_exception_kind(exc)
            metadata["pagination_stopped_reason"] = reason
            capability = "rate_limited" if reason == "rate_limited" else ("auth_required" if reason == "auth_or_forbidden" else "provider_error")
            self._complete_reddit_metadata(metadata, (), None, None, cutoff_dt)
            return RedditPostsResult(provider=self.provider, status="unavailable", capability=capability, posts=tuple(), metadata=metadata, error="Reddit read-only client could not be created.")

        posts: list[RedditPost] = []
        seen_ids: set[str] = set()
        cursor = after_cursor
        latest_dt: datetime | None = None
        oldest_dt: datetime | None = None
        try:
            for page_number in range(max_pages):
                subreddit_obj = reddit.subreddit(name)
                listing = subreddit_obj.new(limit=batch_size, params={"after": cursor}) if cursor else subreddit_obj.new(limit=batch_size)
                page_count = 0
                page_last_cursor: str | None = None
                stop_for_cutoff = False
                for submission in listing:
                    page_count += 1
                    post_id = _canonical_post_id(submission)
                    if post_id is None:
                        continue
                    # Advance the durable watermark before every bounded
                    # exit, including a cutoff crossing, duplicate, or the
                    # item that reaches max_posts.
                    page_last_cursor = post_id
                    metadata["last_consumed_cursor"] = post_id
                    metadata["durable_cursor"] = post_id
                    if post_id in seen_ids:
                        continue
                    seen_ids.add(post_id)
                    created_raw = _submission_attr(submission, "created_utc")
                    try:
                        created_utc = float(created_raw) if created_raw is not None else None
                    except (TypeError, ValueError):
                        created_utc = None
                    created_dt = _parse_datetime(created_utc)
                    # ``new`` is descending, so once the page reaches cutoff
                    # it is safe to stop without claiming unseen older posts.
                    if cutoff_dt is not None and created_dt is not None and created_dt < cutoff_dt:
                        stop_for_cutoff = True
                        break
                    subreddit_value = _submission_attr(submission, "subreddit")
                    subreddit_name = str(_submission_attr(subreddit_value, "display_name", name) or name)
                    title, _ = _safe_text(_submission_attr(submission, "title", ""), max_chars=20_000)
                    raw_body = _submission_attr(submission, "selftext", _submission_attr(submission, "body", ""))
                    body, body_truncated = _safe_text(raw_body, max_chars=DEFAULT_MAX_BODY_CHARS)
                    deleted = body.strip().lower() in {"[deleted]", "[removed]"} or title.strip().lower() in {"[deleted]", "[removed]"}
                    score_raw = _submission_attr(submission, "score")
                    try:
                        score = int(score_raw) if score_raw is not None else None
                    except (TypeError, ValueError):
                        score = None
                    flair = _reddit_flair(_submission_attr(submission, "link_flair_text"))
                    post = RedditPost(
                        post_id=post_id,
                        subreddit=subreddit_name,
                        title=title,
                        body=body,
                        permalink=_canonical_permalink(_submission_attr(submission, "permalink", "")),
                        created_utc=created_utc,
                        score=score,
                        deleted=deleted,
                        body_truncated=body_truncated,
                        flair=flair,
                    )
                    posts.append(post)
                    if created_dt is not None:
                        latest_dt = created_dt if latest_dt is None else max(latest_dt, created_dt)
                        oldest_dt = created_dt if oldest_dt is None else min(oldest_dt, created_dt)
                    if len(posts) >= max_posts:
                        break
                metadata["pages_fetched"] = page_number + 1
                if posts:
                    metadata["latest_cursor"] = posts[0].post_id
                    metadata["oldest_cursor"] = posts[-1].post_id
                if stop_for_cutoff:
                    metadata["pagination_stopped_reason"] = "cutoff_reached"
                    metadata["cutoff_reached"] = True
                    break
                if len(posts) >= max_posts:
                    metadata["pagination_stopped_reason"] = "max_posts"
                    break
                if page_count == 0 or page_last_cursor is None:
                    metadata["pagination_stopped_reason"] = "listing_exhausted"
                    break
                if page_last_cursor == cursor:
                    metadata["pagination_stopped_reason"] = "cursor_stalled"
                    break
                cursor = metadata.get("last_consumed_cursor") or page_last_cursor
            else:
                metadata["pagination_stopped_reason"] = "max_pages"
            if metadata["pagination_stopped_reason"] == "max_pages" or len(posts) >= REDDIT_MAX_LISTING_ITEMS:
                metadata["listing_cap_reached"] = True
        except Exception as exc:
            reason = _redacted_exception_kind(exc)
            metadata["pagination_stopped_reason"] = reason
            if reason == "rate_limited":
                capability = "rate_limited"
            elif reason == "auth_or_forbidden":
                capability = "auth_required"
            else:
                capability = "provider_error"
            self._complete_reddit_metadata(metadata, posts, latest_dt, oldest_dt, cutoff_dt)
            return RedditPostsResult(provider=self.provider, status="partial" if posts else "unavailable", capability=capability, posts=tuple(posts), metadata=metadata, error="Reddit listing could not be fully retrieved.")

        self._complete_reddit_metadata(metadata, posts, latest_dt, oldest_dt, cutoff_dt)
        complete = bool(
            cutoff_dt is not None
            and (metadata.get("cutoff_reached") or (oldest_dt is not None and oldest_dt <= cutoff_dt))
        )
        if cutoff_dt is None:
            status = "ok" if posts else "no_data"
        elif complete:
            status = "ok" if posts else "no_data"
        else:
            status = "partial" if posts else "no_data"
        error = None if posts or metadata["pagination_stopped_reason"] in {"listing_exhausted", "cutoff_reached"} else "Reddit returned no posts."
        return RedditPostsResult(provider=self.provider, status=status, capability="ready", posts=tuple(posts), metadata=metadata, error=error)

    async def fetch_new_posts_async(self, *args: Any, **kwargs: Any) -> RedditPostsResult:
        return await asyncio.to_thread(self.fetch_new_posts, *args, **kwargs)

    async def fetch_async(self, *args: Any, **kwargs: Any) -> RedditPostsResult:
        return await self.fetch_new_posts_async(*args, **kwargs)

    def backfill(self, subreddit: str = DEFAULT_REDDIT_SUBREDDIT, *, days: int = DEFAULT_BACKFILL_DAYS, **kwargs: Any) -> RedditPostsResult:
        return self.fetch_new_posts(subreddit, backfill_days=days, **kwargs)

    def new_posts(self, subreddit: str = DEFAULT_REDDIT_SUBREDDIT, **kwargs: Any) -> RedditPostsResult:
        return self.fetch_new_posts(subreddit, **kwargs)

    @staticmethod
    def _complete_reddit_metadata(
        metadata: dict[str, Any],
        posts: Sequence[RedditPost],
        latest_dt: datetime | None,
        oldest_dt: datetime | None,
        cutoff_dt: datetime | None,
    ) -> None:
        metadata["latest_covered_at"] = _iso(latest_dt)
        metadata["oldest_covered_at"] = _iso(oldest_dt)
        cutoff_reached = bool(metadata.get("cutoff_reached"))
        metadata["cutoff_reached"] = cutoff_reached
        if cutoff_dt is not None and not cutoff_reached and (oldest_dt is None or oldest_dt > cutoff_dt):
            metadata["gap_report"] = [
                {
                    "kind": "coverage_gap",
                    "requested_from": _iso(cutoff_dt),
                    "covered_from": _iso(oldest_dt),
                    "reason": "bounded Reddit new listing ended before the requested cutoff; listing caps, retention, or unavailable pages may be responsible",
                }
            ]
        else:
            metadata["gap_report"] = []
        metadata["cursor_watermarks"] = {
            "in": metadata.get("cursor_in"),
            "latest": metadata.get("latest_cursor"),
            "oldest": metadata.get("oldest_cursor"),
            "last_consumed": metadata.get("last_consumed_cursor"),
            "durable": metadata.get("durable_cursor"),
            "out": metadata.get("next_cursor"),
        }
        metadata["coverage"] = {
            "requested_from": _iso(cutoff_dt),
            "latest_covered_at": _iso(latest_dt),
            "oldest_covered_at": _iso(oldest_dt),
            "latest_cursor": metadata.get("latest_cursor"),
            "oldest_cursor": metadata.get("oldest_cursor"),
            "last_consumed_cursor": metadata.get("last_consumed_cursor"),
            "durable_cursor": metadata.get("durable_cursor"),
            "next_cursor": metadata.get("next_cursor"),
            "complete": bool(cutoff_dt is not None and (cutoff_reached or (oldest_dt is not None and oldest_dt <= cutoff_dt))),
            "status": "complete" if cutoff_dt is not None and (cutoff_reached or (oldest_dt is not None and oldest_dt <= cutoff_dt)) else ("bounded_new_listing" if cutoff_dt is None else "partial"),
        }


PrawConnector = RedditConnector


__all__ = [
    "ALPACA_DEFAULT_PAGE_LIMIT",
    "ALPACA_MAX_PAGE_LIMIT",
    "DEFAULT_BACKFILL_DAYS",
    "DEFAULT_REDDIT_SUBREDDIT",
    "DirectionCondition",
    "CredentialInventory",
    "CredentialMigrationReport",
    "CredentialSource",
    "HttpResponse",
    "MarketBar",
    "MarketBarsResult",
    "MarketDataConnector",
    "AlpacaConnector",
    "PrawConnector",
    "ProviderCredentials",
    "RedditConnector",
    "RedditPost",
    "RedditPostsResult",
    "TechnicalSnapshot",
    "WeeklyBarsResult",
    "aggregate_weekly_bars",
    "compute_technicals",
    "credential_inventory",
    "derive_weekly_bars",
    "load_provider_credentials",
    "migrate_credentials_to_private",
    "normalize_timeframe",
    "resolve_provider_credentials",
]
