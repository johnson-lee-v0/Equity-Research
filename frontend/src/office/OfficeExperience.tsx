import { useEffect, useMemo, useRef } from 'react'
import type { OutputRecord, AgentRecord, Namespace, RunDetail } from '../types'
import { Icon } from '../components/Icon'
import { StatusBadge } from '../components/StatusBadge'
import { OFFICE_ROLE_DETAILS, OfficeFallback, OfficeScene } from './OfficeScene'
import { isWebGLAvailable } from './webgl'
import './office.css'

export type OfficeConnectionState = 'connecting' | 'connected' | 'stale'

export interface OfficeWorkspaceProps {
  agents: AgentRecord[]
  selectedId: string
  selectedAgent?: AgentRecord
  namespace: Namespace
  /** Firm-level pause is durable state and stays visible on the office page. */
  firmPaused?: boolean
  connection: OfficeConnectionState
  connectionMessage: string
  lastError: string
  sceneMode: '3d' | '2d'
  setSceneMode: (mode: '3d' | '2d') => void
  webgl: boolean
  resetToken: number
  zoomDelta: number
  reducedMotion: boolean
  onReset: () => void
  onZoom: (delta: number) => void
  onSelect: (id: string) => void
  onControl: (action: 'pause' | 'resume' | 'cancel' | 'retry', scope: 'firm' | 'run' | 'task', id: string | null) => void
  onNavigate: (view: 'office' | 'research' | 'inbox' | 'tasks' | 'portfolio' | 'memory' | 'simulation' | 'coverage' | 'decisions' | 'settings') => void
  /** Optional run context supplied by the question journal. */
  selectedRun?: RunDetail | null
  onSelectRun?: (runId: string) => void
  onOpenOutput?: (output: OutputRecord) => Promise<void> | void
  onStartSimulation?: () => void
}

const WAITING_STATES = new Set(['waiting_for_evidence', 'waiting_for_review', 'awaiting_input'])
const ROLE_ALIASES: Record<string, string> = {
  chief: 'A00', 'chief of staff': 'A00', reception: 'A00',
  universe: 'A01', 'universe manager': 'A01',
  filings: 'A02', 'filings analyst': 'A02', 'filing reviewer': 'A02',
  fundamental: 'A03', 'fundamental analyst': 'A03',
  technical: 'A04', 'technical analyst': 'A04',
  entry: 'A05', 'entry analyst': 'A05',
  holdings: 'A06', 'holdings monitor': 'A06', 'holdings analyst': 'A06',
  simulation: 'A07', 'simulation analyst': 'A07',
  ownership: 'A08', 'ownership analyst': 'A08', 'ownership & public filings': 'A08',
  macro: 'A09', 'macro analyst': 'A09',
  pm: 'A10', 'portfolio manager': 'A10',
  cio: 'A11', 'chief investment officer': 'A11',
}

function unique<T>(values: T[]) {
  return Array.from(new Set(values))
}

function roleId(value: string) {
  const normalized = value.trim().toLowerCase()
  if (/^a\d{2}$/.test(normalized)) return normalized.toUpperCase()
  return ROLE_ALIASES[normalized] ?? Object.entries(OFFICE_ROLE_DETAILS).find(([, detail]) => detail.name.toLowerCase() === normalized || detail.title.toLowerCase() === normalized)?.[0]
}

function roleIds(values?: string[]) {
  return unique((values ?? []).map(roleId).filter((value): value is string => Boolean(value)))
}

