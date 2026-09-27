import { useId, type ReactNode } from 'react'
import {
  approximateGuidanceComparison,
  CAPEX_BASIS_NOTE,
  CAPEX_COMPARISON_NOTE,
  guidanceComparisonLabel,
  formatTrendRange,
  formatTrendValue,
  distinctExplanationText,
  qualifiedTrendLabel,
  reportedTrendChange,
  safeTrendUrl,
  trendPointLabel,
  trendPointDescription,
  trendEvidenceLabel,
  trendGapExplanation,
  trendScale,
  isCapexSeries,
  trendSeriesCoverage,
  trendSeriesCoverageLabel,
  validTrendValue,
  type EarningsTrendSeries,
  type EarningsTrendPoint,
  type EarningsTrendSource,
  type EarningsTrendsResult,
} from './earningsTrendModel'
import './earnings-trends.css'

function SourceLink({ source, children, label }: { source?: EarningsTrendSource; children?: ReactNode; label?: string }) {
  const url = safeTrendUrl(source?.url)
  return url ? (
    <a href={url} target="_blank" rel="noreferrer" title={source?.title || source?.quote} aria-label={label ? `${label} — ${source?.title || 'source'}` : undefined}>
      {children || 'Source'} <span aria-hidden="true">↗</span>
    </a>
  ) : <span>{children || 'Source unavailable'}</span>
}

function CapexCalculationSources({ point }: { point: EarningsTrendPoint }) {
  if (point.measure_basis !== 'cash_ppe_plus_finance_lease_principal') return null
  const inputs = Array.isArray(point.calculation?.inputs) ? point.calculation.inputs : []
  return <div className="earnings-capex-calculation">
    <ul>{inputs.filter((input) => input && typeof input === 'object').map((input, index) => {
      const label = String(input.tag || '').includes('FinanceLease') ? 'Finance lease principal payments' : 'Cash purchases of PP&E'
      const raw = input.value === null || input.value === undefined || input.value === '' ? NaN : Number(input.value)
      const amount = input.unit === 'USD' ? formatTrendValue(raw / 1_000_000_000, 'USD billions') : formatTrendValue(raw, String(input.unit || ''))
      return <li key={`${input.source_id || ''}-${index}`}>
        <span>{label}: {amount}</span>{' · '}<SourceLink source={input} label={`${point.period}, ${label}: ${amount}`} />
        {input.quote && <blockquote>{String(input.quote)}</blockquote>}
      </li>
    })}</ul>
    {point.definition_source && <div><strong>Company’s capex definition</strong>{' · '}<SourceLink source={point.definition_source} label={`${point.period}, company’s capex definition`} />{point.definition_source.quote && <blockquote>{point.definition_source.quote}</blockquote>}</div>}
  </div>
}

/** Public arithmetic operands may also arrive as named numeric fields. */
function NamedCalculationInputs({ point }: { point: EarningsTrendPoint }) {
  const inputs = point.calculation?.inputs
  if (!inputs || typeof inputs !== 'object' || Array.isArray(inputs)) return null
  return <ul>{Object.entries(inputs).map(([key, value]) => {
    const label = key.replaceAll('_', ' ')
    if (typeof value === 'number' && Number.isFinite(value)) return <li key={key}>{label}: {new Intl.NumberFormat('en-US', { maximumFractionDigits: 6 }).format(value)}</li>
    if (value && typeof value === 'object' && 'url' in value && typeof value.url === 'string') return <li key={key}><SourceLink source={value as EarningsTrendSource} label={`${point.period}, ${label}`}>{label}</SourceLink></li>
    return null
  })}</ul>
}

