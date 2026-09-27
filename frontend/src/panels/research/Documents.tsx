import { useEffect, useId, useRef, useState } from 'react'
import { apiFetch } from '../../api'
import { downloadJson, displayDate, ErrorNotice } from './shared'
import {
  TranscriptView,
  ComparisonView,
  type TranscriptResult,
  type ComparisonResult,
} from './DocumentViews'
import EarningsWorkflow from './EarningsWorkflow'
import './research.css'
import './documents.css'

type SavedAnalysis = {
  id: string
  kind: 'transcript' | 'filing_comparison'
  title: string
  ticker?: string
  period?: string
  previous_period?: string
  current_period?: string
  created_at: string
  summary?: string
  result: TranscriptResult | ComparisonResult
  inputs: { text?: string; previous_text?: string; current_text?: string }
}
type Mode = 'transcript' | 'filing_comparison'
const BASE = '/api/document-analysis'

function FileField({
  label,
  text,
  onText,
  onError,
}: {
  label: string
  text: string
  onText: (text: string) => void
  onError: (error: string) => void
}) {
  const inputId = useId()
  const [importing, setImporting] = useState(false)
  const [filename, setFilename] = useState('')
  async function importFile(file?: File) {
    if (!file) return
    if (file.size > 5_000_000) {
      onError('Choose a file no larger than 5 MB.')
      return
    }
    setImporting(true)
    onError('')
    try {
      const content = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader()
        reader.onload = () => resolve(String(reader.result).split(',')[1])
        reader.onerror = () => reject(new Error('The file could not be read.'))
        reader.readAsDataURL(file)
      })
      const result = await apiFetch<{ text: string; filename: string; warnings: string[] }>(
        `${BASE}/import`,
        { method: 'POST', body: JSON.stringify({ filename: file.name, content_base64: content }) },
        { mutation: true },
      )
      onText(result.text)
      setFilename(result.filename)
      if (result.warnings?.length) onError(result.warnings.join(' '))
    } catch (error) {
      onError(error instanceof Error ? error.message : 'Import failed.')
    } finally {
      setImporting(false)
    }
  }
  return (
    <div className="research-field document-source-field">
      <label className="document-source-label" htmlFor={`${inputId}-text`}>
        {label}
      </label>
      <div className="document-upload">
        <input
          type="file"
          aria-label={`Import ${label.toLowerCase()}`}
          aria-describedby={`${inputId}-format`}
          accept=".txt,.text,.html,.htm,.pdf"
          onChange={(event) => {
            void importFile(event.target.files?.[0])
            event.target.value = ''
          }}
          disabled={importing}
        />
        <small id={`${inputId}-format`}>TXT, HTML or PDF · up to 5 MB</small>
      </div>
      <textarea
        id={`${inputId}-text`}
        value={text}
        onChange={(event) => {
          onText(event.target.value)
          setFilename('')
        }}
        placeholder={
          label === 'Earnings call transcript'
            ? 'Or paste the transcript here, including speaker names…'
            : 'Or paste the full filing or the narrative sections to compare…'
        }
        disabled={importing}
        aria-describedby={`${inputId}-status`}
        required
      />
      <small id={`${inputId}-status`} role="status">
        {importing
          ? 'Reading document…'
          : filename
            ? `${filename} · ${text.length.toLocaleString()} characters`
            : text.length
              ? `${text.length.toLocaleString()} characters`
              : 'Files and analyses stay on this computer.'}
      </small>
    </div>
  )
}

