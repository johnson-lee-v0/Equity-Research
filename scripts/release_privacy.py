#!/usr/bin/env python3
"""Value-blind privacy gate for source releases.

The gate reads private inputs locally and keeps only opaque marker variants in
memory.  Its report contains paths, categories, and counts; it never includes
the private values that caused a match.  It is intentionally stdlib-only so a
clean checkout can run the same check before installing the application.
"""

from __future__ import annotations

import argparse
import csv
import fnmatch
import hashlib
import json
import os
import re
import shlex
import sqlite3
import stat
import subprocess
import sys
import urllib.parse
import zipfile
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Set, Tuple


# These names are a publication policy.  They are kept here as well as in the
# shell packager so that a manually supplied manifest cannot bypass the gate.
PRIVATE_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".venv",
        "__pycache__",
        ".pytest_cache",
        ".vite",
        ".cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        ".runtime",
        "coverage",
        "node_modules",
        "dist",
        "build",
        "data",
        "runtime",
        "exp",
        "tmp",
        "backups",
        "exports",
    }
)

PRIVATE_FILE_PATTERNS = (
    ".DS_Store",
    "*.pyc",
    "*.pyo",
    "*.log",
    "*.sqlite3",
    "*.sqlite3-*",
    "*.sqlite",
    "*.sqlite-*",
    "*.db",
    "*.db-*",
    "*.wal",
    "*.shm",
    "*.tsbuildinfo",
    "*.zip",
    "*.tar",
    "*.tar.gz",
    "*.tgz",
    "*.bak",
    ".env",
    ".env.*",
    ".envrc",
    "praw.ini",
    "providers.json",
    "credentials.json",
    "private-data.local.*",
    "PRD.html",
)

_ALLOWED_EXAMPLE_FILES = frozenset({".env.example"})

_MONEY_KEY_RE = re.compile(
    r"(?:amount|balance|cash|cost(?:_?basis)?|market(?:_?value)?|price|quantity|"
    r"budget|limit|notional|proceeds|fee|funding|capital|allocation|risk|equity|"
    r"exposure|buying[_.-]?power|net[_.-]?liquidation|dollars?)",
    re.IGNORECASE,
)
_CREDENTIAL_KEY_RE = re.compile(
    r"(?:^|[_\.-])(?:api[_.-]?(?:key|secret|token)|client[_.-]?(?:id|secret)|"
    r"access[_.-]?(?:key|token)|refresh[_.-]?token|secret(?:[_.-]?key)?|"
    r"password|credential|authorization|private[_.-]?key)(?:$|[_\.-])",
    re.IGNORECASE,
)
_PERSONAL_KEY_RE = re.compile(
    r"(?:account(?:[_.-]?(?:id|number|name|label)|[_.-]?snapshot[_.-]?id)?|"
    r"portfolio(?:[_.-]?id)?|"
    r"owner|email|phone|client(?:[_.-]?id)?|user(?:[_.-]?id)?|profile)",
    re.IGNORECASE,
)

_ACCOUNT_TABLES = frozenset(
    {
        "accounts",
        "balance_observations",
        "positions",
        "transactions",
    }
)
_PRIVATE_SNAPSHOT_COLUMNS = frozenset(
    {
        ("runs", "input_snapshot_json"),
        ("attempt_decision_inputs", "context_json"),
        ("case_decision_versions", "calculation_context_json"),
    }
)
_POLICY_CONTEXT_KEYS = re.compile(
    r"^(?:portfolio_policy|policy|risk_settings|limits|account_restrictions)$",
    re.IGNORECASE,
)
_TRUSTED_OBSERVATION_STATUSES = frozenset(
    {"confirmed", "verified", "imported", "reconciled", "complete", "completed"}
)
_NUMERIC_RE = re.compile(
    r"^[\s]*(?:\(?[+$€£¥]?\s*)?[+-]?(?:\d+(?:[\d,]*\d)?(?:\.\d*)?|\.\d+)"
    r"(?:[eE][+-]?\d+)?\s*(?:\)\s*)?$"
)


@dataclass
class _Marker:
    """Opaque variants for one private value.

    ``fingerprint`` is never returned to callers.  Keeping variants grouped
    means a comma-formatted value is counted once even when several spellings
    occur in a release file.
    """

    fingerprint: str
    variants: Set[bytes] = field(default_factory=set)
    numbers: Set[Decimal] = field(default_factory=set)


@dataclass
class PrivateMarkers:
    values: Dict[str, Dict[str, _Marker]] = field(
        default_factory=lambda: {
            "monetary_value": {},
            "credential_value": {},
            "personal_value": {},
        }
    )
    input_paths: List[str] = field(default_factory=list)
    input_errors: List[str] = field(default_factory=list)

    def add(self, category: str, value: Any, *, numeric: bool = False) -> None:
        if value is None or category not in self.values:
            return
        if numeric:
            variants = _numeric_variants(value)
        else:
            variants = _text_variants(value)
        if not variants:
            return
        # Fingerprints are used only as an internal deduplication key.  The
        # report deliberately has no value-derived identifiers.
        canonical = min(variants)
        fingerprint = hashlib.sha256(canonical).hexdigest()
        marker = self.values[category].setdefault(
            fingerprint, _Marker(fingerprint=fingerprint)
        )
        marker.variants.update(variants)
        if numeric and (parsed := _parse_decimal(value)) is not None:
            marker.numbers.add(parsed[0])


