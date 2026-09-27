import type {
  AgentRecord,
  CalculationRecord,
  CioBrief,
  CioPricePlan,
  CoverageItem,
  DecisionRecord,
  EvidenceRef,
  MemoryRecord,
  MemoryContext,
  MemoryItem,
  MissingGap,
  ModelConfig,
  MonitoringRule,
  Namespace,
  OfficeSnapshot,
  OutputRecord,
  PortfolioSnapshot,
  ProviderState,
  ResearchCandidate,
  RedditConnectionState,
  RedditInboxQuery,
  RedditInboxSnapshot,
  RedditPostRecord,
  RedditTriage,
  RedditTriageClassification,
  RoutingPlan,
  FactClaimRecord,
  RunAttemptRecord,
  RunCounts,
  RunDependencyRecord,
  RunDetail,
  RunSummary,
  RunTaskRecord,
  SimulationRun,
  TaskEvent,
  TaskRecord,
  WatchlistItem,
  LearningSnapshot,
  LearningBaseline,
  LearningOutcome,
} from './types'

export const CLIENT_HEADER = 'X-Road2M-Client'

export class ApiError extends Error {
  status: number
  code?: string
  details?: unknown

  constructor(message: string, status = 0, code?: string, details?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.details = details
  }
}

function isRecord(value: unknown): value is Record<string, any> {
  return typeof value === 'object' && value !== null
}

function readableLocation(value: unknown, fallback = 'Analyst floor') {
  const raw = typeof value === 'string'
    ? value
    : isRecord(value)
      ? value.room ?? value.label ?? value.name ?? null
      : null
  if (raw == null || String(raw).trim() === '') return fallback
  return String(raw).replace(/[_-]+/g, ' ').replace(/\s+/g, ' ').trim()
}

function errorMessage(body: unknown, fallback: string) {
  if (isRecord(body) && typeof body.detail === 'string') return body.detail
  if (isRecord(body) && Array.isArray(body.detail)) return body.detail.map((item) => isRecord(item) ? `${Array.isArray(item.loc) ? item.loc.filter((part) => part !== 'body').join(' · ') + ': ' : ''}${item.msg ?? 'Invalid value'}` : String(item)).join('; ')
  if (isRecord(body) && isRecord(body.error) && typeof body.error.message === 'string') return body.error.message
  if (isRecord(body) && typeof body.error === 'string') return body.error
  if (isRecord(body) && typeof body.message === 'string') return body.message
  return fallback
}

export async function apiFetch<T>(path: string, init: RequestInit = {}, options?: { mutation?: boolean; signal?: AbortSignal }): Promise<T> {
  const headers = new Headers(init.headers)
  headers.set('Accept', 'application/json')
  if (options?.mutation) {
    headers.set('Content-Type', 'application/json')
    headers.set(CLIENT_HEADER, 'local-ui')
  }
  const response = await fetch(path, { ...init, headers, signal: options?.signal, cache: 'no-store' })
  const text = await response.text()
  let body: unknown = null
  if (text) {
    try { body = JSON.parse(text) } catch { body = text }
  }
  const errorValue = isRecord(body) ? body.error : undefined
  if (!response.ok) throw new ApiError(errorMessage(body, `Request failed (${response.status})`), response.status, isRecord(errorValue) ? errorValue.code : undefined, isRecord(errorValue) ? errorValue.details : undefined)
  return body as T
}

export function unwrapItems<T>(value: unknown): T[] {
  if (Array.isArray(value)) return value as T[]
  if (isRecord(value) && Array.isArray(value.items)) return value.items as T[]
  return []
}

function normalizeStatus(value: unknown): string {
  const raw = String(value ?? 'idle').toLowerCase().replaceAll('-', '_').replaceAll(' ', '_')
  if (raw === 'waiting_evidence') return 'waiting_for_evidence'
  if (raw === 'waiting_review') return 'waiting_for_review'
  return raw || 'idle'
}

export function normalizeModel(value: unknown): ModelConfig {
  const model = isRecord(value) ? value : {}
  return {
    provider: String(model.provider ?? 'codex'),
    model: String(model.model ?? 'gpt-6-luna'),
    reasoning_mode: model.reasoning_mode == null ? (model.reasoning_effort == null ? null : String(model.reasoning_effort)) : String(model.reasoning_mode),
    profile: model.profile == null ? null : String(model.profile),
    scope: model.scope == null ? null : String(model.scope),
    billing_route: model.billing_route == null ? null : String(model.billing_route),
  }
}

function normalizeResolvedModel(value: unknown): ModelConfig | null {
  if (!isRecord(value)) return null
  const provider = typeof value.provider === 'string' ? value.provider.trim() : ''
  const model = typeof value.model === 'string' ? value.model.trim() : ''
  if (!provider || !model) return null
  return normalizeModel({ ...value, provider, model })
}

function normalizeEvidence(value: unknown): EvidenceRef {
  const source = isRecord(value) ? value : {}
  const document = isRecord(source.document) ? source.document : isRecord(source.pdf_document) ? source.pdf_document : null
  return {
    id: source.id == null ? undefined : String(source.id),
    namespace: source.namespace == null ? null : String(source.namespace),
    source_ref: source.source_ref == null ? (source.id == null ? undefined : String(source.id)) : String(source.source_ref),
    title: source.title == null ? undefined : String(source.title),
    url: source.url == null ? null : String(source.url),
    locator: source.locator == null ? null : String(source.locator),
    source_type: source.source_type == null ? null : String(source.source_type),
    published_at: source.publication_at == null ? (source.published_at == null ? null : String(source.published_at)) : String(source.publication_at),
    retrieved_at: source.retrieved_at == null ? null : String(source.retrieved_at),
    observation_time: source.observed_at == null ? null : String(source.observed_at),
    excerpt: source.excerpt == null ? (source.content == null ? null : String(source.content).slice(0, 320)) : String(source.excerpt),
    content: source.content == null ? null : String(source.content),
    content_hash: source.content_hash == null ? null : String(source.content_hash),
    version: typeof source.version === 'number' ? source.version : source.version == null ? null : Number(source.version),
    supersedes_id: source.supersedes_id == null ? (source.supersedes_source_id == null ? null : String(source.supersedes_source_id)) : String(source.supersedes_id),
    status: source.error ? 'error' : source.status == null ? null : String(source.status),
    validation_status: source.validation_status == null ? (source.validationStatus == null ? null : String(source.validationStatus)) : String(source.validation_status),
    validation_reason: source.validation_reason == null ? (source.validationReason == null ? null : String(source.validationReason)) : String(source.validation_reason),
    unknown_reason: source.unknown_reason == null ? null : String(source.unknown_reason),
    matched_excerpt: source.matched_excerpt == null ? (source.match_excerpt == null ? null : String(source.match_excerpt)) : String(source.matched_excerpt),
    line_start: typeof source.line_start === 'number' ? source.line_start : source.line_start == null ? null : Number(source.line_start),
    line_end: typeof source.line_end === 'number' ? source.line_end : source.line_end == null ? null : Number(source.line_end),
    selected_locator: source.selected_locator == null ? null : String(source.selected_locator),
    text_match: typeof source.text_match === 'boolean' ? source.text_match : source.text_match == null ? null : String(source.text_match).toLowerCase() === 'true',
    semantic_status: source.semantic_status == null ? null : String(source.semantic_status),
    binding_checks: Array.isArray(source.binding_checks) ? source.binding_checks.filter(isRecord).map((check) => ({ key: check.key == null ? undefined : String(check.key), status: check.status == null ? undefined : String(check.status), reason: check.reason == null ? undefined : String(check.reason) })) : [],
    source_version: source.source_version == null ? null : String(source.source_version),
    freshness: source.freshness == null ? null : String(source.freshness),
    freshness_policy: source.freshness_policy == null ? null : String(source.freshness_policy),
    freshness_as_of: source.freshness_as_of == null ? null : String(source.freshness_as_of),
    extraction_status: source.extraction_status == null ? (document?.status == null ? null : String(document.status)) : String(source.extraction_status),
    extraction_version: source.extraction_version == null ? (document?.extraction_version == null ? null : String(document.extraction_version)) : String(source.extraction_version),
    extraction_failure_reason: source.extraction_failure_reason == null ? (document?.reason == null ? (document?.failure_reason == null ? null : String(document.failure_reason)) : String(document.reason)) : String(source.extraction_failure_reason),
    original_mime_type: source.original_mime_type == null ? (source.mime_type == null ? (document?.mime_type == null ? null : String(document.mime_type)) : String(source.mime_type)) : String(source.original_mime_type),
    original_hash: source.original_hash == null ? (document?.original_hash == null ? null : String(document.original_hash)) : String(source.original_hash),
    page_count: source.page_count == null ? (document?.page_count == null ? null : Number(document.page_count)) : Number(source.page_count),
    document,
  }
}

function stringList(value: unknown): string[] {
  if (Array.isArray(value)) return value.filter((item) => typeof item === 'string' || typeof item === 'number').map(String)
  if (typeof value === 'string' || typeof value === 'number') {
    const text = String(value).trim()
    return text ? [text] : []
  }
  return []
}

function normalizeCandidateSource(value: unknown): EvidenceRef {
  if (typeof value === 'string') {
    const isUrl = /^https?:\/\//i.test(value)
    return { id: isUrl ? undefined : value, source_ref: value, title: value, url: isUrl ? value : null }
  }
  return normalizeEvidence(value)
}

function normalizeRoutingPlan(value: unknown): RoutingPlan | null {
  if (!isRecord(value)) return null
  return {
    intent: value.intent == null ? null : String(value.intent),
    horizon: value.horizon == null ? null : String(value.horizon),
    tickers: stringList(value.tickers),
    selected_analysts: stringList(value.selected_analysts),
    research_queries: stringList(value.research_queries),
    rationale: value.rationale == null ? null : String(value.rationale),
  }
}

function normalizeResearchCandidate(value: unknown): ResearchCandidate {
  if (typeof value === 'string') return { ticker: value, name: null, rationale: null, source_urls: [], primary_urls: [], urls: [], source_ids: [], source_refs: [], verified: false, evidence_available: false, status: 'candidate', unverified_reason: null }
  const candidate = isRecord(value) ? value : {}
  const sourceIds = stringList(candidate.source_ids)
  const rawRefs = Array.isArray(candidate.source_refs) ? candidate.source_refs : sourceIds
  const evidenceAvailable = typeof candidate.evidence_available === 'boolean'
    ? candidate.evidence_available
    : candidate.evidence_available == null ? undefined : String(candidate.evidence_available).toLowerCase() === 'true'
  return {
    ticker: candidate.ticker == null ? null : String(candidate.ticker),
    symbol: candidate.symbol == null ? null : String(candidate.symbol),
    name: candidate.name == null ? null : String(candidate.name),
    rationale: candidate.rationale == null ? (candidate.reason == null ? null : String(candidate.reason)) : String(candidate.rationale),
    source_urls: stringList(candidate.source_urls),
    primary_urls: stringList(candidate.primary_urls),
    urls: stringList(candidate.urls),
    source_ids: sourceIds,
    source_refs: rawRefs.map(normalizeCandidateSource),
    verified: typeof candidate.verified === 'boolean' ? candidate.verified : String(candidate.verified ?? '').toLowerCase() === 'true',
    evidence_available: evidenceAvailable,
    status: candidate.status == null ? (candidate.verified_status == null ? null : String(candidate.verified_status)) : String(candidate.status),
    unverified_reason: candidate.unverified_reason == null ? (candidate.verified_reason == null ? null : String(candidate.verified_reason)) : String(candidate.unverified_reason),
  }
}

function normalizePricePlan(value: unknown, outer: Record<string, any> = {}): CioPricePlan | null {
  if (value == null && Object.keys(outer).length === 0) return null
  if (value == null && Object.values(outer).every((item) => item == null || item === '')) return null
  if (typeof value === 'string' || typeof value === 'number') return {
    value: String(value), low: null, high: null,
    currency: outer.currency == null ? null : String(outer.currency),
    as_of: outer.as_of == null ? null : String(outer.as_of),
    horizon: outer.horizon == null ? null : String(outer.horizon),
    basis: outer.basis == null ? null : String(outer.basis),
    source_refs: stringList(outer.source_refs ?? outer.source_ids),
    missing_reason: outer.missing_reason == null ? null : String(outer.missing_reason),
  }
  const price = isRecord(value) ? value : {}
  const sourceRefs = stringList(price.source_refs ?? price.source_ids ?? outer.source_refs ?? outer.source_ids)
  const low = price.low ?? price.lower
  const high = price.high ?? price.upper
  const scalar = price.value ?? (value == null ? outer.value : undefined)
  return {
    low: low == null ? null : String(low),
    high: high == null ? null : String(high),
    value: scalar == null ? null : String(scalar),
    currency: price.currency == null ? (outer.currency == null ? null : String(outer.currency)) : String(price.currency),
    as_of: price.as_of == null ? (outer.as_of == null ? null : String(outer.as_of)) : String(price.as_of),
    horizon: price.horizon == null ? (outer.horizon == null ? null : String(outer.horizon)) : String(price.horizon),
    basis: price.basis == null ? (outer.basis == null ? null : String(outer.basis)) : String(price.basis),
    source_refs: sourceRefs,
    missing_reason: price.missing_reason == null ? (outer.missing_reason == null ? null : String(outer.missing_reason)) : String(price.missing_reason),
  }
}

