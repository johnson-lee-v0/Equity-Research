export type Namespace = 'real' | 'demo' | 'simulation'

export type TaskStatus =
  | 'idle'
  | 'queued'
  | 'running'
  | 'waiting_for_evidence'
  | 'waiting_for_review'
  | 'blocked'
  | 'failed'
  | 'cancelled'
  | 'completed'
  | 'interrupted'

export type AgentKind = 'staff' | 'analyst' | 'decision'

export interface ModelConfig {
  provider: string
  model: string
  reasoning_mode?: string | null
  profile?: string | null
  scope?: string | null
  billing_route?: string | null
}

export interface EvidenceRef {
  id?: string
  namespace?: string | null
  source_ref?: string
  title?: string
  url?: string | null
  locator?: string | null
  as_of?: string | null
  source_type?: string | null
  published_at?: string | null
  retrieved_at?: string | null
  observation_time?: string | null
  excerpt?: string | null
  content?: string | null
  content_hash?: string | null
  version?: number | null
  supersedes_id?: string | null
  status?: string | null
  /** Claim/source matching metadata supplied by the run detail endpoint. */
  validation_status?: string | null
  validation_reason?: string | null
  unknown_reason?: string | null
  matched_excerpt?: string | null
  line_start?: number | null
  line_end?: number | null
  selected_locator?: string | null
  /** Current v2 semantic evidence validation metadata. */
  text_match?: boolean | null
  semantic_status?: 'supported' | 'mismatch' | 'ambiguous' | 'unavailable' | string | null
  binding_checks?: Array<{ key?: string; status?: string; reason?: string }>
  source_version?: string | null
  freshness?: string | null
  freshness_policy?: string | null
  freshness_as_of?: string | null
  extraction_status?: string | null
  extraction_version?: string | null
  extraction_failure_reason?: string | null
  original_mime_type?: string | null
  original_hash?: string | null
  page_count?: number | null
  document?: Record<string, unknown> | null
}

export interface RoutingPlan {
  intent?: string | null
  horizon?: string | null
  tickers?: string[]
  selected_analysts?: string[]
  research_queries?: string[]
  rationale?: string | null
}

export interface ResearchCandidate {
  ticker?: string | null
  symbol?: string | null
  name?: string | null
  rationale?: string | null
  source_urls?: string[]
  primary_urls?: string[]
  urls?: string[]
  source_ids?: string[]
  source_refs?: EvidenceRef[]
  verified?: boolean
  evidence_available?: boolean
  status?: string | null
  unverified_reason?: string | null
}

/**
 * Structured CIO output. The backend may return the current `cio_brief`
 * shape or the earlier `decision_brief` shape; the API normalizer presents
 * both through this interface so old runs remain readable.
 */
export interface CioPricePlan {
  low?: string | null
  high?: string | null
  value?: string | null
  currency?: string | null
  as_of?: string | null
  horizon?: string | null
  basis?: string | null
  source_refs?: string[]
  missing_reason?: string | null
}

export type CioStance = 'enter' | 'avoid' | 'wait' | 'watch' | 'defer' | string

export interface CioBrief {
  stance?: CioStance | null
  reason?: string | null
  as_of?: string | null
  horizon?: string | null
  ticker?: string | null
  entry_plan?: CioPricePlan | null
  target_price?: CioPricePlan | null
  accepted_risks?: string[]
  catalysts?: string[]
  invalidation_conditions?: string[]
  blocking_gaps?: string[]
  simulation_ids?: string[]
  next_review_trigger?: string | null
  candidate_briefs?: CioBrief[]
  /** Legacy DecisionBrief fields retained for historical output rendering. */
  entry_advice?: string | null
  entry_zone?: CioPricePlan | null
  target_price_basis?: string | null
  target_price_source_refs?: string[]
  risks?: string[]
  missing_inputs?: string[]
  legacy?: boolean
  allocation_mode?: 'alternatives' | 'combined' | string | null
  direction?: 'long' | 'short' | string | null
  strategy?: 'long_term' | 'trade' | string | null
  asset_class?: string | null
  benchmark_ticker?: string | null
  benchmark_rationale?: string | null
  thesis?: ThesisBlock | null
  valuation_assumptions?: ValuationAssumptions | null
  action_plan?: ActionPlan | null
}

export interface ThesisBlock {
  variant_view?: string | null
  market_expectation?: string | null
  why_now?: string | null
  supporting_claim_ids?: string[]
  strongest_opposing_explanation?: string | null
  disconfirming_evidence?: string[]
  decision_change_conditions?: string[]
  status?: string | null
  missing_inputs?: string[]
  source_refs?: string[]
}

