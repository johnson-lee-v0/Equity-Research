"""Code-owned preparation for a verified earnings assessment.

The saved workflow is the authority for identity and source selection. These
stages do not impersonate model analysis: a single CIO call owns the investment
judgment, forecast assumptions and answers after preparation has completed.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from ..db import json_loads
from ..schemas import AgentOutputPayload
from .earnings_context import verified_earnings_package
from .earnings_sources import EarningsAcquisition
from .source_archive import archive_public_page, archive_public_observation

VERSION = "earnings-assessment.v1"


def enabled(run: Any) -> bool:
    return json_loads(run["input_snapshot_json"], {}).get("assessment_pipeline") == VERSION


async def acquire_financial_baseline(repo, registry, config, run_id: str, *, ticker: str | None = None, dispatch_guard=None) -> list[str]:
    """Acquire issuer financials and dated prices for a reproducible P/E history."""
    from .valuation_corporate_actions import acquire_valuation_corporate_actions
    from .comparable_peers import acquire_peers

    with repo.db.operation() as conn:
        package = verified_earnings_package(conn, run_id, ticker=ticker)
    if not package:
        raise ValueError("The earnings archive changed before financial preparation.")
    company = package["package"]["company"]
    cik = str(company["cik"]).zfill(10)
    url = f"https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/us-gaap/EarningsPerShareDiluted.json"
    acquisition = EarningsAcquisition(repo, registry, config, namespace=package["namespace"])
    acquisition.dispatch_guard = dispatch_guard
    if dispatch_guard:
        dispatch_guard()
    financial_ids, price_ids, action_ids, comparable_ids, provider_ids = await asyncio.gather(
        _acquire_eps_source(repo, acquisition, package, run_id, cik, url),
        acquire_valuation_price_history(repo, config, package["ticker"], namespace=package["namespace"], run_id=run_id),
        acquire_valuation_corporate_actions(repo, config, package["ticker"], namespace=package["namespace"], run_id=run_id),
        _acquire_comparable_financials(repo, acquisition, package, run_id, cik),
        _acquire_provider_multiples(repo, acquisition, package, run_id, config),
    )
    peer_ids = await acquire_peers(repo, acquisition, package, run_id, config)
    return list(dict.fromkeys([*financial_ids, *price_ids, *action_ids, *comparable_ids, *provider_ids, *peer_ids]))


async def _acquire_provider_multiples(repo, acquisition, package, run_id, config) -> list[str]:
    if not getattr(config, "enable_market_connectors", False) or package["namespace"] != "real":
        return []
    from .secondary_valuation_history import refresh_history
    try:
        result = await refresh_history(repo, acquisition, ticker=package["ticker"], namespace=package["namespace"],
            as_of=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), run_id=run_id)
        return list(dict.fromkeys(sid for history in result["provider_multiples"].values() for sid in history["source_refs"]))
    except (ValueError, OSError) as exc:
        repo.emit(package["namespace"], "provider_multiple_history_gap", run_id=run_id, payload={"message": str(exc)[:400]})
        return []


async def _acquire_comparable_financials(repo, acquisition, package, run_id, cik) -> list[str]:
    """One bounded SEC request, retained intact; no inferred EBITDA or NAV."""
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    try:
        from .capex_facts import companyfacts_source
        cached = await companyfacts_source(acquisition, package["package"]["company"], research_as_of=datetime.now(timezone.utc).isoformat())
        if cached:
            repo.append_run_sources(run_id, [cached["id"]], reason="Verified SEC companyfacts reused for comparable valuation.")
            return [cached["id"]]
        raise ValueError("Verified SEC companyfacts could not be acquired or reused.")
    except (ValueError, OSError) as exc:
        repo.emit(package["namespace"], "financial_baseline_gap", run_id=run_id,
                  payload={"message": str(exc)[:500], "url": url})
        return []


async def _acquire_eps_source(repo, acquisition, package, run_id, cik, url) -> list[str]:
    try:
        data, page = await acquisition._json(url, refresh=True)
        if str(data.get("cik", "")).zfill(10) != cik or data.get("tag") != "EarningsPerShareDiluted" or not data.get("units", {}).get("USD/shares"):
            raise ValueError("SEC EPS response did not bind the requested issuer and per-share unit.")
        result = archive_public_page(repo, page, namespace=package["namespace"], scope=run_id, defer_run_id=run_id)
        if not result.get("source_id"):
            raise ValueError(result.get("reason") or "EPS source could not be archived.")
        repo.append_run_sources(run_id, [result["source_id"]], reason="Dated SEC diluted earnings acquired for the price-target baseline.")
        return [result["source_id"]]
    except ValueError as exc:
        repo.emit(package["namespace"], "financial_baseline_gap", run_id=run_id,
                  payload={"message": str(exc)[:500], "url": url})
        return []


async def acquire_valuation_price_history(repo, config, ticker: str, *, namespace: str, run_id: str | None = None) -> list[str]:
    """Archive raw closes separately from dividend-adjusted return simulations.

    These observations explain where trailing P/E traded. They are not a
    live execution quote or permission to replace a company's financial facts.
    """
    if not bool(getattr(config, "enable_market_connectors", False)):
        return []
    from .connectors import AlpacaConnector

    now = datetime.now(timezone.utc)
    start = now - timedelta(days=5 * 366 + 7)
    title = f"{ticker} · five-year raw daily prices for valuation history"
    with repo.db.operation() as conn:
        cached = conn.execute(
            "SELECT id,retrieval_at,original_content,content_hash,url FROM sources WHERE namespace=? AND title=? AND NOT EXISTS "
            "(SELECT 1 FROM sources child WHERE child.namespace=sources.namespace AND child.supersedes_source_id=sources.id) "
            "ORDER BY retrieval_at DESC LIMIT 1", (namespace, title),
        ).fetchone()
    reusable = False
    if cached and str(cached["retrieval_at"] or "")[:10] == now.date().isoformat():
        try:
            content = cached["original_content"]
            header = json.loads(content.splitlines()[1])
            metadata = header.get("metadata") or {}
            reusable = (hashlib.sha256(content.encode()).hexdigest() == cached["content_hash"]
                and header.get("provider") == "alpaca" and header.get("source_type") == "market_bars"
                and cached["url"] == "https://data.alpaca.markets/v2/stocks/bars"
                and metadata.get("symbols") == [ticker] and metadata.get("timeframe") == "1Day"
                and metadata.get("adjustment") == "raw" and metadata.get("currency") == "USD"
                and metadata.get("feed") == getattr(config, "alpaca_data_feed", "iex")
                and str(metadata.get("start") or "")[:10] <= start.date().isoformat())
        except (ValueError, TypeError, IndexError):
            reusable = False
    if reusable:
        identifiers = [cached["id"]]
    else:
        connector = AlpacaConnector(project_root=config.project_root, data_dir=config.data_dir)
        try:
            result = await asyncio.to_thread(connector.fetch_bars, ticker, "1Day", start=start, end=now,
                limit=2000, max_pages=2, max_bars=2500, adjustment="raw", include_technicals=False,
                feed=getattr(config, "alpaca_data_feed", "iex"))
        except (ValueError, OSError):
            if run_id:
                repo.emit(namespace, "valuation_history_gap", run_id=run_id,
                    payload={"message": "Historical market-price retrieval failed; the source gap is retained."})
            return []
        if result.status not in {"ok", "partial"} or not result.bars:
            if run_id:
                repo.emit(namespace, "valuation_history_gap", run_id=run_id,
                    payload={"message": "Historical market prices could not be retrieved from the configured provider.", "status": result.status})
            return []
        try:
            saved = repo.import_evidence(result.as_import_request(namespace=namespace, title=title))
        except (ValueError, OSError):
            if run_id:
                repo.emit(namespace, "valuation_history_gap", run_id=run_id,
                    payload={"message": "Historical prices could not be retained as verified evidence; the existing archive was not replaced."})
            return []
        identifiers = [saved["source_id"]] if saved.get("source_id") else []
    if identifiers and run_id:
        repo.append_run_sources(run_id, identifiers, reason="Dated raw prices retained for the company's historical trailing P/E range.")
    return identifiers


def preparation_payload(stage: str, compiled: dict, package: dict) -> AgentOutputPayload:
    company = package["package"]["company"]
    ticker = package["ticker"]
    ids = list(package["source_bindings"])
    common = dict(status="completed", research_contract="five-questions.v1", source_refs=ids,
                  title=f"{ticker} · verified earnings preparation", allocation_mode="alternatives")
    if stage == "A00":
        return AgentOutputPayload(**common, summary="The saved earnings package identifies the company and research scope.",
            analysis="Code-validated workflow identity and unchanged source versions determine this route; no model routing call was needed.",
            routing_plan=compiled["route"])
    if stage == "A01":
        return AgentOutputPayload(**common, summary="Previously acquired earnings materials passed archive validation.",
            analysis="The existing source package is reused. This preparation step performs no additional web search and makes no investment recommendation.",
            research_candidates=[{"ticker": ticker, "name": company.get("name"), "rationale": "Issuer verified by the saved earnings acquisition workflow.", "source_urls": []}])
    facts = compiled.get("fact_claims") or []
    questions = [{"key": key,
                  "answer": "Source preparation is complete. Investment interpretation and the final answer are pending the investment review.",
                  "decision_implication": "Review the archived evidence before reaching an investment conclusion.",
                  "supporting_claim_ids": [f["claim_id"] for f in facts[:1]] if key == "valuation" else [],
                  "unknowns": []}
                 for key in ("opportunity", "valuation", "catalyst", "downside", "portfolio_action")]
    return AgentOutputPayload(**(common | {"source_refs": list(dict.fromkeys([*ids, *[f["source_ref"] for f in facts]]))}),
        summary="Source-bound financial inputs and earnings trends are ready for investment review.",
        analysis="This is a deterministic evidence preparation record, not a completed investment opinion. Financial facts below are independently validated against their archived sources. The final review will choose and defend forecast assumptions.",
        fact_claims=facts, candidate_briefs=[{"ticker": ticker, "issuer_name": company.get("name"),
            "horizon": "12 months", "key_questions": questions}],
        missing_data=(compiled.get("valuation_readiness") or {}).get("gaps", []))


def valuation_operand_fact_ids(compiled: dict, saved_facts: list[dict]) -> list[str]:
    """Bind compiler financial proofs to durable facts, excluding context metrics."""
    from .earnings_financials import VERSION as FINANCIAL_BINDER_VERSION
    from .comparable_financials import VERSION as COMPARABLE_BINDER_VERSION

    proven = {row["claim_id"] for row in compiled.get("seed_proofs", [])
              if ((row.get("validation") or {}).get("proof") or {}).get("parser") in {FINANCIAL_BINDER_VERSION, COMPARABLE_BINDER_VERSION}}
    fields = ("source_ref", "source_version", "subject", "metric", "value", "unit", "currency",
              "basis", "period", "period_start", "period_end", "statement_type")
    identities = {tuple(row.get(key) for key in fields) for row in compiled.get("fact_claims", [])
                  if row.get("claim_id") in proven}
    return list(dict.fromkeys(fact["fact_id"] for fact in saved_facts
        if fact.get("fact_id") and all(fact.get(key) == "validated" for key in
            ("validation_status", "recorded_validation_status", "current_validation_status"))
        and fact.get("semantic_status") == "supported"
        and tuple(fact.get(key) for key in fields) in identities))


TARGET_INSTRUCTION = (
    "Use valuation_research_context to compare the chosen P/E assumptions with the retained historical trailing P/E range; explain why a forward multiple can differ. "
    "Distinguish reported quarters from a seasonal projection for any unreported quarter. Historical gaps are not projections. "
    "The holding horizon and the earnings forecast span are separate: forecast_years measures baseline fiscal EPS to forecast fiscal EPS. "
    "Use the newest supported annual EPS baseline, and for the next fiscal year use one growth step. Never square a one-year growth rate merely because the holding horizon is 12 months. "
    "Code separately shows implied value today on current reported trailing earnings, without inventing a discount rate. "
    "You are the single investment reviewer after code-owned evidence preparation, not a reviewer of a prior investment opinion. "
    "Your primary deliverable is exactly one complete candidate_briefs entry with all five key_questions and non-null valuation_assumptions. "
    "Complete that structured candidate first. A price written only in summary or analysis cannot be calculated or published. "
    "Use the verified ticker as candidate.ticker and candidate.issuer; keep the full company name in candidate.issuer_name and instrument. "
    "Replace every preparatory answer with a substantive answer to the five questions. Read the full supplied call answers and qualifiers, historical trends, capital guidance and latest financial release. "
    "Ground the operating interpretation in dated numerical trend comparisons. Where provided, explain renewal and member-growth changes and the capital-spending record versus earlier guidance; distinguish the observed miss from any management-stated cause. "
    "A source-backed numerical price target is required. Choose an asset-appropriate supported valuation method and defend bear/base/bull assumptions. "
    "Choose the valuation method for the business: ps_multiple for an unprofitable company with meaningful sales; ev_ebitda for a stable operating company with positive EBITDA; nav_multiple for asset-heavy companies and financial institutions when asset/equity evidence is available. "
    "For financial institutions prefer book or tangible book equity with an explicitly named nav_basis; book equity is not appraised NAV, and EV/EBITDA is generally inappropriate for banks. Explain asset quality and returns on equity. "
    "The comparable methods require exact typed fact inputs baseline_revenue, baseline_ebitda or baseline_nav (total equity value, not per share), diluted_shares, and all EV bridge components (excess_cash, debt, preferred_claims, minority_interest), in unscaled units. "
    "When reported EBITDA is absent, use typed fact inputs baseline_operating_income and baseline_da for the exact same reporting period/currency; code labels their sum an EBITDA proxy, not management adjusted EBITDA. Review the definition before comparing peers. Missing cash/debt/claims are never zero; an explicit forecast bridge assumption requires a rationale. Each bear/base/bull scenario supplies annual growth_rate, exit_multiple, growth_rationale and multiple_rationale; code computes the forecast and prices. "
    "Include comparables only where independently saved peer numerator and denominator facts exist: P/S = market capitalization / sales, EV/EBITDA = enterprise value / EBITDA, P/NAV = market capitalization / total equity NAV or book equity. Bind each peer ticker, observation date, trailing/forward basis and comparability rationale. Do not use subject facts as peer evidence. Missing peers remain an explicit gap; never invent market multiples. "
    "For a profitable operating company where earnings are the appropriate basis use canonical name eps_multiple, inputs containing a baseline_eps row (kind=fact, exact saved fact_id, value, period, unit, currency and basis), "
    "period identifying the forecast fiscal year, horizon_months=12, and scenarios bear/base/bull with growth_rate (decimal annual rate), exit_multiple, growth_rationale and multiple_rationale. "
    "For baseline_eps copy valuation_readiness.baseline_input exactly. In particular, do not prefix its period with a fiscal-year label or rewrite its unit or basis; put the forecast fiscal year in the method's period instead. "
    "When the historical baseline is older than the latest earnings release, set forecast_years to the actual annual steps from its fiscal year to the forecast year, "
    "and forecast_span_rationale explicitly explaining the historical baseline date, forecast date, latest reported context and target horizon. "
    "Do not silently label old earnings current. EPS in a dollar-denominated release without an independently established currency can inform an explicitly labelled forecast assumption; it cannot become a verified USD input. "
    "Do not treat unknown future earnings or exit multiples as missing facts: choose defensible assumptions, explain business drivers and valuation compression risk, and show sensitivity. "
    "A chosen multiple is an analyst assumption, never an invented peer or historical market multiple. Do not invent management forecasts. "
    "Only subtract/add a nonrecurring EPS adjustment when its currency, basis and period are independently validated and it belongs to the baseline period. "
    "Never subtract a FY2026 item from FY2025 earnings. Reuse the saved preparatory fact IDs instead of emitting duplicate claims. "
    "The preparatory fact register includes independently checked reported observations when available. Reference those exact saved IDs in the relevant question. "
    "Valuation method and input fact_claim_ids identify financial calculation operands only; use only valuation_readiness.valuation_fact_ids. "
    "Operating metrics explain growth and multiple assumptions through source_refs and scenario rationales, not through the method's financial fact_claim_ids. "
    "Do not add a reference_price or current_price row to the valuation inputs. A share-price header in a transcript is not a validated quote; disclose that limitation rather than emitting a new quote fact. "
    "Use original source IDs and line locators for additional management commentary, guidance and catalysts; describe these as cited management statements or research interpretation. "
    "Do not manufacture a new verified fact for a prose statement or a metric absent from the supplied register merely to fill a supporting-claim field. "
    "Code will compute prices and reject unsupported arithmetic before publication. Missing portfolio data limits sizing, not the research target. "
    "Put each substantive answer and its numerical comparisons in the relevant candidate key_question, and every valuation input in valuation_assumptions. "
    "Write each key_question answer in 350-500 characters, including citations, leaving room below the 800-character hard limit. Use one or two decisive comparisons, not a quarter-by-quarter list. "
    "Finish every sentence and source locator; never use an incomplete sentence, an unfinished source ID or invisible padding to fit a field. "
    "Keep the top-level analysis to a short executive synthesis under 900 characters. The saved trend charts already contain the full numerical history; do not reproduce tables or repeat the evidence packet. "
    "Keep each rationale concise; do not repeat the same answer in multiple fields."
)


def require_price_targets(payload: AgentOutputPayload, context: dict, sources: list[dict], *, as_of: str) -> None:
    """Validate the exact proposed calculator before saving a final output."""
    from .valuation import build_valuation
    facts = [claim for output in context.get("prior_outputs", []) for claim in output.get("fact_claims", [])]
    candidates = payload.candidate_briefs
    if not candidates:
        raise ValueError("Price target validation: supply the selected candidate and its valuation assumptions.")
    for candidate in candidates:
        if candidate.ticker != context.get("ticker"):
            raise ValueError("Price target validation: candidate ticker must match the verified earnings company.")
        result = build_valuation(candidate.valuation_assumptions.model_dump(mode="json") if candidate.valuation_assumptions else {},
            asset_class=candidate.asset_class, horizon=candidate.horizon, as_of=as_of,
            validated_facts=facts, issuer=candidate.issuer or candidate.issuer_name or candidate.instrument,
            source_records={s["id"]: s for s in sources})
        methods = result.get("methods") or []
        scenarios = result.get("scenarios") or {}
        try:
            complete_prices = all(Decimal(str(scenarios[name])).is_finite() and Decimal(str(scenarios[name])) > 0 for name in ("bear", "base", "bull"))
        except (InvalidOperation, KeyError, ValueError):
            complete_prices = False
        selected = next((m for m in methods if m.get("name") == result.get("selected_method") and m.get("supported")), {})
        bridges = selected.get("scenario_calculations") or {}
        complete_bridges = all(bridges.get(name, {}).get("inputs") and bridges[name].get("formula") for name in ("bear", "base", "bull"))
        if result.get("status") != "complete" or not complete_prices or not complete_bridges:
            reasons = [str(r) for method in methods for r in method.get("reasons", [])]
            raise ValueError("Price target validation: " + "; ".join(reasons or result.get("missing_inputs") or ["Provide an explicit sourced baseline and complete bear/base/bull assumptions and calculation bridges."])[:1800])