/** Normalize both the new CIO brief and the legacy DecisionBrief shape. */
export function normalizeCioBrief(value: unknown): CioBrief | null {
  if (!isRecord(value)) return null
  const raw = value
  // The current backend keeps the human entry advice in `entry_plan` and the
  // evidence-backed range in `entry_zone`. Prefer the range whenever it is
  // present so a legacy string cannot hide a real lower/upper pair.
  const entryZone = isRecord(raw.entry_zone) ? raw.entry_zone : null
  const entryValue = entryZone ?? raw.entry_plan ?? raw.entry_zone
  const targetRaw = raw.target_price
  const target = normalizePricePlan(targetRaw, {
    value: targetRaw,
    currency: raw.target_price_currency,
    as_of: raw.target_price_as_of,
    horizon: raw.target_price_horizon,
    basis: raw.target_price_basis,
    source_refs: raw.target_price_source_refs,
    missing_reason: raw.target_price_missing_reason,
  })
  const entry = normalizePricePlan(entryValue, entryZone ? {} : {
    currency: raw.currency,
    as_of: raw.as_of,
    horizon: raw.horizon,
    basis: raw.basis,
    source_refs: raw.source_refs,
    missing_reason: raw.missing_reason,
  })
  const stanceValue = raw.stance ?? raw.disposition ?? raw.decision ?? null
  const reasonValue = raw.reason ?? raw.rationale ?? (raw.entry_advice == null
    ? (typeof raw.entry_plan === 'string' ? raw.entry_plan : null)
    : raw.entry_advice)
  const acceptedRisks = stringList(raw.accepted_risks ?? raw.risks)
  const catalysts = stringList(raw.catalysts ?? raw.growth_catalysts)
  const blockingGaps = stringList(raw.blocking_gaps ?? raw.missing_inputs ?? raw.missing_data)
  const legacy = typeof raw.entry_plan === 'string' || raw.entry_zone != null || raw.entry_advice != null || raw.target_price_currency != null || raw.target_price_basis != null || raw.risks != null || raw.missing_inputs != null
  const candidateBriefs = Array.isArray(raw.candidate_briefs)
    ? raw.candidate_briefs.map(normalizeCioBrief).filter((item): item is CioBrief => Boolean(item))
    : []
  return {
    stance: stanceValue == null ? null : String(stanceValue),
    reason: reasonValue == null ? null : String(reasonValue),
    as_of: raw.as_of == null ? null : String(raw.as_of),
    horizon: raw.horizon == null ? null : String(raw.horizon),
    ticker: raw.ticker == null ? null : String(raw.ticker),
    entry_plan: entry,
    target_price: target,
    accepted_risks: acceptedRisks,
    catalysts,
    invalidation_conditions: stringList(raw.invalidation_conditions),
    blocking_gaps: blockingGaps,
    simulation_ids: stringList(raw.simulation_ids ?? raw.scenario_ids ?? raw.simulation_id),
    next_review_trigger: raw.next_review_trigger == null ? (raw.next_review_at == null ? null : String(raw.next_review_at)) : String(raw.next_review_trigger),
    candidate_briefs: candidateBriefs,
    entry_advice: raw.entry_advice == null ? null : String(raw.entry_advice),
    entry_zone: raw.entry_zone == null ? null : normalizePricePlan(raw.entry_zone),
    target_price_basis: raw.target_price_basis == null ? null : String(raw.target_price_basis),
    target_price_source_refs: stringList(raw.target_price_source_refs),
    risks: stringList(raw.risks),
    missing_inputs: stringList(raw.missing_inputs),
    legacy,
    allocation_mode: raw.allocation_mode == null ? (raw.allocationMode == null ? null : String(raw.allocationMode)) : String(raw.allocation_mode),
    direction: raw.direction == null ? null : String(raw.direction),
    strategy: raw.strategy == null ? null : String(raw.strategy),
    asset_class: raw.asset_class == null ? null : String(raw.asset_class),
    benchmark_ticker: raw.benchmark_ticker == null ? null : String(raw.benchmark_ticker),
    benchmark_rationale: raw.benchmark_rationale == null ? null : String(raw.benchmark_rationale),
    thesis: isRecord(raw.thesis) ? raw.thesis as CioBrief['thesis'] : null,
    valuation_assumptions: isRecord(raw.valuation_assumptions) ? raw.valuation_assumptions as CioBrief['valuation_assumptions'] : null,
    action_plan: isRecord(raw.action_plan) ? raw.action_plan as CioBrief['action_plan'] : null,
  }
}

function normalizeMissingGap(value: unknown): MissingGap {
  const gap = isRecord(value) ? value : {}
  const rawLinks: unknown = gap.result_links ?? gap.result_refs ?? gap.links
  const resultLinks = Array.isArray(rawLinks)
    ? rawLinks.filter(isRecord).map((link: Record<string, any>) => ({
      id: link.id == null ? (link.output_id == null ? (link.run_id == null ? undefined : String(link.run_id)) : String(link.output_id)) : String(link.id),
      title: link.title == null ? (link.name == null ? undefined : String(link.name)) : String(link.title),
      type: link.type == null ? undefined : String(link.type),
      url: link.url == null ? null : String(link.url),
    }))
    : []
  return {
    ...gap,
    key: gap.key == null ? (gap.id == null ? undefined : String(gap.id)) : String(gap.key),
    description: gap.description == null ? (gap.reason == null ? undefined : String(gap.reason)) : String(gap.description),
    relevant_role: gap.relevant_role == null ? (gap.assigned_agent_id == null ? null : String(gap.assigned_agent_id)) : String(gap.relevant_role),
    owner: gap.owner == null ? (gap.assigned_agent_id == null ? null : String(gap.assigned_agent_id)) : String(gap.owner),
    reopen_when: gap.reopen_when == null ? null : String(gap.reopen_when),
    source_requirements: stringList(gap.source_requirements),
    status: gap.status == null ? null : String(gap.status),
    why_waiting: gap.why_waiting == null ? (gap.wait_reason == null ? (gap.blocking_reason == null ? (gap.terminal_reason == null ? null : String(gap.terminal_reason)) : String(gap.blocking_reason)) : String(gap.wait_reason)) : String(gap.why_waiting),
    action: gap.action == null ? (gap.next_action == null ? (gap.repair_run_id == null ? null : `Follow repair run ${String(gap.repair_run_id)}`) : String(gap.next_action)) : String(gap.action),
    result_links: resultLinks,
    repair_run_id: gap.repair_run_id == null ? null : String(gap.repair_run_id),
    resolved_by_output_id: gap.resolved_by_output_id == null ? null : String(gap.resolved_by_output_id),
    updated_at: gap.updated_at == null ? null : String(gap.updated_at),
  }
}

export function normalizeMemoryContext(value: unknown): MemoryContext | null {
  if (!isRecord(value)) return null
  const normalizeItem = (item: unknown): MemoryItem => {
    const raw = isRecord(item) ? item : {}
    return {
      ...raw,
      record_id: raw.record_id == null ? (raw.id == null ? undefined : String(raw.id)) : String(raw.record_id),
      record_type: raw.record_type == null ? (raw.kind == null ? undefined : String(raw.kind)) : String(raw.record_type),
      namespace: raw.namespace == null ? undefined : String(raw.namespace),
      title: raw.title == null ? 'Reused memory record' : String(raw.title),
      excerpt: raw.excerpt == null ? (raw.summary == null ? '' : String(raw.summary)) : String(raw.excerpt),
      source_refs: stringList(raw.source_refs ?? raw.source_ids),
      source_versions: Array.isArray(raw.source_versions) ? raw.source_versions.filter(isRecord) : [],
      observed_at: raw.observed_at == null ? null : String(raw.observed_at),
      retrieved_at: raw.retrieved_at == null ? null : String(raw.retrieved_at),
      freshness: raw.freshness == null ? (raw.stale ? 'stale' : 'unknown') : String(raw.freshness),
      reuse_reason: raw.reuse_reason == null ? '' : String(raw.reuse_reason),
      stale: Boolean(raw.stale),
    }
  }
  const taskContexts = Array.isArray(value.tasks)
    ? value.tasks.filter(isRecord).map((task) => normalizeMemoryContext(task)).filter((task): task is MemoryContext => Boolean(task))
    : []
  const reused = Array.isArray(value.reused)
    ? value.reused.map(normalizeItem)
    : taskContexts.flatMap((task) => task.reused ?? [])
  const fresh = Array.isArray(value.fresh)
    ? value.fresh.map(normalizeItem)
    : taskContexts.flatMap((task) => task.fresh ?? [])
  return {
    ...value,
    namespace: value.namespace == null ? undefined : String(value.namespace),
    run_id: value.run_id == null ? undefined : String(value.run_id),
    task_id: value.task_id == null ? null : String(value.task_id),
    reused,
    fresh,
    task_contexts: taskContexts,
    freshness_as_of: value.freshness_as_of == null ? null : String(value.freshness_as_of),
    reason: value.reason == null ? '' : String(value.reason),
  }
}

function normalizeFactClaim(value: unknown): FactClaimRecord {
  const claim = isRecord(value) ? value : {}
  const numericLine = (candidate: unknown) => {
    if (typeof candidate === 'number' && Number.isFinite(candidate)) return candidate
    if (typeof candidate === 'string' && candidate.trim() && Number.isFinite(Number(candidate))) return Number(candidate)
    return null
  }
  return {
    ...claim,
    claim: claim.claim == null ? null : String(claim.claim),
    value: claim.value == null ? null : String(claim.value),
    unit: claim.unit == null ? null : String(claim.unit),
    period: claim.period == null ? null : String(claim.period),
    source_ref: claim.source_ref == null ? (claim.source_id == null ? null : String(claim.source_id)) : String(claim.source_ref),
    source_id: claim.source_id == null ? (claim.source_ref == null ? null : String(claim.source_ref)) : String(claim.source_id),
    locator: claim.locator == null ? null : String(claim.locator),
    validation_status: claim.validation_status == null ? (claim.status == null ? null : String(claim.status)) : String(claim.validation_status),
    status: claim.status == null ? null : String(claim.status),
    unknown_reason: claim.unknown_reason == null ? (claim.missing_reason == null ? null : String(claim.missing_reason)) : String(claim.unknown_reason),
    validation_reason: claim.validation_reason == null ? null : String(claim.validation_reason),
    matched_excerpt: claim.matched_excerpt == null ? (claim.excerpt == null ? null : String(claim.excerpt)) : String(claim.matched_excerpt),
    excerpt: claim.excerpt == null ? null : String(claim.excerpt),
    line_start: numericLine(claim.line_start ?? claim.start_line),
    line_end: numericLine(claim.line_end ?? claim.end_line),
    subject: claim.subject == null ? null : String(claim.subject),
    metric: claim.metric == null ? null : String(claim.metric),
    scale: claim.scale == null ? null : String(claim.scale),
    currency: claim.currency == null ? null : String(claim.currency),
    period_start: claim.period_start == null ? null : String(claim.period_start),
    period_end: claim.period_end == null ? null : String(claim.period_end),
    basis: claim.basis == null ? null : String(claim.basis),
    statement_type: claim.statement_type == null ? null : String(claim.statement_type),
    source_quote: claim.source_quote == null ? null : String(claim.source_quote),
    semantic_status: claim.semantic_status == null ? null : String(claim.semantic_status),
    binding_checks: Array.isArray(claim.binding_checks) ? claim.binding_checks.filter(isRecord).map((check) => ({ key: check.key == null ? undefined : String(check.key), status: check.status == null ? undefined : String(check.status), reason: check.reason == null ? undefined : String(check.reason) })) : [],
    source_version: claim.source_version == null ? null : String(claim.source_version),
    freshness: claim.freshness == null ? null : String(claim.freshness),
  }
}

