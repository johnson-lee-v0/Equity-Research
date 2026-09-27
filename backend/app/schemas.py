"""Shared, versioned API and provider contracts."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


Namespace = Literal["real", "demo", "simulation"]
RunOrigin = Literal["user", "reddit", "monitor", "repair", "simulation"]
ResearchContract = Literal["five-questions.v1"]
Status = Literal[
    "idle",
    "queued",
    "running",
    "waiting_for_evidence",
    "waiting_for_review",
    "blocked",
    "failed",
    "cancelled",
    "completed",
    "interrupted",
]
ModelProvider = Literal["codex", "ollama"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ModelConfig(StrictModel):
    provider: ModelProvider
    model: str = Field(min_length=1, max_length=160)
    reasoning_effort: str | None = Field(default=None, max_length=40)
    profile: str = Field(default="gpt-first", min_length=1, max_length=80)

    @field_validator("model")
    @classmethod
    def no_command_text(cls, value: str) -> str:
        if any(char in value for char in ("\n", "\r", "\x00", "/", "\\")):
            raise ValueError("model must be an identifier, not a path or command")
        return value.strip()


class Task(StrictModel):
    id: str
    run_id: str
    agent_id: str
    namespace: Namespace
    title: str
    status: Status
    paused: bool = False
    started_at: str | None = None
    updated_at: str
    completed_at: str | None = None
    elapsed_seconds: float | None = None
    blocking_reason: str | None = None
    progress_message: str | None = None
    input_refs: list[str] = Field(default_factory=list)
    output_id: str | None = None
    attempt_id: str | None = None
    resolved_model: ModelConfig | None = None
    # Additive read-model fields.  Older task rows and clients simply receive
    # an empty context while new tasks expose exactly what was reused and why.
    memory_context: "MemoryContext | None" = None
    assignment_reason: str | None = None
    origin: RunOrigin = "user"


class Agent(StrictModel):
    id: str
    name: str
    title: str
    mandate: str
    location: str
    status: Status
    paused: bool = False
    current_task: Task | None = None
    queued_count: int = 0
    output_count: int = 0
    model: ModelConfig
    model_source: Literal["task_override", "role_override", "profile", "firm_default"] = "firm_default"


class FactBindingCheck(StrictModel):
    key: str = Field(min_length=1, max_length=120)
    status: Literal["pass", "fail", "unavailable", "ambiguous"] = "unavailable"
    reason: str = Field(default="", max_length=1_000)


class FactClaim(StrictModel):
    claim_id: str | None = Field(default=None, min_length=1, max_length=160, pattern=r"^[A-Za-z][A-Za-z0-9_.:-]*$")
    claim: str
    value: str | None = None
    unit: str | None = None
    period: str | None = None
    source_ref: str = Field(min_length=1, max_length=300)
    locator: str = Field(min_length=1, max_length=500)
    # Current projections may carry an explicit semantic binding.  The
    # original free-text fields remain readable for v1 records, while these
    # fields let the calculator distinguish a metric, period, scale and share
    # basis before it treats a number as a financial input.
    subject: str | None = Field(default=None, max_length=300)
    metric: str | None = Field(default=None, max_length=160)
    scale: str | None = Field(default=None, max_length=40)
    currency: str | None = Field(default=None, max_length=12)
    period_start: str | None = None
    period_end: str | None = None
    basis: str | None = Field(default=None, max_length=120)
    statement_type: str | None = Field(default=None, max_length=80)
    source_quote: str | None = Field(default=None, max_length=2_000)
    semantic_status: Literal["supported", "mismatch", "ambiguous", "unavailable"] | None = None
    binding_checks: list[FactBindingCheck] = Field(default_factory=list, max_length=20)
    matched_excerpt: str | None = Field(default=None, max_length=2_000)
    source_version: str | None = Field(default=None, max_length=160)
    freshness: str | None = Field(default=None, max_length=40)

    @field_validator("claim_id")
    @classmethod
    def local_claim_identifier(cls, value: str | None) -> str | None:
        if value and value.startswith("fact_"):
            raise ValueError("claim_id is output-local; the fact_ prefix is reserved for saved fact IDs")
        return value


GapTerminalReason = Literal["auth", "nonpublic", "no_new_evidence", "budget", "unsupported", "already_resolved"]
GapStatus = Literal["open", "in_progress", "resolved", "terminal"]


class MissingGap(StrictModel):
    """A bounded, actionable evidence gap emitted by an analyst."""

    key: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=4_000)
    relevant_role: str = Field(default="A00", min_length=3, max_length=4)
    reopen_when: str = Field(default="", max_length=2_000)
    source_requirements: list[str] = Field(default_factory=list, max_length=8)
    terminal_reason: GapTerminalReason | None = None


class EvidenceGap(StrictModel):
    """Durable gap ledger projection returned by run and gap endpoints."""

    id: str
    root_run_id: str
    namespace: Namespace
    key: str
    description: str
    assigned_agent_id: str
    status: GapStatus
    reopen_when: str | None = None
    source_fingerprint: str | None = None
    repair_run_id: str | None = None
    repair_round: int = Field(default=0, ge=0, le=2)
    terminal_reason: GapTerminalReason | None = None
    resolved_by_output_id: str | None = None
    created_at: str
    updated_at: str


class MemoryItem(StrictModel):
    """One bounded memory reference included in a task packet."""

    record_id: str
    record_type: Literal["fact", "research", "decision", "source", "simulation", "output"]
    namespace: Namespace
    title: str
    excerpt: str = ""
    source_refs: list[str] = Field(default_factory=list, max_length=20)
    source_versions: list[dict[str, Any]] = Field(default_factory=list, max_length=20)
    observed_at: str | None = None
    retrieved_at: str | None = None
    freshness: Literal["fresh", "reused", "stale", "unknown"] = "unknown"
    reuse_reason: str = ""
    stale: bool = False


class MemoryContext(StrictModel):
    """Visible task-level memory decision and bounded effective packet."""

    namespace: Namespace
    run_id: str
    task_id: str | None = None
    # ``source_ids`` preserves the explicitly attached packet; the effective
    # list is the bounded current-head subset used for memory additions.
    source_ids: list[str] = Field(default_factory=list, max_length=100)
    effective_source_ids: list[str] = Field(default_factory=list, max_length=8)
    reused: list[MemoryItem] = Field(default_factory=list, max_length=8)
    fresh: list[MemoryItem] = Field(default_factory=list, max_length=8)
    freshness_as_of: str | None = None
    reason: str = ""
    current_price_refresh_required: bool = False
    refresh_requirements: list[dict[str, Any]] = Field(default_factory=list, max_length=5)
    memory_char_budget: int | None = Field(default=None, ge=0)


class PriceRange(StrictModel):
    """Evidence-backed price range; values remain absent when unsupported."""

    lower: str | None = None
    upper: str | None = None
    currency: str | None = None
    as_of: str | None = None
    source_refs: list[str] = Field(default_factory=list, max_length=20)
    basis: str = ""
    missing_reason: str | None = None


# The legacy ``stance``/``defer`` vocabulary remains readable on historical
# outputs.  New decision views use the small, explicit outcome vocabulary
# below.  Execution state is intentionally separate: an unavailable account
# packet is an operational input problem, not a bearish investment judgment.
DecisionOutcome = Literal["recommend", "watchlist", "decline"]
DecisionExecutionState = Literal["ready", "awaiting_input", "failed"]
DecisionEvidenceKind = Literal["fact", "opinion", "assumption", "unknown"]
DecisionBlockerKind = Literal["evidence", "sizing", "constraint", "data_quality", "execution", "unknown"]


class DecisionEvidence(StrictModel):
    """One typed statement shown in the current investment case."""

    kind: DecisionEvidenceKind
    statement: str = Field(min_length=1, max_length=10_000)
    source_refs: list[str] = Field(default_factory=list, max_length=20)
    locator: str | None = Field(default=None, max_length=500)
    as_of: str | None = None
    validation_status: Literal["validated", "proposed", "unavailable", "unknown"] = "unknown"


class DecisionBlocker(StrictModel):
    """A current material blocker, kept separate from historical gap attempts."""

    key: str = Field(min_length=1, max_length=160)
    kind: DecisionBlockerKind = "unknown"
    reason: str = Field(min_length=1, max_length=4_000)
    owner: str | None = Field(default=None, max_length=80)
    reopen_when: str | None = Field(default=None, max_length=4_000)
    source_refs: list[str] = Field(default_factory=list, max_length=20)
    status: Literal["open", "resolved", "terminal"] = "open"


# The five-question contract is deliberately additive.  Historical payloads
# remain valid because every field below has a default and the repository only
# applies the stricter bounds when the run is explicitly marked with the
# contract.
KeyQuestionKey = Literal["opportunity", "valuation", "catalyst", "downside", "portfolio_action"]
EvidenceStatus = Literal["complete", "partial", "unavailable"]
JointReviewStatus = Literal["not_run", "agreed", "resolved", "pending", "unavailable"]
JointOutcome = Literal["recommend", "watchlist", "decline", "needs_evidence"]


class KeyQuestionProposal(StrictModel):
    """Provider-authored answer for one code-owned decision question."""

    key: KeyQuestionKey
    answer: str = Field(min_length=1, max_length=800)
    decision_implication: str = Field(default="", max_length=400)
    supporting_claim_ids: list[str] = Field(default_factory=list, max_length=2)
    contradicting_claim_ids: list[str] = Field(default_factory=list, max_length=1)
    unknowns: list[str] = Field(default_factory=list, max_length=2)


class VerifiedQuestionFact(StrictModel):
    """Repository-projected fact shown under a canonical key question."""

    fact_id: str = Field(min_length=1, max_length=160)
    role: Literal["support", "contradiction"]
    claim: str = Field(min_length=1, max_length=2_000)
    value: str | None = Field(default=None, max_length=300)
    unit: str | None = Field(default=None, max_length=80)
    period: str | None = Field(default=None, max_length=120)
    source_ref: str = Field(min_length=1, max_length=300)
    locator: str = Field(min_length=1, max_length=500)
    semantic_status: Literal["supported", "mismatch", "ambiguous", "unavailable"] = "unavailable"
    freshness: str = Field(default="unknown", max_length=40)


class KeyQuestion(StrictModel):
    """Canonical code-owned question and its verified evidence projection."""

    key: KeyQuestionKey
    question: str = Field(min_length=1, max_length=300)
    answer: str = Field(default="", max_length=800)
    decision_implication: str = Field(default="", max_length=400)
    evidence_status: EvidenceStatus = "unavailable"
    verified_facts: list[VerifiedQuestionFact] = Field(default_factory=list, max_length=3)
    unknowns: list[str] = Field(default_factory=list, max_length=2)


class AstraLayaResponse(StrictModel):
    """Astra's response to the pre-A11 local classifier assessment."""

    position: Literal["agree", "override", "unable"]
    reason: str = Field(default="", max_length=2_000)
    fact_claim_ids: list[str] = Field(default_factory=list, max_length=15)
    question_keys: list[KeyQuestionKey] = Field(default_factory=list, max_length=5)


