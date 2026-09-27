import { useEffect, useRef, useState } from 'react'
import { apiFetch } from '../../api'
import {
  type Backtest,
  type BacktestRequest,
  getCatalog,
  humanize,
  type LabCatalog,
  textValue,
} from './labs'
import {
  displayDate,
  downloadJson,
  EmptyState,
  ErrorNotice,
  formatNumber,
  percent,
  ResearchHeader,
} from './shared'
import StrategyResults, { EquityCurve, RecordTable, EvidenceSection } from './StrategyResults'
import './research.css'

type ResearchCase = {
  id: string
  label: string
  metrics: Record<string, number>
  curve: { date: string; nav: number; benchmark: number | null }[]
  periods: Record<string, unknown>
  diagnostics: Record<string, unknown>
  tradeCaseId?: string
}
type ResearchSnapshot = {
  view: {
    title: string
    summary: unknown
    asOf: string
    notes: unknown[]
    findings: Record<string, unknown>[]
    cases: ResearchCase[]
  }
  [key: string]: unknown
}
const sizingLabel = (value: string) =>
  ({
    fixed_1: '1% per purchase',
    fixed_2: '2% per purchase',
    fixed_5: '5% per purchase',
    quarter_kelly: '¼ Kelly allocation',
  })[value] || humanize(value)

const DEFAULT_REQUEST: BacktestRequest = {
  strategy: 'rotation_20',
  politician: 'all',
  chamber: 'all',
  start: '2020-01-02',
  end: '2026-09-04',
  sizing: 'fixed_2',
  delay: 1,
  add_purchases: true,
  short_holding: false,
  exit_price: 'next_low',
  fee_bps: 10,
}