export function normalizeOutput(value: unknown): OutputRecord {
  const output = isRecord(value) ? value : {}
  const sources = Array.isArray(output.source_refs) ? output.source_refs : []
  const routingPlan = normalizeRoutingPlan(output.routing_plan)
  const candidates = Array.isArray(output.research_candidates) ? output.research_candidates.map(normalizeResearchCandidate) : []
  const rawNamespace = output.provenance == null ? output.namespace : output.provenance
  const provenance = rawNamespace == null ? undefined : String(rawNamespace) === 'real' ? 'real_research' : String(rawNamespace)
  const inlineBrief = output.agent_id === 'A11' && (output.stance != null || output.entry_plan != null || output.entry_zone != null || output.target_price != null || stringList(output.risks).length > 0 || stringList(output.catalysts).length > 0)
    ? output
    : null
  const rawBrief = output.cio_brief ?? output.decision_brief ?? output.cio_summary
  // Some persisted outputs expose aliases at the top level while also
  // carrying a nested legacy brief. Merge them before normalization so the
  // nested read model cannot discard a direct `entry_zone` or snapshot.
  const briefValue = isRecord(rawBrief) ? { ...output, ...rawBrief } : rawBrief ?? inlineBrief
  const decisionBrief = output.agent_id != null && output.agent_id !== 'A11' ? null : normalizeCioBrief(briefValue)
  const rawScenarioSet = isRecord(output.price_scenarios)
    ? output.price_scenarios
    : isRecord(output.simulation_snapshot)
      ? output.simulation_snapshot
      : isRecord(output.scenario_result)
        ? output.scenario_result
        : isRecord(output.scenario_snapshot) ? output.scenario_snapshot : null
  const rawScenarioBuckets = rawScenarioSet && isRecord(rawScenarioSet.scenarios) ? rawScenarioSet.scenarios : isRecord(output.scenarios) ? output.scenarios : null
  const scenarioSnapshotValues = [
    ...(isRecord(output.simulation_snapshot) ? [output.simulation_snapshot] : []),
    ...(Array.isArray(output.simulation_snapshots) ? output.simulation_snapshots.filter(isRecord) : []),
  ]
  const scenarioSnapshots = scenarioSnapshotValues.filter((snapshot, index, all) => {
    const hash = snapshot.result_hash
    return hash == null || all.findIndex((candidate) => String(candidate.result_hash ?? '') === String(hash)) === index
  })
  const persistedScenarioFan = scenarioSnapshots.flatMap((snapshot) => {
    if (!isRecord(snapshot.scenarios)) return []
    const ticker = String(snapshot.ticker ?? '').trim()
    const currency = snapshot.currency == null ? undefined : String(snapshot.currency)
    return Object.entries(snapshot.scenarios).flatMap(([label, bucket]) => {
      if (!isRecord(bucket) || !Array.isArray(bucket.fan)) return []
      return bucket.fan.filter(isRecord).map((row) => ({
        ...row,
        label: `${ticker ? `${ticker} · ` : ''}${label} · day ${String(row.trading_day ?? '')}`.replace(/ · day $/, ''),
        currency,
      }))
    })
  })
  const persistedScenarioQuantiles = scenarioSnapshots.flatMap((snapshot) => {
    if (!isRecord(snapshot.scenarios)) return []
    const ticker = String(snapshot.ticker ?? '').trim()
    const currency = snapshot.currency == null ? undefined : String(snapshot.currency)
    return Object.entries(snapshot.scenarios).flatMap(([label, bucket]) => {
      if (!isRecord(bucket) || !isRecord(bucket.terminal_price_quantiles)) return []
      return [{ ...bucket.terminal_price_quantiles, label: `${ticker ? `${ticker} · ` : ''}${label}`, currency }]
    })
  })
  const directFan = output.scenario_fan ?? output.scenario_fan_chart ?? output.scenario_bands
  const scenarioFan = Array.isArray(directFan)
    ? directFan.filter(isRecord)
    : isRecord(directFan)
      ? Object.entries(directFan).flatMap(([label, bucket]) => isRecord(bucket) && Array.isArray(bucket.fan) ? bucket.fan.filter(isRecord).map((row) => ({ ...row, label })) : [])
    : persistedScenarioFan.length
      ? persistedScenarioFan
      : rawScenarioBuckets
      ? Object.entries(rawScenarioBuckets).flatMap(([label, bucket]) => {
        if (!isRecord(bucket)) return []
        const fan = Array.isArray(bucket.fan) ? bucket.fan : []
        return fan.filter(isRecord).map((row) => ({ ...row, label: `${label} · ${row.trading_day == null ? 'day' : `day ${row.trading_day}`}` }))
      })
      : []
  const directQuantiles = output.scenario_quantiles ?? output.quantiles ?? output.scenario_terminal_quantiles
  const scenarioQuantiles = Array.isArray(directQuantiles)
    ? directQuantiles.filter(isRecord)
    : persistedScenarioQuantiles.length
      ? persistedScenarioQuantiles
    : rawScenarioBuckets
      ? Object.entries(rawScenarioBuckets).filter(([, bucket]) => isRecord(bucket)).map(([label, bucket]) => {
        const quantiles = isRecord(bucket) && isRecord(bucket.terminal_price_quantiles) ? bucket.terminal_price_quantiles : {}
        return { ...(quantiles as Record<string, unknown>), label }
      })
      : []
  const scenarioMeta = scenarioSnapshots[0] ?? rawScenarioSet
  const scenarioMetaRecord = isRecord(scenarioMeta) ? scenarioMeta : {}
  const scenarioStatuses = scenarioSnapshots.map((snapshot) => String(snapshot.status ?? '').toLowerCase().replaceAll('-', '_').replaceAll(' ', '_')).filter(Boolean)
  const scenarioStatus = scenarioStatuses.includes('insufficient_evidence')
    ? 'insufficient_evidence'
    : scenarioStatuses[0] ?? (output.scenario_status == null ? null : String(output.scenario_status))
  const scenarioMethod = scenarioMetaRecord.method == null
    ? (scenarioMetaRecord.calculation_method == null
      ? (output.scenario_method == null
        ? (output.agent_id === 'A07' && rawScenarioSet ? 'deterministic local calculation' : null)
        : String(output.scenario_method))
      : String(scenarioMetaRecord.calculation_method))
    : String(scenarioMetaRecord.method)
  const scenarioSourceValues = scenarioMetaRecord.source_refs ?? scenarioMetaRecord.sources ?? scenarioMetaRecord.source_ids
  const scenarioSourceRefs = Array.isArray(scenarioSourceValues)
    ? scenarioSourceValues.map((source: unknown) => typeof source === 'string' ? { id: source, source_ref: source, title: source } : normalizeEvidence(source))
    : []
  const scenarioAssumptions = stringList(scenarioMetaRecord.assumptions ?? output.scenario_assumptions)
  const scenarioLimitations = stringList(scenarioMetaRecord.limitations ?? scenarioMetaRecord.limits ?? output.scenario_limitations)
  const calculations: CalculationRecord[] = Array.isArray(output.calculations) ? output.calculations.map((item: unknown, index): CalculationRecord => {
    if (typeof item === 'string') return { label: item, value: null, unit: null, formula: null, missing_reason: 'Calculation detail was not returned by the backend.' }
    const calculation = isRecord(item) ? item : {}
    return {
      label: calculation.label == null ? `Calculation ${index + 1}` : String(calculation.label),
      value: calculation.value == null ? null : String(calculation.value),
      unit: calculation.unit == null ? null : String(calculation.unit),
      formula: calculation.formula == null ? null : String(calculation.formula),
      missing_reason: calculation.missing_reason == null ? null : String(calculation.missing_reason),
      operation: calculation.operation == null ? null : String(calculation.operation),
      input_fact_indices: Array.isArray(calculation.input_fact_indices) ? calculation.input_fact_indices.filter((entry): entry is number => typeof entry === 'number') : [],
      window: typeof calculation.window === 'number' ? calculation.window : null,
      assumed_discount_fraction: calculation.assumed_discount_fraction == null ? null : String(calculation.assumed_discount_fraction),
      assumption_rationale: calculation.assumption_rationale == null ? null : String(calculation.assumption_rationale),
    }
  }) : []
  return {
    id: output.id == null ? (output.output_id == null ? undefined : String(output.output_id)) : String(output.id),
    output_id: output.output_id == null ? (output.id == null ? undefined : String(output.id)) : String(output.output_id),
    task_id: output.task_id == null ? undefined : String(output.task_id),
    run_id: output.run_id == null ? null : String(output.run_id),
    attempt_id: output.attempt_id == null ? null : String(output.attempt_id),
    agent_id: output.agent_id == null ? null : String(output.agent_id),
    version: typeof output.version === 'number' ? output.version : 1,
    title: output.title == null ? 'Saved research output' : String(output.title),
    status: output.status == null ? undefined : String(output.status),
    conclusion: output.summary == null ? (output.conclusion == null ? null : String(output.conclusion)) : String(output.summary),
    proposed_action: output.proposed_action == null ? null : String(output.proposed_action),
    created_at: output.created_at == null ? null : String(output.created_at),
    updated_at: output.updated_at == null ? null : String(output.updated_at),
    provider: output.provider == null ? null : String(output.provider),
    model: output.model == null ? null : String(output.model),
    reasoning_mode: output.reasoning_effort == null ? (output.reasoning_mode == null ? null : String(output.reasoning_mode)) : String(output.reasoning_effort),
    prompt_version: output.prompt_version == null ? null : String(output.prompt_version),
    provenance,
    fact_claims: Array.isArray(output.fact_claims) ? output.fact_claims.map(normalizeFactClaim) : [],
    assumptions: Array.isArray(output.assumptions) ? output.assumptions.map(String) : [],
    calculations,
    counterarguments: Array.isArray(output.counterarguments) ? output.counterarguments.map(String) : [],
    missing_data: Array.isArray(output.missing_data) ? output.missing_data.map(String) : [],
    invalidation_conditions: Array.isArray(output.invalidation_conditions) ? output.invalidation_conditions.map(String) : [],
    source_refs: sources.map((source: unknown) => typeof source === 'string' ? { id: source, source_ref: source, title: source } : normalizeEvidence(source)),
    review_disposition: output.review_disposition == null ? null : String(output.review_disposition),
    revision_requests: stringList(output.revision_requests),
    decision_disposition: output.decision_disposition == null ? null : String(output.decision_disposition),
    decision_brief: decisionBrief,
    cio_brief: decisionBrief,
    current_decision: isRecord(output.current_decision) ? output.current_decision : null,
    canonical_decision: isRecord(output.canonical_decision) ? output.canonical_decision : (isRecord(output.current_case_decision) ? output.current_case_decision : null),
    current_case_decision: isRecord(output.current_case_decision) ? output.current_case_decision : null,
    missing_gaps: Array.isArray(output.missing_gaps) ? output.missing_gaps.map(normalizeMissingGap) : [],
    memory_context: normalizeMemoryContext(output.memory_context),
    scenario_fan: scenarioFan,
    scenario_quantiles: scenarioQuantiles,
    price_scenarios: rawScenarioSet,
    simulation_snapshot: isRecord(output.simulation_snapshot) ? output.simulation_snapshot : null,
    simulation_snapshots: Array.isArray(output.simulation_snapshots) ? output.simulation_snapshots.filter(isRecord) : [],
    scenario_status: scenarioStatus,
    scenario_method: scenarioMethod,
    scenario_ticker: scenarioMetaRecord.ticker == null ? (output.scenario_ticker == null ? null : String(output.scenario_ticker)) : String(scenarioMetaRecord.ticker),
    scenario_currency: scenarioMetaRecord.currency == null ? (output.scenario_currency == null ? null : String(output.scenario_currency)) : String(scenarioMetaRecord.currency),
    scenario_as_of: scenarioMetaRecord.as_of == null ? (output.scenario_as_of == null ? null : String(output.scenario_as_of)) : String(scenarioMetaRecord.as_of),
    scenario_missing_reason: scenarioMetaRecord.missing_reason == null ? (output.scenario_missing_reason == null ? null : String(output.scenario_missing_reason)) : String(scenarioMetaRecord.missing_reason),
    scenario_assumptions: scenarioAssumptions,
    scenario_limitations: scenarioLimitations,
    scenario_source_refs: scenarioSourceRefs,
    scenario_result_hash: scenarioMetaRecord.result_hash == null ? null : String(scenarioMetaRecord.result_hash),
    technical_indicators: Array.isArray(output.technical_indicators ?? output.technical_cards ?? output.technical_evidence ?? output.technical)
      ? (output.technical_indicators ?? output.technical_cards ?? output.technical_evidence ?? output.technical).filter(isRecord)
      : [],
    multi_timeframe: Array.isArray(output.multi_timeframe ?? output.timeframes ?? output.multi_frequency ?? output.indicator_cards)
      ? (output.multi_timeframe ?? output.timeframes ?? output.multi_frequency ?? output.indicator_cards).filter(isRecord)
      : [],
    routing_plan: routingPlan,
    research_candidates: candidates,
    body: output.analysis == null ? (output.body == null ? null : String(output.body)) : String(output.analysis),
  }
}

