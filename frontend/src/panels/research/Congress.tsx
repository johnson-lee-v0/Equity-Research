import { useEffect, useRef, useState } from 'react'
import { apiFetch } from '../../api'
import { type Disclosure, getCatalog, humanize, type LabCatalog, textValue } from './labs'
import {
  displayDate,
  downloadJson,
  EmptyState,
  ErrorNotice,
  formatNumber,
  ResearchHeader,
  sourceUrl,
} from './shared'
import ReportedPositions from './ReportedPositions'
import './research.css'

type Page = { items: Disclosure[]; total: number; offset: number; limit: number }
const EMPTY: Page = { items: [], total: 0, offset: 0, limit: 40 }
const DEFAULT_FILTERS = {
  search: '',
  chamber: 'all',
  politician: 'all',
  owner: 'all',
  action: 'all',
  date: '',
}

export default function Congress() {
  const [tab, setTab] = useState('records')
  const detailRef = useRef<HTMLElement>(null)
  const [catalog, setCatalog] = useState<LabCatalog | null>(null)
  const [filters, setFilters] = useState(DEFAULT_FILTERS)
  const filtersActive = Object.entries(filters).some(
    ([key, value]) => value !== DEFAULT_FILTERS[key as keyof typeof DEFAULT_FILTERS],
  )
  const moreFilterCount = [
    filters.action !== 'all',
    filters.owner !== 'all',
    Boolean(filters.date),
  ].filter(Boolean).length
  const [page, setPage] = useState<Page>(EMPTY)
  const [offset, setOffset] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [detail, setDetail] = useState<Record<string, unknown> | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState('')
  const [refresh, setRefresh] = useState(0)
  const [importing, setImporting] = useState(false)
  const query = new URLSearchParams(Object.entries(filters).filter(([, value]) => value !== ''))
  const params = new URLSearchParams({
    ...Object.fromEntries(query),
    offset: String(offset),
    limit: '40',
  }).toString()
  useEffect(() => {
    const controller = new AbortController()
    getCatalog(controller.signal)
      .then(setCatalog)
      .catch((error) => {
        if (error.name !== 'AbortError') setError(error.message)
      })
    return () => controller.abort()
  }, [])
  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError('')
    const timer = window.setTimeout(() => {
      apiFetch<Page>(`/api/labs/congress/records?${params}`, {}, { signal: controller.signal })
        .then(setPage)
        .catch((error) => {
          if (error.name !== 'AbortError') setError(error.message)
        })
        .finally(() => {
          if (!controller.signal.aborted) setLoading(false)
        })
    }, 200)
    return () => {
      controller.abort()
      window.clearTimeout(timer)
    }
  }, [params, refresh])
  async function refreshArchive() {
    setImporting(true)
    setError('')
    try {
      setCatalog(
        await apiFetch<LabCatalog>(
          '/api/labs/import',
          { method: 'POST', body: JSON.stringify({ refresh: true }) },
          { mutation: true },
        ),
      )
      setRefresh((value) => value + 1)
    } catch (error) {
      setError(error instanceof Error ? error.message : 'Local archive could not be refreshed.')
    } finally {
      setImporting(false)
    }
  }
  function filter(key: keyof typeof filters, value: string) {
    setFilters((current) => ({
      ...current,
      [key]: value,
      ...(key === 'chamber' ? { politician: 'all' } : {}),
    }))
    setOffset(0)
  }
  async function openDisclosure(id: string) {
    setDetailLoading(true)
    setDetailError('')
    try {
      setDetail(
        await apiFetch<Record<string, unknown>>(
          `/api/labs/congress/records/${encodeURIComponent(id)}`,
        ),
      )
      window.setTimeout(() => {
        detailRef.current?.focus({ preventScroll: true })
        detailRef.current?.scrollIntoView({ block: 'start' })
      }, 0)
    } catch (error) {
      setDetailError(
        error instanceof Error ? error.message : 'Disclosure detail could not be loaded.',
      )
    } finally {
      setDetailLoading(false)
    }
  }
  const detailSource = sourceUrl(typeof detail?.source === 'string' ? detail.source : undefined)
  const detailImage = sourceUrl(
    typeof detail?.sourceImage === 'string' ? detail.sourceImage : undefined,
  )
  return (
    <div className="research-page">
      <ResearchHeader
        eyebrow="Congress trading"
        title="Congressional disclosures"
        action={
          <button
            className="button"
            onClick={() => setRefresh((value) => value + 1)}
            disabled={loading}
          >
            Refresh records
          </button>
        }
      >
        Search reported transactions, review their original filings, and inspect disclosed
        positions.
      </ResearchHeader>
      <ErrorNotice message={error} />
      {importing && (
        <p className="research-progress" role="status">
          Updating the verified local archive. This may take about half a minute…
        </p>
      )}
      <section className="research-card">
        <div className="research-fields">
          <label className="research-field">
            Search disclosures
            <input
              value={filters.search}
              onChange={(event) => filter('search', event.target.value)}
              placeholder="Ticker, company or politician"
            />
          </label>
          <label className="research-field">
            Chamber
            <select
              value={filters.chamber}
              onChange={(event) => filter('chamber', event.target.value)}
            >
              <option value="all">House and Senate</option>
              <option value="house">House</option>
              <option value="senate">Senate</option>
            </select>
          </label>
          <label className="research-field">
            Politician
            <select
              value={filters.politician}
              onChange={(event) => filter('politician', event.target.value)}
            >
              <option value="all">All politicians</option>
              {catalog?.politicians
                .filter(
                  (person) =>
                    filters.chamber === 'all' || person.chamber.toLowerCase() === filters.chamber,
                )
                .map((person) => (
                  <option key={person.id} value={person.id}>
                    {person.name} · {formatNumber(person.records)}
                  </option>
                ))}
            </select>
          </label>
        </div>
        <details className="research-details">
          <summary>More filters{moreFilterCount ? ` · ${moreFilterCount} active` : ''}</summary>
          <div>
            <div className="research-fields">
              <label className="research-field">
                Action
                <select
                  value={filters.action}
                  onChange={(event) => filter('action', event.target.value)}
                >
                  <option value="all">All actions</option>
                  <option value="buy">Purchases</option>
                  <option value="sell">Sales</option>
                  <option value="other">Other / unknown</option>
                </select>
              </label>
              <label className="research-field">
                Owner
                <select
                  value={filters.owner}
                  onChange={(event) => filter('owner', event.target.value)}
                >
                  <option value="all">All owners</option>
                  <option value="Self">Self</option>
                  <option value="Spouse">Spouse</option>
                  <option value="Joint">Joint</option>
                  <option value="Dependent">Dependent</option>
                  <option value="Unknown">Unknown</option>
                </select>
              </label>
              <label className="research-field">
                Filed by
                <input
                  type="date"
                  value={filters.date}
                  onInput={(event) => filter('date', event.currentTarget.value)}
                  onChange={(event) => filter('date', event.target.value)}
                />
              </label>
            </div>
            <p className="research-footnote">
              “Filed by” uses the reported filing date; it may differ from the first public release.
            </p>
          </div>
        </details>
        <div className="research-fields">
          {filtersActive && (
            <button
              type="button"
              className="button"
              onClick={() => {
                setFilters(DEFAULT_FILTERS)
                setOffset(0)
              }}
            >
              Clear filters
            </button>
          )}
          <a className="button" href={`/api/labs/congress/export.csv?${query}`} download>
            Export matching CSV
          </a>
        </div>
        <div className="research-tabs" role="group" aria-label="Congress records">
          <button
            className="button"
            type="button"
            aria-pressed={tab === 'records'}
            onClick={() => setTab('records')}
          >
            Disclosures
          </button>
          <button
            className="button"
            type="button"
            aria-pressed={tab === 'positions'}
            onClick={() => setTab('positions')}
          >
            Reported positions
          </button>
        </div>
        {tab === 'records' && (
          <>
            <div className="research-card-heading">
              <h2>Disclosed transactions</h2>
              <span className="muted-copy" role="status">
                {loading ? 'Loading disclosures…' : `${formatNumber(page.total)} matching records`}
              </span>
            </div>
            <div className="research-table-wrap">
              <table className="research-table">
                <thead>
                  <tr>
                    <th>Politician / owner</th>
                    <th>Asset</th>
                    <th>Action / amount</th>
                    <th>Traded / filed</th>
                    <th>Backtest eligibility</th>
                  </tr>
                </thead>
                <tbody>
                  {page.items.map((row) => (
                    <tr key={row.id}>
                      <td>
                        <button
                          onClick={() => void openDisclosure(row.id)}
                          disabled={detailLoading}
                        >
                          {row.politician}
                        </button>
                        <small>
                          {row.chamber} · {row.owner || 'Owner unknown'}
                        </small>
                      </td>
                      <td>
                        <button
                          onClick={() => void openDisclosure(row.id)}
                          disabled={detailLoading}
                        >
                          {row.ticker || row.asset || 'Unidentified asset'}
                        </button>
                        {row.ticker && <small>{row.asset}</small>}
                      </td>
                      <td>
                        {row.action || 'Unknown'}
                        <small>{row.amountRange || 'Amount unknown'}</small>
                      </td>
                      <td>
                        {displayDate(row.tradeDate)}
                        <small>Filed {displayDate(row.filedDate)}</small>
                      </td>
                      <td>
                        <span className={`research-pill ${row.eligible ? 'positive' : 'neutral'}`}>
                          {row.eligible ? 'Eligible' : 'Excluded'}
                        </span>
                        {row.exclusion && <small>{row.exclusion}</small>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!loading && !page.items.length && (
              <EmptyState>No disclosures match these filters.</EmptyState>
            )}
            <div className="research-pagination">
              <span>
                {page.total
                  ? `${offset + 1}–${Math.min(offset + page.items.length, page.total)} of ${formatNumber(page.total)}`
                  : 'No records'}
              </span>
              <div className="button-row">
                <button
                  className="button"
                  onClick={() => setOffset((value) => Math.max(0, value - 40))}
                  disabled={loading || offset === 0}
                >
                  Previous
                </button>
                <button
                  className="button"
                  onClick={() => setOffset((value) => value + 40)}
                  disabled={loading || offset + page.items.length >= page.total}
                >
                  Next
                </button>
              </div>
            </div>
          </>
        )}
      </section>
      {tab === 'positions' && (
        <ReportedPositions query={query.toString()} onSource={openDisclosure} />
      )}
      <ErrorNotice message={detailError} />
      {detailLoading && (
        <p className="research-progress" role="status">
          Loading original disclosure…
        </p>
      )}
      {detail && (
        <section
          className="research-card"
          aria-label="Disclosure source detail"
          ref={detailRef}
          tabIndex={-1}
          style={{ scrollMarginTop: 150 }}
        >
          <div className="research-card-heading">
            <h2>Disclosure evidence</h2>
            <div className="button-row">
              {detailSource && (
                <a className="button" href={detailSource} target="_blank" rel="noreferrer">
                  Original filing ↗
                </a>
              )}
              {detailImage && (
                <a className="button" href={detailImage} target="_blank" rel="noreferrer">
                  Source page ↗
                </a>
              )}
              <button
                className="button"
                onClick={() =>
                  downloadJson(detail, `disclosure-${String(detail.id || 'record')}.json`)
                }
              >
                Export record
              </button>
              <button className="button" onClick={() => setDetail(null)}>
                Close detail
              </button>
            </div>
          </div>
          <dl className="research-detail-list">
            {Object.entries(detail)
              .filter(
                ([key, value]) =>
                  !['source', 'sourceImage'].includes(key) &&
                  value !== null &&
                  typeof value !== 'object',
              )
              .map(([key, value]) => (
                <div style={{ display: 'contents' }} key={key}>
                  <dt>{humanize(key)}</dt>
                  <dd>{textValue(value)}</dd>
                </div>
              ))}
          </dl>
          {Object.values(detail).some((value) => value && typeof value === 'object') && (
            <details className="research-details">
              <summary>Full source and pricing evidence</summary>
              <pre className="research-raw">{JSON.stringify(detail, null, 2)}</pre>
            </details>
          )}
        </section>
      )}
      {catalog && (
        <details className="research-card research-details">
          <summary>Dataset, availability and source methods</summary>
          {catalog.canImport && (
            <button className="button" disabled={importing} onClick={() => void refreshArchive()}>
              {importing ? 'Updating archive…' : 'Update from local source files'}
            </button>
          )}
          <p>
            These are saved disclosure records. Reported transaction dates and filing dates are
            separate; unknown or excluded fields remain visible. Backtest eligibility is assessed
            independently of whether a disclosure exists.
          </p>
          <dl className="research-detail-list">
            {Object.entries(catalog.metadata)
              .filter(([, value]) => typeof value !== 'object')
              .map(([key, value]) => (
                <div style={{ display: 'contents' }} key={key}>
                  <dt>{humanize(key)}</dt>
                  <dd>{textValue(value)}</dd>
                </div>
              ))}
          </dl>
          <details className="research-details">
            <summary>Disclosure watch status</summary>
            <pre className="research-raw">{JSON.stringify(catalog.watch, null, 2)}</pre>
          </details>
        </details>
      )}
    </div>
  )
}
