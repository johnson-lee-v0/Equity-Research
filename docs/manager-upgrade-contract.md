# Manager upgrade implementation contract

Architecture decision: 17 September 2026. This supplements the lean workflow in `research-workflow.md` and replaces weaker decision validation rules. Architecture and acceptance decisions belong to Astra Ultra; implementation belongs to Luna Max. This document contains no private account values.

## Boundaries

- Keep A00 routing, A01 discovery, A03 synthesis, deterministic calculations, and A11 final assessment. Keep one bounded evidence continuation. Add no agents or autonomous workflow branches.
- Research, intake dispatch, monitoring dispatch, and model execution remain paused throughout implementation and verification. Tests use isolated synthetic databases and providers.
- Original sources, outputs, attempts, account snapshots, and the four archived cases remain immutable. Corrections append through `CaseDecisionStore.persist(correction_key=..., correction_reason=...)`; never replace the original output or prior decision revision. A correction may revoke unsupported claims but cannot invent new research.
- Current views use the current canonical case projection. Read-only API calls must not persist new corrections, create observations, or queue research. Current quote/status overlays must not rewrite frozen decisions.
- Keep decimal values as strings, currencies explicit, timestamps UTC, and namespace boundaries. Provider-authored fields are proposals; code-owned computed fields cannot be accepted from provider output.

## Shared v2 candidate contract

Extend `CandidateDecisionBrief` and `DecisionBrief` with optional provider proposals `thesis`, `valuation_assumptions`, and `action_plan`. Extend `CanonicalCandidateDecision` with code-validated `thesis`, `valuation`, `payoff`, `portfolio_context`, `action_plan`, `recommendation_gate`, and `lifecycle`. Existing entry, target, catalysts, invalidation, risks and sizing remain compatibility projections, not competing authorities. Historical defaults must be empty/unknown, never valid-by-default.

Canonical case schema version becomes `case-decision.v2` for newly computed revisions. Existing v1 rows remain readable. Optional blocks use explicit `status`, `missing_inputs` and reasons; do not hide unsupported methods by omitting them.

`thesis`:

- `variant_view`: what the research believes the market is missing.
- `market_expectation`: observed consensus/market expectation or explicitly labeled inference, with evidence references; no invented consensus figures.
- `why_now`: the mechanism and time window that can change the assessment.
- `supporting_claim_ids`: candidate-specific semantically supported facts.
- `strongest_opposing_explanation`: a coherent alternative account of the same facts.
- `disconfirming_evidence`: specific observations that would weaken this thesis.
- `decision_change_conditions`: measurable conditions and the resulting reassessment.
- `status`, `missing_inputs`.

`action_plan`:

- `entry_condition`: executable condition, separate from a valuation target.
- `exit_condition`: target/valuation/event exit rule.
- `invalidation_condition`: measurable thesis failure or explicit stop rule.
- `max_loss_basis`: stop, scenario, or unavailable; scenario loss never claims a guaranteed maximum.
- `catalyst_events`: `{description, event_date, date_kind: confirmed|expected|review, source_refs}`. Unknown event dates remain null; a planned review has date_kind=review.
- `review_at`: a concrete planned review timestamp.
- `expires_at`: optional order/setup expiration.
- `status`, `missing_inputs`.

`recommendation_gate = {status: pass|blocked, checks: [{key, status: pass|fail|unavailable|not_applicable, reason}], missing_inputs}`. This is the authoritative publishability gate.

Recommend requires code-validated instrument identity, explicit direction/strategy/horizon, supported entry, usable valuation/payoff, differentiated thesis and opposing explanation, executable exit, measurable invalidation, a dated catalyst or an explicit dated thesis review, fresh material facts/price, and complete applicable portfolio sizing/eligibility checks. Require at least one defensible, supported, asset-appropriate valuation method. The application supports EPS-multiple and DCF/EV methods, but a second method is an optional cross-check; show dispersion and reconciliation when supplied. Missing applicable inputs block Recommend. Qualitative/direct-answer and macro analysis can complete without inventing corporate valuation inputs. A numeric stop is required when a planned-loss policy uses one; long-term theses may use measurable fundamental invalidation and scenario loss with its limitation.