function ManualDocuments({ onResearch }: { onResearch: (question: string) => void }) {
  const [mode, setMode] = useState<Mode>('transcript')
  const [title, setTitle] = useState('')
  const [ticker, setTicker] = useState('')
  const [period, setPeriod] = useState('')
  const [previousPeriod, setPreviousPeriod] = useState('')
  const [text, setText] = useState('')
  const [previous, setPrevious] = useState('')
  const [current, setCurrent] = useState('')
  const [history, setHistory] = useState<SavedAnalysis[]>([])
  const [selected, setSelected] = useState<SavedAnalysis | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [sourceExpanded, setSourceExpanded] = useState(true)
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([])
  const requestRef = useRef(0)
  const resultRef = useRef<HTMLDivElement>(null)
  const sourceFormRef = useRef<HTMLFormElement>(null)
  function researchFindings() {
    if (!selected) return
    const result = selected.result
    const evidence =
      selected.kind === 'transcript'
        ? (result as TranscriptResult).sentences
            .filter((sentence) => sentence.sentiment.label !== 'neutral')
            .slice(0, 8)
            .map((sentence) => `${sentence.speaker || 'Speaker unknown'}: ${sentence.text}`)
            .join('\n')
        : (result as ComparisonResult).changes
            .slice(0, 6)
            .map((change) => `${change.section}: Before: ${change.before}\nAfter: ${change.after}`)
            .join('\n')
    onResearch(
      `Investigate these saved document findings for ${selected.ticker || selected.title || 'the company'} (${selected.period || `${selected.previous_period || 'previous'} to ${selected.current_period || 'current'} quarter`}). Treat this local NLP analysis as heuristic evidence requiring verification, not an investment conclusion.\nAnalysis ID: ${selected.id}\nMethod: ${result.method}\nSummary: ${result.summary}\nEvidence:\n${evidence.slice(0, 9000)}\nAssess material implications and identify corroborating or contradictory primary sources.`,
    )
  }
  async function loadHistory() {
    const data = await apiFetch<{ items: SavedAnalysis[] }>(`${BASE}/history`)
    setHistory(data.items)
  }
  useEffect(() => {
    void loadHistory().catch((error) => setError(error.message))
  }, [])
  async function analyze(event: React.FormEvent) {
    event.preventDefault()
    const request = ++requestRef.current
    setBusy(true)
    setError('')
    const input =
      mode === 'transcript'
        ? { title, ticker, period, text }
        : {
            title,
            ticker,
            previous_period: previousPeriod,
            current_period: period,
            previous_text: previous,
            current_text: current,
          }
    try {
      const result = await apiFetch<SavedAnalysis>(
        `${BASE}/${mode === 'transcript' ? 'transcript' : 'compare'}`,
        { method: 'POST', body: JSON.stringify(input) },
        { mutation: true },
      )
      if (request !== requestRef.current) return
      setSelected(result)
      setSourceExpanded(false)
      await loadHistory()
      window.setTimeout(() => {
        resultRef.current?.focus({ preventScroll: true })
        resultRef.current?.scrollIntoView({ block: 'start' })
      }, 0)
    } catch (error) {
      if (request === requestRef.current)
        setError(error instanceof Error ? error.message : 'Analysis failed.')
    } finally {
      if (request === requestRef.current) setBusy(false)
    }
  }
  async function openSaved(id: string) {
    const request = ++requestRef.current
    setBusy(true)
    setError('')
    try {
      const saved = await apiFetch<SavedAnalysis>(`${BASE}/history/${encodeURIComponent(id)}`)
      if (request === requestRef.current) {
        setSelected(saved)
        setSourceExpanded(false)
        setMode(saved.kind)
        setTitle(saved.title || '')
        setTicker(saved.ticker || '')
        setPeriod(saved.kind === 'transcript' ? saved.period || '' : saved.current_period || '')
        setPreviousPeriod(saved.previous_period || '')
        setText(saved.inputs?.text || '')
        setPrevious(saved.inputs?.previous_text || '')
        setCurrent(saved.inputs?.current_text || '')
        window.setTimeout(() => {
          resultRef.current?.focus({ preventScroll: true })
          resultRef.current?.scrollIntoView({ block: 'start' })
        }, 0)
      }
    } catch (error) {
      if (request === requestRef.current)
        setError(error instanceof Error ? error.message : 'Saved analysis could not be opened.')
    } finally {
      if (request === requestRef.current) setBusy(false)
    }
  }
  function changeMode(next: Mode) {
    if (next === mode) return
    setMode(next)
    setSelected(null)
    setSourceExpanded(true)
    setError('')
  }
  function resetAnalysis() {
    setSelected(null)
    setTitle('')
    setTicker('')
    setPeriod('')
    setPreviousPeriod('')
    setText('')
    setPrevious('')
    setCurrent('')
    setSourceExpanded(true)
    setError('')
    window.setTimeout(
      () => sourceFormRef.current?.querySelector<HTMLTextAreaElement>('textarea')?.focus(),
      0,
    )
  }
  const modes: { id: Mode; label: string }[] = [
    { id: 'transcript', label: 'Earnings calls' },
    { id: 'filing_comparison', label: 'Filing comparison' },
  ]
  return (
    <div className="document-manual">
      <div className="document-manual-heading">
        <p>Analyze documents you already have. Import a file or paste its text.</p>
        {selected && (
          <button className="button" onClick={resetAnalysis} disabled={busy}>
            New analysis
          </button>
        )}
      </div>
      <div className="document-tabs" role="tablist" aria-label="Document analysis type">
        {modes.map((item, index) => (
          <button
            key={item.id}
            id={`document-tab-${item.id}`}
            ref={(element) => {
              tabRefs.current[index] = element
            }}
            type="button"
            role="tab"
            aria-selected={mode === item.id}
            aria-controls={`document-panel-${item.id}`}
            tabIndex={mode === item.id ? 0 : -1}
            onClick={() => changeMode(item.id)}
            onKeyDown={(event) => {
              let next: number | undefined
              if (event.key === 'ArrowRight' || event.key === 'ArrowLeft')
                next = (index + 1) % modes.length
              if (event.key === 'Home') next = 0
              if (event.key === 'End') next = modes.length - 1
              if (next !== undefined) {
                event.preventDefault()
                changeMode(modes[next].id)
                tabRefs.current[next]?.focus()
              }
            }}
            disabled={busy}
          >
            {item.label}
          </button>
        ))}
      </div>
      <ErrorNotice message={error} />
      <div className="document-layout">
        <div role="tabpanel" id={`document-panel-${mode}`} aria-labelledby={`document-tab-${mode}`}>
          {selected && (
            <div className="document-source-summary">
              <span>
                Source {selected.kind === 'transcript' ? 'transcript' : 'filings'} saved with this
                analysis
              </span>
              <button
                className="button"
                aria-expanded={sourceExpanded}
                aria-controls="document-source-form"
                onClick={() => setSourceExpanded(!sourceExpanded)}
                disabled={busy}
              >
                {sourceExpanded ? 'Hide source' : 'Edit source'}
              </button>
            </div>
          )}
          <form
            id="document-source-form"
            ref={sourceFormRef}
            className="document-form"
            onSubmit={analyze}
            hidden={!sourceExpanded}
          >
            <fieldset disabled={busy}>
              {mode === 'transcript' ? (
                <FileField
                  key={`${selected?.id || 'draft'}-transcript`}
                  label="Earnings call transcript"
                  text={text}
                  onText={setText}
                  onError={setError}
                />
              ) : (
                <>
                  <p className="document-form-help">
                    Add consecutive filings or matching narrative sections. Financial tables and
                    number-only changes are excluded.
                  </p>
                  <div className="research-grid">
                    <FileField
                      key={`${selected?.id || 'draft'}-previous`}
                      label="Previous filing"
                      text={previous}
                      onText={setPrevious}
                      onError={setError}
                    />
                    <FileField
                      key={`${selected?.id || 'draft'}-current`}
                      label="Current filing"
                      text={current}
                      onText={setCurrent}
                      onError={setError}
                    />
                  </div>
                </>
              )}
              <details className="document-metadata">
                <summary>
                  Add company and period <span>Optional</span>
                </summary>
                <div className="research-fields">
                  <label className="research-field">
                    Analysis title
                    <input
                      value={title}
                      onChange={(event) => setTitle(event.target.value)}
                      placeholder="e.g. Acme Q2 earnings"
                    />
                  </label>
                  <label className="research-field">
                    Ticker
                    <input
                      value={ticker}
                      onChange={(event) => setTicker(event.target.value.toUpperCase())}
                      placeholder="e.g. ACME"
                    />
                  </label>
                  {mode === 'filing_comparison' && (
                    <label className="research-field">
                      Previous period
                      <input
                        value={previousPeriod}
                        onChange={(event) => setPreviousPeriod(event.target.value)}
                        placeholder="e.g. Q1 2026"
                      />
                    </label>
                  )}
                  <label className="research-field">
                    {mode === 'transcript' ? 'Reporting period' : 'Current period'}
                    <input
                      value={period}
                      onChange={(event) => setPeriod(event.target.value)}
                      placeholder="e.g. Q2 2026"
                    />
                  </label>
                </div>
              </details>
              <div className="document-form-action">
                <button
                  className="button button-primary"
                  disabled={
                    busy ||
                    (mode === 'transcript' ? !text.trim() : !previous.trim() || !current.trim())
                  }
                >
                  {busy
                    ? 'Analyzing…'
                    : mode === 'transcript'
                      ? 'Analyze transcript'
                      : 'Compare commentary'}
                </button>
                <p>
                  {mode === 'transcript'
                    ? 'Find tone, people and themes, with the source sentences.'
                    : 'Review added, removed and revised commentary.'}
                </p>
              </div>
            </fieldset>
          </form>
          {selected && (
            <div
              ref={resultRef}
              className="document-results"
              tabIndex={-1}
              aria-label="Analysis results"
            >
              <section className="research-card document-result-intro">
                <div className="research-card-heading">
                  <div>
                    <h2>
                      {selected.title ||
                        (selected.kind === 'transcript'
                          ? 'Transcript analysis'
                          : 'Filing comparison')}
                    </h2>
                    <p className="document-result-date">
                      {[
                        selected.ticker,
                        selected.period || selected.current_period,
                        `Saved ${displayDate(selected.created_at)}`,
                      ]
                        .filter(Boolean)
                        .join(' · ')}
                    </p>
                  </div>
                  <button
                    className="button"
                    onClick={() =>
                      downloadJson(
                        selected,
                        `${selected.ticker || 'research'}-${selected.kind}-${selected.id}.json`,
                      )
                    }
                  >
                    Export analysis
                  </button>
                </div>
                <p>{selected.result.summary}</p>
                <button className="button" onClick={researchFindings}>
                  Research these findings
                </button>
                <details className="research-details document-method">
                  <summary>Method and limitations</summary>
                  <p>{selected.result.method}</p>
                  <ul>
                    {selected.result.limitations.map((limitation) => (
                      <li key={limitation}>{limitation}</li>
                    ))}
                  </ul>
                </details>
              </section>
              {selected.kind === 'transcript' ? (
                <TranscriptView
                  analysisId={selected.id}
                  key={selected.id}
                  result={selected.result as TranscriptResult}
                  ticker={selected.ticker}
                  transcriptDownloadUrl={`${BASE}/history/${encodeURIComponent(selected.id)}/transcript.txt`}
                />
              ) : (
                <ComparisonView key={selected.id} result={selected.result as ComparisonResult} />
              )}
            </div>
          )}
        </div>
        <aside className="document-history" aria-label="Saved document analyses">
          <h2>
            Saved analyses <span>{history.length || ''}</span>
          </h2>
          {history.length ? (
            <ul>
              {history.map((item) => (
                <li key={item.id}>
                  <button
                    aria-pressed={selected?.id === item.id}
                    onClick={() => void openSaved(item.id)}
                    disabled={busy}
                  >
                    <strong>
                      {item.title ||
                        item.ticker ||
                        (item.kind === 'transcript' ? 'Earnings call' : 'Filing comparison')}
                    </strong>
                    <small>
                      {item.kind === 'transcript' ? 'Earnings call' : 'Filing comparison'} ·{' '}
                      {item.period || item.current_period || displayDate(item.created_at)}
                    </small>
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p>
              Each analysis is saved here automatically. Return to it later or export the findings.
            </p>
          )}
        </aside>
      </div>
    </div>
  )
}

export default function Documents({
  onResearch,
  onOpenResearch,
}: {
  onResearch: (question: string) => void
  onOpenResearch?: (runId: string) => void
}) {
  const [mode, setMode] = useState<'earnings' | 'manual'>('earnings')
  const modeRefs = useRef<(HTMLButtonElement | null)[]>([])
  const modes = [
    { id: 'earnings' as const, label: 'Latest earnings' },
    { id: 'manual' as const, label: 'Manual documents' },
  ]
  return (
    <div className="research-page documents-page">
      <header className="workspace-header">
        <div>
          <h1>Earnings research</h1>
          <p>Start with a ticker. Review its earnings materials, historical trends and complete call.</p>
        </div>
      </header>
      <div className="document-tabs" role="tablist" aria-label="Research workflow">
        {modes.map((item, index) => (
          <button
            key={item.id}
            id={`earnings-mode-${item.id}`}
            ref={(element) => {
              modeRefs.current[index] = element
            }}
            role="tab"
            aria-selected={mode === item.id}
            aria-controls={`earnings-panel-${item.id}`}
            tabIndex={mode === item.id ? 0 : -1}
            onClick={() => setMode(item.id)}
            onKeyDown={(event) => {
              let next: number | undefined
              if (event.key === 'ArrowRight' || event.key === 'ArrowLeft')
                next = (index + 1) % modes.length
              if (event.key === 'Home') next = 0
              if (event.key === 'End') next = modes.length - 1
              if (next !== undefined) {
                event.preventDefault()
                setMode(modes[next].id)
                modeRefs.current[next]?.focus()
              }
            }}
          >
            {item.label}
          </button>
        ))}
      </div>
      <div
        role="tabpanel"
        id="earnings-panel-earnings"
        aria-labelledby="earnings-mode-earnings"
        hidden={mode !== 'earnings'}
      >
        <EarningsWorkflow onOpenResearch={onOpenResearch} />
      </div>
      <div
        role="tabpanel"
        id="earnings-panel-manual"
        aria-labelledby="earnings-mode-manual"
        hidden={mode !== 'manual'}
      >
        <ManualDocuments onResearch={onResearch} />
      </div>
    </div>
  )
}