def _text_variants(value: Any) -> Set[bytes]:
    if isinstance(value, bytes):
        raw = value.strip()
        text = raw.decode("utf-8", errors="ignore").strip()
    else:
        text = str(value).strip()
        raw = text.encode("utf-8", errors="ignore")
    if not text or len(raw) < 4:
        return set()
    # A JSON parser normally removes quotes, but these forms make direct
    # database/config values safe to compare as well.
    variants = {raw}
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        inner = text[1:-1].strip()
        if len(inner.encode("utf-8")) >= 4:
            variants.add(inner.encode("utf-8"))
    # Email and credential comparisons are useful across harmless case
    # changes, while the original spelling remains available too.
    lowered = text.lower().encode("utf-8", errors="ignore")
    if lowered != raw and len(lowered) >= 4:
        variants.add(lowered)
    return variants


def _parse_decimal(value: Any) -> Optional[Tuple[Decimal, str]]:
    text = str(value).strip()
    if not text:
        return None
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        text = text[1:-1].strip()
    if not _NUMERIC_RE.fullmatch(text):
        return None
    negative_parentheses = text.startswith("(") and text.endswith(")")
    clean = text.strip("() ").replace(",", "")
    clean = re.sub(r"^[+$€£¥]\s*", "", clean)
    try:
        number = Decimal(clean)
    except InvalidOperation:
        return None
    if not number.is_finite():
        return None
    if negative_parentheses:
        number = -abs(number)
    return number, clean


def _numeric_variants(value: Any) -> Set[bytes]:
    parsed = _parse_decimal(value)
    if parsed is None:
        return set()
    number, clean = parsed
    # Ignore boolean/flag-like values and trivial IDs.  Direct money fields
    # with a fractional part still qualify at low values.
    if abs(number) < Decimal("2") and number == number.to_integral_value():
        return set()

    raw_text = str(value).strip()
    raw = raw_text.encode("utf-8", errors="ignore")
    variants: Set[str] = {clean, raw_text}
    try:
        fixed_full = format(number, "f")
    except (ValueError, InvalidOperation):
        fixed_full = clean
    source_mantissa = clean.lower().split("e", 1)[0]
    source_precision = (
        len(source_mantissa.split(".", 1)[1])
        if "." in source_mantissa
        else 0
    )
    # Keep high precision ledger values distinct from a rounded public test
    # constant.  For ordinary currency values, compact and comma-grouped
    # spellings remain accepted; high precision values also get a two-decimal
    # spelling only when that rounding is exact.
    if source_precision <= 2:
        fixed = fixed_full.rstrip("0").rstrip(".") if "." in fixed_full else fixed_full
        variants.add(fixed or "0")
    else:
        fixed = fixed_full
        if number == number.quantize(Decimal("0.01")):
            variants.add(format(number.quantize(Decimal("0.01")), "f"))
    variants.add(fixed)

    def _add_grouped(form: str) -> None:
        sign = ""
        unsigned = form
        if unsigned.startswith(("-", "+")):
            sign, unsigned = unsigned[0], unsigned[1:]
        if "." in unsigned:
            integer, fraction = unsigned.split(".", 1)
            grouped_integer = f"{int(integer):,}"
            variants.add(f"{sign}{grouped_integer}.{fraction}")
        else:
            variants.add(f"{sign}{int(unsigned):,}")

    _add_grouped(fixed)
    if source_precision > 2 and number == number.quantize(Decimal("0.01")):
        _add_grouped(format(number.quantize(Decimal("0.01")), "f"))

    encoded = {item.encode("utf-8", errors="ignore") for item in variants if item}
    if raw and len(raw) >= 2:
        encoded.add(raw)
    return {item for item in encoded if item}


def path_policy_category(path: str) -> Optional[str]:
    """Return the publication category for a POSIX-style relative path."""

    try:
        pure = PurePosixPath(path)
    except (TypeError, ValueError):
        return "private_path"
    if pure.is_absolute() or "\x00" in path:
        return "private_path"
    parts = pure.parts
    if any(part in {"", "."} for part in parts):
        # Empty segments are harmless but malformed archive names should be
        # treated as unsafe by the caller.
        pass
    if ".." in parts:
        return "private_path"
    if any(part in PRIVATE_DIRECTORY_NAMES for part in parts):
        return "private_path"
    name = parts[-1] if parts else ""
    if name in _ALLOWED_EXAMPLE_FILES:
        return None
    if any(fnmatch.fnmatch(name, pattern) for pattern in PRIVATE_FILE_PATTERNS):
        return "private_path"
    return None


def _safe_json(value: Any) -> Optional[Any]:
    if not isinstance(value, (str, bytes, bytearray)):
        return value
    try:
        if isinstance(value, (bytes, bytearray)):
            value = bytes(value).decode("utf-8")
        return json.loads(value)
    except (UnicodeDecodeError, TypeError, ValueError):
        return None


def _is_money_key(key: str) -> bool:
    return bool(_MONEY_KEY_RE.search(key))


