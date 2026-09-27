import { useEffect, useRef, useState } from 'react'
import { ApiError, apiFetch } from '../../api'
import { initialEarningsSelection, newerSourceCoverage, replacementEarningsSelection } from '../../components/investmentProcessModel'
import EarningsBrief from './EarningsBrief'
import type { EarningsTrendsResult } from './earningsTrendModel'
import {
  ComparisonView,
  TranscriptView,
  type ComparisonResult,
  type TranscriptResult,
} from './DocumentViews'
import { displayDate, downloadJson, ErrorNotice, sourceUrl } from './shared'

type RunStatus = 'queued' | 'running' | 'completed' | 'partial' | 'failed' | 'cancelled'
type AgentStatus = 'pending' | 'running' | 'completed' | 'partial' | 'failed' | 'skipped'
type WorkflowStep = {
  id: string
  name: string
  status: AgentStatus
  attempts: number
  error?: string | null
  started_at?: string
  finished_at?: string
}
type WorkflowDocument = {
  status: string
  source_id?: string
  url?: string
  title?: string
  reason?: string
  period_end?: string
  form?: string
  kind?: string
}
type WorkflowResult = {
  company?: { name: string; ticker: string; cik: string }
  event?: {
    fiscal_period?: string
    period_end?: string
    earnings_date?: string
    expected_form?: string
  }
  documents?: Record<string, WorkflowDocument>
  materials?: WorkflowDocument[]
  material_gaps?: string[]
  comparison_gaps?: string[]
  trends?: EarningsTrendsResult | null
  analyses?: {
    transcript?: { id: string; result?: TranscriptResult }
    filing_comparison?: { id: string; result?: ComparisonResult }
  }
  summary?: string
  comparison_basis?: string
  gaps?: string[]
  source_ids?: string[]
  research_run_id?: string
  context?: {
    research?: { items?: { id: string; ticker?: string; request?: string; status?: string }[] }
    congress?: {
      available?: boolean
      snapshot_date?: string
      items?: unknown[]
      total?: number
      note?: string
    }
    library?: { items?: unknown[] }
    portfolio?: { positions?: unknown[] }
    watchlist?: { items?: unknown[] }
    strategies?: { saved_studies?: number; items?: unknown[]; note?: string }
  }
}
type WorkflowRun = {
  id: string
  workflow: string
  version: string
  ticker: string
  status: RunStatus
  created_at: string
  updated_at: string
  steps: WorkflowStep[]
  result?: WorkflowResult | null
  research_run_id?: string | null
  source_refresh_of?: string | null
  error?: string | null
}
type Recipe = {
  id: string
  name: string
  description: string
  version: string
  agents: { id: string; name: string; description: string }[]
}
const BASE = '/api/research-workflows'
const STORAGE_KEY = 'road2m.earnings.selected-run'
const statusLabels: Record<RunStatus | AgentStatus, string> = {
  queued: 'Queued',
  running: 'In progress',
  completed: 'Complete',
  partial: 'Available results · gaps remain',
  failed: 'Needs attention',
  cancelled: 'Cancelled',
  pending: 'Waiting',
  skipped: 'Skipped',
}
const documentLabels: Record<string, string> = {
  earnings_release: 'Earnings release',
  release: 'Earnings release',
  transcript: 'Call transcript',
  current_filing: 'Latest period filing',
  previous_filing: 'Comparison filing',
  prior_filing: 'Comparison filing',
  presentation: 'Earnings presentation',
  supplement: 'Financial supplement',
  prepared_remarks: 'Prepared remarks',
  shareholder_letter: 'Shareholder letter',
  earnings_8k: 'Earnings announcement filing',
  exhibit: 'Earnings exhibit',
}

function isActive(status?: RunStatus) {
  return status === 'queued' || status === 'running'
}

function storedRun() {
  try {
    return initialEarningsSelection(window.location.search, localStorage.getItem(STORAGE_KEY))
  } catch {
    return initialEarningsSelection(window.location.search, null)
  }
}

