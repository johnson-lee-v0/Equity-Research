"""Keep remaining gaps from recursively reopening a bounded earnings review.

This policy changes automatic research routing only. It does not resolve a gap,
authorize a trade, publish a decision, or relax any canonical review gate.
"""
from __future__ import annotations

import re
from typing import Any

from ..db import json_loads
from ..schemas import AgentOutputPayload, MissingGap
from .assessment_pipeline import VERSION, require_price_targets
from .earnings_context import verified_earnings_package
from .fact_references import resolve_fact_references


_FINANCIAL_EVIDENCE = re.compile(
    r"\b(?:eps|earnings|income|revenue|cash\s*flows?|capex|capital expenditure|pp&e|"
    r"filings?|10[ -]?[kq]|8[ -]?k|balance sheets?|financial statements?|annual reports?|"
    r"transcripts?|call passages?|renewal|membership|margins?|guidance|baseline|debt|"
    r"shares outstanding|diluted shares|gaap|normaliz\w*|forecast|discount rate|terminal growth|"
    r"valuation inputs?|target calculation|multiple assumptions?)\b"
)
_ISSUER_CONFLICT = re.compile(
    r"\b(?:wrong|conflicting|mismatched|ambiguous)\s+(?:issuer|company|ticker)|"
    r"\b(?:issuer|company)\s+identity\b"
)


def execution_only_gap(gap: MissingGap) -> str | None:
    """Conservatively recognize quote or personal sizing requests.

    Include source requirements: a gap titled 'current price' must still repair
    a missing EPS/currency binding if that substantive requirement is bundled
    into it. Unknown and mixed requests keep the existing research route.
    """
    text = " ".join((gap.key, gap.description, gap.reopen_when, *gap.source_requirements)).casefold()
    text = re.sub(r"[_-]+", " ", text)
    if _FINANCIAL_EVIDENCE.search(text) or _ISSUER_CONFLICT.search(text):
        return None
    market = re.search(
        r"\b(?:current|dated|latest|live|spot|entry|market|same instrument)\s+"
        r"(?:(?:entry|market|share|stock|price|same instrument)\s+){0,2}(?:price|quote|comparison)\b|"
        r"\b(?:price observation|quote timestamp|connector identity)\b", text
    )
    if market:
        return "current_market_comparison"
    personal = re.search(
        r"\b(?:portfolio|personal|account|brokerage|position siz\w*|cost basis|"
        r"owner risk|risk tolerance|risk limits|cash reserve|cash floor)\b", text
    )
    # Company/fund holdings and corporate balances are research evidence, not
    # the owner's account. Bare 'position' or 'balance' is never sufficient.
    corporate = re.search(r"\b(?:issuer|company|corporate|fund holdings|etf holdings)\b", text)
    if personal and not corporate:
        return "portfolio_sizing"
    return None


def has_verified_earnings_target(repo: Any, conn: Any, *, run: Any, task: Any,
                                 output_id: str, payload: AgentOutputPayload) -> bool:
    """Recompute from this attempt's frozen, source-bound prior facts.

    Code-owned preparation already committed the numerical seeds. Provider
    declarations of target completeness and newly proposed unvalidated facts
    are not evidence for suppressing another research pass. The exact bounded
    earnings recipe must already have a recalculable target; other research
    routes and missing/invalid-target correction retain their existing policy.
    Publication and local review still use their independent controls.
    """
    snapshot = json_loads(run["input_snapshot_json"], {})
    if task["agent_id"] != "A11" or snapshot.get("assessment_pipeline") != VERSION:
        return False
    package = verified_earnings_package(conn, run["id"])
    if not package:
        return False
    output = conn.execute("SELECT * FROM outputs WHERE id=? AND task_id=?", (output_id, task["id"])).fetchone()
    if not output:
        return False
    attempt = conn.execute("SELECT source_versions_json FROM task_attempts WHERE id=?", (output["attempt_id"],)).fetchone()
    versions = json_loads(attempt[0], {}) if attempt else {}
    inputs = repo.attempt_decision_inputs(output["attempt_id"])
    if not isinstance(versions, dict) or not versions or not inputs:
        return False
    try:
        sources = repo.source_packet(run["namespace"], list(versions))
        projected, _ = resolve_fact_references(repo, output, payload.model_dump(mode="json"), [], inputs, sources)
        require_price_targets(payload, {
            "ticker": package["ticker"],
            "prior_outputs": [{"fact_claims": projected["fact_claims"]}],
        }, sources, as_of=run["as_of"])
    except (ValueError, TypeError, KeyError):
        return False
    return True
