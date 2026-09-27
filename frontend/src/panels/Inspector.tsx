import { useEffect, useMemo, useRef } from 'react'
import type { AgentRecord, CalculationRecord, CioBrief, EvidenceRef, FactClaimRecord, Namespace, OutputRecord, ResearchCandidate, RoutingPlan, SimulationRun, TaskEvent, TaskRecord } from '../types'
import { sourceDocumentUrl } from '../api'
import { Icon } from '../components/Icon'
import { PrivateFinancialDisclosure } from '../components/PrivateFinancialDisclosure'
import { StatusBadge } from '../components/StatusBadge'
import { hasPrivateFinancialData, hasPrivateFinancialText, isPersonalFinancialSource } from '../components/financialPrivacy'
import { formatDateTime } from '../date'

export type InspectorTab = 'overview' | 'current' | 'queue' | 'outputs' | 'evidence' | 'history' | 'model'

const TABS: Array<{ id: InspectorTab; label: string }> = [
  { id: 'overview', label: 'Overview' }, { id: 'current', label: 'Current task' }, { id: 'queue', label: 'Queue' }, { id: 'outputs', label: 'Outputs' }, { id: 'evidence', label: 'Evidence' }, { id: 'history', label: 'History' }, { id: 'model', label: 'Model' },
]

function displayDate(value?: string | null) {
  return formatDateTime(value, 'Unknown', 'short')
}

function meaningfulText(value: unknown) {
  if (value == null) return null
  const text = String(value).trim()
  if (!text) return null
  const normalized = text.toLowerCase().replace(/[.!?]+$/, '')
  if (['unknown', 'n/a', 'na', 'not available', 'not recorded', 'reason not supplied', 'reason not recorded', 'no reason supplied'].includes(normalized)) return null
  return text
}

function elapsed(task?: TaskRecord | null) {
  if (!task) return '—'
  if (typeof task.elapsed_seconds === 'number') {
    const seconds = Math.max(0, Math.floor(task.elapsed_seconds))
    if (seconds < 60) return `${seconds}s`
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`
    return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`
  }
  return 'Not measured'
}

function sourceTitle(source: EvidenceRef) {
  return source.title ?? source.source_ref ?? source.id ?? 'Source record'
}

function outputTitle(output: OutputRecord) {
  return output.title ?? output.id ?? 'Saved output'
}

function taskAllows(task: TaskRecord, action: string, fallback = false) {
  const actions = task.allowed_actions
  if (Array.isArray(actions)) return actions.includes(action)
  if (actions && typeof actions === 'object') return actions[action] === true
  return fallback
}

function simulationTitle(simulation: SimulationRun) {
  return simulation.name ?? simulation.id ?? 'Simulation run'
}

function cioPriceLabel(plan?: CioBrief['entry_plan'] | null) {
  if (!plan) return 'Unknown'
  const currency = plan.currency && plan.currency !== 'UNK' ? `${plan.currency} ` : ''
  if (plan.low != null && plan.high != null) return `${currency}${plan.low}–${plan.high}`
  if (plan.low != null) return `${currency}${plan.low}+`
  if (plan.high != null) return `up to ${currency}${plan.high}`
  if (plan.value != null) return `${currency}${plan.value}`
  return 'Unknown'
}

function CioBriefOutputCard({ brief, onSource, decisionDisposition, decisionDate, proposedAction }: { brief: CioBrief; onSource: (source: EvidenceRef) => void; decisionDisposition?: string | null; decisionDate?: string | null; proposedAction?: string | null }) {
  const gate = String(decisionDisposition ?? '').toLowerCase().replaceAll('-', '_').replaceAll(' ', '_')
  const gatedWait = gate === 'defer' || gate === 'deferred'
  const gatedAvoid = gate === 'reject' || gate === 'rejected'
  const stance = gatedWait ? 'wait' : gatedAvoid ? 'avoid' : String(brief.stance ?? 'wait').toLowerCase().replace('watch', 'wait').replace('defer', 'wait')
  const risks = brief.accepted_risks?.length ? brief.accepted_risks : brief.risks ?? []
  const gaps = brief.blocking_gaps?.length ? brief.blocking_gaps : brief.missing_inputs ?? []
  const sourceButtons = (refs?: string[]) => refs?.length ? <span className="cio-source-refs">Sources: {refs.map((id, index) => <button type="button" key={`${id}-${index}`} onClick={() => onSource({ id, source_ref: id, title: id })}>{id}</button>)}</span> : null
  const reason = meaningfulText(brief.reason) ?? meaningfulText(brief.entry_advice)
  const asOf = meaningfulText(brief.as_of) ?? meaningfulText(decisionDate)
  const ticker = meaningfulText(brief.ticker)
  return <section className="inspector-cio-brief"><div className="section-heading"><div><span className="eyebrow">CIO BRIEF</span><h3>Decision summary</h3></div><span className={`cio-stance cio-stance-${stance}`}>{stance}</span></div>{gatedWait && <div className="notice-card notice-amber cio-gate-note"><Icon name="warning" size={16} /><div><strong>Decision gate: wait</strong><p>The authoritative saved review disposition is deferred.{gaps.length ? ` ${gaps.length} blocking gaps remain; details are listed below.` : ' The saved review did not authorize entry.'}</p></div></div>}{brief.legacy && <p className="cio-legacy-inline">Legacy field shape retained; unsupported prices stay unknown.</p>}<div className="cio-output-meta"><span>{meaningfulText(brief.as_of) ? 'As of' : 'Decision saved'} {displayDate(asOf)}</span>{meaningfulText(brief.horizon) && <span>Horizon {meaningfulText(brief.horizon)}</span>}{ticker && <span>Ticker {ticker}</span>}</div><p className="cio-output-reason">{reason ?? 'Reason not recorded.'}</p>{proposedAction && <div className="proposed-action"><span>Saved proposed action</span><strong>{proposedAction}</strong></div>}<div className="cio-output-price-grid"><div><span>Entry range</span><strong>{cioPriceLabel(brief.entry_plan ?? brief.entry_zone)}</strong>{(brief.entry_plan ?? brief.entry_zone)?.basis && <small>Basis: {(brief.entry_plan ?? brief.entry_zone)?.basis}</small>}{(brief.entry_plan ?? brief.entry_zone)?.missing_reason && <small className="cio-missing">Why unknown: {(brief.entry_plan ?? brief.entry_zone)?.missing_reason}</small>}{sourceButtons((brief.entry_plan ?? brief.entry_zone)?.source_refs)}</div><div><span>Target price</span><strong>{cioPriceLabel(brief.target_price)}</strong>{brief.target_price?.horizon && <small>Horizon: {brief.target_price.horizon}</small>}{brief.target_price?.basis && <small>Basis: {brief.target_price.basis}</small>}{brief.target_price?.missing_reason && <small className="cio-missing">Why unknown: {brief.target_price.missing_reason}</small>}{sourceButtons(brief.target_price?.source_refs ?? brief.target_price_source_refs)}</div></div><div className="cio-output-lists"><OutputList title="Accepted risks" items={risks} showEmpty /><OutputList title="Growth catalysts" items={brief.catalysts ?? []} showEmpty /><OutputList title="Invalidation conditions" items={brief.invalidation_conditions ?? []} showEmpty /><OutputList title="Blocking gaps" items={gaps} showEmpty /></div>{brief.next_review_trigger && <small className="cio-next-review-inline">Next review trigger: {brief.next_review_trigger}</small>}</section>
}

