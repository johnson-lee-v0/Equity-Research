import { lazy, Suspense, useEffect, useState } from 'react'
import type { ActionPlan, EvidenceRef, LearningSnapshot, Namespace, OfficeSnapshot, RedditConnectionState, RedditInboxSnapshot, RedditPostRecord, RunDetail, RunSummary, RunTaskRecord, OutputRecord, CioBrief, CioPricePlan, ValuationBlock, MissingGap, WatchlistItem } from '../types'
import { Icon } from '../components/Icon'
import PriceTargetCard from '../components/PriceTargetCard'
import InvestmentProcess from '../components/InvestmentProcess'
import { earningsReviewForRun, selectedResearchTicker } from '../components/investmentProcessModel'
import { PrivateFinancialDisclosure } from '../components/PrivateFinancialDisclosure'
import { ScenarioChart } from '../components/ScenarioChart'
import { statusLabel } from '../components/StatusBadge'
import { hasPrivateFinancialData, hasPrivateFinancialText, isPrivateFinancialKey } from '../components/financialPrivacy'
import { formatCalendarDate, formatDateTime } from '../date'
import './decision-workspace.css'
import ResearchActions from '../components/ResearchActions'
import InterimEvents from '../components/InterimEvents'
import type { RunDetailLoadState } from '../runDetailRequest'
const EarningsWorkflow = lazy(() => import('./research/EarningsWorkflow'))

/**
 * The decision page deliberately reads a small projection of a run. The
 * backend can add fields to the canonical decision object without forcing
 * this view to understand every historical output shape.
 */
type RecordValue = Record<string, unknown>
type DecisionOutcome = 'recommend' | 'watchlist' | 'decline' | 'mixed' | 'awaiting_info' | 'failed' | 'pending' | 'unknown'
type DecisionCitation = { id: string; title: string; locator?: string | null; asOf?: string | null }
type DecisionEvidenceRecord = { statement: string; kind: string; validationStatus: string | null; semanticStatus: string | null; textMatch: boolean | null; bindingChecks: Array<{ key?: string; status?: string; reason?: string }>; sourceVersion: string | null; freshness: string | null; asOf: string | null; unknownReason: string | null; citations: DecisionCitation[] }
type KeyQuestionKey = 'opportunity' | 'valuation' | 'catalyst' | 'downside' | 'portfolio_action'
type QuestionEvidenceStatus = 'complete' | 'partial' | 'unavailable'
type VerifiedQuestionFact = { factId: string | null; role: 'support' | 'contradiction'; claim: string | null; value: string | null; unit: string | null; period: string | null; sourceRef: string | null; locator: string | null; semanticStatus: string | null; freshness: string | null }
type KeyQuestion = { key: KeyQuestionKey; question: string; answer: string | null; decisionImplication: string | null; evidenceStatus: QuestionEvidenceStatus; verifiedFacts: VerifiedQuestionFact[]; unknowns: string[] }
type JointOutcome = 'recommend' | 'watchlist' | 'decline' | 'needs_evidence' | null
type JointReview = { status: 'not_run' | 'agreed' | 'resolved' | 'pending' | 'unavailable'; layaModel: string | null; layaRevision: string | null; layaOutcome: JointOutcome; astraOutcome: JointOutcome; resolution: string | null; disagreements: Array<{ questionKey: KeyQuestionKey | null; reason: string }>; reviewedAt: string | null; layaClassificationConfidence: string | null; astraClassificationConfidence: string | null }

const KEY_QUESTION_ORDER: KeyQuestionKey[] = ['opportunity', 'valuation', 'catalyst', 'downside', 'portfolio_action']
const KEY_QUESTION_PROMPTS: Record<KeyQuestionKey, string> = {
  opportunity: 'What is the opportunity, and what is the market missing?',
  valuation: 'What is it worth versus the entry price and alternatives?',
  catalyst: 'What can change the outcome, and by when?',
  downside: 'What would prove us wrong, and how could we lose money?',
  portfolio_action: 'What should we do now, and does it fit the portfolio?',
}

type CandidateDecision = {
  key: string
  instrument: string
  name: string | null
  outcome: DecisionOutcome
  reason: string | null
  entry: string | null
  entryCondition: string | null
  quantity: string | null
  quantityLabel: string
  quantityIsValid: boolean
  notional: string | null
  currency: string | null
  target: string | null
  exitLogic: string | null
  horizon: string | null
  catalyst: string | null
  downside: string | null
  invalidation: string[]
  trigger: string | null
  triggerState: string | null
  expiry: string | null
  blockers: string[]
  materialBlockers: MissingGap[]
  unknowns: string[]
  rationale: string | null
  inputs: RecordValue | null
  citations: DecisionCitation[]
  evidence: DecisionEvidenceRecord[]
  sizingChecks: string[]
  sizingStatus: string | null
  supportingEvidence: string[]
  objections: string[]
  scenarioOutput: OutputRecord | null
  scenarioForecast: RecordValue | null
  direction: string | null
  strategy: string | null
  decisionAsOf: string | null
  benchmarkTicker: string | null
  benchmarkRationale: string | null
  variantView: string | null
  marketExpectation: string | null
  whyNow: string | null
  opposingExplanation: string | null
  disconfirmingEvidence: string[]
  decisionChangeConditions: string[]
  valuation: ValuationBlock | null
  futureTarget: CioPricePlan | null
  payoff: RecordValue | null
  portfolioContext: RecordValue | null
  actionPlan: ActionPlan | RecordValue | null
  recommendationGate: RecordValue | null
  researchContract: string | null
  keyQuestions: KeyQuestion[]
  jointReview: JointReview | null
  lifecycleState: string | null
  currentQuote: RecordValue | null
  distanceToEntry: RecordValue | null
  reviewDueAt: string | null
  overdue: boolean
  nextAction: string | null
  maximumPermittedShares: string | null
  recommendedShares: string | null
}

type CaseDecision = {
  candidates: CandidateDecision[]
  outcome: DecisionOutcome
  allocationMode: string | null
  stale: boolean
  reason: string | null
  asOf: string | null
  revision: string | null
  evidenceVersion: string | null
  rationale: string | null
  supportingEvidence: string[]
  objections: string[]
  inputs: RecordValue | null
  unknowns: string[]
  blockers: MissingGap[]
  recovery: string | null
  breakStage: string | null
  decisionHistory: unknown[]
  sourceCitations: DecisionCitation[]
  scenarioOutputs: OutputRecord[]
  failureReason: string | null
  retryAvailable: boolean
  retryTarget: string | null
  retryScope: 'task' | 'run' | null
  hasCanonicalDecision: boolean
  lifecycleState: string | null
}

export type DecisionWorkspaceMode = 'research' | 'questions' | 'results' | 'watchlist' | 'reddit'

export interface DecisionWorkspaceProps {
  mode: DecisionWorkspaceMode
  initialOriginFilter?: string
  namespace: Namespace
  runs: RunSummary[]
  selectedRun: RunDetail | null
  selectedRunId?: string | null
  detailLoad?: RunDetailLoadState
  redditInbox?: RedditInboxSnapshot
  redditConnection?: RedditConnectionState
  office?: OfficeSnapshot
  watchlistItems?: WatchlistItem[]
  watchlistPaused?: boolean
  watchlistEnabled?: boolean | null
  learning?: LearningSnapshot | null
  sources: EvidenceRef[]
  onSelectRun: (runId: string) => Promise<void> | void
  onClearRun: () => void
  onOpenSource: (source: EvidenceRef) => Promise<void> | void
  onOpenOutput?: (output: OutputRecord) => Promise<void> | void
  onControl?: (action: 'pause' | 'resume' | 'cancel' | 'retry', scope: 'firm' | 'run' | 'task', id: string | null) => void
  onRecordLifecycle?: (input: { run_id: string; candidate_key: string; state: 'held' | 'closed' | 'watchlist'; reason: string; confirmed: boolean }) => Promise<void> | void
  onNavigate: (destination: 'research' | 'questions' | 'results' | 'reddit' | 'watchlist' | 'office' | 'settings', options?: { preserveSelection?: boolean }) => void
  onDispatchReddit?: (id: string) => Promise<void> | void
  onReuseReddit?: (id: string) => Promise<void> | void
  onRefreshReddit?: () => Promise<void> | void
}

function record(value: unknown): RecordValue | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as RecordValue : null
}

function list(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
}

function firstNonEmptyList(...values: unknown[]): unknown[] {
  return values.find((value) => Array.isArray(value) && value.length > 0) as unknown[] | undefined ?? []
}

function text(value: unknown): string | null {
  if (value == null) return null
  if (typeof value === 'object') return null
  const next = String(value).trim()
  if (!next || ['unknown', 'n/a', 'na', 'not available', 'not recorded'].includes(next.toLowerCase())) return null
  return next
}

function strings(value: unknown): string[] {
  return Array.from(new Set(list(value).map((item) => text(item)).filter((item): item is string => Boolean(item))))
}

function firstText(...values: unknown[]): string | null {
  for (const value of values) {
    const next = text(value)
    if (next) return next
  }
  return null
}

function normalizeKey(value: unknown) {
  return String(value ?? '').toLowerCase().replaceAll('-', '_').replaceAll(' ', '_')
}

function questionKey(value: unknown): KeyQuestionKey | null {
  const key = normalizeKey(value)
  return KEY_QUESTION_ORDER.includes(key as KeyQuestionKey) ? key as KeyQuestionKey : null
}

function questionEvidenceStatus(value: unknown): QuestionEvidenceStatus {
  const status = normalizeKey(value)
  if (status === 'complete' || status === 'partial') return status
  return 'unavailable'
}

function normalizeKeyQuestion(value: unknown): KeyQuestion | null {
  const row = record(value)
  const key = questionKey(row?.key)
  if (!row || !key) return null
  const facts: VerifiedQuestionFact[] = list(row.verified_facts).map((item) => {
    const fact = record(item)
    const role = normalizeKey(fact?.role)
    if (!fact || (role !== 'support' && role !== 'contradiction')) return null
    return {
      factId: firstText(fact.fact_id, fact.id),
      role,
      claim: firstText(fact.claim, fact.statement),
      value: firstText(fact.value),
      unit: firstText(fact.unit),
      period: firstText(fact.period, fact.as_of),
      sourceRef: firstText(fact.source_ref, fact.source_id),
      locator: firstText(fact.locator),
      semanticStatus: firstText(fact.semantic_status, fact.semanticStatus),
      freshness: firstText(fact.freshness, fact.freshness_status),
    } as VerifiedQuestionFact
  }).filter((item): item is VerifiedQuestionFact => Boolean(item))
  // The canonical contract bounds each card at two supporting facts and one
  // contradiction. Keep the display bounded even if an older payload violates
  // that limit; unsupported rows are never turned into facts here.
  const boundedFacts = [
    ...facts.filter((fact) => fact.role === 'support').slice(0, 2),
    ...facts.filter((fact) => fact.role === 'contradiction').slice(0, 1),
  ]
  return {
    key,
    // Question wording is code-owned so a saved provider phrase cannot change
    // the five-question contract shown to the user.
    question: KEY_QUESTION_PROMPTS[key],
    answer: firstText(row.answer),
    decisionImplication: firstText(row.decision_implication, row.decisionImplication),
    evidenceStatus: questionEvidenceStatus(row.evidence_status ?? row.evidenceStatus),
    verifiedFacts: boundedFacts,
    unknowns: strings(row.unknowns).slice(0, 2),
  }
}

function normalizeKeyQuestions(value: unknown): KeyQuestion[] {
  const rows = list(value).map(normalizeKeyQuestion).filter((item): item is KeyQuestion => Boolean(item))
  const byKey = new Map(rows.map((item) => [item.key, item]))
  return KEY_QUESTION_ORDER.map((key) => byKey.get(key)).filter((item): item is KeyQuestion => Boolean(item))
}

function jointOutcome(value: unknown): JointOutcome {
  const outcome = normalizeKey(value)
  return outcome === 'recommend' || outcome === 'watchlist' || outcome === 'decline' || outcome === 'needs_evidence' ? outcome : null
}

function normalizeJointReview(value: unknown): JointReview | null {
  const row = record(value)
  if (!row) return null
  const rawStatus = normalizeKey(row.status)
  const status = ['not_run', 'agreed', 'resolved', 'pending', 'unavailable'].includes(rawStatus)
    ? rawStatus as JointReview['status']
    : 'unavailable'
  const disagreements = list(row.disagreements).map((item) => {
    const disagreement = record(item)
    const reason = firstText(disagreement?.reason)
    if (!reason) return null
    return { questionKey: questionKey(disagreement?.question_key ?? disagreement?.questionKey), reason }
  }).filter((item): item is { questionKey: KeyQuestionKey | null; reason: string } => Boolean(item))
  return {
    status,
    layaModel: firstText(row.laya_model),
    layaRevision: firstText(row.laya_revision),
    layaOutcome: jointOutcome(row.laya_outcome),
    astraOutcome: jointOutcome(row.astra_outcome),
    resolution: firstText(row.resolution),
    disagreements,
    reviewedAt: firstText(row.reviewed_at),
    layaClassificationConfidence: firstText(row.laya_classification_confidence, row.laya_confidence, row.classification_confidence),
    astraClassificationConfidence: firstText(row.astra_classification_confidence, row.astra_confidence),
  }
}

function hasFiveQuestionReview(candidate?: CandidateDecision | null) {
  return Boolean(candidate && normalizeKey(candidate.researchContract) === 'five_questions.v1' && candidate.keyQuestions.length === KEY_QUESTION_ORDER.length)
}

function questionTitle(key: KeyQuestionKey) {
  return key === 'portfolio_action' ? 'Portfolio action' : key.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())
}

function questionEvidenceLabel(value: QuestionEvidenceStatus) {
  return value === 'complete' ? 'Evidence complete' : value === 'partial' ? 'Evidence partial' : 'Evidence unavailable'
}

function jointStatusLabel(value: JointReview['status']) {
  if (value === 'not_run') return 'Not run'
  if (value === 'agreed') return 'Agreed'
  if (value === 'resolved') return 'Resolved'
  if (value === 'pending') return 'Pending review'
  return 'Unavailable'
}

function jointOutcomeLabel(value: JointOutcome) {
  if (value === 'needs_evidence') return 'Needs evidence'
  if (value) return value === 'recommend' ? 'Recommend' : value === 'watchlist' ? 'Watchlist' : 'Decline'
  return 'Unavailable'
}

function classificationConfidence(value: string | null) {
  if (!value) return null
  const raw = value.trim()
  const numeric = Number(raw.replace(/%$/, '').replaceAll(',', ''))
  if (!Number.isFinite(numeric) || Math.abs(numeric) > 100) return null
  if (raw.endsWith('%')) return `${numeric.toFixed(numeric % 1 === 0 ? 0 : 1)}%`
  if (Math.abs(numeric) <= 1) return `${(numeric * 100).toFixed(0)}%`
  return `${numeric.toFixed(numeric % 1 === 0 ? 0 : 1)}%`
}

function redditStateLabel(value: unknown) {
  const state = normalizeKey(value)
  if (['processed', 'reused', 'researched', 'completed'].includes(state)) return 'Result available'
  if (['processing', 'screening', 'dispatched'].includes(state)) return 'In progress'
  if (['queued', 'retained', 'backlog'].includes(state)) return 'Queued'
  if (['failed', 'error'].includes(state)) return 'Research failed'
  if (['blocked', 'attention'].includes(state)) return 'Needs attention'
  if (['dismissed', 'skipped'].includes(state)) return 'Skipped'
  return 'Awaiting screening'
}

function redditConnectionLabel(connection?: RedditConnectionState) {
  if (connection?.connected || connection?.status === 'connected' || connection?.status === 'ready') return 'Connected'
  const state = normalizeKey(connection?.status)
  if (state === 'configured_unchecked') return 'Credentials saved'
  if (state === 'missing_credentials' || state === 'disconnected') return 'Credentials needed'
  if (state === 'error' || state === 'unavailable') return 'Unavailable'
  if (state === 'connecting' || state === 'checking') return 'Checking'
  return 'Not checked'
}

function dateLabel(value: unknown) {
  return formatDateTime(value, 'Unknown time')
}

function compactDate(value: unknown) {
  return formatCalendarDate(value)
}

function evidenceStatusLabel(value: unknown): string | null {
  const next = firstText(value)
  if (!next) return null
  return statusLabel(normalizeKey(next))
}

function formatNumber(value: unknown, maximumFractionDigits = 6) {
  const next = text(value)
  if (!next) return null
  const number = Number(next.replaceAll(',', ''))
  if (!Number.isFinite(number)) return next
  return number.toLocaleString(undefined, { maximumFractionDigits })
}

function money(value: unknown, currency?: unknown) {
  const formatted = formatNumber(value)
  if (!formatted) return null
  const unit = firstText(currency) ?? ''
  return `${unit ? `${unit} ` : ''}${formatted}`
}

function planValue(value: unknown): { display: string | null; currency: string | null; sourceRefs: string[] } {
  const object = record(value)
  if (!object) return { display: text(value), currency: null, sourceRefs: [] }
  const currency = firstText(object.currency, object.currency_code)
  const low = money(object.low ?? object.lower ?? object.min ?? object.from, currency)
  const high = money(object.high ?? object.upper ?? object.max ?? object.to, currency)
  const exact = money(object.value ?? object.price ?? object.amount, currency)
  const display = low && high ? `${low}–${high}` : low ? `${low}+` : high ? `up to ${high}` : exact
  return { display, currency, sourceRefs: strings(object.source_refs ?? object.source_ids ?? object.citations) }
}

function outcome(value: unknown, hasTrigger = false, canonical = false): DecisionOutcome {
  const key = normalizeKey(value)
  if (!key && canonical) return 'pending'
  if (['recommend', 'recommended', 'enter', 'buy', 'approved', 'accept'].includes(key)) return 'recommend'
  if (['watchlist', 'watch_list', 'watch'].includes(key)) return 'watchlist'
  if (['mixed', 'multiple'].includes(key)) return 'mixed'
  // Legacy defer/wait is operational until a concrete trigger is recorded.
  // A v2 null outcome remains pending even when execution is awaiting input;
  // an operational state is never an investment judgment.
  if (['defer', 'deferred', 'wait', 'pending', 'awaiting_info', 'awaiting_input', 'needs_information', 'blocked'].includes(key)) return canonical ? 'pending' : hasTrigger ? 'watchlist' : 'awaiting_info'
  if (['decline', 'declined', 'reject', 'rejected', 'avoid', 'stay_out', 'stayout', 'skip'].includes(key)) return 'decline'
  if (['failed', 'error', 'errored'].includes(key)) return 'failed'
  return 'unknown'
}

function sourceId(value: unknown): string | null {
  const object = record(value)
  return firstText(object?.id, object?.source_id, object?.source_ref, object?.citation_id, typeof value === 'string' ? value : null)
}

function sourceCitation(value: unknown, inherited?: { locator?: string | null; asOf?: string | null }): DecisionCitation | null {
  const object = record(value)
  const id = sourceId(value)
  if (!id) return null
  const namedTitle = firstText(object?.title, object?.name, object?.label)
  return {
    id,
    title: namedTitle && namedTitle !== id ? namedTitle : 'Source record',
    locator: firstText(object?.locator, object?.selected_locator, object?.line_start == null ? null : `lines ${object.line_start}–${object.line_end ?? object.line_start}`, inherited?.locator),
    asOf: firstText(object?.as_of, object?.asOf, object?.period, inherited?.asOf),
  }
}

function citations(value: unknown): DecisionCitation[] {
  const collect = (item: unknown, inherited?: { locator?: string | null; asOf?: string | null }): DecisionCitation[] => {
    if (Array.isArray(item)) return item.flatMap((child) => collect(child, inherited))
    const object = record(item)
    if (!object) {
      const citation = sourceCitation(item, inherited)
      return citation ? [citation] : []
    }
    const ownLocator = firstText(object.locator, object.selected_locator, object.line_start == null ? null : `lines ${object.line_start}–${object.line_end ?? object.line_start}`, inherited?.locator)
    const ownAsOf = firstText(object.as_of, object.asOf, object.period, inherited?.asOf)
    const nested = object.source_refs ?? object.source_ids ?? object.sources
    if (Array.isArray(nested)) return nested.flatMap((child) => collect(child, { locator: ownLocator, asOf: ownAsOf }))
    if (nested != null) return collect(nested, { locator: ownLocator, asOf: ownAsOf })
    const citation = sourceCitation(object, { locator: ownLocator, asOf: ownAsOf })
    return citation ? [citation] : []
  }
  return Array.from(new Map(collect(value).map((item) => [`${item.id}|${item.locator ?? ''}|${item.asOf ?? ''}`, item])).values())
}