class JointDecisionDisagreement(StrictModel):
    question_key: KeyQuestionKey | None = None
    reason: str = Field(min_length=1, max_length=1_000)


class JointDecisionReview(StrictModel):
    """Code-owned read model assembled from immutable model receipts."""

    status: JointReviewStatus = "not_run"
    laya_model: str | None = Field(default=None, max_length=200)
    laya_revision: str | None = Field(default=None, max_length=200)
    laya_outcome: JointOutcome | None = None
    astra_outcome: JointOutcome | None = None
    resolution: str = Field(default="", max_length=4_000)
    # Safe code-derived diagnostic for unavailable/failed local participation;
    # raw provider tracebacks and packet text never enter the canonical case.
    failure_reason: str | None = Field(default=None, max_length=400)
    disagreements: list[JointDecisionDisagreement] = Field(default_factory=list, max_length=5)
    reviewed_at: str | None = None
    pre_receipt_id: str | None = Field(default=None, max_length=160)
    post_receipt_id: str | None = Field(default=None, max_length=160)
    post_proposal_hash: str | None = Field(default=None, max_length=128)
    verified_fact_ids: list[str] = Field(default_factory=list, max_length=15)
    astra_reasoned: bool = False


class WatchTrigger(StrictModel):
    """A typed condition that can reopen or refresh a watchlist case."""

    type: Literal["price", "catalyst", "evidence", "date"]
    condition: str = Field(min_length=1, max_length=4_000)
    operator: Literal["at_or_below", "at_or_above", "between", "crosses", "occurs", "changes", "on_or_after"] | None = None
    threshold: str | None = None
    upper_threshold: str | None = None
    currency: str | None = Field(default=None, max_length=12)
    catalyst: str | None = Field(default=None, max_length=2_000)
    evidence_condition: str | None = Field(default=None, max_length=4_000)
    trigger_date: str | None = None
    reopen_when: str = Field(default="", max_length=4_000)
    review_at: str | None = None
    status: Literal["active", "paused", "unavailable", "fired", "expired"] = "active"
    source_refs: list[str] = Field(default_factory=list, max_length=20)


class DecisionSizing(StrictModel):
    """Code-owned whole-share sizing and its explicit operational outcome."""

    execution_state: DecisionExecutionState
    account_id: str | None = None
    direction: Literal["long", "short"] = "long"
    currency: str | None = None
    entry_price: str | None = None
    # ``entry_price`` remains the executable/reference price for v1 clients.
    # v2 keeps the conservative notional price and adverse-risk price
    # separate, which is essential for a short range.
    notional_cap_price: str | None = None
    risk_entry_price: str | None = None
    planned_loss_per_share: str | None = None
    planned_loss: str | None = None
    approved_budget: str | None = None
    proposed_budget: str | None = None
    available_cash: str | None = None
    shares: int | None = Field(default=None, ge=0)
    maximum_permitted_shares: int | None = Field(default=None, ge=0)
    recommended_shares: int | None = Field(default=None, ge=0)
    recommended_notional: str | None = None
    recommended_planned_loss: str | None = None
    allocation_rationale: str | None = Field(default=None, max_length=4_000)
    notional: str | None = None
    resulting_cash: str | None = None
    risk_budget: str | None = None
    stop_price: str | None = None
    stop_price_currency: str | None = None
    borrow_available: bool | None = None
    short_permission: bool | None = None
    borrow_cost_status: str | None = Field(default=None, max_length=80)
    margin_terms_confirmed: bool | None = None
    margin_available: str | None = None
    missing_inputs: list[str] = Field(default_factory=list, max_length=50)
    checks: list[dict[str, Any]] = Field(default_factory=list, max_length=50)
    formula: str = "floor(usable_budget / entry_price)"
    reason: str = Field(default="", max_length=4_000)
    binding_cap: str | None = None
    policy_status: Literal["approved", "proposed", "unconfigured"] = "unconfigured"
    recommend_requires: list[str] = Field(default_factory=list, max_length=20)