function OutputCard({ output, onOpen }: { output: OutputRecord; onOpen: (output: OutputRecord) => void }) {
  const sourceCount = output.source_refs?.length ?? 0
  const privateOutput = hasPrivateFinancialData(output) || hasPrivateFinancialText(output.conclusion) || hasPrivateFinancialText(output.proposed_action)
  return <button type="button" className="output-card" onClick={() => onOpen(output)}>
    <span className="output-card-top"><span className="output-version">v{output.version ?? 1}</span><span className="output-provenance">{output.provenance === 'simulation' ? 'SIMULATION' : 'SAVED'}</span></span>
    <strong>{outputTitle(output)}</strong>
    <span className={`output-summary${privateOutput ? ' private-financial-hidden' : ''}`}>{privateOutput ? 'Personal account material hidden by default.' : output.conclusion ?? 'No summary was saved.'}</span>
    <span className="output-meta">{sourceCount} source{sourceCount === 1 ? '' : 's'} · {displayDate(output.created_at)}</span>
    <span className="output-open">Open complete analysis <Icon name="arrow" size={14} /></span>
  </button>
}

function TaskMeta({ task }: { task: TaskRecord }) {
  const latestAttempt = task.attempts?.[task.attempts.length - 1]
  const providerStart = task.provider_started_at ?? latestAttempt?.provider_started_at ?? null
  const status = String(task.status ?? 'unknown')
  const waiting = ['queued', 'waiting_for_evidence', 'waiting_for_review', 'blocked'].includes(status) || task.dispatch_state === 'waiting_capacity'
  const startValue = providerStart ?? (waiting ? null : latestAttempt?.started_at ?? task.started_at ?? null)
  const startLabel = providerStart ? 'Provider started' : latestAttempt?.started_at ? 'Attempt started' : task.started_at ? 'Task started' : 'Started'
  const startedValue = startValue ? displayDate(startValue) : 'Not started'
  return <div className="task-meta-grid">
    <div><span>{startValue ? startLabel : 'Started'}</span><strong>{startedValue}</strong></div>
    <div><span>Elapsed</span><strong>{elapsed(task)}</strong></div>
    <div><span>{task.dispatch_state === 'waiting_capacity' ? 'Dispatch' : 'Last update'}</span><strong>{task.dispatch_state === 'waiting_capacity' ? 'Waiting for model capacity' : displayDate(task.updated_at)}</strong></div>
    <div><span>Attempt</span><strong>{task.attempt_id ?? 'Not started'}</strong></div>
  </div>
}

function CurrentTask({ task, onControl }: { task?: TaskRecord | null; onControl: (action: 'pause' | 'resume' | 'cancel' | 'retry', scope: 'task' | 'run' | 'firm', id: string | null) => void }) {
  if (!task) return <div className="empty-panel"><Icon name="clock" size={22} /><strong>No current task</strong><p>This agent is available for a new routed question.</p></div>
  const status = String(task.status ?? 'idle')
  const taskId = task.id ?? task.task_id ?? null
  const runId = task.run_id ?? null
  const canRetry = taskAllows(task, 'retry', ['blocked', 'failed', 'interrupted'].includes(status))
  const canPause = taskAllows(task, 'pause', ['running', 'queued', 'waiting_for_review', 'waiting_for_evidence'].includes(status))
  const canResume = taskAllows(task, 'resume', false)
  const canCancel = taskAllows(task, 'cancel', ['running', 'queued', 'waiting_for_review', 'waiting_for_evidence'].includes(status))
  const memory = task.memory_context
  const privateTask = hasPrivateFinancialData(task) || hasPrivateFinancialText(task.question) || hasPrivateFinancialText(task.current_task)
  return <div className="inspector-section current-task-panel">
    <div className="task-heading"><div><span className="eyebrow">CURRENT RECORD</span><h3>{privateTask ? 'Personal account material hidden by default.' : task.question ?? task.current_task ?? 'Assigned work'}</h3></div><StatusBadge status={status} paused={Boolean(task.paused)} /></div>
    {task.current_task && <p className="task-current">{task.current_task}</p>}
    {task.dispatch_state === 'waiting_capacity' && <div className="event-note"><span className="event-note-dot" />Waiting for model capacity; provider start time will appear after a slot is acquired.</div>}
    {task.wait_reason && <div className="event-note"><span className="event-note-dot" />{task.wait_reason}</div>}
    {task.resolved_model_config ? <p className="task-model">Resolved for this attempt: {task.resolved_model_config.provider} / {task.resolved_model_config.model} / {task.resolved_model_config.reasoning_mode ?? 'effort unknown'}</p> : <p className="task-model">Awaiting dispatch · model attribution will appear when this attempt is resolved.</p>}
    <TaskMeta task={task} />
    {task.progress_message && <div className="event-note"><span className="event-note-dot" />{task.progress_message}</div>}
    {task.blocking_reason && <div className="blocking-note"><Icon name="warning" size={16} /><div><strong>Blocking reason</strong><p>{task.blocking_reason}</p></div></div>}
    {(canRetry || canPause || canResume || canCancel) && <div className="inspector-actions">
      {canRetry && taskId && <button type="button" className="button button-primary" onClick={() => onControl('retry', 'task', taskId)}><Icon name="refresh" size={15} /> Retry task</button>}
      {canResume && taskId && <button type="button" className="button button-subtle" onClick={() => onControl('resume', 'task', taskId)}><Icon name="play" size={15} /> Resume task</button>}
      {canPause && taskId && <button type="button" className="button button-subtle" onClick={() => onControl('pause', 'task', taskId)}><Icon name="pause" size={15} /> Pause task</button>}
      {canPause && runId && <button type="button" className="button button-subtle" onClick={() => onControl('pause', 'run', runId)}><Icon name="pause" size={15} /> Pause run</button>}
      {canCancel && taskId && <button type="button" className="button button-danger" onClick={() => onControl('cancel', 'task', taskId)}><Icon name="stop" size={15} /> Cancel task</button>}
    </div>}
    {task.input_references && task.input_references.length > 0 && <div className="ref-list"><span className="eyebrow">INPUT REFERENCES</span>{task.input_references.map((source, index) => <span key={`${source.id ?? source.source_ref ?? 'input'}-${index}`}>{sourceTitle(source)}{source.locator ? ` · ${source.locator}` : ''}</span>)}</div>}
    {memory && ((memory.reused?.length ?? 0) > 0 || (memory.fresh?.length ?? 0) > 0) && <div className="inspector-memory-context"><span className="eyebrow">MEMORY CONTEXT</span>{memory.reason && <p>{memory.reason}</p>}{(memory.reused?.length ?? 0) > 0 && <div><strong>Reused information</strong>{memory.reused?.map((item, index) => <span key={`${item.record_id ?? item.title ?? 'reused'}-${index}`}>{item.title ?? item.record_id} · {item.freshness ?? 'unknown'}{item.reuse_reason ? ` · ${item.reuse_reason}` : ''}</span>)}</div>}{(memory.fresh?.length ?? 0) > 0 && <div><strong>Fresh retrievals</strong>{memory.fresh?.map((item, index) => <span key={`${item.record_id ?? item.title ?? 'fresh'}-${index}`}>{item.title ?? item.record_id} · {item.freshness ?? 'fresh'}</span>)}</div>}</div>}
  </div>
}