function enrichCitations(refs: DecisionCitation[], sources: EvidenceRef[]): DecisionCitation[] {
  const byId = new Map(sources.map((source) => [String(source.id ?? source.source_ref ?? ''), source]))
  return refs.map((citation) => {
    const source = byId.get(citation.id)
    if (!source) return citation
    return {
      ...citation,
      title: citation.title === citation.id || citation.title === 'Source record'
        ? (firstText(source.title) && firstText(source.title) !== citation.id ? firstText(source.title) as string : 'Source record')
        : citation.title,
      locator: citation.locator ?? firstText(source.locator, source.selected_locator),
      asOf: citation.asOf ?? firstText(source.as_of, source.observation_time, source.published_at),
    }
  })
}

function canonicalPayload(run: RunSummary | RunDetail): RecordValue {
  const object = run as unknown as RecordValue
  // Only the persisted canonical projection may supply an investment
  // outcome. Agent reports and discovery candidates remain context while the
  // CIO decision is absent.
  const nested = [
    object.current_decision,
    object.canonical_decision,
    object.current_case_decision,
  ]
  return nested.map(record).find((item): item is RecordValue => Boolean(item)) ?? {}
}

function candidateValues(run: RunSummary | RunDetail, payload: RecordValue): RecordValue[] {
  const object = run as unknown as RecordValue
  const nested = [payload.candidates, payload.candidate_decisions, payload.outcomes, payload.recommendations, object.candidate_decisions, object.candidates]
  const found = nested.find((item) => Array.isArray(item))
  if (Array.isArray(found) && found.length) return found.map(record).filter((item): item is RecordValue => Boolean(item))
  if (payload.instrument || payload.ticker || payload.symbol || payload.outcome || payload.stance || payload.disposition || payload.entry || payload.entry_plan || payload.watch_trigger) return [payload]
  const research = object.research_candidates
  return Array.isArray(research) ? research.map(record).filter((item): item is RecordValue => Boolean(item)) : []
}

function taskStage(task: RecordValue): string {
  const value = normalizeKey(task.execution_stage ?? task.stage ?? task.kind ?? task.title ?? task.agent_id)
  if (value.includes('claim') || value.includes('intake') || value.includes('route') || task.agent_id === 'A00') return 'Claim'
  if (value.includes('evidence') || value.includes('source') || value.includes('filing') || value.includes('universe')) return 'Evidence'
  if (value.includes('research') || value.includes('fundamental') || value.includes('technical') || value.includes('analyst') || /^a0[1-9]$/.test(String(task.agent_id ?? '').toLowerCase())) return 'Research'
  if (value.includes('check') || value.includes('risk') || value.includes('portfolio') || value.includes('sizing') || task.agent_id === 'A10') return 'Checks'
  if (value.includes('cio') || task.agent_id === 'A11') return 'CIO'
  return 'Research'
}

function taskError(task: RecordValue | null): string | null {
  if (!task) return null
  const taskStatus = normalizeKey(task.status)
  if (!['blocked', 'failed', 'error'].includes(taskStatus)) return null
  const attempts = list(task.attempts).map(record).filter((item): item is RecordValue => Boolean(item))
  const currentAttempt = attempts.length ? attempts[attempts.length - 1] : null
  const message = firstText(task.error, currentAttempt?.error, task.blocking_reason, task.terminal_summary)
  return message ? message.slice(0, 800) : null
}

function actionAllowed(actions: unknown, action: string, fallback = false): boolean {
  if (Array.isArray(actions)) return actions.some((item) => normalizeKey(item) === normalizeKey(action))
  const object = record(actions)
  if (object && action in object) return object[action] === true
  return fallback
}

function friendlyOwner(value: unknown): string | null {
  const owner = firstText(value)
  if (!owner) return null
  const key = normalizeKey(owner).toUpperCase()
  if (key === 'A00' || key.includes('CHIEF')) return 'Chief of Staff'
  if (key === 'A11' || key.includes('CIO') || key.includes('INVESTMENT_COMMITTEE')) return 'CIO'
  if (key.includes('CALCULATION') || key.includes('SIZING') || key.includes('CODE')) return 'Code checks'
  if (/^A(?:0[1-9]|10)$/.test(key)) return 'Researcher'
  return owner
}

function latestScenarioOutputs(run: RunSummary | RunDetail): OutputRecord[] {
  const outputs = 'outputs' in run && Array.isArray(run.outputs) ? run.outputs : []
  const persisted = outputs.filter((item) => Boolean(item.price_scenarios || item.simulation_snapshot || item.scenario_fan?.length || item.scenario_quantiles?.length))
  const object = run as unknown as RecordValue
  const context = record(object.calculation_context) ?? {}
  const contextCandidates = list(context?.candidates).map(record).filter((item): item is RecordValue => Boolean(item))
  const contextOutputs = contextCandidates.flatMap((candidate, index) => {
    const scenario = record(candidate.scenario)
    if (!scenario) return []
    const ticker = firstText(candidate.ticker, candidate.symbol, scenario.ticker)
    const currency = firstText(candidate.currency, scenario.currency)
    const buckets = record(scenario.scenarios)
    const fan = buckets
      ? Object.entries(buckets).flatMap(([label, rawBucket]) => {
        const bucket = record(rawBucket)
        return list(bucket?.fan).map(record).filter((row): row is RecordValue => Boolean(row)).map((row) => ({
          ...row,
          label: `${ticker ? `${ticker} · ` : ''}${label} · day ${String(row.trading_day ?? '')}`.replace(/ · day $/, ''),
          currency,
        }))
      })
      : []
    const quantiles = buckets
      ? Object.entries(buckets).flatMap(([label, rawBucket]) => {
        const bucket = record(rawBucket)
        const terminal = record(bucket?.terminal_price_quantiles)
        return terminal ? [{ ...terminal, label: `${ticker ? `${ticker} · ` : ''}${label}`, currency }] : []
      })
      : []
    const sourceRefs = citations([candidate.source_refs, scenario.source_refs])
    return [{
      id: `calculation-${ticker ?? index}`,
      output_id: `calculation-${ticker ?? index}`,
      title: `Code-owned scenario calculation${ticker ? ` · ${ticker}` : ''}`,
      status: firstText(scenario.status, candidate.status, context.status),
      created_at: firstText(scenario.as_of, context.as_of),
      updated_at: firstText(scenario.as_of, context.as_of),
      scenario_fan: fan,
      scenario_quantiles: quantiles,
      price_scenarios: scenario,
      simulation_snapshot: scenario,
      scenario_status: firstText(scenario.status, candidate.status, context.status),
      scenario_method: firstText(scenario.method, scenario.calculation_method),
      scenario_ticker: ticker,
      scenario_currency: currency,
      scenario_as_of: firstText(scenario.as_of, context.as_of),
      scenario_missing_reason: firstText(scenario.missing_reason),
      scenario_assumptions: strings(scenario.assumptions),
      scenario_limitations: strings(scenario.limitations),
      scenario_source_refs: sourceRefs.map((item) => ({ id: item.id, source_ref: item.id, title: item.title, locator: item.locator })),
      scenario_result_hash: firstText(scenario.result_hash),
    } as OutputRecord]
  })
  return [...persisted, ...contextOutputs]
}

function decisionForRun(run: RunSummary | RunDetail, _sources: EvidenceRef[]): CaseDecision {
  const payload = canonicalPayload(run)
  const hasCanonicalDecision = Object.keys(payload).length > 0
  const object = run as unknown as RecordValue
  const watchlistOverlays = list(object.__watchlist_items).map(record).filter((item): item is RecordValue => Boolean(item))
  const legacyOverlay = record(object.__watchlist_item)
  const rootOutput = record(object.latest_output)
  const brief = (run.decision_brief ?? run.cio_brief ?? null) as CioBrief | null
  const triggerText = (value: unknown): string | null => {
    if (Array.isArray(value)) return value.map((item) => { const itemRecord = record(item); return firstText(itemRecord?.condition, itemRecord?.reopen_when, itemRecord?.catalyst, item) }).filter((item): item is string => Boolean(item)).join(' · ') || null
    const itemRecord = record(value)
    return firstText(itemRecord?.condition, itemRecord?.reopen_when, itemRecord?.catalyst, value)
  }
  const globalTrigger = firstText(triggerText(payload.watch_triggers), triggerText(payload.watch_trigger), payload.trigger, payload.reopen_when, payload.next_review_trigger, brief?.next_review_trigger)
  const scenarioOutputs = latestScenarioOutputs(run)
  const runSources = 'sources' in run && Array.isArray(run.sources) ? run.sources : []
  const calculationSources = list(record(object.calculation_context)?.candidates).flatMap((candidate) => {
    const row = record(candidate)
    return [row?.source_refs, record(row?.scenario)?.source_refs]
  })
  const allSources = citations([
    ...list(payload.source_citations ?? payload.citations ?? payload.source_refs ?? payload.source_ids),
    ...list(object.source_citations),
    ...list(rootOutput?.source_refs),
    ...list(rootOutput?.evidence),
    ...runSources,
    ...calculationSources,
  ])
  // A run can retain discovery gaps beside its current decision. Once the
  // canonical projection exists, only that projection describes what is
  // blocking the current case; the run-level gap ledger is historical and
  // can contain prerequisites that have since been satisfied.
  const canonicalCandidateGaps = list(payload.candidates).flatMap((candidate) => {
    const row = record(candidate)
    return row ? [row.material_blockers, row.current_blockers, row.blocking_gaps].flatMap((value) => list(value)) : []
  })
  const canonicalGaps = firstNonEmptyList(payload.material_blockers, payload.blockers, payload.blocking_gaps, canonicalCandidateGaps)
  const liveRunGaps = firstNonEmptyList(object.current_blockers, object.active_blockers, object.blocking_gaps)
  const gaps = Array.from(new Map(
    (hasCanonicalDecision ? canonicalGaps : liveRunGaps).map((item) => {
      const gap = record(item)
      return gap ? { ...gap, description: firstText(gap.description, gap.reason, gap.key) ?? undefined, action: firstText(gap.action, gap.reopen_when), owner: firstText(gap.owner, gap.relevant_role) } as MissingGap : null
    }).filter((item): item is MissingGap => Boolean(item)).map((gap): [string, MissingGap] => [
      `${String(gap.key ?? '')}|${String(gap.description ?? '')}`,
      gap,
    ]),
  ).values())
  const globalUnknowns = hasCanonicalDecision
    ? strings(payload.unknowns ?? payload.missing_inputs ?? payload.missing_data)
    : strings(payload.unknowns ?? payload.missing_inputs ?? payload.missing_data ?? rootOutput?.missing_data)
  const rawCandidates = candidateValues(run, payload)
  const candidates = (rawCandidates.length ? rawCandidates : [payload]).map((candidate, index) => {
    const identity = (item: RecordValue) => JSON.stringify([String(item.ticker ?? item.symbol ?? '').toUpperCase(), item.direction ?? 'long', record(item.instrument_identity)?.asset_id ?? null, record(item.sizing)?.account_id ?? null])
    const watchlistOverlay = [...watchlistOverlays, ...(legacyOverlay ? [legacyOverlay] : [])].find((item) => {
      const saved = record(item.candidate)
      return saved && identity(saved) === identity(candidate)
    })

    const sizing = record(candidate.sizing ?? candidate.position_sizing ?? candidate.allocation)
    const actionPlan = record(candidate.action_plan)
    const valuation = record(candidate.valuation) as ValuationBlock | null
    const baseValuation = record(valuation?.scenarios)?.base
    const entryPlan = candidate.entry_plan ?? candidate.entry_zone ?? candidate.entry
    const targetPlan = hasCanonicalDecision && valuation
      ? (baseValuation != null ? {value: baseValuation, currency: valuation.currency, condition: actionPlan?.exit_condition} : null)
      : candidate.future_target ?? candidate.target_price ?? candidate.target ?? candidate.exit_price
    const entry = planValue(candidate.entry_price ?? entryPlan)
    const target = planValue(targetPlan)
    const firstTrigger = record(list(candidate.watch_triggers)[0])
    const candidateTrigger = firstText(triggerText(candidate.watch_triggers), triggerText(candidate.watch_trigger), candidate.trigger, candidate.price_trigger, candidate.catalyst_trigger, candidate.reopen_when, globalTrigger)
    const candidateOutcomeValue = hasCanonicalDecision
      ? candidate.outcome !== undefined
        ? candidate.outcome
        : rawCandidates.length === 1 ? payload.outcome ?? payload.stance ?? payload.disposition : null
      : candidate.stance ?? candidate.disposition ?? candidate.decision ?? candidate.execution_state
    const candidateOutcome = outcome(candidateOutcomeValue, Boolean(candidateTrigger), hasCanonicalDecision)
    const candidateSources = citations([
      ...list(candidate.source_citations ?? candidate.citations ?? candidate.source_refs ?? candidate.source_ids),
      ...list(candidate.evidence),
      ...entry.sourceRefs,
      ...target.sourceRefs,
    ])
    const evidenceRows = list(candidate.evidence).map((item): DecisionEvidenceRecord | null => {
      const evidence = record(item)
      const statement = firstText(evidence?.statement, evidence?.claim, evidence?.value, item)
      if (!statement) return null
      return {
        statement,
        kind: normalizeKey(evidence?.kind) || 'unknown',
        validationStatus: firstText(evidence?.validation_status, evidence?.status),
        semanticStatus: firstText(evidence?.semantic_status, evidence?.semanticStatus),
        textMatch: typeof evidence?.text_match === 'boolean' ? evidence.text_match : null,
        bindingChecks: list(evidence?.binding_checks).map(record).filter((item): item is RecordValue => Boolean(item)).map((check) => ({ key: firstText(check.key) ?? undefined, status: firstText(check.status) ?? undefined, reason: firstText(check.reason) ?? undefined })),
        sourceVersion: firstText(evidence?.source_version, evidence?.version),
        freshness: firstText(evidence?.freshness, evidence?.freshness_status),
        asOf: firstText(evidence?.as_of, evidence?.period),
        unknownReason: firstText(evidence?.unknown_reason, evidence?.validation_reason, evidence?.missing_reason),
        citations: citations([evidence]),
      }
    }).filter((item): item is DecisionEvidenceRecord => Boolean(item))
    const validatedFacts = evidenceRows.filter((item) => item.kind === 'fact' && normalizeKey(item.validationStatus) === 'validated').map((item) => item.statement)
    const scenario = scenarioOutputs.find((item) => String(item.scenario_ticker ?? '').toUpperCase() === String(candidate.ticker ?? candidate.symbol ?? '').toUpperCase()) ?? null
    const actionInvalidation = text(actionPlan?.invalidation_condition)
    const invalidation = Array.from(new Set([...(actionInvalidation ? [actionInvalidation] : []), ...strings(candidate.invalidation ?? candidate.invalidation_conditions ?? candidate.downside_conditions ?? brief?.invalidation_conditions)]))
    const blockers = Array.from(new Set([
      ...strings(candidate.blockers),
      ...strings(candidate.current_blockers),
      ...list(candidate.material_blockers ?? candidate.blocking_gaps).map((item) => {
        const blocker = record(item)
        return firstText(blocker?.reason, blocker?.description, blocker?.key, blocker?.why_waiting, item)
      }).filter((item): item is string => Boolean(item)),
      ...strings(candidate.missing_inputs),
    ]))
    const inputs = record(candidate.inputs ?? candidate.sizing_inputs ?? sizing?.inputs ?? payload.inputs)
    const sizingChecks = list(sizing?.checks ?? candidate.sizing_checks ?? candidate.risk_checks).map((check) => {
      const checkRecord = record(check)
      const label = firstText(checkRecord?.label, checkRecord?.check, checkRecord?.key, check) ?? ''
      return [label.replaceAll('_', ' '), firstText(checkRecord?.status), firstText(checkRecord?.detail, checkRecord?.reason)].filter(Boolean).join(' · ')
    }).filter(Boolean)
    const thesis = record(candidate.thesis)
    const payoff = record(candidate.payoff)
    const portfolioContext = record(candidate.portfolio_context)
    const recommendationGate = record(candidate.recommendation_gate)
    const researchContract = firstText(candidate.research_contract, payload.research_contract)
    const keyQuestions = normalizeKeyQuestions(candidate.key_questions)
    const jointReview = normalizeJointReview(candidate.joint_review ?? (rawCandidates.length === 1 ? payload.joint_review : null))
    const lifecycle = record(candidate.lifecycle)
    const recommendedShares = formatNumber(candidate.recommended_shares ?? sizing?.recommended_shares)
    const maximumPermittedShares = formatNumber(candidate.maximum_permitted_shares ?? sizing?.maximum_permitted_shares ?? sizing?.max_shares)
    return {
      key: firstText(candidate.candidate_key, candidate.key, candidate.id, watchlistOverlay?.candidate_key, candidate.ticker, candidate.symbol) ?? String(index),
      instrument: caseCardSummary(firstText(candidate.instrument, candidate.ticker, candidate.symbol, payload.instrument, payload.ticker, run.ticker) ?? 'Candidate unavailable'),
      name: firstText(candidate.name, candidate.company_name),
      outcome: candidateOutcome,
      reason: firstText(candidate.reason, candidate.rationale, candidate.conclusion, candidate.decline_reason, candidate.watch_reason, payload.reason, payload.rationale, brief?.reason, rootOutput?.conclusion),
      entry: entry.display,
      entryCondition: firstText(candidate.entry_condition, actionPlan?.entry_condition, candidate.entry_trigger, candidate.executable_entry, candidate.condition, entryPlan && record(entryPlan)?.condition),
      quantity: recommendedShares,
      quantityLabel: recommendedShares ? 'Recommended shares' : 'Allocation',
      quantityIsValid: Boolean(recommendedShares),
      notional: money(candidate.recommended_notional ?? sizing?.recommended_notional ?? (recommendedShares ? null : candidate.notional ?? candidate.position_notional ?? sizing?.notional ?? sizing?.amount), candidate.currency ?? sizing?.currency ?? entry.currency),
      currency: firstText(candidate.currency, sizing?.currency, entry.currency),
      target: target.display,
      exitLogic: firstText(candidate.exit_logic, actionPlan?.exit_condition, candidate.target_logic, candidate.exit_condition, targetPlan && record(targetPlan)?.condition),
      horizon: firstText(candidate.horizon, candidate.time_horizon, payload.horizon, brief?.horizon, run.horizon),
      catalyst: firstText(candidate.catalyst, candidate.catalysts && strings(candidate.catalysts).join(' · '), list(actionPlan?.catalyst_events).map((event) => text(record(event)?.description)).filter(Boolean).join(' · '), payload.catalyst, payload.catalysts && strings(payload.catalysts).join(' · '), brief?.catalysts?.join(' · '), actionPlan?.review_at ? `Thesis review ${formatCalendarDate(actionPlan.review_at)}` : null),
      downside: firstText(candidate.downside, candidate.max_downside, candidate.risk, candidate.risks && strings(candidate.risks).join(' · '), brief?.accepted_risks?.join(' · ')),
      invalidation,
      trigger: candidateTrigger,
      triggerState: firstText(candidate.trigger_state, candidate.watch_check_status),
      expiry: firstText(candidate.trigger_expiry, candidate.expiry, candidate.review_at, candidate.next_review_at, firstTrigger?.review_at, firstTrigger?.trigger_date, payload.trigger_expiry, payload.expiry),
      blockers,
      unknowns: Array.from(new Set([
        ...strings(candidate.unknowns),
        ...strings(candidate.missing_inputs),
        ...strings(candidate.missing_data),
        ...strings(sizing?.missing_inputs),
      ])),
      rationale: firstText(candidate.recorded_rationale, candidate.rationale, candidate.reason, candidate.conclusion, payload.recorded_rationale, payload.rationale, brief?.reason, rootOutput?.conclusion),
      inputs,
      citations: candidateSources,
      evidence: evidenceRows,
      sizingChecks,
      sizingStatus: firstText(sizing?.status, sizing?.execution_state, sizing?.eligibility, candidate.sizing_status, candidate.execution_state, candidate.allocation_status),
      supportingEvidence: Array.from(new Set([...strings(candidate.supporting_evidence ?? candidate.evidence_for ?? candidate.supporting_claims ?? payload.supporting_evidence ?? payload.evidence_for), ...validatedFacts])),
      objections: Array.from(new Set([
        ...strings(candidate.objections),
        ...strings(candidate.counterarguments),
        ...strings(candidate.evidence_against),
        ...strings(candidate.risks),
        ...strings(payload.objections),
        ...strings(payload.counterarguments),
        ...strings(payload.strongest_objection),
        ...strings(payload.risks),
      ])),
      scenarioOutput: scenario,
      scenarioForecast: record(candidate.forecast ?? candidate.scenario_assessment),
      direction: firstText(candidate.direction, payload.direction, brief?.direction),
      strategy: firstText(candidate.strategy, payload.strategy, brief?.strategy),
      decisionAsOf: firstText(candidate.as_of, payload.as_of, run.updated_at, run.created_at),
      benchmarkTicker: firstText(candidate.benchmark_ticker, actionPlan?.benchmark_ticker, payload.benchmark_ticker, brief?.benchmark_ticker),
      benchmarkRationale: firstText(candidate.benchmark_rationale, actionPlan?.benchmark_rationale, payload.benchmark_rationale, brief?.benchmark_rationale),
      variantView: firstText(thesis?.variant_view, candidate.variant_view, payload.variant_view, brief?.thesis?.variant_view),
      marketExpectation: firstText(thesis?.market_expectation, candidate.market_expectation, payload.market_expectation, brief?.thesis?.market_expectation),
      whyNow: firstText(thesis?.why_now, candidate.why_now, payload.why_now, brief?.thesis?.why_now),
      opposingExplanation: firstText(thesis?.strongest_opposing_explanation, candidate.strongest_opposing_explanation, payload.strongest_opposing_explanation, brief?.thesis?.strongest_opposing_explanation),
      disconfirmingEvidence: strings(thesis?.disconfirming_evidence ?? candidate.disconfirming_evidence ?? brief?.thesis?.disconfirming_evidence),
      decisionChangeConditions: strings(thesis?.decision_change_conditions ?? candidate.decision_change_conditions ?? brief?.thesis?.decision_change_conditions),
      valuation,
      futureTarget: record(candidate.future_target) as CioPricePlan | null,
      payoff,
      portfolioContext,
      actionPlan: actionPlan ?? (brief?.action_plan as RecordValue | null | undefined) ?? null,
      recommendationGate,
      researchContract,
      keyQuestions,
      jointReview,
      materialBlockers: list(candidate.material_blockers).map(record).filter((row): row is RecordValue => Boolean(row)).map((row) => ({...row, description: firstText(row.reason, row.description) ?? undefined})) as MissingGap[],
      lifecycleState: firstText(watchlistOverlay?.lifecycle_state, watchlistOverlay?.state, lifecycle?.state, candidate.lifecycle_state, candidate.state),
      currentQuote: record(watchlistOverlay?.current_quote) ?? record(candidate.current_quote),
      distanceToEntry: record(watchlistOverlay?.distance_to_entry) ?? record(candidate.distance_to_entry),
      reviewDueAt: firstText(watchlistOverlay?.review_due_at, candidate.review_due_at, actionPlan?.review_at, candidate.review_at),
      overdue: Boolean(watchlistOverlay?.overdue ?? candidate.overdue),
      nextAction: firstText(watchlistOverlay?.next_action, candidate.next_action, actionPlan?.entry_condition, candidate.reopen_when),
      maximumPermittedShares,
      recommendedShares,
    }
  }).filter((candidate) => candidate.instrument !== 'Candidate unavailable' || candidate.reason || candidate.outcome !== 'unknown')
  const globalOutcome = hasCanonicalDecision
    ? outcome(payload.outcome !== undefined ? payload.outcome : payload.stance ?? payload.disposition, Boolean(globalTrigger), true)
    : outcome(payload.outcome ?? payload.stance ?? payload.disposition, Boolean(globalTrigger))
  const allocationMode = firstText(payload.allocation_mode, payload.allocationMode, object.allocation_mode, object.allocationMode)
  const firstCandidate = candidates[0]
  const selectedOutcome = hasCanonicalDecision && (payload.outcome === null || payload.outcome === undefined)
    ? 'pending'
    : globalOutcome !== 'unknown' && globalOutcome !== 'pending' ? globalOutcome : firstCandidate?.outcome ?? globalOutcome
  const lifecycleState = firstText(payload.lifecycle_state, record(payload.lifecycle)?.state, firstCandidate?.lifecycleState)
  const activeGapRows = gaps.filter((gap) => {
    const status = normalizeKey(gap.status)
    return !['resolved', 'closed', 'historical', 'superseded'].includes(status)
  })
  const taskRows = 'tasks' in run && Array.isArray(run.tasks) ? run.tasks : []
  const taskRecords = taskRows.map(record).filter((item): item is RecordValue => Boolean(item))
  const failedTask = taskRecords.find((task) => ['blocked', 'failed', 'error'].includes(normalizeKey(task.status)) || taskError(task)) ?? null
  const waitingTask = taskRecords.find((task) => ['waiting_for_evidence', 'waiting_for_review', 'paused'].includes(normalizeKey(task.status))) ?? null
  const breakTask = failedTask ?? waitingTask
  const directOperational = normalizeKey(run.execution_status ?? run.execution_state ?? run.status)
  const canonicalOperational = normalizeKey(payload.execution_state)
  const operational = ['failed', 'awaiting_input'].includes(canonicalOperational) ? canonicalOperational : directOperational
  const breakStage = firstText(object.actual_break_stage, object.break_stage, object.failure_stage, object.failed_at_stage, breakTask && taskStage(breakTask))
  const failureReason = taskError(failedTask) ?? (['failed', 'error'].includes(operational)
    ? firstText(object.error, object.failure_reason, object.current_blocker, object.blocking_reason)
    : null)
  const taskId = firstText(failedTask?.id, failedTask?.task_id)
  const taskRetryAvailable = Boolean(failedTask && actionAllowed(failedTask.allowed_actions, 'retry', ['blocked', 'failed', 'error'].includes(normalizeKey(failedTask.status))))
  const runRetryAvailable = actionAllowed(object.allowed_actions, 'retry', false)
  const retryAvailable = taskRetryAvailable || (!taskId && runRetryAvailable)
  const retryTarget = taskRetryAvailable && taskId ? taskId : runRetryAvailable ? firstText(object.id, object.run_id) : null
  const retryScope = taskRetryAvailable && taskId ? 'task' : runRetryAvailable && retryTarget ? 'run' : null
  const recovery = failureReason
    ? (retryAvailable ? 'Retry the failed stage after reviewing this error.' : 'Review the failed stage before continuing.')
    : firstText(object.recovery_action, object.recovery, object.next_action, object.reopen_when, payload.recovery_action, payload.recovery, activeGapRows[0]?.action, activeGapRows[0]?.reopen_when)
  const candidateCitations = candidates.flatMap((candidate) => candidate.citations)
  const combinedCitations = Array.from(new Map([...allSources, ...candidateCitations].map((item) => [`${item.id}|${item.locator ?? ''}|${item.asOf ?? ''}`, item])).values())
  return {
    candidates,
    allocationMode,
    stale: Boolean(payload.stale ?? object.stale),
    outcome: selectedOutcome === 'unknown' && ['failed', 'error'].includes(operational) ? 'failed' : selectedOutcome,
    reason: hasCanonicalDecision
      ? firstText(payload.reason, payload.rationale, object.current_decision_reason, object.summary, run.summary, rootOutput?.conclusion)
      : firstText(object.current_blocker, object.blocking_reason, run.summary) ?? 'Research is in progress; a current decision has not been recorded.',
    asOf: firstText(payload.as_of, payload.updated_at, brief?.as_of, run.updated_at, run.created_at),
    revision: firstText(payload.decision_revision, payload.revision, payload.version, object.decision_revision, object.current_decision_revision),
    evidenceVersion: firstText(payload.evidence_version, payload.evidence_as_of, object.evidence_version, object.evidence_readiness, run.evidence_readiness, run.evidence_status),
    rationale: hasCanonicalDecision ? firstText(payload.recorded_rationale, payload.rationale, brief?.reason, rootOutput?.conclusion, run.summary) : null,
    supportingEvidence: Array.from(new Set([...strings(payload.supporting_evidence ?? payload.evidence_for ?? payload.strongest_support), ...candidates.flatMap((candidate) => candidate.supportingEvidence)])),
    objections: Array.from(new Set([
      ...strings(payload.objections),
      ...strings(payload.counterarguments),
      ...strings(payload.strongest_objection),
      ...strings(payload.risks),
      ...candidates.flatMap((candidate) => candidate.objections),
    ])),
    inputs: hasCanonicalDecision ? record(payload.inputs ?? payload.portfolio_inputs ?? payload.sizing_inputs ?? object.portfolio_inputs) : null,
    unknowns: Array.from(new Set([...globalUnknowns, ...candidates.flatMap((candidate) => candidate.unknowns)])),
    blockers: activeGapRows,
    recovery,
    breakStage,
    decisionHistory: firstNonEmptyList(payload.history, payload.decision_history, object.decision_history, object.historical_decisions),
    // Keep citations scoped to this case. A global source fallback can make an
    // unrelated source look like support for the selected decision.
    sourceCitations: combinedCitations,
    scenarioOutputs,
    failureReason,
    retryAvailable,
    retryTarget,
    retryScope,
    hasCanonicalDecision,
    lifecycleState,
  }
}

