"""Primary-source policy for a release verified by the earnings workflow.

An issuer-looking URL or source_type never grants this policy. The saved,
code-owned acquisition receipt must bind the exact immutable source. The
checks are repeated on read so edits or supersession cannot retain authority.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from typing import Any
from urllib.parse import urlsplit

from .earnings_sources import _company_present, _date_present, _primary_release_url, _quarter, company_with_verified_issuer_domain


def _object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def verified_earnings_release_policy(conn: Any, source: Any) -> dict[str, Any] | None:
    """Return primary policy only for a verified, unchanged earnings release."""
    row = dict(source)
    source_id, namespace = row.get("id"), row.get("namespace")
    content, expected_hash = row.get("original_content"), row.get("content_hash")
    if not source_id or namespace not in {"real", "demo", "simulation"} or not isinstance(content, str) or len(content) < 300:
        return None
    if row.get("source_type") not in {"document", "html", "filing", "sec_filing", "issuer", "issuer_release", "other"}:
        return None
    if not expected_hash or hashlib.sha256(content.encode()).hexdigest() != expected_hash:
        return None
    archived = conn.execute("SELECT version_no,content_hash,content FROM source_versions WHERE source_id=? ORDER BY version_no DESC LIMIT 1", (source_id,)).fetchone()
    if (not archived or archived["content_hash"] != expected_hash or archived["content"] != content
            or (row.get("version") is not None and str(row["version"]) != str(archived["version_no"]))
            or conn.execute("SELECT 1 FROM sources WHERE namespace=? AND supersedes_source_id=? LIMIT 1", (namespace, source_id)).fetchone()):
        return None
    workflows = conn.execute(
        "SELECT * FROM research_workflow_runs WHERE workflow='earnings' AND namespace=? AND status IN ('partial','completed') AND result_json LIKE ?",
        (namespace, f'%"{source_id}"%'),
    ).fetchall()
    for workflow in workflows:
        package = _object(workflow["result_json"])
        company, event = _object(package.get("company")), _object(package.get("event"))
        company = company_with_verified_issuer_domain(company, event, conn, namespace)
        ticker, cik = str(company.get("ticker") or ""), str(company.get("cik") or "")
        if (not ticker or ticker != workflow["ticker"] or not company.get("name") or not re.fullmatch(r"\d{10}", cik)
                or not isinstance(company.get("tickers"), list) or ticker not in company["tickers"]
                or company.get("submissions_url") != f"https://data.sec.gov/submissions/CIK{cik}.json"
                or not re.fullmatch(r"[a-f0-9]{64}", str(company.get("verification_hash") or ""))
                or not _date(company.get("verified_at"))
                or company.get("resolution") not in {"SEC ticker directory", "Discovered CIK, verified against SEC submissions"}
                or event.get("verification") != "primary_release"
                or event.get("expected_form") not in {"10-K", "10-Q"}
                or event.get("release_source_id") != source_id
                or not isinstance(package.get("source_ids"), list) or source_id not in package["source_ids"]):
            continue
        steps = {item["agent_id"]: item["status"] for item in conn.execute("SELECT agent_id,status FROM research_workflow_steps WHERE run_id=?", (workflow["id"],))}
        if any(steps.get(key) != "completed" for key in ("resolve", "locate", "acquire")):
            continue
        documents = _object(package.get("documents"))
        candidates = [_object(event.get("release_document")), _object(documents.get("release"))]
        documents_bound = True
        for document in (item for item in candidates if item):
            # Legacy release receipts predate an explicit version field. Their
            # immutable source ID can only establish the initial archive.
            expected_version = document.get("source_version", document.get("version", 1))
            if (document.get("status") != "available" or document.get("kind") != "release"
                    or document.get("source_id") != source_id or document.get("content_hash") != expected_hash
                    or document.get("url") != row.get("url") or document.get("period_end") != event.get("period_end")
                    or str(expected_version) != str(archived["version_no"])):
                documents_bound = False
                break
        if not any(candidates) or not documents_bound or event.get("release_url") != row.get("url"):
            continue
        url = str(row.get("url") or "")
        try:
            parsed = urlsplit(url)
        except ValueError:
            continue
        try:
            primary_url = _primary_release_url(url, company)
        except (ValueError, TypeError):
            primary_url = False
        if parsed.username or parsed.password or not primary_url:
            continue
        period_end, reported = _date(event.get("period_end")), _date(event.get("earnings_date"))
        located = _date(event.get("located_at"))
        if not period_end or not reported or not located or not period_end <= reported <= located:
            continue
        header = content[:6500]
        if (not _company_present(header, company) or not _date_present(content, period_end.isoformat())
                or not _date_present(header, reported.isoformat())
                or not re.search(r"\b(?:net (?:income|sales)|revenue|earnings per share|diluted (?:net )?(?:income|earnings))\b", content, re.I)):
            continue
        actual_quarter, expected_quarter = _quarter(header), _quarter(str(event.get("fiscal_period") or ""))
        if (expected_quarter and actual_quarter and expected_quarter != actual_quarter
                or actual_quarter == 4 and event.get("expected_form") != "10-K"
                or actual_quarter in {1, 2, 3} and event.get("expected_form") != "10-Q"):
            continue
        return {"kind": "fundamental", "primary_evidence": True, "coverage": "primary_issuer", "max_age_days": 150,
                "requirement": "dated_primary", "verification": "earnings_workflow_primary_release",
                "workflow_id": workflow["id"], "source_version": archived["version_no"], "content_hash": expected_hash,
                "publication_at": reported.isoformat(), "period_end": period_end.isoformat()}
    return None