def _is_credential_key(key: str) -> bool:
    normalized = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key.strip())
    if normalized.lower() in {"token", "secret", "password", "credential", "authorization"}:
        return True
    if re.fullmatch(r"(?:auth|access|refresh)[_.-]?token", normalized, re.IGNORECASE):
        return True
    return bool(_CREDENTIAL_KEY_RE.search(normalized))


def _is_personal_key(key: str) -> bool:
    # Full matching avoids treating public schema fields such as
    # ``account_type`` or ``user_agent`` as private identifiers.
    return bool(_PERSONAL_KEY_RE.fullmatch(key.strip()))


_ACCOUNT_CONTEXT_KEYS = re.compile(
    r"^(?:account|accounts|account_snapshot|portfolio|portfolio_snapshot|"
    r"positions|position|holdings|holding|balances|balance|cash|funding)$",
    re.IGNORECASE,
)


def _walk_private_snapshot_json(
    markers: PrivateMarkers,
    value: Any,
    *,
    in_account_context: bool = False,
    observation_values_allowed: bool = True,
) -> None:
    """Collect values only below an explicitly named account subtree."""

    parsed = _safe_json(value)
    if parsed is not None and parsed is not value:
        _walk_private_snapshot_json(
            markers,
            parsed,
            in_account_context=in_account_context,
            observation_values_allowed=observation_values_allowed,
        )
        return
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key)
            if _POLICY_CONTEXT_KEYS.fullmatch(key_text):
                # Policy caps and account labels are public decision inputs,
                # not observed account balances.
                continue
            child_in_account_context = in_account_context or bool(
                _ACCOUNT_CONTEXT_KEYS.fullmatch(key_text)
            )
            if child_in_account_context and key_text.lower() in {
                "id",
                "account_id",
                "account_number",
            }:
                markers.add("personal_value", child)
            elif (
                child_in_account_context
                and observation_values_allowed
                and _is_money_key(key_text)
            ):
                if isinstance(child, (Mapping, list, tuple)):
                    _walk_private_snapshot_json(
                        markers,
                        child,
                        in_account_context=child_in_account_context,
                        observation_values_allowed=observation_values_allowed,
                    )
                else:
                    markers.add("monetary_value", child, numeric=True)
            elif child_in_account_context and _is_personal_key(key_text):
                if isinstance(child, (Mapping, list, tuple)):
                    _walk_private_snapshot_json(
                        markers,
                        child,
                        in_account_context=child_in_account_context,
                        observation_values_allowed=observation_values_allowed,
                    )
                else:
                    markers.add("personal_value", child)
            else:
                _walk_private_snapshot_json(
                    markers,
                    child,
                    in_account_context=child_in_account_context,
                    observation_values_allowed=observation_values_allowed,
                )
        return
    if isinstance(value, (list, tuple)):
        for child in value:
            _walk_private_snapshot_json(
                markers,
                child,
                in_account_context=in_account_context,
                observation_values_allowed=observation_values_allowed,
            )


def _quoted_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _table_rows_with_metadata(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    metadata_columns: Sequence[str],
) -> Iterator[Tuple[Any, Dict[str, Any]]]:
    selected = [_quoted_identifier(column)]
    present = [column]
    for metadata_column in metadata_columns:
        if metadata_column not in present:
            selected.append(_quoted_identifier(metadata_column))
            present.append(metadata_column)
    query = "SELECT %s FROM %s" % (
        ", ".join(selected),
        _quoted_identifier(table),
    )
    try:
        for row in conn.execute(query):
            yield row[0], {
                str(name).lower(): row[index + 1]
                for index, name in enumerate(present[1:])
            }
    except sqlite3.DatabaseError:
        return


def _is_real_namespace(namespace: Optional[Any]) -> bool:
    if namespace is None or str(namespace).strip() == "":
        return True
    return str(namespace).strip().lower() == "real"


def _open_database_read_only(path: Path) -> Optional[sqlite3.Connection]:
    # ``mode=ro`` is the normal path.  Some SQLite builds refuse a WAL
    # database with that flag when its directory is locked; immutable mode is
    # still read-only and gives the gate a stable last-checkpoint view.  The
    # final query_only fallback is also write-protected and exists for older
    # platform SQLite builds.
    encoded = urllib.parse.quote(str(path), safe="/")
    for uri in (
        "file:%s?mode=ro" % encoded,
        "file:%s?mode=ro&immutable=1" % encoded,
    ):
        try:
            conn = sqlite3.connect(uri, uri=True, timeout=2.0)
            conn.execute("SELECT 1")
            return conn
        except sqlite3.DatabaseError:
            try:
                conn.close()
            except (UnboundLocalError, AttributeError):
                pass
            continue
    try:
        conn = sqlite3.connect(str(path), timeout=2.0)
        conn.execute("PRAGMA query_only=ON")
        conn.execute("SELECT 1")
        return conn
    except sqlite3.DatabaseError:
        return None