function HistoricResearch({ catalog }: { catalog: LabCatalog }) {
  const [id, setId] = useState(() => catalog.research.find((item) => item.available)?.id || '')
  const [snapshot, setSnapshot] = useState<ResearchSnapshot | null>(null)
  const [caseId, setCaseId] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [date, setDate] = useState('')
  const [ledger, setLedger] = useState<Record<string, unknown> | null>(null)
  const [ledgerLoading, setLedgerLoading] = useState(false)
  useEffect(() => {
    if (!id) return
    const controller = new AbortController()
    setLoading(true)
    setError('')
    setSnapshot(null)
    setLedger(null)
    apiFetch<ResearchSnapshot>(
      `/api/labs/research/${encodeURIComponent(id)}`,
      {},
      { signal: controller.signal },
    )
      .then((result) => {
        setSnapshot(result)
        setCaseId(result.view.cases[0]?.id || '')
        setDate(result.view.cases[0]?.curve.at(-1)?.date || '')
      })
      .catch((error) => {
        if (error.name !== 'AbortError') setError(error.message)
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [id])
  const selected = snapshot?.view.cases.find((item) => item.id === caseId)
  async function dailyTrades() {
    if (!selected?.tradeCaseId || !date) return
    setLedgerLoading(true)
    setError('')
    try {
      setLedger(
        await apiFetch<Record<string, unknown>>(
          `/api/labs/research-trades?${new URLSearchParams({ case: selected.tradeCaseId, date })}`,
        ),
      )
    } catch (error) {
      setError(error instanceof Error ? error.message : 'Trade ledger unavailable.')
    } finally {
      setLedgerLoading(false)
    }
  }
  return (
    <>
      <section className="research-card">
        <div className="research-fields">
          <label className="research-field">
            Saved research study
            <select value={id} onChange={(event) => setId(event.target.value)}>
              {catalog.research.map((item) => (
                <option key={item.id} value={item.id} disabled={!item.available}>
                  {item.label}
                  {!item.available ? ' · unavailable' : ''}
                </option>
              ))}
            </select>
          </label>
          {snapshot && (
            <button className="button" onClick={() => downloadJson(snapshot, `${id}.json`)}>
              Export study
            </button>
          )}
        </div>
        <p>
          Earlier experiments retain their original execution rules, pricing policies and coverage.
          Select a study to compare cases and inspect its underlying assumptions.
        </p>
        <ErrorNotice message={error} />
        {loading && <p role="status">Loading saved research…</p>}
        {snapshot && (
          <>
            <h2>{snapshot.view.title}</h2>
            <p>{textValue(snapshot.view.summary)}</p>
            {snapshot.view.asOf && <p>Snapshot {displayDate(snapshot.view.asOf)}</p>}
            <details className="research-details" open>
              <summary>Study notes</summary>
              <ul>
                {snapshot.view.notes.map((note, index) => (
                  <li key={index}>{textValue(note)}</li>
                ))}
              </ul>
            </details>
            {!!snapshot.view.cases.length && (
              <>
                <h3>Compare cases</h3>
                <div className="research-table-wrap">
                  <table className="research-table">
                    <thead>
                      <tr>
                        <th>Case</th>
                        <th>Return</th>
                        <th>CAGR</th>
                        <th>Drawdown</th>
                        <th>Sharpe</th>
                        <th>SPY</th>
                      </tr>
                    </thead>
                    <tbody>
                      {snapshot.view.cases.map((item) => (
                        <tr key={item.id}>
                          <td>
                            <button
                              onClick={() => {
                                setCaseId(item.id)
                                setDate(item.curve.at(-1)?.date || '')
                                setLedger(null)
                              }}
                            >
                              {item.label}
                            </button>
                          </td>
                          <td>{percent(item.metrics.totalReturn)}</td>
                          <td>{percent(item.metrics.cagr)}</td>
                          <td>{percent(item.metrics.maxDrawdown)}</td>
                          <td>{formatNumber(item.metrics.sharpe, 2)}</td>
                          <td>{percent(item.metrics.benchmarkReturn)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
            {!!snapshot.view.findings.length && (
              <>
                <h3>Recorded findings</h3>
                <RecordTable records={snapshot.view.findings} />
              </>
            )}
          </>
        )}
      </section>
      {selected && (
        <section className="research-card">
          <h2>{selected.label}</h2>
          {selected.curve.length > 0 && <EquityCurve key={selected.id} curve={selected.curve} />}
          <details className="research-details" open>
            <summary>Case diagnostics and periods</summary>
            <div className="research-grid">
              {[selected.metrics, selected.diagnostics, selected.periods]
                .filter((value) => Object.keys(value || {}).length)
                .map((values, index) => (
                  <dl key={index} className="research-detail-list">
                    {Object.entries(values).map(([key, value]) => (
                      <div key={key} style={{ display: 'contents' }}>
                        <dt>{humanize(key)}</dt>
                        <dd>
                          {typeof value === 'number'
                            ? /return|cagr|drawdown|volatility|invested/i.test(key)
                              ? percent(value)
                              : formatNumber(value, 2)
                            : textValue(value)}
                        </dd>
                      </div>
                    ))}
                  </dl>
                ))}
            </div>
          </details>
          {selected.tradeCaseId && (
            <>
              <div className="research-fields">
                <label className="research-field">
                  Trading session
                  <input
                    type="date"
                    value={date}
                    onInput={(event) => setDate(event.currentTarget.value)}
                    onChange={(event) => setDate(event.target.value)}
                    min={selected.curve[0]?.date}
                    max={selected.curve.at(-1)?.date}
                  />
                </label>
                <button
                  className="button"
                  disabled={ledgerLoading || !date}
                  onClick={() => void dailyTrades()}
                >
                  {ledgerLoading ? 'Loading trades…' : 'Inspect daily trades'}
                </button>
              </div>
              {ledger && (
                <>
                  <p>
                    {Object.entries(ledger)
                      .filter(([, value]) => typeof value !== 'object')
                      .map(([key, value]) => `${humanize(key)}: ${textValue(value)}`)
                      .join(' · ')}
                  </p>
                  {Object.entries(ledger)
                    .filter(([, value]) => Array.isArray(value))
                    .map(([key, value]) => (
                      <div key={key}>
                        <h3>{humanize(key)}</h3>
                        <RecordTable
                          records={(value as unknown[]).map((item) =>
                            item && typeof item === 'object'
                              ? (item as Record<string, unknown>)
                              : { value: item },
                          )}
                        />
                      </div>
                    ))}
                </>
              )}
            </>
          )}
        </section>
      )}
      {snapshot && (
        <section className="research-card">
          <h2>Study evidence and diagnostics</h2>
          {[
            'plan',
            'universe',
            'rules',
            'executionAssumptions',
            'definitions',
            'selection',
            'candidates',
            'uncertainty',
            'sensitivity',
            'windowSummary',
            'verification',
            'coverage',
            'sourceCoverage',
            'integrity',
            'provenance',
            'secondary',
            'eventReturns',
            'robustness',
            'originalCases',
            'rotationCases',
            'archivedComparison',
          ]
            .filter((key) => snapshot[key] != null)
            .map((key) => (
              <EvidenceSection key={key} title={humanize(key)} value={snapshot[key]} />
            ))}
        </section>
      )}
      {snapshot && (
        <details className="research-card research-details">
          <summary>Original study record</summary>
          <pre className="research-raw">{JSON.stringify(snapshot, null, 2)}</pre>
        </details>
      )}
    </>
  )
}

export default function Strategies() {
  const resultRef = useRef<HTMLElement>(null)
  const editedFields = useRef(new Set<keyof BacktestRequest>())
  const [catalog, setCatalog] = useState<LabCatalog | null>(null)
  const [request, setRequest] = useState<BacktestRequest>(DEFAULT_REQUEST)
  const [history, setHistory] = useState<Backtest[]>([])
  const [selected, setSelected] = useState<Backtest | null>(null)
  const [compareIds, setCompareIds] = useState<string[]>([])
  const [tab, setTab] = useState('backtest')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [personSearch, setPersonSearch] = useState('')
  useEffect(() => {
    if (!selected) return
    resultRef.current?.focus({ preventScroll: true })
    resultRef.current?.scrollIntoView({ block: 'start' })
  }, [selected])
  async function loadHistory() {
    const result = await apiFetch<{ items: Backtest[] }>('/api/labs/backtests')
    setHistory(result.items)
  }
  useEffect(() => {
    const controller = new AbortController()
    getCatalog(controller.signal)
      .then((value) => {
        setCatalog(value)
        setRequest((current) => ({
          ...current,
          start: editedFields.current.has('start')
            ? current.start
            : String(value.metadata.startDate || current.start),
          end: editedFields.current.has('end')
            ? current.end
            : String(value.metadata.priceCutoff || current.end),
        }))
      })
      .catch((error) => {
        if (error.name !== 'AbortError') setError(error.message)
      })
    void loadHistory().catch((error) => setError(error.message))
    return () => controller.abort()
  }, [])
  function update<K extends keyof BacktestRequest>(key: K, value: BacktestRequest[K]) {
    editedFields.current.add(key)
    setRequest((current) => ({
      ...current,
      [key]: value,
      ...(key === 'chamber' ? { politician: 'all' } : {}),
      ...(key === 'strategy' && (value === 'rotation_20' || value === 'legacy_open')
        ? { short_holding: false }
        : {}),
      ...(key === 'strategy' && value === 'rotation_20' && current.sizing === 'quarter_kelly'
        ? { sizing: 'fixed_2' }
        : {}),
    }))
  }
  async function run(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      const result = await apiFetch<Backtest>(
        '/api/labs/backtests',
        { method: 'POST', body: JSON.stringify(request) },
        { mutation: true },
      )
      setSelected(result)
      await loadHistory()
    } catch (error) {
      setError(error instanceof Error ? error.message : 'Backtest failed.')
    } finally {
      setBusy(false)
    }
  }
  async function open(id: string) {
    setBusy(true)
    setError('')
    try {
      setSelected(await apiFetch<Backtest>(`/api/labs/backtests/${encodeURIComponent(id)}`))
    } catch (error) {
      setError(error instanceof Error ? error.message : 'Saved backtest unavailable.')
    } finally {
      setBusy(false)
    }
  }
  const selectedPeople = request.politician === 'all' ? [] : request.politician.split(',')
  const people =
    catalog?.politicians.filter(
      (person) =>
        (request.chamber === 'all' || person.chamber.toLowerCase() === request.chamber) &&
        person.name.toLowerCase().includes(personSearch.toLowerCase()),
    ) || []
  const comparisons = history.filter((item) => compareIds.includes(item.id))
  return (
    <div className="research-page">
      <ResearchHeader eyebrow="Strategy testing" title="Strategy testing">
        Test trading rules against saved congressional disclosures. Compare results, review trades,
        and check the assumptions.
      </ResearchHeader>
      <div className="research-tabs" role="group" aria-label="Strategy workspace">
        <button
          type="button"
          aria-pressed={tab === 'backtest'}
          className="button"
          onClick={() => setTab('backtest')}
        >
          Custom backtest
        </button>
        <button
          type="button"
          aria-pressed={tab === 'research'}
          className="button"
          onClick={() => setTab('research')}
        >
          Saved research studies
        </button>
      </div>
      <ErrorNotice message={error} />
      {tab === 'research' ? (
        catalog ? (
          <HistoricResearch catalog={catalog} />
        ) : (
          <p role="status">Loading research catalog…</p>
        )
      ) : (
        <>
          <form className="research-card" onSubmit={run}>
            <h2>Set up a backtest</h2>
            <div className="research-fields">
              <label className="research-field">
                Strategy
                <select
                  value={request.strategy}
                  onChange={(event) => update('strategy', event.target.value)}
                >
                  {(
                    catalog?.strategies || [
                      { id: 'rotation_20', label: '20-position rotation', description: '' },
                    ]
                  ).map((strategy) => (
                    <option key={strategy.id} value={strategy.id}>
                      {strategy.label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="research-field">
                Chamber
                <select
                  value={request.chamber}
                  onChange={(event) => update('chamber', event.target.value)}
                >
                  <option value="all">House and Senate</option>
                  <option value="house">House</option>
                  <option value="senate">Senate</option>
                </select>
              </label>
            </div>
            <p>
              {
                catalog?.strategies.find((strategy) => strategy.id === request.strategy)
                  ?.description
              }
            </p>
            <div className="research-fields">
              <label className="research-field">
                Start date
                <input
                  type="date"
                  value={request.start}
                  max={request.end}
                  onInput={(event) => update('start', event.currentTarget.value)}
                  onChange={(event) => update('start', event.target.value)}
                  required
                />
              </label>
              <label className="research-field">
                End date
                <input
                  type="date"
                  value={request.end}
                  min={request.start}
                  max={String(catalog?.metadata.priceCutoff || DEFAULT_REQUEST.end)}
                  onInput={(event) => update('end', event.currentTarget.value)}
                  onChange={(event) => update('end', event.target.value)}
                  required
                />
              </label>
              <label className="research-field">
                Allocation per purchase
                <select
                  value={request.sizing}
                  onChange={(event) => update('sizing', event.target.value)}
                >
                  <option value="fixed_1">1% of equity</option>
                  <option value="fixed_2">2% of equity</option>
                  <option value="fixed_5">5% of equity</option>
                  {request.strategy !== 'rotation_20' && (
                    <option value="quarter_kelly">¼ Kelly · 1% pilot</option>
                  )}
                </select>
              </label>
            </div>
            <details className="research-details">
              <summary>Execution options · fees, sales and repeat purchases</summary>
              <div>
                <div className="research-fields">
                  <label className="research-field">
                    Cost per side (basis points)
                    <input
                      type="number"
                      min="0"
                      max="100"
                      step="0.5"
                      value={request.fee_bps}
                      onChange={(event) => update('fee_bps', Number(event.target.value))}
                      required
                    />
                  </label>
                </div>
                <div className="research-fields">
                  {request.strategy === 'legacy_open' ? (
                    <label className="research-field">
                      Disclosure delay
                      <select
                        value={request.delay}
                        onChange={(event) => update('delay', Number(event.target.value))}
                      >
                        <option value="1">1 calendar day</option>
                        <option value="31">31 calendar days</option>
                      </select>
                    </label>
                  ) : (
                    <>
                      <label className="research-field">
                        Disclosed sale handling
                        <select
                          value={request.exit_price}
                          onChange={(event) => update('exit_price', event.target.value)}
                        >
                          <option value="next_low">Exit at next-session low</option>
                          <option value="hold">Hold through period end</option>
                        </select>
                      </label>
                      <label className="research-checkbox">
                        <input
                          type="checkbox"
                          checked={request.add_purchases}
                          onChange={(event) => update('add_purchases', event.target.checked)}
                        />
                        Add subsequent purchases
                      </label>
                      {request.strategy !== 'rotation_20' && (
                        <label className="research-checkbox">
                          <input
                            type="checkbox"
                            checked={request.short_holding}
                            onChange={(event) => update('short_holding', event.target.checked)}
                          />
                          Use later-sale exclusion (hindsight)
                        </label>
                      )}
                    </>
                  )}
                </div>
              </div>
            </details>
            <details className="research-details">
              <summary>
                Politician selection ·{' '}
                {selectedPeople.length
                  ? `${selectedPeople.length} selected`
                  : 'all matching profiles'}
              </summary>
              <div>
                <label className="research-field">
                  Find a profile
                  <input
                    value={personSearch}
                    onChange={(event) => setPersonSearch(event.target.value)}
                  />
                </label>
                <button
                  type="button"
                  className="button"
                  onClick={() => update('politician', 'all')}
                >
                  Use all matching profiles
                </button>
                <div className="research-grid" style={{ maxHeight: 230, overflow: 'auto' }}>
                  {people.map((person) => (
                    <label className="research-checkbox" key={person.id}>
                      <input
                        type="checkbox"
                        checked={selectedPeople.includes(person.id)}
                        onChange={(event) => {
                          const next = event.target.checked
                            ? [...selectedPeople, person.id]
                            : selectedPeople.filter((id) => id !== person.id)
                          update('politician', next.length ? next.join(',') : 'all')
                        }}
                      />
                      {person.name} · {person.chamber}
                    </label>
                  ))}
                </div>
              </div>
            </details>
            <p className="research-footnote">
              One $100,000 starting account. Saved prices and source exclusions determine coverage.
              These retrospective experiments submit no orders.
            </p>
            <button className="button button-primary" disabled={busy || !catalog?.available}>
              {busy ? 'Running backtest…' : 'Run and save backtest'}
            </button>
          </form>
          {selected && (
            <section
              ref={resultRef}
              tabIndex={-1}
              aria-label="Selected backtest result"
              style={{ scrollMarginTop: 150 }}
            >
              <StrategyResults key={selected.id} result={selected} />
            </section>
          )}
          {history.length > 0 && (
            <section className="research-card">
              <h2>Saved runs</h2>
              <p>
                Select runs to compare; open a result to inspect equity, positions and original
                disclosure evidence.
              </p>
              <div className="research-table-wrap">
                <table className="research-table">
                  <thead>
                    <tr>
                      <th>Compare</th>
                      <th>Saved run</th>
                      <th>Period</th>
                      <th>Total return</th>
                      <th>Drawdown</th>
                      <th>SPY</th>
                    </tr>
                  </thead>
                  <tbody>
                    {history.map((item) => (
                      <tr key={item.id}>
                        <td>
                          <input
                            type="checkbox"
                            aria-label={`Compare ${humanize(item.request.strategy)} saved ${item.created_at}`}
                            checked={compareIds.includes(item.id)}
                            onChange={(event) =>
                              setCompareIds((current) =>
                                event.target.checked
                                  ? [...current, item.id]
                                  : current.filter((id) => id !== item.id),
                              )
                            }
                          />
                        </td>
                        <td>
                          <button onClick={() => void open(item.id)} disabled={busy}>
                            {humanize(item.request.strategy)}
                          </button>
                          <small>
                            {displayDate(item.created_at)} · {sizingLabel(item.request.sizing)} ·{' '}
                            {item.request.fee_bps} bps
                          </small>
                        </td>
                        <td>
                          {item.request.start} – {item.request.end}
                        </td>
                        <td>{percent(item.metrics.totalReturn)}</td>
                        <td>{percent(item.metrics.maxDrawdown)}</td>
                        <td>{percent(item.metrics.benchmarkReturn)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}
          {comparisons.length > 0 && (
            <section className="research-card">
              <h2>Selected run comparison</h2>
              {new Set(comparisons.map((item) => `${item.request.start}:${item.request.end}`))
                .size > 1 && (
                <p>
                  These runs cover different periods. Compare their dates and assumptions before
                  interpreting performance differences.
                </p>
              )}
              <div className="research-table-wrap">
                <table className="research-table">
                  <thead>
                    <tr>
                      <th>Run</th>
                      <th>Ending value</th>
                      <th>Return</th>
                      <th>CAGR</th>
                      <th>Drawdown</th>
                      <th>Volatility</th>
                      <th>Sharpe</th>
                    </tr>
                  </thead>
                  <tbody>
                    {comparisons.map((item) => (
                      <tr key={item.id}>
                        <td>
                          {humanize(item.request.strategy)}
                          <small>
                            {sizingLabel(item.request.sizing)} · {item.request.chamber}
                          </small>
                        </td>
                        <td>${formatNumber(item.metrics.endingValue)}</td>
                        <td>{percent(item.metrics.totalReturn)}</td>
                        <td>{percent(item.metrics.cagr)}</td>
                        <td>{percent(item.metrics.maxDrawdown)}</td>
                        <td>{percent(item.metrics.volatility)}</td>
                        <td>{formatNumber(item.metrics.sharpe, 2)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}
          {!selected && !history.length && (
            <EmptyState>
              Choose a strategy and run a backtest to see its performance and trades here.
            </EmptyState>
          )}
        </>
      )}
    </div>
  )
}