class FutureTarget(StrictModel):
    """A future valuation/exit target kept distinct from an entry trigger."""

    price: str | None = None
    lower: str | None = None
    upper: str | None = None
    currency: str | None = None
    as_of: str | None = None
    source_refs: list[str] = Field(default_factory=list, max_length=20)
    basis: str = ""
    horizon: str | None = None
    missing_reason: str | None = None


class ForecastAcceptance(StrictModel):
    """Separate calculation completion, input quality, and decision acceptance."""

    calculation_status: Literal["complete", "insufficient_evidence", "not_run"]
    data_quality_status: Literal["valid", "degraded", "invalid", "unknown"]
    model_acceptance_status: Literal["accepted", "rejected", "unavailable", "not_applicable"]
    scenario_assessment: Literal["usable", "do_not_use", "not_assessed"] = "not_assessed"
    scenario_reason: str | None = Field(default=None, max_length=4_000)
    accepted: bool = False
    reasons: list[str] = Field(default_factory=list, max_length=20)
    source_refs: list[str] = Field(default_factory=list, max_length=20)


class ThesisBlock(StrictModel):
    """Code-visible thesis contract used by the v2 recommendation gate."""

    variant_view: str = ""
    market_expectation: str = ""
    why_now: str = ""
    supporting_claim_ids: list[str] = Field(default_factory=list, max_length=50)
    strongest_opposing_explanation: str = ""
    disconfirming_evidence: list[str] = Field(default_factory=list, max_length=50)
    decision_change_conditions: list[str] = Field(default_factory=list, max_length=50)
    status: Literal["complete", "partial", "unavailable"] = "unavailable"
    missing_inputs: list[str] = Field(default_factory=list, max_length=50)
    source_refs: list[str] = Field(default_factory=list, max_length=50)


class ValuationInput(StrictModel):
    key: str = Field(min_length=1, max_length=160)
    value: str | None = None
    unit: str | None = Field(default=None, max_length=80)
    currency: str | None = Field(default=None, max_length=12)
    period: str | None = Field(default=None, max_length=120)
    scale: str | None = Field(default=None, max_length=40)
    basis: str | None = Field(default=None, max_length=120)
    statement_type: str | None = Field(default=None, max_length=80)
    kind: Literal["fact", "assumption"] = "assumption"
    fact_claim_ids: list[str] = Field(default_factory=list, max_length=50)
    source_refs: list[str] = Field(default_factory=list, max_length=50)
    rationale: str = Field(default="", max_length=4_000)


class ValuationScenarioProposal(StrictModel):
    growth_rate: str | None = Field(default=None, description="Annual EPS growth as a decimal research assumption, e.g. 0.08 for 8%; code derives forward EPS from the baseline.")
    growth_rationale: str = Field(default="", max_length=4_000, description="Why this scenario's growth assumption fits sourced operating drivers; identify uncertainty, not invented consensus.")
    multiple_rationale: str = Field(default="", max_length=4_000, description="Why this exit P/E is appropriate; distinguish assumed valuation discipline from observed peer/history multiples.")
    forecast_fcf: list[str | int | float] | None = Field(default=None, max_length=100)
    discount_rate: str | None = None
    terminal_growth: str | None = None
    rationale: str = Field(default="", max_length=4_000)
    forecast_eps: str | None = None
    eps: str | None = None
    diluted_eps: str | None = None
    exit_multiple: str | None = None
    pe: str | None = None
    multiple: str | None = None
    forecast_metric: str | None = None
    metric: str | None = None
    ev_multiple: str | None = None
    nav_per_unit: str | None = None
    underlying_value: str | None = None
    target_price: str | None = None
    value: str | None = None


class ValuationScenarioSet(StrictModel):
    bear: ValuationScenarioProposal | None = None
    base: ValuationScenarioProposal | None = None
    bull: ValuationScenarioProposal | None = None


class ValuationNormalizationAdjustment(StrictModel):
    fact_claim_ids: list[str] = Field(min_length=1, max_length=1)
    operation: Literal["subtract", "add"]
    rationale: str = Field(min_length=1, max_length=4_000)


class ValuationComparable(StrictModel):
    ticker: str = Field(min_length=1, max_length=80)
    metric: Literal["P/S", "EV/EBITDA", "P/NAV"]
    basis: Literal["trailing", "forward"]
    as_of: str
    nav_basis: Literal["appraised_nav", "reported_nav", "book_equity", "tangible_book_equity"] | None = None
    numerator: ValuationInput
    denominator: ValuationInput
    rationale: str = Field(min_length=1, max_length=4_000)


class ValuationMethodProposal(StrictModel):
    name: str = Field(default="", max_length=80)
    baseline_eps: str | None = Field(default=None, description="Optional scalar alias for the required baseline_eps typed fact row in an EPS growth model.")
    baseline_revenue: str | None = None
    baseline_ebitda: str | None = None
    baseline_nav: str | None = Field(default=None, description="Total equity NAV/book value in currency units, not per share. Declare nav_basis and supply diluted_shares.")
    nav_basis: Literal["appraised_nav", "reported_nav", "book_equity", "tangible_book_equity"] | None = None
    comparables: list[ValuationComparable] = Field(default_factory=list, max_length=20)
    normalization_adjustments: list[ValuationNormalizationAdjustment] = Field(default_factory=list, max_length=20)
    horizon_months: int | None = Field(default=None, ge=1, le=120, description="Forecast horizon in months, default 12 for a baseline EPS growth model; annual growth compounds to this horizon.")
    forecast_years: str | None = Field(default=None, description="Years of earnings growth from the historical fiscal baseline to the forecast fiscal period, independent of the price-target horizon. Defaults to horizon_months/12.")
    forecast_span_rationale: str = Field(default="", max_length=4_000, description="Explain the baseline and forecast fiscal periods when forecast_years is explicit, e.g. FY2025 to FY2027 is two annual growth steps.")
    forecast_eps: str | None = None
    eps: str | None = None
    forward_diluted_eps: str | None = None
    exit_multiple: str | None = None
    exit_pe: str | None = None
    pe: str | None = None
    forecast_fcf: list[str | int | float] = Field(default_factory=list, max_length=100)
    discount_rate: str | None = None
    wacc: str | None = None
    terminal_growth: str | None = None
    excess_cash: str | None = None
    debt: str | None = None
    preferred_claims: str | None = None
    minority_interest: str | None = None
    diluted_shares: str | None = None
    shares: str | None = None
    adr_ratio: str | None = None
    forecast_metric: str | None = None
    ebitda: str | None = None
    metric: str | None = None
    metric_name: str | None = None
    ev_multiple: str | None = None
    multiple: str | None = None
    nav_per_unit: str | None = None
    underlying_value: str | None = None
    spot_price: str | None = None
    target_price: str | None = None
    currency: str | None = None
    period: str | None = None
    forecast_eps_derivation: str = Field(default="", max_length=4_000)
    basis: str | None = None
    share_basis: str | None = None
    discount_convention: str | None = None
    supported: bool | None = None
    source_refs: list[str] = Field(default_factory=list, max_length=100)
    fact_claim_ids: list[str] = Field(default_factory=list, max_length=100)
    inputs: list[ValuationInput] = Field(default_factory=list, max_length=200)
    scenarios: ValuationScenarioSet | None = None
    rationale: str = Field(default="", max_length=4_000)


