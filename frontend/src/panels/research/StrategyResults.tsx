import { useState } from 'react'
import { apiFetch } from '../../api'
import { type Backtest, humanize, textValue } from './labs'
import {
  displayDate,
  downloadJson,
  EmptyState,
  ErrorNotice,
  formatNumber,
  Metric,
  percent,
  sourceUrl,
} from './shared'

type Point = { date: string; nav: number; benchmark?: number | null }

export function EquityCurve({ curve }: { curve: Point[] }) {
  const [index, setIndex] = useState<number | null>(null)
  const points = curve.filter((point) => Number.isFinite(point.nav))
  if (!points.length) return <EmptyState>No covered market sessions in this result.</EmptyState>
  const values = points.flatMap((point) => [
    point.nav,
    ...(point.benchmark != null && Number.isFinite(point.benchmark) ? [point.benchmark] : []),
  ])
  const min = Math.min(...values)
  const max = Math.max(...values)
  const span = max - min || 1
  const x = (i: number) => 65 + (i / Math.max(1, points.length - 1)) * 670
  const y = (value: number) => 210 - ((value - min) / span) * 180
  const sampled = points.filter(
    (_, i) => i % Math.max(1, Math.floor(points.length / 450)) === 0 || i === points.length - 1,
  )
  const path = (field: 'nav' | 'benchmark') =>
    sampled
      .map((point) =>
        point[field] != null && Number.isFinite(point[field])
          ? `${x(points.indexOf(point))},${y(point[field] as number)}`
          : '',
      )
      .filter(Boolean)
      .join(' ')
  const selected = points[Math.min(index ?? points.length - 1, points.length - 1)]
  return (
    <div>
      <svg
        className="research-chart"
        viewBox="0 0 760 260"
        role="img"
        aria-label={`Portfolio and SPY equity from ${points[0].date} to ${points[points.length - 1].date}`}
      >
        <title>Portfolio equity in green and SPY buy-and-hold in gold, USD</title>
        {[0, 0.5, 1].map((ratio) => (
          <g key={ratio}>
            <line x1="65" x2="735" y1={y(min + span * ratio)} y2={y(min + span * ratio)} />
            <text x="55" y={y(min + span * ratio) + 4} textAnchor="end">
              {formatNumber((min + span * ratio) / 1000)}k
            </text>
          </g>
        ))}
        <polyline points={path('benchmark')} className="benchmark" />
        <polyline points={path('nav')} />
        <text x="65" y="242">
          {points[0].date}
        </text>
        <text x="735" y="242" textAnchor="end">
          {points[points.length - 1].date}
        </text>
      </svg>
      <label className="research-field">
        Inspect a trading session
        <input
          type="range"
          min="0"
          max={points.length - 1}
          value={index ?? points.length - 1}
          onChange={(event) => setIndex(Number(event.target.value))}
        />
      </label>
      <p>
        {displayDate(selected.date)} ·{' '}
        <span style={{ color: 'var(--green)' }}>Portfolio ${formatNumber(selected.nav)}</span> ·{' '}
        <span style={{ color: 'var(--gold)' }}>SPY ${formatNumber(selected.benchmark)}</span>
      </p>
    </div>
  )
}