export function normalizeTask(value: unknown): TaskRecord {
  const task = isRecord(value) ? value : {}
  const output = task.output == null ? null : normalizeOutput(task.output)
  const outputs = Array.isArray(task.outputs) ? task.outputs.map(normalizeOutput) : output ? [output] : []
  const refs = Array.isArray(task.input_refs) ? task.input_refs.map(normalizeCandidateSource) : Array.isArray(task.evidence_refs) ? task.evidence_refs.map(normalizeCandidateSource) : []
  const resolvedModel = normalizeResolvedModel(task.resolved_model)
  return {
    id: task.id == null ? (task.task_id == null ? undefined : String(task.task_id)) : String(task.id),
    task_id: task.task_id == null ? (task.id == null ? undefined : String(task.id)) : String(task.task_id),
    run_id: task.run_id == null ? null : String(task.run_id),
    agent_id: task.agent_id == null ? undefined : String(task.agent_id),
    title: task.title == null ? null : String(task.title),
    kind: task.kind == null ? null : String(task.kind),
    run_status: task.run_status == null ? null : normalizeStatus(task.run_status),
    status: normalizeStatus(task.status) as TaskRecord['status'],
    paused: Boolean(task.paused),
    attempt_id: task.attempt_id == null ? null : String(task.attempt_id),
    progress_message: task.progress_message == null ? (task.current_task == null ? null : String(task.current_task)) : String(task.progress_message),
    mandate: task.mandate == null ? null : String(task.mandate),
    question: task.question == null ? (task.title == null ? null : String(task.title)) : String(task.question),
    current_task: task.progress_message == null ? (task.current_task == null ? null : String(task.current_task)) : String(task.progress_message),
    started_at: task.started_at == null ? null : String(task.started_at),
    provider_started_at: task.provider_started_at == null ? null : String(task.provider_started_at),
    created_at: task.created_at == null ? null : String(task.created_at),
    updated_at: task.updated_at == null ? null : String(task.updated_at),
    completed_at: task.completed_at == null ? null : String(task.completed_at),
    elapsed_seconds: typeof task.elapsed_seconds === 'number' ? task.elapsed_seconds : null,
    progress: typeof task.progress === 'number' ? task.progress : null,
    blocking_reason: task.blocking_reason == null ? null : String(task.blocking_reason),
    output_id: task.output_id == null ? null : String(task.output_id),
    output_status: task.output_status == null ? null : String(task.output_status),
    terminal_summary: task.terminal_summary == null ? null : String(task.terminal_summary),
    wait_reason: task.wait_reason == null ? null : String(task.wait_reason),
    dispatch_state: task.dispatch_state == null ? null : String(task.dispatch_state),
    sequence_no: typeof task.sequence_no === 'number' ? task.sequence_no : task.sequence_no == null ? undefined : Number(task.sequence_no),
    review_context: isRecord(task.review_context) ? {
      output_id: task.review_context.output_id == null ? null : String(task.review_context.output_id),
      title: task.review_context.title == null ? null : String(task.review_context.title),
      relation: task.review_context.relation == null ? null : String(task.review_context.relation),
      label: task.review_context.label == null ? null : String(task.review_context.label),
    } : null,
    memory_context: normalizeMemoryContext(task.memory_context),
    assignment_reason: task.assignment_reason == null ? null : String(task.assignment_reason),
    origin: task.origin == null ? null : String(task.origin),
    retry_at: task.retry_at == null ? null : String(task.retry_at),
    input_references: refs,
    evidence_refs: refs,
    messages: Array.isArray(task.messages) ? task.messages.map((item: unknown) => {
      const message = isRecord(item) ? item : {}
      return { at: message.at == null ? undefined : String(message.at), text: message.text == null ? undefined : String(message.text), type: message.type == null ? undefined : String(message.type) }
    }) : task.progress_message ? [{ at: task.updated_at == null ? undefined : String(task.updated_at), text: String(task.progress_message), type: 'progress' }] : [],
    output,
    outputs,
    history: Array.isArray(task.history) ? task.history as Array<Record<string, unknown>> : [],
    model: task.model == null ? null : normalizeModel(task.model),
    resolved_model_config: resolvedModel,
    allowed_actions: normalizeActionList(task.allowed_actions),
    attempts: Array.isArray(task.attempts) ? task.attempts.map(normalizeAttempt) : [],
  }
}

function normalizeCounts(value: unknown): RunCounts {
  if (!isRecord(value)) return {}
  const counts: RunCounts = {}
  for (const [key, item] of Object.entries(value)) {
    if (typeof item === 'number' && Number.isFinite(item)) counts[key] = item
    else if (typeof item === 'string' && item.trim() && Number.isFinite(Number(item))) counts[key] = Number(item)
  }
  return counts
}

function normalizeActionList(value: unknown): string[] | Record<string, boolean> {
  if (Array.isArray(value)) return value.filter((item) => typeof item === 'string' || typeof item === 'number').map(String)
  if (isRecord(value)) {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, Boolean(item)]))
  }
  return []
}

function normalizeAttempt(value: unknown): RunAttemptRecord {
  const attempt = isRecord(value) ? value : {}
  const config = attempt.resolved_model_config ?? attempt.resolved_model
  return {
    ...attempt,
    id: attempt.id == null ? (attempt.attempt_id == null ? undefined : String(attempt.attempt_id)) : String(attempt.id),
    attempt_id: attempt.attempt_id == null ? (attempt.id == null ? undefined : String(attempt.id)) : String(attempt.attempt_id),
    task_id: attempt.task_id == null ? null : String(attempt.task_id),
    status: attempt.status == null ? null : normalizeStatus(attempt.status),
    attempt_no: typeof attempt.attempt_no === 'number' ? attempt.attempt_no : attempt.attempt_no == null ? null : Number(attempt.attempt_no),
    provider: attempt.provider == null ? null : String(attempt.provider),
    model: attempt.model == null ? null : String(attempt.model),
    reasoning_effort: attempt.reasoning_effort == null ? null : String(attempt.reasoning_effort),
    stage: attempt.stage == null ? null : String(attempt.stage),
    execution_stage: attempt.execution_stage == null ? (attempt.stage == null ? null : String(attempt.stage)) : String(attempt.execution_stage),
    provider_started_at: attempt.provider_started_at == null ? null : String(attempt.provider_started_at),
    started_at: attempt.started_at == null ? null : String(attempt.started_at),
    finished_at: attempt.finished_at == null ? (attempt.completed_at == null ? null : String(attempt.completed_at)) : String(attempt.finished_at),
    completed_at: attempt.completed_at == null ? null : String(attempt.completed_at),
    created_at: attempt.created_at == null ? null : String(attempt.created_at),
    updated_at: attempt.updated_at == null ? null : String(attempt.updated_at),
    error: attempt.error == null ? null : String(attempt.error),
    message: attempt.message == null ? null : String(attempt.message),
    resolved_model: config == null ? null : normalizeResolvedModel(config),
    resolved_model_config: config == null ? null : normalizeResolvedModel(config),
  }
}

function normalizeDependency(value: unknown): RunDependencyRecord {
  const dependency = isRecord(value) ? value : {}
  return {
    ...dependency,
    task_id: dependency.task_id == null ? (dependency.id == null ? undefined : String(dependency.id)) : String(dependency.task_id),
    depends_on_task_id: dependency.depends_on_task_id == null ? (dependency.depends_on == null ? (dependency.dependency_id == null ? (dependency.task_id == null ? undefined : String(dependency.task_id)) : String(dependency.dependency_id)) : String(dependency.depends_on)) : String(dependency.depends_on_task_id),
    id: dependency.id == null ? undefined : String(dependency.id),
    agent_id: dependency.agent_id == null ? undefined : String(dependency.agent_id),
    title: dependency.title == null ? undefined : String(dependency.title),
    status: dependency.status == null ? null : normalizeStatus(dependency.status),
  }
}

function normalizeRunSummary(value: unknown): RunSummary {
  const run = isRecord(value) ? value : {}
  const rawExecution = isRecord(run.execution) ? run.execution : null
  const rawEvidence = isRecord(run.evidence) ? run.evidence : null
  const rawDisposition = isRecord(run.disposition) ? run.disposition : null
  const counts = normalizeCounts(run.counts ?? run.task_counts ?? run.task_summary)
  if (counts.total == null && (run.task_count != null)) counts.total = Number(run.task_count)
  if (counts.completed == null && (run.completed_task_count != null)) counts.completed = Number(run.completed_task_count)
  if (counts.outputs == null && (run.output_count != null)) counts.outputs = Number(run.output_count)
  if (counts.sources == null && (run.source_count != null)) counts.sources = Number(run.source_count)
  const outputValue = run.latest_output ?? run.latestOutput
  const latestOutput = isRecord(outputValue) ? normalizeOutput(outputValue) : null
  const decisionBrief = normalizeCioBrief(run.cio_brief ?? run.decision_brief ?? run.cio_summary ?? latestOutput?.decision_brief)
  const question = String(run.question ?? run.original_question ?? run.request ?? 'Untitled research question')
  const id = String(run.id ?? run.run_id ?? '')
  const agentSelection = Array.isArray(run.agent_selection) ? run.agent_selection.filter(isRecord) : []
  const selectedFromSelection = agentSelection.filter((item) => item.selected).map((item) => item.agent_id)
  // A routing-pending entry is an unresolved role decision, not a skipped role.
  // Keep it visible in `agent_selection` for the detail view without presenting
  // a future agent as deliberately omitted.
  const skippedFromSelection = agentSelection.filter((item) => {
    if (item.selected !== false) return false
    const reason = String(item.reason ?? '').toLowerCase()
    return !(reason.includes('routing') && (reason.includes('pending') || reason.includes('unknown')))
  }).map((item) => item.agent_id)
  const selected = stringList(run.selected_agent_ids ?? (selectedFromSelection.length ? selectedFromSelection : (run.selected_roles ?? run.selected_analysts)))
  const skipped = stringList(run.skipped_agent_ids ?? (skippedFromSelection.length ? skippedFromSelection : (run.skipped_roles ?? run.omitted_roles)))
  const executionStatus = run.execution_status ?? run.execution_state ?? rawExecution?.status ?? run.status
  const evidenceStatus = run.evidence_status ?? run.evidence_readiness ?? rawEvidence?.status ?? rawEvidence?.readiness
  const disposition = run.committee_disposition ?? run.decision_disposition ?? rawDisposition?.label ?? rawDisposition?.status ?? run.disposition
  return {
    ...run,
    id,
    run_id: run.run_id == null ? id : String(run.run_id),
    namespace: run.namespace == null ? undefined : String(run.namespace),
    question,
    original_question: run.original_question == null ? question : String(run.original_question),
    question_group_id: run.question_group_id == null ? (run.questionGroupId == null ? null : String(run.questionGroupId)) : String(run.question_group_id),
    status: run.status == null ? (executionStatus == null ? null : normalizeStatus(executionStatus)) : normalizeStatus(run.status),
    execution_status: executionStatus == null ? null : normalizeStatus(executionStatus),
    execution: rawExecution ? String(rawExecution.label ?? rawExecution.status ?? '') || null : run.execution == null ? null : String(run.execution),
    execution_state: run.execution_state == null ? null : String(run.execution_state),
    evidence_status: evidenceStatus == null ? null : String(evidenceStatus),
    evidence_readiness: run.evidence_readiness == null ? (evidenceStatus == null ? null : String(evidenceStatus)) : String(run.evidence_readiness),
    evidence: rawEvidence ? String(rawEvidence.label ?? rawEvidence.status ?? rawEvidence.readiness ?? '') || null : run.evidence == null ? null : String(run.evidence),
    disposition: disposition == null ? null : String(disposition),
    committee_disposition: run.committee_disposition == null ? (disposition == null ? null : String(disposition)) : String(run.committee_disposition),
    decision_disposition: run.decision_disposition == null ? null : String(run.decision_disposition),
    created_at: run.created_at == null ? null : String(run.created_at),
    updated_at: run.updated_at == null ? null : String(run.updated_at),
    latest_output_id: run.latest_output_id == null ? (latestOutput?.id ?? null) : String(run.latest_output_id),
    latest_output_title: run.latest_output_title == null ? (latestOutput?.title ?? null) : String(run.latest_output_title),
    latest_output_summary: run.latest_output_summary == null ? (run.summary == null ? (latestOutput?.conclusion ?? null) : String(run.summary)) : String(run.latest_output_summary),
    summary: run.summary == null ? (latestOutput?.conclusion ?? null) : String(run.summary),
    task_count: run.task_count == null ? (counts.total ?? undefined) : Number(run.task_count),
    completed_task_count: run.completed_task_count == null ? (counts.completed ?? undefined) : Number(run.completed_task_count),
    source_count: run.source_count == null ? (counts.sources ?? undefined) : Number(run.source_count),
    completed_at: run.completed_at == null ? null : String(run.completed_at),
    display_title: run.display_title == null ? null : String(run.display_title),
    parent_run_id: run.parent_run_id == null ? null : String(run.parent_run_id),
    followup_kind: run.followup_kind == null ? null : String(run.followup_kind),
    latest_output: latestOutput,
    counts,
    task_counts: normalizeCounts(run.task_counts ?? run.counts ?? run.task_summary),
    selected_agent_ids: selected,
    selected_roles: stringList(run.selected_roles ?? run.selected_agent_ids ?? run.selected_analysts),
    skipped_agent_ids: skipped,
    skipped_roles: stringList(run.skipped_roles ?? run.skipped_agent_ids ?? run.omitted_roles),
    allowed_actions: normalizeActionList(run.allowed_actions),
    current_blocker: run.current_blocker == null ? (run.blocking_reason == null ? null : String(run.blocking_reason)) : String(run.current_blocker),
    blocking_reason: run.blocking_reason == null ? null : String(run.blocking_reason),
    ticker: run.ticker == null ? null : String(run.ticker),
    horizon: run.horizon == null ? null : String(run.horizon),
    linked_run_id: run.linked_run_id == null ? null : String(run.linked_run_id),
    origin: run.origin == null ? null : String(run.origin),
    origin_ref: run.origin_ref == null ? null : String(run.origin_ref),
    root_run_id: run.root_run_id == null ? null : String(run.root_run_id),
    root_origin: run.root_origin == null ? null : String(run.root_origin),
    current_decision: isRecord(run.current_decision) ? run.current_decision : null,
    canonical_decision: isRecord(run.canonical_decision) ? run.canonical_decision : null,
    current_case_decision: isRecord(run.current_case_decision) ? run.current_case_decision : null,
    calculation_context: isRecord(run.calculation_context) ? run.calculation_context : null,
    decision_brief: decisionBrief,
    cio_brief: decisionBrief,
    blocking_gaps: Array.isArray(run.blocking_gaps ?? run.gap_resolution_ledger ?? run.research_gap_ledger ?? run.research_gaps ?? run.evidence_gaps)
      ? (run.blocking_gaps ?? run.gap_resolution_ledger ?? run.research_gap_ledger ?? run.research_gaps ?? run.evidence_gaps).map(normalizeMissingGap)
      : [],
    gap_resolution_ledger: Array.isArray(run.gap_resolution_ledger ?? run.research_gap_ledger ?? run.research_gaps ?? run.evidence_gaps ?? run.blocking_gaps)
      ? (run.gap_resolution_ledger ?? run.research_gap_ledger ?? run.research_gaps ?? run.evidence_gaps ?? run.blocking_gaps).map(normalizeMissingGap)
      : [],
    memory_context: normalizeMemoryContext(run.memory_context ?? run.memory),
    candidate_simulations: Array.isArray(run.candidate_simulations) ? run.candidate_simulations.filter(isRecord) : [],
  }
}