function runStatus(run: RunSummary) {
  const object = run as unknown as RecordValue
  const canonical = record(object.current_decision) ?? record(object.canonical_decision) ?? record(object.current_case_decision)
  const direct = normalizeKey(run.execution_status ?? run.execution_state ?? run.status)
  const canonicalState = normalizeKey(canonical?.execution_state)
  return ['awaiting_input', 'failed'].includes(canonicalState) ? canonicalState : direct
}

function runIsOperational(run: RunSummary) {
  return ['queued', 'running', 'waiting_for_evidence', 'waiting_for_review', 'awaiting_input', 'paused', 'failed', 'error', 'blocked', 'interrupted'].includes(runStatus(run))
}

function runNeedsAttention(run: RunSummary) {
  return ['waiting_for_evidence', 'waiting_for_review', 'awaiting_input', 'paused', 'failed', 'error', 'blocked'].includes(runStatus(run))
}

function originIsReddit(run: RunSummary) {
  return normalizeKey(run.origin) === 'reddit' || normalizeKey(run.root_origin) === 'reddit' || normalizeKey(run.origin_ref).includes('reddit')
}

function runTitle(run: RunSummary) {
  const object = run as unknown as RecordValue
  return firstText(object.display_title, object.title, run.original_question, run.question) ?? 'Untitled question'
}

function groupedRuns(runs: RunSummary[]) {
  const map = new Map<string, RunSummary[]>()
  for (const run of runs) {
    const key = String(run.question_group_id ?? run.root_run_id ?? run.original_question ?? run.question ?? run.id)
    map.set(key, [...(map.get(key) ?? []), run])
  }
  const timestamp = (value: unknown) => {
    const date = Date.parse(String(value ?? ''))
    return Number.isFinite(date) ? date : 0
  }
  return [...map.values()].map((items) => {
    const sorted = [...items].sort((a, b) => timestamp(b.updated_at ?? b.created_at) - timestamp(a.updated_at ?? a.created_at))
    const current = sorted.find((item) => {
      const object = item as unknown as RecordValue
      return Boolean(record(object.current_decision) ?? record(object.canonical_decision) ?? record(object.current_case_decision))
    }) ?? sorted.find((item) => runIsOperational(item) || item.decision_brief || item.cio_brief) ?? sorted[0]
    return { key: String(current.id), items, current }
  }).sort((a, b) => timestamp(b.current.updated_at ?? b.current.created_at) - timestamp(a.current.updated_at ?? a.current.created_at))
}

function outcomeLabel(value: DecisionOutcome) {
  if (value === 'recommend') return 'Recommend'
  if (value === 'watchlist') return 'Watchlist'
  if (value === 'decline') return 'Decline'
  if (value === 'mixed') return 'Mixed outcomes'
  if (value === 'awaiting_info') return 'Awaiting information'
  if (value === 'failed') return 'Failed'
  if (value === 'pending') return 'Decision pending'
  return 'Decision pending'
}

function outcomeClass(value: DecisionOutcome) {
  return `decision-outcome decision-outcome-${value}`
}

function candidateHeadline(candidate: CandidateDecision) {
  if (candidate.outcome === 'recommend') {
    const pieces = [candidate.instrument, candidate.entry ? `at ${candidate.entry}` : null].filter(Boolean)
    return pieces.join(' · ') || 'Entry plan recorded'
  }
  if (candidate.outcome === 'watchlist') return candidate.trigger ?? `Wait for a recorded trigger on ${candidate.instrument}`
  if (candidate.outcome === 'decline') return candidate.reason ?? `The case for ${candidate.instrument} is not supported`
  if (candidate.outcome === 'failed') return 'The research flow failed before an investment outcome was recorded'
  if (candidate.outcome === 'pending') return 'Investment outcome is not recorded in this revision'
  return candidate.reason ?? 'A current investment outcome is waiting on information'
}

function FlowStrip({ run }: { run: RunSummary | RunDetail }) {
  const object = run as unknown as RecordValue
  const tasks = 'tasks' in run && Array.isArray(run.tasks) ? run.tasks.map(record).filter((item): item is RecordValue => Boolean(item)) : []
  const canonical = canonicalPayload(run)
  const calculationContext = record(object.calculation_context)
  const calculationCandidates = list(calculationContext?.candidates).map(record).filter((item): item is RecordValue => Boolean(item))
  const canonicalCandidates = list(canonical.candidates).map(record).filter((item): item is RecordValue => Boolean(item))
  const codeCheckRecorded = calculationCandidates.length > 0 || canonicalCandidates.some((candidate) => {
    const sizing = record(candidate.sizing)
    return list(sizing?.checks).length > 0 || ['ready', 'awaiting_input', 'failed'].includes(normalizeKey(sizing?.execution_state))
  })
  const statuses = ['Claim', 'Evidence', 'Research', 'Checks', 'CIO'].map((stage) => {
    const rows = tasks.filter((task) => taskStage(task) === stage)
    if (!rows.length) {
      if (stage === 'Claim' && run.question) return { stage, status: 'recorded' }
      if (stage === 'Checks' && codeCheckRecorded) return { stage, status: 'recorded' }
      return { stage, status: 'not_scheduled' }
    }
    const values = rows.map((task) => normalizeKey(task.status))
    if (values.some((value) => ['failed', 'blocked'].includes(value))) return { stage, status: 'blocked' }
    if (values.some((value) => ['running', 'queued', 'waiting_for_evidence', 'waiting_for_review', 'paused'].includes(value))) return { stage, status: 'active' }
    return { stage, status: values.every((value) => ['completed', 'cancelled', 'interrupted'].includes(value)) ? 'recorded' : 'unknown' }
  })
  const explicit = record(object.flow ?? object.workflow)
  return <div className="decision-flow" aria-label="Decision flow"><span className="decision-flow-caption">Case flow</span>{statuses.map((item, index) => <div className={`decision-flow-step decision-flow-${item.status}`} key={item.stage}><span className="decision-flow-index">{index + 1}</span><span><strong>{item.stage}</strong><small>{item.status === 'not_scheduled' ? (explicit?.[item.stage.toLowerCase()] ? 'Recorded' : 'Not scheduled') : item.status === 'recorded' ? 'Recorded' : item.status === 'active' ? 'In progress' : item.status === 'blocked' ? 'Break here' : statusLabel(item.status)}</small></span>{index < statuses.length - 1 && <i aria-hidden="true" />}</div>)}</div>
}

function DecisionOutcomeBadge({ value }: { value: DecisionOutcome }) {
  return <span className={outcomeClass(value)}><span className="decision-outcome-dot" />{outcomeLabel(value)}</span>
}

function CandidateOutcomeCard({ candidate }: { candidate: CandidateDecision }) {
  const allocationDetails = <div className="candidate-allocation-details"><span>{candidate.quantityLabel}</span><strong>{candidate.quantity ?? 'Not recorded in this revision'}</strong>{candidate.notional && <small>{candidate.notional} notional</small>}{candidate.maximumPermittedShares && <small>Policy ceiling: {candidate.maximumPermittedShares}</small>}{!candidate.quantityIsValid && <small className="candidate-sizing-note">Allocation is not an executable order quantity.</small>}</div>
  return <article className={`candidate-outcome-card candidate-outcome-${candidate.outcome}`}>
    <div className="candidate-outcome-head"><div><span className="candidate-symbol">{candidate.instrument}</span>{candidate.name && <span className="candidate-name">{candidate.name}</span>}</div></div>
    <p className="candidate-outcome-headline">{safeCardText(candidate.outcome === 'watchlist' ? candidate.reason : candidateHeadline(candidate), candidate.outcome === 'watchlist' ? 'Watch condition recorded below.' : 'Decision rationale not recorded.')}</p>
    {candidate.outcome === 'recommend' && <div className="candidate-plan-grid"><div><span>Entry</span><strong>{safeCardText(candidate.entry, 'Unavailable')}</strong>{candidate.entryCondition && <small>{safeCardText(candidate.entryCondition, 'Entry condition recorded')}</small>}</div><PrivateFinancialDisclosure label="Reveal allocation locally" description="Recommended shares, policy ceilings, and notional values can expose personal sizing inputs. They remain closed until you choose to inspect them on this device.">{allocationDetails}</PrivateFinancialDisclosure><div><span>Target / exit</span><strong>{safeCardText(candidate.target, 'Unavailable')}</strong>{candidate.exitLogic && <small>{safeCardText(candidate.exitLogic, 'Exit condition recorded')}</small>}</div></div>}
    {candidate.outcome === 'watchlist' && <div className="candidate-trigger"><span>Change the decision when</span><strong>{safeCardText(candidate.trigger, 'No actionable trigger recorded')}</strong>{candidate.expiry && <small>Review {dateLabel(candidate.expiry)}</small>}{candidate.triggerState && <small>Monitor: {safeCardText(candidate.triggerState.replaceAll('_', ' '), 'status unavailable')}</small>}</div>}
    {candidate.outcome === 'decline' && candidate.reason && <p className="candidate-reason">{safeCardText(candidate.reason, 'The case is not supported.')}</p>}
    <div className="candidate-meta"><span>{safeCardText(candidate.horizon, 'Horizon unavailable')}</span>{candidate.catalyst && <span>Catalyst: {safeCardText(candidate.catalyst, 'Recorded')}</span>}{candidate.sizingStatus && <span>Sizing: {safeCardText(candidate.sizingStatus, 'Status unavailable')}</span>}</div>
  </article>
}

function candidateActionSummary(candidate: CandidateDecision, decision: CaseDecision) {
  if (candidate.outcome === 'watchlist') {
    return { label: 'Reopen when', value: candidate.trigger ?? candidate.nextAction ?? candidate.catalyst, fallback: 'No actionable trigger recorded.' }
  }

  // Entry instructions are executable only after the canonical decision and
  // recommendation gate agree that this candidate is ready. A pending
  // canonical outcome can retain an old action plan, so prefer its recorded
  // recovery action or blocker repair instead of presenting that plan as a
  // current order.
  const gatePassed = normalizeKey(candidate.recommendationGate?.status) === 'pass'
  const actionableRecommendation = candidate.outcome === 'recommend'
    && decision.outcome === 'recommend'
    && (!decision.hasCanonicalDecision || gatePassed)
    && candidate.materialBlockers.length === 0
    && decision.blockers.length === 0
  if (actionableRecommendation) {
    return { label: 'Key action', value: candidate.entryCondition ?? candidate.entry ?? candidate.nextAction ?? candidate.target, fallback: 'Entry condition not recorded.' }
  }
  if (candidate.outcome === 'decline') {
    return { label: 'Reopen when', value: candidate.nextAction ?? candidate.trigger ?? candidate.invalidation[0], fallback: 'No reopening condition recorded.' }
  }
  const recovery = firstText(
    decision.recovery,
    ...decision.blockers.flatMap((gap) => [gap.action, gap.reopen_when]),
    ...candidate.materialBlockers.flatMap((gap) => [gap.action, gap.reopen_when]),
  )
  return { label: 'Next action', value: recovery, fallback: 'Resolve the current decision blockers before acting.' }
}