export interface ValuationInput {
  key?: string
  value?: string | null
  unit?: string | null
  currency?: string | null
  period?: string | null
  kind?: 'fact' | 'assumption' | string
  fact_claim_ids?: string[]
  source_refs?: string[]
  rationale?: string | null
}

export type ValuationScenarioName = 'bear' | 'base' | 'bull'

export interface ValuationScenarioCalculation {
  inputs?: ValuationInput[]
  formula?: string
  steps?: string[]
  intermediate_results?: Record<string, unknown>
  output_price?: string | null
}

export interface ValuationMethod {
  name?: string
  status?: string | null
  inputs?: ValuationInput[]
  rationale?: string | null
  scenario_calculations?: Partial<Record<ValuationScenarioName, ValuationScenarioCalculation>>
  formula?: string | null
  steps?: string[]
  intermediate_results?: Record<string, unknown>
  output_prices?: Record<string, string>
  reasons?: string[]
  source_refs?: string[]
  supported?: boolean
  missing_inputs?: string[]
  as_of?: string | null
}

export interface ValuationAssumptions {
  methods?: Array<string | Record<string, unknown>>
  scenarios?: Record<string, Record<string, unknown> | null>
  inputs?: ValuationInput[]
  rationale?: string | null
  asset_class?: string | null
}

export type ValuationNumeric = string | number | null

export interface ValuationEarningsComponent {
  metric?: string
  value?: ValuationNumeric
  period_start?: string | null
  period_end?: string | null
  available_at?: string | null
  source_refs?: string[]
  locator?: string | null
  accession?: string | null
  source_url?: string | null
  sign?: number
}

export interface ValuationResearchContext {
  as_of?: string | null
  earnings_bridge?: {
    fiscal_year?: string | number | null
    currency?: string | null
    unit?: string | null
    quarters?: Array<{
      period: string
      kind: 'reported' | 'projection' | 'missing'
      value?: ValuationNumeric
      source_refs?: string[]
      rationale?: string | null
      period_start?: string | null
      period_end?: string | null
    }>
    reported_total?: ValuationNumeric
    projected_total?: ValuationNumeric
    full_year_total?: ValuationNumeric
    method?: string | null
    coverage_note?: string | null
  } | null
  historical_pe?: {
    metric?: string
    provenance?: string
    provider?: string
    sampling?: string
    as_of?: string
    source_url?: string
    denominator_name?: string
    basis?: string | null
    points?: Array<{
      date: string
      close?: ValuationNumeric
      ttm_eps?: ValuationNumeric
      pe?: ValuationNumeric
      ebitda_basis?: string | null
      source_refs?: string[]
      eps_period_end?: string | null
      earnings_period_end?: string | null
      available_as_of?: string | null
      eps_formula?: string | null
      eps_components?: ValuationEarningsComponent[]
      period_label?: string
      source_url?: string
    }>
    min?: ValuationNumeric
    median?: ValuationNumeric
    max?: ValuationNumeric
    coverage_note?: string | null
    currency?: string | null
    gaps?: Array<{ date?: string; reason?: string }>
  } | null
  historical_multiples?: Record<string, NonNullable<ValuationResearchContext['historical_pe']> & {
    status?: string
    points?: Array<{
      date: string
      multiple?: ValuationNumeric
      denominator_value?: ValuationNumeric
      numerator_value?: ValuationNumeric
      shares?: ValuationNumeric
      close?: ValuationNumeric
      source_refs?: string[]
      earnings_period_end?: string | null
      available_as_of?: string | null
      components?: ValuationEarningsComponent[]
    }>
  }>
  provider_multiples?: ValuationResearchContext['historical_multiples']
  implied_today?: {
    status?: string | null
    scenarios?: Partial<Record<ValuationScenarioName, ValuationNumeric>>
    earnings_basis?: string | null
    as_of?: string | null
    formula?: string | null
    source_refs?: string[]
    eps?: ValuationNumeric
    currency?: string | null
    period_end?: string | null
    earnings_formula?: string | null
    coverage_note?: string | null
  } | null
  current_earnings?: {
    status?: string | null
    value?: ValuationNumeric
    currency?: string | null
    period_end?: string | null
    available_as_of?: string | null
    formula?: string | null
    source_refs?: string[]
    components?: ValuationEarningsComponent[]
    reason?: string | null
  } | null
  sources?: EvidenceRef[]
}

export interface ValuationBlock {
  status?: string | null
  rationale?: string | null
  as_of?: string | null
  horizon?: string | null
  currency?: string | null
  methods?: ValuationMethod[]
  scenarios?: Record<string, string>
  sensitivities?: Array<Record<string, unknown>>
  missing_inputs?: string[]
  code_version?: string | null
  input_hash?: string | null
  dispersion?: Record<string, unknown>
  selected_method?: string | null
  reconciliation_rationale?: string | null
  research_context?: ValuationResearchContext | null
}

