import { useId, useState } from 'react'
import EarningsTrends, { CapexGuidance } from './EarningsTrends'
import { groupedEarningsTrends } from './earningsReviewModel'
import { selectTrendSeries, type EarningsTrendsResult } from './earningsTrendModel'
import './earnings-trend-explorer.css'

/** Read-only presentation of supplied, source-bound series; never fetches data. */
export default function EarningsTrendExplorer({ result, loading = false }: { result?: EarningsTrendsResult | null; loading?: boolean }) {
  const id = useId()
  const categories = groupedEarningsTrends(result)
  const [categoryId, setCategoryId] = useState<string>()
  const [metricIds, setMetricIds] = useState<Record<string, string>>({})
  const active = categories.find(category => category.id === categoryId)
    ?? categories.find(category => category.series.length > 0 || (category.id === 'capital' && result?.capex_guidance))
    ?? categories[0]
  const selected = selectTrendSeries(active.series, metricIds[active.id], active.id === 'growth' ? 'net_sales_growth' : undefined)
  return <section className="earnings-trend-explorer" aria-labelledby={`${id}-heading`}>
    <div className="earnings-explorer-heading"><h3 id={`${id}-heading`}>The numbers over time</h3><p>Choose a topic, then a metric. Reported results, projections and guidance keep their own labels and sources.</p></div>
    <div className="earnings-category-controls" role="group" aria-label="Choose trend category">
      {categories.filter(category => category.id !== 'other' || category.series.length > 0).map(category => <button type="button" key={category.id} aria-pressed={active.id === category.id} aria-controls={`${id}-figures`} onClick={() => setCategoryId(category.id)}>{category.label}<small>{category.series.length} {category.series.length === 1 ? 'metric' : 'metrics'}</small></button>)}
    </div>
    <div id={`${id}-figures`} className="earnings-category-content" aria-live="polite">
      <h4>{active.label}</h4>
      <EarningsTrends key={active.id} series={selected} choices={active.series} onSelect={metricId => setMetricIds(previous => ({ ...previous, [active.id]: metricId }))} loading={loading} emptyMessage={!result ? 'Historical figures have not been collected for this saved review yet.' : active.empty} />
      {active.id === 'capital' && (result?.capex_guidance ? <CapexGuidance guidance={result.capex_guidance} /> : <p className="earnings-trend-note">No guidance-versus-actual comparison was saved for this review.</p>)}
    </div>
    {!!result?.gaps?.length && <details className="earnings-trends-gaps"><summary>Historical data coverage · {result.gaps.length} {result.gaps.length === 1 ? 'gap' : 'gaps'}</summary><ul>{result.gaps.map((gap, index) => <li key={index}>{gap}</li>)}</ul></details>}
  </section>
}
