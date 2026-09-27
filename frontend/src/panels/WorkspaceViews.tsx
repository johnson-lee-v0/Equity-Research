import { modelEfforts } from '../modelOptions'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { CioBrief, CoverageItem, DecisionRecord, EvidenceRef, MemoryRecord, ModelConfig, MonitoringRule, Namespace, OfficeSnapshot, PortfolioPolicy, PortfolioSnapshot, ProviderState, RedditConnectionState, RedditInboxQuery, RedditInboxSnapshot, RedditPostRecord, RedditTriageClassification, ResearchCandidate, RunCounts, RunDependencyRecord, RunDetail, RunSummary, SimulationRun, RunTaskRecord, OutputRecord, MissingGap, MemoryContext, MemoryItem } from '../types'
import { Icon } from '../components/Icon'
import { ScenarioChart } from '../components/ScenarioChart'
import { StatusBadge, statusLabel } from '../components/StatusBadge'
import { PrivateFinancialDisclosure } from '../components/PrivateFinancialDisclosure'
import { hasPrivateFinancialData, hasPrivateFinancialText, isPersonalFinancialSource } from '../components/financialPrivacy'
import { ROLE_DEFINITIONS } from '../types'
import { formatDateTime } from '../date'
import './journal.css'

function dateLabel(value?: string | null) {
  return formatDateTime(value)
}

function meaningfulText(value: unknown) {
  if (value == null) return null
  const text = String(value).trim()
  if (!text) return null
  const normalized = text.toLowerCase().replace(/[.!?]+$/, '')
  if (['unknown', 'n/a', 'na', 'not available', 'not recorded', 'reason not supplied', 'reason not recorded', 'no reason supplied'].includes(normalized)) return null
  return text
}

function formatDecimal(value: unknown, minimumFractionDigits = 0) {
  if (value == null || value === '') return null
  const raw = String(value).trim().replaceAll(',', '')
  if (!raw) return null
  const negative = raw.startsWith('-')
  const unsigned = raw.replace(/^[+-]/, '')
  const parts = unsigned.split('.')
  if (parts.length > 2 || !/^\d+$/.test(parts[0] ?? '') || (parts[1] != null && !/^\d+$/.test(parts[1]))) return raw
  const integer = (parts[0] ?? '0').replace(/^0+(?=\d)/, '')
  let fraction = (parts[1] ?? '').replace(/0+$/, '')
  if (fraction.length < minimumFractionDigits) fraction += '0'.repeat(minimumFractionDigits - fraction.length)
  const grouped = integer.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  return `${negative ? '-' : ''}${grouped}${fraction ? `.${fraction}` : ''}`
}

function money(value: unknown, currency?: unknown) {
  const formatted = formatDecimal(value, 2)
  if (formatted == null) return 'Unknown amount'
  const label = typeof currency === 'string' && currency.trim() && currency !== 'UNK' ? currency.trim() : 'Currency unknown'
  return `${label} ${formatted}`
}

function quantity(value: unknown) {
  const formatted = formatDecimal(value)
  return formatted == null ? 'Unknown quantity' : formatted
}

function percentage(value: unknown) {
  if (value == null || value === '') return 'Unknown'
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return String(value)
  const percent = Math.abs(numeric) <= 1 ? numeric * 100 : numeric
  const digits = Math.abs(percent) >= 10 ? 0 : 2
  return `${percent.toFixed(digits).replace(/\.00$/, '')}%`
}

export type WorkspaceViewName = 'office' | 'research' | 'inbox' | 'tasks' | 'portfolio' | 'memory' | 'simulation' | 'coverage' | 'decisions' | 'settings'

export type ResearchTab = 'questions' | 'coverage' | 'simulation'

interface WorkspaceProps {
  view: WorkspaceViewName
  researchTab?: ResearchTab
  onResearchTab?: (tab: ResearchTab) => void
  namespace: Namespace
  office: OfficeSnapshot
  providers: ProviderState | null
  policy: any
  portfolio: PortfolioSnapshot | null
  portfolioPolicy: Record<string, unknown> | null
  memory: MemoryRecord[]
  sources: EvidenceRef[]
  coverage: CoverageItem[]
  decisions: DecisionRecord[]
  runs: RunSummary[]
  selectedRun: RunDetail | null
  onSelectRun: (runId: string) => Promise<void> | void
  onRefreshRuns?: () => Promise<void> | void
  onExplainResult: (runId: string, message: string, outputId?: string | null) => Promise<void>
  onResearchMissingEvidence: (runId: string, instruction: string, options?: { source_ids?: string[]; model_override?: Record<string, unknown> | null }) => Promise<void>
  simulations: SimulationRun[]
  monitoring: MonitoringRule[]
  redditInbox?: RedditInboxSnapshot
  redditConnection?: RedditConnectionState
  onRefreshReddit?: (query?: RedditInboxQuery) => Promise<void> | void
  onLoadMoreReddit?: (query?: RedditInboxQuery) => Promise<void> | void
  onDispatchReddit?: (id: string) => Promise<void> | void
  onSaveRedditSettings?: (input: { subreddit: string; window_days: number; enabled: boolean }) => Promise<void> | void
  simulationFocusId?: string | null
  onNavigate: (view: WorkspaceViewName) => void
  onSearchMemory: (query: string, kind: string) => void
  onImport: (payload: { kind: 'evidence' | 'transactions' | 'balances'; title: string; content: string; source_url?: string | null; publication_at?: string | null; observed_at?: string | null; supersedes_id?: string | null }) => Promise<void>
  onExport: (format?: 'json' | 'markdown') => Promise<void>
  onOpenOutput: (output: import('../types').OutputRecord) => Promise<void> | void
  onOpenSource: (source: EvidenceRef) => Promise<void> | void
  onSourceVersions: (sourceId: string) => Promise<EvidenceRef[]>
  onCoverage: (item: CoverageItem, status: string, reason: string) => Promise<void>
  onRunSimulation: (input: { title: string; question: string; horizon: string; participants: number; rounds: number; seed: number; initial_price: string; shock_percent: string; use_llm: boolean; source_ids: string[] }) => Promise<void>
  onReplaySimulation: (id: string) => Promise<any>
  onModelPolicy: (input: { scope: 'firm' | 'role' | 'profile'; agent_id: string | null; config: ModelConfig | null; profile: string | null }) => Promise<void>
  onPortfolioPolicy: (input: PortfolioPolicy | Record<string, unknown>) => Promise<void>
  onPreflight: (provider: string, model: string | null, reasoning_effort: string | null, execute: boolean) => Promise<void>
  onRiskSettings: (settings: Record<string, string | null>) => Promise<void>
  onBackup: () => Promise<void>
  onRestore: (path: string) => Promise<void>
  onMonitoring: (input: { name: string; enabled: boolean; timezone: string; interval_minutes: number; condition: string; source_ids: string[]; mode?: 'interval_research' | 'source_change' }) => Promise<void>
  onMonitoringToggle: (rule: MonitoringRule) => Promise<void>
  onControl: (action: 'pause' | 'resume' | 'cancel' | 'retry', scope: 'firm' | 'run' | 'task', id: string | null) => void
  loading?: boolean
  reducedMotion: boolean
  onReducedMotion: (value: boolean) => void
}

export function WorkspaceViews(props: WorkspaceProps) {
  if (props.view === 'research') return <ResearchView {...props} />
  if (props.view === 'inbox') return <RedditInboxView {...props} />
  if (props.view === 'tasks') return <TasksView {...props} />
  if (props.view === 'portfolio') return <PortfolioView {...props} />
  if (props.view === 'memory') return <MemoryView {...props} />
  if (props.view === 'simulation') return <SimulationView {...props} />
  if (props.view === 'coverage') return <CoverageView {...props} />
  if (props.view === 'decisions') return <DecisionsView {...props} />
  if (props.view === 'settings') return <SettingsView {...props} />
  return null
}

function ViewHeader({ eyebrow, title, copy, namespace: _namespace, actions }: { eyebrow: string; title: string; copy: string; namespace: Namespace; actions?: React.ReactNode }) {
  return <header className="workspace-header"><div><h1>{title}</h1>{eyebrow === 'INBOX / REDDIT INTAKE' && <p className="reddit-policy-copy">Research thesis posts, plus YOLO posts with an identifiable ticker. Skip all other posts and keep the reason.</p>}<p>{copy}</p></div><div className="workspace-header-meta">{actions}</div></header>
}

function normalizedRunStatus(run: RunSummary) {
  return String(run.execution_status ?? run.execution_state ?? run.status ?? 'unknown').toLowerCase().replaceAll('-', '_').replaceAll(' ', '_')
}

function normalizedEvidenceStatus(run: RunSummary) {
  return String(run.evidence_readiness ?? run.evidence_status ?? run.evidence ?? 'unknown').toLowerCase().replaceAll('-', '_').replaceAll(' ', '_')
}

function normalizedDisposition(run: RunSummary) {
  return String(run.committee_disposition ?? run.decision_disposition ?? run.disposition ?? 'pending').toLowerCase().replaceAll('-', '_').replaceAll(' ', '_')
}

function timestamp(value?: string | null) {
  const parsed = value ? Date.parse(value) : Number.NaN
  return Number.isFinite(parsed) ? parsed : 0
}

function runIsActive(run: RunSummary) {
  return ['queued', 'running', 'waiting_for_evidence', 'waiting_for_review', 'paused'].includes(normalizedRunStatus(run))
}

function runIsCompleted(run: RunSummary) {
  return normalizedRunStatus(run) === 'completed'
}

function preferredRun(items: RunSummary[]) {
  return [...items].sort((left, right) => {
    const leftRank = runIsActive(left) ? 2 : runIsCompleted(left) ? 1 : 0
    const rightRank = runIsActive(right) ? 2 : runIsCompleted(right) ? 1 : 0
    return rightRank - leftRank || timestamp(right.created_at) - timestamp(left.created_at)
  })[0] ?? null
}

function isRedditOrigin(run: RunSummary, allRuns: RunSummary[] = []) {
  const origin = String(run.origin ?? '').toLowerCase()
  if (origin === 'reddit') return true
  if (origin !== 'repair') return false
  if (String(run.root_origin ?? '').toLowerCase() === 'reddit') return true
  const root = run.root_run_id ? allRuns.find((candidate) => candidate.id === run.root_run_id) : null
  if (root) return isRedditOrigin(root, allRuns)
  return String(run.origin_ref ?? '').toLowerCase().includes('reddit')
}

function savedAnswerRun(items: RunSummary[]) {
  return [...items].filter((run) => Boolean(run.decision_brief ?? run.cio_brief ?? run.latest_output_id ?? run.latest_output_summary ?? run.summary)).sort((left, right) => {
    const leftCompleted = runIsCompleted(left) ? 1 : 0
    const rightCompleted = runIsCompleted(right) ? 1 : 0
    return rightCompleted - leftCompleted || timestamp(right.updated_at ?? right.created_at) - timestamp(left.updated_at ?? left.created_at)
  })[0] ?? null
}

function runGroups(runs: RunSummary[]) {
  const grouped = new Map<string, RunSummary[]>()
  for (const run of runs) {
    const root = run.root_run_id ? runs.find((candidate) => candidate.id === run.root_run_id) : null
    const key = run.question_group_id || root?.question_group_id || root?.original_question || run.original_question || run.question || run.id
    const existing = grouped.get(key) ?? []
    existing.push(run)
    grouped.set(key, existing)
  }
  return [...grouped.entries()].map(([key, items]) => ({ key, items, preferred: preferredRun(items) })).sort((left, right) => timestamp(right.preferred?.created_at) - timestamp(left.preferred?.created_at))
}

function roleName(id: string) {
  return ROLE_DEFINITIONS.find((role) => role.id === id)?.name ?? id
}

function displayOwner(value?: string | null) {
  if (!value) return 'Unknown'
  return /^A\d{2}$/.test(value) ? `${roleName(value)} · ${value}` : value
}

function runCount(run: RunSummary | null, key: keyof RunCounts) {
  if (!run) return 0
  const counts = run.counts ?? run.task_counts ?? {}
  if (counts[key] != null) return Number(counts[key])
  if (key === 'total' && run.task_count != null) return Number(run.task_count)
  if (key === 'completed' && run.completed_task_count != null) return Number(run.completed_task_count)
  if (key === 'sources' && run.source_count != null) return Number(run.source_count)
  return 0
}

function actionAllowed(actions: RunTaskRecord['allowed_actions'] | RunSummary['allowed_actions'], action: string, fallback = false) {
  if (Array.isArray(actions)) return actions.includes(action)
  if (actions && typeof actions === 'object') return actions[action] === true
  return fallback
}

function runActionAllowed(run: RunSummary, action: string) {
  return actionAllowed(run.allowed_actions, action, false)
}

function runHasOpenActions(run: RunSummary) {
  const actions = run.allowed_actions
  if (Array.isArray(actions)) return actions.some((action) => ['pause', 'resume', 'cancel', 'retry'].includes(action))
  if (actions && typeof actions === 'object') return Object.entries(actions).some(([action, enabled]) => enabled === true && ['pause', 'resume', 'cancel', 'retry'].includes(action))
  return runIsActive(run)
}

function taskProviderStart(task: RunTaskRecord) {
  const attemptStarts = (task.attempts ?? []).map((attempt) => attempt.provider_started_at ?? attempt.started_at).filter((value): value is string => Boolean(value)).map(timestamp)
  const direct = task.provider_started_at ?? task.started_at
  const values = [direct ? timestamp(direct) : 0, ...attemptStarts].filter((value) => value > 0)
  return values.length ? Math.min(...values) : 0
}

function orderedRunTasks(tasks: RunTaskRecord[]) {
  return [...tasks].sort((left, right) => {
    const leftStart = taskProviderStart(left)
    const rightStart = taskProviderStart(right)
    if (leftStart && rightStart && leftStart !== rightStart) return leftStart - rightStart
    if (leftStart !== rightStart) return leftStart ? -1 : 1
    return (Number(left.sequence_no ?? Number.MAX_SAFE_INTEGER) - Number(right.sequence_no ?? Number.MAX_SAFE_INTEGER)) || String(left.id ?? '').localeCompare(String(right.id ?? ''))
  })
}

function revisionQuestions(requests: string[], agentId?: string) {
  if (!agentId) return []
  const prefix = `${agentId.toUpperCase()}:`
  return requests.filter((request) => request.trim().toUpperCase().startsWith(prefix)).map((request) => request.trim().slice(prefix.length).trim()).filter(Boolean)
}

function compactList(values?: string[] | null) {
  return Array.from(new Set((values ?? []).map((value) => String(value).trim()).filter(Boolean)))
}

function memoryRecordHasPrivateMaterial(record: MemoryRecord | EvidenceRef | null | undefined) {
  if (!record) return false
  const candidate = record as Record<string, unknown>
  return isPersonalFinancialSource(candidate)
    || hasPrivateFinancialData(candidate)
    || hasPrivateFinancialText(candidate.content)
    || hasPrivateFinancialText(candidate.summary)
    || hasPrivateFinancialText(candidate.excerpt)
}

function briefStance(value?: string | null) {
  const normalized = String(value ?? '').toLowerCase()
  if (normalized === 'watch' || normalized === 'defer') return 'wait'
  if (normalized === 'enter' || normalized === 'avoid' || normalized === 'wait') return normalized
  return 'wait'
}

function authoritativeBriefStance(brief: CioBrief | null | undefined, disposition?: string | null) {
  const gate = String(disposition ?? '').toLowerCase().replaceAll('-', '_').replaceAll(' ', '_')
  if (gate === 'defer' || gate === 'deferred') return 'wait'
  if (gate === 'reject' || gate === 'rejected') return 'avoid'
  return briefStance(brief?.stance)
}

function pricePlanLabel(plan?: CioBrief['entry_plan'] | null) {
  if (!plan) return 'Unknown'
  const currency = plan.currency && plan.currency !== 'UNK' ? `${plan.currency} ` : ''
  const low = plan.low ?? null
  const high = plan.high ?? null
  if (low != null && high != null) return `${currency}${low}–${high}`
  if (low != null) return `${currency}${low}+`
  if (high != null) return `up to ${currency}${high}`
  if (plan.value != null) return `${currency}${plan.value}`
  return 'Unknown'
}

function BriefSourceRefs({ refs, onOpenSource }: { refs?: string[]; onOpenSource: WorkspaceProps['onOpenSource'] }) {
  const values = compactList(refs)
  if (!values.length) return null
  return <span className="cio-source-refs">Sources: {values.map((id, index) => <button type="button" key={`${id}-${index}`} onClick={() => void onOpenSource({ id, source_ref: id, title: id })}>{id}</button>)}</span>
}