function normalizeRunTask(value: unknown): RunTaskRecord {
  const raw = isRecord(value) ? value : {}
  const base = normalizeTask(raw)
  const rawDependencies = raw.dependency_states ?? raw.dependencies ?? raw.dependency_ids ?? []
  const dependencyStates = Array.isArray(rawDependencies) ? rawDependencies.map(normalizeDependency) : []
  const dependencyIds = stringList(raw.dependency_ids ?? (Array.isArray(raw.dependencies) ? raw.dependencies.filter((item) => typeof item === 'string' || typeof item === 'number') : []))
  const attempts = Array.isArray(raw.attempts) ? raw.attempts.map(normalizeAttempt) : []
  return {
    ...base,
    kind: raw.kind == null ? (raw.title == null ? null : String(raw.title)) : String(raw.kind),
    instruction: raw.instruction == null ? null : String(raw.instruction),
    dependencies: dependencyStates,
    dependency_ids: dependencyIds.length ? dependencyIds : dependencyStates.map((item) => item.depends_on_task_id).filter((item): item is string => Boolean(item)),
    dependency_states: dependencyStates,
    attempts,
    execution_stage: raw.execution_stage == null ? (raw.stage == null ? null : String(raw.stage)) : String(raw.execution_stage),
    evidence_status: raw.evidence_status == null ? null : String(raw.evidence_status),
    evidence_readiness: raw.evidence_readiness == null ? null : String(raw.evidence_readiness),
    allowed_actions: normalizeActionList(raw.allowed_actions),
    explanation: isRecord(raw.explanation) ? raw.explanation : null,
    review_disposition: raw.review_disposition == null ? null : String(raw.review_disposition),
    revision_requests: stringList(raw.revision_requests),
    decision_disposition: raw.decision_disposition == null ? null : String(raw.decision_disposition),
  }
}

export function normalizeRun(value: unknown): RunSummary {
  return normalizeRunSummary(value)
}

export function normalizeRunDetail(value: unknown): RunDetail {
  const run = isRecord(value) ? value : {}
  const summary = normalizeRunSummary(run)
  const agentSelection = Array.isArray(run.agent_selection) ? run.agent_selection.filter(isRecord) : []
  const taskRows = unwrapItems<unknown>(run.tasks ?? run.task_graph).map(normalizeRunTask)
  const outputRows = unwrapItems<unknown>(run.outputs).map(normalizeOutput)
  const attemptRows = unwrapItems<unknown>(run.attempts).map(normalizeAttempt)
  const dependencyRows = unwrapItems<unknown>(run.dependencies ?? run.task_dependencies).map(normalizeDependency)
  const taskDependencies = taskRows.flatMap((task) => task.dependency_states ?? [])
  const taskAttempts = taskRows.flatMap((task) => task.attempts ?? [])
  const sourceRows = unwrapItems<unknown>(run.sources ?? run.source_links ?? run.source_records).map(normalizeSource)
  const taskOutputs = taskRows.flatMap((task) => task.outputs ?? (task.output ? [task.output] : []))
  const outputs = outputRows.length ? outputRows : taskOutputs
  const latestOutput = summary.latest_output_id
    ? outputs.find((output) => (output.id ?? output.output_id) === summary.latest_output_id) ?? null
    : [...outputs].sort((left, right) => (Number(right.version ?? 0) - Number(left.version ?? 0)) || String(right.created_at ?? '').localeCompare(String(left.created_at ?? '')))[0] ?? null
  const detailBrief = normalizeCioBrief(run.cio_brief ?? run.decision_brief ?? run.cio_summary ?? latestOutput?.decision_brief ?? summary.decision_brief)
  const candidateSimulations = Array.isArray(run.candidate_simulations)
    ? run.candidate_simulations.filter(isRecord)
    : []
  return {
    ...summary,
    tasks: taskRows,
    dependencies: dependencyRows.length ? dependencyRows : taskDependencies,
    outputs,
    attempts: attemptRows.length ? attemptRows : taskAttempts,
    latest_output: latestOutput ?? summary.latest_output ?? null,
    current_decision: isRecord(run.current_decision) ? run.current_decision : summary.current_decision ?? null,
    canonical_decision: isRecord(run.canonical_decision) ? run.canonical_decision : summary.canonical_decision ?? null,
    current_case_decision: isRecord(run.current_case_decision) ? run.current_case_decision : summary.current_case_decision ?? null,
    calculation_context: isRecord(run.calculation_context) ? run.calculation_context : summary.calculation_context ?? null,
    sources: sourceRows,
    source_links: sourceRows,
    source_ids: stringList(run.source_ids ?? run.input_source_ids),
    discovery_source_ids: stringList(run.discovery_source_ids),
    routing_plan: normalizeRoutingPlan(run.routing_plan),
    research_candidates: Array.isArray(run.research_candidates) ? run.research_candidates.map(normalizeResearchCandidate) : [],
    timeline: Array.isArray(run.timeline) ? run.timeline.filter(isRecord) : Array.isArray(run.events) ? run.events.filter(isRecord) : [],
    selected_agent_ids: summary.selected_agent_ids ?? [],
    skipped_agent_ids: summary.skipped_agent_ids ?? [],
    agent_selection: agentSelection.map((item) => ({ agent_id: item.agent_id == null ? undefined : String(item.agent_id), selected: Boolean(item.selected), state: item.state == null ? null : String(item.state), reason: item.reason == null ? null : String(item.reason) })),
    explanation: isRecord(run.explanation) ? run.explanation : null,
    decision_history: Array.isArray(run.decision_history) ? run.decision_history.filter(isRecord) : [],
    decision_brief: detailBrief,
    cio_brief: detailBrief,
    blocking_gaps: Array.isArray(run.blocking_gaps ?? run.gap_resolution_ledger ?? run.research_gap_ledger ?? run.research_gaps ?? run.evidence_gaps)
      ? (run.blocking_gaps ?? run.gap_resolution_ledger ?? run.research_gap_ledger ?? run.research_gaps ?? run.evidence_gaps).map(normalizeMissingGap)
      : summary.blocking_gaps ?? [],
    gap_resolution_ledger: Array.isArray(run.gap_resolution_ledger ?? run.research_gap_ledger ?? run.research_gaps ?? run.evidence_gaps ?? run.blocking_gaps)
      ? (run.gap_resolution_ledger ?? run.research_gap_ledger ?? run.research_gaps ?? run.evidence_gaps ?? run.blocking_gaps).map(normalizeMissingGap)
      : summary.gap_resolution_ledger ?? [],
    memory_context: normalizeMemoryContext(run.memory_context ?? run.memory) ?? summary.memory_context ?? null,
    candidate_simulations: candidateSimulations,
  }
}

export function normalizeAgent(value: unknown): AgentRecord {
  const agent = isRecord(value) ? value : {}
  const taskValue = agent.current_task == null ? agent.task : agent.current_task
  const task = taskValue == null ? null : normalizeTask(taskValue)
  const queue = Array.isArray(agent.queue) ? agent.queue.map(normalizeTask) : []
  const outputs = Array.isArray(agent.outputs) ? agent.outputs.map(normalizeOutput) : task?.outputs ?? (task?.output ? [task.output] : [])
  const evidence = Array.isArray(agent.evidence) ? agent.evidence.map(normalizeEvidence) : task?.evidence_refs ?? []
  const model = normalizeModel(agent.model)
  const status = normalizeStatus(agent.status ?? task?.status)
  const currentSimulation = agent.current_simulation == null ? null : normalizeSimulation(agent.current_simulation)
  const relatedSimulations = Array.isArray(agent.related_simulations) ? agent.related_simulations.map(normalizeSimulation) : []
  return {
    id: String(agent.id ?? agent.code ?? 'A00'),
    code: agent.code == null ? undefined : String(agent.code),
    name: String(agent.name ?? agent.title ?? 'Agent'),
    title: String(agent.title ?? agent.name ?? 'Agent'),
    kind: agent.id === 'A00' || agent.id === 'A10' || agent.id === 'A11' ? agent.id === 'A00' ? 'staff' : 'decision' : 'analyst',
    mandate: String(agent.mandate ?? ''),
    zone: readableLocation(agent.location ?? agent.zone),
    location: undefined,
    accent: undefined,
    status,
    paused: Boolean(agent.paused),
    queued_count: typeof agent.queued_count === 'number' ? agent.queued_count : queue.length,
    output_count: typeof agent.output_count === 'number' ? agent.output_count : outputs.length,
    model_source: agent.model_source == null ? undefined : String(agent.model_source),
    current_task: task,
    task,
    queue,
    outputs,
    evidence,
    history: Array.isArray(agent.history) ? agent.history as Array<Record<string, unknown>> : [],
    model,
    last_update: agent.last_update == null ? (task?.updated_at ?? null) : String(agent.last_update),
    current_simulation: currentSimulation,
    related_simulations: relatedSimulations,
    selected_for_run: typeof agent.selected_for_run === 'boolean' ? agent.selected_for_run : null,
    selection_state: agent.selection_state == null ? null : String(agent.selection_state),
    selection_reason: agent.selection_reason == null ? null : String(agent.selection_reason),
    selected_run_id: agent.selected_run_id == null ? null : String(agent.selected_run_id),
    latest_completed_task: agent.latest_completed_task == null ? null : normalizeTask(agent.latest_completed_task),
    latest_output: agent.latest_output == null ? null : normalizeOutput(agent.latest_output),
  }
}

export function normalizeOffice(value: unknown, namespace: Namespace): OfficeSnapshot {
  const office = isRecord(value) ? value : {}
  return {
    mode: namespace,
    namespace: String(office.namespace ?? namespace),
    agents: unwrapItems<unknown>(office.agents).map(normalizeAgent),
    tasks: unwrapItems<unknown>(office.tasks).map(normalizeTask),
    last_event_cursor: office.event_cursor ?? office.last_event_cursor ?? null,
    event_cursor: office.event_cursor ?? office.last_event_cursor ?? null,
    generated_at: office.generated_at == null ? null : String(office.generated_at),
    server_time: office.connection?.last_event_at == null ? null : String(office.connection.last_event_at),
    stale: office.connection?.status !== 'connected',
    paused: Boolean(office.paused),
    provider_state: undefined,
    portfolio: isRecord(office.portfolio_summary) ? { accounts: Array(office.portfolio_summary.accounts ?? 0).fill({}), positions: Array(office.portfolio_summary.positions ?? 0).fill({}) } : undefined,
  }
}

export function normalizeProviderState(value: unknown): ProviderState {
  const rows = unwrapItems<unknown>(value)
  const providers = rows.map((row) => {
    const item = isRecord(row) ? row : {}
    const models = Array.isArray(item.models) ? item.models.map((raw: unknown) => {
      const model = isRecord(raw) ? raw : {}
      return { provider: String(item.id ?? item.provider ?? ''), model: String(model.id ?? model.model ?? ''), label: model.name == null ? undefined : String(model.name), available: Boolean(model.available), reason: model.reason == null ? null : String(model.reason), reasoning_efforts: Array.isArray(model.reasoning_efforts) ? model.reasoning_efforts.map(String) : [], capabilities: isRecord(model.capabilities) ? model.capabilities : {} }
    }) : []
    return { provider: String(item.id ?? item.provider ?? ''), available: Boolean(item.available), status: String(item.status ?? (item.available ? 'available' : 'blocked')), auth_mode: item.auth_mode == null ? null : String(item.auth_mode), billing_route: item.billing_route == null ? null : String(item.billing_route), reason: item.reason == null ? null : String(item.reason), models, checked_at: item.checked_at == null ? null : String(item.checked_at) }
  })
  const first = providers[0] ?? { provider: 'codex', available: false, status: 'unknown', reason: 'Provider status unavailable', models: [] }
  return { ...first, providers }
}

export function normalizeSource(value: unknown): EvidenceRef {
  return normalizeEvidence(value)
}

