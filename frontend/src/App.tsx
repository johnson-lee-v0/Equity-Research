import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react'
import {
  apiFetch,
  createRun,
  dispatchRedditPost,
  eventFromSse,
  explainRun,
  getAgent,
  getCoverage,
  getDecisions,
  getLearning,
  getModelPolicy,
  getMonitoring,
  getOffice,
  getOutput,
  getPortfolio,
  getPortfolioPolicy,
  getProviders,
  getRedditConnection,
  getRedditInbox,
  getRun,
  getSimulation,
  getSimulations,
  getSource,
  getSourceVersions,
  getSources,
  getWatchlist,
  normalizeCoverage,
  normalizeRedditConnection,
  normalizeRedditInbox,
  normalizeRun,
  recordLifecycle,
  researchMissingEvidence,
  reuseRedditPost,
  savePortfolioPolicy,
  saveRedditConnection,
  searchMemory,
} from './api'
import { Icon } from './components/Icon'
import MarketNews from './components/MarketNews'
import { navigationFor } from './navigationModel'
import { createRunDetailRequest, type RunDetailLoadState } from './runDetailRequest'
import { modelEfforts } from './modelOptions'
import { WorkspaceBoundary } from './components/WorkspaceBoundary'
import { Inspector, OutputViewer, SourceViewer, type InspectorTab } from './panels/Inspector'
import type { WorkspaceViewName } from './panels/WorkspaceViews'
import { DecisionWorkspace, type DecisionWorkspaceMode } from './panels/DecisionWorkspace'
import { isWebGLAvailable } from './office/webgl'
import {
  type AgentRecord,
  type CoverageItem,
  type DecisionRecord,
  type EvidenceRef,
  type LearningSnapshot,
  type MemoryRecord,
  type ModelConfig,
  type MonitoringRule,
  type Namespace,
  type OfficeSnapshot,
  type OutputRecord,
  type PortfolioSnapshot,
  type ProviderState,
  type RedditConnectionState,
  type RedditInboxQuery,
  type RedditInboxSnapshot,
  type RunDetail,
  type RunSummary,
  type SimulationRun,
  type TaskEvent,
  type WatchlistItem,
} from './types'
import './styles.css'
import './workspace.css'

const ExperienceWorkspace = lazy(() =>
  import('./office/OfficeExperience').then((module) => ({ default: module.OfficeWorkspace })),
)
const WorkspaceViews = lazy(() =>
  import('./panels/WorkspaceViews').then((module) => ({ default: module.WorkspaceViews })),
)
const Documents = lazy(() => import('./panels/research/Documents'))
const Congress = lazy(() => import('./panels/research/Congress'))
const Strategies = lazy(() => import('./panels/research/Strategies'))
const ResearchLibrary = lazy(() => import('./panels/ResearchLibrary'))
const Memory = lazy(() => import('./panels/Memory'))

type ConnectionState = 'connecting' | 'connected' | 'stale'

type ResearchTab = 'questions' | 'coverage' | 'simulation'

type PrimaryView =
  | DecisionWorkspaceMode
  | 'research'
  | 'office'
  | 'settings'
  | 'documents'
  | 'congress'
  | 'strategies'
  | 'library'
  | 'memory'
  | 'portfolio'
  | 'evidence'
  | 'scenarios'

const NAV_ITEMS: Array<{ id: PrimaryView; label: string; icon: string; description: string }> = [
  { id: 'research', label: 'Research', icon: 'book', description: 'Ideas, earnings, questions, pricing and decisions' },
  { id: 'watchlist', label: 'Watchlist', icon: 'clock', description: 'Entry prices and events to watch' },
  { id: 'portfolio', label: 'Portfolio', icon: 'briefcase', description: 'Positions and portfolio context' },
  { id: 'congress', label: 'Congress', icon: 'users', description: 'Disclosures and research ideas' },
  { id: 'strategies', label: 'Strategy testing', icon: 'beaker', description: 'Backtests and comparisons' },
  { id: 'memory', label: 'Memory', icon: 'database', description: 'Shared research notes and their connections' },
]