export interface PayoffScenario {
  name?: string
  target_price?: string | null
  price_return?: string | null
  estimated_costs?: Record<string, string | null>
  net_return?: string | null
  pnl_per_share?: string | null
  planned_pnl?: string | null
  source_refs?: string[]
}

export interface PayoffBlock {
  status?: string | null
  reference_entry?: string | null
  direction?: string | null
  horizon?: string | null
  scenarios?: PayoffScenario[]
  reward_to_risk?: string | null
  breakeven_price?: string | null
  missing_inputs?: string[]
  limitations?: string[]
}

export interface PortfolioContext {
  status?: string | null
  snapshot_id?: string | null
  as_of?: string | null
  base_currency?: string | null
  before?: Record<string, unknown>
  after?: Record<string, unknown>
  exposures?: Array<Record<string, unknown>>
  checks?: Array<Record<string, unknown>>
  missing_inputs?: string[]
  code_version?: string | null
}

export interface ActionPlan {
  entry_condition?: string | null
  exit_condition?: string | null
  invalidation_condition?: string | null
  hedge_objective?: string | null
  max_loss_basis?: string | null
  catalyst_events?: Array<{ description?: string; event_date?: string | null; date_kind?: string; source_refs?: string[] }>
  review_at?: string | null
  expires_at?: string | null
  status?: string | null
  missing_inputs?: string[]
  benchmark_ticker?: string | null
  benchmark_rationale?: string | null
}

export interface RecommendationGate {
  status?: 'pass' | 'blocked' | string | null
  checks?: Array<{ key?: string; status?: string; reason?: string }>
  missing_inputs?: string[]
}

export interface LifecycleBlock {
  state?: 'watchlist' | 'recommended' | 'held' | 'declined' | 'closed' | string | null
  reopen_when?: string[]
  status?: string | null
  missing_inputs?: string[]
}

export interface CanonicalCandidateDecision {
  schema_version?: string
  decision_revision?: number | null
  ticker?: string | null
  instrument?: string | null
  direction?: string | null
  strategy?: string | null
  outcome?: 'recommend' | 'watchlist' | 'decline' | null
  execution_state?: string | null
  rationale?: string | null
  as_of?: string | null
  horizon?: string | null
  entry?: Record<string, unknown> | null
  future_target?: Record<string, unknown> | null
  sizing?: Record<string, unknown> | null
  risks?: string[]
  catalysts?: string[]
  invalidation?: string[]
  source_refs?: string[]
  evidence?: Array<Record<string, unknown>>
  material_blockers?: Array<Record<string, unknown>>
  watch_triggers?: Array<Record<string, unknown>>
  instrument_identity?: Record<string, unknown>
  thesis?: ThesisBlock
  valuation?: ValuationBlock
  payoff?: PayoffBlock
  portfolio_context?: PortfolioContext
  action_plan?: ActionPlan
  recommendation_gate?: RecommendationGate
  lifecycle?: LifecycleBlock
  [key: string]: unknown
}

export interface CanonicalCaseDecision {
  schema_version?: string
  run_id?: string
  allocation_mode?: 'alternatives' | 'combined' | string
  decision_revision?: number | null
  as_of?: string | null
  outcome?: 'recommend' | 'watchlist' | 'decline' | 'mixed' | null
  execution_state?: string | null
  candidates?: CanonicalCandidateDecision[]
  material_blockers?: Array<Record<string, unknown>>
  stale?: boolean
  [key: string]: unknown
}

export interface MissingGap {
  key?: string
  description?: string
  relevant_role?: string | null
  owner?: string | null
  reopen_when?: string | null
  source_requirements?: string[]
  status?: string | null
  why_waiting?: string | null
  action?: string | null
  result_links?: Array<{ id?: string; title?: string; type?: string; url?: string | null }>
  repair_run_id?: string | null
  resolved_by_output_id?: string | null
  updated_at?: string | null
  [key: string]: unknown
}

export interface MemoryItem {
  record_id?: string
  record_type?: string
  namespace?: Namespace | string
  title?: string
  excerpt?: string
  source_refs?: string[]
  source_versions?: Array<Record<string, unknown>>
  observed_at?: string | null
  retrieved_at?: string | null
  freshness?: 'fresh' | 'reused' | 'stale' | 'unknown' | string
  reuse_reason?: string
  stale?: boolean
  [key: string]: unknown
}

export interface MemoryContext {
  namespace?: Namespace | string
  run_id?: string
  task_id?: string | null
  reused?: MemoryItem[]
  fresh?: MemoryItem[]
  freshness_as_of?: string | null
  reason?: string
  [key: string]: unknown
}

export type RedditTriageClassification = 'thesis' | 'yolo_ticker' | 'skip'

