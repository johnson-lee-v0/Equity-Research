"""SQLite persistence primitives.

The database is intentionally boring: one connection per operation, WAL mode,
foreign keys and explicit transactions.  Higher layers own domain validation;
this module owns durable ordering and migration safety.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import Settings, settings


NAMESPACES = {"real", "demo", "simulation"}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_id(prefix: str = "") -> str:
    value = uuid.uuid4().hex
    return f"{prefix}{value}" if prefix else value


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def json_loads(value: str | None, default: Any = None) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def digest(value: Any) -> str:
    return hashlib.sha256(json_dumps(value).encode("utf-8")).hexdigest()


class Database:
    def __init__(self, path: str | Path | None = None, config: Settings | None = None):
        self.config = config or settings
        self.config.prepare()
        self.path = Path(path or self.config.db_path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._migration_dir = self.config.project_root / "backend" / "migrations"
        self._lock = threading.RLock()
        self.migrate()
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    @contextmanager
    def operation(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection = self.connect()
            try:
                connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    def migrate(self) -> None:
        self.config.prepare()
        with self._lock:
            connection = self.connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
                )
                applied = {row[0] for row in connection.execute("SELECT version FROM schema_migrations")}
                files = sorted(self._migration_dir.glob("*.sql"))
                for migration in files:
                    try:
                        version = int(migration.name.split("_", 1)[0])
                    except (ValueError, IndexError):
                        continue
                    if version in applied:
                        continue
                    connection.executescript(migration.read_text(encoding="utf-8"))
                    connection.execute(
                        "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
                        (version, migration.name, utc_now()),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    def backup_to(self, destination: str | Path) -> Path:
        destination = Path(destination).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination == self.path:
            raise ValueError("backup destination must differ from the live database")
        with self._lock, self.operation() as source:
            target = sqlite3.connect(destination)
            try:
                source.backup(target)
                target.commit()
            finally:
                target.close()
        try:
            os.chmod(destination, 0o600)
        except OSError:
            pass
        return destination

    def restore_from(self, source_path: str | Path) -> None:
        source_path = Path(source_path).expanduser().resolve()
        if not source_path.is_file():
            raise FileNotFoundError(source_path)
        if source_path == self.path:
            raise ValueError("restore source must differ from the live database")
        with self._lock:
            source = sqlite3.connect(source_path)
            target = self.connect()
            try:
                source.execute("PRAGMA query_only=ON")
                source.backup(target)
                target.commit()
            finally:
                source.close()
                target.close()
        self.migrate()

    @staticmethod
    def emit(
        connection: sqlite3.Connection,
        *,
        namespace: str,
        event_type: str,
        run_id: str | None = None,
        task_id: str | None = None,
        attempt_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if namespace not in NAMESPACES:
            raise ValueError(f"unknown namespace: {namespace}")
        event_id = new_id("evt_")
        emitted_at = utc_now()
        payload = payload or {}
        cursor = connection.execute(
            "INSERT INTO events(event_id, namespace, run_id, task_id, attempt_id, type, payload_json, emitted_at) VALUES (?,?,?,?,?,?,?,?)",
            (event_id, namespace, run_id, task_id, attempt_id, event_type, json_dumps(payload), emitted_at),
        )
        sequence_id = int(cursor.lastrowid)
        return {
            "sequence_id": sequence_id,
            "event_id": event_id,
            "namespace": namespace,
            "run_id": run_id,
            "task_id": task_id,
            "attempt_id": attempt_id,
            "emitted_at": emitted_at,
            "type": event_type,
            "payload": payload,
        }

    @staticmethod
    def index_record(
        connection: sqlite3.Connection,
        *,
        record_id: str,
        record_type: str,
        namespace: str,
        title: str,
        body: str,
        provenance: str | None = None,
    ) -> None:
        updated_at = utc_now()
        connection.execute(
            "INSERT INTO search_records(record_id, record_type, namespace, title, body, provenance, updated_at) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(record_id, record_type) DO UPDATE SET namespace=excluded.namespace, title=excluded.title, body=excluded.body, provenance=excluded.provenance, updated_at=excluded.updated_at",
            (record_id, record_type, namespace, title[:500], body[:100_000], provenance, updated_at),
        )
        connection.execute("DELETE FROM memory_fts WHERE record_id=? AND record_type=?", (record_id, record_type))
        connection.execute(
            "INSERT INTO memory_fts(record_id, record_type, namespace, title, body, provenance) VALUES (?,?,?,?,?,?)",
            (record_id, record_type, namespace, title[:500], body[:100_000], provenance or ""),
        )

    @staticmethod
    def remove_index(connection: sqlite3.Connection, record_id: str, record_type: str) -> None:
        connection.execute("DELETE FROM search_records WHERE record_id=? AND record_type=?", (record_id, record_type))
        connection.execute("DELETE FROM memory_fts WHERE record_id=? AND record_type=?", (record_id, record_type))