function SimulationRecordCard({ simulation, onOpenSimulation }: { simulation: SimulationRun; onOpenSimulation?: (simulation: SimulationRun) => void }) {
  const detailRounds = simulation.rounds_detail ?? simulation.events ?? []
  return <article className="simulation-agent-card"><div className="simulation-agent-heading"><div><span className="eyebrow">SIMULATION RECORD · A07</span><strong>{simulationTitle(simulation)}</strong></div><StatusBadge status={simulation.status} compact /></div><p>{simulation.summary ?? simulation.scenario ?? 'Bounded scenario detail is available in the Simulation lab.'}</p><div className="simulation-agent-meta"><span>{simulation.participants ?? '—'} participants</span><span>{(simulation.rounds ?? detailRounds.length) || '—'} rounds</span><span>Seed {simulation.seed ?? '—'}</span></div>{onOpenSimulation && <button type="button" className="button button-subtle" onClick={() => onOpenSimulation(simulation)}><Icon name="beaker" size={14} /> Open simulation report</button>}</article>
}

function claimStatus(claim: FactClaimRecord) {
  return claim.validation_status ?? claim.status ?? 'unknown'
}

function ClaimCard({ claim, index, onSource }: { claim: FactClaimRecord; index: number; onSource: (source: EvidenceRef) => void }) {
  const sourceId = claim.source_id ?? claim.source_ref
  const status = claimStatus(claim)
  const reason = claim.validation_reason ?? claim.unknown_reason
  const excerpt = claim.matched_excerpt ?? claim.excerpt
  const privateClaim = hasPrivateFinancialData(claim) || hasPrivateFinancialText(`${String(claim.claim ?? '')} ${String(claim.value ?? '')} ${String(excerpt ?? '')}`)
  const claimValue = <span>{claim.value == null ? `Value unknown${reason ? ` · ${String(reason)}` : ''}` : String(claim.value)}{claim.unit ? ` · ${String(claim.unit)}` : ''}{claim.period ? ` · ${String(claim.period)}` : ''}</span>
  return <article className="claim-record"><strong>{privateClaim ? 'Personal account value' : String(claim.claim ?? claim.value ?? `Claim ${index + 1}`)}</strong>{privateClaim ? <PrivateFinancialDisclosure label="Reveal recorded value locally" description="This claim may contain personal account information. It stays closed until you choose to inspect it."><div>{claimValue}{excerpt && <p className="source-match-note">Retained excerpt: “{String(excerpt)}”</p>}</div></PrivateFinancialDisclosure> : <>{claimValue}{excerpt && <p className="source-match-note">Retained excerpt: “{String(excerpt)}”</p>}</>}<small className={`claim-validation claim-validation-${String(status).replaceAll('_', '-')}`}>{claim.semantic_status ? `Semantic binding: ${claim.semantic_status.replaceAll('_', ' ')}` : status === 'unknown' ? 'Validation status unavailable' : `Source check: ${String(status).replaceAll('_', ' ')}`}{reason ? ` · ${String(reason)}` : ''}</small>{(claim.subject || claim.metric || claim.scale || claim.basis || claim.statement_type || claim.source_version || claim.freshness) && <small className="claim-binding-meta">{[claim.subject && `Issuer ${claim.subject}`, claim.metric && `Metric ${claim.metric}`, claim.scale && `Scale ${claim.scale}`, claim.basis && `Basis ${claim.basis}`, claim.statement_type, claim.source_version && `source ${claim.source_version}`, claim.freshness && `freshness ${claim.freshness}`].filter(Boolean).join(' · ')}</small>}<button type="button" disabled={!sourceId} onClick={() => sourceId && onSource({ id: String(sourceId), source_ref: String(sourceId), locator: claim.locator ?? null, selected_locator: claim.locator ?? null, line_start: claim.line_start ?? null, line_end: claim.line_end ?? null, matched_excerpt: excerpt ?? null, validation_status: status, validation_reason: reason ?? null, semantic_status: claim.semantic_status, binding_checks: claim.binding_checks, source_version: claim.source_version, freshness: claim.freshness, title: String(sourceId) })}><Icon name="link" size={13} /> {String(sourceId ?? 'Source unavailable')}{claim.locator ? ` · ${claim.locator}` : ''}</button></article>
}