export interface RedditTriage {
  classification: RedditTriageClassification
  reason: string
  thesis_summary: string
  evidence_excerpt: string
  tickers: string[]
  [key: string]: unknown
}

export interface RedditPostRecord {
  id?: string
  post_id?: string | null
  subreddit?: string | null
  /** Retained source observation; legacy payloads may call this `flair`. */
  source_flair?: string | null
  flair?: string | null
  triage?: RedditTriage | null
  title?: string
  body?: string | null
  text?: string | null
  author?: string | null
  permalink?: string | null
  url?: string | null
  score?: number | null
  comments?: number | null
  created_at?: string | null
  ingested_at?: string | null
  retained_at?: string | null
  state?: string | null
  status?: string | null
  dispatch_state?: string | null
  run_id?: string | null
  reused_run_id?: string | null
  reuse_reason?: string | null
  source_id?: string | null
  coverage_window?: string | null
  coverage_start?: string | null
  coverage_end?: string | null
  backlog_reason?: string | null
  reason?: string | null
  dispatch_error?: string | null
  error?: string | null
  [key: string]: unknown
}

export interface RedditInboxQuery {
  /** Durable intake status/category; omit or use `all` for every record. */
  status?: string | null
  limit?: number
  offset?: number
}

export interface RedditConnectionState {
  status?: string | null
  connected?: boolean
  enabled?: boolean | null
  subreddit?: string | null
  window_days?: number | null
  last_sync_at?: string | null
  coverage_start?: string | null
  coverage_end?: string | null
  coverage_complete?: boolean | null
  coverage_reason?: string | null
  reason?: string | null
  account_label?: string | null
  [key: string]: unknown
}

export interface RedditInboxSnapshot {
  posts: RedditPostRecord[]
  connection?: RedditConnectionState | null
  /** Server-side status/category filter used for this page, when supplied. */
  status?: string | null
  filter_status?: string | null
  /** Distinguishes an empty, successfully checked intake from an unavailable or untouched one. */
  availability?: 'not_checked' | 'unavailable' | 'available' | 'empty' | string
  error_message?: string | null
  has_more?: boolean
  next_offset?: number | null
  offset?: number
  page_size?: number
  coverage_start?: string | null
  coverage_end?: string | null
  intake_start?: string | null
  intake_end?: string | null
  /** Durable total across all pages, when returned by intake_status. */
  total?: number
  /** Total within the selected status filter, when the API also returns the global total. */
  filtered_total?: number
  counts?: Record<string, number>
  /** Bounded parallel Reddit dispatch capacity reported by the backend. */
  parallel_limit?: number | null
  active_post_count?: number | null
  available_slots?: number | null
  backlog_count?: number
  retained_count?: number
  researched_count?: number
  skipped_count?: number
  covered_count?: number
  missing_count?: number
  [key: string]: unknown
}

export interface OutputRecord {
  id?: string
  output_id?: string
  task_id?: string
  attempt_id?: string | null
  agent_id?: string | null
  version?: number
  title?: string
  status?: string
  conclusion?: string | null
  proposed_action?: string | null
  created_at?: string | null
  updated_at?: string | null
  provider?: string | null
  model?: string | null
  reasoning_mode?: string | null
  prompt_version?: string | null
  provenance?: 'real_research' | 'demo' | 'simulation' | string
  fact_claims?: FactClaimRecord[]
  assumptions?: string[]
  calculations?: CalculationRecord[]
  counterarguments?: string[]
  missing_data?: string[]
  invalidation_conditions?: string[]
  source_refs?: EvidenceRef[]
  routing_plan?: RoutingPlan | null
  research_candidates?: ResearchCandidate[]
  body?: string | null
  run_id?: string | null
  review_disposition?: string | null
  revision_requests?: string[]
  decision_disposition?: string | null
  decision_brief?: CioBrief | null
  cio_brief?: CioBrief | null
  /** Canonical case projection emitted by the decision service. */
  current_decision?: Record<string, unknown> | null
  canonical_decision?: Record<string, unknown> | null
  current_case_decision?: Record<string, unknown> | null
  missing_gaps?: MissingGap[]
  memory_context?: MemoryContext | null
  scenario_fan?: Array<Record<string, unknown>>
  scenario_quantiles?: Array<Record<string, unknown>>
  price_scenarios?: Record<string, unknown> | null
  simulation_snapshot?: Record<string, unknown> | null
  simulation_snapshots?: Array<Record<string, unknown>>
  scenario_status?: string | null
  scenario_method?: string | null
  scenario_ticker?: string | null
  scenario_currency?: string | null
  scenario_as_of?: string | null
  scenario_missing_reason?: string | null
  scenario_assumptions?: string[]
  scenario_limitations?: string[]
  scenario_source_refs?: EvidenceRef[]
  scenario_result_hash?: string | null
  technical_indicators?: Array<Record<string, unknown>>
  multi_timeframe?: Array<Record<string, unknown>>
}