function DecisionSnapshot({ decision, candidate }: { decision: CaseDecision; candidate: CandidateDecision }) {
  const action = candidateActionSummary(candidate, decision)
  return <section className={`decision-snapshot decision-snapshot-${candidate.outcome}`} aria-label="Current decision summary">
    <div className="decision-snapshot-heading"><span className="decision-kicker">DECISION SNAPSHOT</span><span className="decision-snapshot-note">Outcome shown above</span></div>
    <div className="decision-snapshot-grid">
      <div><span>Rationale</span><MemoNarrative value={candidate.reason ?? candidate.rationale ?? decision.rationale ?? decision.reason} fallback="No concise rationale is recorded." /></div>
      <div><span>{action.label}</span><MemoStrong value={action.value} fallback={action.fallback} /></div>
    </div>
  </section>
}

function lifecycleLabel(value: unknown) {
  const state = normalizeKey(value)
  if (state === 'recommended') return 'Recommended · unexecuted'
  if (state === 'held') return 'Held · user confirmed'
  if (state === 'declined') return 'Declined'
  if (state === 'closed') return 'Closed · user confirmed'
  if (state === 'watchlist') return 'Watchlist'
  return 'Lifecycle not recorded'
}

function statusTone(value: unknown) {
  const state = normalizeKey(value)
  if (['pass', 'complete', 'available', 'fresh', 'known'].includes(state)) return 'is-good'
  if (['fail', 'blocked', 'mismatch', 'stale', 'unknown', 'unavailable'].includes(state)) return 'is-warn'
  return ''
}

function displayPercent(value: unknown) {
  const raw = text(value)
  if (!raw) return null
  const numeric = Number(raw)
  if (!Number.isFinite(numeric)) return raw
  return `${(Math.abs(numeric) <= 1 ? numeric * 100 : numeric).toFixed(1)}%`
}

function MemoValue({ label, value, className = '' }: { label: string; value?: unknown; className?: string }) {
  const valueText = text(value)
  const cleanValue = valueText ? caseCardSummary(valueText) : valueText
  const privateValue = hasPrivateFinancialText(cleanValue)
  const visibleValue = <strong>{cleanValue ?? 'Not recorded in this revision'}</strong>
  return <div className={`manager-memo-value${className ? ` ${className}` : ''}`}><span>{label}</span>{privateValue ? <PrivateFinancialDisclosure label="Reveal account or policy note locally" description="This recorded note may contain account-dependent budgets, loss limits, shares, or allocation values. It stays closed until you choose to inspect it.">{visibleValue}</PrivateFinancialDisclosure> : visibleValue}</div>
}

function MemoNarrative({ value, fallback = 'Not recorded in this revision' }: { value?: unknown; fallback?: string }) {
  const valueText = caseCardSummary(text(value) ?? fallback)
  const content = <p>{valueText}</p>
  return hasPrivateFinancialText(valueText)
    ? <PrivateFinancialDisclosure label="Reveal account or policy note locally" description="This recorded note may contain account-dependent budgets, loss limits, shares, or allocation values. It stays closed until you choose to inspect it.">{content}</PrivateFinancialDisclosure>
    : content
}

function MemoStrong({ value, fallback = 'Not recorded in this revision' }: { value?: unknown; fallback?: string }) {
  const valueText = caseCardSummary(text(value) ?? fallback)
  const content = <strong>{valueText}</strong>
  return hasPrivateFinancialText(valueText)
    ? <PrivateFinancialDisclosure label="Reveal account or policy note locally" description="This recorded note may contain account-dependent budgets, loss limits, shares, or allocation values. It stays closed until you choose to inspect it.">{content}</PrivateFinancialDisclosure>
    : content
}

function MemoInline({ value, fallback = 'Not recorded in this revision' }: { value?: unknown; fallback?: string }) {
  const valueText = caseCardSummary(text(value) ?? fallback)
  const content = <span>{valueText}</span>
  return hasPrivateFinancialText(valueText)
    ? <PrivateFinancialDisclosure label="Reveal account or policy note locally" description="This recorded note may contain account-dependent budgets, loss limits, shares, or allocation values. It stays closed until you choose to inspect it.">{content}</PrivateFinancialDisclosure>
    : content
}

function QuestionNarrative({ value, fallback, privateByDefault = false }: { value?: unknown; fallback: string; privateByDefault?: boolean }) {
  const valueText = caseCardSummary(text(value) ?? fallback)
  const content = <p>{valueText}</p>
  const shouldHide = Boolean(text(value)) && (privateByDefault || hasPrivateFinancialText(valueText))
  return shouldHide
    ? <PrivateFinancialDisclosure label="Reveal this portfolio decision detail locally" description="Portfolio action answers, implications and related facts stay closed until you choose to inspect them on this device.">{content}</PrivateFinancialDisclosure>
    : content
}

function QuestionFactRow({ fact, onOpenSource, privateByDefault = false }: { fact: VerifiedQuestionFact; onOpenSource: DecisionWorkspaceProps['onOpenSource']; privateByDefault?: boolean }) {
  const sourceRef = fact.sourceRef
  const factText = [fact.claim, fact.value, fact.unit, fact.period].filter(Boolean).join(' · ')
  const isPrivate = privateByDefault || hasPrivateFinancialText(factText) || hasPrivateFinancialData({ value: fact.value, unit: fact.unit, period: fact.period })
  const content = <div className={`key-question-fact key-question-fact-${fact.role}`}>
    <div className="key-question-fact-head">
      <span>{fact.role === 'contradiction' ? 'Contradiction' : 'Support'}</span>
      {sourceRef ? <button type="button" className="key-question-source" onClick={() => void onOpenSource({ id: sourceRef, source_ref: sourceRef, title: 'Source for this fact', locator: fact.locator, as_of: fact.period })}><Icon name="book" size={12} /> Open source</button> : <small>Source unavailable</small>}
    </div>
    <p>{caseCardSummary(fact.claim ?? 'Source assertion not recorded.')}</p>
    {(fact.value || fact.unit || fact.period || fact.semanticStatus || fact.freshness) && <small className="key-question-fact-meta">{[fact.value, fact.unit, fact.period, fact.semanticStatus === 'supported' ? 'Source supports this fact' : fact.semanticStatus?.replaceAll('_', ' '), fact.freshness === 'fresh' ? 'Current for this review' : fact.freshness?.replaceAll('_', ' ')].filter(Boolean).join(' · ')}</small>}
  </div>
  return isPrivate
    ? <PrivateFinancialDisclosure label="Reveal selected portfolio fact locally" description="This selected fact may include account-dependent sizing, allocation or policy values. It stays closed until you choose to inspect it.">{content}</PrivateFinancialDisclosure>
    : content
}

function JointReviewPanel({ review }: { review: JointReview | null }) {
  if (!review) return <section className="joint-review-panel joint-review-unavailable"><div className="joint-review-heading"><div><span className="decision-kicker">LAYA + ASTRA REVIEW</span><h3>Joint review unavailable</h3></div><span className="joint-review-status is-warn">Unavailable</span></div><p>No Laya review was saved for this candidate. The available research remains below.</p></section>
  const layaAvailable = Boolean(review.layaModel || review.layaOutcome)
  const astraAvailable = Boolean(review.astraOutcome)
  const agreement = review.status === 'agreed' && review.disagreements.length === 0 && review.layaOutcome && review.astraOutcome && review.layaOutcome === review.astraOutcome
  const statusToneClass = review.status === 'agreed' || review.status === 'resolved' ? 'is-good' : review.status === 'pending' || review.status === 'unavailable' ? 'is-warn' : ''
  const layaConfidence = classificationConfidence(review.layaClassificationConfidence)
  const astraConfidence = classificationConfidence(review.astraClassificationConfidence)
  return <section className="joint-review-panel">
    <div className="joint-review-heading"><div><span className="decision-kicker">LAYA + ASTRA REVIEW</span><h3>Recorded judgments</h3></div><span className={`joint-review-status ${statusToneClass}`}>{jointStatusLabel(review.status)}</span></div>
    <p className="joint-review-line">Laya: <strong>{layaAvailable ? jointOutcomeLabel(review.layaOutcome) : 'Unavailable'}</strong> · Astra: <strong>{astraAvailable ? jointOutcomeLabel(review.astraOutcome) : 'Unavailable'}</strong> · {jointStatusLabel(review.status)}</p>
    <p className="joint-review-note">{agreement ? 'The joint review is complete and both models agree.' : review.disagreements.length ? 'An unresolved disagreement needs review.' : 'Model agreement does not establish that an investment will succeed.'}</p>
    <MemoNarrative value={review.resolution} fallback="No reconciliation rationale is recorded." />
    {review.disagreements.length > 0 && <ul className="joint-review-disagreements">{review.disagreements.map((item, index) => <li key={`${item.questionKey ?? 'case'}-${index}`}><strong>{item.questionKey ? questionTitle(item.questionKey) : 'Case review'}:</strong> <MemoInline value={item.reason} fallback="Reason not recorded." /></li>)}</ul>}
    <details className="joint-review-details"><summary><span>Review details</span><small>{review.reviewedAt ? dateLabel(review.reviewedAt) : 'Timestamp unavailable'}</small></summary><div className="joint-review-models"><div className="joint-review-model"><span>Laya classifier</span><small>{review.layaModel ? `Model ${review.layaModel}` : 'Model unavailable'}{review.layaRevision ? ` · revision ${review.layaRevision}` : ''}</small>{layaConfidence && <small>Classification confidence: {layaConfidence}</small>}</div><div className="joint-review-model"><span>Astra verdict</span><small>Existing CIO assessment</small>{astraConfidence && <small>Classification confidence: {astraConfidence}</small>}</div></div>{review.reviewedAt && <small className="joint-review-reviewed">Reviewed {dateLabel(review.reviewedAt)}</small>}</details>
  </section>
}

function KeyQuestionCard({ question, onOpenSource }: { question: KeyQuestion; onOpenSource: DecisionWorkspaceProps['onOpenSource'] }) {
  const privateByDefault = question.key === 'portfolio_action'
  return <article className={`key-question-card key-question-${question.key}`}>
    <div className="key-question-card-head"><div><span className="key-question-number">{KEY_QUESTION_ORDER.indexOf(question.key) + 1}</span><h3>{questionTitle(question.key)}</h3></div><span className={`key-question-status key-question-status-${question.evidenceStatus}`}>{questionEvidenceLabel(question.evidenceStatus)}</span></div>
    <p className="key-question-prompt">{question.question}</p>
    <div className="key-question-answer"><span>Research answer</span><QuestionNarrative value={question.answer} fallback="No answer recorded." privateByDefault={privateByDefault} /></div>
    <div className="key-question-answer"><span>Decision implication</span><QuestionNarrative value={question.decisionImplication} fallback="No implication recorded." privateByDefault={privateByDefault} /></div>
    <div className="key-question-facts"><span>Key evidence</span>{question.verifiedFacts.length > 0 ? question.verifiedFacts.map((fact, index) => <QuestionFactRow key={`${fact.factId ?? 'fact'}-${index}`} fact={fact} onOpenSource={onOpenSource} privateByDefault={privateByDefault} />) : <p className="key-question-empty">No verified supporting fact is available for this question.</p>}</div>
    {question.unknowns.length > 0 && <div className="key-question-unknowns"><span>Material unknowns</span><ul>{question.unknowns.map((item, index) => <li key={`${item}-${index}`}><QuestionNarrative value={item} fallback="Unknown not recorded." privateByDefault={privateByDefault} /></li>)}</ul></div>}
  </article>
}

function KeyQuestionSummary({ candidate, onOpenSource }: { candidate: CandidateDecision; onOpenSource: DecisionWorkspaceProps['onOpenSource'] }) {
  if (!hasFiveQuestionReview(candidate)) return null
  return <section className="key-question-summary"><div className="key-question-summary-heading"><div><span className="decision-kicker">FIVE-QUESTION CASE</span><h2>Decision questions</h2><p>Five questions behind this decision. The answers are research judgments; open a source to check the evidence.</p></div><span className="key-question-count">5 questions</span></div><div className="key-question-grid">{candidate.keyQuestions.map((question) => <KeyQuestionCard key={question.key} question={question} onOpenSource={onOpenSource} />)}</div><JointReviewPanel review={candidate.jointReview} /></section>
}

function LegacyCaseNotice({ candidate }: { candidate: CandidateDecision | null }) {
  if (!candidate || hasFiveQuestionReview(candidate)) return null
  const contract = normalizeKey(candidate.researchContract)
  const incomplete = contract === 'five_questions.v1'
  return <div className="legacy-case-notice"><Icon name="book" size={15} /><div><strong>{incomplete ? 'Five-question review incomplete' : 'Earlier research · Laya was not used'}</strong><p>{incomplete ? 'Some of the five answers are missing. Review the available analysis and unresolved inputs below.' : 'This decision was saved before joint reviews were added. Its original research remains available and has not been rerun.'}</p></div></div>
}

function ScenarioTable({ candidate }: { candidate: CandidateDecision }) {
  const valuation = candidate.valuation
  const payoff = candidate.payoff
  const valuationScenarios = record(valuation?.scenarios)
  const payoffRows = list(payoff?.scenarios).map(record).filter((row): row is RecordValue => Boolean(row))
  const labels = ['bear', 'base', 'bull']
  const rows = labels.map((name) => {
    const value = valuationScenarios?.[name]
    const payoffRow = payoffRows.find((row) => normalizeKey(row.name) === name)
    return { name, value: money(payoffRow?.target_price ?? value, valuation?.currency ?? candidate.currency), return: displayPercent(payoffRow?.price_return), netReturn: displayPercent(payoffRow?.net_return) }
  }).filter((row) => row.value || row.return || row.netReturn)
  if (!rows.length) return <p className="manager-memo-empty">Bear/base/bull valuation or payoff is not recorded in this revision.</p>
  return <><div className="manager-scenario-table" role="table" aria-label="Bear base bull valuation and payoff"><div className="manager-scenario-row manager-scenario-heading" role="row"><span>Scenario</span><span>Value</span><span>Price return</span><span>After costs</span></div>{rows.map((row) => <div className="manager-scenario-row" role="row" key={row.name}><strong>{row.name}</strong><span>{row.value ?? 'Not recorded'}</span><span>{row.return ?? 'Not recorded'}</span><span>{row.netReturn ?? 'Unavailable'}</span></div>)}</div><p className="manager-memo-note">Conditional scenarios, not probabilities. Price returns exclude costs; an after-cost return requires recorded cost assumptions.</p></>
}

function PortfolioImpact({ candidate }: { candidate: CandidateDecision }) {
  const context = candidate.portfolioContext
  if (!context) return <p className="manager-memo-empty">Portfolio impact is not recorded in this revision.</p>
  const checks = list(context.checks).map(record).filter((item): item is RecordValue => Boolean(item))
  const exposures = list(context.exposures).map(record).filter((item): item is RecordValue => Boolean(item))
  const missingInputs = strings(context.missing_inputs)
  const privateValues = [context.before, context.after, exposures]
  const hasPrivateValues = privateValues.some((value) => hasPrivateFinancialData(value, true)) || checks.some((check) => hasPrivateFinancialText(JSON.stringify(check)))
  const summary = <div className="manager-portfolio-impact"><div className="manager-check-grid"><MemoValue label="Readiness" value={context.status} className={statusTone(context.status)} /><MemoValue label="Snapshot" value={context.snapshot_id} /><MemoValue label="As of" value={context.as_of} /><MemoValue label="Base currency" value={context.base_currency} /></div>{checks.length > 0 && <div className="manager-check-list">{checks.slice(0, 8).map((check, index) => <div key={`${String(check.key ?? 'check')}-${index}`}><span>{text(check.key) ?? 'Portfolio check'}</span><strong className={statusTone(check.status)}>{text(check.status) ?? 'Unavailable'}</strong><small><MemoInline value={check.reason} fallback="Reason not recorded" /></small></div>)}</div>}{exposures.length > 0 && <p className="manager-memo-note">{exposures.length} frozen exposure record{exposures.length === 1 ? '' : 's'} retained for overlap and marginal impact review.</p>}{missingInputs.length > 0 && <p className="manager-memo-note manager-memo-warning">Unknowns: {missingInputs.join(' · ')}</p>}</div>
  return hasPrivateValues ? <PrivateFinancialDisclosure label="Reveal marginal exposures locally" description="Frozen account amounts, holdings, shares, and allocation values stay hidden until you choose to inspect them on this device.">{summary}</PrivateFinancialDisclosure> : summary
}

function OpportunityCost({ candidate }: { candidate: CandidateDecision }) {
  const projection = record(candidate.payoff?.opportunity_cost)
  const benchmark = firstText(projection?.benchmark_ticker, candidate.benchmarkTicker)
  const benchmarkReason = firstText(projection?.benchmark_rationale, candidate.benchmarkRationale)
  return <section className="manager-opportunity-cost"><span className="decision-kicker">ALTERNATIVES TO THIS POSITION</span><div className="manager-memo-grid"><MemoValue label="Retain cash · same horizon" value={displayPercent(projection?.cash_expected_return ?? projection?.expected_return) ?? 'Unavailable'} /><MemoValue label="Cash comparison status" value={projection?.status ?? 'Unavailable'} /><MemoValue label="Benchmark" value={benchmark ?? 'Not selected'} /></div><MemoNarrative value={projection?.reason} fallback="Frozen cash-rate terms and a comparable horizon are needed to calculate the cash alternative." />{text(projection?.rate_assumption) && <MemoNarrative value={projection?.rate_assumption} />}{benchmarkReason && <MemoNarrative value={benchmarkReason} />}<MemoNarrative value={projection?.benchmark_reason} fallback="A supported future benchmark return is unavailable; historical returns are not a forecast." />{projection && <PrivateFinancialDisclosure label="Reveal cash calculation locally" description="This calculation uses the frozen account terms and proposed allocation; it does not move money."><KeyValueList values={projection} empty="Cash calculation unavailable" /></PrivateFinancialDisclosure>}</section>
}

function LifecycleControls({ runId, candidate, onRecordLifecycle }: { runId: string; candidate: CandidateDecision; onRecordLifecycle?: DecisionWorkspaceProps['onRecordLifecycle'] }) {
  const [requestedState, setRequestedState] = useState<'held' | 'closed' | 'watchlist' | null>(null)
  const [reason, setReason] = useState('')
  const [confirmed, setConfirmed] = useState(false)
  const stableCandidateKey = /^[a-f0-9]{24}$/i.test(candidate.key)
  if (!onRecordLifecycle || !stableCandidateKey) return null
  const current = normalizeKey(candidate.lifecycleState)
  const eligible = ['recommended', 'watchlist', 'held', 'declined', 'closed'].includes(current)
  if (!eligible && !requestedState) return null
  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!requestedState || !reason.trim() || (requestedState !== 'watchlist' && !confirmed)) return
    await onRecordLifecycle({ run_id: runId, candidate_key: candidate.key, state: requestedState, reason: reason.trim(), confirmed: requestedState === 'watchlist' ? true : confirmed })
    setRequestedState(null)
    setReason('')
    setConfirmed(false)
  }
  return <section className="manager-lifecycle-controls"><div className="manager-section-title"><span className="decision-kicker">LIFECYCLE</span><strong>{lifecycleLabel(candidate.lifecycleState)}</strong></div>{candidate.nextAction && <p className="manager-memo-note">Next action: {candidate.nextAction}</p>}{!requestedState ? <div className="manager-lifecycle-actions"><button type="button" className="button button-subtle" onClick={() => setRequestedState('watchlist')}>Record watchlist</button><button type="button" className="button button-subtle" onClick={() => setRequestedState('held')}>Confirm held</button><button type="button" className="button button-subtle" onClick={() => setRequestedState('closed')}>Confirm closed</button></div> : <form className="manager-lifecycle-form" onSubmit={(event) => void submit(event)}><label><span>New state</span><select value={requestedState} onChange={(event) => setRequestedState(event.target.value as 'held' | 'closed' | 'watchlist')}><option value="watchlist">Watchlist</option><option value="held">Held</option><option value="closed">Closed</option></select></label><label><span>Reason</span><textarea value={reason} onChange={(event) => setReason(event.target.value)} placeholder="Record the user-confirmed transaction or lifecycle rationale" rows={2} /></label>{requestedState !== 'watchlist' && <label className="manager-confirm-row"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} /><span>I confirm this holding or closure reflects a user-confirmed execution record.</span></label>}<div className="manager-lifecycle-actions"><button type="submit" className="button button-primary" disabled={!reason.trim() || (requestedState !== 'watchlist' && !confirmed)}>Save lifecycle</button><button type="button" className="button button-subtle" onClick={() => setRequestedState(null)}>Cancel</button></div></form>}</section>
}