function Queue({ queue, onControl }: { queue: TaskRecord[]; onControl: (action: 'pause' | 'resume' | 'cancel' | 'retry', scope: 'task' | 'run' | 'firm', id: string | null) => void }) {
  if (!queue.length) return <div className="empty-panel"><Icon name="check" size={22} /><strong>Queue is clear</strong><p>New requests are routed by the Chief of Staff.</p></div>
  return <div className="stack-list">{queue.map((task, index) => { const taskId = task.id ?? task.task_id ?? null; const canRetry = taskAllows(task, 'retry', ['blocked', 'failed', 'interrupted'].includes(String(task.status))); const canCancel = taskAllows(task, 'cancel', ['queued', 'running', 'waiting_for_review', 'waiting_for_evidence'].includes(String(task.status))); const canPause = taskAllows(task, 'pause', ['queued', 'running', 'waiting_for_review', 'waiting_for_evidence'].includes(String(task.status))); const canResume = taskAllows(task, 'resume', Boolean(task.paused) || String(task.status) === 'paused'); return <article className="queue-row" key={taskId ?? index}><div className="queue-index">{String(index + 1).padStart(2, '0')}</div><div className="queue-body"><strong>{task.question ?? task.title ?? task.current_task ?? task.id}</strong><span>{task.updated_at ? displayDate(task.updated_at) : 'Waiting'} · {task.run_id ?? 'No run'}</span></div><StatusBadge status={task.status} paused={Boolean(task.paused)} compact />{canRetry && taskId ? <button type="button" className="icon-button" onClick={() => onControl('retry', 'task', taskId)} aria-label={`Retry ${taskId}`}><Icon name="refresh" size={15} /></button> : canResume && taskId ? <button type="button" className="icon-button" onClick={() => onControl('resume', 'task', taskId)} aria-label={`Resume ${taskId}`}><Icon name="play" size={15} /></button> : canPause && taskId ? <button type="button" className="icon-button" onClick={() => onControl('pause', 'task', taskId)} aria-label={`Pause ${taskId}`}><Icon name="pause" size={15} /></button> : canCancel && taskId ? <button type="button" className="icon-button" onClick={() => onControl('cancel', 'task', taskId)} aria-label={`Cancel ${taskId}`}><Icon name="stop" size={15} /></button> : <span className="queue-action-note">History</span>}</article> })}</div>
}

function Evidence({ evidence, onSource }: { evidence: EvidenceRef[]; onSource: (source: EvidenceRef) => void }) {
  if (!evidence.length) return <div className="empty-panel"><Icon name="database" size={22} /><strong>No evidence attached</strong><p>A source import or a connector result will appear here with dates and locators.</p></div>
  return <div className="stack-list">{evidence.map((source, index) => <button type="button" className="evidence-row" key={source.id ?? source.source_ref ?? index} onClick={() => onSource(source)}><span className="evidence-icon"><Icon name="book" size={16} /></span><span className="evidence-body"><strong>{sourceTitle(source)}</strong><span>{source.source_type ?? 'source'} · {source.locator ?? 'Locator unavailable'}</span><span>{source.observation_time ? `Observed ${displayDate(source.observation_time)}` : 'Observation date unknown'} · {source.retrieved_at ? `Retrieved ${displayDate(source.retrieved_at)}` : 'retrieval unknown'}</span></span><Icon name="chevron" size={15} /></button>)}</div>
}

function History({ history }: { history: Array<Record<string, unknown>> }) {
  if (!history.length) return <div className="empty-panel"><Icon name="clock" size={22} /><strong>No prior events in this view</strong><p>Persisted task events will be replayed after the next update.</p></div>
  return <div className="timeline-list">{history.map((item, index) => { const event = item as TaskEvent; const message = String(event.payload?.message ?? item.message ?? item.text ?? 'Recorded event'); const privateEvent = hasPrivateFinancialData(event.payload) || hasPrivateFinancialText(message); return <article key={String(event.event_id ?? event.sequence_id ?? index)}><span className="timeline-marker" /><div><span className="timeline-date">{displayDate(event.emitted_at ?? (item.at as string | undefined))}</span><strong>{String(event.type ?? item.type ?? 'event').replaceAll('_', ' ')}</strong>{privateEvent ? <PrivateFinancialDisclosure label="Reveal recorded event locally" description="This event may contain personal account information. It stays closed until you choose to inspect it."><p>{message}</p></PrivateFinancialDisclosure> : <p>{message}</p>}</div></article> })}</div>
}

