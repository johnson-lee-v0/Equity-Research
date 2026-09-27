import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { apiFetch } from '../api'
import type { Namespace, ValuationEarningsComponent, ValuationResearchContext as ResearchContext } from '../types'
import { contextNumber, contextNumberLabel, earningsQuarterLabel, historicalPeSegments, historicalMultipleStats, targetSourceUrl } from './priceTargetModel'

type Props = {
  context: ResearchContext
  ticker?: string | null
  namespace?: Namespace
  sourceLinks: (refs: unknown) => ReactNode
  prose: (value: string) => ReactNode
}
const shortDate = (value: string) => Number.isNaN(Date.parse(value)) ? value : new Intl.DateTimeFormat(undefined, { month: 'short', year: '2-digit', timeZone: 'UTC' }).format(new Date(value))
const multiple = (value: unknown) => contextNumber(value) === null ? 'Not available' : `${contextNumberLabel(value)}×`

function EarningsComponents({ components, sourceLinks }: { components: ValuationEarningsComponent[] } & Pick<Props, 'sourceLinks'>) {
  return <ul>{components.map((component, index) => {
    const url = targetSourceUrl(component.source_url)
    return <li key={index}>{component.metric && `${component.metric}: `}{component.sign === -1 ? '−' : index > 0 ? '+' : ''}{contextNumberLabel(component.value)} · {component.period_start} to {component.period_end}{sourceLinks(component.source_refs)}{url && <small><a href={url} target="_blank" rel="noreferrer">Filing ↗</a></small>}{component.available_at && <small>Available {component.available_at}</small>}</li>
  })}</ul>
}

export function CurrentEarningsEvidence({ earnings, sourceLinks }: { earnings?: ResearchContext['current_earnings'] } & Pick<Props, 'sourceLinks'>) {
  if (!earnings?.components?.length) return null
  return <details className="price-target-eps-components"><summary>Current earnings calculation and sources</summary>
    {earnings.formula && <p>{earnings.formula}</p>}
    <EarningsComponents components={earnings.components} sourceLinks={sourceLinks} />
    {earnings.available_as_of && <p>All components were reported by {earnings.available_as_of}.</p>}
  </details>
}