The investment outcome and execution state remain independent. Missing inputs do not manufacture a Decline. Watchlist requires an observable trigger; otherwise show `outcome=null`, `execution_state=awaiting_input`. An explicitly reasoned Decline is a completed investment judgment even when no executable sizing exists. Display operational faults separately from investment decisions.

## Semantic facts and source evidence

The current `_fact_claim_validation` finds a number in a passage and accepts any supported unit with a nonempty period; this does not bind the number to its actual meaning. Preserve historical validation metadata while adding stronger validation for current projections.

Fact binding must cover issuer/instrument, metric, value, unit, scale, currency, period or observation date, accounting/share basis, and locator. Add optional structured fields to `FactClaim` such as `subject`, `metric`, `scale`, `currency`, `period_start`, `period_end`, `basis`, `statement_type`, and `source_quote` (bounded), or an equivalent nested binding object. Typed fields still require corroboration in archived content; they are not self-attestation.

Code returns separate `text_match`, `semantic_status: supported|mismatch|ambiguous|unavailable`, `binding_checks`, `matched_excerpt`, `source_version`, and `freshness`. Legacy `validation_status=validated` may be emitted for a current quantitative fact only when semantic_status=supported. A cited number elsewhere in a table is insufficient. Unit conversions require explicit source scale and audited arithmetic. Parent table headings and selected row/column must stay associated. Distinguish diluted weighted-average shares, period-end shares outstanding, authorized shares, and public float. Distinguish GAAP and adjusted EPS and fiscal from calendar periods.

Do not send every candidate the same facts by default. Candidate evidence references must bind to that issuer or an explicitly shared macro factor. Arbitrary textual assertions cannot become facts merely because a short text value appears in a cited passage. Reddit/author claims stay attributed opinions even when text-matched.

PDF ingestion archives immutable original bytes and extracted page text. Expose page number and table row/column where extraction supports it; retain line locators for compatibility. Use bounded file size/page count and existing safe URL fetching. Corrupt/encrypted/scanned-without-OCR documents report extraction unavailable. No new model/OCR run is implied. Preserve source hash, original MIME type, extraction version, and failure reason. The source viewer must open the retained page/locator.

Freshness uses observation/period end and publication time, not retrieval time alone. Define per-kind policy (market quote, filing, macro release, catalyst date) in code, persist policy/version and as-of. Re-fetching an old page cannot make its facts fresh. Unknown dates, future dates, superseded amendments and materially stale facts cannot pass current recommendation gating.

## Short sizing and portfolio math

Current `_entry_price` chooses the upper bound for both directions. Keep that upper bound as the conservative absolute notional/margin cap price, but introduce `risk_entry_price`: upper bound for a long and lower bound for a short. Preserve a distinct executable/reference price if shown. For a range [L,U], stop S, and risk allowance R:

- Long per-share planned loss = U-S; require S<L for a stop valid throughout the permitted range.
- Short per-share planned loss = S-L; require S>U for a stop valid throughout the permitted range.
- Whole shares = floor(min(all notional caps/U, R/per-share planned loss, other share caps)). Check each cap independently after rounding. Do not convert the risk cap to notional using the wrong entry bound.
- Short sale proceeds are not long cash. Borrow availability, short permission, borrow terms/cost status, and explicit buying-power/margin terms must be available for actionable short sizing. Never infer them from cash.
- Expose `notional_cap_price`, `risk_entry_price`, `planned_loss_per_share`, `planned_loss`, and binding cap/check in sizing. Planned stop loss is not a guaranteed loss ceiling; gaps and borrowing can exceed it.

`portfolio_context` is code-owned: `{status, snapshot_id, as_of, base_currency, before, after, exposures, checks, missing_inputs}`. Exposure rows include instrument, issuer, sector, currency and known common theme. Show gross long, gross short, net exposure, cash headroom, position/sector weights, holding count and incremental planned loss when available. Positions are valued from dated instrument-bound prices and dated FX. Missing prices/currencies/classification produce unknown exposure, never zero. Missing applicable exposures block configured constraints.

Correlated/overlapping alternatives must not be presented as diversification. Derive exact same-underlying/fund overlap only from supported holdings; otherwise label thematic overlap as a research judgment. Empirical correlation is optional and requires aligned sufficient history, method and window; never fabricate correlation coefficients. Stress checks may use transparent shared-factor shocks without probabilities.