class ValuationAssumptions(StrictModel):
    # Keep proposals structured while accepting the ergonomic mapping form
    # used by provider adapters (e.g. ``{"name": "eps_multiple", ...}``).
    methods: list[str | ValuationMethodProposal] = Field(default_factory=list, max_length=12)
    scenarios: ValuationScenarioSet | None = None
    inputs: list[ValuationInput] = Field(default_factory=list, max_length=200)
    rationale: str = Field(default="", max_length=4_000)
    asset_class: str | None = Field(default=None, max_length=80)


class ValuationMethod(StrictModel):
    name: str
    status: Literal["complete", "partial", "unavailable", "not_applicable"]
    inputs: list[dict[str, Any]] = Field(default_factory=list, max_length=200)
    formula: str = ""
    steps: list[str] = Field(default_factory=list, max_length=100)
    intermediate_results: dict[str, Any] = Field(default_factory=dict)
    output_prices: dict[str, str] = Field(default_factory=dict)
    reasons: list[str] = Field(default_factory=list, max_length=50)
    source_refs: list[str] = Field(default_factory=list, max_length=100)
    supported: bool = False
    missing_inputs: list[str] = Field(default_factory=list, max_length=100)
    as_of: str | None = None
    rationale: str = ""
    scenario_calculations: dict[str, dict[str, Any]] = Field(default_factory=dict)


class ValuationBlock(StrictModel):
    status: Literal["complete", "partial", "unavailable"] = "unavailable"
    as_of: str | None = None
    horizon: str | None = None
    currency: str | None = None
    methods: list[ValuationMethod] = Field(default_factory=list, max_length=12)
    scenarios: dict[str, str] = Field(default_factory=dict)
    sensitivities: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    missing_inputs: list[str] = Field(default_factory=list, max_length=100)
    code_version: str = "valuation.v3"
    input_hash: str | None = None
    dispersion: dict[str, Any] = Field(default_factory=dict)
    selected_method: str | None = None
    reconciliation_rationale: str | None = None
    rationale: str = ""
    research_context: dict[str, Any] | None = None


class PayoffScenario(StrictModel):
    name: str
    target_price: str | None = None
    price_return: str | None = None
    estimated_costs: dict[str, str | None] = Field(default_factory=dict)
    net_return: str | None = None
    pnl_per_share: str | None = None
    planned_pnl: str | None = None
    source_refs: list[str] = Field(default_factory=list, max_length=50)


class OpportunityCostProjection(StrictModel):
    """Code-owned cash alternative for a proposed allocation."""

    status: Literal["complete", "partial", "unavailable"] = "unavailable"
    alternative: Literal["cash"] = "cash"
    reason: str = ""
    as_of: str | None = None
    start_at: str | None = None
    end_at: str | None = None
    horizon: str | None = None
    days: int | None = Field(default=None, ge=0)
    account_id: str | None = None
    currency: str | None = None
    principal: str | None = None
    interest_rate: str | None = None
    interest_rate_currency: str | None = None
    interest_rate_period: str | None = None
    interest_compounding: str | None = None
    convention: str | None = None
    day_basis: str | None = None
    rate_observed_at: str | None = None
    term_expires_at: str | None = None
    rate_assumption: str | None = None
    expected_return: str | None = None
    expected_gain: str | None = None
    expected_value: str | None = None
    benchmark_ticker: str | None = Field(default=None, max_length=20)
    benchmark_rationale: str | None = Field(default=None, max_length=2_000)
    benchmark_status: Literal["unavailable"] = "unavailable"
    benchmark_expected_return: str | None = None
    benchmark_reason: str | None = None
    source_refs: list[str] = Field(default_factory=list, max_length=50)
    missing_inputs: list[str] = Field(default_factory=list, max_length=50)
    limitations: list[str] = Field(default_factory=list, max_length=50)
    code_version: str = "opportunity-cost.v1"


class PayoffBlock(StrictModel):
    status: Literal["complete", "partial", "unavailable"] = "unavailable"
    reference_entry: str | None = None
    direction: Literal["long", "short"] | None = None
    horizon: str | None = None
    scenarios: list[PayoffScenario] = Field(default_factory=list, max_length=12)
    reward_to_risk: str | None = None
    breakeven_price: str | None = None
    opportunity_cost: OpportunityCostProjection = Field(default_factory=OpportunityCostProjection)
    missing_inputs: list[str] = Field(default_factory=list, max_length=50)
    limitations: list[str] = Field(default_factory=list, max_length=50)


class PortfolioExposure(StrictModel):
    instrument: str
    account_id: str | None = None
    issuer: str | None = None
    sector: str | None = None
    currency: str | None = None
    direction: Literal["long", "short"] | None = None
    market_value: str | None = None
    weight: str | None = None
    known_common_theme: str | None = None
    status: Literal["known", "partial", "unknown"] = "unknown"
    missing_inputs: list[str] = Field(default_factory=list, max_length=20)
    source_refs: list[str] = Field(default_factory=list, max_length=20)


class PortfolioCheck(StrictModel):
    key: str
    status: Literal["pass", "fail", "unavailable", "not_applicable"]
    reason: str


class PortfolioContext(StrictModel):
    status: Literal["complete", "partial", "unavailable"] = "unavailable"
    snapshot_id: str | None = None
    as_of: str | None = None
    base_currency: str | None = None
    before: dict[str, Any] = Field(default_factory=dict)
    after: dict[str, Any] = Field(default_factory=dict)
    exposures: list[PortfolioExposure] = Field(default_factory=list, max_length=500)
    checks: list[PortfolioCheck] = Field(default_factory=list, max_length=100)
    missing_inputs: list[str] = Field(default_factory=list, max_length=100)
    code_version: str | None = None


class CatalystEvent(StrictModel):
    description: str
    event_date: str | None = None
    date_kind: Literal["confirmed", "expected", "review"] = "review"
    source_refs: list[str] = Field(default_factory=list, max_length=50)