function TrendChart({ series }: { series: EarningsTrendSeries }) {
  const id = useId()
  const width = Math.max(390, series.points.length * 61 + 40)
  const height = 182
  const left = 36
  const right = width - 10
  const top = 29
  const bottom = 143
  const scale = trendScale(series.points)
  const y = (value: number) => bottom - ((value - scale.min) / (scale.max - scale.min)) * (bottom - top)
  const zero = y(0)
  const slot = (right - left) / Math.max(series.points.length, 1)
  const barWidth = Math.min(36, slot * 0.55)
  const hasGuidance = series.points.some((point) => point.kind === 'guidance')
  const hasProjection = series.points.some((point) => point.kind === 'projection')
  const hasBounds = series.points.some((point) => point.qualifier === 'less_than' || point.qualifier === 'greater_than')
  const hasApproximate = series.points.some((point) => point.qualifier === 'approximately')
  const observedChange = reportedTrendChange(series)
  const hasValues = series.points.some((point) => validTrendValue(point.value))
  if (!hasValues) return <p className="earnings-trend-empty">No comparable observations are available for this metric.</p>
  return (
    <>
      <div className="earnings-trend-chart">
        <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-labelledby={`${id}-title ${id}-description`}>
          <title id={`${id}-title`}>{`${series.label} over time`}</title>
          <desc id={`${id}-description`}>
            {series.basis}. Scale includes zero. {series.points.map((point) => `${point.period}: ${trendPointDescription(point, series.unit)}, ${trendEvidenceLabel(point)}`).join('; ')}.
            {hasGuidance ? 'Dashed bars show guidance, not reported results.' : ''}
            {hasProjection ? 'Amber outlined bars show projections, not reported results.' : ''}
          </desc>
          <line x1={left} x2={right} y1={top} y2={top} className="earnings-chart-grid" />
          <line x1={left} x2={right} y1={zero} y2={zero} className="earnings-chart-zero" />
          <text x={left - 7} y={top + 4} textAnchor="end" className="earnings-chart-axis">{new Intl.NumberFormat('en-US', { maximumFractionDigits: 1 }).format(scale.max)}</text>
          <text x={left - 7} y={zero + 4} textAnchor="end" className="earnings-chart-axis">0</text>
          {scale.min < 0 && <text x={left - 7} y={bottom + 4} textAnchor="end" className="earnings-chart-axis">{scale.min}</text>}
          {series.points.map((point, index) => {
            const x = left + slot * (index + 0.5)
            const value = validTrendValue(point.value) ? point.value : null
            const barTop = value === null ? zero : Math.min(zero, y(value))
            const barHeight = value === null ? 0 : Math.max(Math.abs(y(value) - zero), value === 0 ? 1 : 0)
            const fullLabel = value === null ? '—' : trendPointLabel(point, series.unit)
            const label = series.unit === 'USD per share' && value !== null ? `$${fullLabel.replace(' USD per share', '')}` : fullLabel.replaceAll('bn', '')
            const labelY = barTop - 8
            return (
              <g key={`${point.period}-${point.kind}-${index}`}>
                <title>{`${point.period}: ${trendPointDescription(point, series.unit)} (${trendEvidenceLabel(point)})${point.quote ? `. ${point.quote}` : ''}${trendGapExplanation(point) ? `. ${trendGapExplanation(point)}` : ''}`}</title>
                {value !== null && <rect x={x - barWidth / 2} y={barTop} width={barWidth} height={barHeight} rx="2" className={`earnings-chart-bar${point.kind === 'guidance' ? ' guidance' : ''}${point.kind === 'projection' ? ' projection' : ''}${point.qualifier === 'less_than' || point.qualifier === 'greater_than' ? ' bound' : ''}`} />}
                {point.kind === 'guidance' && validTrendValue(point.low) && validTrendValue(point.high) && point.low !== point.high && (
                  <g className="earnings-chart-range">
                    <line x1={x} x2={x} y1={y(point.low)} y2={y(point.high)} />
                    <line x1={x - 6} x2={x + 6} y1={y(point.low)} y2={y(point.low)} />
                    <line x1={x - 6} x2={x + 6} y1={y(point.high)} y2={y(point.high)} />
                  </g>
                )}
                <text x={x} y={labelY} textAnchor="middle" className="earnings-chart-value" style={label.length > 10 ? { fontSize: 10 } : undefined}>{label}</text>
                <text x={x} y={bottom + 22} textAnchor="middle" className="earnings-chart-period">{point.period.replace(/FY\s*20(\d\d)/, 'FY$1')}</text>
              </g>
            )
          })}
        </svg>
      </div>
      <div className="earnings-trend-chart-meta">
        <span>{series.unit === 'percent' ? 'Percent · zero baseline' : `${series.unit} · zero baseline`}</span>
        {(hasGuidance || hasProjection || hasBounds) && <span className="earnings-trend-legend"><i /> Reported {hasGuidance && <><i className="guidance" /> Guidance</>}{hasProjection && <><i className="projection" /> Projection</>}{hasBounds && <><i className="bound" /> Reported bound</>}</span>}
      </div>
      {observedChange && <p className="earnings-trend-change">{observedChange}</p>}
      {(hasBounds || hasApproximate) && <p className="earnings-trend-note">{hasBounds ? '< and > mark a reported boundary; the exact value was not stated. ' : ''}{hasApproximate ? '≈ denotes management’s approximate figure.' : ''}</p>}
      {series.points.some((point) => point.kind === 'guidance' && point.low !== point.high && validTrendValue(point.low) && validTrendValue(point.high)) && <p className="earnings-trend-note">Guidance bars use the range midpoint; whiskers and labels show the full range.</p>}
      {series.points.some((point) => !validTrendValue(point.value)) && <p className="earnings-trend-note">Gaps are missing observations, not zero values. Open Values & sources for the coverage of each period.</p>}
    </>
  )
}

