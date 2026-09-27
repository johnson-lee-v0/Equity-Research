"""Bounded, allow-listed SEC submissions connector."""
from __future__ import annotations

import asyncio
import json
import re
from types import SimpleNamespace
from typing import Any

from ..config import Settings, settings
from ..memory.repository import Repository
from .discovery import fetch_public_page


class SecConnector:
    def __init__(self, repository: Repository, config: Settings | None = None):
        self.repository = repository
        self.config = config or settings

    @staticmethod
    def normalize_cik(value: str) -> str:
        digits = re.sub(r"\D", "", value or "")
        if not digits or len(digits) > 10:
            raise ValueError("CIK must contain one to ten digits")
        return digits.zfill(10)

    async def fetch(self, namespace: str, cik: str, form: str | None = None) -> dict[str, Any]:
        normalized = self.normalize_cik(cik)
        if form and not re.fullmatch(r"[0-9A-Z-]{1,20}", form.upper()):
            raise ValueError("invalid SEC form")
        url = f"https://data.sec.gov/submissions/CIK{normalized}.json"
        try:
            raw = await asyncio.to_thread(self._get, url)
        except ValueError as exc:
            return {"items": [], "status": "unavailable", "reason": "SEC data could not be retrieved: " + str(exc)[:500]}
        except Exception:
            return {"items": [], "status": "unavailable", "reason": "SEC data could not be retrieved; import the filing manually if needed."}
        try:
            data = json.loads(raw.decode("utf-8", errors="replace"))
        except (TypeError, ValueError):
            return {"items": [], "status": "invalid_response", "reason": "SEC returned an invalid submissions document."}
        recent = data.get("filings", {}).get("recent", {}) if isinstance(data, dict) else {}
        forms = recent.get("form", []) if isinstance(recent, dict) else []
        accessions = recent.get("accessionNumber", []) if isinstance(recent, dict) else []
        dates = recent.get("filingDate", []) if isinstance(recent, dict) else []
        primary = recent.get("primaryDocument", []) if isinstance(recent, dict) else []
        company = str(data.get("name") or "SEC filer") if isinstance(data, dict) else "SEC filer"
        sources = []
        for index, value in enumerate(forms[:100]):
            if form and str(value).upper() != form.upper():
                continue
            accession = str(accessions[index]).replace("-", "") if index < len(accessions) else ""
            if not accession:
                continue
            document = str(primary[index]) if index < len(primary) else ""
            filing_url = f"https://www.sec.gov/Archives/edgar/data/{int(normalized)}/{accession}/{document}" if document else f"https://www.sec.gov/Archives/edgar/data/{int(normalized)}/{accession}/"
            content = json.dumps({"company": company, "cik": normalized, "form": value, "accession": accessions[index] if index < len(accessions) else None, "filing_date": dates[index] if index < len(dates) else None, "primary_document": document}, ensure_ascii=False)
            request = SimpleNamespace(namespace=namespace, kind="evidence", title=f"SEC {value} {company}", content=content, source_url=filing_url, publication_at=dates[index] if index < len(dates) else None, observed_at=None, supersedes_id=None, idempotency_key=f"sec:{normalized}:{accession}")
            try:
                imported = self.repository.import_evidence(request)
            except ValueError as exc:
                continue
            sources.extend(self.repository.sources(namespace, imported.get("source_id")) if imported.get("source_id") else [])
            if len(sources) >= 25:
                break
        return {"items": sources, "status": "imported" if sources else "no_matching_filings", "reason": None if sources else "No supported SEC submissions matched the requested form."}

    def _get(self, url: str) -> bytes:
        page = fetch_public_page(url, max_bytes=min(self.config.max_source_bytes, 5_000_000), timeout=20,
                                 sec_user_agent=self.config.sec_user_agent)
        if page.error:
            raise ValueError(page.error)
        return page.content.encode("utf-8")