For combined candidates, apply shared cash/margin/position/sector/holding-count limits to the whole basket and any existing holdings. For alternatives, each fits independently. Aggregate by identity, not ticker alone: duplicate ticker rows/directions/accounts may otherwise overwrite the `shared_sizing_by_ticker` map. Portfolio snapshot for a decision must come from frozen attempt inputs, not the mutable current run snapshot.

Separate `maximum_permitted_shares` from `recommended_shares`. A policy ceiling does not mean the full allowance is the preferred allocation. A proposed allocation needs a rationale and must fit every code cap; absent that decision, show capacity only and do not publish it as an order quantity. Include liquidity, shared catalyst/event and short-carry exposure with explicit unavailable status when the inputs are absent. Comparison includes retaining cash and a frozen benchmark when supplied; do not invent cash yield or benchmark return.

## Valuation and payoff

`valuation_assumptions` proposes methods and three named scenarios (`bear`, `base`, `bull`). Each input is `{key, value, unit, currency, period, kind: fact|assumption, fact_claim_ids, source_refs, rationale}`. Assumptions must be explicit; source references establish the factual baseline, not the truth of a forecast. Code calculates all output prices.

`valuation = {status: complete|partial|unavailable, as_of, horizon, currency, methods, scenarios, sensitivities, missing_inputs, code_version, input_hash}`. Method records contain ordered inputs, formulas/steps, intermediate results, output prices, status and reasons.

- EPS multiple: forward diluted EPS × exit P/E. Store forecast EPS derivation/baseline, fiscal period, GAAP/adjusted basis, share basis and multiple rationale. Never silently apply a positive P/E to zero/negative EPS.
- DCF: discounted forecast unlevered free cash flows plus discounted terminal value yields enterprise value. Terminal growth must be below discount rate; show explicit years and discount convention. Equity value = EV + excess cash/non-operating assets − debt − preferred claims − minority interest; then divide by appropriate diluted shares and apply documented ADR ratio if needed. Each bridge component is a dated supported fact or a labeled assumption. Explicit zero needs support; absent debt is not zero debt.
- EV multiple alternative/cross-check: forecast EBITDA/other supported operating metric × multiple, followed by the same enterprise-to-equity bridge. For funds/commodities where issuer EPS/DCF is inapplicable, return method status=not_applicable and a supported NAV/underlying asset scenario, not invented corporate accounts.
- Show method dispersion. Do not silently average targets; a chosen target states method and reconciliation rationale.

`payoff = {status, reference_entry, direction, horizon, scenarios: [{name, target_price, price_return, estimated_costs, net_return, pnl_per_share, planned_pnl, source_refs}], reward_to_risk, breakeven_price, missing_inputs}`. Bear/base/bull label business/price scenarios; short returns reverse price direction. Long return=(T-E)/E; short price return=(E-T)/E before costs, with notional denominator explicit. Fees, dividends, borrow costs and FX remain separate and unknown if unsupported. Unsupported costs mean price-only payoff, not a falsely precise net return. Reward/risk requires a valid downside denominator. No invented probabilities, weighted expected return or Monte Carlo percentile as a fair value target.

## Terminal evidence gap reconciliation

The existing gap ledger supports open/in_progress/resolved/terminal and can bulk-close open gaps after an accepted CIO output with no new gaps. Replace omission-based closure with explicit reconciliation.

At terminal CIO commit, transactionally reconcile every prior material gap against candidate requirement keys and validated evidence. A gap is resolved only by named supporting facts/calculations/source versions and the responsible output ID. A now irrelevant gap stays in history with an explicit scope/disposition reason, not “evidence obtained.” Unresolved exhausted work becomes terminal with its real reason and reopen condition. Auth, nonpublic, unsupported and budget reasons never imply fact resolution. A changed fingerprint may justify a new attempt; it must not create duplicate live blockers for the same requirement.

Canonical `material_blockers` must agree with the current reconciled ledger. Historical terminal gaps do not all remain current blockers: display them under evidence history with their disposition. A terminal gap is still a current material blocker when the recommendation requires its missing input. Same-key provider `already_resolved` needs evidence and cannot self-certify.

