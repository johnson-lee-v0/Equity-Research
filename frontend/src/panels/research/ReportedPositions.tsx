import { useEffect, useState } from 'react'
import { apiFetch } from '../../api'
import { displayDate, EmptyState, ErrorNotice, formatNumber } from './shared'

type Position = {
  id: string
  politician: string
  owner: string
  account: string
  asset: string
  ticker: string
  status: string
  statusLabel: string
  firstTradeDate: string
  latestTradeDate: string
  latestKnownDate: string
  sourceRowIds: string[]
  warnings: string[]
  transactionCounts: Record<string, number>
}
type Positions = {
  items: Position[]
  total: number
  methodNotes: string[]
  counts: Record<string, number>
}

export default function ReportedPositions({
  query,
  onSource,
}: {
  query: string
  onSource: (id: string) => void
}) {
  const [data, setData] = useState<Positions | null>(null)
  const [offset, setOffset] = useState(0)
  const [status, setStatus] = useState('all')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [expanded, setExpanded] = useState<string | null>(null)
  useEffect(() => {
    setOffset(0)
  }, [query, status])
  useEffect(() => {
    const controller = new AbortController()
    const params = new URLSearchParams(query)
    params.set('action', 'all')
    params.set('offset', String(offset))
    params.set('limit', '40')
    params.set('status', status)
    setLoading(true)
    setError('')
    apiFetch<Positions>(`/api/labs/congress/positions?${params}`, {}, { signal: controller.signal })
      .then(setData)
      .catch((error) => {
        if (error.name !== 'AbortError') setError(error.message)
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [query, offset, status])
  return (
    <section className="research-card">
      <div className="research-card-heading">
        <h2>Reported position history</h2>
        <span className="muted-copy">
          {loading
            ? 'Reconstructing disclosures…'
            : `${formatNumber(data?.total)} matched positions`}
        </span>
      </div>
      <p>
        Conservative reconstruction from known purchases and sales. Disclosure dollar ranges do not
        identify exact shares or current market value. All transaction actions are included when
        reconstructing a position.
      </p>
      <ErrorNotice message={error} />
      <label className="research-field">
        Status
        <select value={status} onChange={(event) => setStatus(event.target.value)}>
          <option value="all">All reported states</option>
          {[
            'purchase_reported_holding_unconfirmed',
            'partial_sale_remainder_unquantified',
            'full_sale_reported',
            'full_sale_scope_uncertain',
            'sale_only_opening_holding_unknown',
            'sale_remainder_unknown',
            'exchange_result_unknown',
            'same_day_order_unknown',
            'reported_activity_unresolved',
          ].map((key) => (
            <option key={key} value={key}>
              {key.replaceAll('_', ' ')}
            </option>
          ))}
        </select>
      </label>
      <div className="research-table-wrap">
        <table className="research-table">
          <thead>
            <tr>
              <th>Politician / owner</th>
              <th>Asset</th>
              <th>Reported state</th>
              <th>First / latest trade</th>
              <th>Evidence</th>
            </tr>
          </thead>
          <tbody>
            {data?.items.map((position) => (
              <tr key={position.id}>
                <td>
                  {position.politician}
                  <small>
                    {position.owner} · {position.account || 'Account unknown'}
                  </small>
                </td>
                <td>
                  {position.ticker || position.asset}
                  <small>{position.ticker ? position.asset : ''}</small>
                </td>
                <td>
                  {position.statusLabel || position.status}
                  <small>
                    {position.warnings.map((warning) => warning.replaceAll('_', ' ')).join(' · ')}
                  </small>
                </td>
                <td>
                  {displayDate(position.firstTradeDate)}
                  <small>Latest {displayDate(position.latestTradeDate)}</small>
                </td>
                <td>
                  <button
                    onClick={() => setExpanded(expanded === position.id ? null : position.id)}
                  >
                    {position.sourceRowIds.length} disclosures
                  </button>
                  {expanded === position.id && (
                    <div className="button-row">
                      {position.sourceRowIds.map((id, index) => (
                        <button className="button" key={id} onClick={() => onSource(id)}>
                          Source {index + 1}
                        </button>
                      ))}
                    </div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!loading && !data?.items.length && (
        <EmptyState>No reported positions match these filters.</EmptyState>
      )}
      <div className="research-pagination">
        <span>{formatNumber(data?.total)} positions</span>
        <div className="button-row">
          <button
            className="button"
            disabled={loading || !offset}
            onClick={() => setOffset((value) => Math.max(0, value - 40))}
          >
            Previous
          </button>
          <button
            className="button"
            disabled={loading || offset + 40 >= (data?.total || 0)}
            onClick={() => setOffset((value) => value + 40)}
          >
            Next
          </button>
        </div>
      </div>
      <details className="research-details">
        <summary>Reconstruction method</summary>
        <ul>
          {data?.methodNotes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      </details>
    </section>
  )
}