export function RecordTable({
  records,
  columns,
  onSource,
}: {
  records: Record<string, unknown>[]
  columns?: string[]
  onSource?: (id: string) => void
}) {
  const [page, setPage] = useState(0)
  const [query, setQuery] = useState('')
  const filtered = records.filter(
    (record) => !query || JSON.stringify(record).toLowerCase().includes(query.toLowerCase()),
  )
  const fields =
    columns ||
    [
      ...new Set(
        records
          .slice(0, 25)
          .flatMap((row) => Object.keys(row).filter((key) => typeof row[key] !== 'object')),
      ),
    ].slice(0, 10)
  const lastPage = Math.max(0, Math.ceil(filtered.length / 25) - 1)
  const currentPage = Math.min(page, lastPage)
  return (
    <>
      <label className="research-field">
        Filter rows
        <input
          value={query}
          onChange={(event) => {
            setQuery(event.target.value)
            setPage(0)
          }}
          placeholder="Search this table"
        />
      </label>
      <div className="research-table-wrap">
        <table className="research-table">
          <thead>
            <tr>
              {fields.map((key) => (
                <th key={key}>{humanize(key)}</th>
              ))}
              <th>Evidence</th>
            </tr>
          </thead>
          <tbody>
            {filtered.slice(currentPage * 25, currentPage * 25 + 25).map((row, index) => (
              <tr key={String(row.id ?? index)}>
                {fields.map((key) => (
                  <td key={key}>
                    {typeof row[key] === 'number'
                      ? /return|drawdown|cagr|volatility/i.test(key)
                        ? percent(row[key] as number)
                        : formatNumber(row[key] as number, 2)
                      : textValue(row[key])}
                  </td>
                ))}
                <td>
                  {onSource &&
                    Array.isArray(row.sourceIds) &&
                    row.sourceIds.map((id) => (
                      <button key={String(id)} onClick={() => onSource(String(id))}>
                        Disclosure ↗{' '}
                      </button>
                    ))}
                  <details>
                    <summary>Detail</summary>
                    <pre className="research-raw">{JSON.stringify(row, null, 2)}</pre>
                  </details>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!filtered.length && <EmptyState>No matching rows.</EmptyState>}
      <div className="research-pagination">
        <span>{formatNumber(filtered.length)} rows</span>
        <div className="button-row">
          <button
            className="button"
            disabled={currentPage === 0}
            onClick={() => setPage(currentPage - 1)}
          >
            Previous
          </button>
          <span>
            {currentPage + 1} / {lastPage + 1}
          </span>
          <button
            className="button"
            disabled={currentPage >= lastPage}
            onClick={() => setPage(currentPage + 1)}
          >
            Next
          </button>
        </div>
      </div>
    </>
  )
}

export function BacktestMetrics({ metrics }: { metrics: Backtest['metrics'] }) {
  return (
    <div className="research-metrics">
      <Metric label="Ending equity" value={`$${formatNumber(metrics.endingValue)}`} />
      <Metric label="Total return" value={percent(metrics.totalReturn)} />
      <Metric label="SPY return" value={percent(metrics.benchmarkReturn)} />
      <Metric label="Annualized return" value={percent(metrics.cagr)} />
      <Metric label="Max drawdown" value={percent(metrics.maxDrawdown)} />
      <Metric label="Sharpe" value={formatNumber(metrics.sharpe, 2)} />
    </div>
  )
}

export function StructuredEvidence({ value }: { value: unknown }) {
  if (value == null) return <p>No recorded value.</p>
  if (Array.isArray(value)) {
    if (!value.length) return <p>No recorded items.</p>
    if (value.every((item) => item && typeof item === 'object' && !Array.isArray(item)))
      return <RecordTable records={value as Record<string, unknown>[]} />
    return (
      <ul>
        {value.map((item, index) => (
          <li key={index}>
            {typeof item === 'object' ? <StructuredEvidence value={item} /> : textValue(item)}
          </li>
        ))}
      </ul>
    )
  }
  if (typeof value !== 'object') return <p>{textValue(value)}</p>
  const pairs = Object.entries(value)
  return (
    <>
      <dl className="research-detail-list">
        {pairs
          .filter(([, item]) => item == null || typeof item !== 'object')
          .map(([key, item]) => (
            <div key={key} style={{ display: 'contents' }}>
              <dt>{humanize(key)}</dt>
              <dd>{textValue(item)}</dd>
            </div>
          ))}
      </dl>
      {pairs
        .filter(([, item]) => item && typeof item === 'object')
        .map(([key, item]) => (
          <EvidenceSection key={key} title={humanize(key)} value={item} />
        ))}
    </>
  )
}

export function EvidenceSection({ title, value }: { title: string; value: unknown }) {
  const [open, setOpen] = useState(false)
  return (
    <details className="research-details" onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>{title}</summary>
      {open && (
        <div>
          <StructuredEvidence value={value} />
        </div>
      )}
    </details>
  )
}

export default function StrategyResults({ result }: { result: Backtest }) {
  const [tab, setTab] = useState('entries')
  const [source, setSource] = useState<Record<string, unknown> | null>(null)
  const [error, setError] = useState('')
  async function openSource(id: string) {
    setError('')
    try {
      setSource(
        await apiFetch<Record<string, unknown>>(
          `/api/labs/congress/records/${encodeURIComponent(id)}`,
        ),
      )
    } catch (error) {
      setError(error instanceof Error ? error.message : 'Source unavailable.')
    }
  }
  const tables: Record<string, Record<string, unknown>[]> = {
    entries: result.entries || [],
    holdings: result.holdings || [],
    closed: result.closed || [],
    orders: result.orders || [],
    exclusions: Array.isArray(result.exclusions)
      ? result.exclusions
      : Object.entries(result.exclusions || {}).map(([reason, count]) => ({ reason, count })),
  }
  return (
    <>
      <section className="research-card">
        <div className="research-card-heading">
          <h2>Backtest result</h2>
          <button
            className="button"
            onClick={() => downloadJson(result, `backtest-${result.id}.json`)}
          >
            Export complete result
          </button>
        </div>
        <p>
          {result.request.start} – {result.request.end} · {humanize(result.request.strategy)} ·{' '}
          {result.request.fee_bps} bps per side
        </p>
        <BacktestMetrics metrics={result.metrics} />
        <EquityCurve curve={result.curve} />
        <details className="research-details" open>
          <summary>Execution assumptions</summary>
          {Array.isArray(result.assumptions) ? (
            <ul>
              {result.assumptions.map((item, i) => (
                <li key={i}>{textValue(item)}</li>
              ))}
            </ul>
          ) : (
            <p>{textValue(result.assumptions)}</p>
          )}
        </details>
      </section>
      <section className="research-card">
        <div className="research-tabs" role="group" aria-label="Backtest ledgers">
          {Object.entries(tables).map(([key, rows]) => (
            <button
              type="button"
              aria-pressed={tab === key}
              className="button"
              key={key}
              onClick={() => setTab(key)}
            >
              {humanize(key)} · {formatNumber(rows.length)}
            </button>
          ))}
        </div>
        <RecordTable
          key={`${result.id}-${tab}`}
          records={tables[tab]}
          columns={
            tab === 'entries'
              ? [
                  'symbol',
                  'politician',
                  'entryDate',
                  'exitDate',
                  'status',
                  'value',
                  'pnl',
                  'returnValue',
                ]
              : undefined
          }
          onSource={openSource}
        />
      </section>
      <ErrorNotice message={error} />
      {source && (
        <section className="research-card">
          <div className="research-card-heading">
            <h2>Source disclosure</h2>
            <button className="button" onClick={() => setSource(null)}>
              Close
            </button>
          </div>
          <p>
            {textValue(source.politician)} · {textValue(source.asset)} ·{' '}
            {textValue(source.transactionType)}
          </p>
          {sourceUrl(String(source.source || '')) && (
            <a href={sourceUrl(String(source.source))} target="_blank" rel="noreferrer">
              Open original filing ↗
            </a>
          )}
          <pre className="research-raw">{JSON.stringify(source, null, 2)}</pre>
        </section>
      )}
    </>
  )
}