No automatic historical writes on API reads. Archived corrections add `gap_reconciliation` records or a correction-side overlay tied to the new projection; preserve historical gap events and original CIO output.

## Lifecycle and learning

Extend the existing watchlist projection/service rather than add another scheduler. Cover Watchlist, recommended-but-unexecuted, held, and Declined with explicit reopen conditions. State comes from decision plus user-confirmed holdings/transactions; recommendation never creates a holding.

Lifecycle row fields: `run_id`, `decision_revision`, `candidate`, `lifecycle_state: watchlist|recommended|held|declined|closed`, `current_quote`, `distance_to_entry`, `review_due_at`, `overdue`, `checks`, `learning`. Quote = `{price,currency,as_of,source_ref,kind: last_trade|close,freshness,status}`. Daily bars are labeled close, not live quote. Distance reports signed absolute/percentage distance plus `inside_range`; document denominator and direction. Null/stale quotes stay visible as unavailable.

Trigger states distinguish condition observed, pending admission, reviewing, reviewed, unavailable, expired, and paused. Pausing preserves last observation and overdue state. On resume, date reviews overdue during pause receive one deduplicated review, subject to existing capacity. Stale decisions require reassessment and must not be silently skipped forever. Price/evidence checks may produce a new bounded case revision but no trade.

Current `watch_key` omits revision and `followup_task_id` permanently suppresses recurrence. Use stable condition identity plus an explicit event/episode key (first crossing/date/evidence version) so one event is deduplicated and a materially new observation or deliberate rearm can lead to a later review. No queue explosion on repeated polls.

`learning` is append-only and revision-bound. Freeze a decision observation including decision as-of, entry hypothesis, thesis/assumptions, valuation/payoff, horizon/review dates, source/input hashes, direction, outcome, benchmark selection and benchmark rationale. Maintain actual realized outcomes only from user-confirmed executions. Unexecuted Recommend, Watchlist and Decline receive explicitly hypothetical/counterfactual outcomes using a fixed documented price observation rule; do not cherry-pick later entries.

Append outcome observations: `{decision_id, revision, observed_at, evaluation_at, horizon, kind: actual|hypothetical|declined_counterfactual, instrument_return, benchmark_return, excess_return, drawdown, catalyst_result, thesis_result, error_tags, source_refs, coverage, code_version}`. Price-only versus total return must match or report comparability missing. Include rejected ideas and unavailable/matured counts in summaries; no survivorship-only win rate. Assess thesis/valuation/timing/data/sizing errors separately. Unknown catalyst outcomes remain unknown. Benchmark is frozen before evaluation; none configured means unavailable, not a retrospective best benchmark. No learning observation changes frozen targets or automatically changes policy/model behavior.

Include observed research effort (attempt count, elapsed execution time and usage when actually provided) and decision usefulness in learning summaries. Monetary research cost remains unavailable without a known cost basis. Missed-opportunity reporting includes Decline and untriggered Watchlist cases under the same frozen evaluation rule.

GET `/api/watchlist` may remain the compatibility route while returning all lifecycle categories with a filter/default documented for the UI; alternatively add GET `/api/lifecycle` and keep watchlist filtered. Add GET `/api/learning?namespace=...` for immutable outcome summaries if needed. Mutation endpoints must use existing local mutation guard, namespace checks and idempotency. No hidden refresh/network/model side effects on GET.

## Reddit clustering and priority

Preserve each original post and screening result. Cluster by identified issuer/instrument plus normalized thesis/catalyst/horizon, retaining reason/confidence and member links. Same ticker alone is not the same thesis. Contradictory long/short theses remain distinguishable. Use deterministic exact/normalized matching first; bounded existing A00 screening may propose semantic cluster membership, with no extra model call or embedding service.

Priority order: user questions and user-selected posts, material new evidence/reopen events, high-quality distinct thesis, repetitive commentary/duplicate thesis. Evidence quality, novelty, timeliness, holding relevance and expected research cost are visible rank components, not a false precision investment score. Vote count is context, not factual reliability. Duplicates point to current case with evidence-delta reason; stale/conflicting evidence prevents blind reuse. Keep canonical-root capacity transaction and original-post text gate intact.

## Manager UI and cleanup

