"""Public discovery leads from the shared vault, without citation authority.

The ordinary notebook contains private opinions and local provenance. A web-
capable discovery worker receives only this projection: dated public URLs and
fixed public topic labels. Source IDs and note bodies stay in the local receipt.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from typing import Any
from urllib.parse import unquote, urlsplit

from ..memory.repository import _safe_public_queries

VERSION = "public-discovery-memory.v1"
_TOPIC_RULES = (
    (r"\b(?:10[- ]?k|10[- ]?q|8[- ]?k|filings?)\b", "SEC filings and amendments"),
    (r"\b(?:transcript|earnings call)\b", "earnings call transcripts"),
    (r"\b(?:release|earnings results)\b", "issuer earnings releases"),
    (r"\b(?:capex|capital expenditure|capital spending)\b", "capital spending and prior guidance"),
    (r"\b(?:renewal|members?|membership)\b", "membership growth and renewal trends"),
    (r"\b(?:revenue|sales|growth)\b", "reported revenue and growth trends"),
    (r"\b(?:margin|profitability|earnings)\b", "profitability and earnings trends"),
    (r"\b(?:cash flow|debt|liquidity)\b", "cash flow, debt and liquidity"),
    (r"\b(?:share count|dilution|buyback)\b", "share counts and capital returns"),
    (r"\b(?:multiple|valuation|p/e|p/s|ebitda)\b", "historical valuation multiples"),
    (r"\b(?:guidance|forecast|outlook)\b", "dated management guidance"),
)


def public_lead_url(value: Any) -> str | None:
    """Reject ambiguous query URLs/private hosts; fetching still uses SSRF gates."""
    if not isinstance(value, str) or len(value) > 2000 or any(ord(char) < 32 for char in value):
        return None
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").casefold().rstrip(".")
        if parsed.scheme != "https" or not host or parsed.username or parsed.password or parsed.port not in (None, 443):
            return None
        # A query can select the actual document or carry private values.
        # Omit it entirely rather than inventing an unobserved queryless URL.
        if parsed.query:
            return None
        if "." not in host or host.endswith((".localhost", ".local", ".internal", ".test", ".invalid")):
            return None
        try:
            if not ipaddress.ip_address(host).is_global:
                return None
        except ValueError:
            pass
        clean = parsed._replace(fragment="").geturl()
        # Existing discovery privacy checks apply to URL paths too. Decoding
        # before the check prevents percent encoding from hiding such fields.
        if not _safe_public_queries([unquote(clean)], ["PUBLIC"]):
            return None
        return clean
    except (ValueError, UnicodeError):
        return None


def prepare_discovery_memory(repo, task_id: str, *, public_tickers: list[str],
                             frozen_source_versions: dict[str, Any], include_gaps: bool = True) -> tuple[dict, dict]:
    """Return the public prompt projection and a separate frozen local receipt."""
    memory = repo.prepare_shared_memory(task_id, max_chars=12_000, max_items=20,
                                       frozen_source_versions=frozen_source_versions)
    allowed = {str(ticker).upper() for ticker in public_tickers
               if isinstance(ticker, str) and re.fullmatch(r"[A-Z0-9][A-Z0-9._-]{0,14}", ticker.upper())}
    tickers = sorted(allowed & set(memory.get("tickers", [])))[:5]
    public: dict[str, Any] = {
        "version": VERSION,
        "policy": "Historical public leads, not supplied evidence or verified current facts. Start with relevant issuer/IR URLs when useful, check current event dates, and return readable sources through the ordinary discovery archive. Do not cite these notes or infer that an old gap remains open today. Do not expand the assigned search scope or budget.",
        "companies": [{"ticker": ticker} for ticker in tickers],
        "sources": [], "open_topics": [],
    }
    receipt: dict[str, Any] = {"version": VERSION, "retrieval_version": memory.get("version"),
        "as_of": memory.get("as_of"), "note_ids": [], "source_versions": {}, "citation_authority": False}
    seen_urls: set[str] = set()
    seen_topics: set[tuple[str, str]] = set()
    namespace = memory.get("namespace")
    for item in memory.get("items", []):
        if not isinstance(item, dict) or item.get("ticker") not in tickers:
            continue
        kind = item.get("kind")
        if kind not in {"source", "fact", "gap"} or item.get("status") in {"invalidated", "superseded", "resolved"}:
            continue
        included = False
        if kind == "gap" and include_gaps and len(public["open_topics"]) < 4:
            # Do not send arbitrary saved gap text to a web-capable worker.
            # Topic labels come only from this fixed vocabulary; private
            # quantities, account references and instructions are discarded.
            title = str(item.get("title") or "")
            if _safe_public_queries([title], tickers):
                topic = next((label for pattern, label in _TOPIC_RULES if re.search(pattern, title, re.I)), None)
                key = (item["ticker"], topic)
                if topic and key not in seen_topics:
                    public["open_topics"].append({"ticker": item["ticker"], "topic": topic,
                                                   "status": "previously unresolved; verify current coverage"})
                    seen_topics.add(key)
                    included = True
        if kind in {"source", "fact"} and len(public["sources"]) < 8:
            versions = {entry.get("id"): entry for entry in item.get("source_versions", []) if isinstance(entry, dict)}
            for ref in item.get("source_refs", [])[:8]:
                expected = versions.get(ref)
                if not expected or len(public["sources"]) >= 8:
                    continue
                try:
                    source = next(iter(repo.source_packet(namespace, [ref])), None)
                    current = repo.source_head_ids(namespace, [ref])
                except (ValueError, KeyError):
                    continue
                if (not source or current != [ref] or source.get("content_hash") != expected.get("content_hash")
                        or str(source.get("version")) != str(expected.get("version"))):
                    continue
                # An HTTPS address is not proof that a pasted/uploaded file
                # is public. In particular, sharing URLs can expose private
                # document IDs. Reuse only code-owned primary public policy;
                # generic secondary leads stay local without public proof.
                policy = source.get("source_policy") or {}
                if (source.get("primary_evidence") is not True
                        or policy.get("primary_evidence") is not True
                        or policy.get("kind") == "user_observation"
                        or policy.get("primary_coverage") in {"user_owned", "unknown_external_document", "unknown"}):
                    continue
                url = public_lead_url(source.get("url"))
                if not url or url in seen_urls:
                    continue
                public["sources"].append({"ticker": item["ticker"], "url": url,
                    "published_at": source.get("publication_at"), "observed_at": source.get("observed_at"),
                    "use": "historical URL lead; verify and archive before citing"})
                receipt["source_versions"][ref] = expected
                seen_urls.add(url)
                included = True
        if included and item.get("note_id"):
            receipt["note_ids"].append(item["note_id"])
    receipt["projection"] = public
    receipt["projection_hash"] = hashlib.sha256(json.dumps(public, sort_keys=True).encode()).hexdigest()
    return public, receipt
