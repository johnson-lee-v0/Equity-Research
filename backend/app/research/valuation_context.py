"""Code-owned valuation context from one unchanged earnings package."""
from __future__ import annotations

import hashlib
from typing import Any

from ..db import json_loads
from .earnings_context import verified_earnings_package
from .earnings_financials import bound_release_eps_observations
from .valuation_history import build_valuation_research_context


def compact_valuation_context(context: dict | None) -> dict | None:
    """Keep all price dates while avoiding repeated EPS components in prompts.

    The full calculation trail remains in the canonical report. Source IDs,
    period ends and publication cutoffs remain attached to every sample here.
    """
    if context is None:
        return None
    history = dict(context.get("historical_pe") or {})
    history["points"] = [{key: value for key, value in point.items() if key != "eps_components"}
                         for point in history.get("points", [])]
    history["calculation_detail"] = "Source-bound annual/YTD component records are retained with each sample in the canonical report. This prompt preserves every sampled date, close, trailing EPS, multiple and source reference."
    multiples = {name: {**series, "points": [{key: value for key, value in point.items() if key != "components"} for point in series.get("points", [])]}
                 for name, series in (context.get("historical_multiples") or {}).items()}
    return {**context, "historical_pe": history, "historical_multiples": multiples}


def compile_valuation_context(repo: Any, run_id: str, sources: list[dict], *, as_of: str, frozen_versions: dict | list | None = None, ticker: str | None = None, frozen_receipt: dict | None = None) -> dict | None:
    """Never attach today's live history to an older assessment revision.

    The caller supplies the exact frozen attempt sources. The workflow binds
    issuer, fiscal calendar and release publication date; retained source
    hashes bind financial observations and historical market closes.
    """
    with repo.db.operation() as conn:
        verified = verified_earnings_package(conn, run_id, ticker=ticker, frozen_receipt=frozen_receipt)
        run = conn.execute("SELECT input_snapshot_json FROM runs WHERE id=?", (run_id,)).fetchone()
    if not verified:
        return None
    if frozen_versions is None:
        frozen_versions = json_loads(run["input_snapshot_json"], {}).get("source_versions") or []
    frozen = ({row["id"]: row for row in frozen_versions if isinstance(row, dict) and row.get("id")}
              if isinstance(frozen_versions, list) else frozen_versions)
    if not isinstance(frozen, dict):
        return None
    for source in sources:
        expected = frozen.get(source.get("id")) or {}
        if (source.get("version") != expected.get("version")
                or source.get("content_hash") != (expected.get("content_hash") or expected.get("hash"))):
            return None
    by_id = {source["id"]: source for source in sources}
    if any(sid not in by_id or by_id[sid].get("content_hash") != binding["content_hash"]
           or by_id[sid].get("version") != binding["version"]
           for sid, binding in verified["source_bindings"].items()):
        return None
    if any(source.get("namespace") != verified["namespace"] or not source.get("content_hash")
           or hashlib.sha256(str(source.get("content") or "").encode()).hexdigest() != source["content_hash"]
           for source in sources):
        return None
    package = verified["package"]
    event, company = package.get("event") or {}, package["company"]
    if not event.get("fiscal_period") or not event.get("period_end"):
        return None
    contents = {sid: source["content"] for sid, source in by_id.items()}
    versions = {sid: {"hash": source["content_hash"], "version": source.get("version")} for sid, source in by_id.items()}
    observations = []
    for role, document in (package.get("documents") or {}).items():
        if not isinstance(document, dict) or role not in {"release", "earnings_release", "current_filing", "prior_filing", "previous_filing"}:
            continue
        sid = document.get("source_id")
        if sid not in by_id:
            continue
        published = by_id[sid].get("publication_at")
        if role in {"release", "earnings_release"} and event.get("verification") == "primary_release":
            published = published or event.get("earnings_date")
        parsed = bound_release_eps_observations(sid, contents, by_id, versions, as_of=as_of)
        observations.extend(row | {"source_ref": sid, "publication_at": published} for row in parsed["observations"])
    return build_valuation_research_context(sources, ticker=verified["ticker"], cik=str(company["cik"]),
        as_of=as_of, fiscal_period=event["fiscal_period"], period_end=event["period_end"], extra_observations=observations,
        share_class_count=len(company.get("tickers") or [verified["ticker"]]))