export function normalizePortfolio(value: unknown): PortfolioSnapshot {
  const portfolio = isRecord(value) ? value : {}
  return { ...portfolio, observations: Array.isArray(portfolio.observations) ? portfolio.observations as PortfolioSnapshot['observations'] : [], missing_data: Array.isArray(portfolio.missing_data) ? portfolio.missing_data.map(String) : [] } as PortfolioSnapshot
}

export function normalizeCoverage(value: unknown): CoverageItem[] {
  return unwrapItems<unknown>(value).map((row, index) => {
    const item = isRecord(row) ? row : {}
    const id = String(item.id ?? item.symbol ?? `coverage-${index}`)
    const ticker = String(item.symbol ?? item.ticker ?? '—')
    const kind = item.kind == null ? (item.record_type == null ? null : String(item.record_type)) : String(item.kind)
    const coverageKey = `${id} ${ticker}`.toLowerCase()
    const isCoverageArea = coverageKey.includes('coverage:')
    const placeholder = Boolean(item.is_placeholder) || kind === 'placeholder' || kind === 'gap' || kind === 'coverage_area' || isCoverageArea
    const sector = item.sector == null ? null : String(item.sector)
    const fallbackName = isCoverageArea ? ticker.replace(/^coverage:/i, '').replace(/[_-]+/g, ' ').replace(/\s+/g, ' ').trim().replace(/\b\w/g, (letter) => letter.toUpperCase()) : null
    return { id, ticker, name: item.name == null ? (sector ?? fallbackName ?? undefined) : String(item.name), sector, kind: kind ?? (isCoverageArea ? 'coverage_area' : null), is_placeholder: placeholder, status: String(item.status ?? 'considered'), reason: item.reason == null ? null : String(item.reason), reopening_condition: item.reopen_when == null ? null : String(item.reopen_when), updated_at: item.updated_at == null ? null : String(item.updated_at), horizon: item.horizon == null ? null : String(item.horizon) }
  })
}

export function normalizeDecision(value: unknown): DecisionRecord {
  const item = isRecord(value) ? value : {}
  return {
    id: item.id == null ? undefined : String(item.id),
    run_id: item.run_id == null ? null : String(item.run_id),
    agent_id: item.agent_id == null ? null : String(item.agent_id),
    subject: item.subject == null ? (item.ticker == null ? undefined : String(item.ticker)) : String(item.subject),
    disposition: item.disposition == null ? undefined : String(item.disposition),
    rationale: item.rationale == null ? undefined : String(item.rationale),
    dissent: Array.isArray(item.dissent) ? item.dissent.join(' ') : item.dissent == null ? null : String(item.dissent),
    conditions: Array.isArray(item.conditions) ? item.conditions.map(String) : [],
    invalidation_conditions: Array.isArray(item.invalidation_conditions) ? item.invalidation_conditions.map(String) : [],
    risk_results: Array.isArray(item.risk_results) ? item.risk_results.filter(isRecord).map((risk) => ({ ...risk, check: risk.check == null ? undefined : String(risk.check), status: risk.status == null ? undefined : String(risk.status), detail: risk.detail == null ? undefined : String(risk.detail) })) : [],
    output_id: item.output_id == null ? null : String(item.output_id),
    supersedes_id: item.supersedes_id == null ? null : String(item.supersedes_id),
    created_at: item.created_at == null ? null : String(item.created_at),
    provenance: item.provenance == null ? (item.namespace == null ? undefined : String(item.namespace)) : String(item.provenance),
  }
}

export function normalizeMonitoring(value: unknown): MonitoringRule {
  const item = isRecord(value) ? value : {}
  const rawInterval = typeof item.interval_minutes === 'number' ? item.interval_minutes : item.interval_minutes == null ? null : Number(item.interval_minutes)
  return {
    id: String(item.id ?? 'monitoring-rule'),
    name: String(item.name ?? 'Monitoring rule'),
    enabled: Boolean(item.enabled),
    timezone: item.timezone == null ? null : String(item.timezone),
    interval_minutes: rawInterval != null && Number.isFinite(rawInterval) ? rawInterval : null,
    last_run_at: item.last_run_at == null ? null : String(item.last_run_at),
    next_run_at: item.next_run_at == null ? null : String(item.next_run_at),
    catch_up_policy: item.catch_up_policy == null ? null : String(item.catch_up_policy),
    condition: item.condition == null ? null : String(item.condition),
    source_ids: Array.isArray(item.source_ids) ? item.source_ids.map(String) : [],
    mode: item.mode == null ? 'interval_research' : String(item.mode),
  }
}

export function normalizeMemory(value: unknown): MemoryRecord {
  const item = isRecord(value) ? value : {}
  return { id: item.id == null ? undefined : String(item.id), type: item.kind == null ? (item.type == null ? undefined : String(item.type)) : String(item.kind), record_type: item.record_type == null ? (item.kind == null ? undefined : String(item.kind)) : String(item.record_type), title: item.title == null ? 'Untitled memory' : String(item.title), summary: item.excerpt == null ? (item.summary == null ? null : String(item.summary)) : String(item.excerpt), content: item.content == null ? null : String(item.content), provenance: item.namespace == null ? undefined : String(item.namespace), created_at: item.created_at == null ? null : String(item.created_at), updated_at: item.updated_at == null ? null : String(item.updated_at), version: typeof item.version === 'number' ? item.version : undefined, freshness: item.freshness == null ? null : String(item.freshness), reuse_reason: item.reuse_reason == null ? null : String(item.reuse_reason), observed_at: item.observed_at == null ? null : String(item.observed_at), retrieved_at: item.retrieved_at == null ? null : String(item.retrieved_at), stale: Boolean(item.stale), source_refs: Array.isArray(item.source_refs) ? item.source_refs.map(normalizeEvidence) : [], backlinks: Array.isArray(item.related_ids) ? item.related_ids.map((id: unknown) => ({ id: String(id), title: String(id), type: 'related' })) : [] }
}

export function normalizeRedditConnection(value: unknown): RedditConnectionState {
  const item = isRecord(value) && isRecord(value.connection) ? value.connection : isRecord(value) ? value : {}
  const rawWindow = item.window_days ?? item.days
  const parsedWindow = rawWindow == null ? 7 : Number(rawWindow)
  return {
    ...item,
    status: item.status == null ? (item.connected === true ? 'connected' : item.connected === false ? 'disconnected' : null) : String(item.status),
    connected: typeof item.connected === 'boolean' ? item.connected : item.status === 'connected' || item.status === 'ready',
    enabled: typeof item.enabled === 'boolean' ? item.enabled : (typeof item.connected === 'boolean' ? item.connected : null),
    subreddit: item.subreddit == null ? (item.community == null ? null : String(item.community)) : String(item.subreddit),
    window_days: Number.isFinite(parsedWindow) ? Math.max(1, Math.min(30, parsedWindow)) : 7,
    last_sync_at: item.last_sync_at == null ? (item.retrieved_at == null ? null : String(item.retrieved_at)) : String(item.last_sync_at),
    coverage_start: item.coverage_start == null ? (item.oldest_published_at == null ? null : String(item.oldest_published_at)) : String(item.coverage_start),
    coverage_end: item.coverage_end == null ? (item.newest_published_at == null ? null : String(item.newest_published_at)) : String(item.coverage_end),
    coverage_complete: typeof item.coverage_complete === 'boolean' ? item.coverage_complete : null,
    coverage_reason: item.coverage_reason == null ? null : String(item.coverage_reason),
    reason: item.reason == null ? (item.error == null ? null : String(item.error)) : String(item.reason),
    account_label: item.account_label == null ? null : String(item.account_label),
  }
}

export function normalizeRedditTriage(value: unknown): RedditTriage | null {
  if (!isRecord(value)) return null
  const rawClassification = String(value.classification ?? '').trim().toLowerCase().replaceAll('-', '_').replaceAll(' ', '_')
  if (!['thesis', 'yolo_ticker', 'skip'].includes(rawClassification)) return null
  const tickers = Array.isArray(value.tickers)
    ? [...new Set(value.tickers.map((ticker) => String(ticker ?? '').trim().toUpperCase()).filter(Boolean))]
    : []
  return {
    ...value,
    classification: rawClassification as RedditTriageClassification,
    reason: value.reason == null ? '' : String(value.reason),
    thesis_summary: value.thesis_summary == null ? '' : String(value.thesis_summary),
    evidence_excerpt: value.evidence_excerpt == null ? '' : String(value.evidence_excerpt),
    tickers,
  }
}

export function normalizeRedditPost(value: unknown): RedditPostRecord {
  const item = isRecord(value) ? value : {}
  const state = item.state ?? item.status ?? item.dispatch_state
  const sourceFlair = item.source_flair == null
    ? (item.flair == null ? (item.link_flair_text == null ? null : String(item.link_flair_text)) : String(item.flair))
    : String(item.source_flair)
  return {
    ...item,
    id: item.id == null ? (item.item_id == null ? (item.external_id == null ? undefined : String(item.external_id)) : String(item.item_id)) : String(item.id),
    post_id: item.post_id == null ? (item.external_id == null ? null : String(item.external_id)) : String(item.post_id),
    subreddit: item.subreddit == null ? (item.community == null ? null : String(item.community)) : String(item.subreddit),
    source_flair: sourceFlair,
    flair: sourceFlair,
    triage: normalizeRedditTriage(item.triage ?? item.reddit_triage),
    title: item.title == null ? 'Untitled Reddit intake' : String(item.title),
    body: item.body == null ? (item.text == null ? null : String(item.text)) : String(item.body),
    text: item.text == null ? (item.body == null ? null : String(item.body)) : String(item.text),
    author: item.author == null ? null : String(item.author),
    permalink: item.permalink == null ? (item.url == null ? null : String(item.url)) : String(item.permalink),
    url: item.url == null ? (item.permalink == null ? null : String(item.permalink)) : String(item.url),
    score: item.score == null ? null : Number(item.score),
    comments: item.comments == null ? (item.num_comments == null ? null : Number(item.num_comments)) : Number(item.comments),
    created_at: item.created_at == null ? (item.published_at == null ? null : String(item.published_at)) : String(item.created_at),
    ingested_at: item.ingested_at == null ? (item.retrieved_at == null ? null : String(item.retrieved_at)) : String(item.ingested_at),
    retained_at: item.retained_at == null ? null : String(item.retained_at),
    state: state == null ? null : String(state),
    status: item.status == null ? (state == null ? null : String(state)) : String(item.status),
    dispatch_state: item.dispatch_state == null ? null : String(item.dispatch_state),
    run_id: item.run_id == null ? null : String(item.run_id),
    reused_run_id: item.reused_run_id == null ? (item.reuse_run_id == null ? null : String(item.reuse_run_id)) : String(item.reused_run_id),
    reuse_reason: item.reuse_reason == null ? null : String(item.reuse_reason),
    source_id: item.source_id == null ? null : String(item.source_id),
    coverage_window: item.coverage_window == null ? null : String(item.coverage_window),
    coverage_start: item.coverage_start == null ? null : String(item.coverage_start),
    coverage_end: item.coverage_end == null ? null : String(item.coverage_end),
    backlog_reason: item.backlog_reason == null ? (item.reason == null ? null : String(item.reason)) : String(item.backlog_reason),
    reason: item.reason == null ? null : String(item.reason),
    dispatch_error: item.dispatch_error == null ? null : String(item.dispatch_error),
    error: item.error == null ? null : String(item.error),
  }
}