class ActionPlan(StrictModel):
    entry_condition: str = ""
    exit_condition: str = ""
    invalidation_condition: str = ""
    hedge_objective: str | None = Field(default=None, max_length=4_000)
    hedge_source_refs: list[str] = Field(default_factory=list, max_length=50)
    max_loss_basis: Literal["stop", "scenario", "unavailable"] = "unavailable"
    catalyst_events: list[CatalystEvent] = Field(default_factory=list, max_length=20)
    review_at: str | None = None
    expires_at: str | None = None
    status: Literal["complete", "partial", "unavailable"] = "unavailable"
    missing_inputs: list[str] = Field(default_factory=list, max_length=50)
    benchmark_ticker: str | None = Field(default=None, max_length=20)
    benchmark_rationale: str | None = Field(default=None, max_length=2_000)


class RecommendationCheck(StrictModel):
    key: str
    status: Literal["pass", "fail", "unavailable", "not_applicable"]
    reason: str


class RecommendationGate(StrictModel):
    status: Literal["pass", "blocked"] = "blocked"
    checks: list[RecommendationCheck] = Field(default_factory=list, max_length=100)
    missing_inputs: list[str] = Field(default_factory=list, max_length=100)


class LifecycleBlock(StrictModel):
    state: Literal["watchlist", "recommended", "held", "declined", "closed"] | None = None
    reopen_when: list[str] = Field(default_factory=list, max_length=50)
    status: Literal["complete", "partial", "unavailable"] = "unavailable"
    missing_inputs: list[str] = Field(default_factory=list, max_length=20)


class CanonicalCandidateDecision(StrictModel):
    """One authoritative, versioned investment decision for one candidate."""

    schema_version: str = "case-decision.v2"
    decision_revision: int = Field(default=1, ge=1)
    ticker: str = Field(min_length=1, max_length=20)
    instrument: str | None = Field(default=None, max_length=300)
    issuer: str | None = Field(default=None, max_length=300)
    sector: str | None = Field(default=None, max_length=120)
    asset_class: str | None = Field(default=None, max_length=80)
    direction: Literal["long", "short"] = "long"
    strategy: Literal["long_term", "trade"] | None = None
    outcome: DecisionOutcome | None = None
    execution_state: DecisionExecutionState
    rationale: str = Field(min_length=1, max_length=10_000)
    as_of: str | None = None
    horizon: str | None = Field(default=None, max_length=100)
    entry: PriceRange | None = None
    future_target: FutureTarget | None = None
    sizing: DecisionSizing
    risks: list[str] = Field(default_factory=list, max_length=50)
    catalysts: list[str] = Field(default_factory=list, max_length=50)
    invalidation: list[str] = Field(default_factory=list, max_length=50)
    source_refs: list[str] = Field(default_factory=list, max_length=50)
    evidence: list[DecisionEvidence] = Field(default_factory=list, max_length=100)
    material_blockers: list[DecisionBlocker] = Field(default_factory=list, max_length=20)
    watch_triggers: list[WatchTrigger] = Field(default_factory=list, max_length=20)
    technical_indicators: dict[str, Any] = Field(default_factory=dict)
    instrument_identity: dict[str, Any] = Field(default_factory=dict)
    forecast: ForecastAcceptance | None = None
    thesis: ThesisBlock = Field(default_factory=ThesisBlock)
    valuation: ValuationBlock = Field(default_factory=ValuationBlock)
    payoff: PayoffBlock = Field(default_factory=PayoffBlock)
    portfolio_context: PortfolioContext = Field(default_factory=PortfolioContext)
    action_plan: ActionPlan = Field(default_factory=ActionPlan)
    recommendation_gate: RecommendationGate = Field(default_factory=RecommendationGate)
    lifecycle: LifecycleBlock = Field(default_factory=LifecycleBlock)
    key_questions: list[KeyQuestion] = Field(default_factory=list, max_length=5)
    joint_review: JointDecisionReview = Field(default_factory=JointDecisionReview)


class CanonicalCaseDecision(StrictModel):
    """Current case projection shared by journal, results, and watchlist views."""

    schema_version: str = "case-decision.v2"
    run_id: str
    research_contract: ResearchContract | None = None
    allocation_mode: Literal["alternatives", "combined"] = "combined"
    decision_revision: int = Field(default=1, ge=1)
    as_of: str | None = None
    outcome: Literal["recommend", "watchlist", "decline", "mixed"] | None = None
    execution_state: DecisionExecutionState
    candidates: list[CanonicalCandidateDecision] = Field(default_factory=list, max_length=20)
    material_blockers: list[DecisionBlocker] = Field(default_factory=list, max_length=50)

    @property
    def decisions(self) -> list[CanonicalCandidateDecision]:
        """Compatibility view for callers that call candidates decisions."""
        return self.candidates


class ValuationReview(StrictModel):
    status: Literal["accept", "disagree", "reject"]
    reason: str = Field(min_length=1, max_length=2000)


class CandidateDecisionBrief(StrictModel):
    """Per instrument CIO brief used when a run contains several candidates."""

    ticker: str = Field(min_length=1, max_length=20)
    instrument: str | None = Field(default=None, max_length=300)
    direction: Literal["long", "short"] | None = None
    strategy: Literal["long_term", "trade"] | None = None
    horizon: str | None = Field(default=None, max_length=100)
    stance: Literal["enter", "watch", "defer", "avoid"] | None = None
    entry_advice: str = ""
    entry_plan: str = ""
    entry_zone: PriceRange | None = None
    target_price: str | None = None
    target_price_currency: str | None = None
    target_price_as_of: str | None = None
    target_price_source_refs: list[str] = Field(default_factory=list, max_length=20)
    target_price_basis: str = ""
    target_price_missing_reason: str | None = None
    # Optional explicit stop used by the code sizing service when a risk
    # budget is configured.  It is kept separate from a future valuation
    # target and remains unavailable unless the CIO supplies it.
    stop_price: str | None = None
    stop_price_currency: str | None = None
    scenario_assessment: Literal["usable", "do_not_use", "not_assessed"] = "not_assessed"
    scenario_reason: str | None = Field(default=None, max_length=4_000)
    risks: list[str] = Field(default_factory=list, max_length=50)
    catalysts: list[str] = Field(default_factory=list, max_length=50)
    invalidation_conditions: list[str] = Field(default_factory=list, max_length=50)
    missing_inputs: list[str] = Field(default_factory=list, max_length=50)
    watch_triggers: list[WatchTrigger] = Field(default_factory=list, max_length=20)
    thesis: ThesisBlock | None = None
    valuation_assumptions: ValuationAssumptions | None = None
    valuation_review: ValuationReview | None = None
    action_plan: ActionPlan | None = None
    allocation_rationale: str | None = Field(default=None, max_length=4_000)
    issuer: str | None = Field(default=None, max_length=300)
    issuer_name: str | None = Field(default=None, max_length=300)
    sector: str | None = Field(default=None, max_length=120)
    asset_class: str | None = Field(default=None, max_length=80)
    recommended_shares: int | None = Field(default=None, ge=0)
    borrow_available: bool | None = None
    short_permission: bool | None = None
    borrow_cost_status: str | None = Field(default=None, max_length=80)
    margin_terms_confirmed: bool | None = None
    margin_available: str | None = None
    margin_currency: str | None = Field(default=None, max_length=12)
    # Five-question proposals are provider inputs.  Verification and Laya
    # participation are projected by repository code into canonical fields.
    key_questions: list[KeyQuestionProposal] = Field(default_factory=list, max_length=5)
    laya_response: AstraLayaResponse | None = None