Primary surface: concise manager memo with conclusion and present readiness, variant view, valuation/payoff comparison, preferred candidate and why, actionable plan, portfolio impact, strongest opposing case, and next review. Evidence/source detail, calculations, archived corrections, and stress simulations are collapsed but accessible. Show unknowns where they change the action. Label Monte Carlo as conditional stress/sensitivity and move it below valuation; do not headline p50 as a forecast. Office remains secondary and reachable.

Show the canonical outcome exactly; do not re-infer Recommend from legacy stance/quantity. Unsupported historical fields display “Not recorded in this revision.” Identify recommendation date separately from quote update/review dates. Fix stale/overdue/paused/status vocabulary consistently in journal, result, lifecycle and source views.

Clean up only after a reachable caller inventory. Current `WorkspaceViews`, portfolio/memory/coverage pages, simulation routes, `OfficeExperience`, old role IDs, namespaces and migration tables are reachable and cannot be called unused merely because they are secondary. Keep private runtime/history and historical readers. Remove duplicate visible result rendering and unreachable navigation/state/style branches after the new manager view is wired. Remove obsolete prompt paragraphs claiming PDFs unsupported once ingestion lands.

Root verified and removed the unused `DemoData`/`DEMO_DATA` exports in `frontend/src/types.ts`. Strict unused compilation also identifies `LegacySimulationView` and unused bindings for removal. Inspect individual legacy `WorkspaceViews` branches against the actual final App render path before deleting them; a remaining file-level import does not establish that every branch is used.

The old static `build_research_tasks(initial_only=False)` keyword planner is expressly a compatibility utility and is not used by the API/scheduler; remove it and callers/tests only if repository-wide references prove no supported path depends on it. The old A10 PM/revision/repair dispatch branches remain reachable for historical non-lean runs: either retain a small isolated compatibility path or retire their execution explicitly with a visible historical-read-only state before deleting creation code. Preserve role names for archived output display. Do not delete deterministic `price_scenarios` with any standalone legacy simulation cleanup; the lean research pipeline actively uses it. Existing API simulation creation is reachable, so deletion would be deliberate feature retirement, not dead-code cleanup.

## Required verification and integration hazards

- Semantic negative cases: same number/wrong metric, wrong issuer, wrong year, wrong table column, adjusted versus GAAP, millions versus units, EPS versus currency price, and weighted-average versus outstanding shares must not pass.
- Math: short range lower-bound risk with upper-bound notional; stop invalid within range; zero/negative inputs; FX absent; unknown exposure versus zero; shared basket caps; duplicate candidate identity; no short proceeds credited to cash.
- Recommendation: a complete synthetic case passes; removing each material requirement blocks independently; unsupported optional stress alone does not block a valid valuation-based recommendation.
- Valuation: independently known EPS/DCF/EV results, bridge signs, share/ADR scaling, r<=g invalid, negative equity handling, reverse short payoff, no unsupported net return.
- Persistence: corrections idempotent and appended, original payload hashes unchanged, frozen attempt snapshots used, no current market observations leak into old revisions.
- Gaps: terminal unavailable preserved; later explicit evidence resolves; omission cannot resolve; historical diagnostic excluded from current blocker only with recorded relevance reason.
- Lifecycle: paused no dispatch; overdue visible; recommended/held/declined covered; fresh/stale quote labeling; one event one review; later real event can rearm; failed admission cannot lose a fired trigger.
- Learning: rejected outcomes included, benchmark frozen, unavailable prices honest, split/FX/dividend comparability labeled, real and hypothetical returns separate.
- Frontend build and bounded UI smoke verification: memo/comparison readable, disclosures collapse, status agrees with backend, privacy masking covers newly added financial fields.

Shared hazards: Pydantic strict schemas/structured-output `$defs` must include added fields at provider boundary; repository output normalization currently filters/rewrites brief fields before case projection; source packet truncation must retain table headers needed for semantic binding; source validation and deterministic calculations must use the same validated fact IDs; current CaseDecisionStore overwrites supplied correction fact_claims with repository-enriched claims, so stronger revalidation must occur at a defined layer and not be discarded; monitors currently mutate the run input snapshot, so frozen decision/learning inputs must use `task_attempt_decision_inputs`; all financial values added to API must be covered by existing privacy disclosure detection.