function readableStatus(status: string) {
  return status.replaceAll('_', ' ').replace(/^./, (letter) => letter.toUpperCase())
}

function SourceCard({ role, document }: { role: string; document: WorkflowDocument }) {
  const url = sourceUrl(document.url)
  return (
    <article className="earnings-source">
      <div className="earnings-source-heading">
        <h3>{documentLabels[role] || readableStatus(role)}</h3>
        <span className={`earnings-state ${document.source_id ? 'completed' : 'partial'}`}>
          {document.source_id ? 'Saved' : readableStatus(document.status)}
        </span>
      </div>
      <p className="earnings-source-title">
        {url ? (
          <a href={url} target="_blank" rel="noreferrer">
            {document.title || 'Open source'} <span aria-hidden="true">↗</span>
          </a>
        ) : (
          document.title || 'Source not yet available'
        )}
      </p>
      {(document.form || document.period_end) && (
        <p className="earnings-source-meta">
          {[
            document.form,
            document.period_end ? `Period ended ${displayDate(document.period_end)}` : null,
          ]
            .filter(Boolean)
            .join(' · ')}
        </p>
      )}
      {document.reason && <p>{document.reason}</p>}
      {url && <small className="earnings-source-host">{new URL(url).hostname}</small>}
    </article>
  )
}

function Method({ result }: { result: TranscriptResult | ComparisonResult }) {
  return (
    <details className="research-details document-method">
      <summary>Method and limitations</summary>
      <p>{result.method}</p>
      <ul>
        {result.limitations.map((limitation) => (
          <li key={limitation}>{limitation}</li>
        ))}
      </ul>
    </details>
  )
}

