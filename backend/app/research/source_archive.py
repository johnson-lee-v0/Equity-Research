"""Shared immutable archival for discovery and bounded source refresh."""
from __future__ import annotations

import hashlib
from typing import Any

from ..schemas import ImportRequest
from .documents import retain_document


def archive_public_observation(repo: Any, page: Any, *, namespace: str, scope: str) -> dict[str, Any]:
    """Retain an observation without globally amending an earlier source.

    User-scoped optional passes and initial event checks may discover changed
    page bytes, but have no authority to reopen other cases that used that URL.
    Explicit source refresh remains the amendment/propagation path.
    """
    if page.error or not page.content:
        return {"status": "unavailable", "source_id": None, "reason": page.error or "The page returned no readable text."}
    request = ImportRequest(namespace=namespace, kind="evidence", title=page.title or page.final_url,
        content=page.content, source_url=page.final_url,
        idempotency_key="public-observation:" + hashlib.sha256(f"{namespace}|{scope}|{page.final_url}|{page.content}".encode()).hexdigest())
    saved = repo.import_evidence(request)
    if saved.get("source_id"):
        retain_document(repo, namespace, saved["source_id"], page.original_bytes, page.document_metadata)
    return saved | {"status": "archived" if saved.get("source_id") else "unavailable"}


def archive_public_page(repo: Any, page: Any, *, namespace: str, scope: str, defer_run_id: str | None = None) -> dict[str, Any]:
    error = str(page.error or "page returned no readable text").strip()[:500] if page.error or not page.content else None
    if error:
        if page.original_bytes and page.document_metadata:
            content = "\n".join(["ResearchCouncil PDF extraction unavailable",f"Original SHA256: {page.document_metadata['original_hash']}",f"Failure: {error}","No document claims were extracted or verified."])
            try:
                request=ImportRequest(namespace=namespace,kind="evidence",title="Unreadable PDF: "+(page.title or page.final_url),content=content,source_url=page.final_url,idempotency_key="pdf-unreadable:"+hashlib.sha256(f"{namespace}|{scope}|{page.final_url}|{content}".encode()).hexdigest())
                saved=repo.import_evidence(request)
                retain_document(repo,namespace,saved["source_id"],page.original_bytes,page.document_metadata)
                return {"status":"unavailable","reason":error,"retained_source_id":saved["source_id"],"source_id":None,"refresh_run_ids":[]}
            except ValueError as exc:
                error += " Original retention failed: "+str(exc)[:150]
        return {"status":"unavailable","reason":error,"source_id":None,"refresh_run_ids":[]}
    content_hash=hashlib.sha256(page.content.encode()).hexdigest()
    with repo.db.operation() as conn:
        prior=conn.execute("SELECT s.id,s.content_hash FROM sources s WHERE s.namespace=? AND s.url=? AND NOT EXISTS (SELECT 1 FROM sources n WHERE n.namespace=s.namespace AND n.supersedes_source_id=s.id) ORDER BY s.retrieval_at DESC,s.rowid DESC LIMIT 1",(namespace,page.final_url)).fetchone()
    if prior and prior["content_hash"] == content_hash:
        return {"status":"unchanged","source_id":prior["id"],"refresh_run_ids":[],"reason":"Archived content is unchanged; observation dates are not refreshed."}
    request=ImportRequest(namespace=namespace,kind="evidence",title=page.title or page.final_url,content=page.content,source_url=page.final_url,publication_at=None,observed_at=None,supersedes_id=prior["id"] if prior else None,idempotency_key="public-source:"+hashlib.sha256(f"{namespace}|{scope}|{page.final_url}|{content_hash}|{prior['id'] if prior else ''}".encode()).hexdigest())
    try:
        kwargs = {"refresh_mode":"defer","defer_run_id":defer_run_id} if prior and defer_run_id else {}
        saved=repo.import_evidence(request,**kwargs)
        source_id=saved.get("source_id")
        if source_id:
            retain_document(repo,namespace,source_id,page.original_bytes,page.document_metadata)
        return saved | {"status":"updated" if prior else "archived","source_id":source_id,"reason":"A new immutable source version was retained."}
    except ValueError as exc:
        return {"status":"unavailable","source_id":None,"refresh_run_ids":[],"reason":"Source archive rejected this version: "+str(exc)[:350]}