function ManagerMemo({ run, decision, candidate, sources, onOpenSource, onRecordLifecycle }: { run: RunDetail; decision: CaseDecision; candidate: CandidateDecision | null; sources: EvidenceRef[]; onOpenSource: DecisionWorkspaceProps['onOpenSource']; onRecordLifecycle?: DecisionWorkspaceProps['onRecordLifecycle'] }) {
  if (!candidate) return <section className="manager-memo"><div className="manager-memo-header"><div><span className="decision-kicker">MANAGER MEMO</span><h2>Decision pending</h2></div><DecisionOutcomeBadge value={decision.outcome} /></div><p className="manager-memo-empty">No candidate decision is recorded in this revision. Research and funding readiness remain separate from the investment outcome.</p></section>
  const thesisRefs = citations(candidate.citations)
  const valuation = candidate.valuation
  const payoff = candidate.payoff
  const gate = candidate.recommendationGate
  const blockerPriority = (gap: MissingGap) => {
    const label = normalizeKey(`${String(gap.key ?? '')} ${String(gap.description ?? '')}`)
    if (/(evidence|source|citation|fact|thesis|valuation|payoff|support|objection|research|quote|catalyst)/.test(label)) return 0
    if (/(watch|trigger|review|lifecycle)/.test(label)) return 1
    if (/(sizing|portfolio|fund|cash|account|allocation|risk)/.test(label)) return 3
    return 2
  }
  const topBlocker = [...candidate.materialBlockers].sort((left, right) => blockerPriority(left) - blockerPriority(right))[0] ?? null
  const reviewAt = candidate.reviewDueAt ?? (candidate.actionPlan && firstText(record(candidate.actionPlan)?.review_at))
  const quote = candidate.currentQuote
  const quoteAsOf = firstText(quote?.as_of)
  const quoteSourceId = text(quote?.source_ref ?? quote?.source_id)
  const quoteObservationStatus = text(quote?.observation_status ?? quote?.status)
  const quoteRetained = normalizeKey(quote?.observation_status) === 'retained_previous'
  const distance = candidate.distanceToEntry
  const rewardRisk = formatNumber(payoff?.reward_to_risk, 2)
  const blockerDescription = topBlocker ? safeCardText(topBlocker.description ?? topBlocker.key, 'Blocker not described') : null
  const blockerAction = topBlocker ? safeCardText(topBlocker.action ?? topBlocker.reopen_when, 'Next action not recorded') : null
  return <section className="manager-memo"><div className="manager-memo-header"><div><span className="decision-kicker">MANAGER MEMO · REVISION {decision.revision ?? 'unknown'}</span><h2>{candidate.instrument}{candidate.name ? ` · ${candidate.name}` : ''}</h2><p>{candidate.direction ?? 'Direction not recorded'} · {candidate.horizon ?? 'Horizon not recorded'}</p></div></div><MemoNarrative value={candidate.reason ?? decision.reason} fallback="No decision rationale is recorded." /><div className="manager-memo-readiness"><MemoValue label="Decision as of" value={decision.asOf ? formatDateTime(decision.asOf) : 'Not recorded'} /><MemoValue label="Quote update" value={quoteAsOf ? `${formatDateTime(quoteAsOf)}${text(quote?.freshness) ? ` · ${text(quote?.freshness)}` : ''}` : 'Not recorded'} /><MemoValue label="Next review" value={reviewAt ? `${formatCalendarDate(reviewAt)}${candidate.overdue ? ' · overdue' : ''}` : 'Not recorded'} className={candidate.overdue ? 'is-warn' : ''} /></div><div className="manager-memo-note">{quote ? <><span>Current quote: {text(quote.price) ?? 'Unavailable'} {text(quote.currency) ?? ''} · {text(quote.kind) ?? 'observation'}{text(distance?.basis) ? ` · ${text(distance?.basis)}` : ''}.</span>{quoteObservationStatus && <span> Observation: {quoteObservationStatus.replaceAll('_', ' ')}{quoteRetained ? ' · prior quote retained' : ''}.</span>}{quoteSourceId && <button type="button" className="button button-subtle manager-quote-source" onClick={() => void onOpenSource({ id: quoteSourceId, source_ref: quoteSourceId, title: 'Price observation', as_of: quoteAsOf, source_version: text(quote?.source_version), freshness: text(quote?.freshness) })}><Icon name="book" size={12} /> Open quote source</button>}</> : 'Current quote not recorded in this revision.'}</div><div className="manager-memo-grid"><MemoValue label="Variant view" value={candidate.variantView} /><MemoValue label="Why now" value={candidate.whyNow} /><MemoValue label="Market expectation" value={candidate.marketExpectation} /><MemoValue label="Entry condition" value={candidate.entryCondition ?? (candidate.actionPlan && firstText(record(candidate.actionPlan)?.entry_condition))} /><MemoValue label="Exit / valuation" value={candidate.exitLogic ?? (candidate.actionPlan && firstText(record(candidate.actionPlan)?.exit_condition))} /><MemoValue label="Invalidation" value={candidate.invalidation.join(' · ') || (candidate.actionPlan && firstText(record(candidate.actionPlan)?.invalidation_condition))} /></div><div className="manager-memo-columns"><div><span className="decision-kicker">STRONGEST OPPOSING CASE</span><MemoNarrative value={candidate.opposingExplanation} />{candidate.disconfirmingEvidence.length > 0 && <><span className="decision-kicker">DISCONFIRMING EVIDENCE</span><ul>{candidate.disconfirmingEvidence.map((item, index) => <li key={index}><MemoNarrative value={item} /></li>)}</ul></>}</div><div><span className="decision-kicker">VALUATION / PAYOFF</span><p>{valuation?.status ? `Valuation ${String(valuation.status)}` : 'Valuation status not recorded'}{valuation?.selected_method ? ` · ${String(valuation.selected_method)}` : ''}</p><ScenarioTable candidate={candidate} />{rewardRisk && <p className="manager-memo-note">Reward/risk: {rewardRisk}</p>}</div></div>{candidate.recommendationGate && <details className="decision-details"><summary>Recommendation checks <small>{text(gate?.status) ?? 'blocked'}</small></summary><div className="manager-gate"><div className="manager-section-title"><span className="decision-kicker">RECOMMENDATION GATE</span><strong className={statusTone(gate?.status)}>{text(gate?.status) ?? 'blocked'}</strong></div>{list(gate?.checks).slice(0, 8).map((check, index) => { const row = record(check); return <div key={`${String(row?.key ?? 'check')}-${index}`}><span>{text(row?.key) ?? 'Requirement'}</span><strong className={statusTone(row?.status)}>{text(row?.status) ?? 'unavailable'}</strong><small>{safeCardText(row?.reason, 'Reason not recorded')}</small></div> })}{strings(gate?.missing_inputs).length > 0 && <p className="manager-memo-note manager-memo-warning">Missing: {strings(gate?.missing_inputs).map((item) => safeCardText(item, 'Input not recorded')).join(' · ')}</p>}</div></details>}{topBlocker && <div className="manager-top-blocker"><span className="decision-kicker">TOP BLOCKER</span><MemoStrong value={blockerDescription} fallback="Blocker not described" /><span>{safeCardText(topBlocker.owner ?? topBlocker.relevant_role, 'Owner not recorded')} · {blockerAction}</span></div>}<OpportunityCost candidate={candidate} /><PortfolioImpact candidate={candidate} /><LifecycleControls runId={run.id} candidate={candidate} onRecordLifecycle={onRecordLifecycle} />{thesisRefs.length > 0 && <SourceCitationDetails citations={enrichCitations(thesisRefs, sources)} onOpenSource={onOpenSource} label="Show memo sources" />}</section>
}

function CandidateComparison({ candidates, allocationMode, onSelect }: { candidates: CandidateDecision[]; allocationMode: string | null; onSelect: (index: number) => void }) {
  if (candidates.length < 2) return null
  const readinessRank = (item: CandidateDecision) => {
    const gate = normalizeKey(item.recommendationGate?.status)
    if (item.outcome === 'recommend' && gate === 'pass') return 0
    if (item.outcome === 'recommend') return 1
    if (item.outcome === 'watchlist') return 2
    if (item.outcome === 'pending' || item.outcome === 'awaiting_info') return 3
    if (item.outcome === 'decline') return 4
    return 5
  }
  const timestamp = (value: unknown) => {
    const parsed = Date.parse(String(value ?? ''))
    return Number.isFinite(parsed) ? parsed : Number.MAX_SAFE_INTEGER
  }
  const sorted = candidates.map((item, index) => ({ item, index })).sort((left, right) => readinessRank(left.item) - readinessRank(right.item) || timestamp(left.item.reviewDueAt ?? left.item.decisionAsOf) - timestamp(right.item.reviewDueAt ?? right.item.decisionAsOf) || left.item.instrument.localeCompare(right.item.instrument))
  const readinessLabel = (item: CandidateDecision) => item.outcome === 'recommend' && normalizeKey(item.recommendationGate?.status) === 'pass' ? 'Ready gate passed' : item.outcome === 'recommend' ? 'Recommended · gate not fully recorded' : item.outcome === 'watchlist' ? 'Watch condition' : item.outcome === 'decline' ? 'Declined' : 'Decision pending'
  return <section className="manager-comparison"><div className="manager-section-title"><div><span className="decision-kicker">CANDIDATE COMPARISON</span><h3>{allocationMode === 'alternatives' ? 'Alternatives · choose one' : 'Combined basket · shared constraints'}</h3></div><span className="muted-copy">Sorted by recorded readiness, review date, and instrument. No invented score.</span></div><div className="manager-comparison-grid">{sorted.map(({ item, index }) => <button type="button" className="manager-comparison-card" key={item.key} onClick={() => onSelect(index)}><div><strong>{item.instrument}</strong><DecisionOutcomeBadge value={item.outcome} /></div><span>{item.direction ?? 'Direction not recorded'} · {item.horizon ?? 'Horizon not recorded'}</span><p>{safeCardText(item.variantView ?? item.reason, 'Rationale not recorded in this revision')}</p><p>Entry: {safeCardText(item.entry, 'Unavailable')} · Base value: {safeCardText(item.target, 'Unavailable')}</p><p>Invalidation: {safeCardText(item.invalidation[0], 'Unavailable')}</p><p>Portfolio: {text(item.portfolioContext?.status) ?? 'Unavailable'} · Main unknown: {safeCardText(item.materialBlockers[0]?.description ?? item.unknowns[0], 'None recorded')}</p><p>Benchmark: {item.benchmarkTicker ?? 'Not selected'} · {safeCardText(item.benchmarkRationale, 'Rationale unavailable')}</p><div><small>Readiness</small><strong>{readinessLabel(item)}</strong><small>Review</small><strong>{item.reviewDueAt ? compactDate(item.reviewDueAt) : 'Not recorded'}</strong></div></button>)}</div></section>
}

function LearningSummary({ learning }: { learning?: LearningSnapshot | null }) {
  if (!learning) return null
  const summary = learning.summary ?? {}
  const observationTime = (item: Record<string, unknown>) => Date.parse(String(item.evaluation_at ?? '')) || 0
  const latest = learning.items.flatMap((item) => (item.observations ?? []).map((observation) => ({ ...observation, ticker: item.ticker, outcome: item.outcome }))).sort((left, right) => observationTime(right) - observationTime(left)).slice(0, 5)
  const baselineRows = learning.items.slice(0, 12)
  const benchmarkUnavailable = learning.items.filter((item) => (item.observations ?? []).some((observation) => observation.benchmark_comparable === false || !observation.benchmark)).length
  return <section className="manager-learning"><div className="manager-section-title"><div><span className="decision-kicker">LEARNING JOURNAL</span><h3>Frozen paper outcomes</h3></div><span className="muted-copy">Read-only · no actual trading performance inferred</span></div><div className="manager-learning-metrics"><MemoValue label="Baselines" value={summary.baselines} /><MemoValue label="Initial cohort" value={summary.initial_idea_cohort} /><MemoValue label="Unevaluated" value={summary.unevaluated} /><MemoValue label="Matured comparable" value={summary.matured_comparable} /><MemoValue label="Unavailable" value={summary.unavailable} /><MemoValue label="Declined" value={summary.declined_baselines} /><MemoValue label="Benchmark unavailable" value={benchmarkUnavailable} /><MemoValue label="Missed opportunities" value={summary.missed_opportunities} /></div>{summary.mean_excess_return != null && <p className="manager-memo-note">Mean benchmark excess return: {displayPercent(summary.mean_excess_return)} · only frozen, comparable, matured observations are included.</p>}{summary.cohort_basis && <p className="manager-learning-method">Cohort: {String(summary.cohort_basis)}</p>}<details className="decision-details"><summary>Show case revision baselines <small>{baselineRows.length}{learning.items.length > baselineRows.length ? ` of ${learning.items.length}` : ''}</small></summary>{baselineRows.length ? <div className="manager-learning-list">{baselineRows.map((item, index) => { const observations = item.observations ?? []; const latestObservation = [...observations].sort((left, right) => observationTime(right) - observationTime(left))[0]; const sourceCount = list(item.source_refs).length || list(latestObservation?.source_refs).length; const benchmarkState = latestObservation ? latestObservation.benchmark_comparable === true ? 'comparable' : 'unavailable' : item.benchmark_ticker ? `frozen · ${item.benchmark_ticker}` : 'unavailable'; return <article key={`${String(item.id ?? item.run_id ?? 'baseline')}-${index}`}><strong>{item.ticker ?? 'Candidate unavailable'} · {item.outcome ?? 'outcome unavailable'}</strong><span>Revision {item.decision_revision ?? 'unknown'} · decision {item.decision_as_of ?? 'date unknown'} · {observations.length} observation{observations.length === 1 ? '' : 's'}</span><p>Benchmark {benchmarkState} · maturity {latestObservation?.maturity ?? 'unevaluated'} · {sourceCount} frozen source{sourceCount === 1 ? '' : 's'}</p><p>Research effort: {text(record(item.research_effort)?.attempts) ?? 'Unknown'} attempts · {text(record(item.research_effort)?.measured_tokens) ?? 'Unmeasured'} tokens</p></article> })}</div> : <p className="decision-empty-inline">No frozen decision baselines have been recorded.</p>}</details><details className="decision-details"><summary>Show latest outcome observations <small>{latest.length} shown</small></summary>{latest.length ? <div className="manager-learning-list">{latest.map((item, index) => <article key={`${String(item.id ?? 'outcome')}-${index}`}><strong>{item.ticker ?? 'Candidate unavailable'} · {item.kind ?? 'observation'}</strong><span>{item.status ?? 'status unavailable'} · evaluated {item.evaluation_at ?? 'date unknown'} · {item.maturity ?? 'maturity unknown'}</span><p>{item.excess_return != null ? `Excess return ${displayPercent(item.excess_return)}` : 'Benchmark-relative outcome unavailable'}{item.missed_opportunity ? ' · missed opportunity' : ''}</p><p>Price return {displayPercent(record(item.instrument)?.return) ?? 'unavailable'} · drawdown {displayPercent(record(item.instrument)?.drawdown) ?? 'unavailable'} · benchmark return {displayPercent(record(item.benchmark)?.return) ?? 'unavailable'}</p><p>Thesis: {text(item.thesis_result) ?? 'unknown'} · catalyst: {text(item.catalyst_result) ?? 'unknown'}</p>{text(record(item.instrument)?.reason) && <p>{text(record(item.instrument)?.reason)}</p>}</article>)}</div> : <p className="decision-empty-inline">No outcome observations have been recorded.</p>}</details>{learning.method && <p className="manager-learning-method">Method: {learning.method}</p>}</section>
}

function caseCardSummary(value: string) {
  const sourceToken = /(?:[,;:]\s*)?\bsrc_[a-z0-9]+(?:\s*:\s*(?:L(?:ines?)?\s*)?\d+(?:\s*[-–—]\s*(?:L(?:ines?)?\s*)?\d+)?)?/gi
  return value
    .replace(sourceToken, '')
    .replace(/\(\s*[,;:·|/\\-]*\s*\)/g, '')
    .replace(/\[\s*[,;:·|/\\-]*\s*\]/g, '')
    .replace(/([,;:])\s*([)\]])/g, '$2')
    .replace(/\s+([,.;:!?%)\]])/g, '$1')
    .replace(/\s+/g, ' ')
    .trim()
}

function safeCardText(value: unknown, fallback: string) {
  const raw = firstText(value)
  if (!raw) return fallback
  const clean = caseCardSummary(raw)
  return hasPrivateFinancialText(clean) ? 'Account-dependent note hidden by default.' : clean
}

function watchTriggerRecords(candidate: RecordValue) {
  const raw = candidate.watch_triggers ?? candidate.triggers ?? candidate.watch_trigger
  const values = Array.isArray(raw) ? raw : raw == null ? [] : [raw]
  return values.map(record).filter((item): item is RecordValue => Boolean(item))
}

function watchAmount(value: unknown) {
  const raw = firstText(value)
  if (!raw) return null
  const number = Number(raw.replaceAll(',', ''))
  return Number.isFinite(number) ? number.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 6 }) : raw
}

function watchPriceCondition(item: WatchlistItem, candidate: RecordValue, triggers: RecordValue[]) {
  const price = triggers.find((trigger) => normalizeKey(trigger.type ?? trigger.kind) === 'price')
  if (!price) return null
  const currency = firstText(price.currency, candidate.currency)
  const low = watchAmount(price.threshold ?? price.value ?? price.price)
  const high = watchAmount(price.upper_threshold ?? price.high ?? price.upper)
  const operator = normalizeKey(price.operator)
  const symbol = operator === 'at_or_below' ? '≤' : operator === 'at_or_above' ? '≥' : operator === 'between' ? 'Between' : operator === 'crosses' ? 'Crosses' : null
  const amount = low && high ? `${low}–${high}` : low ?? high
  if (amount) return `${symbol ? `${symbol} ` : ''}${currency ? `${currency} ` : ''}${amount}`
  return firstText(price.condition, price.reopen_when, item.title)
}

type WatchNextDate = { value: string; label: 'Next review' | 'Next catalyst' }

function watchDateSortValue(value: unknown) {
  const raw = firstText(value)
  if (!raw) return null
  const dateOnly = /^(\d{4})-(\d{2})-(\d{2})$/.exec(raw)
  const parsed = dateOnly
    ? new Date(Number(dateOnly[1]), Number(dateOnly[2]) - 1, Number(dateOnly[3]))
    : new Date(raw)
  const time = parsed.getTime()
  return Number.isFinite(time) ? time : null
}

function watchReviewDate(item: WatchlistItem, candidate: RecordValue, triggers: RecordValue[]): WatchNextDate | null {
  const primary = triggers.find((trigger) => normalizeKey(trigger.type ?? trigger.kind) === 'price')
  const review = firstText(primary?.review_at, primary?.trigger_date, candidate.review_at, candidate.next_review_at, item.next_review_at)
  if (review) return { value: review, label: 'Next review' }

  // A saved non-catalyst check can carry the review date even when the
  // watchlist item has no top-level next_review_at. Catalyst dates stay tied
  // to their event date below so the card does not turn an event into a later
  // administrative review date.
  const triggerReview = triggers.find((trigger) => normalizeKey(trigger.type ?? trigger.kind) !== 'catalyst' && firstText(trigger.review_at))
  const triggerReviewDate = firstText(triggerReview?.review_at)
  if (triggerReviewDate) return { value: triggerReviewDate, label: 'Next review' }

  const today = new Date()
  today.setHours(0, 0, 0, 0)
  const catalystDates = triggers
    .filter((trigger) => normalizeKey(trigger.type ?? trigger.kind) === 'catalyst')
    .map((trigger) => {
      const value = firstText(trigger.trigger_date, trigger.event_date, trigger.date)
      const time = watchDateSortValue(value)
      return value && time != null ? { value, time } : null
    })
    .filter((entry): entry is { value: string; time: number } => Boolean(entry))
    .filter((entry) => entry.time >= today.getTime())
    .sort((left, right) => left.time - right.time)
  const nextCatalyst = catalystDates[0]
  return nextCatalyst ? { value: nextCatalyst.value, label: 'Next catalyst' } : null
}