class PriceScenarioSnapshot(StrictModel):
    """A deterministic A07 snapshot kept alongside the immutable output."""

    code_version: str
    method: str
    status: Literal["complete", "insufficient_evidence"]
    ticker: str
    currency: str = "USD"
    as_of: str
    source_refs: list[str] = Field(default_factory=list, max_length=100)
    source_hashes: dict[str, str] = Field(default_factory=dict)
    input_hash: str
    result_hash: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    calibration: dict[str, Any] = Field(default_factory=dict)
    scenarios: dict[str, Any] = Field(default_factory=dict)
    assumptions: list[str] = Field(default_factory=list, max_length=50)
    limitations: list[str] = Field(default_factory=list, max_length=50)
    missing_reason: str | None = None
    calculation_status: Literal["complete", "insufficient_evidence"] | None = None
    data_quality_status: Literal["valid", "degraded", "invalid", "unknown"] | None = None
    model_acceptance_status: Literal["accepted", "rejected", "unavailable", "not_applicable"] | None = None
    forecast_accepted: bool | None = None
    data_quality: dict[str, Any] = Field(default_factory=dict)
    model_acceptance: dict[str, Any] = Field(default_factory=dict)
    forecast_status: Literal["accepted", "blocked", "unavailable"] | None = None
    acceptance_reasons: list[str] = Field(default_factory=list, max_length=50)


class DecisionBrief(StrictModel):
    """Structured CIO summary with separate entry and target concepts."""

    ticker: str | None = Field(default=None, max_length=20)
    allocation_mode: Literal["alternatives", "combined"] = "combined"
    horizon: str | None = Field(default=None, max_length=100)
    stance: Literal["enter", "watch", "defer", "avoid"] | None = None
    entry_advice: str = ""
    entry_plan: str = ""
    entry_zone: PriceRange | None = None
    target_price: str | None = None
    target_price_currency: str | None = None
    target_price_as_of: str | None = None
    target_price_source_refs: list[str] = Field(default_factory=list, max_length=20)
    target_price_basis: str = ""
    target_price_missing_reason: str | None = None
    stop_price: str | None = None
    stop_price_currency: str | None = None
    scenario_assessment: Literal["usable", "do_not_use", "not_assessed"] = "not_assessed"
    scenario_reason: str | None = Field(default=None, max_length=4_000)
    risks: list[str] = Field(default_factory=list, max_length=50)
    catalysts: list[str] = Field(default_factory=list, max_length=50)
    invalidation_conditions: list[str] = Field(default_factory=list, max_length=50)
    missing_inputs: list[str] = Field(default_factory=list, max_length=50)
    candidate_briefs: list[CandidateDecisionBrief] = Field(default_factory=list, max_length=20)
    direction: Literal["long", "short"] | None = None
    watch_triggers: list[WatchTrigger] = Field(default_factory=list, max_length=20)
    thesis: ThesisBlock | None = None
    valuation_assumptions: ValuationAssumptions | None = None
    action_plan: ActionPlan | None = None
    asset_class: str | None = Field(default=None, max_length=80)
    benchmark_ticker: str | None = Field(default=None, max_length=20)
    benchmark_rationale: str | None = Field(default=None, max_length=2_000)
    key_questions: list[KeyQuestionProposal] = Field(default_factory=list, max_length=5)
    laya_response: AstraLayaResponse | None = None


class Calculation(StrictModel):
    label: str
    value: str | None = None
    unit: str | None = None
    formula: str
    missing_reason: str | None = None
    operation: Literal["ratio", "return", "moving_average", "issuance_assets", "issuance_shares", "issuance_per_share", "price_discount"] | None = None
    input_fact_indices: list[int] = Field(default_factory=list, max_length=20)
    window: int | None = Field(default=None, ge=1, le=1000)
    assumed_discount_fraction: str | None = Field(default=None, max_length=80)
    assumption_rationale: str | None = Field(default=None, max_length=2_000)


class AllocationProposal(StrictModel):
    account_id: str | None = None
    symbol: str | None = None
    sector: str | None = None
    target_position_weight: str | None = None
    sector_weight_after: str | None = None
    cash_weight_after: str | None = None


class RedditTriage(StrictModel):
    """The Chief of Staff's bounded decision for one Reddit submission.

    This is deliberately separate from the ordinary user-question route.  A
    Reddit item is eligible for specialist work only when this decision is
    present and the repository can bind its excerpt/tickers to the retained
    source version for that submission.
    """

    classification: Literal["thesis", "yolo_ticker", "skip"]
    reason: str = Field(min_length=1, max_length=4_000)
    thesis_summary: str = Field(default="", max_length=4_000)
    evidence_excerpt: str = Field(default="", max_length=12_000)
    # A bounded issuer lead lets the workflow preserve a name-only Reddit
    # thesis (for example, ``Bloom Energy - YOLO SHORT``) while public
    # discovery resolves the eventual ticker.  It is provenance, not ticker
    # verification or a recommendation.
    issuer_name: str | None = Field(default=None, max_length=300)
    tickers: list[str] = Field(default_factory=list, max_length=20)


class RoutingPlan(StrictModel):
    """The bounded routing decision produced by the Chief of Staff.

    The fields intentionally stay small: A00 chooses an intent, a horizon,
    relevant tickers, specialist IDs and a few search questions.  It does not
    create evidence or an allocation proposal.  The strict fields below are
    the only routing data the backend consumes.
    """

    intent: Literal["research", "direct_answer"] = "research"
    # Keep the planning horizon as bounded text.  The UI may offer common
    # presets, while A00 can preserve an arbitrary user phrase such as
    # ``2 years`` or ``5 days`` in the durable plan.
    horizon: str | None = Field(default=None, max_length=100)
    tickers: list[str] = Field(default_factory=list, max_length=20)
    selected_analysts: list[str] = Field(default_factory=list, max_length=12)
    research_queries: list[str] = Field(default_factory=list, max_length=6)
    rationale: str = Field(default="", max_length=10_000)
    research_contract: ResearchContract | None = None
    # Optional for ordinary user questions.  Root Reddit intake runs require
    # this field; repair descendants intentionally retain their normal route
    # semantics and are never screened as a second Reddit submission.
    reddit_triage: RedditTriage | None = None


class ResearchCandidate(StrictModel):
    """A discovery lead kept separate from a portfolio allocation."""

    ticker: str = Field(min_length=1, max_length=20)
    name: str | None = Field(default=None, max_length=300)
    rationale: str = Field(default="", max_length=4_000)
    source_urls: list[str] = Field(default_factory=list, max_length=6)
    verified: bool = False
    unverified_reason: str | None = Field(default=None, max_length=1_000)


