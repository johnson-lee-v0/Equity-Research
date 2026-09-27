export type EarningsTrendQualifier = 'less_than' | 'approximately' | 'greater_than'

export type EarningsTrendSource = {
  source_id?: string
  url?: string
  title?: string
  kind?: string
  quote?: string
  published_at?: string
  qualifier?: EarningsTrendQualifier | null
  source_method?: string
  measure_basis?: string
  definition_source?: EarningsTrendSource
  calculation?: { formula?: string; inputs?: unknown; basis?: string; rounding?: string }
}

export type EarningsTrendPoint = EarningsTrendSource & {
  period: string
  period_end?: string
  value: number | null
  kind: 'actual' | 'guidance' | 'projection'
  low?: number | null
  high?: number | null
  gap_reason?: string
  rationale?: string
  source_kind?: string
}

export type EarningsTrendSeries = {
  id: string
  label: string
  unit: string
  frequency: 'quarterly' | 'annual'
  basis: string
  area_ids: string[]
  points: EarningsTrendPoint[]
}

export type CapexGuidanceComparison = {
  period: string
  initial_low: number | null
  initial_high: number | null
  actual: number | null
  initial_qualifier?: EarningsTrendQualifier | null
  actual_qualifier?: EarningsTrendQualifier | null
  approximate?: boolean
  variance?: number | null
  variance_pct?: number | null
  within_range?: boolean
  initiality?: string
  as_of?: string
  initial_source?: EarningsTrendSource
  actual_source?: EarningsTrendSource
  revisions?: (EarningsTrendSource & { low: number | null; high: number | null })[]
}

export type EarningsTrendsResult = {
  version: string
  status: 'complete' | 'partial' | 'unavailable'
  as_of?: string
  series: EarningsTrendSeries[]
  capex_guidance?: {
    comparisons: CapexGuidanceComparison[]
    summary?: string
    explanations?: (EarningsTrendSource & { text: string; period?: string; interpretation?: string })[]
    coverage?: string
  }
  sources?: EarningsTrendSource[]
  gaps?: string[]
}

const numberFormat = new Intl.NumberFormat('en-US', { maximumFractionDigits: 3 })