def _collect_database(markers: PrivateMarkers, db_path: Path) -> None:
    try:
        conn = _open_database_read_only(db_path)
    except sqlite3.DatabaseError:
        conn = None
    if conn is None:
        markers.input_errors.append("database_unreadable")
        return
    markers.input_paths.append(str(db_path))
    try:
        try:
            table_rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        except sqlite3.DatabaseError:
            markers.input_errors.append("database_unreadable")
            return
        for (table,) in table_rows:
            try:
                columns = [row[1] for row in conn.execute(
                    "PRAGMA table_info(%s)" % _quoted_identifier(str(table))
                )]
            except sqlite3.DatabaseError:
                continue
            table_name = str(table)
            table_lower = table_name.lower()
            # Only these tables contain user-owned account observations.  A
            # public research row can also have a column called ``price`` or
            # ``token`` and must never become a private release marker.
            direct_private_table = table_lower in _ACCOUNT_TABLES
            for column in columns:
                column_text = str(column)
                lower_column = column_text.lower()
                if direct_private_table and _is_money_key(column_text):
                    category = "monetary_value"
                elif direct_private_table and (
                    _is_personal_key(column_text)
                    or (
                        table_lower == "accounts"
                        and lower_column in {"id", "account_number"}
                    )
                    or (lower_column == "account_id")
                ):
                    category = "personal_value"
                else:
                    category = None
                is_snapshot = (table_lower, lower_column) in _PRIVATE_SNAPSHOT_COLUMNS
                is_json = is_snapshot
                if not category and not is_json:
                    continue
                metadata_columns = [
                    str(candidate)
                    for candidate in columns
                    if str(candidate).lower()
                    in {"namespace", "status", "source_id", "import_id"}
                ]
                for value, metadata in _table_rows_with_metadata(
                    conn, table_name, column_text, metadata_columns
                ):
                    if direct_private_table and not _is_real_namespace(
                        metadata.get("namespace")
                    ):
                        continue
                    if category == "monetary_value":
                        markers.add(category, value, numeric=True)
                    elif category == "personal_value":
                        markers.add(category, value)
                    if is_json:
                        _walk_private_snapshot_json(markers, value)
    finally:
        conn.close()


def _looks_like_env_key(key: str) -> bool:
    return bool(_CREDENTIAL_KEY_RE.search(key))


def _collect_env_file(markers: PrivateMarkers, path: Path) -> None:
    try:
        raw = path.read_bytes()
    except OSError:
        markers.input_errors.append("private_input_unreadable")
        return
    markers.input_paths.append(str(path))
    text = raw.decode("utf-8", errors="replace")
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[7:].lstrip()
        if "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not _looks_like_env_key(key):
            continue
        try:
            parts = shlex.split(value, comments=False, posix=True)
            value = parts[0] if parts else ""
        except ValueError:
            value = value.strip("'\"")
        markers.add("credential_value", value)

    # A config file may be JSON or INI.  Parsing it structurally avoids
    # treating public provider names and URLs as secret markers.
    parsed = _safe_json(text)
    if parsed is not None:
        _walk_credential_json(markers, parsed)
    elif path.suffix.lower() in {".ini", ".cfg", ".conf"}:
        for line in text.splitlines():
            if "=" not in line or line.lstrip().startswith(("#", ";")):
                continue
            key, value = line.split("=", 1)
            if _looks_like_env_key(key.strip()):
                markers.add("credential_value", value.strip().strip("'\""))