function TrendSources({ series }: { series: EarningsTrendSeries }) {
  return (
    <details className="earnings-trend-sources">
      <summary>Values & sources</summary>
      <div className="earnings-trend-table-wrap">
        <table>
          <caption>{series.label} · {series.basis}</caption>
          <thead><tr><th scope="col">Period</th><th scope="col">Value</th><th scope="col">Evidence</th></tr></thead>
          <tbody>{series.points.map((point, index) => (
            <tr key={`${point.period}-${point.kind}-${index}`}>
              <th scope="row">{point.period}<small>{trendEvidenceLabel(point)}</small></th>
              <td aria-label={trendPointDescription(point, series.unit)}>{trendPointLabel(point, series.unit)}</td>
              <td>
                <SourceLink source={point} label={`${series.label}, ${point.period}, ${trendEvidenceLabel(point)}: ${trendPointDescription(point, series.unit)}`} />
                {point.published_at && <small>Published {point.published_at.slice(0, 10)}</small>}
                {point.source_method && <small>{point.source_method}</small>}
                {trendGapExplanation(point) && <p>{trendGapExplanation(point)}</p>}
                {point.rationale && distinctExplanationText(point.rationale, trendGapExplanation(point) ?? undefined) && <p>{point.rationale}</p>}
                {point.calculation?.formula && <p>Calculation: {point.calculation.formula}{point.calculation.basis ? ` · ${point.calculation.basis}` : ''}{point.calculation.rounding ? ` · ${point.calculation.rounding}` : ''}</p>}
                <CapexCalculationSources point={point} />
                <NamedCalculationInputs point={point} />
                {point.quote && point.measure_basis !== 'cash_ppe_plus_finance_lease_principal' && <blockquote>{point.quote}</blockquote>}
              </td>
            </tr>
          ))}</tbody>
        </table>
      </div>
    </details>
  )
}