export function normalizeRedditInbox(value: unknown): RedditInboxSnapshot {
  const source = isRecord(value) ? value : {}
  const rawPosts = source.posts ?? source.items ?? source.intake_items ?? source.retained_posts
  const explicitAvailability = source.availability ?? source.fetch_status ?? source.intake_status
  const dispatches = Array.isArray(source.dispatches) ? source.dispatches.filter(isRecord) : []
  const latestDispatchByItem = new Map<string, Record<string, any>>()
  for (const dispatch of dispatches) {
    const itemId = dispatch.item_id ?? dispatch.intake_item_id ?? dispatch.post_id ?? dispatch.external_id
    if (itemId == null) continue
    const key = String(itemId)
    const previous = latestDispatchByItem.get(key)
    if (!previous || String(dispatch.updated_at ?? dispatch.created_at ?? '').localeCompare(String(previous.updated_at ?? previous.created_at ?? '')) >= 0) latestDispatchByItem.set(key, dispatch)
  }
  const posts = Array.isArray(rawPosts) ? rawPosts.map((raw) => {
    const post = normalizeRedditPost(raw)
    const keys = [post.id, post.post_id].filter((item): item is string => Boolean(item))
    const dispatch = keys.map((key) => latestDispatchByItem.get(key)).find(Boolean)
    if (!dispatch) return post
    const dispatchStatus = dispatch.status ?? dispatch.state ?? dispatch.dispatch_state
    return {
      ...post,
      dispatch_state: dispatchStatus == null ? post.dispatch_state : String(dispatchStatus),
      run_id: post.run_id ?? (dispatch.run_id == null ? null : String(dispatch.run_id)),
      reused_run_id: post.reused_run_id ?? (dispatch.reused_run_id == null ? (dispatch.reuse_run_id == null ? null : String(dispatch.reuse_run_id)) : String(dispatch.reused_run_id)),
      reuse_reason: post.reuse_reason ?? (dispatch.reuse_reason == null ? null : String(dispatch.reuse_reason)),
      dispatch_id: dispatch.id == null ? undefined : String(dispatch.id),
      dispatch_error: dispatch.error == null ? null : String(dispatch.error),
    }
  }) : []
  const cursor = isRecord(source.cursor) ? source.cursor : {}
  const counts = isRecord(source.counts) ? source.counts : isRecord(source.status_counts) ? source.status_counts : {}
  const countKeys = Object.keys(counts)
  const countValue = (key: string) => {
    const parsed = Number(counts[key] ?? 0)
    return Number.isFinite(parsed) ? Math.max(0, parsed) : 0
  }
  const countTotal = countKeys.reduce((sum, key) => sum + countValue(key), 0)
  const hasCounts = countKeys.length > 0
  const backlogFallback = hasCounts
    ? countValue('queued') + countValue('blocked') + countValue('failed')
    : posts.filter((post) => ['queued', 'blocked', 'backlog', 'failed'].includes(String(post.status ?? post.state))).length
  const researchedFallback = hasCounts
    ? countValue('processed') + countValue('reused') + countValue('researched')
    : posts.filter((post) => ['processed', 'reused', 'researched'].includes(String(post.status ?? post.state))).length
  const skippedFallback = hasCounts
    ? countValue('dismissed') + countValue('skipped')
    : posts.filter((post) => String(post.status ?? post.state) === 'dismissed' || post.triage?.classification === 'skip').length
  const connection = source.connection == null ? null : normalizeRedditConnection(source.connection)
  const normalizedAvailability = explicitAvailability == null
    ? (Array.isArray(rawPosts) ? (posts.length ? 'available' : 'empty') : Object.keys(source).length ? 'available' : 'not_checked')
    : String(explicitAvailability).toLowerCase().replaceAll('-', '_').replaceAll(' ', '_')
  const availability = normalizedAvailability === 'ok' || normalizedAvailability === 'ready' || normalizedAvailability === 'success'
    ? (posts.length ? 'available' : 'empty')
    : normalizedAvailability
  const numeric = (candidate: unknown, fallback: number) => {
    const parsed = typeof candidate === 'number' ? candidate : candidate == null ? Number.NaN : Number(candidate)
    return Number.isFinite(parsed) ? Math.max(0, parsed) : fallback
  }
  const retainedCount = numeric(source.retained_count ?? source.total ?? source.total_count, hasCounts ? countTotal : posts.length)
  const researchedCount = numeric(source.researched_count ?? source.processed_count ?? source.research_count, researchedFallback)
  const skippedCount = numeric(source.skipped_count ?? source.dismissed_count, skippedFallback)
  const parallelLimit = source.parallel_limit ?? source.max_parallel ?? source.dispatch_limit
  const activePostCount = source.active_post_count ?? source.active_count ?? source.processing_count
  const availableSlots = source.available_slots ?? source.remaining_slots
  return {
    ...source,
    posts,
    connection,
    status: source.filter_status == null
      ? (source.status_filter == null
        ? (source.requested_status == null
          ? (['processed', 'processing', 'queued', 'dismissed', 'blocked', 'failed', 'attention'].includes(String(source.status ?? '').toLowerCase()) ? String(source.status) : null)
          : String(source.requested_status))
        : String(source.status_filter))
      : String(source.filter_status),
    filter_status: source.filter_status == null
      ? (source.status_filter == null ? (source.requested_status == null ? null : String(source.requested_status)) : String(source.status_filter))
      : String(source.filter_status),
    availability,
    error_message: source.error_message == null ? (source.error == null ? null : String(source.error)) : String(source.error_message),
    has_more: typeof source.has_more === 'boolean' ? source.has_more : typeof source.next_cursor === 'string' ? Boolean(source.next_cursor) : false,
    next_offset: source.next_offset == null ? (source.offset == null ? null : Number(source.offset) + posts.length) : Number(source.next_offset),
    offset: source.offset == null ? 0 : Number(source.offset),
    page_size: source.page_size == null ? posts.length : Number(source.page_size),
    coverage_start: source.coverage_start == null ? (connection?.coverage_start ?? (cursor.oldest_published_at == null ? null : String(cursor.oldest_published_at))) : String(source.coverage_start),
    coverage_end: source.coverage_end == null ? (connection?.coverage_end ?? (cursor.newest_published_at == null ? null : String(cursor.newest_published_at))) : String(source.coverage_end),
    intake_start: source.intake_start == null ? (source.last_poll_at == null ? null : String(source.last_poll_at)) : String(source.intake_start),
    intake_end: source.intake_end == null ? null : String(source.intake_end),
    total: numeric(source.total ?? source.total_count, retainedCount),
    filtered_total: source.filtered_total == null
      ? (source.filtered_count == null ? undefined : numeric(source.filtered_count, posts.length))
      : numeric(source.filtered_total, posts.length),
    counts: countKeys.reduce<Record<string, number>>((normalized, key) => { normalized[key] = countValue(key); return normalized }, {}),
    backlog_count: numeric(source.backlog_count ?? source.queued_count, backlogFallback),
    retained_count: retainedCount,
    researched_count: researchedCount,
    skipped_count: skippedCount,
    parallel_limit: parallelLimit == null ? null : numeric(parallelLimit, 0),
    active_post_count: activePostCount == null ? null : numeric(activePostCount, 0),
    available_slots: availableSlots == null ? null : numeric(availableSlots, 0),
    // Keep the legacy fields available to older consumers, but derive them
    // from the status counts so dismissed items are never presented as
    // linked research.
    covered_count: numeric(source.covered_count, researchedCount),
    missing_count: numeric(source.missing_count, Math.max(0, retainedCount - backlogFallback - researchedCount - skippedCount)),
  }
}

export function normalizeSimulation(value: unknown): SimulationRun {
  const item = isRecord(value) ? value : {}
  const inputs = isRecord(item.inputs) ? item.inputs : {}
  const detailRounds = Array.isArray(item.rounds) ? item.rounds.filter(isRecord) : []
  const detailParticipants = Array.isArray(item.participants) ? item.participants.filter(isRecord) : []
  const sourceRows = Array.isArray(inputs.sources) ? inputs.sources.map(normalizeEvidence) : []
  return {
    id: item.id == null ? (item.simulation_id == null ? undefined : String(item.simulation_id)) : String(item.id),
    simulation_id: item.simulation_id == null ? (item.id == null ? undefined : String(item.id)) : String(item.simulation_id),
    namespace: item.namespace == null ? 'simulation' : String(item.namespace),
    status: normalizeStatus(item.status),
    name: item.title == null ? (item.name == null ? 'Scenario run' : String(item.name)) : String(item.title),
    scenario: item.question == null ? (item.scenario == null ? undefined : String(item.scenario)) : String(item.question),
    question: item.question == null ? null : String(item.question),
    seed: typeof item.seed === 'number' ? item.seed : typeof inputs.seed === 'number' ? inputs.seed : undefined,
    rounds: typeof item.rounds === 'number' ? item.rounds : detailRounds.length ? detailRounds.length : typeof inputs.rounds === 'number' ? inputs.rounds : typeof inputs.round_count === 'number' ? inputs.round_count : undefined,
    participants: typeof item.participants === 'number' ? item.participants : detailParticipants.length ? detailParticipants.length : typeof inputs.participants === 'number' ? inputs.participants : undefined,
    horizon: item.horizon == null ? (inputs.horizon == null ? undefined : String(inputs.horizon)) : String(item.horizon),
    shock: item.shock_percent == null ? (inputs.shock_percent == null ? undefined : String(inputs.shock_percent)) : String(item.shock_percent),
    assumptions: Array.isArray(item.assumptions) ? item.assumptions.map(String) : [],
    limits: Array.isArray(item.limits) ? item.limits.map(String) : [],
    inputs: Object.keys(inputs).length ? inputs : null,
    participants_detail: detailParticipants,
    rounds_detail: detailRounds,
    summary: item.summary == null ? null : String(item.summary),
    model: item.model == null ? null : normalizeModel(item.model),
    recorded_responses: Array.isArray(item.recorded_responses) ? item.recorded_responses.filter(isRecord) : [],
    rules_only_baseline: isRecord(item.rules_only_baseline) ? item.rules_only_baseline : null,
    failure: isRecord(item.failure) ? item.failure : null,
    blocking_reason: item.blocking_reason == null ? null : String(item.blocking_reason),
    provider_status: item.provider_status == null ? null : String(item.provider_status),
    versions: isRecord(item.versions) ? item.versions : null,
    source_snapshots: sourceRows,
    output: isRecord(item.output) ? item.output : null,
    created_at: item.created_at == null ? null : String(item.created_at),
    events: detailRounds,
  }
}

export function eventFromSse(data: string): TaskEvent | null {
  try {
    const value: unknown = JSON.parse(data)
    return isRecord(value) ? value as TaskEvent : null
  } catch { return null }
}

export async function getOffice(namespace: Namespace, runId?: string | null, signal?: AbortSignal) {
  const query = new URLSearchParams({ namespace })
  if (runId) query.set('run_id', runId)
  const value = await apiFetch<unknown>(`/api/office?${query.toString()}`, { signal }, { signal })
  return normalizeOffice(value, namespace)
}

export async function getRuns(namespace: Namespace, signal?: AbortSignal): Promise<RunSummary[]> {
  const value = await apiFetch<unknown>(`/api/runs?namespace=${encodeURIComponent(namespace)}&include_intake=true`, { signal }, { signal })
  return unwrapItems<unknown>(value).map(normalizeRun)
}

export async function getRun(runId: string, namespace?: Namespace | string | null, signal?: AbortSignal): Promise<RunDetail> {
  const query = `?view=reader${namespace ? `&namespace=${encodeURIComponent(namespace)}` : ''}`
  return normalizeRunDetail(await apiFetch<unknown>(`/api/runs/${encodeURIComponent(runId)}${query}`, { signal }, { signal }))
}

export interface CreateRunRequest {
  question: string
  namespace: 'real' | 'demo'
  horizon?: string | null
  ticker?: string | null
  source_ids?: string[]
  model_override?: Record<string, unknown> | null
  idempotency_key: string
}

export async function createRun(request: CreateRunRequest) {
  return apiFetch<{ run_id: string; reused?: boolean; tasks?: TaskRecord[] }>(
    '/api/runs',
    { method: 'POST', body: JSON.stringify(request) },
    { mutation: true },
  )
}

export async function explainRun(runId: string, message: string, outputId?: string | null) {
  const payload: { message: string; idempotency_key: string; output_id?: string } = {
    message: message.trim(),
    idempotency_key: `explain-${runId}-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`,
  }
  if (outputId) payload.output_id = outputId
  const value = await apiFetch<unknown>(`/api/runs/${encodeURIComponent(runId)}/messages`, { method: 'POST', body: JSON.stringify(payload) }, { mutation: true })
  const raw = isRecord(value) ? value : {}
  return { ...raw, task_id: raw.task_id == null ? (raw.id == null ? (raw.event_id == null ? null : String(raw.event_id)) : String(raw.id)) : String(raw.task_id), run_id: raw.run_id == null ? runId : String(raw.run_id) }
}

export async function researchMissingEvidence(runId: string, instruction: string, options?: { source_ids?: string[]; model_override?: Record<string, unknown> | null }) {
  const payload: { instruction: string; idempotency_key: string; source_ids?: string[]; model_override?: Record<string, unknown> | null } = {
    instruction: instruction.trim(),
    idempotency_key: `research-${runId}-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`,
  }
  if (options?.source_ids?.length) payload.source_ids = options.source_ids
  if (options?.model_override) payload.model_override = options.model_override
  const value = await apiFetch<unknown>(`/api/runs/${encodeURIComponent(runId)}/research`, { method: 'POST', body: JSON.stringify(payload) }, { mutation: true })
  const raw = isRecord(value) ? value : {}
  return { ...raw, linked_run_id: raw.linked_run_id == null ? (raw.run_id == null ? null : String(raw.run_id)) : String(raw.linked_run_id), task_id: raw.task_id == null ? null : String(raw.task_id) }
}

export async function getAgent(id: string, namespace: Namespace, runId?: string | null, signal?: AbortSignal) {
  const query = new URLSearchParams({ namespace })
  if (runId) query.set('run_id', runId)
  const value = await apiFetch<any>(`/api/agents/${encodeURIComponent(id)}?${query.toString()}`, { signal }, { signal })
  const rawAgent = isRecord(value?.agent) ? { ...value.agent } : isRecord(value) ? { ...value } : {}
  if (rawAgent.current_simulation == null && isRecord(value) && value.current_simulation != null) rawAgent.current_simulation = value.current_simulation
  if (rawAgent.related_simulations == null && isRecord(value) && value.related_simulations != null) rawAgent.related_simulations = value.related_simulations
  const agent = normalizeAgent(rawAgent)
  return { agent, queue: unwrapItems<unknown>(value?.queue).map(normalizeTask), outputs: unwrapItems<unknown>(value?.outputs).map(normalizeOutput), evidence: unwrapItems<unknown>(value?.evidence).map(normalizeSource), history: unwrapItems<unknown>(value?.history) }
}