function EarningsBridge({ bridge, sourceLinks, prose }: { bridge: NonNullable<ResearchContext['earnings_bridge']> } & Pick<Props, 'sourceLinks' | 'prose'>) {
  const id = useId()
  const quarters = bridge.quarters ?? []
  const validValues = quarters.map((quarter) => contextNumber(quarter.value)).filter((value): value is number => value !== null)
  const floor = Math.min(0, ...validValues)
  const ceiling = Math.max(0, ...validValues)
  const span = ceiling - floor || 1
  const width = Math.max(360, quarters.length * 94)
  const left = 37
  const right = width - 12
  const top = 28
  const bottom = 142
  const y = (value: number) => bottom - (value - floor) / span * (bottom - top)
  const zero = y(0)
  const slot = (right - left) / Math.max(quarters.length, 1)
  const hasForecast = quarters.some((quarter) => quarter.kind === 'projection' && contextNumber(quarter.value) !== null)
  const fiscalYear = bridge.fiscal_year == null ? '' : /^FY/i.test(String(bridge.fiscal_year)) ? String(bridge.fiscal_year) : `FY${bridge.fiscal_year}`
  return <section className="price-target-context-section" aria-labelledby={`${id}-heading`}>
    <div className="price-target-context-heading"><h3 id={`${id}-heading`}>{fiscalYear ? `${fiscalYear} earnings` : 'Current fiscal year earnings'}</h3><span>{bridge.currency || 'Currency not recorded'} · {bridge.unit || 'Diluted EPS'}</span></div>
    {quarters.length > 0 && <>
      <div className="price-target-chart-wrap"><svg className="price-target-quarter-chart" viewBox={`0 0 ${width} 196`} role="img" aria-labelledby={`${id}-title ${id}-desc`}>
        <title id={`${id}-title`}>{`${fiscalYear} quarterly earnings: reported results and projections`}</title>
        <desc id={`${id}-desc`}>{quarters.map((quarter) => `${quarter.period}: ${contextNumberLabel(quarter.value)}, ${earningsQuarterLabel(quarter.kind)}`).join('; ')}. Solid bars are reported actuals; dashed amber bars are projections. Scale includes zero.</desc>
        <line x1={left} x2={right} y1={zero} y2={zero} className="price-target-chart-axis" />
        <text x={left - 6} y={zero + 4} textAnchor="end" className="price-target-chart-tick">0</text>
        {quarters.map((quarter, index) => {
          const value = contextNumber(quarter.value)
          const x = left + (index + 0.5) * slot
          const barTop = value === null ? zero : Math.min(y(value), zero)
          return <g key={`${quarter.period}-${index}`}>
            <title>{`${quarter.period}: ${contextNumberLabel(value)} · ${earningsQuarterLabel(quarter.kind)}${quarter.rationale ? `. ${quarter.rationale}` : ''}`}</title>
            {value !== null && quarter.kind !== 'missing' && <rect x={x - 20} y={barTop} width={40} height={Math.max(1, Math.abs(y(value) - zero))} rx={2} className={`price-target-quarter-bar ${quarter.kind === 'projection' ? 'is-projection' : ''}`} />}
            <text x={x} y={barTop - 8} textAnchor="middle" className="price-target-chart-value">{value === null ? 'Missing' : contextNumberLabel(value)}</text>
            <text x={x} y={bottom + 20} textAnchor="middle" className="price-target-chart-tick">{quarter.period.replace(/FY\s*20(\d\d)/, 'FY$1')}</text>
            <text x={x} y={bottom + 36} textAnchor="middle" className={`price-target-chart-kind ${quarter.kind === 'projection' ? 'is-projection' : ''}`}>{earningsQuarterLabel(quarter.kind)}</text>
          </g>
        })}
      </svg></div>
      <div className="price-target-chart-legend"><span><i /> Reported actual</span>{hasForecast && <span><i className="is-projection" /> Projection</span>}</div>
    </>}
    <dl className="price-target-context-stats">
      <div><dt>Reported so far</dt><dd>{contextNumberLabel(bridge.reported_total)}</dd></div>
      {hasForecast && <div><dt>Projected remainder</dt><dd className="is-projection">{contextNumberLabel(bridge.projected_total)}</dd></div>}
      <div><dt>{hasForecast ? 'Full year estimate' : contextNumber(bridge.full_year_total) !== null ? 'Full year actual' : 'Full year total'}</dt><dd>{contextNumberLabel(bridge.full_year_total)}</dd></div>
    </dl>
    {bridge.method && <p className="price-target-note">{prose(bridge.method)}</p>}
    {bridge.coverage_note && <p className="price-target-note">{prose(bridge.coverage_note)}</p>}
    {quarters.length > 0 && <details><summary>Quarterly values, sources and projection method</summary><div className="price-target-table-wrap"><table>
      <thead><tr><th scope="col">Quarter</th><th scope="col">EPS</th><th scope="col">Evidence or assumption</th></tr></thead>
      <tbody>{quarters.map((quarter, index) => <tr key={`${quarter.period}-${index}`}><th scope="row">{quarter.period}<small>{earningsQuarterLabel(quarter.kind)}</small></th><td>{contextNumberLabel(quarter.value)}</td><td>{quarter.rationale && prose(quarter.rationale)}{sourceLinks(quarter.source_refs)}{quarter.period_end && <small>Period ended {quarter.period_end}</small>}</td></tr>)}</tbody>
    </table></div></details>}
  </section>
}