function createId(prefix: string) {
  try {
    return `${prefix}-${crypto.randomUUID()}`
  } catch {
    return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2)}`
  }
}

function redditPostUrl(value: string) {
  try {
    const url = new URL(value.trim())
    return /(^|\.)reddit\.com$/.test(url.hostname) && /\/r\/[^/]+\/comments\//i.test(url.pathname)
  } catch {
    return false
  }
}

function displayTime(value?: string | null) {
  if (!value) return 'No timestamp'
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat(undefined, {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
      }).format(date)
}

function runSummaryStatus(run: RunSummary) {
  return String(run.execution_status ?? run.execution_state ?? run.status ?? '')
    .toLowerCase()
    .replaceAll('-', '_')
    .replaceAll(' ', '_')
}

function redditRun(run: RunSummary, allRuns: RunSummary[] = []) {
  const origin = String(run.origin ?? '').toLowerCase()
  if (origin === 'reddit') return true
  if (origin !== 'repair') return false
  const root = run.root_run_id
    ? allRuns.find((candidate) => candidate.id === run.root_run_id)
    : null
  if (root) return redditRun(root, allRuns)
  return String(run.origin_ref ?? '')
    .toLowerCase()
    .includes('reddit')
}

function redditPostStatus(post: RedditInboxSnapshot['posts'][number]) {
  return String(post.status ?? post.state ?? post.dispatch_state ?? 'retained')
    .toLowerCase()
    .replaceAll('-', '_')
    .replaceAll(' ', '_')
}

function redditPostMatchesQuery(
  post: RedditInboxSnapshot['posts'][number],
  status?: string | null,
) {
  if (!status || status === 'all') return true
  const state = redditPostStatus(post)
  if (status === 'attention')
    return (
      ['attention', 'blocked', 'failed', 'error'].includes(state) ||
      Boolean(post.error || post.dispatch_error)
    )
  if (status === 'processed')
    return ['processed', 'reused', 'researched', 'completed'].includes(state)
  if (status === 'processing') return ['processing', 'screening', 'dispatched'].includes(state)
  if (status === 'queued') return ['queued', 'retained', 'backlog'].includes(state)
  if (status === 'dismissed') return ['dismissed', 'skipped'].includes(state)
  return state === status
}

function globalWorkRuns(runs: RunSummary[]) {
  const terminalStatuses = new Set([
    'completed',
    'cancelled',
    'failed',
    'blocked',
    'interrupted',
    'terminal',
    'paused',
  ])
  return runs.filter((run) => !terminalStatuses.has(runSummaryStatus(run)))
}

function persistedTaskCount(run: RunSummary, key: 'running' | 'queued') {
  const value = run.task_counts?.[key] ?? run.counts?.[key]
  return typeof value === 'number' && Number.isFinite(value) ? Math.max(0, value) : 0
}

function hasCioAnswer(detail: RunDetail) {
  const brief =
    detail.decision_brief ??
    detail.cio_brief ??
    detail.latest_output?.decision_brief ??
    detail.latest_output?.cio_brief
  if (!brief) return false
  const hasPrice = (plan: typeof brief.entry_plan) =>
    Boolean(plan && (plan.low || plan.high || plan.value || plan.missing_reason))
  return Boolean(
    brief.stance ||
    brief.reason ||
    brief.ticker ||
    brief.entry_advice ||
    hasPrice(brief.entry_plan) ||
    hasPrice(brief.target_price) ||
    brief.accepted_risks?.length ||
    brief.catalysts?.length ||
    brief.invalidation_conditions?.length ||
    brief.blocking_gaps?.length,
  )
}

function applyEvent(snapshot: OfficeSnapshot, event: TaskEvent): OfficeSnapshot {
  const payload = event.payload ?? {}
  const agentId = payload.agent_id == null ? undefined : String(payload.agent_id)
  if (!agentId)
    return {
      ...snapshot,
      event_cursor: event.sequence_id,
      last_event_cursor: event.sequence_id,
      server_time: event.emitted_at,
      generated_at: event.emitted_at,
    }
  const agent = snapshot.agents.find((candidate) => candidate.id === agentId)
  if (!agent)
    return {
      ...snapshot,
      event_cursor: event.sequence_id,
      last_event_cursor: event.sequence_id,
      server_time: event.emitted_at,
      generated_at: event.emitted_at,
    }
  const current = agent.current_task ?? agent.task
  const status = payload.status == null ? agent.status : String(payload.status)
  const nextTask =
    current || event.task_id
      ? {
          ...(current ?? {}),
          id: current?.id ?? event.task_id,
          task_id: current?.task_id ?? event.task_id,
          run_id: current?.run_id ?? event.run_id ?? null,
          agent_id: agentId,
          status,
          current_task:
            payload.message == null ? (current?.current_task ?? null) : String(payload.message),
          updated_at: event.emitted_at,
          output: current?.output ?? null,
        }
      : null
  const history = [...(agent.history ?? []), event as unknown as Record<string, unknown>].slice(-80)
  const nextAgent: AgentRecord = {
    ...agent,
    status,
    current_task: nextTask,
    task: nextTask,
    history,
    last_update: event.emitted_at,
  }
  return {
    ...snapshot,
    agents: snapshot.agents.map((candidate) => (candidate.id === agentId ? nextAgent : candidate)),
    tasks: snapshot.tasks,
    event_cursor: event.sequence_id,
    last_event_cursor: event.sequence_id,
    server_time: event.emitted_at,
    generated_at: event.emitted_at,
  }
}

function emptyOffice(namespace: Namespace): OfficeSnapshot {
  return {
    namespace,
    mode: namespace,
    agents: [],
    tasks: [],
    stale: true,
    event_cursor: null,
    last_event_cursor: null,
    generated_at: null,
    server_time: null,
    paused: false,
  }
}

function WorkspaceLoadingState() {
  return (
    <section className="workspace-loading-state" role="status" aria-live="polite">
      <Icon name="refresh" size={20} />
      <div>
        <span className="eyebrow">LOCAL WORKSPACE</span>
        <h1>Connecting to local workspace…</h1>
        <p>Loading saved questions and the current case state.</p>
      </div>
    </section>
  )
}

export default function App() {
  const [view, setView] = useState<WorkspaceViewName>('research')
  const [researchSection, setResearchSection] = useState<'cases' | 'earnings' | 'history'>('cases')
  const [researchOrigin, setResearchOrigin] = useState('all')
  const [primaryView, setPrimaryView] = useState<PrimaryView>('research')
  const [navigationOpen, setNavigationOpen] = useState(false)
  const navigationToggle = useRef<HTMLButtonElement>(null)
  const mainContent = useRef<HTMLElement>(null)
  const [researchTab, setResearchTab] = useState<ResearchTab>('questions')
  const [namespace, setNamespace] = useState<Namespace>('real')
  const [office, setOffice] = useState<OfficeSnapshot>(() => emptyOffice('real'))
  const [providers, setProviders] = useState<ProviderState | null>(null)
  const [policy, setPolicy] = useState<any>(null)
  const [portfolio, setPortfolio] = useState<PortfolioSnapshot | null>(null)
  const [portfolioPolicy, setPortfolioPolicy] = useState<Record<string, unknown> | null>(null)
  const [sources, setSources] = useState<EvidenceRef[]>([])
  const [memory, setMemory] = useState<MemoryRecord[]>([])
  const [coverage, setCoverage] = useState<CoverageItem[]>([])
  const [decisions, setDecisions] = useState<DecisionRecord[]>([])
  const [runs, setRuns] = useState<RunSummary[]>([])
  const [watchlistItems, setWatchlistItems] = useState<WatchlistItem[]>([])
  const [watchlistPaused, setWatchlistPaused] = useState(false)
  const [watchlistEnabled, setWatchlistEnabled] = useState<boolean | null>(null)
  const [learning, setLearning] = useState<LearningSnapshot | null>(null)
  const [selectedRun, setSelectedRun] = useState<RunDetail | null>(null)
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)
  const [runDetailLoad, setRunDetailLoad] = useState<RunDetailLoadState>(null)
  const [simulations, setSimulations] = useState<SimulationRun[]>([])
  const [monitoring, setMonitoring] = useState<MonitoringRule[]>([])
  const [redditInbox, setRedditInbox] = useState<RedditInboxSnapshot>(() =>
    normalizeRedditInbox(null),
  )
  const [redditConnection, setRedditConnection] = useState<RedditConnectionState>(() =>
    normalizeRedditConnection(null),
  )
  const [simulationFocusId, setSimulationFocusId] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState('A00')
  const [selectedTab, setSelectedTab] = useState<InspectorTab>('overview')
  const [connection, setConnection] = useState<ConnectionState>('connecting')
  const [connectionMessage, setConnectionMessage] = useState('Connecting to local workspace…')
  const [workspaceHydrating, setWorkspaceHydrating] = useState(true)
  const [lastError, setLastError] = useState('')
  const [resetToken, setResetToken] = useState(0)
  const [zoomDelta, setZoomDelta] = useState(0)
  const [sceneMode, setSceneMode] = useState<'3d' | '2d'>('3d')
  const [reducedMotion, setReducedMotion] = useState(() =>
    typeof window !== 'undefined'
      ? (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false)
      : false,
  )
  const [webgl, setWebgl] = useState(true)
  const [outputViewer, setOutputViewer] = useState<OutputRecord | null>(null)
  const [sourceViewer, setSourceViewer] = useState<EvidenceRef | null>(null)
  const [toast, setToast] = useState('')
  const [snapshotVersion, setSnapshotVersion] = useState(0)
  const eventSourceRef = useRef<EventSource | null>(null)
  const eventRefreshTimerRef = useRef<number | undefined>(undefined)
  const eventCursorRef = useRef<number | null>(null)
  const refreshTimer = useRef<number | undefined>(undefined)
  const toastTimer = useRef<number | undefined>(undefined)
  const firstLoadRef = useRef(true)
  const loadRequestRef = useRef(0)
  const runListRequestRef = useRef(0)
  const runDetailRequestRef = useRef(0)
  const runDetailRequest = useRef(createRunDetailRequest<RunDetail | null>())
  const redditInboxRequestRef = useRef(0)
  const redditQueryRef = useRef<RedditInboxQuery>({ status: 'all', limit: 100, offset: 0 })
  const redditLoadedCountRef = useRef(100)
  const workspaceRequestRef = useRef(0)
  const serverTimeRef = useRef<string | null>(null)
  const selectedRunIdRef = useRef<string | null>(null)
  const explicitSelectedRunIdRef = useRef<string | null>(null)
  const selectedRunRef = useRef<RunDetail | null>(null)
  const runsRef = useRef<RunSummary[]>([])
  const namespaceRef = useRef<Namespace>(namespace)

  const agents = office.agents
  const selectedAgent = selectedId ? agents.find((agent) => agent.id === selectedId) : undefined

  useEffect(() => {
    serverTimeRef.current = office.server_time ?? office.generated_at ?? null
  }, [office.server_time, office.generated_at])
  useEffect(() => {
    selectedRunIdRef.current = selectedRunId
  }, [selectedRunId])
  useEffect(() => {
    selectedRunRef.current = selectedRun
  }, [selectedRun])
  useEffect(() => {
    runsRef.current = runs
  }, [runs])
  useEffect(() => {
    namespaceRef.current = namespace
    runDetailRequest.current.cancel()
    setRunDetailLoad(null)
  }, [namespace])

  useEffect(() => () => runDetailRequest.current.cancel(), [])

  const notify = useCallback((message: string) => {
    setToast(message)
    if (toastTimer.current) window.clearTimeout(toastTimer.current)
    toastTimer.current = window.setTimeout(() => setToast(''), 4800)
  }, [])

  const loadOffice = useCallback(
    async (nextNamespace: Namespace, options?: { quiet?: boolean; runId?: string | null }) => {
      if (nextNamespace === 'simulation') return
      const requestId = ++loadRequestRef.current
      try {
        const snapshot = await getOffice(nextNamespace, options?.runId)
        if (requestId !== loadRequestRef.current || namespaceRef.current !== nextNamespace)
          return null
        setOffice((current) => ({
          ...snapshot,
          agents: snapshot.agents.map((agent) => {
            const previous = current.agents.find((candidate) => candidate.id === agent.id)
            // /api/office intentionally returns a compact roster. Preserve the
            // selected agent's already fetched detail until /api/agents refreshes it.
            return previous && !options?.runId
              ? {
                  ...previous,
                  ...agent,
                  queue: previous.queue,
                  outputs: previous.outputs,
                  evidence: previous.evidence,
                  history: previous.history,
                  current_simulation: agent.current_simulation ?? previous.current_simulation,
                  related_simulations: agent.related_simulations ?? previous.related_simulations,
                }
              : agent
          }),
        }))
        setSnapshotVersion((value) => value + 1)
        const cursor = Number(snapshot.event_cursor)
        eventCursorRef.current = Number.isFinite(cursor) ? cursor : null
        namespaceRef.current = nextNamespace
        setNamespace(nextNamespace)
        setConnection('connected')
        setConnectionMessage(
          `Connected to local workspace · cursor ${snapshot.event_cursor ?? '—'}`,
        )
        setLastError('')
        if (firstLoadRef.current) {
          try {
            const nextProviders = await getProviders()
            if (requestId === loadRequestRef.current && namespaceRef.current === nextNamespace)
              setProviders(nextProviders)
          } catch {
            /* provider status is independently visible */
          }
          firstLoadRef.current = false
        }
        try {
          const nextSources = await getSources(nextNamespace)
          if (requestId === loadRequestRef.current && namespaceRef.current === nextNamespace)
            setSources(nextSources)
        } catch {
          /* source records remain empty until the local backend responds */
        }
        return snapshot
      } catch (error) {
        if (requestId !== loadRequestRef.current || namespaceRef.current !== nextNamespace)
          return null
        if (!options?.quiet) {
          setOffice(emptyOffice(nextNamespace))
          setSources([])
          setPortfolio(null)
          setPortfolioPolicy(null)
          setMemory([])
          setCoverage([])
          setDecisions([])
          setConnection('stale')
          namespaceRef.current = nextNamespace
          setNamespace(nextNamespace)
          setConnectionMessage('Local backend unavailable. Saved records are not simulated.')
          setLastError(error instanceof Error ? error.message : 'Local backend unavailable.')
        } else {
          setConnection('stale')
          setConnectionMessage('Last local snapshot retained. Backend reconnect pending.')
          setLastError(error instanceof Error ? error.message : 'Local backend unavailable.')
        }
        return null
      }
    },
    [],
  )

  const loadRuns = useCallback(async (nextNamespace: Namespace, options?: { quiet?: boolean }) => {
    if (nextNamespace === 'simulation') return [] as RunSummary[]
    const requestId = ++runListRequestRef.current
    try {
      const value = await apiFetch<unknown>(
        `/api/runs?namespace=${encodeURIComponent(nextNamespace)}&include_intake=true`,
      )
      const rows = Array.isArray(value)
        ? value
        : value && typeof value === 'object' && 'items' in value && Array.isArray(value.items)
          ? value.items
          : []
      const nextRuns = rows.map(normalizeRun)
      if (requestId !== runListRequestRef.current || namespaceRef.current !== nextNamespace)
        return [] as RunSummary[]
      setRuns(nextRuns)
      const currentId = selectedRunIdRef.current
      // A Reddit result can be selected from a status-filtered inbox page
      // without being present in the compact `/api/runs` response. Preserve
      // that explicit selection while the list refreshes; the detail endpoint
      // remains the source of truth for the focused result.
      const keepExplicitSelection = Boolean(
        currentId && explicitSelectedRunIdRef.current === currentId,
      )
      const nextId =
        keepExplicitSelection || (currentId && nextRuns.some((run) => run.id === currentId))
          ? currentId
          : null
      selectedRunIdRef.current = nextId
      setSelectedRunId(nextId)
      if (!nextId && !keepExplicitSelection) setSelectedRun(null)
      return nextRuns
    } catch (error) {
      if (requestId !== runListRequestRef.current || namespaceRef.current !== nextNamespace)
        return [] as RunSummary[]
      if (!options?.quiet) {
        setRuns([])
        setSelectedRun(null)
        setSelectedRunId(null)
        if (nextNamespace === 'real')
          setLastError(error instanceof Error ? error.message : 'Saved questions unavailable.')
      }
      return [] as RunSummary[]
    }
  }, [])

  const loadRunDetail = useCallback(
    (runId: string, requestedNamespace?: Namespace | string | null) => {
      const namespaceForRequest = requestedNamespace ?? namespaceRef.current
      return runDetailRequest.current.load(runId, namespaceForRequest, async (signal) => {
        const requestId = ++runDetailRequestRef.current
        if (selectedRunIdRef.current === runId) setRunDetailLoad({ runId, status: 'loading' })
        try {
          let detail = await getRun(runId, namespaceForRequest, signal)
          // Repair runs contain the live gap work but may not have a new CIO
          // answer yet. Pull the root answer into the selected read model so the
          // question keeps its last saved decision while repair progress remains
          // visible in the repair task list.
          if (detail.root_run_id && detail.root_run_id !== detail.id && !hasCioAnswer(detail)) {
            try {
              const root = await getRun(detail.root_run_id, namespaceForRequest, signal)
              const rootOutput =
                root.latest_output ??
                root.outputs.find((output) => output.decision_brief ?? output.cio_brief) ??
                null
              detail = {
                ...detail,
                decision_brief:
                  detail.decision_brief ??
                  root.decision_brief ??
                  root.cio_brief ??
                  rootOutput?.decision_brief ??
                  rootOutput?.cio_brief ??
                  null,
                cio_brief:
                  detail.cio_brief ??
                  root.cio_brief ??
                  root.decision_brief ??
                  rootOutput?.cio_brief ??
                  rootOutput?.decision_brief ??
                  null,
                latest_output: detail.latest_output ?? rootOutput,
                latest_output_id:
                  detail.latest_output_id ??
                  root.latest_output_id ??
                  rootOutput?.id ??
                  rootOutput?.output_id ??
                  null,
                latest_output_title:
                  detail.latest_output_title ?? root.latest_output_title ?? rootOutput?.title ?? null,
                latest_output_summary:
                  detail.latest_output_summary ??
                  root.latest_output_summary ??
                  rootOutput?.conclusion ??
                  null,
                summary: detail.summary ?? root.summary ?? rootOutput?.conclusion ?? null,
                root_origin: detail.root_origin ?? root.origin ?? null,
                current_decision:
                  detail.current_decision ??
                  root.current_decision ??
                  root.canonical_decision ??
                  root.current_case_decision ??
                  rootOutput?.current_decision ??
                  null,
                canonical_decision:
                  detail.canonical_decision ??
                  root.canonical_decision ??
                  root.current_decision ??
                  root.current_case_decision ??
                  rootOutput?.canonical_decision ??
                  null,
                current_case_decision:
                  detail.current_case_decision ??
                  root.current_case_decision ??
                  root.current_decision ??
                  root.canonical_decision ??
                  rootOutput?.current_case_decision ??
                  null,
                calculation_context: detail.calculation_context ?? root.calculation_context ?? null,
                decision_history:
                  Array.isArray(detail.decision_history) && detail.decision_history.length
                    ? detail.decision_history
                    : root.decision_history,
                answer_source_run_id: root.id,
              }
            } catch {
              /* root answer remains unavailable; repair state still renders */
            }
          }
          if (
            requestId !== runDetailRequestRef.current ||
            namespaceRef.current !== namespaceForRequest ||
            selectedRunIdRef.current !== runId
          )
            return null
          setSelectedRun(detail)
          setSelectedRunId(detail.id)
          setRunDetailLoad(null)
          return detail
        } catch (error) {
          if (
            requestId !== runDetailRequestRef.current ||
            namespaceRef.current !== namespaceForRequest ||
            selectedRunIdRef.current !== runId
          )
            return null
          setRunDetailLoad({ runId, status: 'error', error: error instanceof Error ? error.message : 'Saved research detail unavailable.' })
          return null
        }
      })
    },
    [],
  )

  useEffect(() => {
    setWebgl(isWebGLAvailable())
    const stored = localStorage.getItem('road2m-reduced-motion')
    if (stored === 'true') setReducedMotion(true)
  }, [])

  useEffect(() => {
    const applyLocation = () => {
      const route = navigationFor(window.location.hash)
      setPrimaryView(route.primary); setView(route.workspace); setResearchTab('questions')
      setResearchSection(route.research); setResearchOrigin(route.origin)
      if (route.research !== 'cases') clearRunSelection()
    }

    applyLocation()
    window.addEventListener('hashchange', applyLocation)
    return () => window.removeEventListener('hashchange', applyLocation)
  }, [])

  useEffect(() => {
    localStorage.setItem('road2m-reduced-motion', String(reducedMotion))
  }, [reducedMotion])

  useEffect(() => {
    let active = true
    const boot = async () => {
      // Clear transient state before the real snapshot begins. Keeping this
      // before the await prevents a late startup cleanup from wiping records
      // fetched during the response, and never invents an offline fixture.
      setMemory([])
      setCoverage([])
      setDecisions([])
      setSimulations([])
      setPortfolio(null)
      setPortfolioPolicy(null)
      setSources([])
      setProviders(null)
      setRuns([])
      setWatchlistItems([])
      setWatchlistPaused(false)
      setWatchlistEnabled(null)
      setLearning(null)
      namespaceRef.current = 'real'
      const bootRequestId = loadRequestRef.current + 1
      await loadOffice('real')
      if (!active || loadRequestRef.current !== bootRequestId) return
      await loadRuns('real', { quiet: true })
      if (active && loadRequestRef.current === bootRequestId) setWorkspaceHydrating(false)
    }
    void boot()
    return () => {
      active = false
    }
  }, [loadOffice, loadRuns])

  const connectEvents = useCallback(
    (nextNamespace: Namespace, cursor: string | number | null | undefined) => {
      eventSourceRef.current?.close()
      if (eventRefreshTimerRef.current) window.clearTimeout(eventRefreshTimerRef.current)
      // The caller owns the connection-state guard. Keeping this callback stable
      // avoids reconnecting on every event while still opening the stream after
      // the first successful office snapshot.
      if (nextNamespace === 'simulation') return
      const after = cursor == null ? 0 : encodeURIComponent(String(cursor))
      const source = new EventSource(
        `/api/events?namespace=${encodeURIComponent(nextNamespace)}&after=${after}`,
      )
      eventSourceRef.current = source
      source.addEventListener('task_event', (event) => {
        const parsed = eventFromSse((event as MessageEvent).data)
        if (
          namespaceRef.current !== nextNamespace ||
          !parsed ||
          (parsed.namespace && parsed.namespace !== nextNamespace)
        )
          return
        const sequence = Number(parsed.sequence_id)
        if (
          Number.isFinite(sequence) &&
          eventCursorRef.current !== null &&
          sequence <= eventCursorRef.current
        )
          return
        if (Number.isFinite(sequence)) eventCursorRef.current = sequence
        setOffice((current) => applyEvent(current, parsed))
        // Some durable lifecycle events intentionally carry only a run/task id;
        // refresh the compact office snapshot after a short burst so those
        // events still update the correct agent without reopening the stream.
        if (eventRefreshTimerRef.current) window.clearTimeout(eventRefreshTimerRef.current)
        eventRefreshTimerRef.current = window.setTimeout(() => {
          eventRefreshTimerRef.current = undefined
          void loadOffice(nextNamespace, { quiet: true, runId: selectedRunIdRef.current })
          void loadRuns(nextNamespace, { quiet: true })
          if (selectedRunIdRef.current) void loadRunDetail(selectedRunIdRef.current, nextNamespace)
        }, 400)
      })
      source.onopen = () => {
        if (namespaceRef.current !== nextNamespace) return
        setConnection('connected')
        setConnectionMessage('Live local updates connected')
      }
      source.onerror = () => {
        if (namespaceRef.current !== nextNamespace) return
        source.close()
        setConnection('stale')
        setConnectionMessage(
          `Last seen at ${displayTime(serverTimeRef.current)} · reconnecting on the next snapshot`,
        )
      }
    },
    [loadOffice, loadRunDetail, loadRuns],
  )

  useEffect(() => {
    if (connection === 'connected') connectEvents(namespace, office.event_cursor)
    return () => {
      eventSourceRef.current?.close()
      if (eventRefreshTimerRef.current) window.clearTimeout(eventRefreshTimerRef.current)
    }
  }, [connectEvents, connection, namespace])

  useEffect(() => {
    if (refreshTimer.current) window.clearInterval(refreshTimer.current)
    refreshTimer.current = window.setInterval(() => {
      if (document.hidden) return
      void loadOffice(namespace, { quiet: true, runId: selectedRunIdRef.current })
      void loadRuns(namespace, { quiet: true })
      if (selectedRunIdRef.current) void loadRunDetail(selectedRunIdRef.current, namespace)
      if (view === 'inbox' || primaryView === 'reddit' || primaryView === 'research') void refreshReddit(undefined, true)
    }, 15000)
    return () => {
      if (refreshTimer.current) window.clearInterval(refreshTimer.current)
    }
  }, [connection, loadOffice, loadRunDetail, loadRuns, namespace, primaryView, view])

  useEffect(() => {
    let cancelled = false
    if (!selectedAgent || connection !== 'connected') return
    const requestedNamespace = namespace
    const requestedAgentId = selectedAgent.id
    const runId = selectedRunIdRef.current
    getAgent(requestedAgentId, requestedNamespace, runId)
      .then((details) => {
        if (
          cancelled ||
          namespaceRef.current !== requestedNamespace ||
          selectedRunIdRef.current !== runId ||
          selectedId !== requestedAgentId
        )
          return
        setOffice((current) => ({
          ...current,
          agents: current.agents.map((agent) =>
            agent.id === selectedAgent.id
              ? {
                  ...agent,
                  ...details.agent,
                  queue: details.queue,
                  outputs: details.outputs,
                  evidence: details.evidence,
                  history: details.history as Array<Record<string, unknown>>,
                  current_simulation: details.agent.current_simulation ?? agent.current_simulation,
                  related_simulations:
                    details.agent.related_simulations ?? agent.related_simulations,
                }
              : agent,
          ),
        }))
      })
      .catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [
    connection,
    namespace,
    selectedAgent?.id,
    selectedRunId,
    selectedId,
    office.event_cursor,
    snapshotVersion,
  ])

  useEffect(() => {
    let cancelled = false
    const requestId = ++workspaceRequestRef.current
    const current = () => !cancelled && requestId === workspaceRequestRef.current
    const loadWorkspaceData = async () => {
      if (view === 'portfolio') {
        try {
          const nextPortfolio = await getPortfolio(namespace)
          if (current()) setPortfolio(nextPortfolio)
        } catch {
          if (current()) setPortfolio(null)
        }
      }
      if (view === 'coverage' || (view === 'research' && researchTab === 'coverage')) {
        try {
          const nextCoverage = await getCoverage(namespace)
          if (current()) setCoverage(nextCoverage)
        } catch {
          if (current()) setCoverage([])
        }
      }
      if (view === 'decisions' || primaryView === 'results' || primaryView === 'research' || primaryView === 'watchlist') {
        try {
          const nextDecisions = await getDecisions(namespace)
          if (current()) setDecisions(nextDecisions)
        } catch {
          if (current()) setDecisions([])
        }
        try {
          const nextWatchlist = await getWatchlist(namespace === 'demo' ? 'demo' : 'real')
          if (current()) {
            setWatchlistItems(nextWatchlist.items)
            setWatchlistPaused(nextWatchlist.paused)
            setWatchlistEnabled(nextWatchlist.enabled)
          }
        } catch {
          if (current()) {
            setWatchlistItems([])
            setWatchlistPaused(false)
            setWatchlistEnabled(null)
          }
        }
        try {
          const nextLearning = await getLearning(namespace === 'demo' ? 'demo' : 'real')
          if (current()) setLearning(nextLearning)
        } catch {
          if (current()) setLearning(null)
        }
      }
      if (view === 'memory' && primaryView !== 'memory') {
        try {
          const nextMemory = await searchMemory(namespace, '', 'all')
          if (current()) setMemory(nextMemory)
        } catch {
          if (current() && namespace === 'real') setMemory([])
        }
      }
      if (view === 'simulation' || (view === 'research' && researchTab === 'simulation')) {
        try {
          const nextSimulations = await getSimulations()
          if (current()) setSimulations(nextSimulations)
        } catch {
          if (current()) setSimulations([])
        }
      }
      if (view === 'inbox') {
        const requestId = ++redditInboxRequestRef.current
        const savedQuery = {
          ...redditQueryRef.current,
          offset: 0,
          limit: Math.min(500, Math.max(100, redditLoadedCountRef.current)),
        }
        try {
          const nextInbox = await getRedditInbox('real', savedQuery)
          if (current() && requestId === redditInboxRequestRef.current) {
            setRedditInbox((currentInbox) =>
              nextInbox.availability === 'unavailable' && currentInbox.posts.length
                ? {
                    ...currentInbox,
                    availability: nextInbox.availability,
                    error_message: nextInbox.error_message ?? currentInbox.error_message,
                  }
                : nextInbox,
            )
            if (nextInbox.availability !== 'unavailable') {
              redditLoadedCountRef.current = Math.max(100, nextInbox.posts.length)
              redditQueryRef.current = { ...savedQuery, offset: nextInbox.posts.length }
            }
            if (nextInbox.connection) setRedditConnection(nextInbox.connection)
          }
        } catch {
          // Retain the last known inbox when the connector is temporarily
          // unavailable; a failed refresh must not erase readable records.
        }
        try {
          const nextConnection = await getRedditConnection('real')
          if (current() && requestId === redditInboxRequestRef.current)
            setRedditConnection(nextConnection)
        } catch {
          /* connector state stays visible */
        }
      }
      if (view === 'settings') {
        try {
          const nextProviders = await getProviders()
          if (current()) setProviders(nextProviders)
        } catch {
          if (current()) setProviders(null)
        }
        try {
          const nextPolicy = await getModelPolicy()
          if (current()) setPolicy(nextPolicy)
        } catch {
          if (current()) setPolicy(null)
        }
        try {
          const nextPortfolioPolicy = await getPortfolioPolicy('real')
          if (current()) setPortfolioPolicy(nextPortfolioPolicy as Record<string, unknown>)
        } catch {
          if (current()) setPortfolioPolicy(null)
        }
        try {
          const nextMonitoring = await getMonitoring('real')
          if (current()) setMonitoring(nextMonitoring)
        } catch {
          if (current()) setMonitoring([])
        }
      }
    }
    void loadWorkspaceData()
    return () => {
      cancelled = true
    }
  }, [namespace, primaryView, researchTab, view])

  useEffect(() => {
    if (view !== 'tasks' && view !== 'decisions' && view !== 'research') return
    let cancelled = false
    const refreshRuns = async () => {
      const nextRuns = await loadRuns(namespace)
      if (cancelled || !nextRuns.length) return
      const preferred = selectedRunIdRef.current
      if (preferred && selectedRunRef.current?.id !== preferred)
        await loadRunDetail(preferred, namespace)
    }
    void refreshRuns()
    return () => {
      cancelled = true
    }
  }, [loadRunDetail, loadRuns, namespace, view])

  useEffect(() => {
    if (view !== 'simulation' && !(view === 'research' && researchTab === 'simulation')) return
    let cancelled = false
    let timer: number | undefined
    const refreshSimulations = async () => {
      try {
        const summaries = await getSimulations()
        const records = await Promise.all(
          summaries.map(async (summary) => {
            if (!summary.id) return summary
            try {
              return await getSimulation(summary.id)
            } catch {
              return summary
            }
          }),
        )
        if (!cancelled) setSimulations(records)
      } catch {
        if (!cancelled) setSimulations([])
      }
    }
    void refreshSimulations()
    timer = window.setInterval(() => {
      if (!document.hidden) void refreshSimulations()
    }, 1500)
    return () => {
      cancelled = true
      if (timer) window.clearInterval(timer)
    }
  }, [namespace, researchTab, view])

  useEffect(() => {
    // Each workspace opens at its command bar and primary content so returning
    // to the office cannot leave the floor stranded below a prior scroll.
    window.scrollTo({ top: 0, behavior: 'auto' })
  }, [view])

  function openPrimary(destination: PrimaryView, options?: { preserveSelection?: boolean }) {
    const requestedDestination = destination
    if (['questions', 'results', 'reddit', 'documents', 'library'].includes(destination)) {
      setResearchSection(destination === 'documents' ? 'earnings' : destination === 'library' ? 'history' : 'cases')
      setResearchOrigin(destination === 'reddit' ? 'reddit' : 'all')
      destination = 'research'
    } else if (destination === 'research') {
      setResearchSection('cases'); setResearchOrigin('all')
    }

    if (navigationOpen) {
      setNavigationOpen(false)
      window.setTimeout(() => mainContent.current?.focus({ preventScroll: true }), 0)
    }
    if (!options?.preserveSelection) clearRunSelection()
    setPrimaryView(destination)
    if (destination === 'research' || destination === 'questions' || destination === 'results') {
      setView('research')
      setResearchTab('questions')
    } else if (destination === 'reddit') {
      setView('inbox')
    } else if (destination === 'watchlist') {
      // The legacy decisions endpoint is retained as a compatibility source
      // while the canonical projection is served from the run list.
      setView('decisions')
    } else if (destination === 'memory') {
      setView('memory')
    } else if (destination === 'office') {
      setView('office')
    } else if (destination === 'settings') {
      setView('settings')
    } else if (
      destination === 'portfolio' ||
      destination === 'evidence' ||
      destination === 'scenarios'
    ) {
      setView(
        destination === 'portfolio'
          ? 'portfolio'
          : destination === 'evidence'
            ? 'memory'
            : 'simulation',
      )
    } else {
      setView('research')
      setResearchTab('questions')
    }
    window.scrollTo({ top: 0, behavior: 'auto' })
    const route = requestedDestination === 'documents' ? 'research/earnings'
      : requestedDestination === 'library' ? 'research/history'
      : requestedDestination === 'reddit' ? 'reddit' : destination
    window.history.replaceState(null, '', `#${route}`)
  }

  function navigate(nextView: WorkspaceViewName) {
    if (nextView === 'office') {
      openPrimary('office')
      return
    }
    if (nextView === 'inbox') {
      openPrimary('reddit')
      return
    }
    if (nextView === 'settings') {
      openPrimary('settings')
      return
    }
    if (nextView === 'portfolio') {
      openPrimary('portfolio')
      return
    }
    if (nextView === 'memory') {
      openPrimary('memory')
      return
    }
    if (nextView === 'simulation') {
      openPrimary('scenarios')
      return
    }
    let destination = nextView
    if (nextView === 'research') {
      setPrimaryView('research')
      setResearchTab('questions')
    } else if (nextView === 'tasks' || nextView === 'decisions') {
      destination = 'research'
      setResearchTab('questions')
      setPrimaryView('research')
    } else if (nextView === 'coverage') {
      destination = 'research'
      setResearchTab('coverage')
      setPrimaryView('watchlist')
    }
    window.scrollTo({ top: 0, behavior: 'auto' })
    setView(destination)
    window.history.replaceState(
      null,
      '',
      destination === 'research'
        ? `#research/${researchTab === 'questions' && nextView !== 'tasks' && nextView !== 'decisions' ? researchTab : nextView === 'coverage' ? 'coverage' : 'questions'}`
        : `#${destination}`,
    )
  }

  function selectResearchTab(tab: ResearchTab) {
    setResearchTab(tab)
    setView('research')
    window.scrollTo({ top: 0, behavior: 'auto' })
    window.history.replaceState(null, '', `#research/${tab}`)
  }

  function selectAgent(id: string) {
    setSelectedId(id)
    setSelectedTab('overview')
    setView('office')
    // Desk selection is an explicit user action. On stacked layouts the
    // inspector can sit below the office floor, so bring the selected record
    // into view after the office has rendered. The view-change effect still
    // handles the initial page position; this follow-up is selection-only.
    window.setTimeout(() => {
      const inspector = document.getElementById('office-inspector')
      if (!inspector) return
      inspector.focus({ preventScroll: true })
      inspector.scrollIntoView({
        behavior: reducedMotion ? 'auto' : 'smooth',
        block: 'start',
        inline: 'nearest',
      })
    }, 0)
  }

  async function submitRun(question: string, modelOverride: ModelConfig | null = null) {
    if (!question.trim()) return
    const resolvedOverride = modelOverride
      ? {
          provider: modelOverride.provider,
          model: modelOverride.model,
          reasoning_effort: modelOverride.reasoning_mode ?? null,
          profile: modelOverride.profile ?? 'command bar',
        }
      : undefined
    const requestedNamespace = 'real' as const
    if (redditPostUrl(question)) {
      try {
        const response = await apiFetch<any>(
          '/api/intake/reddit/post',
          {
            method: 'POST',
            body: JSON.stringify({
              namespace: requestedNamespace,
              url: question.trim(),
              dispatch: true,
              idempotency_key: createId('reddit'),
            }),
          },
          { mutation: true },
        )
        if (namespaceRef.current !== requestedNamespace) return
        const runId = response?.run_id == null ? null : String(response.run_id)
        if (runId) {
          explicitSelectedRunIdRef.current = runId
          selectedRunIdRef.current = runId
          setSelectedRunId(runId)
          await Promise.all([
            loadRuns(requestedNamespace, { quiet: true }),
            loadRunDetail(runId, requestedNamespace),
          ])
        } else {
          await loadRuns(requestedNamespace, { quiet: true })
        }
        await refreshReddit(undefined, true)
        notify(
          runId
            ? 'Reddit post retained and sent for review.'
            : 'Reddit post retained. It is waiting for local capacity or a paused queue to resume.',
        )
        openPrimary('reddit', { preserveSelection: true })
      } catch (error) {
        notify(error instanceof Error ? error.message : 'Reddit post could not be retained.')
        setLastError(error instanceof Error ? error.message : 'Reddit post could not be retained.')
      }
      return
    }
    const body = {
      question: question.trim(),
      namespace: requestedNamespace,
      ...(resolvedOverride ? { model_override: resolvedOverride } : {}),
      idempotency_key: createId('run'),
    }
    try {
      const result = await createRun(body)
      if (namespaceRef.current !== requestedNamespace) return
      const runId = result.run_id ? String(result.run_id) : null
      if (runId && namespaceRef.current === requestedNamespace) {
        selectedRunIdRef.current = runId
        setSelectedRunId(runId)
      }
      notify(
        result.reused
          ? 'Existing run reused with its original model attribution.'
          : 'Research run queued. Watch the selected desks for backend events.',
      )
      await Promise.all([
        loadOffice(requestedNamespace, { quiet: true, runId }),
        loadRuns(requestedNamespace, { quiet: true }),
      ])
      if (runId && namespaceRef.current === requestedNamespace)
        await loadRunDetail(runId, requestedNamespace)
      if (namespaceRef.current === requestedNamespace)
        openPrimary('results', { preserveSelection: true })
    } catch (error) {
      notify(error instanceof Error ? error.message : 'Run could not be queued.')
      setLastError(error instanceof Error ? error.message : 'Run could not be queued.')
    }
  }

  async function selectRun(runId: string) {
    if (!runId) return
    const requestedNamespace = namespaceRef.current
    const previousRunId = selectedRunIdRef.current
    explicitSelectedRunIdRef.current = runId
    selectedRunIdRef.current = runId
    if (previousRunId !== runId) setSelectedRun(null)
    setSelectedRunId(runId)
    setRunDetailLoad({ runId, status: 'loading' })
    await Promise.all([
      loadRunDetail(runId, requestedNamespace),
      loadOffice(requestedNamespace, { quiet: true, runId }),
    ])
    if (namespaceRef.current === requestedNamespace) {
      // A question card can sit above a long history list. Bring the selected
      // CIO brief and task flow into view after the explicit click; background
      // refreshes use loadRunDetail directly and do not move the reader.
      window.requestAnimationFrame(() =>
        document
          .querySelector<HTMLElement>('[aria-label="Selected research run"]')
          ?.scrollIntoView({ behavior: reducedMotion ? 'auto' : 'smooth', block: 'start' }),
      )
    }
  }

  function clearRunSelection() {
    runDetailRequest.current.cancel()
    setRunDetailLoad(null)
    explicitSelectedRunIdRef.current = null
    selectedRunIdRef.current = null
    setSelectedRunId(null)
    setSelectedRun(null)
    window.scrollTo({ top: 0, behavior: 'auto' })
  }

  async function explainResult(runId: string, message: string, outputId?: string | null) {
    const requestedNamespace = namespaceRef.current
    const result = await explainRun(runId, message, outputId)
    if (namespaceRef.current !== requestedNamespace) return
    notify('Explanation request queued as a linked follow-up task.')
    await Promise.all([
      loadRuns(requestedNamespace, { quiet: true }),
      loadOffice(requestedNamespace, { quiet: true, runId }),
    ])
    await loadRunDetail(runId, requestedNamespace)
    if (result.task_id) notify(`Explanation task queued · ${String(result.task_id).slice(0, 16)}`)
  }

  async function researchEvidence(
    runId: string,
    instruction: string,
    options?: { source_ids?: string[]; model_override?: Record<string, unknown> | null },
  ) {
    const requestedNamespace = namespaceRef.current
    const result = await researchMissingEvidence(runId, instruction, options)
    if (namespaceRef.current !== requestedNamespace) return
    const linkedRunId = result.linked_run_id ? String(result.linked_run_id) : null
    const nextRunId = linkedRunId ?? runId
    explicitSelectedRunIdRef.current = nextRunId
    selectedRunIdRef.current = nextRunId
    setSelectedRunId(nextRunId)
    notify(
      linkedRunId
        ? 'Missing evidence research queued as a linked run.'
        : 'Missing evidence research request accepted.',
    )
    await loadRuns(requestedNamespace, { quiet: true })
    await loadRunDetail(nextRunId, requestedNamespace)
    await loadOffice(requestedNamespace, { quiet: true, runId: nextRunId })
  }

  async function refreshReddit(query?: RedditInboxQuery, quiet = false) {
    const requestedNamespace = 'real' as const
    const previousQuery = redditQueryRef.current
    const previousLoadedCount = redditLoadedCountRef.current
    const explicitQuery = query !== undefined
    const requestedStatus =
      query?.status === 'all' ? undefined : (query?.status ?? previousQuery.status)
    const requestedLimit =
      query?.limit ??
      (explicitQuery ? 100 : Math.min(500, Math.max(100, redditLoadedCountRef.current)))
    const requestQuery: RedditInboxQuery = {
      status: requestedStatus,
      limit: requestedLimit,
      offset: 0,
    }
    redditQueryRef.current = requestQuery
    const requestId = ++redditInboxRequestRef.current
    try {
      const nextInbox = await getRedditInbox(requestedNamespace, requestQuery)
      if (
        namespaceRef.current !== requestedNamespace ||
        requestId !== redditInboxRequestRef.current ||
        redditQueryRef.current.status !== requestedStatus
      )
        return
      setRedditInbox((current) => {
        if (nextInbox.availability === 'unavailable' && !explicitQuery) {
          return {
            ...current,
            availability: nextInbox.availability,
            error_message: nextInbox.error_message ?? current.error_message,
          }
        }
        // Keep already loaded rows when an older page cannot be requested in a
        // single bounded response. Normal inbox refreshes fit in one request;
        // this tail merge preserves pagination for unusually large views.
        const currentRows = current.posts ?? []
        const sameFilter = previousQuery.status === requestedStatus
        if (
          !explicitQuery &&
          sameFilter &&
          currentRows.length > nextInbox.posts.length &&
          nextInbox.posts.length >= 500
        ) {
          const seen = new Set(nextInbox.posts.map((post) => String(post.id ?? post.post_id ?? '')))
          return {
            ...nextInbox,
            posts: [
              ...nextInbox.posts,
              ...currentRows.filter(
                (post) =>
                  !seen.has(String(post.id ?? post.post_id ?? '')) &&
                  redditPostMatchesQuery(post, requestedStatus),
              ),
            ],
          }
        }
        return nextInbox
      })
      if (nextInbox.availability !== 'unavailable') {
        const loadedCount = explicitQuery
          ? nextInbox.posts.length
          : Math.max(previousLoadedCount, nextInbox.posts.length)
        redditLoadedCountRef.current = Math.max(100, loadedCount)
        redditQueryRef.current = {
          ...requestQuery,
          offset: nextInbox.posts.length >= 500 ? loadedCount : nextInbox.posts.length,
        }
      }
      if (nextInbox.connection && namespaceRef.current === requestedNamespace)
        setRedditConnection(nextInbox.connection)
      try {
        const nextConnection = await getRedditConnection(requestedNamespace)
        if (
          namespaceRef.current !== requestedNamespace ||
          requestId !== redditInboxRequestRef.current
        )
          return
        setRedditConnection(nextInbox.connection ?? nextConnection)
      } catch {
        // The inbox payload already carries the connection state when the
        // canonical route is available; keep it if the secondary read fails.
      }
      if (
        namespaceRef.current !== requestedNamespace ||
        requestId !== redditInboxRequestRef.current
      )
        return
      if (!quiet) notify('Reddit intake refreshed.')
    } catch (error) {
      if (
        namespaceRef.current !== requestedNamespace ||
        requestId !== redditInboxRequestRef.current
      )
        return
      if (!quiet) notify(error instanceof Error ? error.message : 'Reddit intake is unavailable.')
    }
  }

  async function loadMoreReddit() {
    const requestedNamespace = 'real' as const
    const query = redditQueryRef.current
    const offset = query.offset ?? redditLoadedCountRef.current
    const requestId = ++redditInboxRequestRef.current
    try {
      const nextInbox = await getRedditInbox(requestedNamespace, {
        ...query,
        limit: query.limit ?? 100,
        offset,
      })
      if (
        namespaceRef.current !== requestedNamespace ||
        requestId !== redditInboxRequestRef.current ||
        redditQueryRef.current.status !== query.status
      )
        return
      if (nextInbox.availability === 'unavailable') {
        notify(nextInbox.error_message ?? 'More Reddit intake records are unavailable.')
        return
      }
      setRedditInbox((current) => {
        const rows = [...current.posts, ...nextInbox.posts]
        const seen = new Set<string>()
        const posts = rows.filter((post, index) => {
          const key = String(post.id ?? post.post_id ?? `row-${index}`)
          if (seen.has(key)) return false
          seen.add(key)
          return true
        })
        return {
          ...current,
          ...nextInbox,
          posts,
          offset: nextInbox.offset ?? offset,
          next_offset: nextInbox.next_offset ?? posts.length,
          page_size: posts.length,
        }
      })
      redditLoadedCountRef.current = Math.max(100, offset + nextInbox.posts.length)
      redditQueryRef.current = { ...query, offset: offset + nextInbox.posts.length }
      if (nextInbox.connection && namespaceRef.current === requestedNamespace)
        setRedditConnection(nextInbox.connection)
      if (namespaceRef.current !== requestedNamespace) return
      notify(
        nextInbox.posts.length
          ? `Loaded ${nextInbox.posts.length} more retained Reddit posts.`
          : 'No additional retained Reddit posts were returned.',
      )
    } catch (error) {
      if (namespaceRef.current !== requestedNamespace) return
      notify(
        error instanceof Error ? error.message : 'More Reddit intake records could not be loaded.',
      )
    }
  }

  async function dispatchReddit(id: string) {
    const requestedNamespace = 'real' as const
    try {
      const result = await dispatchRedditPost(id, requestedNamespace)
      if (namespaceRef.current !== requestedNamespace) return
      const response = result && typeof result === 'object' ? (result as Record<string, any>) : {}
      const dispatched = Array.isArray(response.dispatched)
        ? (response.dispatched.find(
            (item: unknown) =>
              item &&
              typeof item === 'object' &&
              String((item as Record<string, unknown>).item_id ?? '') === id,
          ) as Record<string, any> | undefined)
        : undefined
      const blocked = Array.isArray(response.blocked)
        ? (response.blocked.find(
            (item: unknown) =>
              item &&
              typeof item === 'object' &&
              String((item as Record<string, unknown>).item_id ?? '') === id,
          ) as Record<string, any> | undefined)
        : undefined
      const runId = response.run_id ?? dispatched?.run_id ?? null
      const message =
        typeof response.message === 'string'
          ? response.message
          : runId
            ? `Reddit item dispatched to run ${String(runId).slice(0, 16)}.`
            : blocked
              ? String(blocked.error ?? 'Reddit item is blocked for inspection.')
              : dispatched
                ? 'Reddit item dispatched; the linked run is now visible in the inbox.'
                : Array.isArray(response.errors) && response.errors.length
                  ? 'Reddit dispatch returned an error; inspect the retained item.'
                  : 'No Reddit item was dispatched; the inbox state was refreshed.'
      notify(message)
      await refreshReddit()
    } catch (error) {
      if (namespaceRef.current !== requestedNamespace) return
      notify(error instanceof Error ? error.message : 'Reddit item could not be dispatched.')
    }
  }

  async function reuseReddit(id: string) {
    const requestedNamespace = 'real' as const
    try {
      const result = await reuseRedditPost(id, requestedNamespace)
      if (namespaceRef.current !== requestedNamespace) return
      const response = result && typeof result === 'object' ? (result as Record<string, any>) : {}
      const runId = response.run_id == null ? null : String(response.run_id)
      notify(
        runId
          ? `Existing research reused · run ${runId.slice(0, 16)}.`
          : typeof response.message === 'string'
            ? response.message
            : 'Existing research was reused for this Reddit item.',
      )
      await refreshReddit()
    } catch (error) {
      if (namespaceRef.current !== requestedNamespace) return
      notify(error instanceof Error ? error.message : 'Reddit item could not be reused.')
    }
  }

  async function saveRedditSettings(input: {
    subreddit: string
    window_days: number
    enabled: boolean
  }) {
    const requestedNamespace = 'real' as const
    try {
      const saved = await saveRedditConnection({ namespace: requestedNamespace, ...input })
      if (namespaceRef.current !== requestedNamespace) return
      setRedditConnection(saved)
      notify('Reddit connection settings saved.')
    } catch (error) {
      if (namespaceRef.current !== requestedNamespace) return
      notify(
        error instanceof Error ? error.message : 'Reddit connection settings could not be saved.',
      )
    }
  }

  async function control(
    action: 'pause' | 'resume' | 'cancel' | 'retry',
    scope: 'firm' | 'run' | 'task',
    id: string | null,
  ) {
    const requestedNamespace = namespaceRef.current
    try {
      const result = await apiFetch<{ ok: boolean; affected: number }>(
        '/api/control',
        { method: 'POST', body: JSON.stringify({ scope, id, action }) },
        { mutation: true },
      )
      if (namespaceRef.current !== requestedNamespace) return
      notify(
        `${action[0].toUpperCase()}${action.slice(1)} recorded for ${scope}${result.affected == null ? '' : ` · ${result.affected} affected`}.`,
      )
      await Promise.all([
        loadOffice(requestedNamespace, { quiet: true, runId: selectedRunIdRef.current }),
        loadRuns(requestedNamespace, { quiet: true }),
      ])
      if (selectedRunIdRef.current)
        await loadRunDetail(selectedRunIdRef.current, requestedNamespace)
    } catch (error) {
      notify(error instanceof Error ? error.message : 'Control request failed.')
    }
  }

  async function openMemorySearch(query: string, kind: string) {
    const requestId = workspaceRequestRef.current
    const requestedNamespace = namespace
    try {
      const nextMemory = await searchMemory(requestedNamespace, query, kind)
      if (requestId === workspaceRequestRef.current && requestedNamespace === namespace)
        setMemory(nextMemory)
    } catch (error) {
      if (requestId === workspaceRequestRef.current && requestedNamespace === namespace)
        notify(error instanceof Error ? error.message : 'Memory search failed.')
    }
  }

  async function importSource(payload: {
    kind: 'evidence' | 'transactions' | 'balances'
    title: string
    content: string
    source_url?: string | null
    publication_at?: string | null
    observed_at?: string | null
    supersedes_id?: string | null
  }) {
    const requestedNamespace = namespace
    const requestId = workspaceRequestRef.current
    const result = await apiFetch<any>(
      '/api/imports',
      {
        method: 'POST',
        body: JSON.stringify({
          namespace: requestedNamespace,
          ...payload,
          source_url: payload.source_url ?? null,
          publication_at: payload.publication_at ?? null,
          observed_at: payload.observed_at ?? null,
          supersedes_id: payload.supersedes_id ?? null,
          idempotency_key: createId('import'),
        }),
      },
      { mutation: true },
    )
    if (requestId !== workspaceRequestRef.current || requestedNamespace !== namespace) return
    notify(
      result.status === 'needs_review'
        ? 'Import staged and needs review. Ambiguities remain visible.'
        : 'Import saved with a source id.',
    )
    try {
      const nextSources = await getSources(requestedNamespace)
      if (requestId === workspaceRequestRef.current && requestedNamespace === namespace)
        setSources(nextSources)
    } catch {
      /* refreshed on next connection snapshot */
    }
    if (requestId === workspaceRequestRef.current && requestedNamespace === namespace)
      await loadOffice(requestedNamespace, { quiet: true, runId: selectedRunIdRef.current })
  }

  async function exportArchive(format: 'json' | 'markdown' = 'json') {
    try {
      const result = await apiFetch<any>(
        `/api/export?namespace=${encodeURIComponent(namespace)}&format=${format}`,
      )
      const isMarkdown = format === 'markdown'
      const body = typeof result === 'string' ? result : JSON.stringify(result, null, 2)
      const blob = new Blob([body], { type: isMarkdown ? 'text/markdown' : 'application/json' })
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = `researchcouncil-${namespace}-archive-${new Date().toISOString().slice(0, 10)}.${isMarkdown ? 'md' : 'json'}`
      anchor.click()
      URL.revokeObjectURL(url)
      notify(`${isMarkdown ? 'Markdown' : 'JSON'} archive downloaded.`)
    } catch (error) {
      notify(error instanceof Error ? error.message : 'Export failed.')
    }
  }

  async function openOutput(output: OutputRecord) {
    const outputId = output.id ?? output.output_id
    const requestId = workspaceRequestRef.current
    const requestedNamespace = namespaceRef.current
    if (outputId && connection === 'connected') {
      try {
        const fetched = await getOutput(outputId, requestedNamespace)
        if (
          requestId !== workspaceRequestRef.current ||
          namespaceRef.current !== requestedNamespace
        )
          return
        setOutputViewer(fetched)
        return
      } catch {
        /* retain the already fetched output if the detail route is temporarily unavailable */
      }
    }
    if (requestId === workspaceRequestRef.current && namespaceRef.current === requestedNamespace)
      setOutputViewer(output)
  }

  async function openSource(source: EvidenceRef) {
    const sourceId = source.id ?? source.source_ref
    const requestId = workspaceRequestRef.current
    const requestedNamespace = namespaceRef.current
    if (sourceId && connection === 'connected') {
      try {
        const fetched = await getSource(sourceId, requestedNamespace)
        if (
          requestId !== workspaceRequestRef.current ||
          namespaceRef.current !== requestedNamespace
        )
          return
        // Keep claim context (locator, validation and matched excerpt) when the
        // full source record replaces a lightweight output reference.
        setSourceViewer({
          ...fetched,
          id: fetched.id ?? source.id,
          source_ref: fetched.source_ref ?? source.source_ref,
          title: fetched.title ?? source.title,
          url: fetched.url ?? source.url,
          source_type: fetched.source_type ?? source.source_type,
          content: fetched.content ?? source.content,
          excerpt: source.excerpt ?? fetched.excerpt,
          locator: source.locator ?? fetched.locator,
          selected_locator: source.selected_locator ?? source.locator ?? fetched.locator,
          matched_excerpt: source.matched_excerpt ?? fetched.matched_excerpt,
          validation_status: source.validation_status ?? fetched.validation_status,
          validation_reason: source.validation_reason ?? fetched.validation_reason,
          unknown_reason: source.unknown_reason ?? fetched.unknown_reason,
          line_start: source.line_start ?? fetched.line_start,
          line_end: source.line_end ?? fetched.line_end,
          text_match: source.text_match ?? fetched.text_match,
          semantic_status: source.semantic_status ?? fetched.semantic_status,
          binding_checks: source.binding_checks?.length
            ? source.binding_checks
            : fetched.binding_checks,
          source_version: source.source_version ?? fetched.source_version,
          freshness: source.freshness ?? fetched.freshness,
          freshness_policy: source.freshness_policy ?? fetched.freshness_policy,
          freshness_as_of: source.freshness_as_of ?? fetched.freshness_as_of,
          extraction_status: source.extraction_status ?? fetched.extraction_status,
          extraction_version: source.extraction_version ?? fetched.extraction_version,
          extraction_failure_reason:
            source.extraction_failure_reason ?? fetched.extraction_failure_reason,
        })
        return
      } catch {
        /* retain the reference when source content is unavailable */
      }
    }
    if (requestId === workspaceRequestRef.current && namespaceRef.current === requestedNamespace)
      setSourceViewer(source)
  }

  async function openSourceVersions(sourceId: string) {
    const requestId = workspaceRequestRef.current
    const requestedNamespace = namespaceRef.current
    const values = await getSourceVersions(sourceId, requestedNamespace)
    if (requestId !== workspaceRequestRef.current || namespaceRef.current !== requestedNamespace)
      return []
    return values
  }

  async function updateCoverage(item: CoverageItem, status: string, reason: string) {
    const result = await apiFetch<any>(
      `/api/coverage/${encodeURIComponent(item.ticker)}`,
      {
        method: 'PUT',
        body: JSON.stringify({
          namespace,
          sector: item.name ?? 'Unspecified',
          status,
          reason,
          reopen_when: item.reopening_condition ?? '',
        }),
      },
      { mutation: true },
    )
    const updated = normalizeCoverage({ items: [result?.item ?? result] })[0]
    setCoverage((current) =>
      current.map((candidate) =>
        candidate.id === item.id
          ? updated?.ticker === '—'
            ? { ...candidate, status, reason }
            : updated
          : candidate,
      ),
    )
    notify(`${item.ticker} coverage updated.`)
  }

  async function runSimulation(input: {
    title: string
    question: string
    horizon: string
    participants: number
    rounds: number
    seed: number
    initial_price: string
    shock_percent: string
    use_llm: boolean
    source_ids: string[]
  }) {
    const result = await apiFetch<any>(
      '/api/simulations',
      {
        method: 'POST',
        body: JSON.stringify({
          ...input,
          source_ids: input.source_ids,
          idempotency_key: createId('simulation'),
        }),
      },
      { mutation: true },
    )
    const run = {
      id: result.simulation_id,
      simulation_id: result.simulation_id,
      status: result.status ?? 'queued',
      name: input.title,
      scenario: input.question,
      participants: input.participants,
      rounds: input.rounds,
      seed: input.seed,
      horizon: input.horizon,
      shock: input.shock_percent,
      created_at: new Date().toISOString(),
    }
    setSimulations((current) => [run, ...current])
    notify('Scenario queued in the separate workspace.')
  }

  async function replaySimulation(id: string) {
    try {
      const result = await apiFetch<any>(
        `/api/simulations/${encodeURIComponent(id)}/replay`,
        { method: 'POST', body: '{}' },
        { mutation: true },
      )
      notify(
        result.matches
          ? 'Exact replay matched the recorded actions.'
          : 'Replay completed with a mismatch; inspect the recorded inputs.',
      )
      return result
    } catch (error) {
      notify(error instanceof Error ? error.message : 'Replay failed.')
      throw error
    }
  }

  function openSimulation(simulation: SimulationRun) {
    setSimulationFocusId(simulation.id ?? simulation.simulation_id ?? null)
    navigate('simulation')
  }

  async function updateModelPolicy(input: {
    scope: 'firm' | 'role' | 'profile'
    agent_id: string | null
    config: ModelConfig | null
    profile: string | null
  }) {
    const result = await apiFetch<any>(
      '/api/model-policy',
      {
        method: 'PUT',
        body: JSON.stringify({
          ...input,
          config: input.config
            ? {
                provider: input.config.provider,
                model: input.config.model,
                reasoning_effort: input.config.reasoning_mode ?? null,
                profile: input.config.profile ?? 'default',
              }
            : null,
        }),
      },
      { mutation: true },
    )
    setPolicy(result)
    notify('Model policy saved. In-flight attempts retain their resolved configuration.')
  }

  async function updatePortfolioPolicy(input: Record<string, unknown>) {
    const requestedNamespace = 'real' as const
    const result = await savePortfolioPolicy({ ...input, namespace: requestedNamespace })
    if (namespaceRef.current !== requestedNamespace) return
    if (result && typeof result === 'object') setPortfolioPolicy(result as Record<string, unknown>)
    notify('Portfolio policy saved. Only an explicitly approved policy is used for funded sizing.')
  }

  async function preflight(
    provider: string,
    model: string | null,
    reasoning_effort: string | null,
    execute: boolean,
  ) {
    try {
      const result = await apiFetch<any>(
        `/api/providers/${encodeURIComponent(provider)}/preflight`,
        { method: 'POST', body: JSON.stringify({ model, reasoning_effort, execute }) },
        { mutation: true },
      )
      notify(
        result.available
          ? `${provider} preflight passed${result.actual_execution ? ' with observed execution.' : '.'}`
          : `${provider} preflight blocked: ${result.reason ?? 'availability unavailable'}`,
      )
      setProviders(await getProviders())
    } catch (error) {
      notify(error instanceof Error ? error.message : 'Provider preflight failed.')
    }
  }

  async function saveRiskSettings(settings: Record<string, string | null>) {
    await apiFetch<any>(
      '/api/risk-settings',
      { method: 'PUT', body: JSON.stringify(settings) },
      { mutation: true },
    )
    notify('Risk constraints saved. Missing values remain unknown.')
  }

  async function createBackup() {
    try {
      const result = await apiFetch<any>(
        '/api/backup',
        { method: 'POST', body: '{}' },
        { mutation: true },
      )
      notify(
        result.validated
          ? `Backup validated at ${result.path}`
          : 'Backup created; validation state was not returned.',
      )
    } catch (error) {
      notify(error instanceof Error ? error.message : 'Backup failed.')
    }
  }

  async function restoreBackup(path: string) {
    const result = await apiFetch<any>(
      '/api/restore',
      { method: 'POST', body: JSON.stringify({ path }) },
      { mutation: true },
    )
    notify(result.validated ? 'Backup restored and validated.' : 'Backup restore completed.')
    await loadOffice(namespace, { quiet: true, runId: selectedRunIdRef.current })
  }

  async function addMonitoring(input: {
    name: string
    enabled: boolean
    timezone: string
    interval_minutes: number
    condition: string
    source_ids: string[]
    mode?: 'interval_research' | 'source_change'
  }) {
    const requestedNamespace = 'real' as const
    const requestId = workspaceRequestRef.current
    const result = await apiFetch<any>(
      '/api/monitoring',
      { method: 'POST', body: JSON.stringify({ namespace: requestedNamespace, ...input }) },
      { mutation: true },
    )
    if (requestId !== workspaceRequestRef.current || requestedNamespace !== namespace) return
    setMonitoring((current) => [
      { ...result, id: String(result.id) },
      ...current.filter((rule) => rule.id !== result.id),
    ])
    notify('Monitoring rule saved. It runs only while the local process is available.')
  }

  async function toggleMonitoring(rule: MonitoringRule) {
    const requestedNamespace = namespace
    const requestId = workspaceRequestRef.current
    const result = await apiFetch<any>(
      `/api/monitoring/${encodeURIComponent(rule.id)}`,
      { method: 'PUT', body: JSON.stringify({ enabled: !rule.enabled }) },
      { mutation: true },
    )
    if (requestId !== workspaceRequestRef.current || requestedNamespace !== namespace) return
    setMonitoring((current) =>
      current.map((candidate) =>
        candidate.id === rule.id ? { ...candidate, ...result } : candidate,
      ),
    )
    notify(`${rule.name} ${result.enabled ? 'enabled' : 'paused'}.`)
  }

  async function saveLifecycle(input: {
    run_id: string
    candidate_key: string
    state: 'held' | 'closed' | 'watchlist'
    reason: string
    confirmed: boolean
  }) {
    const requestedNamespace = namespaceRef.current === 'demo' ? 'demo' : 'real'
    await recordLifecycle(input.run_id, {
      namespace: requestedNamespace,
      candidate_key: input.candidate_key,
      state: input.state,
      reason: input.reason,
      confirmed: input.confirmed,
      idempotency_key: createId(`lifecycle-${input.state}`),
    })
    notify(`Lifecycle recorded as ${input.state}.`)
    try {
      const nextWatchlist = await getWatchlist(requestedNamespace)
      setWatchlistItems(nextWatchlist.items)
      setWatchlistPaused(nextWatchlist.paused)
      setWatchlistEnabled(nextWatchlist.enabled)
    } catch {
      /* current case remains readable while the list refreshes */
    }
    try {
      setLearning(await getLearning(requestedNamespace))
    } catch {
      /* learning is read-only and optional */
    }
  }

  const workspaceProps = {
    view,
    researchTab,
    onResearchTab: selectResearchTab,
    namespace,
    office,
    providers,
    policy,
    portfolio,
    portfolioPolicy,
    memory,
    sources,
    coverage,
    decisions,
    runs,
    selectedRun,
    simulations,
    monitoring,
    simulationFocusId,
    redditInbox,
    redditConnection,
    onRefreshReddit: refreshReddit,
    onLoadMoreReddit: loadMoreReddit,
    onDispatchReddit: dispatchReddit,
    onReuseReddit: reuseReddit,
    onSaveRedditSettings: saveRedditSettings,
    onNavigate: navigate,
    onSearchMemory: openMemorySearch,
    onImport: importSource,
    onExport: exportArchive,
    onOpenOutput: openOutput,
    onOpenSource: openSource,
    onSourceVersions: openSourceVersions,
    onCoverage: updateCoverage,
    onRunSimulation: runSimulation,
    onReplaySimulation: replaySimulation,
    onModelPolicy: updateModelPolicy,
    onPortfolioPolicy: updatePortfolioPolicy,
    onPreflight: preflight,
    onRiskSettings: saveRiskSettings,
    onBackup: createBackup,
    onRestore: restoreBackup,
    onMonitoring: addMonitoring,
    onMonitoringToggle: toggleMonitoring,
    onControl: control,
    onSelectRun: selectRun,
    onExplainResult: explainResult,
    onResearchMissingEvidence: researchEvidence,
    onRefreshRuns: async () => {
      await loadRuns(namespace, { quiet: true })
    },
    reducedMotion,
    onReducedMotion: setReducedMotion,
  }
  const connectionTone =
    connection === 'connected' ? 'connected' : connection === 'stale' ? 'stale' : 'connecting'
  const activeRuns = globalWorkRuns(runs)
  const runningCount = activeRuns.reduce((sum, run) => sum + persistedTaskCount(run, 'running'), 0)
  const queuedCount = activeRuns.reduce((sum, run) => sum + persistedTaskCount(run, 'queued'), 0)
  const activeCount = runningCount + queuedCount
  const activeTaskRuns = activeRuns.filter(
    (run) => persistedTaskCount(run, 'running') + persistedTaskCount(run, 'queued') > 0,
  )
  const activeWorkDestination: WorkspaceViewName =
    activeTaskRuns.length > 0 && activeTaskRuns.every((run) => redditRun(run, runs))
      ? 'inbox'
      : 'research'

  return (
    <div
      className="app-shell"
      onKeyDown={(event) => {
        if (event.key === 'Escape' && navigationOpen) {
          setNavigationOpen(false)
          navigationToggle.current?.focus()
        }
      }}
    >
      <a
        className="skip-link"
        href="#main-content"
        onClick={(event) => {
          event.preventDefault()
          mainContent.current?.focus()
        }}
      >
        Skip to content
      </a>
      <header className="topbar">
        <button
          type="button"
          className="brand"
          onClick={() => openPrimary('questions')}
          aria-label="Open research"
        >
          <span className="brand-mark">RE</span>
          <span>
            <strong>Research Engine</strong>
            <small>Local workspace</small>
          </span>
        </button>
        <div className="topbar-actions">
          <button
            type="button"
            className="topbar-stat"
            onClick={() => navigate(activeWorkDestination)}
            disabled={workspaceHydrating}
            aria-label={
              workspaceHydrating
                ? 'Connecting to local workspace'
                : activeWorkDestination === 'inbox'
                  ? 'Open Reddit intake'
                  : 'Open research queue'
            }
          >
            <span className={`live-pip ${activeCount ? 'is-live' : ''}`} />
            {workspaceHydrating
              ? 'Connecting to local workspace…'
              : activeCount
                ? `${runningCount} running · ${queuedCount} queued`
                : 'No active work'}
          </button>
          <span
            className={`connection-pill connection-${connectionTone}${office.paused ? ' is-paused' : ''}`}
            title={`${connectionMessage}. Updated ${displayTime(office.server_time ?? office.generated_at)}`}
          >
            <span className="connection-dot" />
            {connection === 'connected'
              ? office.paused
                ? 'Background research paused'
                : 'Connected'
              : connection === 'stale'
                ? 'Reconnecting'
                : 'Connecting'}
          </span>
          <button
            type="button"
            className="button navigation-toggle"
            ref={navigationToggle}
            aria-expanded={navigationOpen}
            aria-controls="workspace-navigation"
            aria-label={navigationOpen ? 'Close navigation' : 'Open navigation'}
            onClick={() => setNavigationOpen(!navigationOpen)}
          >
            <Icon name={navigationOpen ? 'close' : 'menu'} size={18} />
            Menu
          </button>
        </div>
      </header>
      <div className="app-body">
        <aside
          id="workspace-navigation"
          className={`sidebar${navigationOpen ? ' is-open' : ''}`}
          aria-label="Primary navigation"
        >
          <nav className="primary-nav" aria-label="Main sections">
            {NAV_ITEMS.map((item) => <button type="button" key={item.id}
              className={`nav-item${primaryView === item.id ? ' is-active' : ''}`}
              onClick={() => openPrimary(item.id)} aria-current={primaryView === item.id ? 'page' : undefined}>
              <Icon name={item.icon} size={18} /><span>{item.label}</span>
            </button>)}
          </nav>
          <div className="sidebar-bottom">
            <div className="sidebar-utilities">
              <button
                type="button"
                className={`nav-item nav-item-secondary${primaryView === 'office' ? ' is-active' : ''}`}
                onClick={() => openPrimary('office')}
                aria-current={primaryView === 'office' ? 'page' : undefined}
              >
                <Icon name="grid" size={17} />
                <span>Office</span>
              </button>
              <button
                type="button"
                className={`nav-item nav-item-secondary${primaryView === 'settings' ? ' is-active' : ''}`}
                onClick={() => openPrimary('settings')}
                aria-current={primaryView === 'settings' ? 'page' : undefined}
              >
                <Icon name="settings" size={17} />
                <span>Settings</span>
              </button>
            </div>
            <button
              type="button"
              className="sidebar-refresh"
              onClick={() => void loadOffice(namespace, { runId: selectedRunIdRef.current })}
              title={`Last updated ${displayTime(office.server_time ?? office.generated_at)}`}
            >
              <Icon name="refresh" size={14} /> Refresh workspace
            </button>
          </div>
        </aside>
        <main
          id="main-content"
          ref={mainContent}
          tabIndex={-1}
          className={`main-content${workspaceHydrating ? ' is-hydrating' : ''}`}
        >
          {!selectedRun && (primaryView === 'research' || primaryView === 'questions' || primaryView === 'reddit') && (
            <CommandBar
              namespace={namespace}
              providers={providers}
              onRun={submitRun}
              disabled={workspaceHydrating}
              showIntro={primaryView === 'research' && researchSection === 'cases'}
            />
          )}
          {primaryView === 'research' && researchSection === 'cases' && !selectedRun && <MarketNews />}
          {workspaceHydrating && <WorkspaceLoadingState />}
          {primaryView === 'research' && !selectedRun && <div className="research-section-switch" role="group" aria-label="Research views">
            {([{id:'cases',label:'All research'}, {id:'earnings',label:'Earnings & materials'}, {id:'history',label:'Company history'}] as const).map((item) => <button type="button" key={item.id} className={`button${researchSection === item.id ? ' is-active' : ''}`} aria-pressed={researchSection === item.id} onClick={() => {setResearchSection(item.id); window.history.replaceState(null, '', `${window.location.search}#research${item.id === 'cases' ? '' : '/' + item.id}`)}}>{item.label}</button>)}
          </div>}

          <WorkspaceBoundary key={`${primaryView}:${view}`}>
            <Suspense
              fallback={
                <p role="status" className="muted-copy">
                  Loading workspace…
                </p>
              }
            >
              {primaryView === 'documents' || (primaryView === 'research' && researchSection === 'earnings' && !selectedRun) ? (
                <Documents
                  onResearch={(question) => {
                    void submitRun(question)
                  }}
                  onOpenResearch={(runId) => {
                    void selectRun(runId)
                    openPrimary('results', { preserveSelection: true })
                  }}
                />
              ) : primaryView === 'memory' ? (
                <Memory key={namespace} namespace={namespace} />
              ) : primaryView === 'congress' ? (
                <Congress />
              ) : primaryView === 'strategies' ? (
                <Strategies />
              ) : primaryView === 'library' || (primaryView === 'research' && researchSection === 'history' && !selectedRun) ? (
                <ResearchLibrary
                  key={namespace}
                  namespace={namespace}
                  onResearch={(question) => {
                    void submitRun(question)
                  }}
                />
              ) : primaryView === 'office' ? (
                <ExperienceWorkspace
                  agents={agents}
                  selectedId={selectedAgent?.id ?? selectedId}
                  selectedAgent={selectedAgent}
                  namespace={namespace}
                  firmPaused={Boolean(office.paused)}
                  connection={connection}
                  connectionMessage={connectionMessage}
                  lastError={lastError}
                  sceneMode={sceneMode}
                  setSceneMode={setSceneMode}
                  webgl={webgl}
                  resetToken={resetToken}
                  zoomDelta={zoomDelta}
                  reducedMotion={reducedMotion}
                  onReset={() => setResetToken((current) => current + 1)}
                  onZoom={(delta) => setZoomDelta((value) => value + delta)}
                  onSelect={selectAgent}
                  onControl={control}
                  onNavigate={navigate}
                  selectedRun={selectedRun}
                  onSelectRun={(value: RunSummary | string | null) => {
                    const id = typeof value === 'string' ? value : value?.id
                    if (id) {
                      void selectRun(id)
                      openPrimary('results', { preserveSelection: true })
                    }
                  }}
                  onOpenOutput={openOutput}
                  onStartSimulation={() => navigate('simulation')}
                />
              ) : view === 'portfolio' ||
                view === 'memory' ||
                view === 'simulation' ||
                view === 'coverage' ||
                (view === 'research' && researchTab === 'simulation') ? (
                <WorkspaceViews key={namespace} {...workspaceProps} />
              ) : primaryView === 'research' || primaryView === 'questions' ||
                primaryView === 'results' ||
                primaryView === 'watchlist' ||
                primaryView === 'reddit' ? (
                <DecisionWorkspace
                  mode={primaryView}
                  initialOriginFilter={researchOrigin}
                  namespace={namespace}
                  runs={runs}
                  selectedRun={selectedRun}
                  selectedRunId={selectedRunId}
                  detailLoad={runDetailLoad}
                  redditInbox={redditInbox}
                  redditConnection={redditConnection}
                  office={office}
                  watchlistItems={watchlistItems}
                  watchlistPaused={watchlistPaused}
                  watchlistEnabled={watchlistEnabled}
                  learning={learning}
                  sources={sources}
                  onSelectRun={selectRun}
                  onClearRun={clearRunSelection}
                  onOpenSource={openSource}
                  onOpenOutput={openOutput}
                  onControl={control}
                  onRecordLifecycle={saveLifecycle}
                  onNavigate={openPrimary}
                  onDispatchReddit={async (id) => {
                    await dispatchReddit(id)
                  }}
                  onReuseReddit={async (id) => {
                    await reuseReddit(id)
                  }}
                  onRefreshReddit={async () => {
                    await refreshReddit(undefined, true)
                  }}
                />
              ) : (
                <WorkspaceViews key={namespace} {...workspaceProps} />
              )}
            </Suspense>
          </WorkspaceBoundary>
        </main>
        {primaryView === 'office' && selectedAgent && (
          <Inspector
            agent={selectedAgent}
            namespace={namespace}
            tab={selectedTab}
            onTab={setSelectedTab}
            onClose={() => setSelectedId('')}
            onControl={control}
            onOpenOutput={openOutput}
            onOpenSource={openSource}
            onOpenSimulation={openSimulation}
          />
        )}
      </div>
      {outputViewer && (
        <OutputViewer
          output={outputViewer}
          onClose={() => setOutputViewer(null)}
          onSource={openSource}
        />
      )}
      {sourceViewer && (
        <SourceViewer
          source={sourceViewer}
          namespace={namespace}
          onClose={() => setSourceViewer(null)}
        />
      )}
      {toast && (
        <div className="toast" role="status">
          <Icon name="check" size={16} />
          {toast}
        </div>
      )}
    </div>
  )
}