export interface FactClaimRecord {
  claim?: string | null
  value?: string | null
  unit?: string | null
  period?: string | null
  source_ref?: string | null
  source_id?: string | null
  locator?: string | null
  validation_status?: string | null
  status?: string | null
  unknown_reason?: string | null
  validation_reason?: string | null
  matched_excerpt?: string | null
  excerpt?: string | null
  line_start?: number | null
  line_end?: number | null
  subject?: string | null
  metric?: string | null
  scale?: string | null
  currency?: string | null
  period_start?: string | null
  period_end?: string | null
  basis?: string | null
  statement_type?: string | null
  source_quote?: string | null
  semantic_status?: string | null
  binding_checks?: Array<{ key?: string; status?: string; reason?: string }>
  source_version?: string | null
  freshness?: string | null
  [key: string]: unknown
}

export interface RunAttemptRecord {
  id?: string
  attempt_id?: string
  task_id?: string | null
  status?: string | null
  attempt_no?: number | null
  provider?: string | null
  model?: string | null
  reasoning_effort?: string | null
  stage?: string | null
  execution_stage?: string | null
  provider_started_at?: string | null
  started_at?: string | null
  finished_at?: string | null
  completed_at?: string | null
  created_at?: string | null
  updated_at?: string | null
  error?: string | null
  message?: string | null
  resolved_model?: ModelConfig | null
  resolved_model_config?: ModelConfig | null
  [key: string]: unknown
}

export interface RunDependencyRecord {
  task_id?: string
  depends_on_task_id?: string
  agent_id?: string
  title?: string
  id?: string
  status?: string | null
  [key: string]: unknown
}

export interface RunReviewContext {
  output_id?: string | null
  title?: string | null
  relation?: 'recorded' | 'historical_inference' | string | null
  label?: string | null
}

export interface RunCounts {
  total?: number
  active?: number
  queued?: number
  running?: number
  waiting?: number
  waiting_for_evidence?: number
  waiting_for_review?: number
  blocked?: number
  failed?: number
  completed?: number
  cancelled?: number
  interrupted?: number
  outputs?: number
  evidence_ready?: number
  evidence_incomplete?: number
  [key: string]: number | undefined
}

export interface RunSummary {
  id: string
  run_id?: string
  namespace?: Namespace | string
  question: string
  original_question?: string
  question_group_id?: string | null
  status?: string | null
  execution_status?: string | null
  execution?: string | null
  execution_state?: string | null
  evidence_status?: string | null
  evidence_readiness?: string | null
  evidence?: string | null
  disposition?: string | null
  committee_disposition?: string | null
  decision_disposition?: string | null
  created_at?: string | null
  updated_at?: string | null
  display_title?: string | null
  latest_output_id?: string | null
  latest_output_title?: string | null
  latest_output_summary?: string | null
  summary?: string | null
  task_count?: number
  completed_task_count?: number
  source_count?: number
  completed_at?: string | null
  parent_run_id?: string | null
  followup_kind?: string | null
  latest_output?: OutputRecord | null
  counts?: RunCounts
  task_counts?: RunCounts
  selected_agent_ids?: string[]
  selected_roles?: string[]
  skipped_agent_ids?: string[]
  skipped_roles?: string[]
  allowed_actions?: string[] | Record<string, boolean>
  current_blocker?: string | null
  blocking_reason?: string | null
  ticker?: string | null
  horizon?: string | null
  linked_run_id?: string | null
  origin?: string | null
  origin_ref?: string | null
  root_run_id?: string | null
  root_origin?: string | null
  answer_source_run_id?: string | null
  /** One current case decision shared by every decision view. */
  current_decision?: Record<string, unknown> | null
  canonical_decision?: Record<string, unknown> | null
  current_case_decision?: Record<string, unknown> | null
  /** Code-owned technical and scenario context retained with the current case revision. */
  calculation_context?: Record<string, unknown> | null
  candidate_simulations?: Array<Record<string, unknown>>
  decision_brief?: CioBrief | null
  cio_brief?: CioBrief | null
  blocking_gaps?: MissingGap[]
  gap_resolution_ledger?: MissingGap[]
  memory_context?: MemoryContext | null
  [key: string]: unknown
}

export interface RunTaskRecord extends TaskRecord {
  kind?: string | null
  instruction?: string | null
  dependencies?: RunDependencyRecord[]
  dependency_ids?: string[]
  dependency_states?: RunDependencyRecord[]
  attempts?: RunAttemptRecord[]
  execution_stage?: string | null
  evidence_status?: string | null
  evidence_readiness?: string | null
  allowed_actions?: string[] | Record<string, boolean>
  explanation?: Record<string, unknown> | null
  decision_history?: Array<Record<string, unknown>>
  review_disposition?: string | null
  revision_requests?: string[]
  decision_disposition?: string | null
  output_id?: string | null
}