def _walk_credential_json(markers: PrivateMarkers, value: Any) -> None:
    """Collect only values below credential-named JSON keys."""

    parsed = _safe_json(value)
    if parsed is not None and parsed is not value:
        _walk_credential_json(markers, parsed)
        return
    if isinstance(value, Mapping):
        for key, child in value.items():
            if _is_credential_key(str(key)):
                markers.add("credential_value", child)
            elif isinstance(child, (Mapping, list, tuple)):
                _walk_credential_json(markers, child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _walk_credential_json(markers, child)


def _collect_environment(markers: PrivateMarkers) -> None:
    for key, value in os.environ.items():
        if _looks_like_env_key(key) and value:
            markers.add("credential_value", value)


def _default_env_paths(private_root: Path) -> List[Path]:
    candidates = [
        private_root / ".env",
        private_root / ".env.local",
        private_root / "backend" / ".env",
        private_root / "config" / "providers.json",
        private_root / "config" / "credentials.json",
        private_root / "providers.json",
        private_root / "credentials.json",
        private_root / "praw.ini",
    ]
    return [path for path in candidates if path.is_file() and path.name != ".env.example"]


def _default_db_paths(data_dir: Path) -> List[Path]:
    if data_dir.is_file():
        return [data_dir]
    if not data_dir.is_dir():
        return []
    candidates: List[Path] = []
    preferred = data_dir / "road2m.sqlite3"
    if preferred.is_file():
        candidates.append(preferred)
    for pattern in ("*.sqlite3", "*.sqlite", "*.db"):
        for path in sorted(data_dir.rglob(pattern)):
            if path.is_file() and path not in candidates:
                candidates.append(path)
    return candidates


def collect_private_markers(
    *,
    private_root: Path,
    data_dir: Optional[Path] = None,
    db_paths: Sequence[Path] = (),
    env_paths: Sequence[Path] = (),
    allow_empty: bool = False,
) -> PrivateMarkers:
    """Derive private markers from local inputs without returning their values."""

    markers = PrivateMarkers()
    selected_db_paths = list(db_paths) if db_paths else _default_db_paths(
        data_dir or (private_root / "data")
    )
    selected_env_paths = list(env_paths) if env_paths else _default_env_paths(private_root)
    for path in selected_db_paths:
        if path.is_file():
            _collect_database(markers, path)
        else:
            markers.input_errors.append("private_input_missing")
    for path in selected_env_paths:
        if path.is_file():
            _collect_env_file(markers, path)
        else:
            markers.input_errors.append("private_input_missing")
    _collect_environment(markers)
    if not selected_db_paths:
        markers.input_errors.append("private_database_missing")
    if not selected_env_paths:
        markers.input_errors.append("private_environment_missing")
    if not allow_empty and (not selected_db_paths or not selected_env_paths):
        # Keep the reason category generic; no local path/value is needed to
        # explain a fail-closed result.
        markers.input_errors.append("private_input_required")
    return markers


def _record_hit(
    hits: Dict[Tuple[str, str], int], path: str, category: str, count: int = 1
) -> None:
    if count <= 0:
        return
    key = (path, category)
    hits[key] = hits.get(key, 0) + count


def _is_boundary_byte(value: int) -> bool:
    return not (value == 95 or 48 <= value <= 57 or 65 <= value <= 90 or 97 <= value <= 122)


def _contains_private_value(content: bytes, marker: _Marker) -> bool:
    for variant in marker.variants:
        if not variant:
            continue
        start = 0
        while True:
            index = content.find(variant, start)
            if index < 0:
                break
            before_ok = index == 0 or _is_boundary_byte(content[index - 1])
            end = index + len(variant)
            after_ok = end == len(content) or _is_boundary_byte(content[end])
            if before_ok and after_ok:
                return True
            start = index + 1
    return False


# Numeric observations must match an entire number, not a component of a
# different decimal, dependency version, IP address or comma-grouped value.
# Keep this distinct from credential/identifier matching, whose punctuation
# can be part of the actual secret.
_NUMBER_TOKEN_RE = re.compile(
    rb"(?<![A-Za-z0-9_.])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+|\.\d+)"
    rb"(?:\.\d+)?(?:[eE][-+]?\d+)?(?![A-Za-z0-9_])"
)
_ACCOUNT_LABEL = (
    rb"(?:cash(?:[_ -]?(?:balance|available))?|balance|buying[_ -]?power|"
    rb"net[_ -]?liquidation|holdings?|positions?|quantity|shares|cost[_ -]?basis|"
    rb"account(?:[_ -]?(?:value|balance|equity))?|portfolio(?:[_ -]?(?:value|cash|equity))?)"
)
_CURRENCY_PREFIX = rb"(?:[$]|\xc2\xa3|\xc2\xa5|\xe2\x82\xac|[A-Z]{3})?"
_VALUE_WRAPPER_PREFIX = rb"(?:(?:Decimal|float|int)\(\s*)?\(?\s*[\"']?\s*" + _CURRENCY_PREFIX + rb"\s*"
_ACCOUNTING_NEGATIVE_PREFIX_RE = re.compile(rb"\(\s*" + _CURRENCY_PREFIX + rb"\s*$", re.IGNORECASE)
_ACCOUNT_VALUE_PREFIX_RE = re.compile(
    rb"\b" + _ACCOUNT_LABEL + rb"[\"']?\s*(?:[:=|]\s*)?"
    + _VALUE_WRAPPER_PREFIX + rb"$", re.IGNORECASE
)
_ACCOUNT_TABLE_HEADER_RE = re.compile(
    rb"^[ A-Za-z_#|,\t-]*\b(?:account|portfolio|holdings?|positions?)\b[ A-Za-z_#|,\t-]*[|,\t][ A-Za-z_#|,\t-]*"
    rb"\b(?:balance|amount|quantity|shares|value|cash|equity)\b[ A-Za-z_#|,\t-]*$", re.IGNORECASE
)
_ACCOUNT_OBJECT_RE = re.compile(
    rb"[\"']?(?:account|portfolio|holding|position|balance)(?:_snapshot)?[\"']?\s*[:=]\s*\{",
    re.IGNORECASE,
)
_AMOUNT_PREFIX_RE = re.compile(
    rb"\b(?:amount|value|price|cost|equity|notional)[\"']?\s*[:=]\s*"
    + _VALUE_WRAPPER_PREFIX + rb"$", re.IGNORECASE
)


def _still_in_object(suffix: bytes) -> bool:
    depth, quote, escaped = 1, None, False
    for char in suffix:
        if quote is not None:
            if escaped:
                escaped = False
            elif char == 92:
                escaped = True
            elif char == quote:
                quote = None
        elif char in (34, 39):
            quote = char
        elif char == 123:
            depth += 1
        elif char == 125:
            depth -= 1
            if depth == 0:
                return False
    return depth > 0


def _account_table_header(line: bytes) -> bool:
    if b'"' in line:
        # Quoted CSV is an actual tabular representation, unlike a TypeScript
        # union or a regular-expression literal containing these field names.
        if b"," not in line or b"|" in line:
            return False
        try:
            cells = next(csv.reader([line.decode("utf-8")], strict=True))
        except (UnicodeDecodeError, csv.Error, StopIteration):
            return False
        line = ",".join(cells).encode()
    return bool(_ACCOUNT_TABLE_HEADER_RE.fullmatch(line))


def _account_number_context(content: bytes, start: int, end: int) -> bool:
    """Short whole counts alone cannot identify private account observations.

    Require a directly associated financial/account label, including labels
    split onto the preceding line and tabular account records. This is not a
    file or value allowlist: the same rule applies to source, archives and Git.
    """
    before = content[max(0, start - 512):start]
    if _ACCOUNT_VALUE_PREFIX_RE.search(before):
        return True
    previous_lines = before.splitlines()[-4:]
    if any(_account_table_header(line) for line in previous_lines):
        return True
    # An unclosed explicitly named account object also binds generic monetary
    # keys. Do not mistake a later unrelated source constant for its contents.
    if _AMOUNT_PREFIX_RE.search(before):
        for account in _ACCOUNT_OBJECT_RE.finditer(before):
            if _still_in_object(before[account.end():]):
                return True
    return False


def _money_numbers(content: bytes) -> Set[Decimal]:
    values: Set[Decimal] = set()
    for match in _NUMBER_TOKEN_RE.finditer(content):
        start, end = match.span()
        # A version/IP token has another dot + digit beyond the otherwise
        # valid first decimal. Reject that token instead of matching a prefix.
        if content[end:end + 1] == b"." and content[end + 1:end + 2].isdigit():
            continue
        parsed = _parse_decimal(match[0].decode("ascii"))
        if parsed is None:
            continue
        value = parsed[0]
        # Parenthesized accounting negatives retain their sign. Function
        # argument parentheses are not accounting-negative syntax.
        prefix_start = max(0, start - 64)
        before = content[prefix_start:start]
        negative_prefix = _ACCOUNTING_NEGATIVE_PREFIX_RE.search(before)
        if negative_prefix and re.match(rb"\s*\)", content[end:end + 64]):
            opening = prefix_start + negative_prefix.start()
            if opening == 0 or _is_boundary_byte(content[opening - 1]):
                value = -abs(value)
        # Three-digit-or-shorter whole values are common counts, limits and
        # UI constants. Their occurrence alone does not identify an account.
        # Precise fractional amounts and larger values remain global checks.
        if abs(value) < 1000 and value == value.to_integral_value() and not _account_number_context(content, start, end):
            continue
        values.add(value)
    return values


def _scan_content(
    content: bytes, display_path: str, markers: PrivateMarkers, hits: Dict[Tuple[str, str], int]
) -> None:
    money_numbers = _money_numbers(content) if markers.values["monetary_value"] else set()
    for category, category_markers in markers.values.items():
        matched = 0
        for marker in category_markers.values():
            if (bool(marker.numbers & money_numbers) if category == "monetary_value" else _contains_private_value(content, marker)):
                matched += 1
        _record_hit(hits, display_path, category, matched)


def _normal_manifest_path(raw_path: str) -> Optional[str]:
    value = raw_path.strip().replace("\\", "/")
    if not value or "\x00" in value:
        return None
    pure = PurePosixPath(value)
    if pure.is_absolute() or ".." in pure.parts:
        return None
    return "/".join(part for part in pure.parts if part not in {"."})


def _resolve_manifest_entry(root: Path, entry: str) -> Optional[Path]:
    normalized = _normal_manifest_path(entry)
    if normalized is None:
        return None
    parts = PurePosixPath(normalized).parts
    if parts and parts[0] == root.name:
        parts = parts[1:]
    if not parts:
        return None
    root_absolute = Path(os.path.abspath(str(root)))
    root_resolved = root.resolve()
    candidate = root_absolute.joinpath(*parts)
    try:
        candidate.relative_to(root_absolute)
    except ValueError:
        return None
    # Keep the unresolved path so callers can reject symlinks before reading
    # their targets.  A target outside the publication root is unsafe even if
    # the link itself has a harmless-looking name.
    try:
        candidate.resolve().relative_to(root_resolved)
    except ValueError:
        return None
    return candidate


def scan_manifest(
    root: Path,
    manifest_path: Path,
    markers: PrivateMarkers,
    hits: Dict[Tuple[str, str], int],
) -> int:
    scanned = 0
    try:
        entries = manifest_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        _record_hit(hits, str(manifest_path), "manifest_unreadable")
        return scanned
    for raw_entry in entries:
        entry = _normal_manifest_path(raw_entry)
        if entry is None:
            _record_hit(hits, raw_entry or "<blank>", "private_path")
            continue
        category = path_policy_category(entry)
        if category:
            _record_hit(hits, entry, category)
        path = _resolve_manifest_entry(root, entry)
        if path is None or not path.is_file():
            _record_hit(hits, entry, "manifest_missing")
            continue
        if path.is_symlink():
            _record_hit(hits, entry, "unsafe_symlink")
            continue
        try:
            content = path.read_bytes()
        except OSError:
            _record_hit(hits, entry, "file_unreadable")
            continue
        _scan_content(content, entry, markers, hits)
        scanned += 1
    return scanned


def scan_tree(
    root: Path,
    markers: PrivateMarkers,
    hits: Dict[Tuple[str, str], int],
) -> int:
    """Scan a clean publication tree and reject private paths in it."""

    scanned = 0
    if not root.is_dir():
        _record_hit(hits, str(root), "tree_missing")
        return scanned
    for current, dirs, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        kept_dirs: List[str] = []
        for name in dirs:
            rel = (current_path / name).relative_to(root).as_posix()
            category = path_policy_category(rel + "/")
            if category:
                _record_hit(hits, rel, category)
                continue
            if (current_path / name).is_symlink():
                _record_hit(hits, rel, "unsafe_symlink")
                continue
            kept_dirs.append(name)
        dirs[:] = kept_dirs
        for name in files:
            path = current_path / name
            rel = path.relative_to(root).as_posix()
            category = path_policy_category(rel)
            if category:
                _record_hit(hits, rel, category)
            if path.is_symlink():
                _record_hit(hits, rel, "unsafe_symlink")
                continue
            try:
                content = path.read_bytes()
            except OSError:
                _record_hit(hits, rel, "file_unreadable")
                continue
            _scan_content(content, rel, markers, hits)
            scanned += 1
    return scanned


def _git_refs(root: Path) -> List[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "for-each-ref", "--format=%(refname)",
             "refs/heads", "refs/tags", "refs/remotes"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return []
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def scan_git_history(
    git_root: Path,
    markers: PrivateMarkers,
    hits: Dict[Tuple[str, str], int],
) -> int:
    """Scan reachable publication objects and the exact staged index blobs.

    Codex checkpoint refs and unreachable loose objects can be very large and
    are not part of a normal publication history, so they are intentionally
    excluded.  A clean repository with no publication refs is a valid empty
    history.
    """

    git_dir = git_root / ".git"
    if not git_dir.exists():
        return 0
    scanned = 0
    refs = _git_refs(git_root)
    object_lines: List[str] = []
    if refs:
        try:
            result = subprocess.run(
                ["git", "-C", str(git_root), "rev-list", "--objects", *refs],
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError:
            result = None
        if result is not None and result.returncode == 0:
            object_lines = result.stdout.splitlines()
        elif result is not None:
            _record_hit(hits, ".git", "git_history_unreadable")
    object_names: Dict[str, List[str]] = {}
    for line in object_lines:
        oid, _, name = line.partition(" ")
        names = object_names.setdefault(oid, [])
        if name:
            names.append(_normal_manifest_path(name) or name)
    for oid, names in object_names.items():
        for normalized_name in names:
            category = path_policy_category(normalized_name)
            if category:
                _record_hit(hits, "git:" + normalized_name, category)
        try:
            type_result = subprocess.run(
                ["git", "-C", str(git_root), "cat-file", "-t", oid],
                check=False,
                capture_output=True,
                text=True,
            )
            if type_result.returncode != 0:
                _record_hit(hits, ".git", "git_history_unreadable")
                continue
            object_type = type_result.stdout.strip()
            if object_type not in {"blob", "commit", "tag"}:
                continue
            blob_result = subprocess.run(
                ["git", "-C", str(git_root), "cat-file", object_type, oid],
                check=False,
                capture_output=True,
            )
        except OSError:
            _record_hit(hits, ".git", "git_history_unreadable")
            break
        if blob_result.returncode == 0:
            if object_type in {"commit", "tag"}:
                # Author/tagger identities and messages are public too. Only
                # object IDs, never subjects or matched text, reach the report.
                _scan_content(blob_result.stdout, f"git:{object_type}:{oid}", markers, hits)
            else:
                for normalized_name in names:
                    _scan_content(blob_result.stdout, "git:" + normalized_name, markers, hits)
                    scanned += 1
        else:
            _record_hit(hits, ".git", "git_history_unreadable")

    # The index may include a private file that has not reached a commit yet.
    try:
        index_result = subprocess.run(
            ["git", "-C", str(git_root), "ls-files", "--stage", "-z"],
            check=False,
            capture_output=True,
        )
    except OSError:
        index_result = None
    if index_result is not None and index_result.returncode == 0:
        for entry in index_result.stdout.split(b"\0"):
            if not entry:
                continue
            metadata, separator, raw_name = entry.partition(b"\t")
            fields = metadata.split()
            if not separator or len(fields) != 3:
                _record_hit(hits, ".git", "git_index_unreadable")
                continue
            mode, raw_oid, stage = fields
            name = raw_name.decode("utf-8", errors="replace")
            normalized_name = _normal_manifest_path(name) or name
            display_path = "git:index:" + normalized_name
            category = path_policy_category(normalized_name)
            if category:
                _record_hit(hits, display_path, category)
            if stage != b"0":
                _record_hit(hits, display_path, "git_index_unmerged")
            # A staged gitlink is a commit in a different repository, outside
            # this source gate's content boundary. Never silently accept it.
            if mode == b"160000":
                _record_hit(hits, display_path, "git_index_gitlink")
                continue
            try:
                staged = subprocess.run(
                    ["git", "-C", str(git_root), "cat-file", "blob", raw_oid.decode("ascii")],
                    check=False, capture_output=True,
                )
            except (OSError, UnicodeDecodeError):
                _record_hit(hits, display_path, "git_index_unreadable")
                continue
            if staged.returncode == 0:
                _scan_content(staged.stdout, display_path, markers, hits)
                scanned += 1
            else:
                _record_hit(hits, display_path, "git_index_unreadable")
    else:
        _record_hit(hits, ".git", "git_index_unreadable")
    return scanned


def _archive_entry_is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0xFFFF
    return stat.S_ISLNK(mode)


def scan_archive(
    archive_path: Path,
    markers: PrivateMarkers,
    hits: Dict[Tuple[str, str], int],
) -> int:
    scanned = 0
    try:
        archive = zipfile.ZipFile(archive_path)
    except (OSError, zipfile.BadZipFile):
        _record_hit(hits, str(archive_path), "archive_unreadable")
        return scanned
    with archive:
        for info in archive.infolist():
            name = info.filename.replace("\\", "/")
            if info.is_dir():
                continue
            normalized = _normal_manifest_path(name)
            display = "archive:" + (normalized or name)
            if normalized is None:
                _record_hit(hits, display, "private_path")
                continue
            category = path_policy_category(normalized)
            if category:
                _record_hit(hits, display, category)
            if _archive_entry_is_symlink(info):
                _record_hit(hits, display, "unsafe_symlink")
                continue
            try:
                content = archive.read(info)
            except (OSError, RuntimeError, KeyError, zipfile.BadZipFile):
                _record_hit(hits, display, "archive_entry_unreadable")
                continue
            _scan_content(content, display, markers, hits)
            scanned += 1
    return scanned


def build_report(
    *,
    root: Path,
    manifest: Optional[Path] = None,
    archive: Optional[Path] = None,
    tree: Optional[Path] = None,
    private_root: Optional[Path] = None,
    data_dir: Optional[Path] = None,
    db_paths: Sequence[Path] = (),
    env_paths: Sequence[Path] = (),
    git_root: Optional[Path] = None,
    allow_empty: bool = False,
    include_git_history: bool = True,
) -> Dict[str, Any]:
    private_root = private_root or root
    markers = collect_private_markers(
        private_root=private_root,
        data_dir=data_dir,
        db_paths=db_paths,
        env_paths=env_paths,
        allow_empty=allow_empty,
    )
    hits: Dict[Tuple[str, str], int] = {}
    scanned = {"manifest_files": 0, "tree_files": 0, "archive_entries": 0, "git_blobs": 0}
    missing_input_reasons = {
        "private_input_missing",
        "private_database_missing",
        "private_environment_missing",
        "private_input_required",
    }
    if markers.input_errors:
        for reason in sorted(set(markers.input_errors)):
            if not allow_empty or reason not in missing_input_reasons:
                _record_hit(hits, str(private_root), reason)
    if manifest is not None:
        scanned["manifest_files"] = scan_manifest(root, manifest, markers, hits)
    if tree is not None:
        scanned["tree_files"] = scan_tree(tree, markers, hits)
    if archive is not None:
        scanned["archive_entries"] = scan_archive(archive, markers, hits)
    if include_git_history:
        scanned["git_blobs"] = scan_git_history(git_root or private_root, markers, hits)
    hit_rows = [
        {"path": path, "category": category, "count": count}
        for (path, category), count in sorted(hits.items())
    ]
    return {
        "pass": not hit_rows,
        "scanned": scanned,
        "hits": hit_rows,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the value-blind source release privacy gate.")
    parser.add_argument("--root", type=Path, required=True, help="publication tree root")
    parser.add_argument("--manifest", type=Path, help="manifest of files included in the archive")
    parser.add_argument("--archive", type=Path, help="archive to inspect")
    parser.add_argument("--tree", type=Path, help="clean tree to inspect")
    parser.add_argument("--private-root", type=Path, help="root containing local private inputs")
    parser.add_argument("--data-dir", type=Path, help="private SQLite data directory")
    parser.add_argument("--db", dest="db_paths", action="append", type=Path)
    parser.add_argument("--env-file", dest="env_paths", action="append", type=Path)
    parser.add_argument("--git-root", type=Path, help="repository whose reachable refs are checked")
    parser.add_argument("--no-git-history", action="store_true")
    parser.add_argument(
        "--allow-empty-private-inputs",
        action="store_true",
        help="explicitly allow a clean install with no local database/config inputs",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.manifest is None and args.archive is None and args.tree is None:
        parser.error("one of --manifest, --archive, or --tree is required")
    report = build_report(
        root=args.root.resolve(),
        manifest=args.manifest.resolve() if args.manifest else None,
        archive=args.archive.resolve() if args.archive else None,
        tree=args.tree.resolve() if args.tree else None,
        private_root=args.private_root.resolve() if args.private_root else None,
        data_dir=args.data_dir.resolve() if args.data_dir else None,
        db_paths=[path.resolve() for path in (args.db_paths or [])],
        env_paths=[path.resolve() for path in (args.env_paths or [])],
        git_root=args.git_root.resolve() if args.git_root else None,
        allow_empty=args.allow_empty_private_inputs,
        include_git_history=not args.no_git_history,
    )
    # json.dumps is restricted to the safe report structure above.
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