function OutputList({ title, items, showEmpty = false, emptyLabel = 'Not recorded.' }: { title: string; items: string[]; showEmpty?: boolean; emptyLabel?: string }) {
  if (!items.length && !showEmpty) return null
  return <section><span className="eyebrow">{title}</span>{items.length ? <ul className="plain-list">{items.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul> : <p className="muted-copy">{emptyLabel}</p>}</section>
}

function CioBriefPanel({ run, latestOutput, scenarioOutput, onOpenSource, onNavigate: _onNavigate }: { run: RunDetail; latestOutput: OutputRecord | null; scenarioOutput?: OutputRecord | null; onOpenSource: WorkspaceProps['onOpenSource']; onNavigate: WorkspaceProps['onNavigate'] }) {
  const hasCioOutput = Boolean(run.answer_source_run_id) || run.outputs.some(output => output.agent_id === 'A11' || run.tasks.some(task => task.agent_id === 'A11' && task.id === output.task_id))
  if (!hasCioOutput && run.routing_plan?.intent === 'direct_answer') return <section className="workspace-card cio-brief-panel"><span className="eyebrow">CHIEF OF STAFF ANSWER</span><h3>{latestOutput?.title ?? 'Saved answer'}</h3><p>{latestOutput?.conclusion ?? run.latest_output_summary ?? run.summary ?? 'The answer is being prepared.'}</p></section>
  if (!hasCioOutput) return <section className="workspace-card cio-brief-panel"><span className="eyebrow">CIO DECISION</span><p>The CIO has not recorded a decision for this flow.</p><ScenarioFanPanel output={scenarioOutput ?? null} simulations={[]} /></section>
  const brief = run.decision_brief ?? run.cio_brief ?? latestOutput?.decision_brief ?? latestOutput?.cio_brief ?? null
  const legacySummary = latestOutput?.conclusion ?? run.summary ?? run.latest_output_summary ?? null
  if (!brief && !legacySummary) return null
  const decisionGate = run.decision_disposition ?? run.committee_disposition ?? run.disposition ?? latestOutput?.decision_disposition
  const gate = String(decisionGate ?? '').toLowerCase().replaceAll('-', '_').replaceAll(' ', '_')
  const gateDeferred = gate === 'defer' || gate === 'deferred'
  const effectiveStance = authoritativeBriefStance(brief, decisionGate)
  const entry = brief?.entry_plan ?? brief?.entry_zone ?? null
  const target = brief?.target_price ?? null
  const briefAsOf = meaningfulText(brief?.as_of)
  const decisionDate = briefAsOf ?? meaningfulText(latestOutput?.created_at) ?? meaningfulText(run.updated_at) ?? meaningfulText(run.created_at)
  const summaryReason = meaningfulText(brief?.reason) ?? meaningfulText(brief?.entry_advice) ?? meaningfulText(legacySummary)
  const proposedAction = latestOutput?.proposed_action ?? null
  const risks = compactList(brief?.accepted_risks ?? brief?.risks)
  const catalysts = compactList(brief?.catalysts)
  const invalidation = compactList(brief?.invalidation_conditions)
  const gapValues: string[] = [
    ...(brief?.blocking_gaps ?? []),
    ...(brief?.missing_inputs ?? []),
    ...(latestOutput?.missing_data ?? []),
    ...(run.blocking_gaps ?? []).flatMap((gap) => [gap.description, gap.key]),
    ...(run.gap_resolution_ledger ?? []).flatMap((gap) => [gap.description, gap.key]),
  ].filter((value): value is string => Boolean(value && value.trim()))
  const gaps = compactList(gapValues)
  const candidateSimulationIds = (run.candidate_simulations ?? []).flatMap((item) => {
    const id = item.simulation_id ?? item.id
    return id == null ? [] : [String(id)]
  })
  const simulations = compactList([...(brief?.simulation_ids ?? []), ...candidateSimulationIds])
  return <section className="workspace-card cio-brief-panel" aria-label="CIO brief">
    <div className="section-heading"><div><span className="eyebrow">CIO BRIEF</span><h3>{brief?.ticker ? `${brief.ticker} decision summary` : 'Decision summary'}</h3></div>{(brief?.stance || gateDeferred) && <span className={`cio-stance cio-stance-${effectiveStance}`}>{effectiveStance}</span>}</div>
    {run.answer_source_run_id && <div className="notice-card notice-blue cio-linked-answer-note"><Icon name="link" size={15} /><div><strong>Last saved CIO answer retained</strong><p>This linked repair has not returned a newer CIO brief yet. Showing the answer from run {run.answer_source_run_id} while repair progress stays below.</p></div></div>}
    {gateDeferred && <div className="notice-card notice-amber cio-gate-note"><Icon name="warning" size={15} /><div><strong>Decision gate: wait</strong><p>{run.current_blocker ?? run.blocking_reason ?? (gaps.length ? `${gaps.length} blocking gaps remain. Details are listed below.` : 'The saved review did not authorize entry.')}</p></div></div>}
    {brief?.legacy && <p className="cio-legacy-note">Earlier report · entry/target prices were not recorded.</p>}
    {brief ? <><div className="cio-brief-meta"><span>{briefAsOf ? 'As of' : 'Decision saved'} {dateLabel(decisionDate)}</span><span>Horizon {meaningfulText(brief.horizon) ?? 'Unknown'}</span>{meaningfulText(brief.ticker) && <span>Ticker {meaningfulText(brief.ticker)}</span>}</div><div className="cio-reason"><span className="eyebrow">WHY</span><p>{summaryReason ?? 'Reason not recorded.'}</p>{proposedAction && <div className="proposed-action"><span>Saved proposed action</span><strong>{proposedAction}</strong></div>}</div><div className="cio-price-grid"><div><span>Entry range</span><strong>{pricePlanLabel(entry)}</strong>{entry?.basis && <small>Basis: {entry.basis}</small>}{entry?.missing_reason && <small className="cio-missing">Why unknown: {entry.missing_reason}</small>}<BriefSourceRefs refs={entry?.source_refs} onOpenSource={onOpenSource} /></div><div><span>Target price</span><strong>{pricePlanLabel(target)}</strong>{target?.horizon && <small>Horizon: {target.horizon}</small>}{target?.basis && <small>Basis: {target.basis}</small>}{target?.missing_reason && <small className="cio-missing">Why unknown: {target.missing_reason}</small>}<BriefSourceRefs refs={target?.source_refs ?? brief.target_price_source_refs} onOpenSource={onOpenSource} /></div></div><div className="cio-brief-columns"><OutputList title="Accepted risks" items={risks} showEmpty /><OutputList title="Growth catalysts" items={catalysts} showEmpty /><OutputList title="Invalidation conditions" items={invalidation} showEmpty /><OutputList title="Blocking gaps" items={gaps} showEmpty /></div>{brief.next_review_trigger && <div className="cio-next-review"><span>Next review trigger</span><strong>{brief.next_review_trigger}</strong></div>}{simulations.length > 0 && <div className="cio-scenario-links"><span className="eyebrow">LINKED SCENARIOS</span><div>{simulations.map((id) => <button type="button" key={id} onClick={() => document.getElementById(`candidate-scenarios-${run.id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })}><Icon name="beaker" size={13} /> View price scenarios</button>)}</div><small>Scenario records stay hypothetical and separate from market evidence.</small></div>}</> : <div className="cio-legacy-fallback"><span className="eyebrow">LEGACY OUTPUT SUMMARY</span><div className="cio-brief-meta"><span>Decision saved {dateLabel(decisionDate)}</span><span>Horizon Unknown</span></div><p>{legacySummary}</p>{proposedAction && <div className="proposed-action"><span>Saved proposed action</span><strong>{proposedAction}</strong></div>}<div className="cio-brief-columns"><OutputList title="Accepted risks" items={[]} showEmpty /><OutputList title="Growth catalysts" items={[]} showEmpty /><OutputList title="Invalidation conditions" items={[]} showEmpty /><OutputList title="Blocking gaps" items={gaps} showEmpty /></div><small>No structured CIO brief was returned, so no entry range or target price is inferred.</small></div>}
    <div id={`candidate-scenarios-${run.id}`} style={{ scrollMarginTop: 90 }}><ScenarioFanPanel output={scenarioOutput ?? latestOutput} simulations={simulations} /></div>
    <TechnicalEvidencePanel output={latestOutput} />
    <CioCandidateBriefList briefs={brief?.candidate_briefs ?? []} onOpenSource={onOpenSource} />
  </section>
}

function CioCandidateBriefList({ briefs, onOpenSource }: { briefs: CioBrief[]; onOpenSource: WorkspaceProps['onOpenSource'] }) {
  if (!briefs.length) return null
  return <section className="cio-candidate-briefs"><div className="section-heading"><div><span className="eyebrow">CANDIDATE SUMMARIES</span><h4>Per ticker CIO readout</h4></div><span className="muted-copy">{briefs.length} candidate{briefs.length === 1 ? '' : 's'}</span></div><div className="cio-candidate-grid">{briefs.map((candidate, index) => { const stance = candidate.stance ? briefStance(candidate.stance) : null; const entry = candidate.entry_plan ?? candidate.entry_zone; const target = candidate.target_price; return <article key={`${candidate.ticker ?? 'candidate'}-${index}`}><div className="cio-candidate-heading"><strong>{candidate.ticker ?? 'Unknown ticker'}</strong>{stance && <span className={`cio-stance cio-stance-${stance}`}>{stance}</span>}</div>{candidate.reason && <p>{candidate.reason}</p>}<div className="cio-candidate-prices"><span>Entry {pricePlanLabel(entry)}</span><span>Target {pricePlanLabel(target)}</span></div>{entry?.missing_reason && <small className="cio-missing">Entry unknown: {entry.missing_reason}</small>}{target?.missing_reason && <small className="cio-missing">Target unknown: {target.missing_reason}</small>}<BriefSourceRefs refs={[...(entry?.source_refs ?? []), ...(target?.source_refs ?? candidate.target_price_source_refs ?? [])]} onOpenSource={onOpenSource} /></article> })}</div></section>
}

function candidateHttpUrl(value: string) {
  try {
    const url = new URL(value)
    return url.protocol === 'http:' || url.protocol === 'https:'
  } catch {
    return false
  }
}

function candidateSourceTitle(source: EvidenceRef) {
  return source.title ?? source.source_ref ?? source.id ?? 'Source record'
}

function ResearchCandidatePanel({ candidates, onOpenSource }: { candidates: ResearchCandidate[]; onOpenSource: WorkspaceProps['onOpenSource'] }) {
  if (!candidates.length) return null
  return <section className="research-candidates-section"><div className="section-heading"><div><span className="eyebrow">RESEARCH CANDIDATES</span><h4>Discovery leads</h4></div><span className="muted-copy">Research leads only · no allocation authority</span></div><div className="research-candidates">{candidates.map((candidate, index) => {
    const ticker = candidate.ticker ?? candidate.symbol
    const title = ticker ?? candidate.name ?? `Candidate ${index + 1}`
    const status = String(candidate.status ?? 'candidate').replaceAll('_', ' ')
    const verification = candidate.verified ? 'verified' : 'unverified'
    const urls = Array.from(new Set([...(candidate.source_urls ?? []), ...(candidate.primary_urls ?? []), ...(candidate.urls ?? [])]))
    const refs = candidate.source_refs ?? []
    return <article className="research-candidate" key={`${title}-${index}`}><div className="research-candidate-heading"><div><strong>{title}</strong>{candidate.name && ticker && <span>{candidate.name}</span>}</div><span className={`candidate-status candidate-status-${verification}`}>{status} · {verification}</span></div>{candidate.rationale && <p>{candidate.rationale}</p>}{candidate.unverified_reason ? <small className="candidate-verification-note">Verification note: {candidate.unverified_reason}</small> : !candidate.verified && <small className="candidate-verification-note">Verification status was not recorded for this historical lead.</small>}{candidate.evidence_available != null && <small className="candidate-evidence-state">Evidence archive: {candidate.evidence_available ? 'available locally' : 'not available'}</small>}{(urls.length > 0 || refs.length > 0) ? <div className="research-candidate-sources"><span className="eyebrow">SOURCE REFERENCES</span><div className="source-chip-list">{urls.map((url, urlIndex) => candidateHttpUrl(url) ? <a className="research-candidate-source-link" href={url} target="_blank" rel="noreferrer" key={`url-${url}-${urlIndex}`}><Icon name="external" size={13} /> {url}</a> : <span className="research-candidate-source-text" key={`url-${url}-${urlIndex}`}>{url}</span>)}{refs.map((source, sourceIndex) => source.url && candidateHttpUrl(source.url) ? <a className="research-candidate-source-link" href={source.url} target="_blank" rel="noreferrer" key={`ref-${source.id ?? source.source_ref ?? sourceIndex}`}><Icon name="external" size={13} /> {candidateSourceTitle(source)}</a> : <button type="button" key={`ref-${source.id ?? source.source_ref ?? sourceIndex}`} onClick={() => void onOpenSource(source)}><Icon name="book" size={13} /> {candidateSourceTitle(source)}</button>)}</div></div> : <small className="research-candidate-sources-empty">No source references returned.</small>}</article>
  })}</div></section>
}

function rawText(value: unknown, fallback = 'Unknown') {
  if (value == null || value === '') return fallback
  return String(value)
}

function scenarioOutputFromCandidates(candidates: Array<Record<string, unknown>> | undefined): OutputRecord | null {
  const fan: Array<Record<string, unknown>> = []
  const quantiles: Array<Record<string, unknown>> = []
  const snapshots: Array<Record<string, unknown>> = []
  for (const candidate of candidates ?? []) {
    const snapshot = isRecordValue(candidate.snapshot)
      ? candidate.snapshot
      : isRecordValue(candidate.result)
        ? candidate.result
        : null
    if (!snapshot || !isRecordValue(snapshot.scenarios)) continue
    snapshots.push(snapshot)
    const ticker = rawText(snapshot.ticker ?? candidate.candidate_ticker, 'Unknown ticker')
    const currency = snapshot.currency == null ? undefined : String(snapshot.currency)
    for (const [label, value] of Object.entries(snapshot.scenarios)) {
      if (!isRecordValue(value)) continue
      const bucket = value
      const terminal = bucket.terminal_price_quantiles
      if (isRecordValue(terminal)) quantiles.push({ ...terminal, label: `${ticker} · ${label}`, currency })
      if (Array.isArray(bucket.fan)) {
        bucket.fan.filter(isRecordValue).forEach((row) => fan.push({ ...row, label: `${ticker} · ${label} · day ${String(row.trading_day ?? '')}`.replace(/ · day $/, ''), currency }))
      }
    }
  }
  if (!fan.length && !quantiles.length && !snapshots.length) return null
  const first = snapshots[0] ?? {}
  const statuses = snapshots.map((snapshot) => String(snapshot.status ?? '').toLowerCase().replaceAll('-', '_').replaceAll(' ', '_')).filter(Boolean)
  const assumptions = snapshots.flatMap((snapshot) => Array.isArray(snapshot.assumptions) ? snapshot.assumptions.map(String) : []).filter((value, index, values) => values.indexOf(value) === index)
  const limitations = snapshots.flatMap((snapshot) => {
    const values = snapshot.limitations ?? snapshot.limits
    return Array.isArray(values) ? values.map(String) : []
  }).filter((value, index, values) => values.indexOf(value) === index)
  return {
    scenario_fan: fan,
    scenario_quantiles: quantiles,
    scenario_status: statuses.includes('insufficient_evidence') ? 'insufficient_evidence' : statuses[0] ?? null,
    scenario_method: first.method == null ? 'deterministic local calculation' : String(first.method),
    scenario_ticker: first.ticker == null ? null : String(first.ticker),
    scenario_currency: first.currency == null ? null : String(first.currency),
    scenario_as_of: first.as_of == null ? null : String(first.as_of),
    scenario_missing_reason: first.missing_reason == null ? null : String(first.missing_reason),
    scenario_assumptions: assumptions,
    scenario_limitations: limitations,
    scenario_result_hash: first.result_hash == null ? null : String(first.result_hash),
  }
}

function ScenarioFanPanel({ output, simulations }: { output: OutputRecord | null; simulations: string[] }) {
  const fan = output?.scenario_fan ?? []
  const quantiles = output?.scenario_quantiles ?? []
  if (!fan.length && !quantiles.length && !simulations.length) return null
  const rows = fan.length ? fan : quantiles
  const scenarioStatus = output?.scenario_status
  const statusLabel = scenarioStatus === 'insufficient_evidence' ? 'Insufficient evidence' : scenarioStatus ? scenarioStatus.replaceAll('_', ' ') : 'Status unavailable'
  const method = output?.scenario_method ?? 'Method not recorded'
  const scenarioMeta = output && (scenarioStatus || output.scenario_ticker || output.scenario_as_of || output.scenario_missing_reason || output.scenario_result_hash)
  return <section className="scenario-fan-panel"><div className="section-heading"><div><span className="eyebrow">SCENARIO FAN / QUANTILES</span><h4>Conditional model bands</h4></div><span className="scenario-caveat">Conditional bands · not a financial forecast</span></div>{scenarioMeta && <div className="scenario-model-meta"><span><strong>Model scenario</strong> {output?.scenario_ticker ?? 'Ticker unknown'}</span><span><strong>Status</strong> {statusLabel}</span><span><strong>Method</strong> {method}</span><span><strong>As of</strong> {output?.scenario_as_of ? dateLabel(output.scenario_as_of) : 'Unknown'}</span>{output?.scenario_result_hash && <span><strong>Result</strong> {output.scenario_result_hash}</span>}</div>}{scenarioStatus === 'insufficient_evidence' && <div className="notice-card notice-amber scenario-insufficient-note"><Icon name="warning" size={15} /><div><strong>Insufficient evidence</strong><p>{output?.scenario_missing_reason ?? 'The local scenario could not produce complete bands from the available inputs.'}</p></div></div>}{output?.scenario_missing_reason && scenarioStatus !== 'insufficient_evidence' && <p className="scenario-missing-reason">Why bands are limited: {output.scenario_missing_reason}</p>}{rows.length ? <ScenarioChart rows={rows} quantiles={quantiles} /> : <p className="muted-copy">No complete scenario bands are available.</p>} {(output?.scenario_assumptions?.length || output?.scenario_limitations?.length) ? <details className="scenario-assumptions-details"><summary>Recorded assumptions and limitations</summary>{output.scenario_assumptions?.length ? <div><strong>Assumptions</strong><ul className="plain-list">{output.scenario_assumptions.map((item, index) => <li key={`assumption-${index}`}>{item}</li>)}</ul></div> : null}{output.scenario_limitations?.length ? <div><strong>Limitations</strong><ul className="plain-list">{output.scenario_limitations.map((item, index) => <li key={`limitation-${index}`}>{item}</li>)}</ul></div> : null}</details> : null}<small className="scenario-fan-footnote">Bands reflect the recorded assumptions, calibration and source snapshot for this run. Quantiles are scenario outputs and do not establish probabilities, a target price or market truth.</small></section>
}

function TechnicalEvidencePanel({ output }: { output: OutputRecord | null }) {
  const rows = [...(output?.technical_indicators ?? []), ...(output?.multi_timeframe ?? [])]
  if (!rows.length) return null
  return <section className="technical-evidence-panel"><div className="section-heading"><div><span className="eyebrow">TECHNICAL EVIDENCE</span><h4>Multi-frequency indicators</h4></div><span className="scenario-caveat">Source as-of and feed required</span></div><div className="technical-card-grid">{rows.slice(0, 16).map((row, index) => <article key={`${String(row.timeframe ?? row.frequency ?? row.indicator ?? 'indicator')}-${index}`}><strong>{rawText(row.indicator ?? row.name ?? row.label, 'Indicator')}</strong><span>{rawText(row.timeframe ?? row.frequency, 'Frequency unknown')}</span><b>{rawText(row.value ?? row.signal ?? row.status)}</b><small>As of {rawText(row.as_of ?? row.observation_time, 'unknown')} · Feed {rawText(row.feed ?? row.source ?? row.source_ref, 'unknown')}</small></article>)}</div><small className="technical-footnote">Technical cards are observations from the named feed and timestamp. Missing source metadata keeps the indicator unresolved.</small></section>
}

function MemoryContextPanel({ context }: { context?: MemoryContext | null }) {
  if (!context) return null
  const reused = context.reused ?? []
  const fresh = context.fresh ?? []
  // The backend may attach an empty context object to every task. Keep the
  // useful memory signal visible while avoiding nine identical empty cards in
  // a long historical flow.
  if (!reused.length && !fresh.length) return null
  const item = (record: MemoryItem, index: number) => <article key={`${record.record_id ?? record.title ?? 'memory'}-${index}`}><strong>{record.title ?? record.record_id ?? 'Memory record'}</strong><span>{record.freshness ?? 'unknown'} · {record.reuse_reason || (record.stale ? 'Stale record' : 'No reuse reason supplied')}</span><details className="memory-record-details"><summary>View retained excerpt and source versions</summary><pre>{record.excerpt || 'No excerpt retained.'}</pre>{record.source_versions?.map((version, versionIndex) => <p key={versionIndex}><strong>{String(version.id ?? 'Source')} · version {String(version.version ?? 'unknown')}</strong><br />Retrieved {dateLabel(version.retrieved_at == null ? null : String(version.retrieved_at))}<br /><code>{String(version.content_hash ?? version.hash ?? 'Hash not recorded')}</code></p>)}</details></article>
  return <div className="memory-context-panel"><div className="section-heading"><div><span className="eyebrow">MEMORY CONTEXT</span><h4>Retrieved records</h4></div><span className="muted-copy">As of {dateLabel(context.freshness_as_of)}</span></div>{context.reason && <p className="memory-context-reason">{context.reason}</p>}{reused.length > 0 && <div><span className="memory-context-label">Reused information · {reused.length}</span><div className="memory-context-list">{reused.map(item)}</div></div>}{fresh.length > 0 && <div><span className="memory-context-label">Fresh retrievals · {fresh.length}</span><div className="memory-context-list">{fresh.map(item)}</div></div>}</div>
}

function GapResolutionLedger({ gaps, onOpenOutput, onNavigate, onSelectRun }: { onSelectRun: WorkspaceProps['onSelectRun']; gaps: MissingGap[]; onOpenOutput: WorkspaceProps['onOpenOutput']; onNavigate: WorkspaceProps['onNavigate'] }) {
  if (!gaps.length) return null
  const active = gaps.some((gap) => {
    const status = String(gap.status ?? '').toLowerCase().replaceAll('-', '_').replaceAll(' ', '_')
    return ['in_progress', 'queued', 'running', 'blocked', 'waiting'].includes(status) || (status === 'open' && Boolean(gap.repair_run_id))
  })
  return <section className="gap-ledger-panel"><div className="section-heading"><div><span className="eyebrow">GAP RESOLUTION LEDGER</span><h4>{active ? 'Missing data being addressed' : 'Missing data ledger'}</h4></div><span className="muted-copy">{gaps.length} gap{gaps.length === 1 ? '' : 's'}</span></div><div className="gap-ledger-list">{gaps.map((gap, index) => { const links = gap.result_links ?? []; const resultOutput = gap.resolved_by_output_id; const status = String(gap.status ?? 'historical').toLowerCase().replaceAll('_', '-'); const historicalUnassigned = status === 'historical' && !gap.repair_run_id && (gap.owner === 'Not assigned' || (!gap.owner && !gap.relevant_role)); const statusText = historicalUnassigned ? 'Historical gap · not assigned' : status.replaceAll('-', ' '); return <details className="gap-ledger-entry" key={`${gap.key ?? gap.id ?? 'gap'}-${index}`}><summary><strong>{gap.description ?? gap.key ?? 'Evidence gap'}</strong><span className={`gap-state gap-state-${status}`}>{statusText}</span></summary><div className="gap-ledger-body"><div className="gap-ledger-meta"><span>Owner {displayOwner(gap.owner ?? gap.relevant_role)}</span>{gap.updated_at && <span>Updated {dateLabel(gap.updated_at)}</span>}{gap.repair_run_id && <button type="button" onClick={() => { onNavigate('research'); if (gap.repair_run_id) void onSelectRun(gap.repair_run_id) }}>Open repair flow</button>}</div><p><strong>Why waiting:</strong> {gap.why_waiting ?? 'No wait reason was recorded.'}</p><p><strong>Next action:</strong> {gap.action ?? 'No next action was recorded.'}</p>{gap.reopen_when && <p><strong>Reopen when:</strong> {gap.reopen_when}</p>}{gap.source_requirements?.length ? <p><strong>Needs:</strong> {gap.source_requirements.join(' · ')}</p> : null}{(links.length > 0 || resultOutput) && <div className="gap-result-links"><span>Result links</span>{resultOutput && <button type="button" onClick={() => void onOpenOutput({ id: resultOutput, output_id: resultOutput, title: resultOutput })}><Icon name="book" size={13} /> {resultOutput}</button>}{links.map((link, linkIndex) => link.url ? <a href={link.url} target="_blank" rel="noreferrer" key={`${link.id ?? link.url}-${linkIndex}`}>{link.title ?? link.id ?? link.url}</a> : <button type="button" key={`${link.id ?? link.title ?? 'link'}-${linkIndex}`} onClick={() => link.id && void onOpenOutput({ id: link.id, output_id: link.id, title: link.title ?? link.id })}><Icon name="link" size={13} /> {link.title ?? link.id ?? 'Saved result'}</button>)}</div>}</div></details> })}</div></section>
}

function QuestionGroupList({ runs, selectedRunId, namespace: _namespace, onSelectRun }: { runs: RunSummary[]; selectedRunId?: string | null; namespace: Namespace; onSelectRun: (runId: string) => Promise<void> | void }) {
  const groups = useMemo(() => runGroups(runs), [runs])
  if (!groups.length) return <div className="run-empty-state"><Icon name="book" size={24} /><strong>No saved questions yet</strong><p>Ask the Chief of Staff from the command bar to create a persisted question and its auditable run.</p></div>
  return <div className="question-group-list">{groups.map(({ key, items, preferred }) => {
    const selected = items.some((run) => run.id === selectedRunId)
    const answer = savedAnswerRun(items)
    const question = preferred?.original_question ?? preferred?.question ?? items[0]?.question ?? 'Untitled research question'
    const execution = preferred ? normalizedRunStatus(preferred) : 'unknown'
    const evidence = preferred ? normalizedEvidenceStatus(preferred) : 'unknown'
    const disposition = preferred ? normalizedDisposition(preferred) : 'pending'
    return <article className={`question-group-card${selected ? ' is-selected' : ''}`} key={key}>
      <button type="button" className="question-group-trigger" onClick={() => preferred?.id && void onSelectRun(preferred.id)}>
        <span><span className="question-group-question">{question}</span><span className="question-group-meta"><span>{items.length} run{items.length === 1 ? '' : 's'}</span><span>{preferred?.selected_agent_ids?.length ?? 0} roles selected</span><span>Created {dateLabel(preferred?.created_at)}</span></span></span>
        <span className="question-group-state"><StatusBadge status={execution} compact /><span className="question-group-state-copy"><strong>{statusLabel(evidence)}</strong><span>{statusLabel(disposition)}</span></span></span>
      </button>
      {(answer?.latest_output_title || answer?.latest_output_summary || answer?.summary) && <div className="question-group-output"><span className="eyebrow">CURRENT SAVED ANSWER</span><strong>{answer?.latest_output_title ?? 'Saved CIO answer'}</strong><p>{answer?.latest_output_summary ?? answer?.summary ?? 'The latest output has no saved summary.'}</p>{answer && preferred && answer.id !== preferred.id && <small>Latest flow: {statusLabel(normalizedRunStatus(preferred))} · prior answer retained while this linked run completes.</small>}</div>}
      {items.length > 1 && <div className="question-group-runs"><span className="question-group-runs-label">Run history</span>{[...items].sort((left, right) => timestamp(right.created_at) - timestamp(left.created_at)).map((run) => <button type="button" className={`question-run-chip${run.id === selectedRunId ? ' is-active' : ''}`} key={run.id} onClick={() => void onSelectRun(run.id)}><span>{run.id.slice(0, 12)}</span><em>{runIsActive(run) ? 'active' : runIsCompleted(run) ? 'finished' : statusLabel(normalizedRunStatus(run))}</em><span>{dateLabel(run.created_at)}</span></button>)}</div>}
    </article>
  })}</div>
}

function RunTaskCard({ task, index, outputs, onOpenOutput, onControl }: { task: RunTaskRecord; index: number; outputs: OutputRecord[]; onOpenOutput: (output: OutputRecord) => Promise<void> | void; onControl: WorkspaceProps['onControl'] }) {
  const status = String(task.status ?? 'unknown')
  const taskId = task.id ?? task.task_id ?? null
  const dependencies: RunDependencyRecord[] = task.dependency_states?.length
    ? task.dependency_states
    : task.dependencies?.length
      ? task.dependencies
      : (task.dependency_ids ?? []).map((id) => ({ depends_on_task_id: id }))
  const outputId = task.output_id ?? task.output?.id ?? task.output?.output_id
  const output = outputId ? outputs.find((candidate) => (candidate.id ?? candidate.output_id) === outputId) ?? task.output : task.output
  const canPause = actionAllowed(task.allowed_actions, 'pause', ['queued', 'running', 'waiting_for_evidence', 'waiting_for_review'].includes(status))
  const canCancel = actionAllowed(task.allowed_actions, 'cancel', ['queued', 'running', 'waiting_for_evidence', 'waiting_for_review'].includes(status))
  const canRetry = actionAllowed(task.allowed_actions, 'retry', ['blocked', 'failed', 'interrupted'].includes(status))
  const canResume = actionAllowed(task.allowed_actions, 'resume', Boolean(task.paused) || status === 'paused')
  const latestAttempt = task.attempts?.[task.attempts.length - 1] ?? null
  const taskOutputs = taskId ? [...outputs.filter((candidate) => candidate.task_id === taskId)].sort((left, right) => (Number(right.version ?? 0) - Number(left.version ?? 0)) || timestamp(right.created_at) - timestamp(left.created_at)) : []
  const reviewContextOutput = task.review_context?.output_id ? outputs.find((candidate) => (candidate.id ?? candidate.output_id) === task.review_context?.output_id) ?? null : null
  const taskStateLabel = task.dispatch_state === 'waiting_capacity' ? 'Waiting for model capacity' : statusLabel(status)
  return <article className={`run-task-card${['completed', 'cancelled'].includes(status) ? ' is-terminal' : ''}`}>
    <span className="run-task-index">{String(index + 1).padStart(2, '0')}</span>
    <div className="run-task-main"><strong>{task.title ?? task.kind ?? task.question ?? task.instruction ?? task.current_task ?? 'Assigned research task'}</strong><div className="run-task-meta"><span>{task.agent_id ? `${roleName(task.agent_id)} · ${task.agent_id}` : 'Unassigned desk'}</span><span>{taskStateLabel}</span><span>{dateLabel(task.completed_at ?? task.updated_at)}</span></div>{task.assignment_reason && <p className="run-task-note"><strong>Why this desk:</strong> {task.assignment_reason}</p>}{task.current_task && task.question && task.current_task !== task.question && <p className="run-task-note">{task.current_task}</p>}{task.progress_message && task.progress_message !== task.current_task && task.progress_message !== task.question && <p className="run-task-note">{task.progress_message}</p>}{task.blocking_reason && <p className="run-task-note"><strong>Waiting reason:</strong> {task.blocking_reason}</p>}{task.review_context && <div className="run-task-review-context"><strong>Review input</strong><span>{task.review_context.label ?? task.review_context.title ?? 'Saved review context'}</span>{reviewContextOutput && <button type="button" className="run-task-output" onClick={() => void onOpenOutput(reviewContextOutput)}><Icon name="book" size={13} /> Open PM report · {reviewContextOutput.title ?? task.review_context.title ?? 'Saved output'}</button>}</div>}{dependencies.length > 0 && <div className="run-task-deps"><strong>Depends on</strong>{dependencies.map((dependency, depIndex) => <span key={`${dependency.depends_on_task_id ?? dependency.id ?? depIndex}`}><span>{dependency.title ?? dependency.depends_on_task_id ?? dependency.id ?? 'unknown'}</span>{dependency.agent_id && <small>{roleName(dependency.agent_id)} · {dependency.agent_id}</small>}{dependency.status ? ` · ${statusLabel(dependency.status)}` : ''}</span>)}</div>}<MemoryContextPanel context={task.memory_context} />{latestAttempt ? <div className="run-task-attempt"><span>{task.attempts?.length ?? 1} attempt{(task.attempts?.length ?? 1) === 1 ? '' : 's'}</span><span>{statusLabel(String(latestAttempt.status ?? 'attempt'))}</span><span>{latestAttempt.provider_started_at ? `Started ${dateLabel(latestAttempt.provider_started_at)}` : latestAttempt.started_at ? `Started ${dateLabel(latestAttempt.started_at)}` : task.dispatch_state === 'waiting_capacity' ? 'Waiting for model capacity' : 'Provider start unknown'}</span>{latestAttempt.error && <span className="run-task-attempt-error">{latestAttempt.error}</span>}</div> : task.attempt_id && <div className="run-task-attempt"><span>Attempt {task.attempt_id}</span><span>{task.dispatch_state === 'waiting_capacity' ? 'Waiting for model capacity' : 'Provider start unknown'}</span></div>}{task.attempts?.length ? <details className="run-attempt-history"><summary>Attempt history · {task.attempts.length}</summary><div className="run-attempt-list">{task.attempts.map((attempt, attemptIndex) => <div className="run-attempt-row" key={attempt.id ?? attempt.attempt_id ?? attemptIndex}><strong>Attempt {attempt.attempt_no ?? attemptIndex + 1}</strong><span>{statusLabel(String(attempt.status ?? 'unknown'))}</span><span>{attempt.provider ?? 'Provider unknown'} · {attempt.model ?? 'Model unknown'} · {attempt.reasoning_effort ?? 'effort unknown'}</span><span>{attempt.provider_started_at ? `Started ${dateLabel(attempt.provider_started_at)}` : attempt.started_at ? `Started ${dateLabel(attempt.started_at)}` : 'Start not recorded'}{attempt.finished_at ? ` · Finished ${dateLabel(attempt.finished_at)}` : ''}</span>{attempt.error && <small className="run-task-attempt-error">{attempt.error}</small>}</div>)}</div></details> : null}{output && <button type="button" className="run-task-output" onClick={() => void onOpenOutput(output)}><Icon name="book" size={13} /> Open saved output{output.title ? ` · ${output.title}` : ''}</button>}{taskOutputs.length > 1 && <details className="run-output-history"><summary>Output history · {taskOutputs.length} versions</summary><div className="run-output-list">{taskOutputs.map((candidate, outputIndex) => <button type="button" className="run-task-output" onClick={() => void onOpenOutput(candidate)} key={candidate.id ?? candidate.output_id ?? outputIndex}><Icon name="book" size={13} /> v{candidate.version ?? 1} · {candidate.title ?? 'Saved output'} · {statusLabel(candidate.status)}</button>)}</div></details>}</div>
    <div className="run-task-actions"><StatusBadge status={status} paused={Boolean(task.paused)} compact />{canRetry && taskId && <button type="button" className="icon-button" onClick={() => onControl('retry', 'task', taskId)} aria-label={`Retry ${task.title ?? task.kind ?? taskId}`}><Icon name="refresh" size={14} /></button>}{canResume && taskId && <button type="button" className="icon-button" onClick={() => onControl('resume', 'task', taskId)} aria-label={`Resume ${task.title ?? task.kind ?? taskId}`}><Icon name="play" size={14} /></button>}{canPause && taskId && <button type="button" className="icon-button" onClick={() => onControl('pause', 'task', taskId)} aria-label={`Pause ${task.title ?? task.kind ?? taskId}`}><Icon name="pause" size={14} /></button>}{canCancel && taskId && <button type="button" className="icon-button icon-danger" onClick={() => onControl('cancel', 'task', taskId)} aria-label={`Cancel ${task.title ?? task.kind ?? taskId}`}><Icon name="stop" size={14} /></button>}</div>
  </article>
}

function RunDetailPanel({ run, sources, detailTitle, onNavigate, onSelectRun, onOpenOutput, onOpenSource, onControl, onExplainResult, onResearchMissingEvidence }: { run: RunDetail | null; sources: EvidenceRef[]; detailTitle?: string | null; onSelectRun: WorkspaceProps['onSelectRun']; onNavigate: WorkspaceProps['onNavigate']; onOpenOutput: WorkspaceProps['onOpenOutput']; onOpenSource: WorkspaceProps['onOpenSource']; onControl: WorkspaceProps['onControl']; onExplainResult: WorkspaceProps['onExplainResult']; onResearchMissingEvidence: WorkspaceProps['onResearchMissingEvidence'] }) {
  const [explanation, setExplanation] = useState('')
  const [researchInstruction, setResearchInstruction] = useState('')
  const [selectedSources, setSelectedSources] = useState<string[]>([])
  const [action, setAction] = useState<'explain' | 'research' | null>(null)
  const [message, setMessage] = useState('')
  const mainOutputId = run?.latest_output_id ?? run?.latest_output?.id ?? run?.latest_output?.output_id ?? null
  useEffect(() => {
    const outputs = run?.outputs ?? []
    const latest = run?.latest_output_id
      ? outputs.find((output) => (output.id ?? output.output_id) === run.latest_output_id) ?? run.latest_output
      : run?.latest_output ?? [...outputs].sort((left, right) => (Number(right.version ?? 0) - Number(left.version ?? 0)) || timestamp(right.created_at) - timestamp(left.created_at))[0]
    setExplanation('')
    setResearchInstruction(latest?.missing_data?.[0] ?? '')
    setSelectedSources([])
    setMessage('')
  }, [run?.id, mainOutputId])
  if (!run) return <div className="workspace-card run-empty-state"><Icon name="arrow" size={23} /><strong>Select a question to inspect its run</strong><p>The selected flow keeps the original question, every task and dependency, output links, attempts, sources, and review handoffs together.</p></div>
  const currentRun = run
  const originalQuestion = run.original_question ?? run.question
  const heading = meaningfulText(detailTitle) ?? originalQuestion
  const displayTasks = orderedRunTasks(run.tasks)
  const runSources: EvidenceRef[] = (run.sources ?? run.source_links ?? []).length
    ? (run.sources ?? run.source_links ?? [])
    : (run.source_ids ?? []).map<EvidenceRef>((id) => ({ id, source_ref: id, title: id }))
  const sourceRows: EvidenceRef[] = runSources.map((source, index) => {
    const sourceId = source.id ?? source.source_ref ?? `source-${index}`
    const known = sources.find((candidate) => candidate.id === sourceId || candidate.source_ref === sourceId)
    if (!known) return source
    const sourceTitle = source.title && source.title !== sourceId ? source.title : known.title
    return { ...known, ...source, id: source.id ?? sourceId, source_ref: source.source_ref ?? known.source_ref ?? sourceId, title: sourceTitle ?? source.title ?? sourceId, source_type: source.source_type ?? known.source_type, locator: source.locator ?? known.locator }
  })
  const revisionTasks = displayTasks.filter((task) => String(task.kind ?? '').includes('revision') || (task.revision_requests?.length ?? 0) > 0 || task.review_disposition != null)
  const pmReviewTask = displayTasks.find((task) => task.agent_id === 'A10' && String(task.kind ?? '') === 'pm_review')
  const pmReviewOutput = pmReviewTask?.output_id ? run.outputs.find((output) => (output.id ?? output.output_id) === pmReviewTask.output_id) ?? null : null
  const pmRevisionRequests = pmReviewOutput?.revision_requests ?? []
  const latestOutput = run.latest_output_id
    ? run.outputs.find((output) => (output.id ?? output.output_id) === run.latest_output_id) ?? run.latest_output ?? null
    : run.latest_output ?? [...run.outputs].sort((left, right) => (Number(right.version ?? 0) - Number(left.version ?? 0)) || timestamp(right.created_at) - timestamp(left.created_at))[0] ?? null
  const recordedScenarioOutput = [...run.outputs].reverse().find((output) => Boolean(output.price_scenarios ?? output.simulation_snapshot ?? output.scenario_fan?.length ?? output.scenario_quantiles?.length)) ?? null
  const scenarioOutput = recordedScenarioOutput ?? scenarioOutputFromCandidates(run.candidate_simulations)
  const briefMissing = run.decision_brief ?? run.cio_brief ?? latestOutput?.decision_brief ?? latestOutput?.cio_brief
  const missing = Array.from(new Set([...(latestOutput?.missing_data ?? []), ...(briefMissing?.blocking_gaps ?? []), ...(briefMissing?.missing_inputs ?? [])])).filter(Boolean)
  const gapLedgerRaw = run.gap_resolution_ledger?.length
    ? run.gap_resolution_ledger
    : run.blocking_gaps?.length
      ? run.blocking_gaps
      : latestOutput?.missing_gaps?.length
        ? latestOutput.missing_gaps
        : missing.map((description) => ({ description, status: 'historical', owner: 'Not assigned', why_waiting: 'A dated source or explicit observation is required.', action: 'No repair run is linked; queue bounded evidence research when ready.' }))
  const gapLedger = gapLedgerRaw as MissingGap[]
  const candidateRows = Array.from(new Map([...((run.research_candidates ?? [])), ...((latestOutput?.research_candidates ?? []))].map((candidate, index) => {
    const key = String(candidate.ticker ?? candidate.symbol ?? candidate.name ?? `candidate-${index}`).toUpperCase()
    return [key, candidate] as const
  })).values())
  const roleSelection = run.agent_selection ?? []
  const canPauseRun = runActionAllowed(currentRun, 'pause')
  const canResumeRun = runActionAllowed(currentRun, 'resume')
  const canCancelRun = runActionAllowed(currentRun, 'cancel')
  const canRetryRun = runActionAllowed(currentRun, 'retry')
  async function explain(event: React.FormEvent) {
    event.preventDefault(); if (!explanation.trim()) return
    setAction('explain'); setMessage('')
    try { await onExplainResult(currentRun.id, explanation.trim(), latestOutput?.id ?? latestOutput?.output_id ?? null); setExplanation(''); setMessage('Explanation request queued as a linked follow-up task.') } catch (error) { setMessage(error instanceof Error ? error.message : 'Explanation request could not be queued.') } finally { setAction(null) }
  }
  async function research(event: React.FormEvent) {
    event.preventDefault(); if (!researchInstruction.trim()) return
    setAction('research'); setMessage('')
    try { await onResearchMissingEvidence(currentRun.id, researchInstruction.trim(), { source_ids: selectedSources }); setMessage('Bounded evidence research queued as a linked run.') } catch (error) { setMessage(error instanceof Error ? error.message : 'Evidence research could not be queued.') } finally { setAction(null) }
  }
  return <section className="workspace-card run-detail-card" aria-label="Selected research run">
    <div className="run-detail-header"><div><span className="eyebrow">SELECTED QUESTION / RUN DETAIL</span><h2>{heading}</h2>{detailTitle && detailTitle !== originalQuestion && <p className="run-detail-original-question"><strong>Original saved question</strong> {originalQuestion}</p>}<p>{run.id} · created {dateLabel(run.created_at)} · selected flow retains historical attempts and revisions.</p></div><div className="run-detail-header-actions"><StatusBadge status={run.execution_status ?? run.status} /><StatusBadge status={run.evidence_readiness ?? run.evidence_status ?? 'unknown'} compact />{latestOutput && <button type="button" className="button button-subtle" onClick={() => void onOpenOutput(latestOutput)}><Icon name="book" size={14} /> Open latest output</button>}{canResumeRun && <button type="button" className="icon-button" onClick={() => onControl('resume', 'run', currentRun.id)} aria-label="Resume question flow"><Icon name="play" size={14} /></button>}{canPauseRun && <button type="button" className="icon-button" onClick={() => onControl('pause', 'run', currentRun.id)} aria-label="Pause question flow"><Icon name="pause" size={14} /></button>}{canRetryRun && <button type="button" className="icon-button" onClick={() => onControl('retry', 'run', currentRun.id)} aria-label="Retry question flow"><Icon name="refresh" size={14} /></button>}{canCancelRun && <button type="button" className="icon-button icon-danger" onClick={() => onControl('cancel', 'run', currentRun.id)} aria-label="Cancel question flow"><Icon name="stop" size={14} /></button>}</div></div>
    <div className="run-detail-metrics"><div className="run-detail-metric"><span>Execution</span><strong className={`state-${normalizedRunStatus(run)}`}>{statusLabel(normalizedRunStatus(run))}</strong></div><div className="run-detail-metric"><span>Evidence status</span><strong className={`state-${normalizedEvidenceStatus(run)}`}>{statusLabel(normalizedEvidenceStatus(run))}</strong></div><div className="run-detail-metric"><span>Decision</span><strong className={`state-${normalizedDisposition(run)}`}>{statusLabel(normalizedDisposition(run))}</strong></div><div className="run-detail-metric"><span>Tasks in this flow</span><strong>{run.tasks.length} tasks · {run.outputs.length} outputs</strong></div></div>
    <CioBriefPanel run={run} latestOutput={latestOutput} scenarioOutput={scenarioOutput} onOpenSource={onOpenSource} onNavigate={onNavigate} />
    <ResearchCandidatePanel candidates={candidateRows} onOpenSource={onOpenSource} />
    <GapResolutionLedger gaps={gapLedger} onSelectRun={onSelectRun} onOpenOutput={onOpenOutput} onNavigate={(destination) => destination === 'research' && isRedditOrigin(run, [run]) ? onNavigate('inbox') : onNavigate(destination)} />
    <MemoryContextPanel context={run.memory_context} />
    {run.current_blocker && <div className="notice-card notice-amber"><Icon name="warning" size={17} /><div><strong>Current blocker</strong><p>{run.current_blocker}</p></div></div>}
    {roleSelection.length > 0 ? <div className="run-role-selection"><div className="run-role-selection-heading"><span>Role selection</span><small>Saved routing state</small></div>{roleSelection.map((role, index) => { const state = role.state ?? (role.selected ? 'selected' : String(role.reason ?? '').toLowerCase().includes('routing') ? 'routing_pending' : 'skipped'); const stateLabel = state === 'routing_pending' ? 'routing pending' : state === 'simulation_only' ? 'simulation only' : state; return <div className={`run-role-selection-row state-${state}`} key={`${role.agent_id ?? 'role'}-${index}`}><strong>{roleName(role.agent_id ?? 'Role')}</strong><span>{role.agent_id ?? 'Role'}</span><em>{stateLabel}</em>{role.reason && <small>{role.reason}</small>}</div> })}</div> : (run.selected_agent_ids.length > 0 || run.skipped_agent_ids.length > 0) && <div className="run-role-strip"><span>Routing</span>{run.selected_agent_ids.map((id) => <span className="run-role-chip" key={`selected-${id}`}>{roleName(id)} · {id}</span>)}{run.skipped_agent_ids.map((id) => <span className="run-role-chip is-skipped" key={`skipped-${id}`}>{roleName(id)} · {id} · skipped</span>)}</div>}
    {run.routing_plan && <div className="routing-plan-card"><span className="eyebrow">ROUTING PLAN</span><div className="routing-plan-grid"><div><span>Intent</span><strong>{run.routing_plan.intent ?? 'research'}</strong></div><div><span>Horizon</span><strong>{run.routing_plan.horizon ?? 'Derived from question'}</strong></div>{run.routing_plan.tickers?.length ? <div><span>Tickers</span><strong>{run.routing_plan.tickers.join(', ')}</strong></div> : null}</div>{run.routing_plan.rationale && <p>{run.routing_plan.rationale}</p>}</div>}
    {sourceRows.length > 0 && <div className="run-source-strip"><span>Sources in run</span>{sourceRows.map((source, index) => <button type="button" className="run-source-chip" key={source.id ?? source.source_ref ?? index} onClick={() => void onOpenSource(source)}><Icon name="book" size={13} />{source.title ?? source.id ?? source.source_ref}</button>)}</div>}
    <div className="run-detail-section"><h3>Complete question flow</h3>{displayTasks.length ? <div className="run-task-list">{displayTasks.map((task, index) => <RunTaskCard task={task} index={index} outputs={run.outputs} onOpenOutput={onOpenOutput} onControl={onControl} key={task.id ?? task.task_id ?? index} />)}</div> : <div className="run-empty-state"><strong>Tasks are still being prepared</strong><p>The backend accepted the question but has not returned its saved task records yet.</p></div>}</div>
    {run.timeline?.length ? <div className="run-detail-section"><details className="run-history-details"><summary>Saved flow history · {run.timeline.length} events</summary><ol className="run-timeline">{run.timeline.map((event, index) => { const payload = event.payload && typeof event.payload === 'object' ? event.payload as Record<string, unknown> : {}; const eventText = payload.message ?? event.message ?? event.summary ?? ''; return <li className="run-timeline-item" key={String(event.event_id ?? event.sequence_id ?? index)}><time>{dateLabel(String(event.emitted_at ?? event.created_at ?? ''))}</time><strong>{String(event.type ?? 'Flow update').replaceAll('_', ' ')}</strong>{eventText && <p>{String(eventText)}</p>}</li> })}</ol></details></div> : null}
    {revisionTasks.length > 0 && <div className="run-detail-section"><h3>PM revision discussion</h3>{pmRevisionRequests.length > 0 && <p className="run-revision-intro">The PM requested targeted answers before the final decision. Each response stays linked to its saved output.</p>}<div className="run-revision-list">{revisionTasks.map((task, index) => { const revisionOutput = task.output_id ? run.outputs.find((output) => (output.id ?? output.output_id) === task.output_id) : null; const challenges = task.revision_requests?.length ? task.revision_requests : revisionQuestions(pmRevisionRequests, task.agent_id); const response = revisionOutput?.conclusion ?? task.instruction ?? (task.current_task && task.current_task !== 'Finished — more evidence is needed.' ? task.current_task : null) ?? task.terminal_summary ?? 'Revision response retained in the saved task history.'; return <article className="run-revision-card" key={task.id ?? task.task_id ?? index}><strong>{task.agent_id ? `${task.agent_id} · ${roleName(task.agent_id)}` : 'Review handoff'}{task.title ? ` · ${task.title}` : ''}{task.review_disposition ? ` · ${statusLabel(task.review_disposition)}` : ''}</strong>{challenges.length > 0 && <div className="run-revision-challenge"><span>PM asked</span><ul>{challenges.map((challenge, challengeIndex) => <li key={`${task.id ?? index}-challenge-${challengeIndex}`}>{challenge}</li>)}</ul></div>}<p><span className="run-revision-response-label">Response</span>{response}</p>{revisionOutput && <button type="button" className="run-task-output" onClick={() => void onOpenOutput(revisionOutput)}><Icon name="book" size={13} /> Open revision response · {revisionOutput.title ?? 'Saved output'}</button>}</article> })}</div></div>}
    <div className="run-detail-actions"><div className="run-explain-box"><h3>Explain this result</h3><p>Ask for a concise explanation grounded in the saved packet, cited evidence, assumptions, objections, and review facts.</p><form className="run-action-form" onSubmit={explain}><textarea value={explanation} onChange={(event) => setExplanation(event.target.value)} placeholder="What should the saved result explain?" rows={3} /><div className="run-action-form-footer"><span className="run-action-hint">Uses the existing run context · no private model reasoning</span><button type="submit" className="button button-subtle" disabled={action === 'explain' || !explanation.trim()}><Icon name="message" size={14} /> {action === 'explain' ? 'Queuing…' : 'Explain result'}</button></div></form></div>{missing.length > 0 && <div className="run-research-box"><h3>Research missing evidence</h3><p>Start a bounded linked research run for an explicit gap. Existing outputs remain historical and unchanged.</p><form className="run-action-form" onSubmit={research}><textarea value={researchInstruction} onChange={(event) => setResearchInstruction(event.target.value)} placeholder="Describe the missing evidence to retrieve" rows={3} />{sourceRows.length > 0 && <fieldset className="source-picker"><legend>Carry relevant sources (optional)</legend>{sourceRows.map((source, index) => { const id = source.id ?? source.source_ref ?? `source-${index}`; const secondary = source.title && source.title !== id ? `${id} · ${source.locator ?? source.source_type ?? 'source'}` : source.locator ?? source.source_type ?? 'source'; return <label className="check-row" key={id}><input type="checkbox" checked={selectedSources.includes(id)} onChange={() => setSelectedSources((current) => current.includes(id) ? current.filter((value) => value !== id) : [...current, id])} /><span>{source.title ?? id}<small>{secondary}</small></span></label> })}</fieldset>}<div className="run-action-form-footer"><span className="run-action-hint">Creates a linked run · bounded to the stated evidence gap</span><button type="submit" className="button button-primary" disabled={action === 'research' || !researchInstruction.trim()}><Icon name="search" size={14} /> {action === 'research' ? 'Queuing…' : 'Research missing evidence'}</button></div></form></div>}</div>
    {message && <p className="form-message" role="status">{message}</p>}
  </section>
}

function CanonicalRunsView({ mode, runs, selectedRun, selectedRunId, namespace, office: _office, sources, onNavigate, onSelectRun, onOpenOutput, onOpenSource, onControl, onExplainResult, onResearchMissingEvidence, showHeader = true }: { mode: 'tasks' | 'decisions'; runs: RunSummary[]; selectedRun: RunDetail | null; selectedRunId?: string | null; namespace: Namespace; office: OfficeSnapshot; sources: EvidenceRef[]; onNavigate: WorkspaceProps['onNavigate']; onSelectRun: WorkspaceProps['onSelectRun']; onOpenOutput: WorkspaceProps['onOpenOutput']; onOpenSource: WorkspaceProps['onOpenSource']; onControl: WorkspaceProps['onControl']; onExplainResult: WorkspaceProps['onExplainResult']; onResearchMissingEvidence: WorkspaceProps['onResearchMissingEvidence']; showHeader?: boolean }) {
  const totalTasks = runs.reduce((sum, run) => sum + runCount(run, 'total'), 0) || (selectedRun?.tasks.length ?? 0)
  const activeTasks = runs.reduce((sum, run) => sum + (runHasOpenActions(run) ? runCount(run, 'active') : 0), 0)
  // A cancelled or completed parent closes its remaining task rows. Their
  // persisted blocked/failed states stay useful in the selected history, but
  // they are no longer current work requiring attention.
  const attentionTasks = runs.reduce((sum, run) => sum + (runHasOpenActions(run) ? runCount(run, 'blocked') + runCount(run, 'failed') + runCount(run, 'interrupted') : 0), 0)
  const terminalTasks = runs.reduce((sum, run) => sum + runCount(run, 'completed') + runCount(run, 'cancelled'), 0)
  const isTasks = mode === 'tasks'
  return <div className="workspace-page">{showHeader && <ViewHeader eyebrow={isTasks ? 'QUESTIONS / FLOWS' : 'INVESTMENT COMMITTEE / QUESTION JOURNAL'} title={isTasks ? 'Questions & tasks' : 'Decision journal'} copy={isTasks ? 'Follow each original question through routing, dependencies, evidence, review and saved outputs.' : 'Each card is an original question. Finished execution, evidence status and decision remain separate.'} namespace={namespace} actions={<button type="button" className="button button-subtle" onClick={() => onNavigate('office')}><Icon name="grid" size={15} /> Open office</button>} />}<div className="metric-strip"><Metric value={runs.length} label="Saved runs" tone="blue" /><Metric value={activeTasks} label="Active tasks" tone="green" /><Metric value={attentionTasks} label="Tasks needing attention" tone="amber" /><Metric value={terminalTasks || totalTasks} label={terminalTasks ? 'Finished tasks' : 'Tasks in selected flow'} tone="muted" /></div><div className="question-journal"><div className="question-journal-intro"><div><span className="eyebrow">ORIGINAL QUESTIONS</span><h2>{isTasks ? 'One saved record for every question' : 'Questions with answer history'}</h2><p>{isTasks ? 'Task counts and states come from saved runs, so visiting a desk cannot change the flow.' : 'Select a question to read the preferred current answer. Older attempts stay available in its run history.'}</p></div><span className="question-journal-count">{runs.length} saved run{runs.length === 1 ? '' : 's'}</span></div><QuestionGroupList runs={runs} selectedRunId={selectedRunId} namespace={namespace} onSelectRun={onSelectRun} /><RunDetailPanel run={selectedRun} sources={sources} onSelectRun={onSelectRun} onOpenOutput={onOpenOutput} onOpenSource={onOpenSource} onControl={onControl} onExplainResult={onExplainResult} onResearchMissingEvidence={onResearchMissingEvidence} onNavigate={onNavigate} /></div></div>
}

function ResearchView(props: WorkspaceProps) {
  const activeTab = props.researchTab ?? 'questions'
  const researchRuns = props.runs.filter((run) => !isRedditOrigin(run, props.runs))
  const selectedResearchRun = props.selectedRun && !isRedditOrigin(props.selectedRun, props.runs) ? props.selectedRun : null
  const setTab = (tab: ResearchTab) => {
    if (props.onResearchTab) props.onResearchTab(tab)
    else props.onNavigate(tab === 'coverage' ? 'coverage' : tab === 'simulation' ? 'simulation' : 'research')
  }
  return <div className="workspace-page research-page"><ViewHeader eyebrow="RESEARCH / WORKSPACE" title="Research" copy="Questions, coverage and bounded scenarios share one auditable research record." namespace={props.namespace} actions={<button type="button" className="button button-subtle" onClick={() => props.onNavigate('office')}><Icon name="grid" size={15} /> Open office</button>} /><nav className="research-tabs" aria-label="Research views" role="group"><button type="button" aria-pressed={activeTab === 'questions'} className={activeTab === 'questions' ? 'is-active' : ''} onClick={() => setTab('questions')}><Icon name="book" size={15} /> Questions</button><button type="button" aria-pressed={activeTab === 'coverage'} className={activeTab === 'coverage' ? 'is-active' : ''} onClick={() => setTab('coverage')}><Icon name="compass" size={15} /> Coverage</button><button type="button" aria-pressed={activeTab === 'simulation'} className={activeTab === 'simulation' ? 'is-active' : ''} onClick={() => setTab('simulation')}><Icon name="beaker" size={15} /> Scenarios</button></nav>{activeTab === 'questions' && <CanonicalRunsView mode="tasks" runs={researchRuns} selectedRun={selectedResearchRun} selectedRunId={selectedResearchRun?.id} namespace={props.namespace} office={props.office} sources={props.sources} onNavigate={props.onNavigate} onSelectRun={props.onSelectRun} onOpenOutput={props.onOpenOutput} onOpenSource={props.onOpenSource} onControl={props.onControl} onExplainResult={props.onExplainResult} onResearchMissingEvidence={props.onResearchMissingEvidence} showHeader={false} />}{activeTab === 'coverage' && <CoverageView {...props} embedded />}{activeTab === 'simulation' && <SimulationView {...props} embedded />}</div>
}

function TasksView(props: WorkspaceProps) {
  return <CanonicalRunsView mode="tasks" runs={props.runs} selectedRun={props.selectedRun} selectedRunId={props.selectedRun?.id} namespace={props.namespace} office={props.office} sources={props.sources} onNavigate={props.onNavigate} onSelectRun={props.onSelectRun} onOpenOutput={props.onOpenOutput} onOpenSource={props.onOpenSource} onControl={props.onControl} onExplainResult={props.onExplainResult} onResearchMissingEvidence={props.onResearchMissingEvidence} />
}

function PortfolioView({ portfolio, namespace, sources, onImport, onRiskSettings, onOpenSource }: WorkspaceProps) {
  const [importKind, setImportKind] = useState<'evidence' | 'transactions' | 'balances'>('evidence')
  const [importTitle, setImportTitle] = useState('')
  const [importContent, setImportContent] = useState('')
  const [supersedesId, setSupersedesId] = useState('')
  const [sourceUrl, setSourceUrl] = useState('')
  const [publicationAt, setPublicationAt] = useState('')
  const [observedAt, setObservedAt] = useState('')
  const [importing, setImporting] = useState(false)
  const [message, setMessage] = useState('')
  const [risk, setRisk] = useState({ max_position_weight: '', max_sector_weight: '', cash_floor: '' })
  const accounts = portfolio?.accounts ?? []
  const positions = portfolio?.positions ?? []
  const missing = portfolio?.missing_data ?? []

  async function submitImport(event: React.FormEvent) {
    event.preventDefault(); setImporting(true); setMessage('')
    try { await onImport({ kind: importKind, title: importTitle || 'Imported source', content: importContent, supersedes_id: supersedesId || null, source_url: sourceUrl || null, publication_at: publicationAt || null, observed_at: observedAt || null }); setMessage('Import staged. The backend will report imported or needs review.'); setImportTitle(''); setImportContent(''); setSupersedesId(''); setSourceUrl(''); setPublicationAt(''); setObservedAt('') } catch (error) { setMessage(error instanceof Error ? error.message : 'Import failed.') } finally { setImporting(false) }
  }

  async function saveRisk(event: React.FormEvent) {
    event.preventDefault()
    await onRiskSettings({ namespace, ...risk })
    setMessage('Risk settings saved to the local audit trail.')
  }

  function openSourceId(sourceId: unknown) {
    if (sourceId == null || !onOpenSource) return
    const id = String(sourceId)
    const source = sources.find((candidate) => candidate.id === id || candidate.source_ref === id)
    if (source) void onOpenSource(source)
  }

  return <div className="workspace-page">
    <ViewHeader eyebrow="PORTFOLIO / OBSERVATIONS" title="Portfolio" copy="Review saved account balances and positions, or import a new statement." namespace={namespace} />
    <div className="metric-strip"><Metric value={accounts.length} label="Accounts" tone="blue" /><Metric value={positions.length} label="Positions" tone="green" /><Metric value={portfolio?.reconciliation_status ?? 'Unknown'} label="Reconciliation" tone="amber" wide /><Metric value={portfolio?.as_of ? dateLabel(portfolio.as_of) : 'Unknown'} label="Portfolio as of" tone="muted" wide /></div>
    <div className="two-column-workspace">
      <section className="workspace-card">
        <div className="section-heading"><div><span className="eyebrow">ACCOUNT LEDGER</span><h2>Accounts and balances</h2></div><span className="muted-copy">{accounts.length ? 'Values hidden by default' : 'No account records returned'}</span></div>
        {accounts.length ? <PrivateFinancialDisclosure label={`Reveal ${accounts.length} saved account ${accounts.length === 1 ? 'record' : 'records'}`} description="Account names, balances, and observation details are shown only after you choose to reveal them on this device.">
          <div className="account-list">{accounts.map((account: any, index: number) => { const accountType = String(account.account_type ?? 'unknown'); const isTfsa = accountType.toLowerCase().includes('tfsa'); return <article className="account-row" key={String(account.id ?? index)}><div><strong>{String(account.name ?? account.account_type ?? 'Account')}</strong><span>{accountType} · {String(account.reconciliation_status ?? 'status unknown')}</span>{account.interest_rate != null && <span className="account-note">Stated interest {percentage(account.interest_rate)} · period {String(account.interest_rate_period ?? 'unknown')} · compounding {String(account.interest_compounding ?? 'unknown')}</span>}{isTfsa && <span className="account-note">TFSA balance composition unknown; this is a supplied observation.</span>}</div><div className="account-balances">{Array.isArray(account.balances) && account.balances.length ? account.balances.map((balance: any, balanceIndex: number) => <span key={balanceIndex}>{money(balance.amount, balance.currency)}<small>{balance.status ?? 'status unknown'} · observed {dateLabel(balance.observed_at)}{balance.unknown_reason ? ` · ${balance.unknown_reason}` : ''}{balance.source_id ? <button type="button" className="inline-source" onClick={() => openSourceId(balance.source_id)}>Source</button> : null}</small></span>) : <span>Balance unknown</span>}</div><span className="account-observed">Observed {dateLabel(account.observed_at)}</span></article> })}</div>
        </PrivateFinancialDisclosure> : <Empty icon="briefcase" title="No portfolio observations" copy="Import a statement or balance observation to begin. The system will not infer a balance or current holding." />}
      </section>
      <section className="workspace-card">
        <div className="section-heading"><div><span className="eyebrow">POSITIONS</span><h2>Position observations</h2></div><span className="muted-copy">{positions.length ? 'Values hidden by default' : 'No positions returned'}</span></div>
        {positions.length ? <PrivateFinancialDisclosure label={`Reveal ${positions.length} saved position ${positions.length === 1 ? 'record' : 'records'}`} description="Symbols, quantities, cost basis, and supplied portfolio values are shown only after you choose to reveal them on this device.">
          <div className="position-list">{positions.map((position: any, index: number) => <article className="position-row" key={String(position.id ?? index)}><strong>{String(position.symbol ?? 'Unknown symbol')}</strong><span>{quantity(position.quantity)} units</span><span>{money(position.cost_basis, position.currency)} cost basis</span>{position.market_value != null && <span>{money(position.market_value, position.market_value_currency)} supplied market value observation</span>}<small>{String(position.status ?? 'status unknown')} · observed {dateLabel(position.observed_at)}{position.unknown_reason ? ` · ${position.unknown_reason}` : ''}{position.source_id ? <button type="button" className="inline-source" onClick={() => openSourceId(position.source_id)}>Source</button> : null}</small></article>)}</div>
        </PrivateFinancialDisclosure> : <Empty icon="scale" title="No positions returned" copy="A recommendation never counts as a fill. Position detail only appears after a supplied observation." />}
      </section>
    </div>
    {missing.length > 0 && <section className="notice-card notice-amber"><Icon name="warning" size={18} /><div><strong>Missing data</strong><ul>{missing.map((item: string, index: number) => <li key={index}>{String(item)}</li>)}</ul></div></section>}
    <div className="two-column-workspace"><section className="workspace-card"><div className="section-heading"><div><span className="eyebrow">IMPORT EVIDENCE</span><h2>Stage a source</h2></div></div><p className="muted-copy">Use text, Markdown or CSV. Imported content is immutable and gets a source id, timestamps and a validation result.</p><form className="stack-form" onSubmit={submitImport}><label>Kind<select value={importKind} onChange={(event) => setImportKind(event.target.value as typeof importKind)}><option value="evidence">Evidence document</option><option value="balances">Balance observation</option><option value="transactions">Transaction CSV</option></select></label><label>Title<input required value={importTitle} onChange={(event) => setImportTitle(event.target.value)} placeholder="e.g. Q2 account statement" /></label><label>Source URL (optional)<input type="url" value={sourceUrl} onChange={(event) => setSourceUrl(event.target.value)} placeholder="https://…" /></label><div className="form-grid"><label>Publication date<input type="datetime-local" value={publicationAt} onChange={(event) => setPublicationAt(event.target.value)} /></label><label>Observation date<input type="datetime-local" value={observedAt} onChange={(event) => setObservedAt(event.target.value)} /></label></div><label>Supersedes source (optional)<select value={supersedesId} onChange={(event) => setSupersedesId(event.target.value)}><option value="">New source version</option>{sources.map((source, index) => <option key={source.id ?? index} value={source.id ?? source.source_ref}>{source.title ?? source.id}</option>)}</select></label><label>Content / CSV<textarea required value={importContent} onChange={(event) => setImportContent(event.target.value)} rows={7} placeholder="Paste the retained content or supported CSV headers" /></label><button type="submit" className="button button-primary" disabled={importing}><Icon name="upload" size={15} /> {importing ? 'Staging…' : 'Stage import'}</button>{message && <p className="form-message" role="status">{message}</p>}</form></section><section className="workspace-card"><div className="section-heading"><div><span className="eyebrow">RISK SETTINGS</span><h2>Constraints before sizing</h2></div></div><p className="muted-copy">Weights and cash floor are fractions from 0 to 1. Empty settings remain unknown and cause sizing to defer.</p><form className="stack-form" onSubmit={saveRisk}><label>Maximum position weight <input value={risk.max_position_weight} onChange={(event) => setRisk({ ...risk, max_position_weight: event.target.value })} placeholder="fraction, e.g. 0.10 (10%)" /></label><label>Maximum sector weight <input value={risk.max_sector_weight} onChange={(event) => setRisk({ ...risk, max_sector_weight: event.target.value })} placeholder="fraction, e.g. 0.25 (25%)" /></label><label>Cash floor <input value={risk.cash_floor} onChange={(event) => setRisk({ ...risk, cash_floor: event.target.value })} placeholder="fraction, e.g. 0.05 (5%)" /></label><button type="submit" className="button button-subtle"><Icon name="scale" size={15} /> Save constraints</button></form></section></div>
  </div>
}

function MemoryView({ memory, namespace, sources, onSearchMemory, onExport, onOpenOutput, onOpenSource, onSourceVersions }: WorkspaceProps) {
  const [query, setQuery] = useState('')
  const [kind, setKind] = useState('all')
  const [selected, setSelected] = useState<MemoryRecord | null>(null)
  const [compare, setCompare] = useState(false)
  const [versions, setVersions] = useState<EvidenceRef[]>([])
  const [loadingVersions, setLoadingVersions] = useState(false)
  const versionsRequestRef = useRef(0)
  function submit(event: React.FormEvent) { event.preventDefault(); onSearchMemory(query, kind) }
  async function selectRecord(record: MemoryRecord) {
    ++versionsRequestRef.current
    setSelected(record)
    setCompare(false)
    setVersions([])
    const recordId = record.id
    if (!recordId) return
    if (record.type === 'source') {
      const known = sources.find((source) => source.id === recordId || source.source_ref === recordId)
      await onOpenSource({ ...(known ?? {}), id: recordId, source_ref: recordId, title: known?.title ?? record.title, excerpt: known?.excerpt ?? record.summary })
    } else if (record.type === 'research' || record.type === 'output') {
      await onOpenOutput({ id: recordId, output_id: recordId, title: record.title, conclusion: record.summary, provenance: 'real_research' })
    }
  }
  async function toggleCompare() {
    if (compare) { ++versionsRequestRef.current; setCompare(false); setVersions([]); setLoadingVersions(false); return }
    setCompare(true)
    if (selected?.type !== 'source' || !selected.id) return
    const requestId = ++versionsRequestRef.current
    setLoadingVersions(true)
    try {
      const nextVersions = await onSourceVersions(selected.id)
      if (requestId === versionsRequestRef.current) setVersions(nextVersions)
    } finally {
      if (requestId === versionsRequestRef.current) setLoadingVersions(false)
    }
  }
  function exportRecord() {
    if (!selected) return
    const body = JSON.stringify({ ...selected, versions: versions.length ? versions : undefined }, null, 2)
    const url = URL.createObjectURL(new Blob([body], { type: 'application/json' }))
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = `researchcouncil-${namespace}-${selected.id ?? 'record'}.json`; anchor.click(); URL.revokeObjectURL(url)
  }
  const selectedIsPrivate = memoryRecordHasPrivateMaterial(selected)
  return <div className="workspace-page"><ViewHeader eyebrow="MEMORY / SOURCE LINEAGE" title="Evidence" copy="Search saved sources, research and decisions. Open a record to see its origin and related work." namespace={namespace} actions={<div className="button-row"><button type="button" className="button button-subtle" onClick={() => onExport('markdown')}><Icon name="download" size={15} /> Markdown export</button><button type="button" className="button button-subtle" onClick={() => onExport('json')}><Icon name="download" size={15} /> JSON archive</button></div>} /><section className="memory-search-card"><form onSubmit={submit}><div className="search-input-wrap"><Icon name="search" size={18} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search source ids, claims, decisions…" aria-label="Search memory" /><button type="submit" className="button button-primary">Search</button></div><div className="memory-filters"><span>Filter class</span>{['all', 'fact', 'research', 'decision', 'source', 'simulation'].map((item) => <button type="button" key={item} className={kind === item ? 'is-active' : ''} onClick={() => { setKind(item); onSearchMemory(query, item) }}>{item}</button>)}</div></form></section><div className={`memory-layout${selected ? ' has-detail' : ''}`}><section className="workspace-card memory-results"><div className="section-heading"><div><span className="eyebrow">SEARCH RESULTS</span><h2>{memory.length ? `${memory.length} records` : 'No records yet'}</h2></div><span className="muted-copy">Scenario records stay separate from research.</span></div>{memory.length ? <div className="memory-list">{memory.map((record, index) => { const privateRecord = memoryRecordHasPrivateMaterial(record); return <button type="button" className={`memory-row${selected?.id === record.id ? ' is-selected' : ''}`} key={record.id ?? index} onClick={() => void selectRecord(record)}><span className={`memory-kind kind-${record.type}`}>{record.type ?? 'record'}</span><span><strong>{record.title}</strong><small>{privateRecord ? 'Personal account material hidden by default.' : record.summary ?? 'No excerpt saved.'}</small><small>{record.source_refs?.length ?? 0} sources · updated {dateLabel(record.updated_at ?? record.created_at)}</small></span><Icon name="chevron" size={15} /></button> })}</div> : <Empty icon="database" title="Search the memory ledger" copy="Run a query or import a source. The backend keeps source content and relationships available for audit." />}</section>{selected && <section className="workspace-card memory-detail"><div className="section-heading"><div><span className="eyebrow">RECORD DETAIL</span><h2>{selected.title}</h2></div><button type="button" className="icon-button" onClick={() => { ++versionsRequestRef.current; setSelected(null); setCompare(false); setVersions([]); setLoadingVersions(false) }} aria-label="Close memory detail"><Icon name="close" /></button></div><div className="memory-detail-actions"><button type="button" className={`button button-subtle${compare ? ' is-active' : ''}`} onClick={() => void toggleCompare()}><Icon name="scale" size={15} /> {compare ? 'Comparing versions' : 'Compare versions'}</button><button type="button" className="button button-subtle" onClick={exportRecord}><Icon name="download" size={15} /> Export record</button></div>{selectedIsPrivate ? <PrivateFinancialDisclosure label="Reveal retained account material locally" description="This saved record may contain account or allocation values. It stays closed until you choose to reveal it on this device."><p className="memory-content">{selected.content ?? selected.summary ?? 'No retained content returned.'}</p></PrivateFinancialDisclosure> : <p className="memory-content">{selected.content ?? selected.summary ?? 'No retained content returned.'}</p>}{compare && <div className="compare-box"><span className="eyebrow">VERSION COMPARE</span>{loadingVersions ? <p>Loading immutable source versions…</p> : selected.type !== 'source' ? <p>Only source records expose immutable content versions. Open a source record to compare amendments.</p> : versions.length > 0 ? <div className="version-list">{versions.map((version, index) => { const privateVersion = memoryRecordHasPrivateMaterial(version); return <article key={version.id ?? index}><strong>Version {version.version ?? index + 1}</strong><span>{version.content_hash ?? 'Hash unavailable'} · {dateLabel(version.retrieved_at ?? version.observation_time)}</span>{privateVersion ? <PrivateFinancialDisclosure label="Reveal this retained version locally" description="Personal account material remains closed until you choose to inspect it."><p>{version.content ?? version.excerpt ?? 'No retained content.'}</p></PrivateFinancialDisclosure> : <p>{version.content ?? version.excerpt ?? 'No retained content.'}</p>}</article> })}</div> : <p>No immutable versions returned for this source.</p>}</div>}<div className="backlinks"><span className="eyebrow">BACKLINKS / RELATED</span>{selected.backlinks?.length ? selected.backlinks.map((backlink, index) => <button type="button" key={backlink.id ?? index} onClick={() => { const next = memory.find((item) => item.id === backlink.id); if (next) void selectRecord(next) }}><Icon name="link" size={14} /> {backlink.title ?? backlink.id}</button>) : <p className="muted-copy">No related records returned.</p>}</div></section>}</div></div>
}

function redditStatus(post: RedditPostRecord) {
  return String(post.status ?? post.state ?? post.dispatch_state ?? 'retained').toLowerCase().replaceAll('-', '_').replaceAll(' ', '_')
}

function normalizeKey(value: unknown) {
  return String(value ?? '').toLowerCase().replaceAll('-', '_').replaceAll(' ', '_')
}

function redditConnectionLabel(value: unknown, connected = false) {
  if (connected || normalizeKey(value) === 'connected' || normalizeKey(value) === 'ready') return 'Connected'
  const state = normalizeKey(value)
  if (state === 'configured_unchecked') return 'Credentials saved'
  if (state === 'missing_credentials' || state === 'disconnected') return 'Credentials needed'
  if (state === 'error' || state === 'unavailable') return 'Unavailable'
  return 'Not checked'
}

type RedditStage = {
  key: 'awaiting_screening' | 'screening' | 'thesis_research' | 'yolo_ticker_research' | 'skipped' | 'processed'
  label: 'Awaiting screening' | 'Screening' | 'Thesis research' | 'YOLO ticker research' | 'Skipped' | 'Result available'
  classification: RedditTriageClassification | null
}

function redditStage(post: RedditPostRecord): RedditStage {
  const state = redditStatus(post)
  const classification = post.triage?.classification ?? null
  if (classification === 'skip' || state === 'dismissed') return { key: 'skipped', label: 'Skipped', classification }
  if (classification === 'thesis') return { key: 'thesis_research', label: 'Thesis research', classification }
  if (classification === 'yolo_ticker') return { key: 'yolo_ticker_research', label: 'YOLO ticker research', classification }
  if (['processed', 'reused', 'researched', 'completed'].includes(state)) return { key: 'processed', label: 'Result available', classification }
  if (state === 'processing' || state === 'screening') return { key: 'screening', label: 'Screening', classification }
  return { key: 'awaiting_screening', label: 'Awaiting screening', classification }
}

function redditTriageReason(post: RedditPostRecord) {
  const candidates = [post.triage?.reason, post.reason, post.backlog_reason, post.dispatch_error, post.error]
  return candidates.map(meaningfulText).find((value): value is string => Boolean(value)) ?? null
}

function sameRedditReason(first: unknown, second: unknown) {
  const left = meaningfulText(first)
  const right = meaningfulText(second)
  if (!left || !right) return false
  return left.toLowerCase().replace(/\s+/g, ' ') === right.toLowerCase().replace(/\s+/g, ' ')
}

type RedditInboxFilter = 'results' | 'in_progress' | 'queue' | 'skipped' | 'needs_attention' | 'all'

const REDDIT_INBOX_FILTERS: Array<{ id: RedditInboxFilter; label: string; apiStatus?: string; statuses: string[] }> = [
  { id: 'results', label: 'Results', apiStatus: 'processed', statuses: ['processed', 'reused', 'researched', 'completed'] },
  { id: 'in_progress', label: 'In progress', apiStatus: 'processing', statuses: ['processing', 'screening', 'dispatched'] },
  { id: 'queue', label: 'Queue', apiStatus: 'queued', statuses: ['queued', 'retained', 'backlog'] },
  { id: 'skipped', label: 'Skipped', apiStatus: 'dismissed', statuses: ['dismissed', 'skipped'] },
  { id: 'needs_attention', label: 'Needs attention', apiStatus: 'attention', statuses: ['attention', 'blocked', 'failed', 'error'] },
  { id: 'all', label: 'All', statuses: [] },
]

function redditFilterDefinition(filter: RedditInboxFilter) {
  return REDDIT_INBOX_FILTERS.find((item) => item.id === filter) ?? REDDIT_INBOX_FILTERS[0]
}

function redditFilterFromApi(value: unknown): RedditInboxFilter {
  const status = String(value ?? '').toLowerCase().replaceAll('-', '_').replaceAll(' ', '_')
  if (status === 'all') return 'all'
  if (status === 'processing' || status === 'screening' || status === 'in_progress') return 'in_progress'
  if (status === 'queued' || status === 'retained' || status === 'backlog' || status === 'queue') return 'queue'
  if (status === 'dismissed' || status === 'skipped') return 'skipped'
  if (status === 'attention' || status === 'needs_attention' || status === 'blocked' || status === 'failed') return 'needs_attention'
  if (status === 'processed' || status === 'reused' || status === 'researched' || status === 'completed' || status === 'results') return 'results'
  return 'results'
}

function redditPostMatchesFilter(post: RedditPostRecord, filter: RedditInboxFilter) {
  if (filter === 'all') return true
  const definition = redditFilterDefinition(filter)
  const state = redditStatus(post)
  if (filter === 'needs_attention') return definition.statuses.includes(state) || Boolean(meaningfulText(post.error) || meaningfulText(post.dispatch_error))
  return definition.statuses.includes(state)
}

function redditCount(inbox: RedditInboxSnapshot, statuses: string[], fallback = 0) {
  const counts = inbox.counts ?? {}
  const values = statuses.map((status) => counts[status]).filter((value): value is number => typeof value === 'number' && Number.isFinite(value))
  return values.length ? values.reduce((sum, value) => sum + value, 0) : fallback
}


function RedditInboxView({ namespace, redditInbox, redditConnection, onRefreshReddit, onLoadMoreReddit, onDispatchReddit, onSaveRedditSettings, onSelectRun, onNavigate, selectedRun, runs, sources, onOpenOutput, onOpenSource, onControl, onExplainResult, onResearchMissingEvidence }: WorkspaceProps) {
  const inbox = redditInbox ?? { posts: [], availability: 'not_checked' }
  const connection = redditConnection ?? inbox.connection ?? {}
  const [subreddit, setSubreddit] = useState(connection.subreddit ?? 'wallstreetbets')
  const [windowDays, setWindowDays] = useState(connection.window_days ?? 7)
  const [enabled, setEnabled] = useState(Boolean(connection.enabled ?? connection.connected))
  const [saving, setSaving] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [filterLoading, setFilterLoading] = useState(false)
  const [selectedResultRunId, setSelectedResultRunId] = useState<string | null>(null)
  const [selectedResultTitle, setSelectedResultTitle] = useState<string | null>(null)
  const initialFilter = redditFilterFromApi(inbox.filter_status ?? inbox.status)
  const [filter, setFilter] = useState<RedditInboxFilter>(initialFilter)

  useEffect(() => {
    if (connection.subreddit) setSubreddit(connection.subreddit)
    if (connection.window_days != null) setWindowDays(connection.window_days)
    if (connection.enabled != null || connection.connected != null) setEnabled(Boolean(connection.enabled ?? connection.connected))
  }, [connection.connected, connection.enabled, connection.subreddit, connection.window_days])

  useEffect(() => {
    // A namespace switch remounts the workspace and the API snapshot carries
    // the current server-side filter. Keep the default focused on results.
    if (inbox.filter_status != null || inbox.status != null) setFilter(redditFilterFromApi(inbox.filter_status ?? inbox.status))
  }, [inbox.filter_status, inbox.status])

  async function save(event: React.FormEvent) {
    event.preventDefault()
    if (!onSaveRedditSettings) return
    setSaving(true)
    try {
      await onSaveRedditSettings({ subreddit: subreddit.trim() || 'wallstreetbets', window_days: Math.max(1, Math.min(30, Number(windowDays) || 7)), enabled })
    } finally { setSaving(false) }
  }

  async function selectFilter(nextFilter: RedditInboxFilter) {
    if (nextFilter === filter && !filterLoading) return
    const definition = redditFilterDefinition(nextFilter)
    setFilter(nextFilter)
    setFilterLoading(true)
    try {
      await onRefreshReddit?.({ status: definition.apiStatus ?? 'all', limit: 100, offset: 0 })
    } finally { setFilterLoading(false) }
  }

  async function loadMore() {
    if (!onLoadMoreReddit || loadingMore || filterLoading) return
    setLoadingMore(true)
    try { await onLoadMoreReddit({ status: redditFilterDefinition(filter).apiStatus ?? 'all' }) } finally { setLoadingMore(false) }
  }

  const posts = inbox.posts ?? []
  const visiblePosts = useMemo(() => posts.filter((post) => redditPostMatchesFilter(post, filter)), [filter, posts])
  const backlog = redditCount(inbox, ['queued', 'blocked', 'failed'], inbox.backlog_count ?? posts.filter((post) => ['queued', 'blocked', 'backlog'].includes(redditStatus(post))).length)
  const researched = redditCount(inbox, ['processed', 'reused', 'researched'], inbox.researched_count ?? posts.filter((post) => ['processed', 'reused', 'researched'].includes(redditStatus(post))).length)
  const skipped = redditCount(inbox, ['dismissed', 'skipped'], inbox.skipped_count ?? posts.filter((post) => redditStatus(post) === 'dismissed' || post.triage?.classification === 'skip').length)
  const inProgress = redditCount(inbox, ['processing', 'screening', 'dispatched'], posts.filter((post) => ['processing', 'screening', 'dispatched'].includes(redditStatus(post))).length)
  const attention = redditCount(inbox, ['blocked', 'failed', 'attention'], posts.filter((post) => ['blocked', 'failed', 'attention'].includes(redditStatus(post))).length)
  const connectionStatusRaw = String(connection.status ?? (connection.connected ? 'connected' : 'not checked'))
  const connectionStatus = redditConnectionLabel(connectionStatusRaw, Boolean(connection.connected))
  const coverageDays = connection.window_days ?? 7
  const intakeAvailability = String(inbox.availability ?? 'not_checked').toLowerCase().replaceAll('-', '_').replaceAll(' ', '_')
  const intakeUnavailable = intakeAvailability === 'unavailable' || intakeAvailability === 'error' || connection.status === 'error'
  const intakeChecked = posts.length > 0 || (['available', 'empty'].includes(intakeAvailability) && connection.connected === true && connection.coverage_complete != null)
  const countValue = (value: number | undefined, fallback: number) => intakeChecked ? (value ?? fallback) : '—'
  const hasMore = Boolean(inbox.has_more)
  const selectedRedditPost = selectedRun
    ? posts.find((post) => (post.run_id ?? post.reused_run_id) === selectedRun.id || (selectedRun.root_run_id && (post.run_id ?? post.reused_run_id) === selectedRun.root_run_id))
    : null
  const selectedRedditRun = selectedRun && (selectedRun.id === selectedResultRunId || isRedditOrigin(selectedRun, runs) || selectedRedditPost) ? selectedRun : null
  const connectionStatusKey = connectionStatusRaw.toLowerCase().replaceAll('-', '_').replaceAll(' ', '_')
  const setupCopy = connectionStatusKey === 'missing_credentials'
    ? 'Add Reddit credentials in the private backend configuration, then enable local intake.'
    : 'Enable local intake to start retrieval, then refresh status to see available posts.'
  const sameIntakeError = Boolean(inbox.error_message && connection.reason && inbox.error_message === connection.reason)
  const parallelLimit = inbox.parallel_limit ?? 3
  const activePostCount = inbox.active_post_count
  const availableSlots = inbox.available_slots
  const hasCapacity = activePostCount != null || availableSlots != null || inbox.parallel_limit != null

  return <div className="workspace-page reddit-page">
    <ViewHeader eyebrow="INBOX / REDDIT INTAKE" title="Research inbox" copy={`Retrieve available posts from the requested ${coverageDays}-day r/wallstreetbets window, report coverage gaps, and keep reused research linked to its original run.`} namespace={namespace} actions={<div className="reddit-header-actions"><span className="reddit-capacity-note" aria-live="polite">{hasCapacity ? `${activePostCount ?? Math.max(0, Number(parallelLimit) - Number(availableSlots ?? parallelLimit))} of ${parallelLimit} research slots in use` : 'Up to 3 research slots at once'}</span><button type="button" className="button button-subtle" onClick={() => void onRefreshReddit?.()}><Icon name="refresh" size={15} /> Refresh status</button></div>} />
    {intakeUnavailable ? <section className="notice-card notice-amber reddit-intake-notice"><Icon name="warning" size={18} /><div><strong>Reddit intake is unavailable</strong><p>{inbox.error_message ?? 'The local connector did not return an intake snapshot.'} Existing retained records stay visible; no count is inferred from this response.</p></div></section> : !intakeChecked ? <section className="notice-card notice-amber reddit-intake-notice"><Icon name="clock" size={18} /><div><strong>Reddit intake has not been checked</strong><p>{setupCopy}</p></div></section> : <section className="notice-card notice-blue reddit-intake-notice"><Icon name="download" size={18} /><div><strong>Coverage window: {inbox.coverage_start ? dateLabel(inbox.coverage_start) : `last ${coverageDays} days`} → {inbox.coverage_end ? dateLabel(inbox.coverage_end) : 'now'}</strong><p>Available posts are retained from the requested window. Listing caps or connector gaps are reported below; credentials stay in the local runtime and never enter this browser.</p>{connection.coverage_reason && <p>Coverage note: {connection.coverage_reason}</p>}</div></section>}
    <div className="metric-strip"><Metric value={countValue(inbox.retained_count, posts.length)} label="Retained posts" tone="blue" /><Metric value={countValue(inbox.backlog_count, backlog)} label="Backlog" tone="amber" /><Metric value={countValue(inbox.researched_count, researched)} label="Researched" tone="green" /><Metric value={countValue(inbox.skipped_count, skipped)} label="Skipped" tone="muted" /></div>
    <section className="workspace-card reddit-inbox-toolbar" aria-label="Reddit inbox filters"><div className="reddit-filter-heading"><div><span className="eyebrow">WORK QUEUES</span><h2>Find a retained post by state</h2></div><span className="muted-copy">Counts include records outside the loaded page.</span></div><div className="reddit-filter-tabs" role="group" aria-label="Reddit inbox views">{REDDIT_INBOX_FILTERS.map((item) => { const count = item.id === 'results' ? researched : item.id === 'in_progress' ? inProgress : item.id === 'queue' ? redditCount(inbox, ['queued'], posts.filter((post) => ['queued', 'retained', 'backlog'].includes(redditStatus(post))).length) : item.id === 'skipped' ? skipped : item.id === 'needs_attention' ? attention : countValue(inbox.retained_count, inbox.total ?? posts.length); return <button type="button" aria-pressed={filter === item.id} className={`reddit-filter-tab${filter === item.id ? ' is-active' : ''}`} onClick={() => void selectFilter(item.id)} disabled={filterLoading && filter !== item.id}><span>{item.label}</span><strong>{count}</strong></button> })}</div></section>
    {selectedRedditRun && <RunDetailPanel run={selectedRedditRun} detailTitle={selectedRedditPost?.title ?? selectedResultTitle} sources={sources} onSelectRun={onSelectRun} onNavigate={onNavigate} onOpenOutput={onOpenOutput} onOpenSource={onOpenSource} onControl={onControl} onExplainResult={onExplainResult} onResearchMissingEvidence={onResearchMissingEvidence} />}
    <div className="two-column-workspace reddit-layout"><section className="workspace-card"><div className="section-heading"><div><span className="eyebrow">{redditFilterDefinition(filter).label.toUpperCase()} / RETAINED POSTS</span><h2>{redditFilterDefinition(filter).label}</h2></div><span className="muted-copy">{intakeUnavailable || !intakeChecked ? 'Count unavailable' : `${visiblePosts.length} loaded${hasMore ? ' · more available' : ''}`}</span></div>{filterLoading ? <div className="reddit-filter-loading" role="status"><Icon name="refresh" size={16} /> Loading {redditFilterDefinition(filter).label.toLowerCase()}…</div> : visiblePosts.length ? <div className="reddit-post-list">{visiblePosts.map((post, index) => { const state = redditStatus(post); const stage = redditStage(post); const triage = post.triage; const triageReason = redditTriageReason(post); const skippedCard = stage.key === 'skipped' || stage.classification === 'skip' || state === 'dismissed'; const researchEligible = !skippedCard && (stage.classification === 'thesis' || stage.classification === 'yolo_ticker'); const postId = post.id ?? post.post_id ?? 'post-' + index; const runId = post.run_id ?? post.reused_run_id; const canDispatch = !runId && !skippedCard && ['retained', 'queued', 'blocked', 'failed'].includes(state); const dispatchReason = meaningfulText(post.dispatch_error); const backlogReason = meaningfulText(post.backlog_reason); const showDispatchReason = dispatchReason && !sameRedditReason(dispatchReason, triageReason); const showBacklogReason = backlogReason && !sameRedditReason(backlogReason, triageReason) && !sameRedditReason(backlogReason, dispatchReason); const viewResult = runId && ['processed', 'reused', 'researched', 'completed'].includes(state); return <article className={`reddit-post${selectedRedditPost && (selectedRedditPost.id ?? selectedRedditPost.post_id) === postId ? ' is-selected' : ''}`} key={postId}><div className="reddit-post-heading"><div><span className="reddit-community">r/{post.subreddit ?? connection.subreddit ?? 'wallstreetbets'}{(post.source_flair ?? post.flair) ? <span className="reddit-flair"> · {post.source_flair ?? post.flair}</span> : null}</span><h3>{post.title ?? 'Untitled Reddit intake'}</h3></div><span className={`reddit-state reddit-state-${state} reddit-stage-${stage.key}`}>{stage.label}</span></div><p className="reddit-post-meta">{post.author ? `u/${post.author} · ` : ''}{dateLabel(post.created_at ?? post.ingested_at)}{post.score != null ? ` · ${post.score} score` : ''}{post.comments != null ? ` · ${post.comments} comments` : ''}{post.dispatch_state ? ` · dispatch ${String(post.dispatch_state).replaceAll('_', ' ')}` : ''} · status {stage.label.toLowerCase()}</p>{triageReason && <p className={`reddit-triage-reason reddit-triage-reason-${stage.key}`}><strong>{stage.label === 'Skipped' ? 'Skip reason:' : 'Triage reason:'}</strong> {triageReason}</p>}{triage?.thesis_summary && <p className="reddit-triage-summary"><strong>{stage.classification === 'thesis' ? 'Thesis summary:' : 'Research focus:'}</strong> {triage.thesis_summary}</p>}{triage?.tickers?.length ? <div className="reddit-ticker-list" aria-label="Research tickers">{triage.tickers.map((ticker) => <span className="reddit-ticker-chip" key={ticker}>{ticker}</span>)}</div> : null}{showDispatchReason && <p className="reddit-post-note"><strong>Dispatch:</strong> {dispatchReason}</p>}{(post.body ?? post.text) && <p className="reddit-post-body">{String(post.body ?? post.text)}</p>}{showBacklogReason && <p className="reddit-post-note"><strong>Backlog:</strong> {backlogReason}</p>}{post.reuse_reason && <p className="reddit-post-note"><strong>Reuse:</strong> {post.reuse_reason}</p>}{triage?.evidence_excerpt && <details className="reddit-evidence-details"><summary>View cited excerpt</summary><blockquote>{triage.evidence_excerpt}</blockquote></details>}<div className="reddit-post-actions">{post.permalink || post.url ? <a href={post.permalink ?? post.url ?? undefined} target="_blank" rel="noreferrer"><Icon name="external" size={13} /> Open post</a> : null}{canDispatch && onDispatchReddit && <button type="button" className="button button-primary" onClick={() => void onDispatchReddit(postId)}><Icon name="play" size={13} /> {researchEligible ? (stage.classification === 'thesis' ? 'Dispatch thesis research' : 'Dispatch ticker research') : 'Screen post'}</button>}{runId && <button type="button" className={`button ${viewResult ? 'button-primary' : 'button-subtle'}`} onClick={() => { setSelectedResultRunId(runId); setSelectedResultTitle(post.title ?? null); void onSelectRun?.(runId) }}><Icon name="book" size={13} /> {viewResult ? 'View result' : 'Open saved run'}</button>}{post.reused_run_id && <span className="reddit-link-note">Reused run {post.reused_run_id}</span>}</div></article> })}</div> : <Empty icon="download" title={intakeUnavailable ? 'Reddit intake unavailable' : intakeChecked ? `No ${redditFilterDefinition(filter).label.toLowerCase()} found` : 'No intake check yet'} copy={intakeUnavailable ? (inbox.error_message ?? 'The local connector did not return a snapshot; zero posts are not inferred.') : intakeChecked ? `No retained posts currently match ${redditFilterDefinition(filter).label.toLowerCase()}. Totals include records outside this loaded page.` : setupCopy} />}{hasMore && <div className="reddit-load-more"><button type="button" className="button button-subtle" onClick={() => void loadMore()} disabled={loadingMore || filterLoading || !onLoadMoreReddit}><Icon name="download" size={14} /> {loadingMore ? 'Loading more…' : `Load more ${redditFilterDefinition(filter).label.toLowerCase()}`}</button><small>Older retained records remain available through the next page.</small></div>}</section><section className="workspace-card reddit-settings-card"><div className="section-heading"><div><span className="eyebrow">CONNECTION SETTINGS</span><h2>Local Reddit intake</h2></div><span className={`reddit-connection-state ${connection.connected ? 'is-connected' : ''}`}>{connectionStatus}</span></div><p className="muted-copy">The connector runs locally. Only subreddit, window and dispatch state are shown here.</p><form className="stack-form" onSubmit={save}><label>Subreddit<input value={subreddit} onChange={(event) => setSubreddit(event.target.value)} placeholder="wallstreetbets" /></label><label>Coverage window (days)<input type="number" min={1} max={30} value={windowDays} onChange={(event) => setWindowDays(Number(event.target.value))} /></label><label className="check-row"><input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} /><span>Enable local intake<small>Save the requested window and let the local connector retrieve available posts; credentials never enter the browser.</small></span></label><button type="submit" className="button button-primary" disabled={saving || !onSaveRedditSettings}><Icon name="check" size={15} /> {saving ? 'Saving…' : 'Save connection settings'}</button></form>{connection.reason && !sameIntakeError && <p className="form-message">{connection.reason}</p>}{inbox.error_message && !intakeUnavailable && !sameIntakeError && <p className="reddit-post-note">Intake note: {inbox.error_message}</p>}<div className="reddit-window-detail"><span>Coverage start</span><strong>{dateLabel(inbox.coverage_start ?? connection.coverage_start)}</strong><span>Coverage end</span><strong>{dateLabel(inbox.coverage_end ?? connection.coverage_end)}</strong><span>Coverage status</span><strong>{connection.coverage_complete === true ? 'Complete' : connection.coverage_complete === false ? 'Partial · see note' : 'Unknown'}</strong><span>New intake window</span><strong>{inbox.intake_start ? `${dateLabel(inbox.intake_start)} → ${dateLabel(inbox.intake_end)}` : 'No intake run reported'}</strong></div></section></div>
  </div>
}

function SimulationView({ simulations, namespace: _namespace, sources, simulationFocusId, onOpenSource, onRunSimulation, onReplaySimulation, embedded = false }: WorkspaceProps & { embedded?: boolean }) {
  const [form, setForm] = useState({ title: 'Declared price shock', question: 'Stress a declared price shock through bounded participant cash and share rules.', horizon: '3 months', participants: 6, rounds: 5, seed: 4242, initial_price: '2500', shock_percent: '-25', use_llm: false })
  const [running, setRunning] = useState(false)
  const [message, setMessage] = useState('')
  const [sourceIds, setSourceIds] = useState<string[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [replay, setReplay] = useState<any>(null)
  const selected = simulations.find((run) => run.id === selectedId) ?? simulations[0]
  const selectedSources = selected?.source_snapshots ?? []
  const detailRounds = selected?.rounds_detail ?? selected?.events ?? []
  const detailParticipants = selected?.participants_detail ?? []
  const terminal = ['completed', 'failed', 'cancelled', 'interrupted'].includes(String(selected?.status))
  const replayable = selected?.status === 'completed' && detailRounds.length > 0

  useEffect(() => {
    if (simulationFocusId && simulations.some((run) => run.id === simulationFocusId)) setSelectedId(simulationFocusId)
  }, [simulationFocusId, simulations])

  async function submit(event: React.FormEvent) {
    event.preventDefault(); setRunning(true); setMessage('')
    try { await onRunSimulation({ ...form, source_ids: sourceIds }); setMessage('Scenario accepted. The live record will refresh as the backend writes each lifecycle state.') } catch (error) { setMessage(error instanceof Error ? error.message : 'Simulation could not be queued.') } finally { setRunning(false) }
  }

  async function replaySelected() {
    if (!selected?.id || !replayable) return
    setReplay(null)
    try { setReplay(await onReplaySimulation(selected.id)) } catch (error) { setMessage(error instanceof Error ? error.message : 'Replay failed.') }
  }

  function sourceForId(id: string) {
    return selectedSources.find((source) => source.id === id || source.source_ref === id) ?? sources.find((source) => source.id === id || source.source_ref === id)
  }

  return <div className="workspace-page">
    {!embedded && <ViewHeader eyebrow="SIMULATION LAB / HYPOTHETICAL" title="Scenarios" copy="Run a hypothetical price shock and inspect how the participants respond. Results are saved separately from market evidence." namespace="simulation" />}
    <div className="notice-card notice-purple"><Icon name="beaker" size={18} /><div><strong>Simulation namespace</strong><p>Seeds and recorded actions support exact replay. A fresh hosted-model call is a new experiment. This lab does not produce market probabilities.</p></div></div>
    <div className="two-column-workspace">
      <section className="workspace-card"><div className="section-heading"><div><span className="eyebrow">SCENARIO SETUP</span><h2>Bounded experiment</h2></div><span className="limit-note">6–12 participants · 5–10 rounds</span></div><form className="stack-form" onSubmit={submit}><label>Title<input required value={form.title} onChange={(event) => setForm({ ...form, title: event.target.value })} /></label><label>Question<textarea required rows={3} value={form.question} onChange={(event) => setForm({ ...form, question: event.target.value })} /></label><div className="form-grid"><label>Horizon<input value={form.horizon} onChange={(event) => setForm({ ...form, horizon: event.target.value })} /></label><label>Participants<input type="number" min={6} max={12} value={form.participants} onChange={(event) => setForm({ ...form, participants: Number(event.target.value) })} /></label><label>Rounds<input type="number" min={5} max={10} value={form.rounds} onChange={(event) => setForm({ ...form, rounds: Number(event.target.value) })} /></label><label>Seed<input type="number" value={form.seed} onChange={(event) => setForm({ ...form, seed: Number(event.target.value) })} /></label><label>Initial price<input required value={form.initial_price} onChange={(event) => setForm({ ...form, initial_price: event.target.value })} /></label><label>Shock %<input required value={form.shock_percent} onChange={(event) => setForm({ ...form, shock_percent: event.target.value })} /></label></div><fieldset className="source-picker"><legend>Attach same namespace sources (optional)</legend>{sources.length ? sources.map((source, index) => { const id = source.id ?? source.source_ref ?? `source-${index}`; return <label className="check-row" key={id}><input type="checkbox" checked={sourceIds.includes(id)} onChange={() => setSourceIds((current) => current.includes(id) ? current.filter((value) => value !== id) : [...current, id])} /><span>{source.title ?? id}<small>{source.source_type ?? 'source'} · {source.version == null ? 'version unknown' : `v${source.version}`}</small></span></label> }) : <p className="muted-copy">No local source records are available to snapshot.</p>}</fieldset><label className="check-row"><input type="checkbox" checked={form.use_llm} onChange={(event) => setForm({ ...form, use_llm: event.target.checked })} /> <span>Allow selected model interpretations <small>Rules-only baseline remains available.</small></span></label><button type="submit" className="button button-primary" disabled={running}><Icon name="play" size={15} /> {running ? 'Queuing…' : 'Run scenario'}</button>{message && <p className="form-message" role="status">{message}</p>}</form></section>
      <section className="workspace-card"><div className="section-heading"><div><span className="eyebrow">RECENT RUNS</span><h2>Replayable experiments</h2></div><span className="muted-copy">{simulations.length} records · live detail polling</span></div>{simulations.length ? <div className="simulation-list">{simulations.map((run, index) => { const isSelected = (selected?.id ?? null) === run.id; const canReplay = run.status === 'completed' && ((run.rounds_detail?.length ?? run.events?.length ?? 0) > 0); return <article className={`simulation-row${isSelected ? ' is-selected' : ''}`} key={run.id ?? index} onClick={() => setSelectedId(run.id ?? null)}><button type="button" className="simulation-row-main" onClick={() => setSelectedId(run.id ?? null)}><span className="simulation-row-icon"><Icon name="beaker" size={17} /></span><span><strong>{run.name ?? 'Scenario run'}</strong><span>{run.status ? statusLabel(run.status) : 'Status unknown'} · {run.horizon ?? 'Horizon unknown'} · {run.participants ?? '—'} participants · seed {run.seed ?? '—'}</span><small>{run.assumptions?.[0] ?? 'Detail loads from the simulation record'} · {dateLabel(run.created_at)}</small></span></button><StatusBadge status={run.status} compact /><button type="button" className="button button-subtle" disabled={!canReplay} onClick={(event) => { event.stopPropagation(); setSelectedId(run.id ?? null); if (run.id) void onReplaySimulation(run.id) }}><Icon name="refresh" size={14} /> {run.status === 'completed' ? 'Replay' : 'Await completion'}</button></article> })}</div> : <Empty icon="beaker" title="No simulation runs" copy="Define a bounded scenario to create an isolated, replayable record." />}</section>
    </div>
    {selected && <section className="workspace-card simulation-detail"><div className="section-heading"><div><span className="eyebrow">SCENARIO REPORT · SIMULATION</span><h2>{selected.name}</h2><p className="muted-copy">{selected.id} · {statusLabel(selected.status)} · created {dateLabel(selected.created_at)}</p></div><div className="button-row"><StatusBadge status={selected.status} /><button type="button" className="button button-subtle" disabled={!replayable} onClick={() => void replaySelected()}><Icon name="refresh" size={14} /> {replayable ? 'Replay exact record' : 'Replay after completion'}</button></div></div><div className="simulation-summary-grid"><div><span>Question</span><strong>{selected.scenario ?? selected.question ?? 'Unknown'}</strong></div><div><span>Inputs</span><strong>{selected.inputs?.initial_price == null ? 'Price unknown' : `Initial ${String(selected.inputs.initial_price)}`} · shock {selected.shock ?? 'unknown'}</strong></div><div><span>Bounds</span><strong>{selected.rounds ?? '—'} rounds · {selected.participants ?? '—'} participants · seed {selected.seed ?? '—'}</strong></div><div><span>Model</span><strong>{selected.model ? `${selected.model.provider} / ${selected.model.model} / ${selected.model.reasoning_mode ?? 'effort unknown'}` : selected.inputs?.use_llm ? 'Model unavailable' : 'Rules only'}</strong></div></div>{selected.summary && <div className="simulation-summary"><span className="eyebrow">RECORDED SUMMARY</span><p>{selected.summary}</p></div>}{selected.failure && <div className="notice-card notice-amber"><Icon name="warning" size={17} /><div><strong>Simulation state needs attention</strong><p>{String(selected.failure.reason ?? selected.blocking_reason ?? 'The backend reported a failure. No hypothetical result was inferred.')}</p></div></div>}<div className="simulation-detail-grid"><section><span className="eyebrow">ASSUMPTIONS / LIMITS</span>{selected.assumptions?.length ? <ul className="plain-list">{selected.assumptions.map((item, index) => <li key={`a-${index}`}>{item}</li>)}</ul> : <p className="muted-copy">No assumptions returned.</p>}{selected.limits?.length ? <ul className="plain-list simulation-limits">{selected.limits.map((item, index) => <li key={`l-${index}`}>{item}</li>)}</ul> : null}</section><section><span className="eyebrow">SOURCE SNAPSHOT</span>{selectedSources.length ? <div className="source-chip-list">{selectedSources.map((source, index) => <button type="button" key={source.id ?? index} onClick={() => { const full = sourceForId(String(source.id ?? source.source_ref ?? '')); if (full) void onOpenSource(full) }}><Icon name="book" size={14} /> {source.title ?? source.id}<small>{source.version == null ? 'version unknown' : `v${source.version}`} · {source.content_hash ?? 'hash unknown'}</small></button>)}</div> : <p className="muted-copy">No source snapshot attached.</p>}</section></div><div className="simulation-detail-grid"><section><span className="eyebrow">PARTICIPANTS / OPENING STATE</span>{detailParticipants.length ? <div className="simulation-participant-list">{detailParticipants.slice(0, 12).map((participant, index) => <article key={String(participant.id ?? index)}><strong>{String(participant.id ?? `Participant ${index + 1}`)}</strong><span>{String(participant.style ?? participant.horizon ?? 'Profile')} · cash {String(participant.initial_cash ?? participant.cash ?? 'unknown')}</span><small>Shares {String(participant.initial_shares ?? participant.shares ?? 'unknown')} · target {String(participant.target_weight ?? 'unknown')}</small></article>)}</div> : <p className="muted-copy">Participant detail is available after the backend returns the scenario record.</p>}</section><section><span className="eyebrow">RULES-ONLY BASELINE</span>{selected.rules_only_baseline && isRecordValue(selected.rules_only_baseline) ? <div className="simulation-baseline"><strong>{String((selected.rules_only_baseline as any).summary ?? 'Recorded deterministic baseline')}</strong><span>{Array.isArray((selected.rules_only_baseline as any).rounds) ? `${(selected.rules_only_baseline as any).rounds.length} baseline rounds` : 'Baseline rounds retained'}</span><small>No hosted-model output is treated as market evidence.</small></div> : <p className="muted-copy">Rules-only baseline is unavailable until the run completes.</p>}</section></div><section className="simulation-rounds"><div className="section-heading"><div><span className="eyebrow">ROUNDS / RECORDED ACTIONS</span><h3>{detailRounds.length ? `${detailRounds.length} rounds` : terminal ? 'No rounds recorded' : 'Waiting for rounds'}</h3></div></div>{detailRounds.length ? <div className="round-list">{detailRounds.map((round, index) => { const actions = Array.isArray(round.actions) ? round.actions : []; return <article key={String(round.round ?? index)}><div className="round-heading"><strong>Round {String(round.round ?? index + 1)}</strong><span>Price {String(round.price ?? round.price_after ?? 'unknown')}</span><span>{actions.length} actions</span></div><p>{String(round.explanation ?? round.mechanism ?? 'Recorded deterministic transition')}</p>{actions.length > 0 && <div className="round-actions">{actions.slice(0, 12).map((action: any, actionIndex: number) => <span key={actionIndex}>{String(action.participant_id ?? 'participant')} · {String(action.action ?? 'action')} · {String(action.quantity ?? '0')}</span>)}</div>}</article> })}</div> : <p className="muted-copy">The live simulation record is still being written. This panel refreshes automatically.</p>}</section>{replay && <section className="simulation-replay"><span className="eyebrow">EXACT REPLAY RESULT</span><p>{String(replay.replay?.summary ?? (replay.matches ? 'Exact replay matched the recorded actions.' : 'Replay completed with mismatches.'))}</p>{Array.isArray(replay.replay?.mismatches) && replay.replay.mismatches.length > 0 && <ul className="plain-list">{replay.replay.mismatches.map((item: unknown, index: number) => <li key={index}>{String(item)}</li>)}</ul>}</section>}</section>}
  </div>
}

function isRecordValue(value: unknown): value is Record<string, unknown> { return typeof value === 'object' && value !== null && !Array.isArray(value) }

function CoverageView({ coverage, namespace, onCoverage, embedded = false }: WorkspaceProps & { embedded?: boolean }) {
  const [filter, setFilter] = useState('all')
  const statuses = ['all', 'considered', 'watch', 'active_research', 'held', 'rejected']
  const filtered = coverage.filter((item) => filter === 'all' || item.status === filter)
  const placeholderCount = filtered.filter((item) => item.is_placeholder || item.kind === 'placeholder' || item.kind === 'gap').length
  const candidateCount = filtered.length - placeholderCount
  const symbolCount = new Set(filtered.filter((item) => !item.is_placeholder && item.kind !== 'placeholder' && item.kind !== 'gap').map((item) => item.ticker)).size
  return <div className="workspace-page">{!embedded && <ViewHeader eyebrow="RESEARCH / COVERAGE" title="Coverage watch" copy="Ideas keep a dated status, reason and reopening condition. Explicit gaps remain visible when a source is unavailable." namespace={namespace} />}<section className="workspace-card"><div className="coverage-toolbar"><div className="filter-pills">{statuses.map((status) => <button type="button" key={status} className={filter === status ? 'is-active' : ''} onClick={() => setFilter(status)}>{status === 'all' ? 'All' : statusLabel(status)}</button>)}</div><span className="muted-copy">{candidateCount} candidates · {placeholderCount} coverage gaps · {symbolCount} symbols</span></div>{filtered.length ? <div className="coverage-list">{filtered.map((item) => <CoverageRow key={item.id} item={item} onCoverage={onCoverage} />)}</div> : <Empty icon="compass" title="No coverage records" copy="The backend returns explicit coverage records and gaps for every supported area." />}</section><section className="coverage-grid"><CoverageCallout title="Standard equity sectors" copy="Sector coverage is returned by the backend with gaps called out explicitly." /><CoverageCallout title="Gold and crypto" copy="These exposures stay separate from sector labels and require their own evidence trail." /><CoverageCallout title="Data delay" copy="Quotes, filings and public disclosures carry observation and retrieval times." /></section></div>
}

function CoverageRow({ item, onCoverage }: { item: CoverageItem; onCoverage: (item: CoverageItem, status: string, reason: string) => Promise<void> }) {
  const [editing, setEditing] = useState(false)
  const [status, setStatus] = useState(item.status)
  const [reason, setReason] = useState(item.reason ?? '')
  async function save() { await onCoverage(item, status, reason); setEditing(false) }
  const isPlaceholder = Boolean(item.is_placeholder || item.kind === 'placeholder' || item.kind === 'gap')
  return <article className="coverage-row"><div className="coverage-symbol"><strong>{isPlaceholder ? (item.name ?? item.sector ?? item.ticker.replace(/^coverage:/i, '').replace(/[_-]+/g, ' ')) : item.ticker}</strong><span>{isPlaceholder ? 'Coverage area' : item.name ?? item.sector ?? item.id}</span></div>{editing ? <div className="coverage-edit"><select value={status} onChange={(event) => setStatus(event.target.value)}>{['considered', 'watch', 'active_research', 'held', 'rejected'].map((value) => <option key={value} value={value}>{statusLabel(value)}</option>)}</select><input value={reason} onChange={(event) => setReason(event.target.value)} placeholder="Dated reason" /></div> : <div className="coverage-reason"><StatusBadge status={item.status} compact /><p>{item.reason ?? 'Reason not supplied.'}</p><small>Reopen when: {item.reopening_condition ?? 'Condition not supplied.'}</small></div>}<span className="coverage-date">{dateLabel(item.updated_at)}</span>{editing ? <div className="button-row"><button type="button" className="button button-primary" onClick={save}>Save</button><button type="button" className="button button-subtle" onClick={() => setEditing(false)}>Cancel</button></div> : !isPlaceholder ? <button type="button" className="button button-subtle" onClick={() => setEditing(true)}><Icon name="settings" size={14} /> Update</button> : <span className="coverage-gap-note">Add a dated candidate to update</span>}</article>
}

function DecisionsView(props: WorkspaceProps) {
  return <CanonicalRunsView mode="decisions" runs={props.runs} selectedRun={props.selectedRun} selectedRunId={props.selectedRun?.id} namespace={props.namespace} office={props.office} sources={props.sources} onNavigate={props.onNavigate} onSelectRun={props.onSelectRun} onOpenOutput={props.onOpenOutput} onOpenSource={props.onOpenSource} onControl={props.onControl} onExplainResult={props.onExplainResult} onResearchMissingEvidence={props.onResearchMissingEvidence} />
}

function policyConfigLabel(config: any) {
  if (!config) return 'Inherited · no override'
  const provider = config.provider ?? 'provider unknown'
  const model = config.model ?? 'model unknown'
  const effort = config.reasoning_effort ?? config.reasoning_mode ?? 'effort unknown'
  return `${provider} / ${model} / ${effort}`
}

type PolicyLimitForm = {
  currency: string
  initial_notional: string
  max_notional: string
  planned_loss_limit: string
}

type PortfolioPolicyForm = {
  status: 'proposed' | 'approved'
  max_positions: string
  tfsa_long_term_only: boolean
  allow_tfsa_outflows: boolean
  allow_chequing_to_nonregistered: boolean
  long_term: PolicyLimitForm
  trade: PolicyLimitForm
}

const DEFAULT_POLICY_FORM: PortfolioPolicyForm = {
  status: 'proposed',
  max_positions: '10',
  tfsa_long_term_only: true,
  allow_tfsa_outflows: false,
  allow_chequing_to_nonregistered: true,
  long_term: { currency: 'USD', initial_notional: '5000', max_notional: '7500', planned_loss_limit: '' },
  trade: { currency: 'USD', initial_notional: '2500', max_notional: '5000', planned_loss_limit: '250' },
}

function policyRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function policyLimitForm(value: unknown, fallback: PolicyLimitForm): PolicyLimitForm {
  const limit = policyRecord(value)
  const plannedLoss = Object.prototype.hasOwnProperty.call(limit, 'planned_loss_limit') ? limit.planned_loss_limit : fallback.planned_loss_limit
  return {
    currency: String(limit.currency ?? fallback.currency),
    initial_notional: String(limit.initial_notional ?? fallback.initial_notional),
    max_notional: String(limit.max_notional ?? fallback.max_notional),
    planned_loss_limit: plannedLoss == null ? '' : String(plannedLoss),
  }
}

function portfolioPolicyForm(value: Record<string, unknown> | null): PortfolioPolicyForm {
  const source = policyRecord(value?.policy ?? value)
  const limits = policyRecord(source.limits)
  const status = source.status === 'approved' ? 'approved' : 'proposed'
  return {
    status,
    max_positions: String(source.max_positions ?? DEFAULT_POLICY_FORM.max_positions),
    tfsa_long_term_only: source.tfsa_long_term_only == null ? DEFAULT_POLICY_FORM.tfsa_long_term_only : Boolean(source.tfsa_long_term_only),
    allow_tfsa_outflows: source.allow_tfsa_outflows == null ? DEFAULT_POLICY_FORM.allow_tfsa_outflows : Boolean(source.allow_tfsa_outflows),
    allow_chequing_to_nonregistered: source.allow_chequing_to_nonregistered == null ? DEFAULT_POLICY_FORM.allow_chequing_to_nonregistered : Boolean(source.allow_chequing_to_nonregistered),
    long_term: policyLimitForm(limits.long_term, DEFAULT_POLICY_FORM.long_term),
    trade: policyLimitForm(limits.trade, DEFAULT_POLICY_FORM.trade),
  }
}

function PortfolioPolicyPanel({ namespace: _namespace, value, onSave, onNavigate }: { namespace: Namespace; value: Record<string, unknown> | null; onSave: (input: Record<string, unknown>) => Promise<void>; onNavigate: WorkspaceProps['onNavigate'] }) {
  const [form, setForm] = useState<PortfolioPolicyForm>(() => portfolioPolicyForm(value))
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState('')
  const savedPolicy = policyRecord(value?.policy ?? value)
  const status = savedPolicy.status === 'approved' ? 'approved' : 'proposed'
  const configured = value?.configured === true

  useEffect(() => { setForm(portfolioPolicyForm(value)); setMessage('') }, [value])

  function updateLimit(bucket: 'long_term' | 'trade', key: keyof PolicyLimitForm, next: string) {
    setForm((current) => ({ ...current, [bucket]: { ...current[bucket], [key]: next } }))
  }

  async function save(event: React.FormEvent) {
    event.preventDefault()
    setSaving(true)
    setMessage('')
    try {
      await onSave({
        status: form.status,
        max_positions: Math.max(1, Math.min(500, Number(form.max_positions) || 10)),
        tfsa_long_term_only: form.tfsa_long_term_only,
        allow_tfsa_outflows: form.allow_tfsa_outflows,
        allow_chequing_to_nonregistered: form.allow_chequing_to_nonregistered,
        limits: {
          long_term: { ...form.long_term, currency: form.long_term.currency.trim().toUpperCase(), planned_loss_limit: form.long_term.planned_loss_limit.trim() || null },
          trade: { ...form.trade, currency: form.trade.currency.trim().toUpperCase(), planned_loss_limit: form.trade.planned_loss_limit.trim() || null },
        },
      })
      setMessage(form.status === 'approved' ? 'Approved policy saved. Funded sizing can use these limits after its other checks pass.' : 'Proposed policy saved. It remains planning input until explicitly approved.')
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Portfolio policy could not be saved.')
    } finally { setSaving(false) }
  }

  const limitFields = (bucket: 'long_term' | 'trade', label: string) => <fieldset className="policy-limit-fieldset"><legend>{label}</legend><div className="policy-limit-grid"><label>Currency<input maxLength={3} value={form[bucket].currency} onChange={(event) => updateLimit(bucket, 'currency', event.target.value)} /></label><label>Initial amount<input inputMode="decimal" value={form[bucket].initial_notional} onChange={(event) => updateLimit(bucket, 'initial_notional', event.target.value)} /></label><label>Maximum amount<input inputMode="decimal" value={form[bucket].max_notional} onChange={(event) => updateLimit(bucket, 'max_notional', event.target.value)} /></label><label>Planned loss limit<input inputMode="decimal" value={form[bucket].planned_loss_limit} onChange={(event) => updateLimit(bucket, 'planned_loss_limit', event.target.value)} placeholder="Optional" /></label></div></fieldset>

  return <section className="workspace-card portfolio-policy-card" aria-labelledby="portfolio-policy-title"><div className="section-heading"><div><span className="eyebrow">PORTFOLIO POLICY</span><h2 id="portfolio-policy-title">Planning and funding limits</h2></div><div className="policy-heading-actions"><span className={`policy-state-pill policy-state-${status}`}>{status === 'approved' ? 'Approved' : 'Proposed'}</span><button type="button" className="button button-subtle" onClick={() => onNavigate('portfolio')}>View accounts and holdings</button></div></div><p className="muted-copy">Set the limits used when a case proposes position sizing. Proposed values are planning inputs; only an explicitly approved policy can support funded shares.</p>{!configured && <p className="policy-draft-note">No saved policy was returned. The form starts with the latest proposed test caps as a draft.</p>}<form className="stack-form" onSubmit={save}><div className="policy-form-top"><label>Policy status<select value={form.status} onChange={(event) => setForm((current) => ({ ...current, status: event.target.value === 'approved' ? 'approved' : 'proposed' }))}><option value="proposed">Proposed · planning only</option><option value="approved">Approved · eligible for funded sizing</option></select></label><label>Maximum distinct positions<input type="number" min={1} max={500} value={form.max_positions} onChange={(event) => setForm((current) => ({ ...current, max_positions: event.target.value }))} /></label></div>{limitFields('long_term', 'Long-term / TFSA')} {limitFields('trade', 'Trade / non-registered')}<div className="policy-checkboxes"><label className="check-row"><input type="checkbox" checked={form.tfsa_long_term_only} onChange={(event) => setForm((current) => ({ ...current, tfsa_long_term_only: event.target.checked }))} /><span>Keep TFSA long-term only<small>Short-term trades stay outside TFSA.</small></span></label><label className="check-row"><input type="checkbox" checked={form.allow_tfsa_outflows} onChange={(event) => setForm((current) => ({ ...current, allow_tfsa_outflows: event.target.checked }))} /><span>Allow TFSA outflows<small>Leave off unless this is an intentional account rule.</small></span></label><label className="check-row"><input type="checkbox" checked={form.allow_chequing_to_nonregistered} onChange={(event) => setForm((current) => ({ ...current, allow_chequing_to_nonregistered: event.target.checked }))} /><span>Allow chequing to non-registered funding</span></label></div><button type="submit" className="button button-primary" disabled={saving}><Icon name="check" size={15} /> {saving ? 'Saving…' : 'Save portfolio policy'}</button>{message && <p className="form-message" role="status">{message}</p>}</form></section>
}

function SettingsView({ providers, policy, office, sources, monitoring, namespace, portfolioPolicy, onModelPolicy, onPortfolioPolicy, onPreflight, onBackup, onMonitoring, onMonitoringToggle, reducedMotion, onReducedMotion, onNavigate }: WorkspaceProps) {
  const [selectedProvider, setSelectedProvider] = useState('codex')
  const [selectedModel, setSelectedModel] = useState('gpt-6-luna')
  const [reasoning, setReasoning] = useState('high')
  const [selectedRole, setSelectedRole] = useState('')
  const [selectedProfile, setSelectedProfile] = useState('gpt-first')
  const [message, setMessage] = useState('')
  const [monitor, setMonitor] = useState({ name: 'Dependency review', enabled: false, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC', interval_minutes: 1440, condition: 'Reopen research when a referenced source version changes.', mode: 'interval_research' as 'interval_research' | 'source_change', source_ids: [] as string[] })
  const providerRows: any[] = providers?.providers ?? (providers ? [providers] : [])
  const models = providerRows.flatMap((provider) => (provider.models ?? []).map((model: any) => ({ ...model, provider: model.provider || provider.provider })))
  const selectedProviderModels = models.filter((model) => model.provider === selectedProvider)
  const selectedDescriptor = selectedProviderModels.find((model) => model.model === selectedModel)
  const reasoningOptions = selectedDescriptor?.reasoning_efforts?.length ? selectedDescriptor.reasoning_efforts : modelEfforts(selectedModel)
  const profileRows: any[] = Array.isArray(policy?.profiles) && policy.profiles.length ? policy.profiles : [{ id: 'gpt-first', name: 'GPT first', available: true, config: policy?.firm_default ?? null }]
  const roleOverrides: Record<string, any> = policy?.role_overrides ?? {}
  const selectedRoleConfig = selectedRole ? roleOverrides[selectedRole] : null
  const selectedProfileRow = profileRows.find((profile) => String(profile.id) === selectedProfile) ?? profileRows[0]
  const defaultEffort = (model: any) => model.reasoning_efforts?.includes('high') ? 'high' : model.reasoning_efforts?.[0] ?? 'high'
  const providerStatus = (provider: any) => provider.available ? 'ready' : provider.status ?? 'blocked'
  const providerStatusCopy = (provider: any) => provider.available ? `Ready · ${provider.billing_route === 'chatgpt_subscription' ? 'ChatGPT subscription' : provider.billing_route ?? 'local route'}` : provider.status === 'auth_required' ? 'Authentication required' : provider.status === 'missing' ? 'Not installed' : 'Unavailable'

  useEffect(() => {
    const firm = policy?.firm_default
    if (firm) {
      if (firm.provider) setSelectedProvider(String(firm.provider))
      if (firm.model) setSelectedModel(String(firm.model))
      if (firm.reasoning_effort) setReasoning(String(firm.reasoning_effort))
    }
    if (policy?.active_profile) setSelectedProfile(String(policy.active_profile))
  }, [policy])

  async function saveFirm() {
    setMessage('')
    if (!selectedDescriptor?.available) { setMessage('Validate this exact provider/model pair before saving it as the firm default.'); return }
    try {
      await onModelPolicy({ scope: 'firm', agent_id: null, config: { provider: selectedProvider, model: selectedModel, reasoning_mode: reasoning, profile: selectedProfile, scope: 'firm' }, profile: null })
      setMessage('Firm default saved. Running attempts retain their resolved configuration.')
    } catch (error) { setMessage(error instanceof Error ? error.message : 'Model policy could not be saved.') }
  }

  async function saveRole() {
    setMessage('')
    if (!selectedRole) { setMessage('Choose a role before saving an override.'); return }
    if (!selectedDescriptor?.available) { setMessage('Validate this exact provider/model pair before saving a role override.'); return }
    try {
      await onModelPolicy({ scope: 'role', agent_id: selectedRole, config: { provider: selectedProvider, model: selectedModel, reasoning_mode: reasoning, profile: selectedProfile, scope: 'role' }, profile: null })
      setMessage(`Override saved for ${selectedRole}.`)
    } catch (error) { setMessage(error instanceof Error ? error.message : 'Role override could not be saved.') }
  }

  async function clearRole() {
    setMessage('')
    if (!selectedRole) { setMessage('Choose a role before clearing an override.'); return }
    try {
      await onModelPolicy({ scope: 'role', agent_id: selectedRole, config: null, profile: null })
      setMessage(`${selectedRole} now inherits the active profile and firm default.`)
    } catch (error) { setMessage(error instanceof Error ? error.message : 'Role override could not be cleared.') }
  }

  async function saveProfile() {
    setMessage('')
    try {
      await onModelPolicy({ scope: 'profile', agent_id: null, config: null, profile: selectedProfile })
      setMessage(`Active profile set to ${selectedProfile}.`)
    } catch (error) { setMessage(error instanceof Error ? error.message : 'Active profile could not be saved.') }
  }

  async function saveMonitor(event: React.FormEvent) {
    event.preventDefault()
    try {
      await onMonitoring(monitor)
      setMessage('Monitoring rule saved. Local process and sleep limitations remain visible.')
    } catch (error) { setMessage(error instanceof Error ? error.message : 'Monitoring rule could not be saved.') }
  }

  function toggleSource(sourceId: string) {
    setMonitor((current) => ({ ...current, source_ids: current.source_ids.includes(sourceId) ? current.source_ids.filter((id) => id !== sourceId) : [...current.source_ids, sourceId] }))
  }

  return <div className="workspace-page">
    <ViewHeader eyebrow="SETTINGS / PROVIDER POLICY" title="Settings" copy="Manage research models, portfolio limits, monitoring and local backups. Model changes apply to future research." namespace={namespace} />
    <div className="notice-card notice-blue"><Icon name="compass" size={18} /><div><strong>Subscription-first execution</strong><p>Codex uses the authenticated ChatGPT subscription route when the local preflight validates it. Ollama remains optional and disconnected until installed. No API keys or provider auth material enter this browser.</p></div></div>
    <div className="two-column-workspace">
      <section className="workspace-card">
        <div className="section-heading"><div><span className="eyebrow">PROVIDER PREFLIGHT</span><h2>Connection state</h2></div><span className="muted-copy">No silent fallback</span></div>
        {providerRows.length ? <div className="provider-list">{providerRows.map((provider) => <article className="provider-row" key={provider.provider}>
          <div className="provider-row-heading"><div><strong>{provider.provider === 'codex' ? 'Codex / ChatGPT' : provider.provider === 'ollama' ? 'Ollama / local' : provider.provider}</strong><span>{providerStatusCopy(provider)}</span></div><StatusBadge status={providerStatus(provider)} compact /></div>
          <p>{provider.reason ?? (provider.available ? 'Provider is ready for later dispatch.' : 'Availability has not been confirmed.')}</p>
          <div className="model-list">{(provider.models ?? []).map((model: any) => <div key={model.model}><span><strong>{model.label ?? model.model}</strong><small>{model.model}</small></span><span className={model.available ? 'capability-yes' : 'capability-no'}>{model.available ? 'Validated' : (model.reason ?? 'Unvalidated')}</span><span className="provider-probe"><button type="button" className="button button-subtle" disabled={!provider.available} onClick={() => void onPreflight(provider.provider, model.model, defaultEffort(model), true)}><Icon name="play" size={13} /> {model.available ? 'Recheck' : 'Validate'}</button></span></div>)}</div>
          <button type="button" className="button button-subtle" onClick={() => void onPreflight(provider.provider, null, null, false)}><Icon name="refresh" size={14} /> Check discovery</button>
        </article>)}</div> : <Empty icon="compass" title="Provider state unavailable" copy="Connect the local backend to inspect validated models and billing routes." />}
      </section>
      <section className="workspace-card">
        <div className="section-heading"><div><span className="eyebrow">MODEL POLICY</span><h2>Resolution precedence</h2></div></div>
        <ol className="precedence-list"><li>One-run or task override</li><li>Role override</li><li>Active profile</li><li>Firm default</li></ol>
        <div className="model-policy-summary" aria-label="Current model policy"><div><span>Firm default</span><strong>{policyConfigLabel(policy?.firm_default)}</strong></div><div><span>Active profile · {policy?.active_profile ?? selectedProfile}</span><strong>{policyConfigLabel(selectedProfileRow?.config)}</strong></div><div><span>A00 · Chief of Staff</span><strong>{policyConfigLabel(roleOverrides.A00) === 'Inherited · no override' ? 'Inherited · routing' : policyConfigLabel(roleOverrides.A00)}</strong></div><div><span>Researcher · A01 + A03</span><strong>{roleOverrides.A01 || roleOverrides.A03 ? `${roleOverrides.A01 ? policyConfigLabel(roleOverrides.A01) : 'A01 inherits'} · ${roleOverrides.A03 ? policyConfigLabel(roleOverrides.A03) : 'A03 inherits'}` : 'Inherited · discovery and analysis'}</strong></div><div><span>A11 · Chief Investment Officer</span><strong>{policyConfigLabel(roleOverrides.A11) === 'Inherited · no override' ? 'Inherited · final decision' : policyConfigLabel(roleOverrides.A11)}</strong></div></div>
        <div className="model-form">
          <label>Provider<select value={selectedProvider} onChange={(event) => { const next = event.target.value; setSelectedProvider(next); const first = models.find((model) => model.provider === next); if (first) { setSelectedModel(first.model); setReasoning(defaultEffort(first) ?? 'max') } }}><option value="codex">codex</option><option value="ollama">ollama</option>{providerRows.filter((provider) => !['codex', 'ollama'].includes(provider.provider)).map((provider) => <option key={provider.provider} value={provider.provider}>{provider.provider}</option>)}</select></label>
          <label>Model<select value={selectedModel} onChange={(event) => { const next = event.target.value; setSelectedModel(next); const descriptor = models.find((model) => model.provider === selectedProvider && model.model === next); if (descriptor?.reasoning_efforts?.length) setReasoning(defaultEffort(descriptor) ?? 'max') }}>{selectedProviderModels.length ? selectedProviderModels.map((model) => <option key={`${model.provider}:${model.model}`} value={model.model} disabled={!model.available}>{model.label ?? model.model}{model.available ? '' : ' · unvalidated'}</option>) : <option value={selectedModel}>{selectedModel}</option>}</select></label>
          <label>Reasoning effort<select value={reasoningOptions.includes(reasoning) ? reasoning : reasoningOptions[reasoningOptions.length - 1]} onChange={(event) => setReasoning(event.target.value)}>{reasoningOptions.map((effort: string) => <option key={effort} value={effort}>{effort}</option>)}</select></label>
          <button type="button" className="button button-primary" disabled={!selectedDescriptor?.available} onClick={() => void saveFirm()}><Icon name="check" size={15} /> Save firm default</button>
        </div>
        <details className="advanced-role-controls"><summary>Advanced role overrides</summary><p className="muted-copy">Use this only when one specialist needs a different provider, model or effort. The case flow still names roles by purpose.</p><div className="role-override-form"><label>Role override<select value={selectedRole} onChange={(event) => setSelectedRole(event.target.value)}><option value="">No role selected</option>{office.agents.map((agent) => <option key={agent.id} value={agent.id}>{agent.id} · {agent.name}</option>)}</select></label><div className="button-row"><button type="button" className="button button-subtle" disabled={!selectedRole || !selectedDescriptor?.available} onClick={() => void saveRole()}><Icon name="users" size={15} /> Save role override</button><button type="button" className="button button-subtle" disabled={!selectedRole || !selectedRoleConfig} onClick={() => void clearRole()}>Clear override</button></div></div></details>
        <div className="profile-form"><label>Active profile<select value={selectedProfile} onChange={(event) => setSelectedProfile(event.target.value)}>{profileRows.map((profile) => <option key={profile.id} value={profile.id} disabled={profile.available === false}>{profile.name ?? profile.id}{profile.available === false ? ' · unavailable' : ''}</option>)}</select></label><button type="button" className="button button-subtle" onClick={() => void saveProfile()}><Icon name="check" size={15} /> Save active profile</button></div>
        {policy?.history?.length > 0 && <p className="policy-history-note">Last policy change {String(policy.history[0].changed_at ?? 'unknown')} · {String(policy.history[0].scope ?? 'unknown scope')}.</p>}
        {message && <p className="form-message" role="status">{message}</p>}
      </section>
    </div>
    <PortfolioPolicyPanel namespace={namespace} value={portfolioPolicy} onSave={onPortfolioPolicy} onNavigate={onNavigate} />
    <div className="two-column-workspace settings-lower">
      <section className="workspace-card"><div className="section-heading"><div><span className="eyebrow">MONITORING</span><h2>Dependency watch</h2></div></div><p className="muted-copy">Opt-in local scans reopen affected work when a referenced source changes. The condition is a research instruction; no price alert is inferred. The local process must remain running; sleep catch-up is run once.</p>{monitoring.length ? <div className="monitoring-list" aria-label="Existing monitoring rules">{monitoring.map((rule) => <article className="monitoring-row" key={rule.id}><div><strong>{rule.name}</strong><span>{rule.mode === 'source_change' ? 'Source version change' : 'Interval research instruction'} · every {rule.interval_minutes ?? '—'} minutes</span><small>{rule.condition ?? 'No research instruction recorded.'}</small><small>{rule.next_run_at ? `Next local scan ${dateLabel(rule.next_run_at)}` : rule.last_run_at ? `Last local scan ${dateLabel(rule.last_run_at)}` : 'No local scan recorded yet.'}</small></div><div className="button-row"><StatusBadge status={rule.enabled ? 'ready' : 'paused'} compact /><button type="button" className="button button-subtle" onClick={() => void onMonitoringToggle(rule)}>{rule.enabled ? 'Pause rule' : 'Enable rule'}</button></div></article>)}</div> : <p className="muted-copy monitoring-empty">No monitoring rules are saved yet.</p>}<form className="stack-form" onSubmit={saveMonitor}><label>Rule name<input value={monitor.name} onChange={(event) => setMonitor({ ...monitor, name: event.target.value })} /></label><label>Mode<select value={monitor.mode} onChange={(event) => setMonitor({ ...monitor, mode: event.target.value as typeof monitor.mode })}><option value="interval_research">Interval research instruction</option><option value="source_change">Source version change</option></select></label><label>Interval (minutes)<input type="number" min={5} value={monitor.interval_minutes} onChange={(event) => setMonitor({ ...monitor, interval_minutes: Number(event.target.value) })} /></label><label>Condition<textarea rows={3} value={monitor.condition} onChange={(event) => setMonitor({ ...monitor, condition: event.target.value })} /></label>{monitor.mode === 'source_change' && <fieldset className="source-picker"><legend>Referenced sources</legend>{sources.length ? sources.map((source, index) => { const id = source.id ?? source.source_ref ?? `source-${index}`; return <label className="check-row" key={id}><input type="checkbox" checked={monitor.source_ids.includes(id)} onChange={() => toggleSource(id)} /><span>{source.title ?? id}<small>{source.source_type ?? 'source'} · {source.version == null ? 'version unknown' : `v${source.version}`}</small></span></label> }) : <p className="muted-copy">No same-namespace sources are available yet.</p>}</fieldset>}<label className="check-row"><input type="checkbox" checked={monitor.enabled} onChange={(event) => setMonitor({ ...monitor, enabled: event.target.checked })} /> <span>Enable local monitoring <small>Default disabled · no third-party notifications</small></span></label><button type="submit" className="button button-subtle"><Icon name="clock" size={15} /> Save monitoring rule</button></form></section>
      <section className="workspace-card"><div className="section-heading"><div><span className="eyebrow">BACKUP / RESTORE</span><h2>Protect local records</h2></div></div><p className="muted-copy">The backend uses SQLite backup validation and retains referenced evidence separately. Backup paths stay private to the local runtime.</p><div className="button-row"><button type="button" className="button button-primary" onClick={() => void onBackup()}><Icon name="download" size={15} /> Create database snapshot</button></div><p className="muted-copy">Database only. For a full backup including evidence, run <code>./scripts/backup.sh</code>.</p><div className="notice-card notice-amber" role="note"><Icon name="upload" size={17} /><div><strong>Restore a full archive from a stopped service</strong><p>First run <code>./scripts/backup.sh</code> and use the returned <code>.tar.gz</code> archive path. Stop the local service, then run <code>./scripts/restore.sh &lt;archive&gt; --confirm-stopped</code> from the repository root.</p><p className="muted-copy">Restart the app after validation. See <code>docs/setup.md</code> for the safety checks and pre-restore copy.</p></div></div></section>
    </div>
    <section className="workspace-card settings-lower"><div className="section-heading"><div><span className="eyebrow">ACCESSIBILITY / LOCAL RUNTIME</span><h2>Interface behavior</h2></div></div><label className="toggle-row"><span><strong>Reduced motion</strong><small>Stop decorative work animation and use lighter scene rendering.</small></span><input type="checkbox" checked={reducedMotion} onChange={(event) => onReducedMotion(event.target.checked)} /></label><div className="runtime-row"><span><strong>Connection</strong><small>Last event cursor {office.event_cursor ?? 'unknown'} · API state remains observable in the command bar.</small></span><span><StatusBadge status={providerRows.some((provider) => provider.available) ? 'ready' : 'blocked'} compact /></span></div></section>
  </div>
}

function Metric({ value, label, tone, wide = false }: { value: number | string; label: string; tone: string; wide?: boolean }) { return <div className={`metric metric-${tone}${wide ? ' metric-wide' : ''}`}><strong>{value}</strong><span>{label}</span></div> }
function Empty({ icon, title, copy }: { icon: string; title: string; copy: string }) { return <div className="empty-panel"><Icon name={icon} size={22} /><strong>{title}</strong><p>{copy}</p></div> }
function CoverageCallout({ title, copy }: { title: string; copy: string }) { return <article className="coverage-callout"><span className="eyebrow">COVERAGE NOTE</span><h3>{title}</h3><p>{copy}</p></article> }