function HistoricalPe({ history, sourceLinks, prose, asOf }: { history: NonNullable<ResearchContext['historical_pe']>; asOf?: string | null } & Pick<Props, 'sourceLinks' | 'prose'>) {
  const id = useId()
  const metric = history.metric ?? 'P/E'
  const provider = history.provenance === 'secondary_provider'
  const providerUrl = targetSourceUrl(history.source_url)
  const stats = historicalMultipleStats(history.points ?? [], provider ? history.as_of : asOf)
  const points = stats.points as NonNullable<ResearchContext['historical_pe']>['points'] & object[]
  const segments = historicalPeSegments(points, history.sampling === 'quarterly' ? 'quarterly' : 'monthly')
  const valid = segments.flat()
  const width = 610
  const height = 210
  const left = 42
  const right = width - 72
  const top = 24
  const bottom = 175
  const minimum = stats.minimum
  const maximum = stats.maximum
  const datedPoints = points.filter((point) => !Number.isNaN(Date.parse(point.date)))
  const start = datedPoints.length ? Date.parse(datedPoints[0].date) : 0
  const end = datedPoints.length ? Date.parse(datedPoints[datedPoints.length - 1].date) : 1
  const x = (date: string) => left + ((Date.parse(date) - start) / (end - start || 1)) * (right - left)
  const y = (value: number) => bottom - (value - minimum) / (maximum - minimum) * (bottom - top)
  const ticks = Array.from(new Set([0, Math.floor((datedPoints.length - 1) / 2), datedPoints.length - 1])).filter((index) => index >= 0 && datedPoints[index])
  return <section className="price-target-context-section" aria-labelledby={`${id}-heading`}>
    <div className="price-target-context-heading"><h3 id={`${id}-heading`}>Where the company has traded</h3><span>Historical {metric}</span></div>
    {history.basis && <p className="price-target-note">{prose(history.basis)}</p>}
    {provider && <p className="price-target-note"><strong>Provider comparison</strong> · {providerUrl ? <a href={providerUrl} target="_blank" rel="noreferrer">{history.provider || 'Source'} ↗</a> : history.provider}{history.as_of && ` · Retrieved ${history.as_of.slice(0, 10)}`}. Shown for comparison; the saved decision and target are unchanged.</p>}
    {valid.length > 0 && <>
      <dl className="price-target-context-stats"><div><dt>Sampled low</dt><dd>{multiple(history.min)}</dd></div><div><dt>Five-year sample average</dt><dd>{multiple(stats.mean)}</dd></div><div><dt>Standard deviation</dt><dd>{multiple(stats.deviation)}</dd></div><div><dt>Sampled high</dt><dd>{multiple(history.max)}</dd></div></dl>
      <div className="price-target-chart-wrap"><svg className="price-target-pe-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-labelledby={`${id}-title ${id}-desc`}>
        <title id={`${id}-title`}>{`Historical ${metric} multiple`}</title>
        <desc id={`${id}-desc`}>{valid.length} dated observations{valid.length ? ` from ${valid[0].date} to ${valid[valid.length - 1].date}` : ''}. Missing observations break the line. Values and their sources are listed below.</desc>
        {[minimum, (minimum + maximum) / 2, maximum].map((value) => <g key={value}><line x1={left} x2={right} y1={y(value)} y2={y(value)} className="price-target-chart-axis" /><text x={left - 7} y={y(value) + 4} textAnchor="end" className="price-target-chart-tick">{value.toFixed(1)}×</text></g>)}
        {stats.bands.map((band) => <g key={band.sigma}><line x1={left} x2={right} y1={y(band.value)} y2={y(band.value)} className={`price-target-sigma-line is-sigma-${Math.abs(band.sigma)}`} /><text x={right + 7} y={y(band.value) + 3} className="price-target-sigma-label">{band.sigma === 0 ? 'Average' : `${band.sigma > 0 ? '+' : '−'}${Math.abs(band.sigma)} SD`}</text></g>)}
        {segments.map((segment, index) => <polyline key={index} points={segment.map((point) => `${x(point.date)},${y(point.value)}`).join(' ')} className="price-target-pe-line" />)}
        {valid.map((point) => <circle key={`${point.date}-${point.index}`} cx={x(point.date)} cy={y(point.value)} r={2.4} className="price-target-pe-point"><title>{`${point.date}: ${contextNumberLabel(point.value)}× ${metric}`}</title></circle>)}
        {ticks.map((index) => <text key={index} x={x(datedPoints[index].date)} y={bottom + 23} textAnchor={index === 0 ? 'start' : index === datedPoints.length - 1 ? 'end' : 'middle'} className="price-target-chart-tick">{shortDate(datedPoints[index].date)}</text>)}
      </svg></div>
      <p className="price-target-note">{valid.length} {provider ? 'quarter-end observations and latest snapshot' : 'monthly observations'}{valid.length ? ` · ${valid[0].date} to ${valid[valid.length - 1].date}` : ''}. Average and ±1, ±2, ±3 standard deviations use the available samples in the last five years; missing periods are excluded. Bands describe historical variation, not a price prediction.</p>
      {!provider && <p className="price-target-note">Historical {metric} uses financials already reported at each observation date. A target multiple applied to a forecast uses a different basis.</p>}
    </>}
    {history.coverage_note && (provider ? <details><summary>How these numbers were sourced</summary><p className="price-target-note">{prose(history.coverage_note)}</p></details> : <p className="price-target-note">{prose(history.coverage_note)}</p>)}
    {!!history.gaps?.length && <details><summary>Historical coverage gaps <span>{history.gaps.length}</span></summary><ul>{history.gaps.map((gap, index) => <li key={index}>{gap.date && `${gap.date}: `}{gap.reason || 'The retained sources could not support this observation.'}</li>)}</ul></details>}
    {points.length > 0 && <details><summary>Dated multiples and sources <span>{points.length} observations</span></summary><div className="price-target-table-wrap price-target-history-table"><table>
      <thead><tr><th scope="col">Date</th>{!provider && <><th scope="col">Share price{history.currency && ` · ${history.currency}`}</th><th scope="col">{history.denominator_name ?? 'Trailing EPS'}</th></>}<th scope="col">{metric}</th><th scope="col">Sources</th></tr></thead>
      <tbody>{points.map((point, index) => <tr key={`${point.date}-${index}`}><th scope="row">{point.date}{point.period_label && <small>{point.period_label}</small>}</th>{!provider && <><td>{contextNumberLabel(point.close)}</td><td>{contextNumberLabel(point.ttm_eps)}{point.ebitda_basis && <small>{point.ebitda_basis}</small>}</td></>}<td>{multiple(point.pe)}</td><td>{provider && providerUrl ? <a href={providerUrl} target="_blank" rel="noreferrer">{history.provider} ↗</a> : sourceLinks(point.source_refs)}{(point.eps_period_end || point.earnings_period_end) && <small>Earnings through {point.eps_period_end || point.earnings_period_end}</small>}{point.available_as_of && <small>Reported by {point.available_as_of}</small>}{!!point.eps_components?.length && <details className="price-target-eps-components"><summary>Financial components</summary>{point.eps_formula && <p>{point.eps_formula}</p>}<EarningsComponents components={point.eps_components} sourceLinks={sourceLinks} /></details>}</td></tr>)}</tbody>
    </table></div></details>}
  </section>
}