export function Inspector({ agent, namespace: _namespace, tab, onTab, onClose, onControl, onOpenOutput, onOpenSource, onOpenSimulation }: { agent: AgentRecord; namespace: Namespace; tab: InspectorTab; onTab: (tab: InspectorTab) => void; onClose: () => void; onControl: (action: 'pause' | 'resume' | 'cancel' | 'retry', scope: 'firm' | 'run' | 'task', id: string | null) => void; onOpenOutput: (output: OutputRecord) => void; onOpenSource: (source: EvidenceRef) => void; onOpenSimulation?: (simulation: SimulationRun) => void }) {
  const task = agent.current_task ?? agent.task
  const queue = agent.queue ?? []
  const outputs = useMemo(() => agent.outputs?.length ? agent.outputs : agent.latest_output ? [agent.latest_output] : task?.outputs?.length ? task.outputs : task?.output ? [task.output] : [], [agent.latest_output, agent.outputs, task])
  const evidence = agent.evidence?.length ? agent.evidence : task?.evidence_refs ?? []
  const history = agent.history ?? task?.history ?? []
  const simulation = agent.current_simulation ?? agent.related_simulations?.[0] ?? null
  const lastUpdate = agent.last_update ?? task?.updated_at ?? agent.latest_completed_task?.updated_at ?? agent.latest_output?.created_at ?? null
  const awaitingDispatch = Boolean(task && !task.resolved_model_config)
  const resolvedModel = task?.resolved_model_config ?? (!task ? agent.model : null)
  const resolvedSource = task?.resolved_model_config ? 'task attempt' : awaitingDispatch ? 'awaiting dispatch' : agent.model ? (agent.model_source ?? 'role setting') : 'awaiting dispatch'
  return <aside id="office-inspector" className="inspector" tabIndex={-1} aria-label={`${agent.name} record`}>
    <div className="inspector-head"><div className="inspector-title"><div className="avatar" style={{ '--avatar-accent': agent.accent ?? '#d5ae62' } as React.CSSProperties}>{agent.id.replace('A', '')}</div><div><span className="eyebrow">{agent.id} · {agent.zone}</span><h2>{agent.name}</h2><p>{agent.title}</p></div></div><button type="button" className="icon-button close-inspector" onClick={onClose} aria-label="Close inspector"><Icon name="close" /></button></div>
    <div className="inspector-status"><StatusBadge status={agent.status} paused={Boolean(agent.paused)} /><span>Local research workspace</span>{agent.selection_state && <span className={`selection-state selection-state-${agent.selection_state}`}>Question role · {agent.selection_state.replaceAll('_', ' ')}</span>}</div>
    <nav className="inspector-tabs" aria-label="Agent record tabs">{TABS.map((item) => <button type="button" key={item.id} aria-selected={tab === item.id} className={tab === item.id ? 'is-active' : ''} onClick={() => onTab(item.id)}>{item.label}{item.id === 'outputs' && outputs.length > 0 && <em>{outputs.length}</em>}{item.id === 'queue' && queue.length > 0 && <em>{queue.length}</em>}</button>)}</nav>
    <div className="inspector-scroll">
      {tab === 'overview' && <div className="inspector-section"><div className="mandate-card"><span className="eyebrow">MANDATE</span><p>{agent.mandate || 'The backend has not supplied a mandate for this role.'}</p>{agent.selection_reason && <small className="selection-reason">Question routing: {agent.selection_reason}</small>}</div><div className="overview-stats"><div><span>Current status</span><strong><StatusBadge status={agent.status} paused={Boolean(agent.paused)} compact /></strong></div><div><span>Queued work</span><strong>{agent.queued_count ?? queue.length}</strong></div><div><span>Saved outputs</span><strong>{agent.output_count ?? outputs.length}</strong></div><div><span>Last update</span><strong>{displayDate(lastUpdate)}</strong></div></div>{!task && agent.latest_completed_task && <div className="event-note"><span className="event-note-dot" /><strong>Last finished task:</strong> {agent.latest_completed_task.title ?? agent.latest_completed_task.question ?? agent.latest_completed_task.id ?? 'Saved task'}</div>}<CurrentTask task={task} onControl={onControl} />{simulation && <SimulationRecordCard simulation={simulation} onOpenSimulation={onOpenSimulation} />}</div>}
      {tab === 'current' && <div className="inspector-section"><CurrentTask task={task} onControl={onControl} />{simulation && <SimulationRecordCard simulation={simulation} onOpenSimulation={onOpenSimulation} />}</div>}
      {tab === 'queue' && <div className="inspector-section"><div className="section-heading"><div><span className="eyebrow">DISPATCH QUEUE</span><h3>Work waiting for this role</h3></div><span className="count-pill">{queue.length}</span></div><Queue queue={queue} onControl={onControl} /></div>}
      {tab === 'outputs' && <div className="inspector-section"><div className="section-heading"><div><span className="eyebrow">SAVED WORK</span><h3>Output versions</h3></div><span className="count-pill">{outputs.length}</span></div>{outputs.length ? <div className="output-list">{outputs.map((output, index) => <OutputCard output={output} onOpen={onOpenOutput} key={output.id ?? output.output_id ?? index} />)}</div> : <div className="empty-panel"><Icon name="book" size={22} /><strong>No saved outputs</strong><p>Only validated, attributed outputs appear here.</p></div>}{simulation && <SimulationRecordCard simulation={simulation} onOpenSimulation={onOpenSimulation} />}</div>}
      {tab === 'evidence' && <div className="inspector-section"><div className="section-heading"><div><span className="eyebrow">SOURCE TRAIL</span><h3>Evidence references</h3></div><span className="count-pill">{evidence.length}</span></div><Evidence evidence={evidence} onSource={onOpenSource} /></div>}
      {tab === 'history' && <div className="inspector-section"><div className="section-heading"><div><span className="eyebrow">AUDIT TRAIL</span><h3>Observable work events</h3></div></div><History history={history} /></div>}
      {tab === 'model' && <div className="inspector-section"><div className="section-heading"><div><span className="eyebrow">{awaitingDispatch ? 'AWAITING DISPATCH' : 'RESOLVED CONFIGURATION'}</span><h3>Model attribution</h3></div></div><dl className="detail-list"><dt>Provider</dt><dd>{awaitingDispatch ? 'Awaiting dispatch' : resolvedModel?.provider ?? 'Unknown'}</dd><dt>Model</dt><dd>{awaitingDispatch ? 'Awaiting dispatch' : resolvedModel?.model ?? 'Unknown'}</dd><dt>Reasoning / mode</dt><dd>{awaitingDispatch ? 'Awaiting dispatch' : resolvedModel?.reasoning_mode ?? 'Unknown'}</dd><dt>Profile</dt><dd>{awaitingDispatch ? 'Awaiting dispatch' : resolvedModel?.profile ?? 'Unknown'}</dd><dt>Resolution source</dt><dd>{resolvedSource}</dd><dt>Billing route</dt><dd>{awaitingDispatch ? 'Awaiting dispatch' : resolvedModel?.billing_route ?? 'Unknown'}</dd><dt>Attempt</dt><dd>{task?.attempt_id ?? 'No current attempt'}</dd></dl><p className="model-note">{awaitingDispatch ? 'The backend has not resolved a model for this attempt yet. Role settings remain available for later dispatch.' : 'Settings changed after dispatch apply to later work. This record keeps the provider and model resolved for the current attempt and its saved outputs.'}</p></div>}
    </div>
  </aside>
}

function statusCopy(status?: string | null) {
  return status ? status.replaceAll('_', ' ') : 'status unknown'
}

function CalculationList({ calculations }: { calculations: CalculationRecord[] }) {
  if (!calculations.length) return null
  return <section className="calculations-section"><span className="eyebrow">BACKEND CALCULATIONS</span><div className="calculations-list">{calculations.map((calculation, index) => <article key={`${calculation.label ?? 'calculation'}-${index}`}><strong>{calculation.label ?? `Calculation ${index + 1}`}</strong><span>{calculation.value == null ? 'Value unknown' : calculation.value}{calculation.unit ? ` · ${calculation.unit}` : ''}</span>{calculation.formula && <small>Formula: {calculation.formula}</small>}{calculation.missing_reason && <small className="calculation-reason">Why unavailable: {calculation.missing_reason}</small>}{calculation.operation && <small>Operation: {calculation.operation}</small>}{calculation.assumed_discount_fraction && <small className="calculation-assumption">Hypothetical price discount assumption: {calculation.assumed_discount_fraction}</small>}{calculation.assumption_rationale && <small className="calculation-assumption">Assumption rationale: {calculation.assumption_rationale}</small>}</article>)}</div></section>
}