function watchCheckSummary(_candidate: RecordValue, triggers: RecordValue[]) {
  const other = triggers.filter((trigger) => normalizeKey(trigger.type ?? trigger.kind) !== 'price')
  const catalystCount = other.filter((trigger) => normalizeKey(trigger.type ?? trigger.kind) === 'catalyst').length
  const evidenceCount = other.filter((trigger) => normalizeKey(trigger.type ?? trigger.kind) === 'evidence').length
  if (!other.length) return 'No additional checks recorded'
  const parts = [`${other.length} other check${other.length === 1 ? '' : 's'}`]
  if (catalystCount) parts.push(`${catalystCount} catalyst`)
  if (evidenceCount) parts.push(`${evidenceCount} evidence`)
  return parts.join(' · ')
}

function watchPreview(item: WatchlistItem, candidate: RecordValue) {
  const raw = firstText(candidate.reason, candidate.rationale, candidate.summary, candidate.conclusion, item.question, item.title)
  if (!raw) return 'A saved condition is waiting for its next review.'
  const clean = caseCardSummary(raw).replace(/^canonical outcome input:\s*/i, '').trim()
  if (hasPrivateFinancialText(clean)) return 'Account-dependent note hidden by default.'
  return clean.length > 180 ? `${clean.slice(0, 177).trimEnd()}…` : clean
}

function canonicalDecisionPreview(decision: CaseDecision, candidate: CandidateDecision, preferCase = false) {
  const raw = preferCase
    ? firstText(decision.rationale, decision.reason, candidate.rationale, candidate.reason)
    : firstText(candidate.rationale, candidate.reason, decision.rationale, decision.reason)
  const clean = raw ? caseCardSummary(raw).replace(/^canonical outcome input:\s*/i, '').trim() : ''
  if (hasPrivateFinancialText(clean)) return 'Account-dependent note hidden by default.'
  if (clean) return clean.length > 190 ? `${clean.slice(0, 187).trimEnd()}…` : clean
  return candidate.outcome === 'watchlist'
    ? `Watchlist conditions are recorded for ${candidate.instrument}.`
    : candidateHeadline(candidate)
}

function watchIdentity(item: WatchlistItem, candidate: RecordValue) {
  const ticker = firstText(candidate.ticker, candidate.symbol, item.ticker, candidate.instrument) ?? 'Ticker unavailable'
  const company = firstText(candidate.name, candidate.company_name, candidate.issuer_name)
  return company ? `${ticker} · ${company}` : ticker
}

function WatchlistCard({ item, selected, onSelect }: { item: WatchlistItem; selected: boolean; onSelect: () => void }) {
  const candidate = record(item.candidate) ?? {}
  const triggers = watchTriggerRecords(candidate)
  const price = watchPriceCondition(item, candidate, triggers)
  const nextDate = watchReviewDate(item, candidate, triggers)
  const stale = Boolean(item.stale)
  const lifecycle = firstText(item.lifecycle_state, item.state, item.status) ?? 'watchlist'
  const canonicalOutcome = candidate.outcome === undefined ? null : candidate.outcome
  const badge = outcome(canonicalOutcome, false, true)
  const quote = record(item.current_quote)
  const distance = record(item.distance_to_entry)
  const quoteObservationStatus = text(quote?.observation_status ?? quote?.status)
  const quoteLabel = quote ? `${text(quote.price) ?? 'Unavailable'} ${text(quote.currency) ?? ''} · ${text(quote.as_of) ?? 'date unknown'} · ${text(quote.freshness) ?? 'freshness unknown'}${quoteObservationStatus ? ` · ${quoteObservationStatus.replaceAll('_', ' ')}` : ''}` : 'Current quote unavailable'
  const distanceLabel = distance ? `${text(distance.absolute) ?? 'unavailable'}${distance.percent ? ` (${displayPercent(distance.percent)})` : ''}${distance.inside_range === true ? ' · inside entry range' : ''}` : null
  return <button type="button" className={`decision-card${selected ? ' is-selected' : ''}`} onClick={onSelect} aria-label={`Open ${lifecycleLabel(lifecycle)} case ${watchIdentity(item, candidate)}`}>
    <div className="decision-card-top"><span className="decision-card-origin"><Icon name="clock" size={13} />{lifecycleLabel(lifecycle)}</span><DecisionOutcomeBadge value={badge} /></div>
    <strong className="decision-card-title">{watchIdentity(item, candidate)}</strong>
    {price && <div className="decision-card-idea"><span>Primary price condition</span><p>{price}</p></div>}
    <div className="decision-card-watch-meta"><span>{quoteLabel}</span>{distanceLabel && <span>Distance {distanceLabel}</span>}<span>{nextDate ? `${nextDate.label} ${compactDate(nextDate.value)}` : item.review_due_at ? `Review ${compactDate(item.review_due_at)}` : 'Next review not recorded'}</span><span>{watchCheckSummary(candidate, triggers)}</span></div>
    <p className="decision-card-reason">{watchPreview(item, candidate)}</p>
    {item.overdue && <div className="decision-card-state decision-card-stale"><Icon name="warning" size={13} /><span>Review overdue</span></div>}
    {item.paused && <div className="decision-card-state"><Icon name="pause" size={13} /><span>Monitoring paused · last observation retained</span></div>}
    {item.next_action && <div className="decision-card-state"><Icon name="arrow" size={13} /><span>{safeCardText(item.next_action, 'Next action not recorded.')}</span></div>}
    {stale && <div className="decision-card-state decision-card-stale"><Icon name="warning" size={13} /><span>Evidence changed · review needed</span></div>}
    <div className="decision-card-foot"><span>{item.decision_revision != null ? `Revision ${item.decision_revision}` : 'Revision unavailable'}</span><span>Open current case <Icon name="chevron" size={13} /></span></div>
  </button>
}

function decisionCardReadiness(decision: CaseDecision, candidate: CandidateDecision | undefined, runState: string, evidenceLabel: string | null) {
  if (runState === 'failed' || runState === 'error') return 'Research failed · inspect the recorded break'
  if (decision.stale) return 'Evidence changed · review before acting'
  if (runState === 'waiting_for_evidence') return 'Evidence needed before the candidate outcome can be used'
  if (runState === 'awaiting_input') return 'Input needed before the candidate outcome can be used'
  if (runState === 'waiting_for_review') return 'Awaiting manager review'
  if (candidate?.recommendationGate && normalizeKey(candidate.recommendationGate.status) !== 'pass') return `Recommendation gate ${text(candidate.recommendationGate.status) ?? 'not passed'} · review the gate`
  if (candidate?.outcome === 'pending' || decision.outcome === 'pending' || decision.outcome === 'awaiting_info') return 'Decision pending · review the open inputs'
  if (candidate?.outcome === 'watchlist') return candidate.nextAction ? `Watch condition recorded · ${safeCardText(candidate.nextAction, 'next action recorded')}` : 'Watch condition recorded · review the dated trigger'
  if (evidenceLabel) return `Research assessment: ${evidenceLabel} · open the memo for review`
  return 'Decision recorded · open the memo for review'
}

function DecisionCard({ run, sources, selected, onSelect }: { run: RunSummary; sources: EvidenceRef[]; selected: boolean; onSelect: () => void }) {
  const decision = decisionForRun(run, sources)
  const question = runTitle(run)
  const origin = originIsReddit(run) ? 'Reddit' : 'Question'
  const first = decision.candidates[0]
  const canonical = canonicalPayload(run)
  const rawCandidates = candidateValues(run, canonical)
  const rawFirst = rawCandidates[0] ?? {}
  const triggers = watchTriggerRecords(rawFirst)
  const primaryPrice = first?.outcome === 'watchlist' && triggers.length
    ? watchPriceCondition({ run_id: run.id, title: question } as WatchlistItem, rawFirst, triggers)
    : null
  const checkSummary = first?.outcome === 'watchlist' && triggers.length ? watchCheckSummary(rawFirst, triggers) : null
  const candidateLabels = decision.candidates.slice(0, 5).map((candidate, index) => caseCardSummary(firstText(rawCandidates[index]?.ticker, rawCandidates[index]?.symbol, candidate.instrument) ?? candidate.instrument))
  const primaryPriceLabel = primaryPrice && decision.candidates.length > 1 && candidateLabels[0] ? `Price ${candidateLabels[0]} ${primaryPrice}` : primaryPrice ? `Price ${primaryPrice}` : null
  const compactResultDetails = [primaryPriceLabel, checkSummary].filter(Boolean).join(' · ')
  const runState = runStatus(run)
  const evidenceLabel = evidenceStatusLabel(decision.evidenceVersion)
  const readiness = decisionCardReadiness(decision, first, runState, evidenceLabel)
  const cardTitle = candidateLabels.length ? `${candidateLabels.join(' · ')} research` : question
  return <button type="button" className={`decision-card decision-research-card${selected ? ' is-selected' : ''}`} onClick={onSelect} aria-label={`Open ${cardTitle}`}>
    <div className="decision-card-top"><span className="decision-card-origin"><Icon name={origin === 'Reddit' ? 'link' : 'book'} size={13} />{origin}</span><DecisionOutcomeBadge value={decision.outcome} /></div>
    <strong className="decision-card-title">{cardTitle}</strong>
    {first && <div className="decision-card-idea"><span>{decision.candidates.length > 1 ? candidateLabels.join(' · ') : caseCardSummary(first.instrument)}</span><p>{canonicalDecisionPreview(decision, first, decision.candidates.length > 1)}</p>{compactResultDetails && <p className="decision-card-watch-meta">{compactResultDetails}</p>}</div>}
    {!first && decision.reason && <p className="decision-card-reason">{safeCardText(decision.reason, 'Decision rationale not recorded.')}</p>}
    <div className={`decision-card-state${decision.stale || ['failed', 'error'].includes(runState) ? ' decision-card-stale' : ''}`}><Icon name={decision.stale || ['failed', 'error'].includes(runState) ? 'warning' : first?.outcome === 'watchlist' ? 'clock' : 'arrow'} size={13} /><span>{readiness}</span></div>
    <div className="decision-card-foot"><span>{decision.asOf ? `As of ${compactDate(decision.asOf)}` : 'Date unavailable'}</span><span className="decision-card-cta">Review case <Icon name="chevron" size={13} /></span></div>
  </button>
}

function SourceChips({ citations: refs, onOpenSource }: { citations: DecisionCitation[]; onOpenSource: DecisionWorkspaceProps['onOpenSource'] }) {
  if (!refs.length) return <p className="decision-empty-inline">No source citations recorded.</p>
  return <div className="decision-source-chips">{refs.map((source, index) => <button type="button" key={`${source.id}|${source.locator ?? ''}|${source.asOf ?? ''}`} onClick={() => void onOpenSource({ id: source.id, source_ref: source.id, title: source.title, locator: source.locator, as_of: source.asOf })}><Icon name="book" size={13} /><span>{source.title === 'Source record' ? `Source ${index + 1}` : source.title}</span>{(source.locator || source.asOf) && <small>{[source.locator, source.asOf && `as of ${source.asOf}`].filter(Boolean).join(' · ')}</small>}</button>)}</div>
}

function SourceCitationDetails({ citations: refs, onOpenSource, label = 'Show source citations' }: { citations: DecisionCitation[]; onOpenSource: DecisionWorkspaceProps['onOpenSource']; label?: string }) {
  if (!refs.length) return null
  return <details className="decision-details decision-source-details"><summary><span>{label}</span><small>{refs.length} recorded</small></summary><div className="decision-source-details-body"><SourceChips citations={refs} onOpenSource={onOpenSource} /></div></details>
}

function KeyValueList({ values, empty = 'Not recorded.' }: { values: RecordValue | null; empty?: string }) {
  if (!values || !Object.keys(values).length) return <p className="decision-empty-inline">{empty}</p>
  const entries = Object.entries(values).slice(0, 18)
  const privateEntries = entries.filter(([key, value]) => isPrivateFinancialKey(key) || hasPrivateFinancialData(value, isPrivateFinancialKey(key)))
  const visibleEntries = entries.filter(([key, value]) => !isPrivateFinancialKey(key) && !hasPrivateFinancialData(value, isPrivateFinancialKey(key)))
  const renderEntries = (rows: Array<[string, unknown]>) => rows.map(([key, value]) => { const display = typeof value === 'object' ? JSON.stringify(value) : text(value); return display ? <div key={key}><span>{key.replaceAll('_', ' ')}</span><strong>{display}</strong></div> : null })
  return <div className="decision-key-value-list">{renderEntries(visibleEntries)}{privateEntries.length > 0 && <PrivateFinancialDisclosure label={`Reveal ${privateEntries.length} recorded account or allocation ${privateEntries.length === 1 ? 'value' : 'values'}`} description="These recorded inputs may include account balances, holdings, or personal sizing values. They stay closed until you choose to inspect them locally."><div className="decision-key-value-list decision-key-value-list-private">{renderEntries(privateEntries)}</div></PrivateFinancialDisclosure>}</div>
}

function evidenceKindLabel(value: string) {
  if (value === 'fact') return 'Fact'
  if (value === 'opinion') return 'Opinion'
  if (value === 'assumption') return 'Assumption'
  if (value === 'unknown') return 'Unknown'
  return value.replaceAll('_', ' ')
}

function EvidenceRecords({ rows, onOpenSource }: { rows: DecisionEvidenceRecord[]; onOpenSource: DecisionWorkspaceProps['onOpenSource'] }) {
  if (!rows.length) return <p className="decision-empty-inline">No typed evidence statements were recorded.</p>
  return <div className="decision-evidence-list">{rows.slice(0, 24).map((item, index) => { const privateText = hasPrivateFinancialText(item.statement); const semantic = item.semanticStatus ? item.semanticStatus.replaceAll('_', ' ') : 'semantic status unavailable'; const content = <><div className="decision-evidence-meta"><span>{evidenceKindLabel(item.kind)}</span><small>{item.validationStatus ? item.validationStatus.replaceAll('_', ' ') : 'validation unknown'} · {semantic}{item.textMatch === false ? ' · text mismatch' : ''}{item.freshness ? ` · freshness ${item.freshness}` : ''}{item.asOf ? ` · as of ${dateLabel(item.asOf)}` : ''}</small></div>{item.sourceVersion && <small className="decision-evidence-source-meta">Source version {item.sourceVersion}{item.bindingChecks.length ? ` · ${item.bindingChecks.length} binding check${item.bindingChecks.length === 1 ? '' : 's'}` : ''}</small>}<p>{item.statement}</p>{item.unknownReason && <small className="decision-evidence-unknown-reason">Why unresolved: {item.unknownReason}</small>}{item.citations.length > 0 && <SourceCitationDetails citations={item.citations} onOpenSource={onOpenSource} label="Show supporting sources" />}</>; return <article className={`decision-evidence-row decision-evidence-${item.kind}`} key={`${item.kind}-${item.statement}-${index}`}>{privateText ? <PrivateFinancialDisclosure label="Reveal account or policy statement locally" description="This statement may contain account-dependent budgets, loss limits, shares, or allocation values. It stays closed until you choose to inspect it.">{content}</PrivateFinancialDisclosure> : content}</article> })}</div>
}

function ScenarioBlock({ output, forecast }: { output: OutputRecord; forecast?: RecordValue | null }) {
  const rows = output.scenario_fan ?? []
  const quantiles = output.scenario_quantiles ?? []
  const status = normalizeKey(output.scenario_status)
  const scenario = record(output.price_scenarios ?? output.simulation_snapshot)
  const acceptance = record(scenario?.model_acceptance)
  const forecastAccepted = forecast?.accepted === true
  const forecastStatus = normalizeKey(forecast?.model_acceptance_status)
  const acceptanceLabel = forecastAccepted
    ? 'Accepted by CIO'
    : forecast
      ? forecastStatus === 'rejected' || forecast?.accepted === false ? 'Not accepted for decision use' : 'Acceptance pending'
      : 'Acceptance not recorded'
  const forecastReasons = strings(forecast?.reasons)
  const warning = firstText(
    ...forecastReasons,
    output.scenario_missing_reason,
    ...(output.scenario_limitations ?? []),
    ...(Array.isArray(scenario?.acceptance_reasons) ? scenario.acceptance_reasons : []),
    acceptance?.reasons && strings(acceptance.reasons).join(' · '),
    !forecast ? 'This calculation is shown for context until canonical CIO forecast acceptance is recorded.' : !forecastAccepted ? 'This calculation is shown for context; it is not accepted for decision use.' : null,
    status && !['accepted', 'validated', 'complete'].includes(status) ? 'The calculation did not complete with a full scenario path.' : null,
  )
  return <section className="decision-scenario-block"><div className="decision-section-heading"><div><span className="decision-kicker">SCENARIO PLOT</span><h4>{output.scenario_ticker ?? 'Conditional price path'}</h4></div><div className="decision-scenario-label-stack"><span className={`decision-scenario-label${forecastAccepted ? ' is-accepted' : ' is-unaccepted'}`}>{acceptanceLabel}</span><small>Calculation {status ? status.replaceAll('_', ' ') : 'status unavailable'}</small></div></div>{warning && <div className="decision-scenario-warning"><Icon name="warning" size={14} /><span>{warning}</span></div>}<ScenarioChart rows={rows} quantiles={quantiles} /><small className="decision-footnote">Scenario bands are conditional calculations. They do not establish probabilities or a price target.</small></section>
}

function RecordedWork({ run, stages, onOpenSource, onOpenOutput }: { run: RunDetail; stages: string[]; onOpenSource: DecisionWorkspaceProps['onOpenSource']; onOpenOutput?: DecisionWorkspaceProps['onOpenOutput'] }) {
  const tasks = run.tasks ?? []
  const runOutputs = run.outputs ?? []
  const object = run as unknown as RecordValue
  const context = record(object.calculation_context)
  const canonical = canonicalPayload(run)
  const codeChecksRecorded = list(context?.candidates).length > 0 || list(canonical.candidates).some((candidate) => {
    const sizing = record(record(candidate)?.sizing)
    return list(sizing?.checks).length > 0 || ['ready', 'awaiting_input', 'failed'].includes(normalizeKey(sizing?.execution_state))
  })
  const taskForStage = (stage: string) => tasks.filter((task) => taskStage(task as unknown as RecordValue) === stage)
  const outputSources = (output: OutputRecord) => enrichCitations((output.source_refs ?? []).map((source) => sourceCitation(source)).filter((item): item is DecisionCitation => Boolean(item)), run.sources ?? run.source_links ?? [])
  const outputsForTask = (task: RunTaskRecord) => {
    const attached = task.outputs?.length ? task.outputs : task.output ? [task.output] : []
    if (attached.length) return attached
    const taskId = String(task.id ?? task.task_id ?? '')
    const agentId = String(task.agent_id ?? '')
    return runOutputs.filter((output) => {
      const outputTaskId = String(output.task_id ?? '')
      return taskId && outputTaskId === taskId || !taskId && agentId && String(output.agent_id ?? '') === agentId
    })
  }
  return <div className="decision-flow-detail-list">{stages.map((stage) => {
    const rows = taskForStage(stage)
    const recordedByCode = stage === 'Checks' && codeChecksRecorded && !rows.length
    const outputCount = rows.reduce((sum, task) => sum + (task.outputs?.length ?? (task.output ? 1 : 0)), 0)
    return <details key={stage}><summary><strong>{stage}</strong><span>{rows.length ? `${rows.length} task${rows.length === 1 ? '' : 's'} · ${rows.some((task) => ['blocked', 'failed'].includes(normalizeKey(task.status))) ? 'break recorded' : statusLabel(String(rows[0]?.status ?? 'recorded'))}` : recordedByCode ? 'Recorded · code checks' : 'Not scheduled'}</span></summary>{rows.length ? <div className="decision-flow-detail-body">{rows.map((task, index) => {
      const taskOutputs = outputsForTask(task)
      const taskSummary = firstText(task.terminal_summary, task.progress_message, record(task.explanation)?.summary, record(task.explanation)?.rationale)
      return <div key={task.id ?? task.task_id ?? index}><strong>{task.title ?? task.kind ?? task.question ?? 'Recorded task'}</strong><span>{task.status ? statusLabel(String(task.status)) : 'Recorded'} · {task.updated_at ? dateLabel(task.updated_at) : 'time unknown'}</span>{taskSummary && <p className="decision-flow-detail-summary">{taskSummary}</p>}{task.blocking_reason && <small>{task.blocking_reason}</small>}{taskOutputs.map((output, outputIndex) => { const outputSourcesForReport = outputSources(output); return <div className="decision-saved-report" key={output.id ?? output.output_id ?? outputIndex}><div className="decision-saved-report-head"><strong>{output.title ?? 'Saved agent report'}</strong>{onOpenOutput && <button type="button" className="button button-subtle" onClick={() => void onOpenOutput(output)}><Icon name="book" size={12} /> Open report</button>}</div>{output.conclusion && <p>{output.conclusion}</p>}{outputSourcesForReport.length > 0 && <SourceCitationDetails citations={outputSourcesForReport} onOpenSource={onOpenSource} label="Show report sources" />}</div> })}</div>
    })}{outputCount > 0 && <small>{outputCount} saved output{outputCount === 1 ? '' : 's'}</small>}</div> : recordedByCode ? <p className="decision-empty-inline">Code-owned sizing and calculation checks were recorded with the case decision.</p> : <p className="decision-empty-inline">This stage was not scheduled for the case.</p>}</details>
  })}</div>
}

