"""Immutable originals behind extracted PDF source text."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from ..db import utc_now


def retain_document(repo: Any, namespace: str, source_id: str, raw: bytes | None, metadata: dict[str, Any] | None) -> None:
    if not raw or not metadata:
        return
    content_hash = hashlib.sha256(raw).hexdigest()
    if metadata.get("original_hash") != content_hash:
        raise ValueError("PDF archive hash does not match extracted provenance.")
    with repo.db.transaction(immediate=True) as conn:
        source = conn.execute("SELECT original_content FROM sources WHERE id=? AND namespace=?",(source_id,namespace)).fetchone()
        if not source or f"Original SHA256: {content_hash}" not in source[0]:
            raise ValueError("PDF original does not belong to this extracted source.")
        prior = conn.execute("SELECT original_hash FROM source_documents WHERE source_id=?",(source_id,)).fetchone()
        if prior and prior[0] != content_hash:
            raise ValueError("Archived PDF originals cannot be replaced.")
        conn.execute("INSERT OR IGNORE INTO source_documents(source_id,namespace,original_hash,original_bytes,metadata_json,created_at) VALUES(?,?,?,?,?,?)",(source_id,namespace,content_hash,raw,json.dumps(metadata),utc_now()))


def document_record(repo: Any, namespace: str, source_id: str, *, include_bytes: bool = False) -> dict[str, Any] | None:
    columns = "metadata_json,original_bytes" if include_bytes else "metadata_json"
    with repo.db.operation() as conn:
        row = conn.execute(f"SELECT {columns} FROM source_documents WHERE source_id=? AND namespace=?",(source_id,namespace)).fetchone()
    if not row:
        return None
    metadata = json.loads(row["metadata_json"])
    return metadata | ({"bytes":row["original_bytes"]} if include_bytes else {})