function safeExternalUrl(value: string) {
  try {
    const url = new URL(value)
    return url.protocol === 'http:' || url.protocol === 'https:'
  } catch {
    return false
  }
}

function CandidateSources({ candidate, onSource }: { candidate: ResearchCandidate; onSource: (source: EvidenceRef) => void }) {
  const urls = Array.from(new Set([...(candidate.source_urls ?? []), ...(candidate.primary_urls ?? []), ...(candidate.urls ?? [])]))
  const refs = candidate.source_refs ?? []
  if (!urls.length && !refs.length) return <small className="research-candidate-sources-empty">No source references returned.</small>
  return <div className="research-candidate-sources"><span className="eyebrow">SOURCE REFERENCES</span><div className="source-chip-list">{urls.map((url, index) => safeExternalUrl(url) ? <a className="research-candidate-source-link" href={url} target="_blank" rel="noreferrer" key={`url-${url}-${index}`}><Icon name="external" size={13} /> {url}</a> : <span className="research-candidate-source-text" key={`url-${url}-${index}`}>{url}</span>)}{refs.map((source, index) => source.url && safeExternalUrl(source.url) ? <a className="research-candidate-source-link" href={source.url} target="_blank" rel="noreferrer" key={`ref-${source.id ?? source.source_ref ?? index}`}><Icon name="external" size={13} /> {sourceTitle(source)}</a> : <button type="button" key={`ref-${source.id ?? source.source_ref ?? index}`} onClick={() => onSource(source)}><Icon name="book" size={13} /> {sourceTitle(source)}</button>)}</div></div>
}

function ResearchCandidateList({ candidates, onSource }: { candidates: ResearchCandidate[]; onSource: (source: EvidenceRef) => void }) {
  if (!candidates.length) return null
  return <section className="research-candidates-section"><div className="section-heading"><div><span className="eyebrow">RESEARCH CANDIDATES</span><h3>Discovery leads</h3></div><span className="muted-copy">Research leads only · no allocation authority</span></div><div className="research-candidates">{candidates.map((candidate, index) => {
    const ticker = candidate.ticker ?? candidate.symbol
    const title = ticker ?? candidate.name ?? `Candidate ${index + 1}`
    const status = candidate.status ?? 'candidate'
    const verification = candidate.verified ? 'verified' : 'unverified'
    return <article className="research-candidate" key={`${title}-${index}`}><div className="research-candidate-heading"><div><strong>{title}</strong>{candidate.name && ticker && <span>{candidate.name}</span>}</div><span className={`candidate-status candidate-status-${verification}`}>{status.replaceAll('_', ' ')} · {verification}</span></div>{candidate.rationale && <p>{candidate.rationale}</p>}{!candidate.verified && candidate.unverified_reason && <small className="candidate-verification-note">Verification note: {candidate.unverified_reason}</small>}{candidate.evidence_available != null && <small className="candidate-evidence-state">Evidence archive: {candidate.evidence_available ? 'available locally' : 'not available'}</small>}<CandidateSources candidate={candidate} onSource={onSource} /></article>
  })}</div></section>
}

function RoutingPlanCard({ plan, chiefOfStaff = false }: { plan: RoutingPlan; chiefOfStaff?: boolean }) {
  const tickers = plan.tickers ?? []
  const analysts = plan.selected_analysts ?? []
  const queries = plan.research_queries ?? []
  return <section className="routing-plan-card"><span className="eyebrow">{chiefOfStaff ? 'CHIEF OF STAFF ROUTING PLAN' : 'ROUTING PLAN'}</span><div className="routing-plan-grid"><div><span>Intent</span><strong>{plan.intent ?? 'research'}</strong></div><div><span>Horizon</span><strong>{plan.horizon ?? 'Derived from question'}</strong></div>{tickers.length > 0 && <div><span>Tickers</span><strong>{tickers.join(', ')}</strong></div>}{analysts.length > 0 && <div><span>Selected desks</span><strong>{analysts.join(', ')}</strong></div>}</div>{plan.rationale && <p>{plan.rationale}</p>}{queries.length > 0 && <div className="routing-plan-queries"><span className="eyebrow">RESEARCH QUESTIONS</span><ul className="plain-list">{queries.map((query, index) => <li key={`${query}-${index}`}>{query}</li>)}</ul></div>}</section>
}