export async function getProviders() {
  return normalizeProviderState(await apiFetch<unknown>('/api/providers'))
}

export async function getModelPolicy() {
  return await apiFetch<any>('/api/model-policy')
}

export async function getPortfolio(namespace: Namespace) {
  return normalizePortfolio(await apiFetch<unknown>(`/api/portfolio?namespace=${encodeURIComponent(namespace)}`))
}

/** Portfolio limits are user-owned planning policy. The backend decides
 * whether a saved policy is proposed or approved; the UI only presents that
 * explicit state and never treats a proposed policy as a funded allocation. */
export async function getPortfolioPolicy(namespace: 'real' | 'demo' = 'real') {
  return await apiFetch<unknown>(`/api/portfolio-policy?namespace=${encodeURIComponent(namespace)}`)
}

export async function savePortfolioPolicy(input: Record<string, unknown>) {
  return await apiFetch<unknown>('/api/portfolio-policy', { method: 'PUT', body: JSON.stringify(input) }, { mutation: true })
}

export async function getCoverage(namespace: Namespace) {
  return normalizeCoverage(await apiFetch<unknown>(`/api/coverage?namespace=${encodeURIComponent(namespace)}`))
}

export async function getDecisions(namespace: Namespace) {
  return unwrapItems<unknown>(await apiFetch<unknown>(`/api/case-decisions?namespace=${encodeURIComponent(namespace)}`)).map(normalizeDecision)
}

function normalizeWatchlistItem(value: unknown): WatchlistItem | null {
  const item = isRecord(value) ? value : {}
  const candidate = isRecord(item.candidate) ? item.candidate : null
  const runId = item.run_id ?? item.id ?? candidate?.run_id
  if (runId == null || String(runId).trim() === '') return null
  const revision = item.decision_revision == null ? null : Number(item.decision_revision)
  return {
    ...item,
    run_id: String(runId),
    title: item.title == null ? (item.question == null ? null : String(item.question)) : String(item.title),
    decision_revision: revision != null && Number.isFinite(revision) ? revision : null,
    candidate,
    checks: Array.isArray(item.checks) ? item.checks.filter(isRecord) : [],
    trigger_state: item.trigger_state == null ? (item.state == null ? null : String(item.state)) : String(item.trigger_state),
    lifecycle_state: item.lifecycle_state == null ? (item.state == null ? (item.status == null ? null : String(item.status)) : String(item.state)) : String(item.lifecycle_state),
    review_due_at: item.review_due_at == null ? null : String(item.review_due_at),
    overdue: Boolean(item.overdue),
    last_checked_at: item.last_checked_at == null ? null : String(item.last_checked_at),
    next_action: item.next_action == null ? null : String(item.next_action),
    paused: Boolean(item.paused),
    candidate_key: item.candidate_key == null ? null : String(item.candidate_key),
    current_quote: isRecord(item.current_quote) ? item.current_quote : null,
    distance_to_entry: isRecord(item.distance_to_entry) ? item.distance_to_entry : null,
    learning: isRecord(item.learning) ? item.learning : null,
  }
}

export async function getWatchlist(namespace: 'real' | 'demo' = 'real') {
  const value = await apiFetch<unknown>(`/api/watchlist?namespace=${encodeURIComponent(namespace)}`)
  const raw = isRecord(value) ? value : {}
  const items = unwrapItems<unknown>(value).map(normalizeWatchlistItem).filter((item): item is WatchlistItem => Boolean(item))
  return {
    items,
    paused: Boolean(raw.paused ?? raw.firm_paused ?? raw.office_paused),
    enabled: raw.enabled == null ? null : Boolean(raw.enabled),
  }
}

/** Read-only frozen decision baselines and append-only paper observations. */
function normalizeLearningOutcome(value: unknown): LearningOutcome {
  const raw = isRecord(value) ? value : {}
  return {
    ...raw,
    id: raw.id == null ? undefined : String(raw.id),
    status: raw.status == null ? null : String(raw.status),
    kind: raw.kind == null ? null : String(raw.kind),
    evaluation_at: raw.evaluation_at == null ? null : String(raw.evaluation_at),
    observed_at: raw.observed_at == null ? null : String(raw.observed_at),
    maturity: raw.maturity == null ? null : String(raw.maturity),
    instrument: isRecord(raw.instrument) ? raw.instrument : undefined,
    benchmark: isRecord(raw.benchmark) ? raw.benchmark : undefined,
    benchmark_comparable: typeof raw.benchmark_comparable === 'boolean' ? raw.benchmark_comparable : null,
    excess_return: raw.excess_return == null ? null : String(raw.excess_return),
    drawdown: raw.drawdown == null ? null : String(raw.drawdown),
    thesis_result: raw.thesis_result == null ? null : String(raw.thesis_result),
    catalyst_result: raw.catalyst_result == null ? null : String(raw.catalyst_result),
    missed_opportunity: Boolean(raw.missed_opportunity),
    limitations: stringList(raw.limitations),
  }
}

function normalizeLearningBaseline(value: unknown): LearningBaseline {
  const raw = isRecord(value) ? value : {}
  return {
    ...raw,
    id: raw.id == null ? undefined : String(raw.id),
    run_id: raw.run_id == null ? undefined : String(raw.run_id),
    decision_revision: raw.decision_revision == null ? null : Number(raw.decision_revision),
    candidate_key: raw.candidate_key == null ? undefined : String(raw.candidate_key),
    ticker: raw.ticker == null ? undefined : String(raw.ticker),
    direction: raw.direction == null ? undefined : String(raw.direction),
    outcome: raw.outcome == null ? null : String(raw.outcome),
    decision_as_of: raw.decision_as_of == null ? null : String(raw.decision_as_of),
    review_at: raw.review_at == null ? null : String(raw.review_at),
    benchmark_ticker: raw.benchmark_ticker == null ? null : String(raw.benchmark_ticker),
    benchmark_rationale: raw.benchmark_rationale == null ? null : String(raw.benchmark_rationale),
    retrospective: raw.retrospective !== false,
    observations: Array.isArray(raw.observations) ? raw.observations.map(normalizeLearningOutcome) : [],
  }
}

export function normalizeLearning(value: unknown): LearningSnapshot {
  const raw = isRecord(value) ? value : {}
  return {
    ...raw,
    items: unwrapItems<unknown>(raw.items ?? raw.baselines).map(normalizeLearningBaseline),
    summary: isRecord(raw.summary) ? raw.summary : {},
    method: raw.method == null ? null : String(raw.method),
  }
}

export async function getLearning(namespace: 'real' | 'demo' = 'real') {
  return normalizeLearning(await apiFetch<unknown>(`/api/learning?namespace=${encodeURIComponent(namespace)}`))
}

export async function recordLifecycle(runId: string, input: { namespace: 'real' | 'demo'; candidate_key: string; state: 'held' | 'closed' | 'watchlist'; reason: string; confirmed: boolean; idempotency_key: string }) {
  return apiFetch<unknown>(`/api/lifecycle/${encodeURIComponent(runId)}`, { method: 'POST', body: JSON.stringify(input) }, { mutation: true })
}

export async function getMonitoring(namespace: 'real' | 'demo') {
  return unwrapItems<unknown>(await apiFetch<unknown>(`/api/monitoring?namespace=${encodeURIComponent(namespace)}`)).map(normalizeMonitoring)
}

export async function searchMemory(namespace: Namespace, q: string, kind = 'all') {
  return unwrapItems<unknown>(await apiFetch<unknown>(`/api/memory/search?namespace=${encodeURIComponent(namespace)}&q=${encodeURIComponent(q)}&kind=${encodeURIComponent(kind)}`)).map(normalizeMemory)
}

export async function getSimulations() {
  return unwrapItems<unknown>(await apiFetch<unknown>('/api/simulations')).map(normalizeSimulation)
}

export async function getSimulation(id: string) {
  return normalizeSimulation(await apiFetch<unknown>(`/api/simulations/${encodeURIComponent(id)}`))
}

export async function getSources(namespace: Namespace) {
  return unwrapItems<unknown>(await apiFetch<unknown>(`/api/sources?namespace=${encodeURIComponent(namespace)}`)).map(normalizeSource)
}

export async function getSource(id: string, namespace?: Namespace) {
  const query = namespace ? `?namespace=${encodeURIComponent(namespace)}` : ''
  return normalizeSource(await apiFetch<unknown>(`/api/sources/${encodeURIComponent(id)}${query}`))
}

export async function getSourceVersions(id: string, namespace?: Namespace) {
  const query = namespace ? `?namespace=${encodeURIComponent(namespace)}` : ''
  return unwrapItems<unknown>(await apiFetch<unknown>(`/api/sources/${encodeURIComponent(id)}/versions${query}`)).map(normalizeSource)
}

export function sourceDocumentUrl(id: string, namespace: Namespace = 'real') {
  return `/api/sources/${encodeURIComponent(id)}/document?namespace=${encodeURIComponent(namespace)}`
}

export async function getOutput(id: string, namespace?: Namespace) {
  const query = namespace ? `?namespace=${encodeURIComponent(namespace)}` : ''
  const value = await apiFetch<any>(`/api/outputs/${encodeURIComponent(id)}${query}`)
  const output = normalizeOutput(value?.output ?? value)
  const sources = unwrapItems<unknown>(value?.sources).map(normalizeSource)
  if (sources.length) output.source_refs = sources
  return output
}

/** Reddit intake is an optional connector. Keep the browser contract limited
 * to retained post metadata and never accept credentials in this payload. */
export async function getRedditInbox(namespace: 'real' | 'demo' = 'real', options?: RedditInboxQuery) {
  const limit = Math.max(1, Math.min(500, Math.floor(options?.limit ?? 100)))
  const offset = Math.max(0, Math.floor(options?.offset ?? 0))
  const requestedStatus = options?.status && options.status !== 'all' ? String(options.status).trim().toLowerCase() : ''
  const statusQuery = requestedStatus ? `&status=${encodeURIComponent(requestedStatus)}` : ''
  const query = `namespace=${encodeURIComponent(namespace)}&limit=${limit}&offset=${offset}${statusQuery}`
  const paths = [`/api/reddit/inbox?${query}`, `/api/intake?origin=reddit&${query}`]
  let lastError: unknown = null
  for (const path of paths) {
    try { return normalizeRedditInbox(await apiFetch<unknown>(path)) } catch (error) { lastError = error }
  }
  if (lastError) {
    const message = lastError instanceof Error ? lastError.message : 'The Reddit intake route is unavailable.'
    // Keep the read model explicit. An unavailable route must not look like a
    // successful empty seven-day listing in the inbox.
    return normalizeRedditInbox({ availability: 'unavailable', error_message: message, reason: message })
  }
  return normalizeRedditInbox(null)
}

export async function getRedditConnection(namespace: 'real' | 'demo' = 'real') {
  const query = `namespace=${encodeURIComponent(namespace)}`
  const paths = [`/api/reddit/connection?${query}`, `/api/reddit/settings?${query}`]
  let lastError: unknown = null
  for (const path of paths) {
    try { return normalizeRedditConnection(await apiFetch<unknown>(path)) } catch (error) { lastError = error }
  }
  if (lastError) throw lastError
  return normalizeRedditConnection(null)
}

export async function dispatchRedditPost(id: string, namespace: 'real' | 'demo' = 'real') {
  const payload = JSON.stringify({ namespace, idempotency_key: `reddit-dispatch-${id}` })
  const paths = [`/api/reddit/inbox/${encodeURIComponent(id)}/dispatch`, `/api/intake/${encodeURIComponent(id)}/dispatch`]
  let lastError: unknown = null
  for (const path of paths) {
    try { return await apiFetch<unknown>(path, { method: 'POST', body: payload }, { mutation: true }) } catch (error) { lastError = error }
  }
  throw lastError instanceof Error ? lastError : new Error('Reddit item could not be dispatched.')
}

export async function reuseRedditPost(id: string, namespace: 'real' | 'demo' = 'real') {
  const payload = JSON.stringify({ namespace, idempotency_key: `reddit-reuse-${id}` })
  const paths = [`/api/reddit/inbox/${encodeURIComponent(id)}/reuse`, `/api/intake/${encodeURIComponent(id)}/reuse`]
  let lastError: unknown = null
  for (const path of paths) {
    try { return await apiFetch<unknown>(path, { method: 'POST', body: payload }, { mutation: true }) } catch (error) { lastError = error }
  }
  throw lastError instanceof Error ? lastError : new Error('Reddit item could not be reused.')
}

export async function saveRedditConnection(input: { namespace: 'real' | 'demo'; subreddit: string; window_days: number; enabled: boolean }) {
  const payload = JSON.stringify(input)
  const paths = ['/api/reddit/connection', '/api/reddit/settings']
  let lastError: unknown = null
  for (const path of paths) {
    try { return normalizeRedditConnection(await apiFetch<unknown>(path, { method: 'PUT', body: payload }, { mutation: true })) } catch (error) { lastError = error }
  }
  throw lastError instanceof Error ? lastError : new Error('Reddit connection settings could not be saved.')
}