type SupplementalHistory = { ticker: string; namespace: Namespace; provider_multiples?: ResearchContext['provider_multiples'] }

export default function ValuationResearchContext({ context, sourceLinks, prose, ticker, namespace }: Props) {
  const [selectedHistory, setSelectedHistory] = useState('P/E')
  const [supplemental, setSupplemental] = useState<SupplementalHistory | null>(null)
  const [updating, setUpdating] = useState(false)
  const [error, setError] = useState('')
  const request = useRef<AbortController | null>(null)
  const key = `${namespace}:${ticker}`
  const activeKey = useRef(key)
  activeKey.current = key
  const endpoint = ticker && /^[A-Z][A-Z0-9.-]{0,14}$/.test(ticker) && namespace ? `/api/valuation-history/${encodeURIComponent(ticker)}` : null
  useEffect(() => {
    const controller = new AbortController()
    request.current = controller
    setSupplemental(null); setError(''); setUpdating(false)
    if (endpoint) void apiFetch<SupplementalHistory>(`${endpoint}?namespace=${namespace}`, {}, { signal: controller.signal }).then((value) => {
      if (!controller.signal.aborted && activeKey.current === key) setSupplemental(value)
    }).catch(() => { /* Saved report remains usable when optional context is unavailable. */ })
    return () => { controller.abort(); request.current?.abort() }
  }, [endpoint, namespace, key])
  async function updateHistory() {
    if (!endpoint || namespace !== 'real' || updating) return
    request.current?.abort()
    const controller = new AbortController()
    request.current = controller
    setUpdating(true); setError('')
    try {
      const value = await apiFetch<SupplementalHistory>(`${endpoint}/refresh?namespace=${namespace}`, { method: 'POST', body: '{}' }, { mutation: true, signal: controller.signal })
      if (!controller.signal.aborted && activeKey.current === key) setSupplemental(value)
    } catch (cause) {
      if (!controller.signal.aborted && activeKey.current === key) setError(cause instanceof Error ? cause.message : 'Multiple history could not be updated.')
    } finally {
      if (!controller.signal.aborted && activeKey.current === key) setUpdating(false)
    }
  }
  const fresh = supplemental && supplemental.ticker === ticker && supplemental.namespace === namespace ? supplemental.provider_multiples : undefined
  const providerHistories = { ...context.provider_multiples, ...fresh }
  const histories: Record<string, NonNullable<ResearchContext['historical_pe']>> = { ...(context.historical_pe ? { 'P/E': context.historical_pe } : {}), ...(context.historical_multiples ?? {}) }
  for (const [metric, history] of Object.entries(providerHistories)) {
    if (!histories[metric]?.points?.length) histories[metric] = history
    else histories[`${metric} · provider`] = history
  }
  const activeHistory = histories[selectedHistory] ? selectedHistory : Object.keys(histories)[0]
  const chosen = histories[activeHistory]
  const adapted = chosen ? { ...chosen, points: (chosen.points ?? []).map((point) => {
    const other = point as { multiple?: string | number | null; denominator_value?: string | number | null; components?: ValuationEarningsComponent[] }
    return { ...point, pe: other.multiple ?? point.pe, ttm_eps: other.denominator_value ?? point.ttm_eps, eps_components: other.components ?? point.eps_components }
  }) } : null
  return <div className="price-target-research-context">
    {context.earnings_bridge && <EarningsBridge bridge={context.earnings_bridge} sourceLinks={sourceLinks} prose={prose} />}
    {Object.keys(histories).length > 1 && <label className="price-target-method-select">Company multiple history <select value={activeHistory} onChange={(event) => setSelectedHistory(event.target.value)}>{Object.keys(histories).map((name) => <option key={name} value={name}>{name}</option>)}</select></label>}
    {endpoint && namespace === 'real' && <div className="price-target-history-refresh"><button type="button" className="button" disabled={updating} onClick={() => void updateHistory()}>{updating ? 'Updating multiple history…' : 'Update multiple history'}</button><small>P/S, EV/EBITDA and book-value comparisons · no new agent run</small></div>}
    {error && <p className="price-target-note" role="alert">{error}</p>}
    {adapted && <HistoricalPe asOf={context.as_of} history={adapted} sourceLinks={sourceLinks} prose={prose} />}
  </div>
}