export function OutputViewer({ output, onClose, onSource }: { output: OutputRecord; onClose: () => void; onSource: (source: EvidenceRef) => void }) {
  const claims = output.fact_claims ?? []
  const provenance = String(output.provenance ?? '')
  const provenanceLabel = provenance === 'simulation' ? 'SIMULATION OUTPUT' : 'SAVED RESEARCH OUTPUT'
  const status = String(output.status ?? 'unknown')
  const privateConclusion = hasPrivateFinancialText(output.conclusion)
  const privateBody = hasPrivateFinancialText(output.body)
  const privateCalculations = hasPrivateFinancialData(output.calculations) || (output.calculations ?? []).some((calculation) => hasPrivateFinancialText(calculation.label) || hasPrivateFinancialText(calculation.value))
  const privateLists = (items: string[]) => items.some((item) => hasPrivateFinancialText(item))
  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
    <article className="output-viewer" role="dialog" aria-modal="true" aria-labelledby="output-viewer-title">
      <header><div><span className="eyebrow">{provenanceLabel} · VERSION {output.version ?? 1}</span><h2 id="output-viewer-title">{outputTitle(output)}</h2><p>{output.provider ?? 'Provider unavailable'} / {output.model ?? 'Model unavailable'} · effort {output.reasoning_mode ?? 'unknown'} · {displayDate(output.created_at)}</p></div><div className="output-header-actions"><StatusBadge status={status} compact /><button type="button" className="icon-button" onClick={onClose} aria-label="Close output"><Icon name="close" /></button></div></header>
      <div className="output-viewer-scroll">
        <section className="output-lede"><span className="eyebrow">CONCLUSION · {statusCopy(output.status)}</span>{privateConclusion ? <PrivateFinancialDisclosure label="Reveal conclusion locally" description="This saved conclusion may contain personal account information. It stays closed until you choose to inspect it."><p>{output.conclusion ?? 'No conclusion was returned.'}</p></PrivateFinancialDisclosure> : <p>{output.conclusion ?? 'No conclusion was returned.'}</p>}{output.proposed_action && <div className="proposed-action"><span>Proposed action</span><strong>{output.proposed_action}</strong></div>}</section>
        {output.decision_brief && <CioBriefOutputCard brief={output.decision_brief} decisionDisposition={output.decision_disposition} decisionDate={output.created_at} proposedAction={output.proposed_action} onSource={onSource} />}
        {output.routing_plan && <RoutingPlanCard plan={output.routing_plan} chiefOfStaff={output.agent_id === 'A00'} />}
        {output.research_candidates && output.research_candidates.length > 0 && <ResearchCandidateList candidates={output.research_candidates} onSource={onSource} />}
        {output.body && <section><span className="eyebrow">ANALYSIS</span>{privateBody ? <PrivateFinancialDisclosure label="Reveal analysis locally" description="This saved analysis may contain personal account information. It stays closed until you choose to inspect it."><div className="analysis-copy">{output.body.split('\n').map((line, index) => line.trim() ? <p key={index}>{line}</p> : <br key={index} />)}</div></PrivateFinancialDisclosure> : <div className="analysis-copy">{output.body.split('\n').map((line, index) => line.trim() ? <p key={index}>{line}</p> : <br key={index} />)}</div>}</section>}
        <div className="output-columns">
          {privateLists(output.assumptions ?? []) ? <PrivateFinancialDisclosure label="Reveal recorded assumptions locally" description="These assumptions may contain personal account information. They stay closed until you choose to inspect them."><OutputList title="Assumptions" items={output.assumptions ?? []} /></PrivateFinancialDisclosure> : <OutputList title="Assumptions" items={output.assumptions ?? []} />}
          {privateCalculations ? <PrivateFinancialDisclosure label="Reveal recorded calculations locally" description="These calculations may contain personal account or allocation values. They stay closed until you choose to inspect them."><CalculationList calculations={output.calculations ?? []} /></PrivateFinancialDisclosure> : <CalculationList calculations={output.calculations ?? []} />}
          {privateLists(output.counterarguments ?? []) ? <PrivateFinancialDisclosure label="Reveal recorded counterarguments locally" description="These counterarguments may contain personal account information. They stay closed until you choose to inspect them."><OutputList title="Counterarguments" items={output.counterarguments ?? []} /></PrivateFinancialDisclosure> : <OutputList title="Counterarguments" items={output.counterarguments ?? []} />}
          {privateLists(output.missing_data ?? []) ? <PrivateFinancialDisclosure label="Reveal recorded missing data locally" description="These records may contain personal account information. They stay closed until you choose to inspect them."><OutputList title="Missing data" items={output.missing_data ?? []} /></PrivateFinancialDisclosure> : <OutputList title="Missing data" items={output.missing_data ?? []} />}
          {privateLists(output.invalidation_conditions ?? []) ? <PrivateFinancialDisclosure label="Reveal recorded conditions locally" description="These conditions may contain personal account information. They stay closed until you choose to inspect them."><OutputList title="Invalidation conditions" items={output.invalidation_conditions ?? []} /></PrivateFinancialDisclosure> : <OutputList title="Invalidation conditions" items={output.invalidation_conditions ?? []} />}
        </div>
        {claims.length > 0 && <section><span className="eyebrow">FACT CLAIMS</span><div className="claims-list">{claims.map((claim, index) => <ClaimCard claim={claim} index={index} onSource={onSource} key={`${String(claim.claim ?? claim.value ?? 'claim')}-${index}`} />)}</div></section>}
        <section><span className="eyebrow">SOURCE REFERENCES</span><div className="source-chip-list">{(output.source_refs ?? []).length ? output.source_refs?.map((source, index) => <button type="button" key={`${source.id ?? source.source_ref ?? 'source'}-${index}`} onClick={() => onSource(source)}><Icon name="book" size={14} /> {sourceTitle(source)}</button>) : <p className="muted-copy">No source references were saved.</p>}</div></section>
      </div>
    </article>
  </div>
}
function OutputList({ title, items, showEmpty = false, emptyLabel = 'Not recorded.' }: { title: string; items: string[]; showEmpty?: boolean; emptyLabel?: string }) {
  if (!items.length && !showEmpty) return null
  return <section><span className="eyebrow">{title}</span>{items.length ? <ul className="plain-list">{items.map((item, index) => <li key={index}>{item}</li>)}</ul> : <p className="muted-copy">{emptyLabel}</p>}</section>
}

function locatorRange(source: EvidenceRef, lines: string[]) {
  let start = source.line_start ?? null
  let end = source.line_end ?? null
  const locator = String(source.selected_locator ?? source.locator ?? '')
  if (start == null) {
    const match = locator.match(/(?:lines?|L)\s*(\d+)(?:\s*(?:-|–|—|to)\s*(?:L\s*)?(\d+))?/i)
    if (match) { start = Number(match[1]); end = match[2] ? Number(match[2]) : Number(match[1]) }
  }
  if (start == null && source.matched_excerpt && lines.length) {
    const target = source.matched_excerpt.trim().toLowerCase()
    const found = lines.findIndex((line) => line.toLowerCase().includes(target))
    if (found >= 0) { start = found + 1; end = found + 1 }
  }
  if (start == null) return null
  const safeEnd = end ?? start
  // A locator outside the retained source is unavailable. Clamping it to the
  // final line would highlight unrelated text and make an invalid claim look
  // supported.
  if (!Number.isInteger(start) || !Number.isInteger(safeEnd) || start < 1 || safeEnd < start || start > lines.length || safeEnd > lines.length) return null
  return { start, end: safeEnd }
}

