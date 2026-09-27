"""Financial preparation shared by ordinary single-company investment reviews.

Prepared facts are validated by the earnings compiler, then committed with the
analyst's output. The reviewer receives the resulting durable IDs. This does
not turn an unavailable baseline into an assumed financial observation.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation

from ..db import json_loads
from ..schemas import AgentOutputPayload, FactClaim
from .assessment_pipeline import valuation_operand_fact_ids
from .comparable_financials import VERSION as COMPARABLE_VERSION
from .earnings_financials import VERSION as FINANCIAL_VERSION
from .freshness import evaluate_fact_freshness
from .valuation import source_has_primary_coverage


TARGET_INSTRUCTION = (
    "Financial preparation: financial_seeds contains exact source-bound financial inputs. "
    "Keep the requested holding horizon. Build candidate valuation_assumptions with bear/base/bull scenarios when valuation_readiness.ready_methods has a suitable method. "
    "Use eps_multiple for a profitable operating company when earnings are a meaningful basis; ps_multiple for an unprofitable company with meaningful sales; "
    "ev_ebitda for stable positive operating EBITDA; nav_multiple with an explicit nav_basis for a financial or asset-heavy company. "
    "Book equity is not appraised NAV; EV/EBITDA is generally inappropriate for banks. Explain asset quality, profitability and method fit. "
    "Copy valuation_readiness.baseline_input exactly for baseline_eps, keeping its reported period, unit and basis. "
    "For other methods use typed fact inputs baseline_revenue, baseline_ebitda or baseline_nav (total equity, not per share), and diluted_shares. "
    "EV/EBITDA additionally needs excess_cash, debt, preferred_claims and minority_interest; missing liabilities are not zero. "
    "If reported EBITDA is absent, baseline_operating_income plus baseline_da must cover exactly the same period and currency; describe the result as an EBITDA proxy. "
    "Use unscaled amounts, exact supplied fact IDs and source_refs. Forecast shares or balance-sheet bridge inputs must be explicit assumptions with rationales. "
    "Each scenario supplies growth_rate as a decimal annual rate, exit_multiple, growth_rationale and multiple_rationale. Code computes the prices. "
    "Set horizon_months from the user's requested holding period, and the method period to the forecast fiscal period. "
    "forecast_years measures the baseline-to-forecast span, not the holding horizon. The next fiscal year is one growth step; whenever supplying forecast_years, also provide forecast_span_rationale. "
    "Show implied value today separately from the future target. For EPS/P-E valuation, today's implied value is current reported diluted EPS from valuation_research_context.current_earnings multiplied by each selected scenario P/E, without growth or discounting. "
    "The future EPS target applies the explicit growth assumptions and fiscal forecast span. Do not invent a discounted present value or say the calculation is unavailable when these code-owned inputs and calculations are supplied. "
    "If current_earnings is unavailable, preserve that specific current-value limitation while retaining any supported future target. Compare multiples against the dated reported-as-of history in valuation_research_context; trailing and forward multiples differ. "
    "Use peer comparisons only with independently archived issuer, price and financial evidence. A chosen multiple is an analyst assumption, not an invented market observation. "
    "Do not treat unknown future growth or multiples as missing historical facts: choose and defend assumptions. If no suitable financial baseline is available, "
    "preserve the exact missing inputs in missing_data and the valuation question's unknowns; do not invent a target. "
    "Use financial calculation operands only in valuation fact_claim_ids; operating trends belong in scenario rationales and source_refs. "
    "Answer all five investment questions using the full supplied management answers and numerical trends, including actual versus projected quarters. "
    "Missing portfolio data limits sizing, not a financially supported research target."
)


def compile_financial_preparation(repo, run_id: str, sources: list[dict]) -> dict | None:
    """Compile financial evidence without admitting cross-case memory as inputs.

    Memory is separately frozen on the provider attempt. It remains visible
    there, but is not part of this run's earnings-acquisition source packet.
    """
    from .assessment_evidence import compile_earnings_assessment
    snapshot = json_loads(repo.run_record(run_id)["input_snapshot_json"], {})
    retained = {row["id"] for row in snapshot.get("source_versions", []) if isinstance(row, dict) and row.get("id")}
    return compile_earnings_assessment(repo, run_id, [source for source in sources if source.get("id") in retained])


def financial_seeds(compiled: dict) -> list[dict]:
    proven = {row["claim_id"] for row in compiled.get("seed_proofs", [])
              if ((row.get("validation") or {}).get("proof") or {}).get("parser") in {FINANCIAL_VERSION, COMPARABLE_VERSION}}
    # Leave room for three material additional claims within the ordinary
    # 15-fact contract. Source-bound operating trends remain in their context.
    selected = [row for row in compiled.get("fact_claims", []) if row.get("claim_id") in proven][:12]
    return [deepcopy(row) | {"claim_id": f"prepared_{index + 1}"} for index, row in enumerate(selected)]


def _positive(value) -> bool:
    try:
        number = Decimal(str(value))
        return number.is_finite() and number > 0
    except (InvalidOperation, ValueError):
        return False


def prepared_context(compiled: dict, prior_outputs: list[dict], sources: list[dict], *, agent_id: str, as_of: str) -> dict:
    """Expose usable operands without treating a raw SEC JSON prefix as facts."""
    if agent_id == "A03":
        seeds = financial_seeds(compiled)
    else:
        saved = [dict(fact) for output in prior_outputs if output.get("agent_id") == "A03"
                 for fact in output.get("fact_claims", [])]
        allowed = set(valuation_operand_fact_ids(compiled, saved))
        seeds = [{key: value for key, value in fact.items() if key != "claim_id"}
                 for fact in saved if fact.get("fact_id") in allowed]
    by_source = {source["id"]: source for source in sources}
    usable = []
    for fact in seeds:
        source = by_source.get(fact.get("source_ref"), {})
        if source_has_primary_coverage(source) and evaluate_fact_freshness(fact, source, as_of=as_of).get("status") == "fresh":
            usable.append(fact)
    # Every supported method uses one currency. Do not combine a company's
    # different reporting currencies merely because their metric names match.
    currency_groups = {fact.get("currency") for fact in usable if fact.get("currency")}
    ready = []
    for currency in currency_groups:
        metrics = {}
        for fact in sorted(usable, key=lambda row: row.get("period_end") or "", reverse=True):
            if fact.get("currency") in {currency, None}:
                metrics.setdefault(fact.get("metric"), fact)
        if _positive((metrics.get("eps") or {}).get("value")):
            ready.append("eps_multiple")
        shares = _positive((metrics.get("shares") or {}).get("value"))
        if shares and _positive((metrics.get("revenue") or {}).get("value")):
            ready.append("ps_multiple")
        if shares and _positive((metrics.get("book equity") or {}).get("value")):
            ready.append("nav_multiple")
        operating, da = metrics.get("operating income"), metrics.get("depreciation and amortization")
        proxy = (operating and da and operating.get("period_start")
                 and all(operating.get(key) == da.get(key) for key in ("period_start", "period_end", "currency"))
                 and _positive(Decimal(operating["value"]) + Decimal(da["value"])))
        if shares and (_positive((metrics.get("ebitda") or {}).get("value")) or proxy) and all(metric in metrics for metric in ("cash", "debt", "preferred", "minority")):
            ready.append("ev_ebitda")
    latest_eps = max((fact for fact in usable if fact.get("metric") == "eps"), key=lambda row: row.get("period_end") or "", default=None)
    baseline = latest_eps if latest_eps and _positive(latest_eps.get("value")) else None
    identifiers = [fact.get("fact_id") or fact["claim_id"] for fact in usable]
    baseline_id = (baseline.get("fact_id") or baseline["claim_id"]) if baseline else None
    readiness = {"status": "ready" if ready else "needs_valuation_evidence", "ready_methods": list(dict.fromkeys(ready)),
                 "valuation_fact_ids": identifiers, "baseline_fact_id": baseline_id,
                 "missing_inputs": [] if ready else ["No complete, current, source-bound financial baseline for a supported valuation method."],
                 "baseline_input": ({"key": "baseline_eps", "kind": "fact",
                     **{key: baseline.get(key) for key in ("value", "period", "unit", "currency", "basis", "scale", "statement_type")},
                     "fact_claim_ids": [baseline_id], "source_refs": [baseline["source_ref"]],
                     "rationale": "Reported historical diluted EPS; forecast assumptions are supplied separately."} if baseline else None)}
    return {"financial_seeds": seeds, "valuation_readiness": readiness,
            "assessment_preparation": {"version": compiled["version"], "valuation_readiness": readiness,
                                       "mode": "ordinary_investment", "metrics": compiled.get("metrics", {})}}


def merge_prepared_facts(payload: AgentOutputPayload, seeds: list[dict]) -> AgentOutputPayload:
    """Reserve backend aliases; provider narration cannot rewrite their facts."""
    reserved = {row["claim_id"] for row in seeds}
    if any(fact.claim_id in reserved for fact in payload.fact_claims):
        raise ValueError("Prepared financial facts are backend-owned; reference their aliases without re-emitting them.")
    if len(payload.fact_claims) + len(seeds) > 15:
        raise ValueError("Prepared financial facts and additional analyst claims exceed the 15-fact research limit.")
    return payload.model_copy(update={"fact_claims": [*[FactClaim.model_validate(row) for row in seeds], *payload.fact_claims],
        "source_refs": list(dict.fromkeys([*payload.source_refs, *[row["source_ref"] for row in seeds]]))})