class AgentOutputPayload(StrictModel):
    """The only provider-generated structure accepted for a committed output."""

    status: Literal["completed", "insufficient_evidence", "needs_review"]
    # ``None`` preserves every historical output.  New lean research runs set
    # this explicitly before A00 is dispatched.
    research_contract: ResearchContract | None = None
    allocation_mode: Literal["alternatives", "combined"] = "combined"
    title: str = Field(min_length=1, max_length=300)
    summary: str = Field(min_length=1, max_length=10_000)
    analysis: str = Field(min_length=1, max_length=100_000)
    fact_claims: list[FactClaim] = Field(default_factory=list, max_length=100)

    @field_validator("fact_claims")
    @classmethod
    def unique_local_claim_ids(cls, claims: list[FactClaim]) -> list[FactClaim]:
        identifiers = [claim.claim_id for claim in claims if claim.claim_id]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Each fact claim_id must be unique within this output")
        return claims
    assumptions: list[str] = Field(default_factory=list, max_length=100)
    calculations: list[Calculation] = Field(default_factory=list, max_length=100)
    counterarguments: list[str] = Field(default_factory=list, max_length=100)
    missing_data: list[str] = Field(default_factory=list, max_length=100)
    proposed_action: str = Field(default="", max_length=20_000)
    invalidation_conditions: list[str] = Field(default_factory=list, max_length=100)
    next_review_at: str | None = None
    source_refs: list[str] = Field(default_factory=list, max_length=100)
    # Explicit gaps drive bounded Chief of Staff repair work.  ``missing_data``
    # remains as the human-readable compatibility field.
    missing_gaps: list[MissingGap] = Field(default_factory=list, max_length=20)
    review_disposition: Literal["accept", "reject", "defer", "revise"] | None = None
    revision_requests: list[str] = Field(default_factory=list, max_length=20)
    decision_disposition: Literal["accept", "reject", "defer", "recommend"] | None = None
    proposal: AllocationProposal | None = None
    decision_brief: DecisionBrief | None = None
    candidate_briefs: list[CandidateDecisionBrief] = Field(default_factory=list, max_length=20)
    laya_response: AstraLayaResponse | None = None
    # Top-level aliases keep the provider contract ergonomic while the
    # nested brief is the canonical CIO read model.
    stance: Literal["enter", "watch", "defer", "avoid"] | None = None
    entry_plan: str | None = Field(default=None, max_length=10_000)
    target_price: str | None = None
    target_price_currency: str | None = None
    target_price_as_of: str | None = None
    target_price_source_refs: list[str] = Field(default_factory=list, max_length=20)
    target_price_basis: str = ""
    target_price_missing_reason: str | None = None
    stop_price: str | None = None
    stop_price_currency: str | None = None
    scenario_assessment: Literal["usable", "do_not_use", "not_assessed"] = "not_assessed"
    scenario_reason: str | None = Field(default=None, max_length=4_000)
    entry_zone: PriceRange | None = None
    ticker: str | None = Field(default=None, max_length=20)
    instrument: str | None = Field(default=None, max_length=300)
    horizon: str | None = Field(default=None, max_length=100)
    strategy: Literal["long_term", "trade"] | None = None
    entry_advice: str | None = Field(default=None, max_length=10_000)
    direction: Literal["long", "short"] | None = None
    risks: list[str] = Field(default_factory=list, max_length=50)
    catalysts: list[str] = Field(default_factory=list, max_length=50)
    missing_inputs: list[str] = Field(default_factory=list, max_length=50)
    simulation_snapshot: PriceScenarioSnapshot | None = None
    simulation_snapshots: list[PriceScenarioSnapshot] = Field(default_factory=list, max_length=5)
    # Routing/discovery fields are additive to the original output contract.
    # They are persisted with the immutable output and consumed only by the
    # orchestration boundary for the corresponding A00/A01 task.
    routing_plan: RoutingPlan | None = None
    research_candidates: list[ResearchCandidate] = Field(default_factory=list, max_length=20)
    discovery_queries: list[str] = Field(default_factory=list, max_length=6)
    discovery_urls: list[str] = Field(default_factory=list, max_length=20)
    watch_triggers: list[WatchTrigger] = Field(default_factory=list, max_length=20)
    thesis: ThesisBlock | None = None
    valuation_assumptions: ValuationAssumptions | None = None
    action_plan: ActionPlan | None = None
    benchmark_ticker: str | None = Field(default=None, max_length=20)
    benchmark_rationale: str | None = Field(default=None, max_length=2_000)
    asset_class: str | None = Field(default=None, max_length=80)
    recommended_shares: int | None = Field(default=None, ge=0)
    allocation_rationale: str | None = Field(default=None, max_length=4_000)
    issuer: str | None = Field(default=None, max_length=300)
    sector: str | None = Field(default=None, max_length=120)


class Output(StrictModel):
    id: str
    task_id: str
    agent_id: str
    namespace: Namespace
    version: int
    research_contract: ResearchContract | None = None
    status: Literal["completed", "insufficient_evidence", "needs_review"]
    title: str
    summary: str
    analysis: str
    fact_claims: list[FactClaim]
    assumptions: list[str]
    calculations: list[Calculation]
    counterarguments: list[str]
    missing_data: list[str]
    proposed_action: str
    invalidation_conditions: list[str]
    next_review_at: str | None
    source_refs: list[str]
    missing_gaps: list[MissingGap] = Field(default_factory=list, max_length=20)
    decision_brief: DecisionBrief | None = None
    candidate_briefs: list[CandidateDecisionBrief] = Field(default_factory=list, max_length=20)
    laya_response: AstraLayaResponse | None = None
    stance: Literal["enter", "watch", "defer", "avoid"] | None = None
    entry_plan: str | None = None
    target_price: str | None = None
    target_price_currency: str | None = None
    target_price_as_of: str | None = None
    target_price_source_refs: list[str] = Field(default_factory=list, max_length=20)
    target_price_basis: str = ""
    target_price_missing_reason: str | None = None
    entry_zone: PriceRange | None = None
    ticker: str | None = Field(default=None, max_length=20)
    instrument: str | None = Field(default=None, max_length=300)
    horizon: str | None = Field(default=None, max_length=100)
    strategy: Literal["long_term", "trade"] | None = None
    entry_advice: str | None = Field(default=None, max_length=10_000)
    direction: Literal["long", "short"] | None = None
    risks: list[str] = Field(default_factory=list, max_length=50)
    catalysts: list[str] = Field(default_factory=list, max_length=50)
    missing_inputs: list[str] = Field(default_factory=list, max_length=50)
    simulation_snapshot: PriceScenarioSnapshot | None = None
    simulation_snapshots: list[PriceScenarioSnapshot] = Field(default_factory=list, max_length=5)
    canonical_decision: CanonicalCaseDecision | None = None
    stale: bool = False
    provider: str
    model: str
    reasoning_effort: str | None
    prompt_version: str
    schema_version: str
    created_at: str
    thesis: ThesisBlock | None = None
    valuation_assumptions: ValuationAssumptions | None = None
    action_plan: ActionPlan | None = None
    benchmark_ticker: str | None = Field(default=None, max_length=20)
    benchmark_rationale: str | None = Field(default=None, max_length=2_000)
    asset_class: str | None = Field(default=None, max_length=80)
    recommended_shares: int | None = Field(default=None, ge=0)
    allocation_rationale: str | None = Field(default=None, max_length=4_000)
    issuer: str | None = Field(default=None, max_length=300)
    sector: str | None = Field(default=None, max_length=120)