function SourceContent({ source }: { source: EvidenceRef }) {
  const content = source.content ?? source.excerpt ?? ''
  const firstHighlightRef = useRef<HTMLDivElement | null>(null)
  const lines = content ? content.split(/\r?\n/) : []
  const range = locatorRange(source, lines)
  const status = source.validation_status
  const semanticStatus = source.semantic_status
  const privateSource = isPersonalFinancialSource(source) || hasPrivateFinancialData(source) || hasPrivateFinancialText(content)
  useEffect(() => { if (range) firstHighlightRef.current?.scrollIntoView({ block: 'center' }) }, [range?.start, range?.end, source.id, source.content])
  if (!content) return <section className="source-content"><span className="eyebrow">RETAINED CONTENT</span><p>The backend did not return retained content for this source.</p></section>
  const contentPanel = <>{status && <p className="source-match-note">Claim source check: {status.replaceAll('_', ' ')}{source.validation_reason ? ` · ${source.validation_reason}` : ''}</p>}{semanticStatus && <p className={`source-match-note source-semantic-${semanticStatus}`}>Semantic binding: {semanticStatus.replaceAll('_', ' ')}{source.text_match === false ? ' · text match failed' : ''}</p>}{source.matched_excerpt && <p className="source-match-note">Matched excerpt: “{source.matched_excerpt}”</p>}{source.binding_checks?.length ? <div className="source-binding-checks">{source.binding_checks.map((check, index) => <span key={`${check.key ?? 'check'}-${index}`} className={`source-binding-check source-binding-${check.status ?? 'unavailable'}`}>{check.key ?? 'binding'} · {check.status ?? 'unavailable'}</span>)}</div> : null}<div className="source-line-viewer">{lines.map((line, index) => { const lineNumber = index + 1; const highlighted = Boolean(range && lineNumber >= range.start && lineNumber <= range.end); return <div className={`source-line${highlighted ? ' is-highlighted' : ''}`} ref={range && lineNumber === range.start ? firstHighlightRef : undefined} key={lineNumber}><span className="source-line-number">{lineNumber}</span><span>{line || ' '}</span></div> })}</div></>
  return <section className="source-content"><span className="eyebrow">RETAINED CONTENT {range ? `· L${range.start}${range.end !== range.start ? `–L${range.end}` : ''} HIGHLIGHTED` : ''}</span>{privateSource ? <PrivateFinancialDisclosure label="Reveal retained source locally" description="This source may contain personal account information. It stays closed until you choose to inspect it on this device.">{contentPanel}</PrivateFinancialDisclosure> : contentPanel}</section>
}
function sourcePage(source: EvidenceRef) {
  const metadata = source.document ?? {}
  const value = metadata.page ?? metadata.page_number ?? null
  const match = String(source.selected_locator ?? source.locator ?? '').match(/(?:page|p\.?|pdf page)\s*(\d+)/i)
  const parsed = match ? Number(match[1]) : typeof value === 'number' ? value : null
  return parsed && Number.isFinite(parsed) && parsed > 0 ? parsed : null
}

export function SourceViewer({ source, namespace = 'real', onClose }: { source: EvidenceRef; namespace?: Namespace; onClose: () => void }) {
  const id = source.id ?? source.source_ref
  const document = source.document ?? {}
  const isPdf = String(source.original_mime_type ?? document.mime_type ?? '').toLowerCase() === 'application/pdf' || String(source.source_type ?? '').toLowerCase().includes('pdf')
  const page = sourcePage(source)
  const pdfUrl = id && isPdf ? `${sourceDocumentUrl(id, namespace)}${page ? `#page=${page}` : ''}` : null
  const privateSource = isPersonalFinancialSource(source) || hasPrivateFinancialData(source) || hasPrivateFinancialText(source.content ?? source.excerpt)
  const documentWarnings = Array.isArray(document.warnings) ? document.warnings.map(String).join(' · ') : meaningfulText(document.warnings)
  const pdfPanel = pdfUrl ? <details className="source-pdf-details" open><summary>Open retained PDF{page ? ` at page ${page}` : ''}</summary><iframe title={`Retained PDF ${sourceTitle(source)}`} src={pdfUrl} className="source-pdf-frame" /></details> : null
  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}><article className="source-viewer" role="dialog" aria-modal="true" aria-labelledby="source-viewer-title"><header><div><span className="eyebrow">SOURCE RECORD · {source.source_type ?? 'UNSPECIFIED'}</span><h2 id="source-viewer-title">{sourceTitle(source)}</h2><p>{source.selected_locator ?? source.locator ?? 'Locator unavailable'} · {source.retrieved_at ? `Retrieved ${displayDate(source.retrieved_at)}` : 'Retrieval date unknown'}</p></div><button type="button" className="icon-button" onClick={onClose} aria-label="Close source viewer"><Icon name="close" /></button></header><div className="source-viewer-scroll"><dl className="detail-list source-details"><dt>Publication</dt><dd>{displayDate(source.published_at)}</dd><dt>Observation</dt><dd>{displayDate(source.observation_time)}</dd><dt>Freshness</dt><dd>{meaningfulText(source.freshness) ?? 'Unknown'}{source.freshness_policy ? ` · ${source.freshness_policy}` : ''}</dd><dt>Semantic binding</dt><dd>{meaningfulText(source.semantic_status ?? source.validation_status) ?? 'Unavailable'}{source.text_match === false ? ' · text match failed' : ''}</dd><dt>Version</dt><dd>{source.version ?? meaningfulText(source.source_version) ?? 'Unknown'}</dd><dt>Extraction</dt><dd>{meaningfulText(source.extraction_status ?? source.extraction_version ?? document.extraction_version) ?? 'Unknown'}{source.extraction_failure_reason ? ` · ${source.extraction_failure_reason}` : ''}</dd><dt>Pages</dt><dd>{source.page_count ?? meaningfulText(document.page_count) ?? 'Unknown'}{page ? ` · locator page ${page}` : ''}</dd><dt>Content hash</dt><dd>{source.content_hash ?? source.original_hash ?? meaningfulText(document.original_hash) ?? 'Unknown'}</dd><dt>URL</dt><dd>{source.url ? <a href={source.url} target="_blank" rel="noreferrer">{source.url} <Icon name="external" size={13} /></a> : 'No URL — imported content'}</dd><dt>Source id</dt><dd>{id ?? 'Unknown'}</dd>{source.supersedes_id && <><dt>Supersedes</dt><dd>{source.supersedes_id}</dd></>}</dl>{documentWarnings && <p className="source-extraction-warning">Extraction note: {documentWarnings}</p>}{pdfPanel && (privateSource ? <PrivateFinancialDisclosure label="Reveal retained PDF locally" description="This document may contain personal account information. It stays closed until you choose to inspect it on this device.">{pdfPanel}</PrivateFinancialDisclosure> : pdfPanel)}<SourceContent source={source} /></div></article></div>
}