export default function EarningsWorkflow({
  onOpenResearch,
  initialWorkflowId,
  embedded = false,
}: {
  onOpenResearch?: (runId: string) => void
  initialWorkflowId?: string
  embedded?: boolean
}) {
  const [ticker, setTicker] = useState('')
  const [history, setHistory] = useState<WorkflowRun[]>([])
  const [selectedId, setSelectedId] = useState(() => initialWorkflowId || storedRun())
  const [selected, setSelected] = useState<WorkflowRun | null>(null)
  const [recipe, setRecipe] = useState<Recipe | null>(null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [pollError, setPollError] = useState('')
  const [researchMessage, setResearchMessage] = useState('')
  const [refreshKey, setRefreshKey] = useState(0)
  const [focusSentenceId, setFocusSentenceId] = useState<string | number>()
  const [focusRequest, setFocusRequest] = useState(0)
  const selectionRef = useRef(selectedId)
  selectionRef.current = selectedId
  const result = selected?.result
  const active = isActive(selected?.status)
  const transcript = result?.analyses?.transcript
  const comparison = result?.analyses?.filing_comparison
  const gaps = [...new Set(result?.gaps || [])]
  const context = result?.context
  const researchRunId = selected?.research_run_id || result?.research_run_id
  const materials = result?.materials || Object.entries(result?.documents || {})
    .filter(([role, document]) => role !== 'prior_filing' && document.status === 'available')
    .map(([role, document]) => ({ ...document, kind: document.kind || role }))
  const collectionNotes = [...new Set([...(result?.material_gaps || []), ...(result?.comparison_gaps || [])])]
  const callUrl = sourceUrl(result?.documents?.transcript?.url)
  const newerCoverage = selected ? newerSourceCoverage(history, selected.id, selected.ticker) : undefined
  const separateReview = embedded && !!initialWorkflowId && !!selected && selected.id !== initialWorkflowId
  const revisedCoverage = separateReview && !!selected?.source_refresh_of

  function jumpToSection(id: string) {
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  function readQuote(sentenceId: string | number) {
    setFocusSentenceId(sentenceId)
    setFocusRequest((value) => value + 1)
  }

  const reassessAction = researchRunId && !active && !!result?.source_ids?.length ? (
    <button className="button" disabled={!!busy} onClick={() => void launchResearch(true)}>
      {busy === 'reassess' ? 'Starting new assessment…' : 'Reassess with saved materials'}
    </button>
  ) : null

  const researchAction =
    researchRunId && onOpenResearch ? (
      <><button className="button button-primary" onClick={() => onOpenResearch(researchRunId)}>
        Open position review
      </button>{reassessAction}</>
    ) : (
      <button
        className="button button-primary"
        disabled={!!busy || active || !result?.source_ids?.length}
        onClick={() => void launchResearch()}
      >
        {busy === 'research' ? 'Starting review…' : 'Review effect on my position'}
      </button>
    )

  function retainRun(run: WorkflowRun) {
    setHistory((items) =>
      [run, ...items.filter((item) => item.id !== run.id)]
        .sort((a, b) => b.created_at.localeCompare(a.created_at))
        .slice(0, 25),
    )
  }

  function chooseRun(run: WorkflowRun) {
    const url = new URL(window.location.href)
    if (!embedded && url.searchParams.has('earnings')) {
      url.searchParams.set('earnings', run.id)
      window.history.replaceState(window.history.state, '', url)
    }
    setSelected(run)
    setSelectedId(run.id)
    selectionRef.current = run.id
    setTicker(run.ticker)
    setError('')
    setPollError('')
    setResearchMessage('')
    setFocusSentenceId(undefined)
    setRefreshKey((value) => value + 1)
  }

  function returnToAssessmentEvidence() {
    if (!initialWorkflowId) return
    selectionRef.current = initialWorkflowId
    setSelected(null)
    setSelectedId(initialWorkflowId)
    setError('')
    setPollError('')
    setResearchMessage('')
    setFocusSentenceId(undefined)
  }

  useEffect(() => {
    const controller = new AbortController()
    void Promise.allSettled([
      apiFetch<{ items: WorkflowRun[] }>(
        `${BASE}/runs?limit=25`,
        {},
        { signal: controller.signal },
      ),
      apiFetch<{ items: Recipe[] }>(`${BASE}/catalog`, {}, { signal: controller.signal }),
    ]).then(([runs, catalog]) => {
      if (controller.signal.aborted) return
      if (runs.status === 'fulfilled') {
        setHistory(runs.value.items)
        if (!selectionRef.current && runs.value.items.length) chooseRun(runs.value.items[0])
      } else
        setError(
          runs.reason instanceof Error
            ? runs.reason.message
            : 'Saved workflows could not be loaded.',
        )
      if (catalog.status === 'fulfilled')
        setRecipe(catalog.value.items.find((item) => item.id === 'earnings') || null)
    })
    return () => controller.abort()
  }, [])

  useEffect(() => {
    try {
      if (embedded) { /* A saved assessment keeps its own selection. */ }
      else if (selectedId) localStorage.setItem(STORAGE_KEY, selectedId)
      else localStorage.removeItem(STORAGE_KEY)
    } catch {
      // Run history remains available when browser storage is disabled.
    }
    if (!selectedId) return
    const controller = new AbortController()
    let timer: number | undefined
    async function poll() {
      try {
        const run = await apiFetch<WorkflowRun>(
          `${BASE}/runs/${encodeURIComponent(selectedId)}`,
          {},
          { signal: controller.signal },
        )
        if (controller.signal.aborted || selectionRef.current !== selectedId) return
        setSelected(run)
        retainRun(run)
        setPollError('')
        if (isActive(run.status)) timer = window.setTimeout(() => void poll(), 2000)
      } catch (cause) {
        if (controller.signal.aborted || selectionRef.current !== selectedId) return
        // A history cleanup can remove the last selected review. Recover only
        // on 404; a temporary server error must not silently switch evidence.
        if (cause instanceof ApiError && cause.status === 404 && !embedded) {
          try {
            const saved = await apiFetch<{ items: WorkflowRun[] }>(`${BASE}/runs?limit=25`, {}, { signal: controller.signal })
            if (controller.signal.aborted || selectionRef.current !== selectedId) return
            setHistory(saved.items)
            const replacement = replacementEarningsSelection(saved.items, selectedId)
            if (replacement) {
              chooseRun(replacement)
              setResearchMessage('The previously selected review is no longer available. Opened the latest saved earnings review.')
            } else {
              setSelected(null)
              setSelectedId('')
              selectionRef.current = ''
              const url = new URL(window.location.href)
              url.searchParams.delete('earnings')
              window.history.replaceState(window.history.state, '', url)
              setPollError('The previously selected earnings review is no longer available. Enter a ticker to collect its latest earnings.')
            }
            return
          } catch (fallbackError) {
            if (controller.signal.aborted || selectionRef.current !== selectedId) return
            setPollError(fallbackError instanceof Error ? fallbackError.message : 'Saved earnings could not be loaded.')
            return
          }
        }
        setPollError(
          cause instanceof Error ? cause.message : 'The workflow could not be refreshed.',
        )
      }
    }
    void poll()
    return () => {
      controller.abort()
      window.clearTimeout(timer)
    }
  }, [selectedId, refreshKey])

  async function start(event?: React.FormEvent, requestedTicker = ticker) {
    event?.preventDefault()
    if (!requestedTicker.trim() || busy) return
    setBusy('create')
    setError('')
    try {
      const run = await apiFetch<WorkflowRun>(
        `${BASE}/runs`,
        {
          method: 'POST',
          body: JSON.stringify({
            ticker: requestedTicker.trim().toUpperCase(),
            workflow: 'earnings',
            namespace: 'real',
          }),
        },
        { mutation: true },
      )
      chooseRun(run)
      retainRun(run)
      setRefreshKey((value) => value + 1)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'The earnings workflow could not start.')
    } finally {
      setBusy('')
    }
  }

  async function refreshFigures() {
    if (!selected || busy || active) return
    const runId = selected.id
    setBusy('refresh-sources')
    setError('')
    try {
      const run = await apiFetch<WorkflowRun>(`${BASE}/runs/${encodeURIComponent(runId)}/refresh-sources`,
        { method: 'POST', body: '{}' }, { mutation: true })
      retainRun(run)
      if (selectionRef.current === runId) chooseRun(run)
    } catch (cause) {
      if (selectionRef.current === runId) setError(cause instanceof Error ? cause.message : 'Missing figures could not be updated.')
    } finally {
      setBusy('')
    }
  }

  async function changeRun(action: 'retry' | 'cancel') {
    if (!selected || busy) return
    const runId = selected.id
    setBusy(action)
    setError('')
    try {
      const run = await apiFetch<WorkflowRun>(
        `${BASE}/runs/${encodeURIComponent(runId)}/${action}`,
        { method: 'POST', body: '{}' },
        { mutation: true },
      )
      retainRun(run)
      if (selectionRef.current === runId) {
        setSelected(run)
        setRefreshKey((value) => value + 1)
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : `The workflow could not ${action}.`)
    } finally {
      setBusy('')
    }
  }

  async function launchResearch(reassess = false) {
    if (!selected || busy) return
    const runId = selected.id
    setBusy(reassess ? 'reassess' : 'research')
    setError('')
    try {
      const handoff = await apiFetch<{ run_id: string; status: string }>(
        `${BASE}/runs/${encodeURIComponent(runId)}/research${reassess ? '?reassess=true' : ''}`,
        { method: 'POST', body: '{}' },
        { mutation: true },
      )
      if (selectionRef.current === runId) {
        setSelected((current) =>
          current ? { ...current, research_run_id: handoff.run_id } : current,
        )
        setResearchMessage(
          handoff.status === 'paused'
            ? 'Research is saved and waiting while the Research Engine is paused.'
            : reassess ? 'A new assessment is saved in Research using these materials. Earlier assessments remain in your research history.' : 'Research is saved in Research with the collected sources attached.',
        )
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Research could not be started.')
    } finally {
      setBusy('')
    }
  }

  return (
    <div className="document-layout earnings-layout">
      <div>
        {!embedded && <div className="earnings-intake-row">
          <form className="earnings-start" onSubmit={(event) => void start(event)}>
            <label className="research-field">
              Company ticker
              <input
                value={ticker}
                onChange={(event) => setTicker(event.target.value.toUpperCase())}
                placeholder="e.g. COST"
                maxLength={15}
                autoCapitalize="characters"
                autoCorrect="off"
                spellCheck={false}
                aria-describedby="earnings-intake-help"
                required
                disabled={busy === 'create'}
              />
            </label>
            <button className="button button-primary" disabled={!ticker.trim() || !!busy}>
              {busy === 'create' ? 'Starting…' : 'Analyze latest earnings'}
            </button>
            <p id="earnings-intake-help">
              Collect the latest call, release, presentations, supplements and related filings.
              Check the numbers against recent quarters and five years of capital spending.
            </p>
          </form>
          {!!history.length && (
            <label className="research-field earnings-saved-select">
              Saved earnings
              <select
                value={selectedId}
                onChange={(event) => {
                  const run = history.find((item) => item.id === event.target.value)
                  if (run) chooseRun(run)
                }}
                disabled={!!busy}
              >
                {!selectedId && <option value="">Choose a saved call</option>}
                {history.map((run) => (
                  <option key={run.id} value={run.id}>
                    {run.ticker} · {run.result?.event?.fiscal_period || displayDate(run.created_at)}
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>
        }
        <ErrorNotice message={error} />
        {pollError && (
          <div className="earnings-refresh-error">
            <ErrorNotice message={pollError} />
            <button className="button" onClick={() => setRefreshKey((value) => value + 1)}>
              Refresh status
            </button>
          </div>
        )}
        {selectedId && !selected && !pollError && (
          <p className="muted-copy" role="status">
            Loading saved workflow…
          </p>
        )}
        {!selected && !selectedId && (
          <section className="earnings-intro">
            <h2>One ticker, a repeatable research process</h2>
            <p>
              Each agent has a defined task. Sources, analysis and progress are saved so you can
              return to the work and retry missing material when it becomes available.
            </p>
            <ol className="earnings-recipe">
              {(
                recipe?.agents || [
                  {
                    id: 'locate',
                    name: 'Locate earnings',
                    description: 'Resolve the company and confirm its latest reporting period.',
                  },
                  {
                    id: 'collect',
                    name: 'Collect the evidence',
                    description: 'Find the call, earnings release, presentation and related reports.',
                  },
                  {
                    id: 'analyze',
                    name: 'Analyze the documents',
                    description: 'Review call tone and changes in management commentary.',
                  },
                  {
                    id: 'connect',
                    name: 'Connect the research',
                    description: 'Bring the evidence into the wider Research Engine.',
                  },
                ]
              ).map((agent, index) => (
                <li key={agent.id}>
                  <span>{String(index + 1).padStart(2, '0')}</span>
                  <div>
                    <h3>{agent.name}</h3>
                    <p>{agent.description}</p>
                  </div>
                </li>
              ))}
            </ol>
            <p className="earnings-intro-note">
              Available earnings materials can be reviewed immediately. Additional reports and
              gaps in historical metrics stay visible in the result.
            </p>
          </section>
        )}
        {selected && (
          <div className="document-results earnings-results">
            <section className="research-card document-result-intro">
              <div className="research-card-heading">
                <div>
                  <p className="earnings-kicker">{selected.ticker} · {embedded ? separateReview ? revisedCoverage ? 'Updated earnings coverage' : 'Separate earnings review' : 'Earnings used in this assessment' : 'Latest earnings'}</p>
                  <h2>{result?.company?.name || selected.ticker}</h2>
                  <p className="document-result-date">
                    {[
                      result?.event?.fiscal_period,
                      result?.event?.earnings_date
                        ? `Reported ${displayDate(result.event.earnings_date)}`
                        : null,
                    ]
                      .filter(Boolean)
                      .join(' · ')}
                  </p>
                </div>
                <span className={`earnings-state ${selected.status}`} role="status">
                  {selected.status === 'partial'
                    ? 'Available results · coverage gaps'
                    : statusLabels[selected.status]}
                </span>
              </div>
              <div className="earnings-availability">
                <span>
                  <strong>Call transcript</strong>{' '}
                  {transcript?.result
                    ? 'ready to read'
                    : active
                      ? 'being collected'
                      : 'unavailable'}
                </span>
                <span>
                  <strong>Earnings materials</strong> {materials.length} collected
                </span>
                <span>
                  <strong>Historical trends</strong>{' '}
                  {result?.trends?.series?.length ? 'ready to explore' : active ? 'being researched' : 'not yet collected'}
                </span>
              </div>
              <ErrorNotice message={selected.error || ''} />
              {!!gaps.length && (
                <div className="earnings-gap-notice">
                  <p>
                    Some evidence or historical comparisons remain unavailable. Available material
                    can be explored below.
                  </p>
                  <details>
                    <summary>Collection details ({gaps.length})</summary>
                    <ul>
                      {gaps.map((gap) => (
                        <li key={gap}>{gap}</li>
                      ))}
                    </ul>
                  </details>
                </div>
              )}
              {transcript?.result && (
                <nav className="earnings-jump-nav" aria-label="Earnings review sections">
                  <button onClick={() => jumpToSection('earnings-shareholder-brief')}>
                    Highlights & trends
                  </button>
                  <button onClick={() => jumpToSection('earnings-call-reader')}>
                    Explore the call & transcript
                  </button>
                  {comparison?.result && (
                    <button onClick={() => jumpToSection('earnings-filings')}>
                      Filing changes
                    </button>
                  )}
                  <button
                    onClick={() => {
                      const sources = document.getElementById(
                        'earnings-sources',
                      ) as HTMLDetailsElement | null
                      if (sources) sources.open = true
                      jumpToSection('earnings-sources')
                    }}
                  >
                    Sources & progress
                  </button>
                </nav>
              )}
              <div className="earnings-actions">
                {newerCoverage && <button className="button" disabled={!!busy} onClick={() => chooseRun(newerCoverage)}>Open updated figures</button>}
                {separateReview && <button className="button" disabled={!!busy} onClick={returnToAssessmentEvidence}>Return to assessment evidence</button>}
                {active && (
                  <button
                    className="button"
                    disabled={!!busy}
                    onClick={() => void changeRun('cancel')}
                  >
                    {busy === 'cancel' ? 'Cancelling…' : 'Cancel workflow'}
                  </button>
                )}
                {!researchRunId && selected.version === recipe?.version && ['partial', 'failed', 'cancelled'].includes(selected.status) && (
                  <button
                    className="button"
                    disabled={!!busy}
                    onClick={() => void changeRun('retry')}
                  >
                    {busy === 'retry' ? 'Retrying…' : 'Retry incomplete work'}
                  </button>
                )}
                {!active && (
                  <button className="button" disabled={!!busy || !result?.trends} onClick={() => void refreshFigures()}>
                    {busy === 'refresh-sources' ? 'Updating missing figures…' : 'Update missing figures'}
                  </button>
                )}
                {!active && (
                  <button
                    className="button"
                    disabled={!!busy}
                    onClick={() => void start(undefined, selected.ticker)}
                  >
                    {busy === 'create' ? 'Starting…' : 'Refresh materials & trends'}
                  </button>
                )}
                {!active && result && (
                  <button
                    className="button"
                    onClick={() =>
                      downloadJson(selected, `${selected.ticker}-earnings-${selected.id}.json`)
                    }
                  >
                    Export results
                  </button>
                )}
              </div>
              {selected.source_refresh_of && <p className="earnings-trend-note">This is a separate source-coverage revision. The earlier investment decision retains its original evidence.</p>}
              {separateReview && !selected.source_refresh_of && <p className="earnings-trend-note">This is a separate earnings review. The investment decision retains the earnings evidence used in its original assessment.</p>}
            </section>
            {transcript?.result && (
              <>
                <EarningsBrief
                  result={transcript.result}
                  ticker={selected.ticker}
                  positions={context?.portfolio?.positions}
                  trends={result?.trends}
                  trendsLoading={active && !result?.trends}
                  onRead={readQuote}
                  action={researchAction}
                />
                {researchMessage && (
                  <p className="muted-copy" role="status">
                    {researchMessage}
                  </p>
                )}
                <section
                  className="earnings-analysis"
                  id="earnings-call-reader"
                  aria-label="Explore the earnings call"
                >
                  <div className="earnings-analysis-heading">
                    <p>
                      Follow a business theme, review negative language with the reply, or read the
                      full transcript.
                    </p>
                    {callUrl && (
                      <p className="earnings-call-source">
                        Saved transcript from{' '}
                        <a href={callUrl} target="_blank" rel="noreferrer">
                          {new URL(callUrl).hostname} ↗
                        </a>
                      </p>
                    )}
                  </div>
                  <TranscriptView
                    analysisId={transcript.id}
                    key={transcript.id}
                    result={transcript.result}
                    ticker={selected.ticker}
                    fiscalPeriod={result?.event?.fiscal_period}
                    earningsDate={result?.event?.earnings_date}
                    trends={result?.trends}
                    transcriptDownloadUrl={`/api/document-analysis/history/${encodeURIComponent(transcript.id)}/transcript.txt`}
                    focusSentenceId={focusSentenceId}
                    focusRequest={focusRequest}
                  />
                  <Method result={transcript.result} />
                </section>
              </>
            )}
            {comparison?.result && (
              <section
                className="earnings-analysis"
                id="earnings-filings"
                aria-label="Filing commentary comparison"
              >
                <div className="earnings-analysis-heading">
                  <h2>Filing commentary comparison</h2>
                  <p>{comparison.result.summary}</p>
                  {result?.comparison_basis && (
                    <p className="muted-copy">{result.comparison_basis}</p>
                  )}
                  <Method result={comparison.result} />
                </div>
                <ComparisonView key={comparison.id} result={comparison.result} />
              </section>
            )}
            <details
              className="research-card research-details earnings-workflow-secondary"
              id="earnings-sources"
              open={!transcript?.result}
            >
              <summary>
                Sources & workflow details{' '}
                <span>
                  {result?.source_ids?.length || 0} archived sources ·{' '}
                  {displayDate(selected.updated_at)}
                </span>
              </summary>
              <details
                className="research-card research-details earnings-agents"
                open={active || selected.status === 'failed' || !result}
              >
                <summary>
                  Agent progress{' '}
                  <span>
                    {selected.steps.filter((step) => step.status === 'completed').length} of{' '}
                    {selected.steps.length} complete
                  </span>
                </summary>
                <ol>
                  {selected.steps.map((step, index) => (
                    <li key={step.id}>
                      <span className={`earnings-step-number ${step.status}`} aria-hidden="true">
                        {step.status === 'completed' ? '✓' : index + 1}
                      </span>
                      <div>
                        <div className="earnings-step-heading">
                          <h3>{step.name}</h3>
                          <span className={`earnings-state ${step.status}`}>
                            {statusLabels[step.status]}
                          </span>
                        </div>
                        {step.error && <p>{step.error}</p>}
                        {step.attempts > 1 && <small>{step.attempts} attempts</small>}
                      </div>
                    </li>
                  ))}
                </ol>
              </details>
              {!!materials.length && (
                <section className="research-card">
                  <div className="research-card-heading">
                    <h2>Latest earnings materials</h2>
                    <span className="muted-copy">
                      {materials.length} collected for this event
                    </span>
                  </div>
                  <div className="earnings-sources">
                    {materials.map((document, index) => (
                      <SourceCard key={document.source_id || index} role={document.kind || 'material'} document={document} />
                    ))}
                  </div>
                </section>
              )}
              {!!collectionNotes.length && (
                <details className="research-card research-details">
                  <summary>Additional material and optional filing comparison</summary>
                  <p>Periodic filings add detail when available; they do not hold up the earnings review.</p>
                  <ul>{collectionNotes.map(note => <li key={note}>{note}</li>)}</ul>
                </details>
              )}
            </details>
            {context && (
              <details className="research-card research-details earnings-context earnings-workflow-secondary">
                <summary>Related Research Engine context</summary>
                <p>
                  Context from your saved Research Engine archive. Historical records are not a
                  current trading signal.
                </p>
                <dl className="earnings-context-counts">
                  {context.research && (
                    <div>
                      <dt>Research runs</dt>
                      <dd>{context.research.items?.length || 0}</dd>
                    </div>
                  )}
                  {context.congress && (
                    <div>
                      <dt>Congress disclosures</dt>
                      <dd>
                        {context.congress.available === false
                          ? 'Unavailable'
                          : (context.congress.total ?? context.congress.items?.length ?? 0)}
                      </dd>
                    </div>
                  )}
                  {context.library && (
                    <div>
                      <dt>Library findings</dt>
                      <dd>{context.library.items?.length || 0}</dd>
                    </div>
                  )}
                  {context.portfolio && (
                    <div>
                      <dt>Portfolio observations</dt>
                      <dd>{context.portfolio.positions?.length || 0}</dd>
                    </div>
                  )}
                  {context.watchlist && (
                    <div>
                      <dt>Watchlist entries</dt>
                      <dd>{context.watchlist.items?.length || 0}</dd>
                    </div>
                  )}
                  {context.strategies && (
                    <div>
                      <dt>Saved strategy studies</dt>
                      <dd>
                        {context.strategies.saved_studies ?? context.strategies.items?.length ?? 0}
                      </dd>
                    </div>
                  )}
                </dl>
                {context.congress?.snapshot_date && (
                  <p className="muted-copy">
                    Congress archive as of {displayDate(context.congress.snapshot_date)}.
                  </p>
                )}
                {context.congress?.note && <p className="muted-copy">{context.congress.note}</p>}
                {context.strategies?.note && (
                  <p className="muted-copy">{context.strategies.note}</p>
                )}
                {!!context.research?.items?.length && onOpenResearch && (
                  <ul className="earnings-related-runs">
                    {context.research.items.slice(0, 5).map((run) => (
                      <li key={run.id}>
                        <button className="button" onClick={() => onOpenResearch(run.id)}>
                          {run.request || `${run.ticker || selected.ticker} research`}
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </details>
            )}
            {!transcript?.result && !active && !!result?.source_ids?.length && (
              <section className="research-card earnings-handoff">
                <div>
                  <h2>Continue in the Research Engine</h2>
                  <p>
                    Use the archived sources to investigate the business implications and test the
                    findings against other evidence.
                  </p>
                </div>
                <div className="earnings-actions">
                  {researchRunId && onOpenResearch ? (
                    <button
                      className="button button-primary"
                      onClick={() => onOpenResearch(researchRunId)}
                    >
                      Open research
                    </button>
                  ) : (
                    <button
                      className="button button-primary"
                      disabled={!!busy}
                      onClick={() => void launchResearch()}
                    >
                      {busy === 'research' ? 'Starting research…' : 'Start deeper research'}
                    </button>
                  )}
                  {reassessAction}
                </div>
                {researchMessage && <p role="status">{researchMessage}</p>}
              </section>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