class Source(StrictModel):
    id: str
    namespace: Namespace
    title: str
    source_type: str
    url: str | None
    publisher: str | None = None
    publication_at: str | None
    observed_at: str | None
    retrieved_at: str
    content_hash: str
    version: int
    supersedes_id: str | None
    content: str
    coverage: str
    freshness: str
    error: str | None = None


class TaskEvent(StrictModel):
    sequence_id: int
    event_id: str
    namespace: Namespace
    run_id: str | None
    task_id: str | None
    attempt_id: str | None
    emitted_at: str
    type: str
    payload: dict[str, Any]


class RunCreate(StrictModel):
    question: str = Field(min_length=1, max_length=20_000)
    namespace: Literal["real", "demo"] = "real"
    # A duration may be omitted so A00 can derive an explicit bounded plan
    # from the request.  Existing clients may continue sending 1m/3m/event,
    # while arbitrary bounded phrases remain valid for the planner.
    horizon: str | None = Field(default=None, max_length=100)
    ticker: str | None = Field(default=None, max_length=20)
    source_ids: list[str] = Field(default_factory=list, max_length=100)
    research_contract: ResearchContract | None = None
    model_override: ModelConfig | None = None
    idempotency_key: str = Field(min_length=8, max_length=200)
    origin: RunOrigin = "user"
    origin_ref: str | None = Field(default=None, max_length=300)
    root_run_id: str | None = Field(default=None, max_length=200)

    @field_validator("ticker")
    @classmethod
    def ticker_format(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.strip().upper()
        if not value or len(value) > 15 or not all(c.isalnum() or c in ".-_" for c in value):
            raise ValueError("ticker must be a short symbol")
        return value


class RunMessage(StrictModel):
    message: str = Field(min_length=1, max_length=20_000)
    idempotency_key: str = Field(min_length=8, max_length=200)
    output_id: str | None = Field(default=None, min_length=1, max_length=200)


class ResearchRequest(StrictModel):
    """A bounded evidence-research repair linked to an existing run."""

    instruction: str = Field(min_length=1, max_length=20_000)
    idempotency_key: str = Field(min_length=8, max_length=200)
    source_ids: list[str] | None = Field(default=None, max_length=100)
    model_override: ModelConfig | None = None
    gap_ids: list[str] = Field(default_factory=list, max_length=20)
    root_run_id: str | None = Field(default=None, max_length=200)


class RedditIntakeRequest(StrictModel):
    """Bounded control request for the local Reddit intake poller."""

    namespace: Literal["real", "demo"] = "real"
    subreddit: str = Field(default="wallstreetbets", min_length=2, max_length=50)
    max_posts: int = Field(default=100, ge=1, le=1000)
    max_pages: int = Field(default=5, ge=1, le=10)
    max_dispatches: int = Field(default=3, ge=0, le=5)
    force_backfill: bool = False


class ControlRequest(StrictModel):
    scope: Literal["firm", "run", "task"]
    id: str | None = None
    action: Literal["pause", "resume", "cancel", "retry", "run_once", "use_archived_evidence", "refresh_earnings_context"]


class ImportRequest(StrictModel):
    namespace: Literal["real", "demo"] = "real"
    kind: Literal["evidence", "transactions", "balances"]
    title: str = Field(min_length=1, max_length=500)
    content: str = Field(min_length=1, max_length=10_000_000)
    source_url: str | None = None
    publication_at: str | None = None
    observed_at: str | None = None
    supersedes_id: str | None = None
    idempotency_key: str = Field(min_length=8, max_length=200)


class SecRequest(StrictModel):
    namespace: Literal["real", "demo"] = "real"
    cik: str = Field(min_length=1, max_length=20)
    form: str | None = Field(default=None, max_length=20)


class RiskSettingsRequest(StrictModel):
    namespace: Literal["real", "demo"] = "real"
    max_position_weight: str | None = None
    max_sector_weight: str | None = None
    cash_floor: str | None = None
    account_restrictions: list[str] = Field(default_factory=list, max_length=100)


class PortfolioLimit(StrictModel):
    """A proposed or approved notional envelope for one strategy bucket."""

    currency: str = Field(min_length=3, max_length=3)
    initial_notional: str
    max_notional: str
    planned_loss_limit: str | None = None


class PortfolioPolicy(StrictModel):
    """User-owned portfolio preferences; proposed values stay non-authoritative."""

    status: Literal["proposed", "approved"] = "proposed"
    max_positions: int = Field(default=10, ge=1, le=500)
    tfsa_long_term_only: bool = True
    allow_tfsa_outflows: bool = False
    allow_chequing_to_nonregistered: bool = True
    limits: dict[str, PortfolioLimit] = Field(default_factory=dict, max_length=12)


class PortfolioPolicyRequest(PortfolioPolicy):
    namespace: Literal["real", "demo"] = "real"


class CoverageRequest(StrictModel):
    namespace: Literal["real", "demo"] = "real"
    sector: str = Field(min_length=1, max_length=100)
    status: Literal["considered", "watch", "active_research", "rejected", "held"]
    reason: str = Field(min_length=1, max_length=4_000)
    reopen_when: str = Field(default="", max_length=4_000)


class ModelPolicyRequest(StrictModel):
    scope: Literal["firm", "role", "profile"]
    agent_id: str | None = None
    config: ModelConfig | None = None
    profile: str | None = None


class ProviderPreflightRequest(StrictModel):
    model: str | None = None
    reasoning_effort: str | None = None
    execute: bool = False


class SimulationCreate(StrictModel):
    title: str = Field(min_length=1, max_length=500)
    question: str = Field(min_length=1, max_length=10_000)
    horizon: str = Field(min_length=1, max_length=100)
    participants: int = Field(ge=6, le=12)
    rounds: int = Field(ge=5, le=10)
    seed: int = Field(ge=0, le=2**63 - 1)
    initial_price: str
    shock_percent: str
    source_ids: list[str] = Field(default_factory=list, max_length=100)
    use_llm: bool = False
    idempotency_key: str = Field(min_length=8, max_length=200)


class MonitoringRequest(StrictModel):
    namespace: Literal["real", "demo"] = "real"
    name: str = Field(min_length=1, max_length=300)
    enabled: bool = False
    timezone: str = Field(default="UTC", min_length=1, max_length=80)
    interval_minutes: int = Field(ge=5, le=31_536_000)
    condition: str = Field(min_length=1, max_length=5_000)
    source_ids: list[str] = Field(default_factory=list, max_length=100)
    mode: Literal["interval_research", "source_change"] = "interval_research"


class MonitoringUpdate(StrictModel):
    enabled: bool


class SettingsRequest(StrictModel):
    key: str = Field(min_length=1, max_length=100)
    value: Any


# Pydantic resolves the task's forward reference after all read-model
# contracts are declared.  Keeping this explicit makes imports robust across
# the supported Pydantic 2 minor versions.
Task.model_rebuild()