function numberValue(value: unknown) {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

type OfficeRunContext = {
  selected: string[]
  omitted: string[]
  simulationOnly: string[]
  waiting: Array<{ name: string; reason: string | null }>
  waitingCount: number | null
  blocker: string | null
  routingPending: boolean
}

function recordValue(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null
}

function roleEntry(value: unknown) {
  const item = recordValue(value)
  if (!item) return null
  const id = roleId(String(item.agent_id ?? item.role_id ?? item.id ?? item.code ?? item.role ?? item.name ?? ''))
  if (!id) return null
  const selected = typeof item.selected === 'boolean' ? item.selected : typeof item.participating === 'boolean' ? item.participating : undefined
  const selectionState = item.selection_state ?? item.selectionState ?? item.state
  const status = item.status ?? item.state ?? item.task_status ?? item.execution_status
  const reason = item.waiting_reason ?? item.blocking_reason ?? item.reason ?? item.message ?? null
  return { id, selected, selectionState: selectionState == null ? null : String(selectionState).toLowerCase(), status: status == null ? null : String(status), reason: reason == null ? null : String(reason) }
}

function roleIdsFrom(value: unknown) {
  if (!Array.isArray(value)) return []
  return value.map((item) => typeof item === 'string' || typeof item === 'number' ? roleId(String(item)) : roleEntry(item)?.id).filter((id): id is string => Boolean(id))
}

function runContext(run: RunDetail | null | undefined): OfficeRunContext {
  if (!run) return { selected: [], omitted: [], simulationOnly: [], waiting: [], waitingCount: null, blocker: null, routingPending: false }
  const extra = run as unknown as Record<string, unknown>
  const explanation = recordValue(extra.explanation)
  const participation = recordValue(extra.participation)
  const rolesRecord = recordValue(extra.roles)
  const rawRoles = Array.isArray(extra.roles)
    ? extra.roles
    : rolesRecord
      ? Object.entries(rolesRecord).map(([id, value]) => ({ ...(recordValue(value) ?? {}), agent_id: id, selected: typeof value === 'boolean' ? value : recordValue(value)?.selected }))
      : []
  const roleRows = [
    ...rawRoles,
    ...(Array.isArray(extra.agent_selection) ? extra.agent_selection : []),
  ].map(roleEntry).filter((value): value is NonNullable<ReturnType<typeof roleEntry>> => Boolean(value))
  const taskRows = (run.tasks ?? []).map((task) => ({
    id: roleId(String(task.agent_id ?? '')),
    status: task.status == null ? null : String(task.status),
    reason: task.blocking_reason ?? task.progress_message ?? task.current_task ?? null,
  })).filter((value): value is { id: string; status: string | null; reason: string | null } => Boolean(value.id))

  const selected = unique([
    ...roleIds(run.selected_agent_ids),
    ...roleIds(run.selected_roles),
    ...taskRows.map((row) => row.id),
    ...roleRows.filter((row) => row.selected === true).map((row) => row.id),
    ...roleIdsFrom(participation?.selected ?? participation?.participating ?? participation?.participants),
  ])
  const runState = String(run.status ?? run.execution_status ?? '').toLowerCase()
  const routingState = String(extra.routing_status ?? extra.routing_state ?? explanation?.routing_status ?? explanation?.routing_state ?? '').toLowerCase()
  const routingPending = routingState === 'pending' || routingState === 'routing_pending' || runState === 'routing_pending' || runState === 'pending_routing'
  const simulationOnly = unique([
    ...roleRows.filter((row) => row.selectionState === 'simulation_only' || row.selectionState === 'simulation-only' || /simulation[\s-]*only/i.test(row.reason ?? '')).map((row) => row.id),
    ...roleIdsFrom(extra.simulation_only_agent_ids),
    ...roleIdsFrom(extra.simulation_only_roles),
    ...roleIdsFrom(participation?.simulation_only ?? participation?.simulationOnly),
  ])

  // Only explicit role rows/fields make a role unassigned. Before routing is
  // complete, a missing selected list means unknown rather than omitted.
  const explicitOmitted = roleRows.filter((row) => row.selected === false && !simulationOnly.includes(row.id) && !/routing\s+(is\s+)?still\s+pending|selection\s+is\s+not\s+yet\s+known|pending/i.test(row.reason ?? '')).map((row) => row.id)
  const omitted = unique([
    ...roleIds(run.skipped_agent_ids),
    ...roleIds(run.skipped_roles),
    ...explicitOmitted,
    ...roleIdsFrom(participation?.omitted ?? participation?.unassigned ?? participation?.not_assigned),
  ]).filter((id) => !selected.includes(id) && !simulationOnly.includes(id))

  const waitingRows = [
    ...taskRows.filter((row) => row.status && WAITING_STATES.has(row.status)).map((row) => ({ id: row.id, reason: row.reason })),
    ...roleRows.filter((row) => row.status && WAITING_STATES.has(row.status)).map((row) => ({ id: row.id, reason: row.reason })),
  ]
  const participationWaiting = participation?.waiting
  if (Array.isArray(participationWaiting)) {
    waitingRows.push(...participationWaiting.map((value) => {
      const row = roleEntry(value)
      return row ? { id: row.id, reason: row.reason } : null
    }).filter((value): value is { id: string; reason: string | null } => Boolean(value)))
  }
  const waiting = unique(waitingRows.map((row) => `${row.id}\u0000${row.reason ?? ''}`)).map((entry) => {
    const [id, reason] = entry.split('\u0000')
    return { name: OFFICE_ROLE_DETAILS[id]?.name ?? id, reason: reason || null }
  })
  const counts = run.counts ?? run.task_counts
  const waitingCount = numberValue(counts?.waiting) ?? numberValue(counts?.waiting_for_evidence) ?? numberValue(counts?.waiting_for_review) ?? (waiting.length || null)
  const blocker = run.current_blocker ?? run.blocking_reason ?? null
  return { selected, omitted, simulationOnly, waiting, waitingCount, blocker, routingPending }
}

function roleNames(ids: string[]) {
  return ids.map((id) => OFFICE_ROLE_DETAILS[id]?.name ?? id)
}

function QuestionContextBanner({ run, onSelectRun, onOpenOutput, onNavigate }: { run: RunDetail; onSelectRun?: (runId: string) => void; onOpenOutput?: (output: OutputRecord) => Promise<void> | void; onNavigate: OfficeWorkspaceProps['onNavigate'] }) {
  const context = runContext(run)
  const question = run.question?.trim() || run.original_question?.trim() || 'Question text unavailable.'
  const latestOutput = run.latest_output
  const openFlow = () => {
    if (onSelectRun) onSelectRun(run.id)
    // Selecting the run and changing views are separate parent actions. Keep
    // the button useful when the callback only updates the selected record.
    onNavigate('tasks')
  }
  return <section className="office-question-banner" aria-labelledby="office-question-title">
    <div className="office-question-mark"><Icon name="compass" size={18} /></div>
    <div className="office-question-copy">
      <span className="eyebrow">QUESTION IN FOCUS</span>
      <h2 id="office-question-title">{question}</h2>
      <div className="office-question-meta">
        <span>{context.selected.length ? `${context.selected.length} desks assigned for this question` : context.routingPending ? 'Question routing is still pending' : 'This question’s team is not available yet'}</span>
        {context.waitingCount != null && context.waitingCount > 0 && <span>{context.waitingCount} waiting</span>}
        {context.blocker && <span className="office-question-blocker">{context.blocker}</span>}
      </div>
    </div>
    <div className="office-question-actions">
      <button type="button" className="button button-primary" onClick={openFlow}><Icon name="arrow" size={14} /> View question flow</button>
      {latestOutput && onOpenOutput && <button type="button" className="button button-subtle" onClick={() => void onOpenOutput(latestOutput)}><Icon name="book" size={14} /> Open latest output</button>}
    </div>
  </section>
}

function TeamContextCard({ run }: { run: RunDetail }) {
  const context = runContext(run)
  const selectedNames = roleNames(context.selected)
  const omittedNames = roleNames(context.omitted)
  const simulationOnlyNames = roleNames(context.simulationOnly)
  return <section className="office-insight-card office-team-context">
    <div className="section-heading"><div><span className="eyebrow">THIS QUESTION’S TEAM</span><h2>Participating team</h2></div><span className="office-context-state">{context.routingPending ? 'Routing pending' : context.selected.length ? 'Assigned' : 'Unknown'}</span></div>
    {selectedNames.length ? <div className="office-role-chips">{context.selected.map((id, index) => <span key={id} className="office-role-chip"><strong>{selectedNames[index]}</strong><small>{id}</small></span>)}</div> : <p className="office-context-note">This question’s assigned desks are not available yet.</p>}
    {omittedNames.length > 0 && <p className="office-context-note"><strong>Not assigned for this question:</strong> {omittedNames.join(', ')}.</p>}
    {simulationOnlyNames.length > 0 && <p className="office-context-note"><strong>Simulation-only desk:</strong> {simulationOnlyNames.join(', ')}.</p>}
    {context.waitingCount != null && context.waitingCount > 0 && <p className="office-context-note"><strong>Waiting:</strong> {context.waiting.length ? context.waiting.map((item) => `${item.name}${item.reason ? ` · ${item.reason}` : ' · Saved wait reason unavailable'}`).join('; ') : `${context.waitingCount} desk${context.waitingCount === 1 ? '' : 's'} waiting · Saved wait reason unavailable`}</p>}
    {context.blocker && <p className="office-context-note office-context-blocker"><strong>Saved blocker:</strong> {context.blocker}</p>}
    {!context.omitted.length && !context.simulationOnly.length && !context.waitingCount && !context.selected.length && !context.routingPending && <p className="office-context-note">Desk assignment and wait details are currently unknown.</p>}
  </section>
}

function PulseItem({ value, label, tone }: { value: number; label: string; tone: string }) {
  return <div className={`pulse-item pulse-${tone}`}><strong>{value}</strong><span>{label}</span></div>
}

export function OfficeWorkspace({ agents, selectedId, selectedAgent, namespace: _namespace, firmPaused = false, connection, connectionMessage, lastError, sceneMode, setSceneMode, webgl, resetToken, zoomDelta, reducedMotion, onReset, onZoom, onSelect, onControl: _onControl, onNavigate, selectedRun, onSelectRun, onOpenOutput, onStartSimulation }: OfficeWorkspaceProps) {
  const compactDefaulted = useRef(false)
  // The desk list is the practical starting surface at every width. Users can
  // opt into the procedural 3D floor with the view switch.
  useEffect(() => {
    if (compactDefaulted.current || !webgl) return
    compactDefaulted.current = true
    if (sceneMode === '3d') setSceneMode('2d')
  }, [sceneMode, setSceneMode, webgl])

  const statusCounts = useMemo(() => agents.reduce<Record<string, number>>((result, agent) => {
    const status = String(agent.status ?? 'idle')
    result[status] = (result[status] ?? 0) + 1
    return result
  }, {}), [agents])
  const isThreeD = sceneMode === '3d' && webgl
  const startSimulation = onStartSimulation ?? (() => onNavigate('simulation'))

    return <div className="office-page office-experience">
    <header className="office-heading">
      <div><span className="eyebrow">RESEARCH FIRM / OFFICE FLOOR</span><h1>Office floor</h1><p>Select a desk or office to inspect saved work.</p></div>
      <div className="office-heading-actions"><button type="button" className="button button-subtle" onClick={() => onNavigate('tasks')}><Icon name="briefcase" size={15} /> {statusCounts.running ?? 0} running</button></div>
    </header>

    {firmPaused && <div className="office-paused-banner" role="status"><Icon name="pause" size={16} /><div><strong>Firm paused</strong><span>Queued and active records remain visible. Resume from controls when you are ready.</span></div></div>}
    {selectedRun && <QuestionContextBanner run={selectedRun} onSelectRun={onSelectRun} onOpenOutput={onOpenOutput} onNavigate={onNavigate} />}
    {(connection !== 'connected' || lastError) && <div className={`connection-banner banner-${connection}`}><span className="banner-icon"><Icon name="warning" size={17} /></span><div><strong>Local connection needs attention</strong><p>{connectionMessage}{lastError ? ` · ${lastError}` : ''}</p></div><button type="button" className="button button-subtle" onClick={() => window.location.reload()}><Icon name="refresh" size={14} /> Reconnect</button></div>}

    <section className="office-card">
      <div className="office-toolbar"><div className="toolbar-title"><span className="eyebrow">INTERACTIVE FLOOR PLAN</span><strong>{isThreeD ? 'Isometric office' : 'Readable desk list'}</strong><span>{isThreeD ? 'Detailed desk view · select a room to inspect saved work' : '3 decision roles + code checks · select a role to inspect saved work'}</span></div><div className="office-tools"><div className="view-switch" role="group" aria-label="Office view"><button type="button" className={isThreeD ? 'is-active' : ''} onClick={() => setSceneMode('3d')} disabled={!webgl}><Icon name="grid" size={14} />3D scene</button><button type="button" className={!isThreeD ? 'is-active' : ''} onClick={() => setSceneMode('2d')}><Icon name="users" size={14} />Desk list</button></div>{selectedAgent?.id === 'A07' && <button type="button" className="button button-primary office-start-simulation" onClick={startSimulation}><Icon name="play" size={14} /> Start simulation</button>}{isThreeD && <><button type="button" className="icon-button" onClick={onReset} aria-label="Reset camera" title="Reset camera"><Icon name="compass" size={16} /></button><button type="button" className="icon-button" onClick={() => onZoom(1)} aria-label="Zoom in" title="Zoom in">+</button><button type="button" className="icon-button" onClick={() => onZoom(-1)} aria-label="Zoom out" title="Zoom out">−</button></>}</div></div>
      <div className="scene-wrap" aria-label={isThreeD ? 'Interactive 3D office floor' : 'Selectable office desk list'}>{isThreeD ? <OfficeScene agents={agents} selectedId={selectedId} onSelect={onSelect} resetToken={resetToken} zoomDelta={zoomDelta} reducedMotion={reducedMotion} /> : <OfficeFallback agents={agents} selectedId={selectedId} onSelect={onSelect} listMode={sceneMode === '2d'} selectedRunId={selectedRun?.id ?? null} selectedRun={selectedRun} onOpenOutput={onOpenOutput} onStartSimulation={startSimulation} />}{isThreeD && <div className="scene-legend"><span><i className="legend-dot legend-active" />Active</span><span><i className="legend-dot legend-waiting" />Waiting</span><span><i className="legend-dot legend-blocked" />Blocked</span><span><i className="legend-dot legend-idle" />Idle</span></div>}</div>
    </section>

    <div className="office-bottom-grid"><section className="office-insight-card"><div className="section-heading"><div><span className="eyebrow">FIRM PULSE</span><h2>Observable state</h2></div><StatusBadge status={statusCounts.running ? 'running' : 'idle'} /></div><div className="pulse-grid"><PulseItem value={statusCounts.running ?? 0} label="Running" tone="green" /><PulseItem value={statusCounts.queued ?? 0} label="Queued" tone="amber" /><PulseItem value={(statusCounts.waiting_for_evidence ?? 0) + (statusCounts.waiting_for_review ?? 0) + (statusCounts.awaiting_input ?? 0)} label="Waiting" tone="blue" /><PulseItem value={(statusCounts.blocked ?? 0) + (statusCounts.failed ?? 0)} label="Needs attention" tone="red" /></div><p className="card-footnote">Counts come from saved desk state. Running work has no invented percentage.</p></section><section className="office-insight-card office-guide"><div className="section-heading"><div><span className="eyebrow">HOW TO ENTER</span><h2>Follow a question</h2></div></div><div className="guide-row"><span className="guide-number">01</span><p>Ask the firm from the command bar. A00 routes the question to the roles named for it.</p></div><div className="guide-row"><span className="guide-number">02</span><p>Select a desk, occupant or executive nameplate to inspect the same record.</p></div><div className="guide-row"><span className="guide-number">03</span><p>Open outputs and sources in readable panels. Simulation findings stay labeled.</p></div></section>{selectedRun && <TeamContextCard run={selectedRun} />}</div>
  </div>
}

export { isWebGLAvailable }
