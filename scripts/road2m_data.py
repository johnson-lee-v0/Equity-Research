#!/usr/bin/env python3
"""Private ResearchCouncil data utilities.

The backup command uses sqlite3.Connection.backup so a live database is copied
consistently.  Restore validates the archive, SQLite integrity, foreign keys,
and the core schema before it moves anything into the active data directory.
Export deliberately selects an allowlist of research tables and removes
credential, command, and raw worker fields.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = ROOT / "data"
MAX_ARCHIVE_MEMBER_BYTES = 512 * 1024 * 1024
MAX_EXPORT_EVIDENCE_BYTES = 10 * 1024 * 1024

# These are the durable records described by the implementation contract.
# The exporter skips tables added by a local experiment unless they are added
# to this explicit list and reviewed for credential/log leakage.
EXPORT_TABLES = (
    "agents",
    "accounts",
    "balance_observations",
    "transactions",
    "positions",
    "sources",
    "source_versions",
    "source_documents",
    "fact_claims",
    "research_items",
    "research_versions",
    "decisions",
    "case_decision_versions",
    "attempt_decision_inputs",
    "watch_checks",
    "watch_review_episodes",
    "idea_baselines",
    "idea_outcomes",
    "idea_lifecycle_events",
    "runs",
    "tasks",
    "task_dependencies",
    "task_attempts",
    "outputs",
    "output_claims",
    "output_claim_aliases",
    "events",
    "model_policies",
    "simulations",
    "simulation_events",
    "schedules",
    "research_gaps",
    "research_repairs",
    "research_repair_gaps",
    "memory_retrievals",
    "candidate_simulations",
    "intake_items",
    "intake_cursors",
    "intake_dispatches",
    "intake_runs",
    "intake_monitors",
)

# Never put provider credentials, shell commands, internal thoughts, or raw
# worker output into an open-format export.
SENSITIVE_COLUMN = re.compile(
    r"(?:^|_)(?:api[_-]?key|access[_-]?token|auth|authorization|credential|password|secret|private[_-]?key|shell|command|stdin|stdout|stderr|raw[_-]?log|internal[_-]?thought|thoughts?|prompt|packet)(?:$|_)",
    re.IGNORECASE,
)

REQUIRED_SCHEMA: dict[str, set[str]] = {
    "agents": {"id", "name", "mandate"},
    "accounts": {"id", "namespace"},
    "sources": {"id", "namespace", "content_hash"},
    "runs": {"id", "namespace", "status"},
    "tasks": {"id", "run_id", "agent_id", "status"},
    "outputs": {"id", "task_id", "agent_id"},
}


def utc_now() -> str:
    return (
        dt.datetime.now(dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def path_from_env(data_dir: Path | None = None) -> Path:
    value = os.environ.get("ROAD2M_DATA_DIR")
    return Path(value).expanduser().resolve() if value else (data_dir or DEFAULT_DATA_DIR).resolve()


def reject_symlink(path: Path, label: str) -> None:
    if path.is_symlink():
        raise RuntimeError(f"Refusing a symlink for {label}: {path}")


def ensure_private_directory(path: Path) -> None:
    reject_symlink(path, "private directory")
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def ensure_private_file(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def database_path(data_dir: Path) -> Path:
    return data_dir / "road2m.sqlite3"


def evidence_path(data_dir: Path) -> Path:
    return data_dir / "evidence"


def backup_path(data_dir: Path) -> Path:
    return data_dir / "backups"


def connect_readonly(path: Path) -> sqlite3.Connection:
    reject_symlink(path, "database")
    uri = f"file:{path.as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def table_names(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
    ).fetchall()
    return {str(row[0]) for row in rows}


def table_columns(connection: sqlite3.Connection, table: str) -> list[str]:
    # Table names come only from sqlite_master or the explicit export allowlist.
    return [str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')]


def validate_database(path: Path, *, require_schema: bool = True) -> dict[str, Any]:
    if not path.is_file():
        raise RuntimeError(f"Database file does not exist: {path}")
    reject_symlink(path, "database")
    connection = connect_readonly(path)
    try:
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        foreign_keys = [tuple(row) for row in connection.execute("PRAGMA foreign_key_check")]
        names = table_names(connection)
        missing: dict[str, list[str]] = {}
        if require_schema:
            for table, required_columns in REQUIRED_SCHEMA.items():
                if table not in names:
                    missing[table] = sorted(required_columns)
                    continue
                columns = set(table_columns(connection, table))
                absent = sorted(required_columns - columns)
                if absent:
                    missing[table] = absent
        if integrity.lower() != "ok":
            raise RuntimeError(f"SQLite integrity check failed: {integrity}")
        if foreign_keys:
            raise RuntimeError(f"SQLite foreign-key check failed ({len(foreign_keys)} rows)")
        if missing:
            details = ", ".join(f"{name}: {', '.join(columns)}" for name, columns in missing.items())
            raise RuntimeError(f"Database schema is missing required fields ({details})")
        return {
            "integrity_check": integrity,
            "foreign_key_errors": [],
            "tables": sorted(names),
            "required_schema": {
                table: sorted(columns) for table, columns in REQUIRED_SCHEMA.items()
            },
        }
    finally:
        connection.close()


def sqlite_backup(source: Path, destination: Path) -> None:
    """Copy a database with SQLite's online backup API."""

    reject_symlink(source, "database")
    if not source.is_file():
        raise RuntimeError(f"Database file does not exist: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    reject_symlink(destination, "backup destination")
    if destination.exists():
        raise RuntimeError(f"Refusing to overwrite existing file: {destination}")
    connection = sqlite3.connect(source)
    target: sqlite3.Connection | None = None
    try:
        target = sqlite3.connect(destination)
        connection.backup(target)
        target.commit()
    finally:
        connection.close()
        if target is not None:
            target.close()
    ensure_private_file(destination)


def iter_regular_files(directory: Path) -> Iterable[tuple[Path, str]]:
    if not directory.exists():
        return
    reject_symlink(directory, "evidence directory")
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise RuntimeError(f"Refusing a symlink in evidence: {path}")
        if path.is_file():
            relative = path.relative_to(directory).as_posix()
            yield path, relative


def source_evidence_references(connection: sqlite3.Connection, evidence_dir: Path) -> list[str]:
    """Find source rows that point at an evidence file, when such a column exists."""

    if "sources" not in table_names(connection):
        return []
    columns = table_columns(connection, "sources")
    path_columns = {
        name
        for name in columns
        if name.lower() in {"evidence_path", "file_path", "content_path", "stored_path", "path"}
        or "evidence" in name.lower() and "path" in name.lower()
    }
    hashes = set()
    if "content_hash" in columns:
        for row in connection.execute("SELECT content_hash FROM sources WHERE content_hash IS NOT NULL"):
            hashes.add(str(row[0]))
    references: set[str] = set()
    if path_columns:
        quoted = ", ".join(f'"{column}"' for column in sorted(path_columns))
        for row in connection.execute(f'SELECT {quoted} FROM sources'):
            for value in row:
                if not value:
                    continue
                candidate = PurePosixPath(str(value).replace("\\", "/"))
                if candidate.is_absolute() or ".." in candidate.parts:
                    continue
                target = evidence_dir.joinpath(*candidate.parts)
                if target.is_file():
                    references.add(candidate.as_posix())
    for file_path, relative in iter_regular_files(evidence_dir):
        if file_path.name in hashes or file_path.stem in hashes:
            references.add(relative)
    return sorted(references)


def copy_tree(source: Path, destination: Path) -> None:
    if not source.exists():
        destination.mkdir(parents=True, exist_ok=True)
        return
    reject_symlink(source, "directory")
    if destination.exists():
        raise RuntimeError(f"Refusing to overwrite existing directory: {destination}")
    shutil.copytree(source, destination, symlinks=False)
    for directory in [destination, *[p for p in destination.rglob("*") if p.is_dir()]]:
        try:
            os.chmod(directory, 0o700)
        except OSError:
            pass
    for file_path in destination.rglob("*"):
        if file_path.is_file():
            ensure_private_file(file_path)


def backup_command(data_dir: Path, output: Path | None) -> Path:
    ensure_private_directory(data_dir)
    database = database_path(data_dir)
    evidence = evidence_path(data_dir)
    backups = backup_path(data_dir)
    ensure_private_directory(backups)
    validate = validate_database(database)
    if output is None:
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output = backups / f"road2m-backup-{stamp}.tar.gz"
    output = output.expanduser().resolve()
    if output == database or is_within(output, evidence):
        raise RuntimeError("Backup destination must not be the active database or evidence directory")
    if output.exists():
        raise RuntimeError(f"Refusing to overwrite existing backup: {output}")
    ensure_private_directory(output.parent)

    with tempfile.TemporaryDirectory(prefix=".road2m-backup-", dir=backups) as temporary:
        staging = Path(temporary)
        staged_database = staging / "database" / "road2m.sqlite3"
        staged_database.parent.mkdir(parents=True)
        sqlite_backup(database, staged_database)
        staged_evidence = staging / "evidence"
        copy_tree(evidence, staged_evidence)
        connection = connect_readonly(database)
        try:
            references = source_evidence_references(connection, evidence)
        finally:
            connection.close()
        evidence_records = []
        for file_path, relative in iter_regular_files(evidence):
            evidence_records.append(
                {
                    "path": relative,
                    "size_bytes": file_path.stat().st_size,
                    "sha256": sha256_file(file_path),
                    "referenced": relative in references,
                }
            )
        manifest = {
            "format": "road2m-backup",
            "format_version": 1,
            "created_at": utc_now(),
            "database": {
                "path": "database/road2m.sqlite3",
                "size_bytes": staged_database.stat().st_size,
                "sha256": sha256_file(staged_database),
            },
            "evidence": {
                "path": "evidence",
                "files": evidence_records,
                "referenced_paths": references,
            },
            "schema": validate,
        }
        manifest_path = staging / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        ensure_private_file(manifest_path)
        try:
            with tarfile.open(output, "w:gz") as archive:
                archive.add(manifest_path, arcname="manifest.json", recursive=False)
                archive.add(staged_database, arcname="database/road2m.sqlite3", recursive=False)
                if staged_evidence.exists():
                    archive.add(staged_evidence, arcname="evidence", recursive=False)
                    for path in sorted(staged_evidence.rglob("*")):
                        relative = path.relative_to(staging).as_posix()
                        if path.is_symlink():
                            raise RuntimeError(f"Refusing a symlink in staged evidence: {path}")
                        archive.add(path, arcname=relative, recursive=False)
        except Exception:
            output.unlink(missing_ok=True)
            raise
    ensure_private_file(output)
    print(output)
    return output


def safe_archive_member_path(name: str) -> Path:
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise RuntimeError(f"Unsafe archive member path: {name!r}")
    if any(part in {"", "."} for part in path.parts):
        raise RuntimeError(f"Unsafe archive member path: {name!r}")
    return Path(*path.parts)


def extract_archive(archive_path: Path, staging: Path) -> dict[str, Any]:
    reject_symlink(archive_path, "restore archive")
    if not archive_path.is_file():
        raise RuntimeError(f"Restore archive does not exist: {archive_path}")
    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
        seen_members: set[str] = set()
        for member in members:
            relative = safe_archive_member_path(member.name)
            normalized = relative.as_posix()
            if normalized in seen_members:
                raise RuntimeError(f"Duplicate archive member: {member.name}")
            seen_members.add(normalized)
            if member.issym() or member.islnk() or member.isdev():
                raise RuntimeError(f"Archive links/devices are not allowed: {member.name}")
            if member.size > MAX_ARCHIVE_MEMBER_BYTES:
                raise RuntimeError(f"Archive member is too large: {member.name}")
            destination = staging / relative
            if member.isdir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                raise RuntimeError(f"Unsupported archive member: {member.name}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise RuntimeError(f"Could not read archive member: {member.name}")
            with destination.open("wb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
            ensure_private_file(destination)
    manifest_path = staging / "manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError("Backup manifest.json is missing")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Backup manifest is unreadable: {error}") from error
    if not isinstance(manifest, dict) or manifest.get("format") != "road2m-backup":
        raise RuntimeError("Unsupported backup format")
    if manifest.get("format_version") != 1:
        raise RuntimeError("Unsupported backup format version")
    return manifest


def validate_staged_backup(staging: Path, manifest: dict[str, Any]) -> tuple[Path, Path]:
    database = staging / "database" / "road2m.sqlite3"
    evidence = staging / "evidence"
    if not database.is_file():
        raise RuntimeError("Backup database/road2m.sqlite3 is missing")
    database_info = manifest.get("database")
    if not isinstance(database_info, dict):
        raise RuntimeError("Backup database manifest is missing")
    expected_hash = database_info.get("sha256")
    if expected_hash and sha256_file(database) != expected_hash:
        raise RuntimeError("Backup database checksum does not match manifest")
    validate_database(database)
    evidence_info = manifest.get("evidence")
    if not isinstance(evidence_info, dict):
        raise RuntimeError("Backup evidence manifest is missing")
    if evidence.exists() and evidence.is_symlink():
        raise RuntimeError("Backup evidence directory is a symlink")
    referenced = evidence_info.get("referenced_paths", [])
    if not isinstance(referenced, list) or any(not isinstance(item, str) for item in referenced):
        raise RuntimeError("Backup referenced evidence paths are malformed")
    for relative in referenced:
        candidate = safe_archive_member_path(relative)
        if not (evidence / candidate).is_file():
            raise RuntimeError(f"Referenced evidence is missing from backup: {relative}")
    for record in evidence_info.get("files", []):
        if not isinstance(record, dict) or not isinstance(record.get("path"), str):
            raise RuntimeError("Backup evidence manifest contains a malformed file")
        relative = safe_archive_member_path(record["path"])
        file_path = evidence / relative
        if not file_path.is_file():
            raise RuntimeError(f"Evidence listed in manifest is missing: {record['path']}")
        expected = record.get("sha256")
        if expected and sha256_file(file_path) != expected:
            raise RuntimeError(f"Evidence checksum does not match manifest: {record['path']}")
    return database, evidence


def active_pid(data_dir: Path) -> int | None:
    pid_file = data_dir / ".road2m.pid"
    if not pid_file.is_file():
        return None
    try:
        pid = int(pid_file.read_text(encoding="utf-8").splitlines()[0].strip())
    except (OSError, ValueError, IndexError):
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    except PermissionError:
        return pid
    except OSError:
        return None
    return pid


def restore_command(data_dir: Path, archive_path: Path, confirm_stopped: bool) -> Path:
    if not confirm_stopped:
        raise RuntimeError(
            "Restore requires --confirm-stopped. Stop the ResearchCouncil server first, then rerun the command."
        )
    ensure_private_directory(data_dir)
    pid = active_pid(data_dir)
    if pid is not None:
        raise RuntimeError(f"ResearchCouncil process {pid} is still running; stop it before restoring")
    backups = backup_path(data_dir)
    ensure_private_directory(backups)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safety = backups / f"pre-restore-{stamp}"
    suffix = 0
    while safety.exists():
        suffix += 1
        safety = backups / f"pre-restore-{stamp}-{suffix}"
    safety.mkdir(parents=True)
    os.chmod(safety, 0o700)

    try:
        with tempfile.TemporaryDirectory(prefix=".road2m-restore-", dir=data_dir) as temporary:
            staging = Path(temporary)
            manifest = extract_archive(archive_path.expanduser().resolve(), staging)
            staged_database, staged_evidence = validate_staged_backup(staging, manifest)

            current_database = database_path(data_dir)
            current_evidence = evidence_path(data_dir)
            safety_database = safety / "database-before-restore.sqlite3"
            if current_database.exists():
                sqlite_backup(current_database, safety_database)
            safety_evidence = safety / "evidence-before-restore"
            if current_evidence.exists():
                copy_tree(current_evidence, safety_evidence)
            safety_manifest = {
                "format": "road2m-pre-restore",
                "created_at": utc_now(),
                "archive": str(archive_path.expanduser().resolve()),
                "archive_sha256": sha256_file(archive_path.expanduser().resolve()),
                "database": str(safety_database.relative_to(data_dir)) if safety_database.exists() else None,
                "evidence": str(safety_evidence.relative_to(data_dir)) if safety_evidence.exists() else None,
            }
            (safety / "manifest.json").write_text(
                json.dumps(safety_manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            ensure_private_file(safety / "manifest.json")

            new_database = data_dir / f".road2m.sqlite3.restore-{stamp}"
            shutil.copy2(staged_database, new_database)
            ensure_private_file(new_database)
            moved_old_evidence = False
            moved_new_evidence = False
            try:
                if current_evidence.exists():
                    current_evidence.rename(safety / "evidence-active-before-restore")
                    moved_old_evidence = True
                staged_evidence.rename(current_evidence)
                moved_new_evidence = True
                os.replace(new_database, current_database)
            except Exception:
                new_database.unlink(missing_ok=True)
                if moved_new_evidence and current_evidence.exists():
                    shutil.rmtree(current_evidence)
                if moved_old_evidence and (safety / "evidence-active-before-restore").exists():
                    (safety / "evidence-active-before-restore").rename(current_evidence)
                raise
            ensure_private_directory(current_evidence)
            validate_database(current_database)
    except Exception:
        # Preserve the pre-restore directory for manual recovery; it contains
        # the previous database/evidence whenever those existed.
        raise
    print(safety)
    return safety


def decode_json_value(value: Any, column: str) -> Any:
    if isinstance(value, bytes):
        return "<binary omitted>"
    if isinstance(value, str) and column.endswith("_json"):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def safe_export_columns(columns: list[str]) -> list[str]:
    # Original PDFs are available in full SQLite backups and the explicit
    # document download. JSON exports retain their immutable hash/metadata.
    return [column for column in columns if not SENSITIVE_COLUMN.search(column) and column != "original_bytes"]


def rows_for_export(
    connection: sqlite3.Connection,
    table: str,
    namespace: str,
    allowed: dict[str, set[str]],
) -> list[dict[str, Any]]:
    columns = safe_export_columns(table_columns(connection, table))
    if not columns:
        return []
    quoted_columns = ", ".join(f'"{column}"' for column in columns)
    all_columns = set(table_columns(connection, table))
    parameters: list[Any] = []
    where = ""
    if table == "simulations":
        ids = sorted(allowed.get("simulations", set()))
        if not ids:
            return []
        where = f' WHERE "id" IN ({", ".join("?" for _ in ids)})'
        parameters.extend(ids)
    elif "namespace" in all_columns:
        where = ' WHERE "namespace" = ?'
        parameters.append(namespace)
    elif table in {"tasks", "watch_checks", "case_decision_versions", "attempt_decision_inputs"} and "run_id" in all_columns:
        ids = sorted(allowed.get("runs", set()))
        if not ids:
            return []
        where = f' WHERE "run_id" IN ({", ".join("?" for _ in ids)})'
        parameters.extend(ids)
    elif table == "idea_outcomes" and "baseline_id" in all_columns:
        ids = sorted(allowed.get("idea_baselines", set()))
        if not ids:
            return []
        where = f' WHERE "baseline_id" IN ({", ".join("?" for _ in ids)})'
        parameters.extend(ids)
    elif table == "attempt_decision_inputs" and "attempt_id" in all_columns:
        ids = sorted(allowed.get("tasks", set()))
        if not ids:
            return []
        where = f' WHERE "attempt_id" IN (SELECT id FROM task_attempts WHERE task_id IN ({", ".join("?" for _ in ids)}))'
        parameters.extend(ids)
    elif table == "task_attempts" and "task_id" in all_columns:
        ids = sorted(allowed.get("tasks", set()))
        if not ids:
            return []
        where = f' WHERE "task_id" IN ({", ".join("?" for _ in ids)})'
        parameters.extend(ids)
    elif table == "outputs" and "task_id" in all_columns:
        ids = sorted(allowed.get("tasks", set()))
        if not ids:
            return []
        where = f' WHERE "task_id" IN ({", ".join("?" for _ in ids)})'
        parameters.extend(ids)
    elif table == "output_claims" and "output_id" in all_columns:
        ids = sorted(allowed.get("outputs", set()))
        if not ids:
            return []
        where = f' WHERE "output_id" IN ({", ".join("?" for _ in ids)})'
        parameters.extend(ids)
    elif table == "research_repair_gaps" and {"repair_id", "gap_id"}.issubset(all_columns):
        repair_ids = sorted(allowed.get("research_repairs", set()))
        gap_ids = sorted(allowed.get("research_gaps", set()))
        if not repair_ids or not gap_ids:
            return []
        where = f' WHERE "repair_id" IN ({", ".join("?" for _ in repair_ids)}) AND "gap_id" IN ({", ".join("?" for _ in gap_ids)})'
        parameters.extend(repair_ids)
        parameters.extend(gap_ids)
    elif table == "intake_dispatches" and "item_id" in all_columns:
        ids = sorted(allowed.get("intake_items", set()))
        if not ids:
            return []
        where = f' WHERE "item_id" IN ({", ".join("?" for _ in ids)})'
        parameters.extend(ids)
    elif table == "events" and "namespace" not in all_columns:
        return []
    elif table == "source_versions" and "source_id" in all_columns:
        ids = sorted(allowed.get("sources", set()))
        if not ids:
            return []
        where = f' WHERE "source_id" IN ({", ".join("?" for _ in ids)})'
        parameters.extend(ids)
    elif table == "research_versions" and "research_item_id" in all_columns:
        ids = sorted(allowed.get("research_items", set()))
        if not ids:
            return []
        where = f' WHERE "research_item_id" IN ({", ".join("?" for _ in ids)})'
        parameters.extend(ids)
    elif table == "simulation_events" and "simulation_id" in all_columns:
        ids = sorted(allowed.get("simulations", set()))
        if not ids:
            return []
        where = f' WHERE "simulation_id" IN ({", ".join("?" for _ in ids)})'
        parameters.extend(ids)
    elif table == "task_dependencies" and {"task_id", "depends_on_task_id"}.issubset(all_columns):
        ids = sorted(allowed.get("tasks", set()))
        if not ids:
            return []
        placeholders = ", ".join("?" for _ in ids)
        where = f' WHERE "task_id" IN ({placeholders}) AND "depends_on_task_id" IN ({placeholders})'
        parameters.extend(ids)
        parameters.extend(ids)
    query = f'SELECT {quoted_columns} FROM "{table}"{where}'
    rows = []
    for row in connection.execute(query, parameters):
        rows.append({column: decode_json_value(row[column], column) for column in columns})
    return rows


def collect_export(connection: sqlite3.Connection, data_dir: Path, namespace: str) -> dict[str, Any]:
    available = table_names(connection)
    allowed: dict[str, set[str]] = {}
    for table, key in (
        ("runs", "id"),
        ("tasks", "id"),
        ("sources", "id"),
        ("research_items", "id"),
        ("simulations", "id"),
        ("research_repairs", "id"),
        ("research_gaps", "id"),
        ("intake_items", "id"),
        ("idea_baselines", "id"),
    ):
        if table not in available:
            continue
        columns = set(table_columns(connection, table))
        if "namespace" not in columns:
            continue
        rows = connection.execute(
            f'SELECT "{key}" FROM "{table}" WHERE "namespace" = ?', (namespace,)
        ).fetchall()
        allowed[table] = {str(row[0]) for row in rows}

    # Price scenarios reuse the legacy simulation storage table, whose
    # namespace is fixed to "simulation". Their candidate link owns the
    # actual research namespace. Keep real/demo price results with their
    # parent research and exclude them from participant-simulation exports.
    if {"simulations", "candidate_simulations"}.issubset(available):
        rows = connection.execute(
            'SELECT s.id FROM simulations s WHERE '
            'EXISTS (SELECT 1 FROM candidate_simulations cs WHERE cs.simulation_id=s.id AND cs.namespace=?) '
            'OR (s.namespace=? AND NOT EXISTS '
            '(SELECT 1 FROM candidate_simulations cs WHERE cs.simulation_id=s.id))',
            (namespace, namespace),
        ).fetchall()
        allowed["simulations"] = {str(row[0]) for row in rows}

    # Tasks also carry no namespace column; scope them through their parent
    # run before collecting attempts, outputs, or other task relationships.
    if {"tasks", "runs"}.issubset(available):
        task_columns = set(table_columns(connection, "tasks"))
        run_columns = set(table_columns(connection, "runs"))
        if {"id", "run_id"}.issubset(task_columns) and {"id", "namespace"}.issubset(run_columns):
            rows = connection.execute(
                'SELECT t."id" FROM "tasks" t '
                'JOIN "runs" r ON r."id" = t."run_id" '
                'WHERE r."namespace" = ?',
                (namespace,),
            ).fetchall()
            allowed["tasks"] = {str(row[0]) for row in rows}

    # output_claims has no namespace column of its own. Derive its allowlist
    # through outputs -> tasks -> runs so an association can only follow an
    # output already eligible for this namespace's export.
    if {"outputs", "tasks", "runs"}.issubset(available):
        output_columns = set(table_columns(connection, "outputs"))
        task_columns = set(table_columns(connection, "tasks"))
        run_columns = set(table_columns(connection, "runs"))
        if {"id", "task_id"}.issubset(output_columns) and {"id", "run_id"}.issubset(task_columns) and {"id", "namespace"}.issubset(run_columns):
            rows = connection.execute(
                'SELECT o."id" FROM "outputs" o '
                'JOIN "tasks" t ON t."id" = o."task_id" '
                'JOIN "runs" r ON r."id" = t."run_id" '
                'WHERE r."namespace" = ?',
                (namespace,),
            ).fetchall()
            allowed["outputs"] = {str(row[0]) for row in rows}

    tables: dict[str, list[dict[str, Any]]] = {}
    for table in EXPORT_TABLES:
        if table not in available:
            continue
        rows = rows_for_export(connection, table, namespace, allowed)
        tables[table] = rows

    evidence_files = []
    # Evidence is stored under evidence/<namespace>. Restrict the text export
    # to that subtree so a real export cannot include demo or simulation files.
    # Keep paths relative to the evidence root, matching backup manifests.
    evidence_root = evidence_path(data_dir)
    if evidence_root.exists():
        reject_symlink(evidence_root, "evidence directory")
    namespace_evidence = evidence_root / namespace
    for file_path, subtree_relative in iter_regular_files(namespace_evidence):
        relative = PurePosixPath(namespace, subtree_relative).as_posix()
        record: dict[str, Any] = {
            "path": relative,
            "size_bytes": file_path.stat().st_size,
            "sha256": sha256_file(file_path),
        }
        if record["size_bytes"] <= MAX_EXPORT_EVIDENCE_BYTES:
            try:
                record["content"] = file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                record["content"] = None
                record["content_missing_reason"] = "binary evidence omitted from text export"
        else:
            record["content"] = None
            record["content_missing_reason"] = "evidence exceeds text export size limit"
        evidence_files.append(record)
    return {
        "format": "road2m-export",
        "format_version": 1,
        "generated_at": utc_now(),
        "namespace": namespace,
        "tables": tables,
        "evidence_files": evidence_files,
        "omissions": [
            "provider credentials and auth material",
            "raw worker logs and internal provider thoughts",
        ],
    }


def markdown_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value).replace("|", "\\|").replace("\n", "<br>")


def to_markdown(export: dict[str, Any]) -> str:
    lines = [
        "# ResearchCouncil export",
        "",
        f"- Namespace: `{export['namespace']}`",
        f"- Generated: `{export['generated_at']}`",
        "- This export omits provider credentials, raw worker logs, and internal provider thoughts.",
        "",
    ]
    for table, rows in export.get("tables", {}).items():
        lines.extend([f"## {table}", ""])
        if not rows:
            lines.extend(["No records.", ""])
            continue
        columns = list(rows[0])
        lines.append("| " + " | ".join(columns) + " |")
        lines.append("| " + " | ".join("---" for _ in columns) + " |")
        for row in rows:
            lines.append("| " + " | ".join(markdown_value(row.get(column)) for column in columns) + " |")
        lines.append("")
    evidence = export.get("evidence_files", [])
    lines.extend(["## Evidence files", ""])
    if evidence:
        lines.extend(["| Path | Size | SHA-256 |", "| --- | ---: | --- |"])
        for record in evidence:
            lines.append(
                f"| {markdown_value(record['path'])} | {record['size_bytes']} | `{record['sha256']}` |"
            )
    else:
        lines.append("No evidence files.")
    lines.append("")
    return "\n".join(lines)


def atomic_write(path: Path, content: str) -> None:
    if path.exists():
        raise RuntimeError(f"Refusing to overwrite existing export: {path}")
    ensure_private_directory(path.parent)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        ensure_private_file(Path(temporary))
        os.replace(temporary, path)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise
    ensure_private_file(path)


def export_command(data_dir: Path, namespace: str, output_format: str, output: Path | None) -> Path | None:
    database = database_path(data_dir)
    validate_database(database)
    connection = connect_readonly(database)
    try:
        payload = collect_export(connection, data_dir, namespace)
    finally:
        connection.close()
    suffix = "json" if output_format == "json" else "md"
    if output is None:
        export_dir = data_dir / "exports"
        ensure_private_directory(export_dir)
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output = export_dir / f"road2m-{namespace}-{stamp}.{suffix}"
    else:
        output = output.expanduser().resolve()
    content = (
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
        if output_format == "json"
        else to_markdown(payload)
    )
    atomic_write(output, content)
    print(output)
    return output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Private ResearchCouncil backup, restore, and export utility")
    parser.add_argument("--data-dir", type=Path, default=None, help="Data directory (default: ROAD2M_DATA_DIR or ./data)")
    subparsers = parser.add_subparsers(dest="command", required=True)
    backup = subparsers.add_parser("backup", help="Create a consistent SQLite plus evidence archive")
    backup.add_argument("--data-dir", dest="command_data_dir", type=Path, default=None, help=argparse.SUPPRESS)
    backup.add_argument("--output", type=Path, default=None, help="Archive path (default: data/backups/...) ")
    restore = subparsers.add_parser("restore", help="Validate and restore a backup archive")
    restore.add_argument("--data-dir", dest="command_data_dir", type=Path, default=None, help=argparse.SUPPRESS)
    restore.add_argument("archive", type=Path)
    restore.add_argument("--confirm-stopped", action="store_true", help="Confirm the local server is stopped")
    export = subparsers.add_parser("export", help="Export namespace records without secrets or raw worker logs")
    export.add_argument("--data-dir", dest="command_data_dir", type=Path, default=None, help=argparse.SUPPRESS)
    export.add_argument("--namespace", choices=("real", "demo", "simulation"), default="real")
    export.add_argument("--format", choices=("json", "markdown"), default="json")
    export.add_argument("--output", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    data_dir = path_from_env(arguments.command_data_dir or arguments.data_dir)
    try:
        if arguments.command == "backup":
            backup_command(data_dir, arguments.output)
        elif arguments.command == "restore":
            restore_command(data_dir, arguments.archive, arguments.confirm_stopped)
        elif arguments.command == "export":
            export_command(data_dir, arguments.namespace, arguments.format, arguments.output)
        else:
            parser.error("a command is required")
    except (OSError, RuntimeError, sqlite3.Error) as error:
        print(f"ResearchCouncil {arguments.command} failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