export function validTrendValue(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

export function formatTrendValue(value: number | null | undefined, unit: string): string {
  if (!validTrendValue(value)) return 'Not available'
  if (unit === 'percent') return `${numberFormat.format(value)}%`
  if (unit === 'USD billions') return `${value < 0 ? '−' : ''}$${numberFormat.format(Math.abs(value))}bn`
  return `${numberFormat.format(value)}${unit ? ` ${unit}` : ''}`
}

export function formatTrendRange(
  low: number | null | undefined,
  high: number | null | undefined,
  unit: string,
): string {
  if (validTrendValue(low) && validTrendValue(high) && low !== high)
    return `${formatTrendValue(low, unit)}–${formatTrendValue(high, unit)}`
  return formatTrendValue(validTrendValue(low) ? low : high, unit)
}

export function qualifiedTrendLabel(label: string, qualifier: EarningsTrendQualifier | null | undefined): string {
  const prefix = qualifier === 'less_than' ? '<' : qualifier === 'greater_than' ? '>' : qualifier === 'approximately' ? '≈' : ''
  return `${label === 'Not available' ? '' : prefix}${label}`
}

export function trendPointLabel(point: EarningsTrendPoint, unit: string): string {
  if (point.kind === 'guidance' && validTrendValue(point.low) && validTrendValue(point.high))
    return qualifiedTrendLabel(formatTrendRange(point.low, point.high, unit), point.qualifier)
  return qualifiedTrendLabel(formatTrendValue(point.value, unit), point.qualifier)
}

export function trendPointDescription(point: EarningsTrendPoint, unit: string): string {
  return trendPointLabel(point, unit).replace(/^</, 'Less than ').replace(/^>/, 'Greater than ').replace(/^≈/, 'Approximately ')
}

export function trendEvidenceLabel(point: EarningsTrendPoint): string {
  if (!validTrendValue(point.value)) return 'Missing observation'
  if (point.kind === 'actual' && point.measure_basis === 'cash_ppe_plus_finance_lease_principal' && point.calculation?.formula) return 'Calculated actual'
  return point.kind === 'guidance' ? 'Management guidance' : point.kind === 'projection' ? 'Projection' : 'Reported actual'
}

export function trendGapExplanation(point: EarningsTrendPoint): string | null {
  if (validTrendValue(point.value)) return null
  return point.gap_reason?.trim() || 'A comparable figure was not found in the retained sources for this period. This gap is not a zero.'
}

/** Include zero, missing observations and both ends of a guidance range honestly. */
export function trendScale(points: EarningsTrendPoint[]) {
  const values = points.flatMap((point) =>
    [point.value, point.kind === 'guidance' ? point.low : null, point.kind === 'guidance' ? point.high : null]
      .filter(validTrendValue),
  )
  const min = Math.min(0, ...values)
  const max = Math.max(0, ...values)
  return { min, max: min === max ? max + 1 : max }
}

/** Guidance must never become the endpoint of an observed historical change. */
export function reportedTrendChange(series: EarningsTrendSeries): string | null {
  const observed = series.points.filter(
    (point) => point.kind === 'actual' && validTrendValue(point.value) && point.qualifier !== 'less_than' && point.qualifier !== 'greater_than',
  )
  if (observed.length < 2) return null
  const first = observed[0]
  const last = observed[observed.length - 1]
  const change = Math.round(((last.value as number) - (first.value as number)) * 1000) / 1000
  const magnitude = numberFormat.format(Math.abs(change))
  const direction = change > 0 ? 'up' : change < 0 ? 'down' : 'unchanged'
  const unit = series.unit === 'percent' ? 'percentage points' : series.unit
  const approximate = first.qualifier === 'approximately' || last.qualifier === 'approximately'
  return change === 0
    ? `${approximate ? 'Approximately unchanged' : 'Unchanged'} from ${first.period} to ${last.period}.`
    : `${approximate ? 'Approximately ' : ''}${magnitude} ${unit} ${direction} from ${first.period} to ${last.period}.`
}

export function seriesForArea(result: EarningsTrendsResult | null | undefined, areaId: string) {
  return (result?.series || []).filter((series) => series.area_ids.includes(areaId))
}

export function isCapexSeries(series: EarningsTrendSeries): boolean {
  return series.id === 'capex' || series.id === 'capex_cash_ppe'
}

/** Count distinct reported periods; guidance and projections are not history. */
export function trendSeriesCoverage(series: EarningsTrendSeries) {
  const actuals = series.points.filter((point) => point.kind === 'actual')
  return {
    reported: new Set(actuals.filter((point) => validTrendValue(point.value)).map((point) => point.period)).size,
    periods: new Set(actuals.map((point) => point.period)).size,
    guidance: new Set(series.points.filter((point) => point.kind === 'guidance' && (
      validTrendValue(point.value) || (validTrendValue(point.low) && validTrendValue(point.high))
    )).map((point) => point.period)).size,
  }
}

export function trendSeriesCoverageLabel(series: EarningsTrendSeries): string {
  const coverage = trendSeriesCoverage(series)
  const period = series.frequency === 'annual' ? 'year' : 'quarter'
  const reported = `${coverage.periods ? `${coverage.reported}/${coverage.periods}` : coverage.reported} reported ${period}${coverage.periods === 1 || (!coverage.periods && coverage.reported === 1) ? '' : 's'}`
  return `${reported}${coverage.guidance ? ` · ${coverage.guidance} guidance ${period}${coverage.guidance === 1 ? '' : 's'}` : ''}`
}

/** Preserve explicit choices; initially show the available capex history. */
export function selectTrendSeries(choices: EarningsTrendSeries[], selectedId?: string, preferredId?: string): EarningsTrendSeries | undefined {
  const explicit = choices.find((series) => series.id === selectedId)
  if (explicit) return explicit
  const preferred = choices.find((series) => series.id === preferredId) || choices[0]
  if (!preferred || !isCapexSeries(preferred)) return preferred
  return choices.filter(isCapexSeries).reduce((selected, candidate) =>
    trendSeriesCoverage(candidate).reported > trendSeriesCoverage(selected).reported ? candidate : selected,
  preferred)
}

export const CAPEX_BASIS_NOTE = 'Cash PP&E is cash spent on property, plant and equipment. Management capex follows the company’s stated definition and may include other spending, such as finance lease payments. Cash PP&E alone is not a substitute for management capex when checking guidance.'

export function safeTrendUrl(url: string | undefined): string | undefined {
  if (!url) return undefined
  try {
    const parsed = new URL(url)
    return parsed.protocol === 'https:' || parsed.protocol === 'http:' ? url : undefined
  } catch {
    return undefined
  }
}

/** Extraction may retain the same verbatim passage as both text and quote. */
export function distinctExplanationText(text: string, quote: string | undefined): string | null {
  const normalized = (value: string) => value.replace(/\s+/g, ' ').trim()
  return quote && normalized(text) === normalized(quote) ? null : text.trim() || null
}

export function approximateGuidanceComparison(comparison: CapexGuidanceComparison): boolean {
  return comparison.approximate === true || [comparison.initial_qualifier, comparison.actual_qualifier, comparison.initial_source?.qualifier, comparison.actual_source?.qualifier].includes('approximately')
}

export function guidanceComparisonLabel(comparison: CapexGuidanceComparison): string | null {
  if (!validTrendValue(comparison.actual) || !validTrendValue(comparison.initial_low) || !validTrendValue(comparison.initial_high)) return null
  if (approximateGuidanceComparison(comparison)) {
    if (comparison.initial_qualifier === 'approximately' || comparison.initial_source?.qualifier === 'approximately') return 'Compared with approximate guidance'
    if (comparison.actual_qualifier === 'approximately' || comparison.actual_source?.qualifier === 'approximately') return 'Comparison uses approximate actual spending'
    return 'Comparison uses approximate figures'
  }
  if (comparison.initial_low !== comparison.initial_high && typeof comparison.within_range === 'boolean') return comparison.within_range ? 'Within guidance range' : 'Outside guidance range'
  if (comparison.initial_low === comparison.initial_high) return comparison.actual > comparison.initial_low ? 'Above stated guidance' : comparison.actual < comparison.initial_low ? 'Below stated guidance' : 'Matches stated guidance'
  return null
}

export const CAPEX_COMPARISON_NOTE = 'Above/below counts compare the stated amounts with the captured point estimate or range boundaries. Differences use the point estimate or range midpoint. Approximate amounts have no stated tolerance; these comparisons describe numerical differences without grading guidance as a hit or miss.'