function FlowDetails({ run, decision, sources, selectedCandidate, onOpenSource, onOpenOutput, onControl }: { run: RunDetail; decision: CaseDecision; sources: EvidenceRef[]; selectedCandidate?: CandidateDecision; onOpenSource: DecisionWorkspaceProps['onOpenSource']; onOpenOutput?: DecisionWorkspaceProps['onOpenOutput']; onControl?: DecisionWorkspaceProps['onControl'] }) {
  const evidenceRows = decision.candidates.flatMap((candidate) => candidate.evidence).map((item) => ({ ...item, citations: enrichCitations(item.citations, sources) }))
  const scenarioRows = decision.scenarioOutputs.filter((output, index, all) => {
    if (!output.price_scenarios && !output.simulation_snapshot && !output.scenario_fan?.length && !output.scenario_quantiles?.length && !output.scenario_status) return false
    const ticker = String(output.scenario_ticker ?? '').toUpperCase()
    return all.findIndex((item) => item === output || (ticker && String(item.scenario_ticker ?? '').toUpperCase() === ticker && item.id === output.id)) === index
  })
  const selectedTicker = String(selectedCandidate?.instrument ?? '').toUpperCase()
  const selectedScenario = scenarioRows.find((output) => output === selectedCandidate?.scenarioOutput || (selectedTicker && String(output.scenario_ticker ?? '').toUpperCase() === selectedTicker)) ?? scenarioRows[0] ?? null
  const otherScenarioRows = scenarioRows.filter((output) => output !== selectedScenario)
  const scenarioForecast = (output: OutputRecord) => decision.candidates.find((candidate) => candidate.scenarioOutput === output || String(candidate.instrument).toUpperCase() === String(output.scenario_ticker ?? '').toUpperCase())?.scenarioForecast
  const sourceRows = enrichCitations(decision.sourceCitations, sources)
  const historicalGaps = (run.gap_resolution_ledger ?? []).filter((gap) => ['resolved', 'closed', 'historical', 'superseded'].includes(normalizeKey(gap.status)))
  const stages = ['Claim', 'Evidence', 'Research', 'Checks', 'CIO']
  const canonical = canonicalPayload(run)
  const modelSupportValues = [
    canonical.supporting_evidence,
    canonical.evidence_for,
    canonical.strongest_support,
    ...list(canonical.candidates).flatMap((candidate) => {
      const row = record(candidate)
      return row ? [row.supporting_evidence, row.evidence_for, row.supporting_claims] : []
    }),
  ]
  const supportHeading = modelSupportValues.some((value) => Array.isArray(value) && value.length > 0) ? 'Strongest support' : 'Recorded evidence'
  const blockerCategory = (value: unknown) => {
    const key = normalizeKey(value)
    if (key.includes('evidence') || key.includes('source') || key.includes('citation') || key.includes('fact')) return 'Evidence gap'
    if (key.includes('portfolio') || key.includes('sizing') || key.includes('fund') || key.includes('cash') || key.includes('account') || key.includes('risk')) return 'Portfolio gate'
    if (key.includes('watch') || key.includes('trigger') || key.includes('review') || key.includes('catalyst')) return 'Lifecycle condition'
    return 'Decision input'
  }
  const blockerRows = [
    ...decision.blockers.map((gap, index) => ({
      key: `gap-${String(gap.key ?? gap.description ?? index)}`,
      title: gap.description ?? gap.key ?? 'Missing input',
      action: firstText(gap.action, gap.why_waiting, gap.reopen_when) ?? 'No next action recorded.',
      owner: gap.owner ?? null,
      category: blockerCategory(gap.key ?? gap.description),
      status: firstText(gap.status) ?? 'active',
    })),
    ...decision.unknowns.map((unknown, index) => ({
      key: `unknown-${index}`,
      title: unknown,
      action: 'Required before this case can be fully resolved.',
      owner: null,
      category: blockerCategory(unknown),
      status: 'unresolved',
    })),
  ]
  const visibleBlockerRows = blockerRows.slice(0, 3)
  const hiddenBlockerRows = blockerRows.slice(3)
  const renderBlocker = (row: typeof blockerRows[number]) => <article key={row.key}><strong>{row.title}</strong><small>{row.category} · {statusLabel(row.status)}</small><span>{row.action}</span>{row.owner && <small>Owner {friendlyOwner(row.owner)}</small>}</article>
  return <div className="decision-detail-sections">
    <section className="decision-section"><div className="decision-section-heading"><div><span className="decision-kicker">WHY THIS DECISION</span><h3>Recorded rationale</h3></div></div><p className="decision-rationale">{decision.rationale ?? 'No recorded rationale is available yet.'}</p><details className="decision-details"><summary>Show recorded inputs</summary><KeyValueList values={decision.inputs} empty="No portfolio or sizing inputs were recorded." /></details></section>
    <section className="decision-section"><div className="decision-section-heading"><div><span className="decision-kicker">EVIDENCE</span><h3>Source citations</h3></div><span className="decision-section-count">{sourceRows.length} recorded</span></div><SourceCitationDetails citations={sourceRows} onOpenSource={onOpenSource} label="Show source citations" /></section>
    <section className="decision-section"><div className="decision-section-heading"><div><span className="decision-kicker">EVIDENCE RECORD</span><h3>Typed statements</h3></div><span className="decision-section-count">{evidenceRows.length} recorded</span></div><details className="decision-details decision-evidence-details"><summary><span>Show typed statements</span><small>{evidenceRows.length} recorded</small></summary><EvidenceRecords rows={evidenceRows} onOpenSource={onOpenSource} /></details></section>
    <section className="decision-section"><div className="decision-section-heading"><div><span className="decision-kicker">INVESTMENT CASE</span><h3>Support and objection</h3></div></div>{decision.supportingEvidence.length || decision.objections.length ? <div className="decision-case-columns"><div><span>{supportHeading}</span>{decision.supportingEvidence.length ? <ul>{decision.supportingEvidence.slice(0, 3).map((item, index) => <li key={`support-${index}`}>{item}</li>)}</ul> : <p className="decision-empty-inline">No supporting statement recorded.</p>}</div><div><span>Strongest objection</span>{decision.objections.length ? <ul>{decision.objections.slice(0, 3).map((item, index) => <li key={`objection-${index}`}>{item}</li>)}</ul> : <p className="decision-empty-inline">No objection recorded.</p>}</div></div> : <p className="decision-empty-inline">The case packet has no separate support or objection fields.</p>}</section>
    <section className="decision-section"><div className="decision-section-heading"><div><span className="decision-kicker">WHAT IS UNKNOWN</span><h3>Active material blockers</h3></div><span className="decision-section-count">{blockerRows.length}</span></div>{blockerRows.length ? <><div className="decision-blocker-list">{visibleBlockerRows.map(renderBlocker)}</div>{hiddenBlockerRows.length > 0 && <details className="decision-details decision-more-blockers"><summary>Show {hiddenBlockerRows.length} more active blocker{hiddenBlockerRows.length === 1 ? '' : 's'}</summary><div className="decision-blocker-list">{hiddenBlockerRows.map(renderBlocker)}</div></details>}</> : <p className="decision-empty-inline">No active material blockers recorded.</p>}</section>
    {(decision.breakStage || decision.failureReason) && <section className="decision-section"><div className="decision-section-heading"><div><span className="decision-kicker">ACTUAL BREAK</span><h3>Where the flow stopped</h3></div></div><div className="decision-break-box"><div><span>Stage</span><strong>{decision.breakStage ?? 'Operational state recorded'}</strong></div>{decision.failureReason && <div><span>Error</span><strong>{decision.failureReason}</strong></div>}<div><span>Recovery</span><strong>{decision.recovery ?? 'Review the recorded state before continuing.'}</strong>{decision.retryAvailable && onControl && decision.retryTarget && <button type="button" className="button button-subtle decision-retry-button" onClick={() => onControl('retry', decision.retryScope ?? 'run', decision.retryTarget)}><Icon name="refresh" size={13} /> Retry {decision.retryScope === 'task' ? 'stage' : 'run'}</button>}</div></div></section>}
    <section className="decision-section"><div className="decision-section-heading"><div><span className="decision-kicker">FLOW DETAIL</span><h3>Recorded work</h3></div></div><RecordedWork run={run} stages={stages} onOpenSource={onOpenSource} onOpenOutput={onOpenOutput} /></section>
    <section className="decision-section"><div className="decision-section-heading"><div><span className="decision-kicker">HISTORY</span><h3>Earlier decisions and gap attempts</h3></div><span className="decision-section-count">{decision.decisionHistory.length + historicalGaps.length}</span></div><details className="decision-details"><summary>Show historical ledger</summary>{decision.decisionHistory.length || historicalGaps.length ? <div className="decision-history-list">{[...decision.decisionHistory, ...historicalGaps].slice(0, 20).map((item, index) => { const row = record(item); const correction = record(row?.correction); const revision = firstText(row?.decision_revision, row?.revision); const rationale = firstText(row?.reason, row?.rationale, row?.action, row?.updated_at); const correctionReason = firstText(row?.correction_reason, correction?.reason); return <article key={index}><strong>{firstText(row?.outcome, row?.disposition, row?.status, row?.description) ?? 'Recorded history'}{revision ? ` · Revision ${revision}` : ''}</strong>{rationale ? <MemoNarrative value={`Rationale: ${rationale}`} fallback="Historical entry retained." /> : <span>Historical entry retained.</span>}{correctionReason && <MemoNarrative value={`Code-only correction: ${correctionReason}`} />}</article> })}</div> : <p className="decision-empty-inline">No historical entries recorded.</p>}</details></section>
    {selectedScenario && <ScenarioBlock output={selectedScenario} forecast={scenarioForecast(selectedScenario)} />}
    {otherScenarioRows.length > 0 && <details className="decision-details decision-scenario-others"><summary><span>Show other candidate scenarios</span><small>{otherScenarioRows.length} available</small></summary><div className="decision-scenario-others-body">{otherScenarioRows.map((output, index) => <ScenarioBlock output={output} forecast={scenarioForecast(output)} key={output.id ?? output.output_id ?? index} />)}</div></details>}
  </div>
}

function DecisionDetail({ run, watchlistItem, watchlistItems, initialCandidateKey, selectedTitle, monitorPaused = false, sources, onClearRun, onOpenSource, onOpenOutput, onControl, onRecordLifecycle, onNavigate, backLabel = 'Back to cases' }: { run: RunDetail; watchlistItem?: WatchlistItem | null; watchlistItems?: WatchlistItem[]; initialCandidateKey?: string | null; selectedTitle?: string | null; monitorPaused?: boolean; sources: EvidenceRef[]; onClearRun: () => void; onOpenSource: DecisionWorkspaceProps['onOpenSource']; onOpenOutput?: DecisionWorkspaceProps['onOpenOutput']; onControl?: DecisionWorkspaceProps['onControl']; onRecordLifecycle?: DecisionWorkspaceProps['onRecordLifecycle']; onNavigate: DecisionWorkspaceProps['onNavigate']; backLabel?: string }) {
  useEffect(() => { window.scrollTo({top: 0, left: 0, behavior: 'auto'}) }, [run.id])
  const [earningsOpen, setEarningsOpen] = useState(false)
  const decisionRun = { ...run, __watchlist_item: watchlistItem, __watchlist_items: (watchlistItems ?? []).filter((item) => item.run_id === run.id) } as RunDetail
  const decision = decisionForRun(decisionRun, sources)
  const evidenceLabel = evidenceStatusLabel(decision.evidenceVersion)
  const [selectedCandidate, setSelectedCandidate] = useState(() => Math.max(0, decision.candidates.findIndex((item) => item.key === initialCandidateKey)))
  const candidate = decision.candidates[selectedCandidate] ?? decision.candidates[0]
  const rawCandidateRows = candidateValues(run, canonicalPayload(run))
  const candidateLabel = (item: CandidateDecision, index: number) => caseCardSummary(firstText(rawCandidateRows[index]?.ticker, rawCandidateRows[index]?.symbol, item.instrument) ?? item.instrument)
  const headerLabel = candidate ? candidateLabel(candidate, selectedCandidate) : null
  const frameOutcome = candidate && decision.candidates.length > 1 && decision.outcome !== candidate.outcome ? decision.outcome : null
  const operation = runStatus(run)
  const operational = ['queued', 'running', 'waiting_for_evidence', 'waiting_for_review', 'awaiting_input', 'paused', 'failed', 'error', 'blocked', 'interrupted'].includes(operation)
  const retryInProgress = 'tasks' in run && Array.isArray(run.tasks) && run.tasks.some((rawTask) => {
    const task = record(rawTask)
    if (!task || normalizeKey(task.status) !== 'running') return false
    const attempts = list(task.attempts).map(record).filter((item): item is RecordValue => Boolean(item))
    return attempts.slice(0, -1).some((attempt) => ['failed', 'blocked', 'error'].includes(normalizeKey(attempt.status)))
  })
  const operationalMessage = decision.failureReason ?? run.current_blocker ?? run.blocking_reason ?? (retryInProgress
    ? 'Retry in progress; waiting for the current research stage.'
    : operation === 'running'
      ? 'Research is running; a current decision has not been recorded.'
      : 'Progress information does not change the investment outcome.')
  const valuationRunning = Array.isArray(run.tasks) && run.tasks.some((task) => task.agent_id === 'A03' && task.status === 'running')
  const researchTicker = selectedResearchTicker(run, rawCandidateRows[selectedCandidate], decision.candidates.length)
  const earnings = researchTicker || decision.candidates.length <= 1 ? earningsReviewForRun(run, researchTicker) : undefined
  const question = selectedTitle ?? runTitle(run)
  const managerMemo = <ManagerMemo run={run} decision={decision} candidate={candidate ?? null} sources={sources} onOpenSource={onOpenSource} onRecordLifecycle={onRecordLifecycle} />
  const selectedOutcome = candidate?.outcome ?? decision.outcome
  const monitoringState = monitorPaused ? 'paused by the firm' : operation === 'paused' ? 'paused with this case' : 'available when the local process is running'
  return <article className="decision-detail-page" aria-label="Selected research run">
    <header className="decision-detail-header"><div><button type="button" className="decision-back-button" onClick={onClearRun}><Icon name="arrow" size={15} /> {backLabel}</button><span className="decision-kicker">CURRENT DECISION</span><div className="decision-detail-title-row"><h1>{headerLabel && headerLabel !== 'Candidate unavailable' ? headerLabel : outcomeLabel(selectedOutcome)}</h1><DecisionOutcomeBadge value={selectedOutcome} /></div><p className="decision-detail-question">{question}</p><div className="decision-detail-meta"><span>As of {dateLabel(decision.asOf)}</span>{decision.revision && <span>Revision {decision.revision}</span>}{evidenceLabel && <span>Research assessment: {evidenceLabel}</span>}{frameOutcome && <span>Case frame {outcomeLabel(frameOutcome)}</span>}</div></div><div className="decision-detail-actions"><button type="button" className="icon-button" aria-label="Close decision" onClick={onClearRun}><Icon name="close" size={17} /></button></div></header>
    <InvestmentProcess ticker={researchTicker || candidate?.instrument || run.ticker} earnings={earnings} onOpenEarnings={() => setEarningsOpen((value) => !value)}
      questionCount={candidate?.keyQuestions.length} answeredCount={candidate?.keyQuestions.filter((item) => !!item.answer).length}
      pricingRecorded={['complete', 'ready'].includes(candidate?.valuation?.status || '') || !!candidate?.futureTarget?.value}
      decisionRecorded={decision.hasCanonicalDecision && !['pending', 'unknown', 'failed'].includes(selectedOutcome)} />
    {earningsOpen && earnings?.workflow_id && <section className="case-earnings-section" aria-label="Earnings and materials used in this research"><header><h2>Earnings & materials</h2><button type="button" className="button" onClick={() => setEarningsOpen(false)}>Close earnings</button></header><Suspense fallback={<p role="status">Opening the saved earnings review…</p>}><EarningsWorkflow key={earnings.workflow_id} initialWorkflowId={earnings.workflow_id} embedded /></Suspense></section>}
    {decision.stale && <div className="decision-stale-banner"><Icon name="warning" size={16} /><div><strong>Evidence changed — decision needs review</strong><span>The saved decision remains visible until a new case revision is recorded.</span></div></div>}
    {operational && <div className={`decision-operational-banner decision-operational-${operation}`}><Icon name={operation === 'failed' || operation === 'error' ? 'warning' : operation === 'paused' ? 'pause' : 'clock'} size={16} /><div><strong>{operation === 'failed' || operation === 'error' ? 'Research failed' : operation === 'paused' ? 'Research is paused' : operation === 'waiting_for_evidence' || operation === 'awaiting_input' ? 'Needs input' : operation === 'waiting_for_review' ? 'Awaiting review' : operation === 'blocked' ? 'Needs attention' : retryInProgress ? 'Retry in progress' : statusLabel(operation)}</strong><p>{operationalMessage}</p></div></div>}
    {decision.candidates.length > 1 && <div className="candidate-tabs" role="group" aria-label="Candidate decisions">{decision.candidates.map((item, index) => <button type="button" aria-pressed={index === selectedCandidate} className={index === selectedCandidate ? 'is-active' : ''} key={item.key} onClick={() => setSelectedCandidate(index)}><span>{candidateLabel(item, index)}</span><DecisionOutcomeBadge value={item.outcome} /></button>)}</div>}
    {run.status === 'completed' && decision.hasCanonicalDecision && <LegacyCaseNotice candidate={candidate ?? null} />}
    {candidate && <DecisionSnapshot decision={decision} candidate={candidate} />}
    <InterimEvents value={record(run.investment_process)?.interim_events} ticker={researchTicker} onOpenSource={onOpenSource} />
    {candidate && <KeyQuestionSummary candidate={candidate} onOpenSource={onOpenSource} />}
    {candidate && <PriceTargetCard key={candidate.key} valuation={candidate.valuation} futureTarget={candidate.futureTarget} horizon={candidate.horizon} asOf={candidate.decisionAsOf} ticker={researchTicker} namespace={run.namespace === 'real' || run.namespace === 'demo' || run.namespace === 'simulation' ? run.namespace : undefined} sources={sources} loading={valuationRunning} onOpenSource={onOpenSource} />}
    {hasFiveQuestionReview(candidate) ? <details className="manager-memo-details"><summary><span>Open detailed memo, valuation and gate checks</span><small>Collapsed</small></summary>{managerMemo}</details> : managerMemo}
    {decision.candidates.length > 1 && <details className="decision-details"><summary>Compare candidates <small>{decision.allocationMode === 'alternatives' ? 'Alternatives — choose one' : 'Combined basket'}</small></summary><CandidateComparison candidates={decision.candidates} allocationMode={decision.allocationMode} onSelect={setSelectedCandidate} /></details>}
    <details className="decision-details" open={candidate?.outcome === 'recommend'}><summary>Entry, sizing and decision conditions</summary><section className="decision-plan-section"><div className="decision-section-heading"><div><span className="decision-kicker">PLAN</span><h2>{candidate ? candidateLabel(candidate, selectedCandidate) : 'Case plan'}</h2></div></div>{candidate ? <><CandidateOutcomeCard candidate={candidate} />{candidate.outcome === 'recommend' && <div className="decision-plan-notes"><div><span>Downside / invalidation</span><MemoStrong value={candidate.downside ?? candidate.invalidation[0]} fallback="Unavailable" /></div><div><span>Catalyst / review</span><MemoStrong value={candidate.catalyst} fallback="Unavailable" /></div><div><span>Horizon</span><MemoStrong value={candidate.horizon} fallback="Unavailable" /></div></div>}{candidate.outcome === 'recommend' && candidate.downside && <MemoNarrative value={`Risks to accept before entry: ${candidate.downside}`} />}{candidate.outcome !== 'recommend' && <p className="decision-risk-wording">Risk is recorded for review before entry. No accepted position risk is implied.</p>}</> : <p className="decision-empty-inline">No candidate decision has been recorded.</p>}</section></details>
    {candidate?.outcome === 'recommend' && candidate.sizingChecks.length > 0 && <PrivateFinancialDisclosure label="Reveal sizing checks locally" description="These checks can include account-dependent budgets, allocation formulas and loss limits."><section className="decision-checks"><span className="decision-kicker">PORTFOLIO FIT</span><h3>Sizing checks</h3><ul>{candidate.sizingChecks.map((check, index) => <li key={index}>{hasPrivateFinancialText(check) ? <PrivateFinancialDisclosure label="Reveal account or policy check locally" description="This check may contain account-dependent sizing or loss-limit values. It stays closed until you choose to inspect it."><span>{check}</span></PrivateFinancialDisclosure> : check}</li>)}</ul></section></PrivateFinancialDisclosure>}
    <ResearchActions runId={run.id} parentStatus={run.status} namespace={run.namespace === 'demo' ? 'demo' : 'real'} onOpenSource={onOpenSource} />
    <details className="decision-details manager-technical-details"><summary><span>Show evidence, calculations, workflow, history, and simulations</span><small>{decision.sourceCitations.length} sources · {decision.decisionHistory.length} history entries</small></summary><FlowStrip run={run} /><FlowDetails run={run} decision={decision} sources={sources} selectedCandidate={candidate} onOpenSource={onOpenSource} onOpenOutput={onOpenOutput} onControl={onControl} /></details>
    {decision.outcome === 'watchlist' && <div className="decision-watch-action"><Icon name="clock" size={16} /><span>Monitoring is shown as a saved condition. Its state is {monitoringState}.</span></div>}
    <footer className="decision-detail-footer"><button type="button" className="button button-subtle" onClick={onClearRun}><Icon name="arrow" size={14} /> {backLabel}</button><button type="button" className="button button-subtle" onClick={() => onNavigate('office', { preserveSelection: true })}><Icon name="grid" size={14} /> Inspect office activity</button></footer>
  </article>
}

