"""Read-only reuse of observed transcript URLs, never cached acceptance or analysis."""
from __future__ import annotations

from datetime import date
import hashlib
import re
from urllib.parse import urlsplit

from ..db import json_loads


def _fiscal_key(value: str) -> tuple[int, int] | None:
    """Ignore cosmetic repeated annual labels, never conflicting periods."""
    years = set(re.findall(r"(?<!\d)(20\d{2})(?!\d)", value))
    quarters = {int(q) for q in re.findall(r"\bQ([1-4])\b", value, re.I)}
    named = {"first": 1, "second": 2, "third": 3, "fourth": 4}
    quarters.update(named[word.lower()] for word in re.findall(r"\b(first|second|third|fourth)\s+quarter\b", value, re.I))
    if len(years) != 1 or len(quarters) != 1:
        return None
    return int(next(iter(years))), next(iter(quarters))


def prior_transcript_candidates(repo, namespace: str, company: dict, event: dict) -> list[str]:
    """Return at most four intact, exact-event transcript URL candidates.

    Callers must fetch each URL and apply today's issuer/event/completeness and
    speaker-quality checks. A cancelled workflow can contribute an acquired
    source, but cannot contribute an accepted analysis or investment result.
    """
    ticker = company.get("ticker")
    cik = str(company.get("cik") or "")
    event_fields = ("fiscal_period", "period_end", "earnings_date")
    if (namespace not in {"real", "demo"} or not isinstance(ticker, str)
            or not re.fullmatch(r"[A-Z0-9][A-Z0-9.-]{0,14}", ticker)
            or not re.fullmatch(r"\d{1,10}", cik) or int(cik) == 0
            or event.get("verification") != "primary_release"
            or any(not isinstance(event.get(key), str) or not event[key] for key in event_fields)):
        return []
    try:
        if date.fromisoformat(event["period_end"]) > date.fromisoformat(event["earnings_date"]):
            return []
    except ValueError:
        return []
    fiscal = _fiscal_key(event["fiscal_period"])
    if fiscal is None:
        return []
    candidates = []
    with repo.db.operation() as conn:
        workflows = conn.execute(
            "SELECT id FROM research_workflow_runs WHERE namespace=? AND ticker=? AND workflow='earnings' "
            "AND status IN ('completed','partial','cancelled') ORDER BY created_at DESC,rowid DESC LIMIT 10",
            (namespace, ticker),
        ).fetchall()
        for workflow in workflows:
            steps = {row["agent_id"]: row for row in conn.execute(
                "SELECT agent_id,status,output_json FROM research_workflow_steps "
                "WHERE run_id=? AND agent_id IN ('resolve','locate','acquire')", (workflow["id"],))}
            if (any(key not in steps for key in ("resolve", "locate", "acquire"))
                    or any(steps[key]["status"] != "completed" for key in ("resolve", "locate"))
                    or steps["acquire"]["status"] not in {"completed", "partial"}):
                continue
            resolved, located, acquired = (json_loads(steps[key]["output_json"], {}) for key in ("resolve", "locate", "acquire"))
            if not all(isinstance(item, dict) for item in (resolved, located, acquired)):
                continue
            prior_cik = str(resolved.get("cik") or "")
            if (resolved.get("ticker") != ticker or not re.fullmatch(r"\d{1,10}", prior_cik)
                    or int(prior_cik) != int(cik) or located.get("verification") != "primary_release"
                    or not isinstance(located.get("fiscal_period"), str)
                    or _fiscal_key(located["fiscal_period"]) != fiscal
                    or any(located.get(key) != event[key] for key in ("period_end", "earnings_date"))):
                continue
            documents = acquired.get("documents")
            document = documents.get("transcript") if isinstance(documents, dict) else None
            if (not isinstance(document, dict) or document.get("status") != "available"
                    or document.get("kind") != "transcript"
                    or any(document.get(key) != event[key] for key in ("period_end", "earnings_date"))):
                continue
            url, source_id, expected_hash = document.get("url"), document.get("source_id"), document.get("content_hash")
            if not isinstance(url, str) or len(url) > 3000 or not isinstance(source_id, str) or not isinstance(expected_hash, str):
                continue
            try:
                parsed = urlsplit(url)
                if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                    continue
            except ValueError:
                continue
            source = conn.execute("SELECT url,original_content,content_hash FROM sources WHERE id=? AND namespace=?", (source_id, namespace)).fetchone()
            version = conn.execute("SELECT content,content_hash FROM source_versions WHERE source_id=? ORDER BY version_no DESC LIMIT 1", (source_id,)).fetchone()
            if (not source or not version or source["url"] != url or not isinstance(source["original_content"], str) or not source["original_content"]
                    or not isinstance(version["content"], str)
                    or source["content_hash"] != expected_hash or version["content_hash"] != expected_hash
                    or hashlib.sha256(source["original_content"].encode()).hexdigest() != expected_hash
                    or hashlib.sha256(version["content"].encode()).hexdigest() != expected_hash
                    or conn.execute("SELECT 1 FROM sources WHERE supersedes_source_id=? LIMIT 1", (source_id,)).fetchone()):
                continue
            if url not in candidates:
                candidates.append(url)
            if len(candidates) == 4:
                break
    return candidates