export function CapexGuidance({ guidance }: { guidance: EarningsTrendsResult['capex_guidance'] }) {
  if (!guidance) return null
  return (
    <div className="earnings-capex-guidance">
      <h4>Capex guidance track record</h4>
      <p className="earnings-trend-note">Management capex only, using actuals on the same basis as guidance. Cash PP&E is shown separately and is not substituted for missing management capex actuals.</p>
      {guidance.summary && <p className="earnings-capex-summary">{guidance.summary}</p>}
      {!!guidance.comparisons?.length && <p className="earnings-trend-note">{CAPEX_COMPARISON_NOTE}</p>}
      <details>
      <summary>Compare guidance with actual spending</summary>
      <div className="earnings-capex-guidance-body">
        {!!guidance.comparisons?.length && (
          <div className="earnings-trend-table-wrap">
            <table>
              <caption>Management capex · earliest captured guidance and reported actuals · USD billions</caption>
              <thead><tr><th scope="col">Fiscal year</th><th scope="col">Earliest captured guidance</th><th scope="col">Actual</th><th scope="col">Difference</th></tr></thead>
              <tbody>{guidance.comparisons.map((comparison) => (
                <tr key={comparison.period}>
                  <th scope="row">{comparison.period}</th>
                  <td>
                    <SourceLink source={comparison.initial_source} label={`${comparison.period}, earliest captured management capex guidance: ${trendPointDescription({ period: comparison.period, kind: 'guidance', value: null, low: comparison.initial_low, high: comparison.initial_high, qualifier: comparison.initial_qualifier || comparison.initial_source?.qualifier }, 'USD billions')}`}>{qualifiedTrendLabel(formatTrendRange(comparison.initial_low, comparison.initial_high, 'USD billions'), comparison.initial_qualifier || comparison.initial_source?.qualifier)}</SourceLink>
                    {comparison.initial_source?.published_at && <small>Published {comparison.initial_source.published_at.slice(0, 10)}</small>}
                    {!!comparison.revisions?.length && <details className="earnings-guidance-revisions">
                      <summary>{comparison.revisions.length} later captured guidance {comparison.revisions.length === 1 ? 'observation' : 'observations'}</summary>
                      <ul>{comparison.revisions.map((revision, index) => <li key={index}>
                        <SourceLink source={revision} label={`${comparison.period}, later management capex guidance: ${trendPointDescription({ ...revision, period: comparison.period, kind: 'guidance', value: null }, 'USD billions')}`}>{qualifiedTrendLabel(formatTrendRange(revision.low, revision.high, 'USD billions'), revision.qualifier)}</SourceLink>
                        <small>{revision.published_at ? `Published ${revision.published_at.slice(0, 10)}` : 'Publication date unavailable'}</small>
                        {revision.quote && <blockquote>“{revision.quote}”</blockquote>}
                      </li>)}</ul>
                    </details>}
                  </td>
                  <td>
                    <SourceLink source={comparison.actual_source} label={`${comparison.period}, actual management capex: ${trendPointDescription({ period: comparison.period, kind: 'actual', value: comparison.actual, qualifier: comparison.actual_qualifier || comparison.actual_source?.qualifier }, 'USD billions')}`}>{qualifiedTrendLabel(formatTrendValue(comparison.actual, 'USD billions'), comparison.actual_qualifier || comparison.actual_source?.qualifier)}</SourceLink>
                    {comparison.actual_source?.published_at ? <small>Published {comparison.actual_source.published_at.slice(0, 10)}</small> : comparison.as_of && <small>{validTrendValue(comparison.actual) ? 'As of' : 'Unreported as of'} {comparison.as_of.slice(0, 10)}</small>}
                    {comparison.actual_source?.measure_basis === 'cash_ppe_plus_finance_lease_principal' && <details className="earnings-guidance-revisions"><summary>How this actual was calculated</summary><p>{comparison.actual_source.calculation?.formula}</p><CapexCalculationSources point={{ ...comparison.actual_source, period: comparison.period, kind: 'actual', value: comparison.actual }} /></details>}
                  </td>
                  <td>{validTrendValue(comparison.variance) ? `${approximateGuidanceComparison(comparison) ? '≈' : ''}${comparison.variance > 0 ? '+' : ''}${formatTrendValue(comparison.variance, 'USD billions')}` : 'Not comparable'}{guidanceComparisonLabel(comparison) && <small>{guidanceComparisonLabel(comparison)}</small>}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
        <p className="earnings-trend-note">Difference is actual spending less the midpoint of the earliest guidance captured. A positive difference means management spent more. Captured guidance may not be the company’s first forecast.</p>
        {guidance.comparisons.some(approximateGuidanceComparison) && <p className="earnings-trend-note">≈ marks approximate amounts and differences calculated from them. Statements of “less than” or “greater than” are excluded from exact guidance comparisons.</p>}
        {guidance.coverage && <p className="earnings-trend-note">{guidance.coverage}</p>}
        {!!guidance.explanations?.length && (
          <div className="earnings-capex-explanations">
            <h4>Management’s explanation of spending changes</h4>
            {guidance.explanations.map((explanation, index) => {
              const commentary = distinctExplanationText(explanation.text, explanation.quote)
              return <div className="earnings-capex-explanation" key={index}>
                {(explanation.period || explanation.published_at) && <p className="earnings-capex-explanation-meta">
                  {explanation.period}{explanation.period && explanation.published_at ? ' · ' : ''}
                  {explanation.published_at ? `Published ${explanation.published_at.slice(0, 10)}` : ''}
                </p>}
                {commentary && <p>{commentary}</p>}
                {explanation.interpretation && <p><strong>Research context:</strong> {explanation.interpretation}</p>}
                {explanation.quote && <blockquote>“{explanation.quote}”</blockquote>}
                <SourceLink source={explanation} label={`${explanation.period ? `${explanation.period}, ` : ''}management’s explanation of capex changes`} />
              </div>
            })}
          </div>
        )}
        {!guidance.explanations?.length && <p className="earnings-trend-note">No sourced explanation for a guidance difference was captured. Spending above guidance alone does not establish why it happened.</p>}
      </div>
      </details>
    </div>
  )
}

export default function EarningsTrends({
  series,
  choices,
  onSelect,
  loading = false,
  emptyMessage,
}: {
  series?: EarningsTrendSeries
  choices: EarningsTrendSeries[]
  onSelect: (id: string) => void
  loading?: boolean
  emptyMessage?: string
}) {
  const id = useId()
  const coverage = series ? trendSeriesCoverage(series) : null
  return (
    <div className="earnings-area-trends" aria-busy={loading}>
      <div className="earnings-trend-heading">
        <span className="earnings-trend-kicker">Trend</span>
        {loading && <span className="earnings-trend-loading" role="status">Updating sources…</span>}
      </div>
      {series ? (
        <>
          {choices.length > 1 ? (
            <div className="earnings-trend-selector" role="group" aria-label="Choose historical metric">
              {choices.map((choice) => <button type="button" key={choice.id} aria-pressed={choice.id === series.id} onClick={() => onSelect(choice.id)}>{choice.label}{isCapexSeries(choice) && <small className="earnings-trend-coverage">{trendSeriesCoverageLabel(choice)}</small>}</button>)}
            </div>
          ) : <h4 id={id}>{series.label}</h4>}
          {choices.length === 1 && isCapexSeries(series) && <p className="earnings-trend-note">{trendSeriesCoverageLabel(series)}</p>}
          <p className="earnings-trend-basis">{series.basis}</p>
          {isCapexSeries(series) && <p className="earnings-trend-note earnings-capex-basis-note">{CAPEX_BASIS_NOTE}</p>}
          {series.points.some((point) => point.measure_basis === 'cash_ppe_plus_finance_lease_principal') && <p className="earnings-trend-note">Calculated actuals add cash PP&E purchases and finance lease principal from the same filing, using the company’s stated capex definition. Values & sources shows both inputs.</p>}
          {isCapexSeries(series) && coverage?.reported === 0 && !!coverage.guidance && <p className="earnings-trend-note">Only guidance is available for this measure; historical actuals are still missing.</p>}
          <TrendChart series={series} />
          <TrendSources key={series.id} series={series} />
        </>
      ) : (
        <p className="earnings-trend-empty" role={loading ? 'status' : undefined}>
          {loading ? 'Collecting comparable figures from earnings materials…' : emptyMessage || 'No comparable numerical history was found for this topic. The management excerpt is available in context.'}
        </p>
      )}
    </div>
  )
}