export interface RunDetail extends RunSummary {
  tasks: RunTaskRecord[]
  dependencies: RunDependencyRecord[]
  outputs: OutputRecord[]
  attempts: RunAttemptRecord[]
  sources?: EvidenceRef[]
  source_links?: EvidenceRef[]
  source_ids?: string[]
  discovery_source_ids?: string[]
  routing_plan?: RoutingPlan | null
  research_candidates?: ResearchCandidate[]
  timeline?: Array<Record<string, unknown>>
  selected_agent_ids: string[]
  skipped_agent_ids: string[]
  agent_selection?: Array<{ agent_id?: string; selected?: boolean; state?: 'selected' | 'skipped' | 'routing_pending' | 'simulation_only' | string | null; reason?: string | null }>
  explanation?: Record<string, unknown> | null
  decision_brief?: CioBrief | null
  gap_resolution_ledger?: MissingGap[]
  candidate_simulations?: Array<Record<string, unknown>>
  answer_source_run_id?: string | null
}

export interface CalculationRecord {
  label?: string
  value?: string | null
  unit?: string | null
  formula?: string | null
  missing_reason?: string | null
  operation?: string | null
  input_fact_indices?: number[]
  window?: number | null
  assumed_discount_fraction?: string | null
  assumption_rationale?: string | null
}

export interface TaskRecord {
  id?: string
  task_id?: string
  run_id?: string | null
  agent_id?: string
  status?: TaskStatus | string
  paused?: boolean
  attempt_id?: string | null
  progress_message?: string | null
  mandate?: string | null
  question?: string | null
  current_task?: string | null
  started_at?: string | null
  created_at?: string | null
  updated_at?: string | null
  completed_at?: string | null
  last_update?: string | null
  elapsed_seconds?: number | null
  progress?: number | null
  blocking_reason?: string | null
  retry_at?: string | null
  input_references?: EvidenceRef[]
  evidence_refs?: EvidenceRef[]
  messages?: Array<{ at?: string; text?: string; type?: string }>
  output?: OutputRecord | null
  outputs?: OutputRecord[]
  history?: Array<Record<string, unknown>>
  model?: ModelConfig | null
  resolved_model_config?: ModelConfig | null
  allowed_actions?: string[] | Record<string, boolean>
  output_id?: string | null
  execution_stage?: string | null
  evidence_status?: string | null
  evidence_readiness?: string | null
  title?: string | null
  kind?: string | null
  run_status?: string | null
  provider_started_at?: string | null
  output_status?: string | null
  terminal_summary?: string | null
  wait_reason?: string | null
  dispatch_state?: string | null
  sequence_no?: number
  attempts?: RunAttemptRecord[]
  review_context?: RunReviewContext | null
  memory_context?: MemoryContext | null
  assignment_reason?: string | null
  origin?: string | null
}

export interface AgentRecord {
  id: string
  code?: string
  name: string
  title: string
  kind: AgentKind
  mandate: string
  zone: string
  location?: { x: number; y: number; z: number; room?: string }
  accent?: string
  status: TaskStatus | string
  paused?: boolean
  queued_count?: number
  output_count?: number
  model_source?: string
  current_task?: TaskRecord | null
  task?: TaskRecord | null
  queue?: TaskRecord[]
  outputs?: OutputRecord[]
  evidence?: EvidenceRef[]
  history?: Array<Record<string, unknown>>
  model?: ModelConfig | null
  last_update?: string | null
  current_simulation?: SimulationRun | null
  related_simulations?: SimulationRun[]
  /** Run-scoped routing state supplied by the backend when a question is selected. */
  selected_for_run?: boolean | null
  selection_state?: 'selected' | 'skipped' | 'routing_pending' | 'simulation_only' | string | null
  selection_reason?: string | null
  selected_run_id?: string | null
  latest_completed_task?: TaskRecord | null
  latest_output?: OutputRecord | null
}

export interface OfficeSnapshot {
  mode?: Namespace | string
  namespace?: Namespace | string
  agents: AgentRecord[]
  tasks?: TaskRecord[]
  last_event_cursor?: string | number | null
  event_cursor?: string | number | null
  generated_at?: string | null
  server_time?: string | null
  stale?: boolean
  paused?: boolean
  provider_state?: ProviderState
  portfolio?: PortfolioSnapshot
}

export interface ProviderModel {
  provider: string
  model: string
  label?: string
  available?: boolean
  reason?: string | null
  reasoning_efforts?: string[]
  capabilities?: Record<string, unknown>
}