function watchlistRun(item: WatchlistItem): RunSummary {
  const rawCandidate = record(item.candidate) ?? {}
  const checkState = item.checks?.map((check) => firstText(record(check)?.status)).find((value): value is string => Boolean(value)) ?? null
  const candidate: RecordValue = { ...rawCandidate, candidate_key: item.candidate_key, watch_check_status: checkState, lifecycle_state: item.lifecycle_state ?? item.state ?? item.status, current_quote: item.current_quote, distance_to_entry: item.distance_to_entry, review_due_at: item.review_due_at, overdue: item.overdue, next_action: item.next_action }
  const title = firstText(item.title, item.question, candidate.instrument, candidate.ticker, candidate.symbol) ?? 'Saved watchlist condition'
  const state = firstText(item.trigger_state, item.state, item.status) ?? 'completed'
  const decision: RecordValue = {
    run_id: item.run_id,
    decision_revision: item.decision_revision,
    as_of: item.as_of,
    outcome: item.candidate && rawCandidate.outcome !== undefined ? rawCandidate.outcome : null,
    lifecycle_state: item.lifecycle_state ?? item.state ?? item.status,
    execution_state: item.execution_state ?? 'ready',
    stale: item.stale ?? false,
    candidates: [candidate],
    material_blockers: [],
  }
  return {
    ...item,
    id: item.run_id,
    run_id: item.run_id,
    namespace: item.namespace == null ? undefined : String(item.namespace),
    question: title,
    original_question: title,
    origin: 'watchlist',
    status: state,
    execution_status: state,
    updated_at: firstText(item.updated_at, item.as_of),
    current_decision: decision,
    canonical_decision: decision,
  }
}

function ResultsList({ mode, runs, watchlistItems, sources, selectedRunId, onSelectRun }: { mode: Exclude<DecisionWorkspaceMode, 'reddit'>; runs: RunSummary[]; watchlistItems?: WatchlistItem[]; sources: EvidenceRef[]; selectedRunId?: string | null; onSelectRun: (id: string, candidateKey?: string | null) => void }) {
  if (mode === 'watchlist' && watchlistItems?.length) {
    return <div className="decision-card-list">{watchlistItems.map((item, index) => <WatchlistCard key={`${item.run_id}:${item.candidate_key ?? item.decision_revision ?? index}`} item={item} selected={selectedRunId === item.run_id} onSelect={() => onSelectRun(item.run_id, item.candidate_key)} />)}</div>
  }
  const listSource = mode === 'watchlist' && watchlistItems?.length ? watchlistItems.map(watchlistRun) : runs
  const grouped = groupedRuns(listSource.filter((run) => mode === 'watchlist' ? true : mode === 'questions' ? !originIsReddit(run) : true))
  const rows = grouped.filter(({ current }) => {
    const decision = decisionForRun(current, sources)
    if (mode !== 'watchlist') return true
    return decision.outcome === 'watchlist' || decision.candidates.some((candidate) => candidate.outcome === 'watchlist')
  })
  return <div className="decision-card-list">{rows.length ? rows.map(({ key, current }) => <DecisionCard key={key} run={current} sources={sources} selected={selectedRunId === current.id} onSelect={() => onSelectRun(current.id)} />) : <div className="decision-empty-state"><Icon name={mode === 'watchlist' ? 'clock' : 'book'} size={23} /><strong>{mode === 'watchlist' ? 'No active watchlist conditions' : mode === 'questions' ? 'No questions yet' : 'No results yet'}</strong><p>{mode === 'watchlist' ? 'A case appears here only when it has a concrete price or catalyst trigger.' : 'Ask the Chief of Staff to create the first case.'}</p></div>}</div>
}

function RedditCard({ post, runs, sources, onSelectRun, onDispatchReddit, onReuseReddit }: { post: RedditPostRecord; runs: RunSummary[]; sources: EvidenceRef[]; onSelectRun: (id: string) => void; onDispatchReddit?: (id: string) => Promise<void> | void; onReuseReddit?: (id: string) => Promise<void> | void }) {
  const state = normalizeKey(post.status ?? post.state ?? post.dispatch_state ?? 'retained')
  const linkedRunId = post.run_id ?? post.reused_run_id
  const linked = linkedRunId ? runs.find((run) => run.id === linkedRunId) : null
  const decision = linked ? decisionForRun(linked, sources) : null
  const title = firstText(post.title, post.text, post.post_id) ?? 'Untitled Reddit post'
  const triage = record(post.triage)
  const tickers = strings(triage?.tickers ?? post.tickers)
  const linkedPayload = linked ? canonicalPayload(linked) : {}
  const linkedCandidate = linked ? candidateValues(linked, linkedPayload)[0] ?? {} : {}
  const linkedTriggers = linked ? watchTriggerRecords(linkedCandidate) : []
  const primaryPrice = decision?.candidates?.[0]?.outcome === 'watchlist' && linkedTriggers.length
    ? watchPriceCondition({ run_id: linked?.id ?? '', title } as WatchlistItem, linkedCandidate, linkedTriggers)
    : null
  const checkSummary = decision?.candidates?.[0]?.outcome === 'watchlist' && linkedTriggers.length ? watchCheckSummary(linkedCandidate, linkedTriggers) : null
  const compactWatchDetails = [primaryPrice ? `Price ${primaryPrice}` : null, checkSummary].filter(Boolean).join(' · ')
  return <article className="reddit-decision-card"><div className="reddit-decision-head"><span className="decision-card-origin"><Icon name="link" size={13} /> Reddit {post.subreddit ? `· r/${post.subreddit}` : ''}</span><span className={`reddit-decision-state reddit-decision-state-${state}`}>{redditStateLabel(state)}</span></div><h3>{title}</h3><p className="reddit-decision-meta">{post.created_at ? `Posted ${dateLabel(post.created_at)}` : 'Post date unknown'}{post.author ? ` · u/${post.author}` : ''}{tickers.length ? ` · ${tickers.join(', ')}` : ''}</p>{decision?.candidates?.[0] ? <div className="reddit-decision-result"><DecisionOutcomeBadge value={decision.outcome} /><div><p>{canonicalDecisionPreview(decision, decision.candidates[0])}</p>{compactWatchDetails && <p className="reddit-decision-meta">{compactWatchDetails}</p>}</div></div> : triage?.reason ? <p className="reddit-decision-reason">{String(triage.reason)}</p> : <p className="reddit-decision-reason">This post is retained with its original source.</p>}<div className="reddit-decision-actions">{linked && <button type="button" className="button button-primary" onClick={() => onSelectRun(linked.id)}>View current decision</button>}{!linked && linkedRunId && <span className="muted-copy">Linked run {linkedRunId.slice(0, 10)}</span>}{!linked && onDispatchReddit && post.id && <button type="button" className="button button-subtle" onClick={() => void onDispatchReddit(String(post.id))}>Research post</button>}{state === 'processed' && onReuseReddit && post.id && <button type="button" className="button button-subtle" onClick={() => void onReuseReddit(String(post.id))}>Reuse</button>}{post.permalink && <a className="button button-subtle" href={post.permalink} target="_blank" rel="noreferrer"><Icon name="external" size={13} /> Open source</a>}</div></article>
}

function RedditWorkspace({ inbox, connection, runs, sources, onSelectRun, onDispatchReddit, onReuseReddit, onRefreshReddit }: { inbox?: RedditInboxSnapshot; connection?: RedditConnectionState; runs: RunSummary[]; sources: EvidenceRef[]; onSelectRun: (id: string) => void; onDispatchReddit?: (id: string) => Promise<void> | void; onReuseReddit?: (id: string) => Promise<void> | void; onRefreshReddit?: () => Promise<void> | void }) {
  const posts = inbox?.posts ?? []
  const [filter, setFilter] = useState<'all' | 'results' | 'attention'>('all')
  const visible = posts.filter((post) => {
    const state = normalizeKey(post.status ?? post.state ?? post.dispatch_state)
    if (filter === 'results') return Boolean(post.run_id) || ['processed', 'reused', 'researched', 'completed'].includes(state)
    if (filter === 'attention') return ['failed', 'blocked', 'error', 'attention'].includes(state) || Boolean(post.error || post.dispatch_error)
    return true
  })
  return <div className="decision-workspace-page reddit-decision-page"><header className="decision-workspace-header"><div><span className="decision-kicker">REDDIT / SOURCE INTAKE</span><h1>Reddit</h1><p>Each post keeps its original title and source. Open its case to see the decision.</p></div><div className="decision-workspace-actions"><span className={`reddit-connection-pill ${connection?.connected || connection?.status === 'connected' ? 'is-connected' : ''}`}>{redditConnectionLabel(connection)}</span><button type="button" className="button button-subtle" onClick={() => void onRefreshReddit?.()}><Icon name="refresh" size={14} /> Refresh</button></div></header><div className="reddit-decision-toolbar"><div className="decision-filter-tabs" role="group" aria-label="Reddit cases"><button type="button" className={filter === 'all' ? 'is-active' : ''} onClick={() => setFilter('all')}>All <strong>{posts.length}</strong></button><button type="button" className={filter === 'results' ? 'is-active' : ''} onClick={() => setFilter('results')}>Results <strong>{posts.filter((post) => Boolean(post.run_id)).length}</strong></button><button type="button" className={filter === 'attention' ? 'is-active' : ''} onClick={() => setFilter('attention')}>Needs attention <strong>{posts.filter((post) => ['failed', 'blocked', 'error', 'attention'].includes(normalizeKey(post.status ?? post.state)) || Boolean(post.error || post.dispatch_error)).length}</strong></button></div><span className="muted-copy">{inbox?.availability === 'unavailable' ? inbox.error_message ?? 'Reddit intake unavailable.' : `${visible.length} retained title${visible.length === 1 ? '' : 's'}`}</span></div>{visible.length ? <div className="reddit-decision-list">{visible.map((post, index) => <RedditCard key={post.id ?? post.post_id ?? index} post={post} runs={runs} sources={sources} onSelectRun={onSelectRun} onDispatchReddit={onDispatchReddit} onReuseReddit={onReuseReddit} />)}</div> : <div className="decision-empty-state"><Icon name="link" size={23} /><strong>No retained Reddit titles</strong><p>Paste a Reddit post URL above to read and evaluate it.</p></div>}</div>
}

export function DecisionWorkspace(props: DecisionWorkspaceProps) {
  const [preferredCandidateKey, setPreferredCandidateKey] = useState<string | null>(null)
  const [originFilter, setOriginFilter] = useState(props.initialOriginFilter || 'all')
  const [progressFilter, setProgressFilter] = useState('all')
  useEffect(() => { setOriginFilter(props.initialOriginFilter || 'all') }, [props.initialOriginFilter])
  const selectedTitle = props.selectedRun ? (props.redditInbox?.posts ?? []).find((post) => post.run_id === props.selectedRun?.id || post.reused_run_id === props.selectedRun?.id)?.title ?? props.watchlistItems?.find((item) => item.run_id === props.selectedRun?.id)?.title : null
  const watching = props.mode === 'watchlist'
  const pendingDetail = props.selectedRunId && props.selectedRun?.id !== props.selectedRunId
    && props.detailLoad?.runId === props.selectedRunId ? props.detailLoad : null
  if (pendingDetail) return <section className="decision-detail-page" aria-label="Selected research run" aria-busy={pendingDetail.status === 'loading'}>
    <button type="button" className="decision-back-button" onClick={props.onClearRun}><Icon name="arrow" size={15} />{watching ? 'Back to watchlist' : 'Back to research'}</button>
    {pendingDetail.status === 'loading'
      ? <div role="status"><h1>Opening saved research…</h1><p>Loading the decision, answers and supporting evidence.</p></div>
      : <div role="alert"><h1>This case could not open</h1><p>{pendingDetail.error}</p><button type="button" className="button button-primary" onClick={() => void props.onSelectRun(pendingDetail.runId)}>Try again</button></div>}
  </section>
  const selectedDecision = props.selectedRun ? decisionForRun(props.selectedRun, props.sources) : null
  const selectedWatchlistItem = props.selectedRun ? props.watchlistItems?.find((item) => item.run_id === props.selectedRun?.id) : null
  const selectedBelongsToView = Boolean(props.selectedRun) && (!watching || Boolean(selectedWatchlistItem ?? (selectedDecision?.outcome === 'watchlist' || selectedDecision?.lifecycleState === 'watchlist' || selectedDecision?.candidates.some((candidate) => ['watchlist', 'held', 'closed', 'recommended', 'declined'].includes(normalizeKey(candidate.lifecycleState)) || candidate.outcome === 'watchlist'))))
  if (props.selectedRun && selectedBelongsToView) return <DecisionDetail key={`${props.selectedRun.id}:${preferredCandidateKey ?? 'default'}`} run={props.selectedRun} watchlistItem={selectedWatchlistItem} watchlistItems={props.watchlistItems} initialCandidateKey={preferredCandidateKey} selectedTitle={selectedTitle} monitorPaused={Boolean(props.office?.paused || props.watchlistPaused)} sources={props.sources} onClearRun={props.onClearRun} onOpenSource={props.onOpenSource} onOpenOutput={props.onOpenOutput} onControl={props.onControl} onRecordLifecycle={props.onRecordLifecycle} onNavigate={props.onNavigate} backLabel={watching ? 'Back to watchlist' : 'Back to research'} />
  const allRuns = props.runs.filter((run) => !String(run.origin_ref || '').startsWith('research-action:') && !String(record(run)?.followup_kind || '').startsWith('optional_research_'))
  const watchlistRuns = props.watchlistItems?.length ? props.watchlistItems.map(watchlistRun) : allRuns
  const origin = (run: RunSummary) => originIsReddit(run) ? 'reddit' : String(run.origin || '').includes('congress') ? 'congress' : ['watchlist', 'monitor'].includes(String(run.origin)) ? 'watchlist' : 'user'
  const shownRuns = watching ? watchlistRuns : allRuns.filter((run) =>
    (originFilter === 'all' || origin(run) === originFilter) &&
    (progressFilter === 'all' || (progressFilter === 'attention' ? runNeedsAttention(run) : progressFilter === 'completed' ? runStatus(run) === 'completed' : runStatus(run) !== 'completed')))
  const watchCount = shownRuns.filter((run) => { const decision = decisionForRun(run, props.sources); return decision.outcome === 'watchlist' || decision.candidates.some((candidate) => candidate.outcome === 'watchlist') }).length
  const monitorPaused = Boolean(props.office?.paused || props.watchlistPaused)
  const monitorLabel = monitorPaused ? 'Paused' : props.watchlistEnabled === true ? 'Monitoring on' : props.watchlistEnabled === false ? 'Monitoring off' : 'Monitoring status unavailable'
  return <div className="decision-workspace-page"><header className="decision-workspace-header"><div><h1>{watching ? 'Watchlist' : 'Your research'}</h1><p>{watching ? 'Entry prices and events that would change a decision.' : 'Pick a case to review its decision and next step.'}</p></div>{watching && <span className="decision-watch-monitor-pill">{monitorLabel}</span>}</header>
    {monitorPaused && <div className="decision-paused-banner"><Icon name="pause" size={15} /><div><strong>Background research paused</strong><span>Saved research remains available.</span></div></div>}
    {!watching && <div className="research-case-filters"><div role="group" aria-label="Research origin">{[['all','All ideas'],['user','You'],['reddit','Reddit'],['congress','Congress'],['watchlist','Watchlist']].map(([value,label]) => <button type="button" className={`button${originFilter === value ? ' is-active' : ''}`} key={value} aria-pressed={originFilter === value} onClick={() => setOriginFilter(value)}>{label}</button>)}</div><label>Progress <select value={progressFilter} onChange={(event) => setProgressFilter(event.target.value)}><option value="all">All stages</option><option value="active">In progress</option><option value="completed">Completed</option><option value="attention">Needs attention</option></select></label></div>}
    {watching ? <div className="decision-summary-strip"><div><strong>{shownRuns.length}</strong><span>{watching ? 'saved candidates' : 'cases'}</span></div><div><strong>{shownRuns.filter(runNeedsAttention).length}</strong><span>Needs attention</span></div><div><strong>{shownRuns.filter((run) => decisionForRun(run, props.sources).outcome === 'recommend').length}</strong><span>recommendations</span></div><div><strong>{watchCount}</strong><span>watchlist</span></div></div> : <p className="research-count-summary">{shownRuns.length} {shownRuns.length === 1 ? 'case' : 'cases'} · {shownRuns.filter(runNeedsAttention).length} need attention · {watchCount} on watchlist</p>}
    <section className="decision-list-section">{watching && <div className="decision-list-heading"><h2>Lifecycle and review queue</h2></div>}<ResultsList mode={watching ? 'watchlist' : 'research'} runs={shownRuns} watchlistItems={props.watchlistItems} sources={props.sources} selectedRunId={props.selectedRunId} onSelectRun={(id, key) => {setPreferredCandidateKey(key ?? null); void props.onSelectRun(id)}} /></section>
    {!watching && originFilter === 'reddit' && <details className="decision-details"><summary>Reddit posts awaiting research</summary><RedditWorkspace inbox={props.redditInbox} connection={props.redditConnection} runs={allRuns} sources={props.sources} onSelectRun={(id) => void props.onSelectRun(id)} onDispatchReddit={props.onDispatchReddit} onReuseReddit={props.onReuseReddit} onRefreshReddit={props.onRefreshReddit}/></details>}
    <details className="decision-details manager-learning-shell"><summary>Paper learning journal</summary><LearningSummary learning={props.learning} /></details>
  </div>
}