function CommandBar({
  namespace: _namespace,
  providers,
  onRun,
  disabled = false,
  showIntro = false,
}: {
  namespace: Namespace
  providers: ProviderState | null
  onRun: (question: string, modelOverride?: ModelConfig | null) => Promise<void>
  disabled?: boolean
  showIntro?: boolean
}) {
  const [question, setQuestion] = useState('')
  const [model, setModel] = useState('gpt-6-luna')
  const [provider, setProvider] = useState('codex')
  const [reasoning, setReasoning] = useState('high')
  const [modelOverride, setModelOverride] = useState(false)
  const [sending, setSending] = useState(false)
  const modelOptions =
    providers?.providers?.flatMap((row: any) =>
      (row.models ?? []).map((item: any) => ({
        provider: item.provider || row.provider,
        id: item.model,
        label: item.label ?? item.model,
        available: item.available,
        reason: item.reason,
        reasoning_efforts: Array.isArray(item.reasoning_efforts) ? item.reasoning_efforts : [],
      })),
    ) ?? []
  const modelState = (providerId: string, modelId: string) =>
    modelOptions.find((item) => item.provider === providerId && item.id === modelId)
  const modelDescriptor = modelOptions.find(
    (item) => item.provider === provider && item.id === model,
  )
  const effortOptions = modelDescriptor?.reasoning_efforts?.length
    ? modelDescriptor.reasoning_efforts
    : modelEfforts(model)
  const selectedEffort = effortOptions.includes(reasoning)
    ? reasoning
    : effortOptions[effortOptions.length - 1]
  async function submit(event: React.FormEvent) {
    event.preventDefault()
    if (disabled || !question.trim()) return
    setSending(true)
    try {
      const override = modelOverride
        ? { provider, model, reasoning_mode: selectedEffort, profile: 'command bar', scope: 'run' }
        : null
      await onRun(question, override)
      setQuestion('')
    } finally {
      setSending(false)
    }
  }
  return (
    <section className="command-bar" aria-label="Research composer">
      {showIntro && <div className="command-intro"><h1>What should we research?</h1><p>Start with a ticker, an investment question, or a Reddit link.</p></div>}
      <form className="command-form" onSubmit={submit}>
        <div className="command-input">
          <input
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder="e.g. META — is the growth worth today’s price?"
            aria-label="Enter a ticker, question, or Reddit post URL"
            disabled={disabled}
          />
          <button
            type="submit"
            className="command-submit"
            disabled={disabled || sending || !question.trim()}
            aria-label="Start research"
          >
            <span>{sending ? 'Starting…' : 'Start research'}</span>
            <Icon name="arrow" size={16} />
          </button>
        </div>
        <details className="command-options">
          <summary>Model options</summary>
          <div className="command-controls">
            <label className="model-select">
              <span>Model</span>
              <select
                value={modelOverride ? `${provider}:${model}` : ''}
                disabled={disabled}
                onChange={(event) => {
                  if (!event.target.value) {
                    setModelOverride(false)
                    return
                  }
                  const [nextProvider, ...rest] = event.target.value.split(':')
                  const nextModel = rest.join(':')
                  setModelOverride(true)
                  setProvider(nextProvider)
                  setModel(nextModel)
                  const descriptor = modelState(nextProvider, nextModel)
                  if (
                    descriptor?.reasoning_efforts?.length &&
                    !descriptor.reasoning_efforts.includes(reasoning)
                  )
                    setReasoning(
                      descriptor.reasoning_efforts[descriptor.reasoning_efforts.length - 1],
                    )
                }}
              >
                <option value="">Role default</option>
                {[
                  { id: 'gpt-6-luna', label: 'GPT-6 Luna' },
                  { id: 'gpt-6-sol', label: 'GPT-6 Sol' },
                  { id: 'gpt-6-astra', label: 'GPT-6 Astra' },
                ].map((item) => <option key={item.id} value={`codex:${item.id}`} disabled={!modelState('codex', item.id)?.available}>{item.label}{modelState('codex', item.id)?.available ? '' : ' · unvalidated'}</option>)}
                {modelOptions
                  .filter((item) => item.id && !(item.provider === 'codex' && ['gpt-6-luna', 'gpt-6-sol', 'gpt-6-astra'].includes(item.id)))
                  .map((item) => (
                    <option
                      key={`${item.provider}:${item.id}`}
                      value={`${item.provider}:${item.id}`}
                      disabled={!item.available}
                    >
                      {item.label}
                      {item.available ? '' : ` · ${item.reason ?? 'unavailable'}`}
                    </option>
                  ))}
              </select>
            </label>
            <label>
              <span>Effort</span>
              <select
                value={selectedEffort}
                disabled={disabled || !modelOverride}
                onChange={(event) => setReasoning(event.target.value)}
              >
                {effortOptions.map((effort: string) => (
                  <option key={effort} value={effort}>
                    {effort}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </details>
      </form>
    </section>
  )
}