export interface ProviderState {
  provider?: string
  available?: boolean
  status?: string
  auth_mode?: string | null
  billing_route?: string | null
  reason?: string | null
  models?: ProviderModel[]
  checked_at?: string | null
  providers?: Array<ProviderState & { name?: string; id?: string }>
}

export interface PortfolioObservation {
  id?: string
  account?: string
  instrument?: string
  quantity?: number | null
  value?: number | null
  currency?: string | null
  observation_date?: string | null
  status?: string | null
  source_ref?: string | null
  reason?: string | null
}

export interface PortfolioSnapshot {
  observations?: PortfolioObservation[]
  accounts?: Array<Record<string, unknown>>
  positions?: Array<Record<string, unknown>>
  balances?: Array<Record<string, unknown>>
  as_of?: string | null
  reconciliation_status?: string | null
  missing_data?: string[]
}

export interface PortfolioLimit {
  currency: string
  initial_notional: string
  max_notional: string
  planned_loss_limit?: string | null
}

export interface PortfolioPolicy {
  status?: 'proposed' | 'approved' | string
  max_positions?: number
  tfsa_long_term_only?: boolean
  allow_tfsa_outflows?: boolean
  allow_chequing_to_nonregistered?: boolean
  limits?: Record<string, PortfolioLimit>
  [key: string]: unknown
}

export interface MemoryRecord {
  id?: string
  type?: string
  title?: string
  summary?: string | null
  content?: string | null
  provenance?: string
  created_at?: string | null
  updated_at?: string | null
  source_refs?: EvidenceRef[]
  backlinks?: Array<{ id?: string; title?: string; type?: string }>
  version?: number
  record_type?: string
  freshness?: string | null
  reuse_reason?: string | null
  observed_at?: string | null
  retrieved_at?: string | null
  stale?: boolean
}

export interface SimulationRun {
  id?: string
  simulation_id?: string
  status?: TaskStatus | string
  name?: string
  scenario?: string
  seed?: number
  rounds?: number
  participants?: number
  horizon?: string
  shock?: string
  assumptions?: string[]
  output?: Record<string, unknown> | null
  created_at?: string | null
  events?: Array<Record<string, unknown>>
  namespace?: Namespace | string
  question?: string | null
  limits?: string[]
  inputs?: Record<string, unknown> | null
  participants_detail?: Array<Record<string, unknown>>
  rounds_detail?: Array<Record<string, unknown>>
  summary?: string | null
  model?: ModelConfig | null
  recorded_responses?: Array<Record<string, unknown>>
  rules_only_baseline?: Record<string, unknown> | null
  failure?: Record<string, unknown> | null
  blocking_reason?: string | null
  provider_status?: string | null
  versions?: Record<string, unknown> | null
  source_snapshots?: EvidenceRef[]
}

export interface CoverageItem {
  id: string
  ticker: string
  name?: string
  sector?: string | null
  kind?: string | null
  is_placeholder?: boolean
  status: 'considered' | 'watch' | 'active_research' | 'rejected' | 'held' | string
  reason?: string | null
  updated_at?: string | null
  reopening_condition?: string | null
  horizon?: string | null
}

export interface DecisionRecord {
  id?: string
  run_id?: string | null
  agent_id?: string | null
  subject?: string
  disposition?: string
  rationale?: string
  dissent?: string | null
  conditions?: string[]
  invalidation_conditions?: string[]
  risk_results?: Array<{ check?: string; status?: string; detail?: string; [key: string]: unknown }>
  output_id?: string | null
  supersedes_id?: string | null
  created_at?: string | null
  provenance?: string
}

/** Read model returned by the dedicated watchlist endpoint. */
export interface WatchlistItem {
  run_id: string
  title?: string | null
  question?: string | null
  namespace?: Namespace | string | null
  as_of?: string | null
  updated_at?: string | null
  decision_revision?: number | null
  execution_state?: string | null
  state?: string | null
  status?: string | null
  stale?: boolean
  candidate?: Record<string, unknown> | null
  checks?: Array<Record<string, unknown>>
  trigger_state?: string | null
  lifecycle_state?: 'watchlist' | 'recommended' | 'held' | 'declined' | 'closed' | string | null
  current_quote?: Record<string, unknown> | null
  distance_to_entry?: Record<string, unknown> | null
  review_due_at?: string | null
  overdue?: boolean
  last_checked_at?: string | null
  next_action?: string | null
  paused?: boolean
  candidate_key?: string | null
  learning?: Record<string, unknown> | null
  [key: string]: unknown
}

export interface LearningOutcome {
  id?: string
  status?: string | null
  kind?: string | null
  evaluation_at?: string | null
  observed_at?: string | null
  maturity?: string | null
  instrument?: Record<string, unknown>
  benchmark?: Record<string, unknown>
  benchmark_comparable?: boolean | null
  excess_return?: string | null
  drawdown?: string | null
  thesis_result?: string | null
  catalyst_result?: string | null
  missed_opportunity?: boolean
  limitations?: string[]
  [key: string]: unknown
}

export interface LearningBaseline {
  id?: string
  run_id?: string
  decision_revision?: number | null
  candidate_key?: string
  ticker?: string
  direction?: string
  outcome?: string | null
  decision_as_of?: string | null
  review_at?: string | null
  benchmark_ticker?: string | null
  benchmark_rationale?: string | null
  retrospective?: boolean
  observations?: LearningOutcome[]
  [key: string]: unknown
}

export interface LearningSummary {
  baselines?: number
  initial_idea_cohort?: number
  unevaluated?: number
  evaluated?: number
  prospective_evaluated?: number
  matured_comparable?: number
  unavailable?: number
  declined_baselines?: number
  missed_opportunities?: number
  mean_excess_return?: string | null
  cohort_basis?: string | null
  [key: string]: unknown
}

export interface LearningSnapshot {
  items: LearningBaseline[]
  summary?: LearningSummary
  method?: string | null
  [key: string]: unknown
}

export interface MonitoringRule {
  id: string
  name: string
  enabled: boolean
  timezone?: string | null
  interval_minutes?: number | null
  last_run_at?: string | null
  next_run_at?: string | null
  catch_up_policy?: string | null
  condition?: string | null
  source_ids?: string[]
  mode?: 'interval_research' | 'source_change' | string
}

export interface TaskEvent {
  sequence_id?: string | number
  event_id?: string
  run_id?: string
  task_id?: string
  attempt_id?: string
  emitted_at?: string
  namespace?: Namespace
  type?: string
  payload?: Record<string, unknown>
}

export const ROLE_DEFINITIONS: Array<Pick<AgentRecord, 'id' | 'name' | 'title' | 'kind' | 'mandate' | 'zone' | 'accent'>> = [
  { id: 'A00', name: 'Chief of Staff', title: 'Chief of Staff', kind: 'staff', mandate: 'Route requests, gather context and keep the firm’s work moving.', zone: 'Reception', accent: '#d5ae62' },
  { id: 'A01', name: 'Universe', title: 'Universe Analyst', kind: 'analyst', mandate: 'Coverage, exclusions and a clear investable universe.', zone: 'Analyst floor', accent: '#80c6a4' },
  { id: 'A02', name: 'Filings', title: 'Filings Analyst', kind: 'analyst', mandate: 'Facts, amendments and source locators from public filings.', zone: 'Analyst floor', accent: '#93c6d9' },
  { id: 'A03', name: 'Fundamental', title: 'Fundamental Analyst', kind: 'analyst', mandate: 'Value, thesis structure and code-checked company fundamentals.', zone: 'Analyst floor', accent: '#c9a1d9' },
  { id: 'A04', name: 'Technical', title: 'Technical Analyst', kind: 'analyst', mandate: 'Price context, market structure and explicitly dated observations.', zone: 'Analyst floor', accent: '#e7b07c' },
  { id: 'A05', name: 'Entry', title: 'Entry Analyst', kind: 'analyst', mandate: 'Entry conditions, downside and invalidation triggers.', zone: 'Analyst floor', accent: '#a3d29e' },
  { id: 'A06', name: 'Holdings', title: 'Holdings Analyst', kind: 'analyst', mandate: 'Account observations, changes and concentration alerts.', zone: 'Analyst floor', accent: '#d7c48d' },
  { id: 'A07', name: 'Simulation', title: 'Simulation Analyst', kind: 'analyst', mandate: 'Bounded scenarios, assumptions and exact replay records.', zone: 'Simulation lab', accent: '#b7a1e4' },
  { id: 'A08', name: 'Ownership', title: 'Ownership Analyst', kind: 'analyst', mandate: '13F and public disclosure evidence with reporting-delay context.', zone: 'Analyst floor', accent: '#d39a9a' },
  { id: 'A09', name: 'Macro', title: 'Macro Analyst', kind: 'analyst', mandate: 'Regimes, rates, commodities and correlated exposures.', zone: 'Analyst floor', accent: '#8fc3b8' },
  { id: 'A10', name: 'Portfolio Manager', title: 'Portfolio Manager', kind: 'decision', mandate: 'Challenge evidence, test the thesis and record a disposition.', zone: 'PM office', accent: '#d5ae62' },
  { id: 'A11', name: 'Chief Investment Officer', title: 'Chief Investment Officer', kind: 'decision', mandate: 'Review allocation, constraints, risk and the decision journal.', zone: 'CIO office', accent: '#d5ae62' },
]
