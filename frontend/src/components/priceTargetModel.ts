import type { CioPricePlan, EvidenceRef, ValuationBlock, ValuationInput, ValuationMethod, ValuationResearchContext, ValuationScenarioCalculation, ValuationScenarioName } from '../types'

export type RecordedPriceTarget = CioPricePlan & { price?: string | number | null }
export type PriceTargetSource = EvidenceRef & { quote?: string | null; publishedAt?: string | null }
export type TargetScenario = { name: ValuationScenarioName; value: string | null; calculation: ValuationScenarioCalculation | null }
export type PriceTargetModel = {
  status: 'complete' | 'partial' | 'unavailable' | 'legacy'
  base: string | null
  currency: string | null
  horizon: string | null
  asOf: string | null
  rationale: string | null
  method: ValuationMethod | null
  scenarios: TargetScenario[]
  missing: string[]
  sourceIds: string[]
  researchContext: ValuationResearchContext | null
}
const text = (value: unknown): string | null => typeof value === 'string' && value.trim() ? value.trim() : null
export const targetSourceIds = (value: unknown): string[] => Array.isArray(value) ? [...new Set(value.filter((item): item is string => typeof item === 'string' && !!item.trim()).map((item) => item.trim()))] : []
export function targetNumber(value: unknown): string | null {
  if (typeof value !== 'string' && typeof value !== 'number') return null
  if (typeof value === 'string' && !/^\d+(?:\.\d+)?$/.test(value.trim())) return null
  const number = Number(value)
  return Number.isFinite(number) && number > 0 ? String(value).trim() : null
}
export function targetPrice(value: unknown, currency?: string | null): string {
  const valid = targetNumber(value)
  if (!valid) return 'Not supplied'
  const formatted = Number(valid).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  return currency ? `${currency} ${formatted}` : formatted
}
export function targetSourceUrl(value: unknown): string | null {
  if (typeof value !== 'string') return null
  try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : null } catch { return null }
}
function inputRefs(inputs: ValuationInput[] | undefined) { return (inputs ?? []).flatMap((input) => targetSourceIds(input.source_refs)) }

export function buildPriceTargetModel(valuation: ValuationBlock | null | undefined, fallback: { futureTarget?: RecordedPriceTarget | null; horizon?: string | null; asOf?: string | null } = {}): PriceTargetModel {
  const methods = Array.isArray(valuation?.methods) ? valuation.methods : []
  const method = methods.find((item) => item.name === valuation?.selected_method) ?? methods.find((item) => item.supported === true && item.status === 'complete') ?? null
  const scenarios: TargetScenario[] = (['bear', 'base', 'bull'] as const).map((name) => {
    const calculation = method?.scenario_calculations?.[name] ?? null
    return { name, value: targetNumber(valuation?.scenarios?.[name]), calculation }
  })
  // Old case targets remain visible as historical records, never as a newly
  // calculated valuation or as substitutes for an incomplete canonical block.
  const legacyPrice = targetNumber(fallback.futureTarget?.price ?? fallback.futureTarget?.value)
  const legacy = !valuation && !!legacyPrice
  const base = scenarios[1].value ?? (legacy ? legacyPrice : null)
  const currency = text(valuation?.currency) ?? (legacy ? text(fallback.futureTarget?.currency) : null)
  const horizon = text(valuation?.horizon) ?? text(fallback.horizon) ?? (legacy ? text(fallback.futureTarget?.horizon) : null)
  const rationale = text(valuation?.rationale) ?? text(method?.rationale) ?? text(valuation?.reconciliation_rationale) ?? (legacy ? text(fallback.futureTarget?.basis) : null)
  const missing = [...new Set([...(valuation?.missing_inputs ?? []), ...(method?.missing_inputs ?? []), ...(!base ? ['A supported base price target is still required.'] : []), ...(!currency ? ['Target currency is not recorded.'] : []), ...(!horizon ? ['Target horizon is not recorded.'] : []), ...(!method && !legacy ? ['A supported calculation method is still required.'] : [])])]
  const researchContext = valuation?.research_context ?? null
  const contextRefs = [...Object.values(researchContext?.historical_multiples ?? {}).flatMap((series) => (series.points ?? []).flatMap((point) => targetSourceIds(point.source_refs))), ...Object.values(researchContext?.provider_multiples ?? {}).flatMap((series) => (series.points ?? []).flatMap((point) => targetSourceIds(point.source_refs))), ...(researchContext?.earnings_bridge?.quarters ?? []).flatMap((quarter) => targetSourceIds(quarter.source_refs)), ...(researchContext?.historical_pe?.points ?? []).flatMap((point) => targetSourceIds(point.source_refs)), ...targetSourceIds(researchContext?.implied_today?.source_refs), ...targetSourceIds(researchContext?.current_earnings?.source_refs), ...(researchContext?.current_earnings?.components ?? []).flatMap((component) => targetSourceIds(component.source_refs))]
  const sourceIds = [...new Set([...(method?.source_refs ?? []), ...inputRefs(method?.inputs), ...scenarios.flatMap((scenario) => inputRefs(scenario.calculation?.inputs)), ...contextRefs, ...(legacy ? targetSourceIds(fallback.futureTarget?.source_refs) : [])])]
  const status = legacy ? 'legacy' : base && valuation?.status === 'complete' && method?.supported === true && currency && horizon ? 'complete' : base ? 'partial' : 'unavailable'
  return { status, base, currency, horizon, asOf: text(valuation?.as_of) ?? text(fallback.asOf), rationale, method, scenarios, missing, sourceIds, researchContext }
}

export const valuationMethodLabel = (name?: string | null) => ({ eps_multiple: 'P/E · earnings', ps_multiple: 'P/S · sales', ev_ebitda: 'EV/EBITDA · operating profit', ev_multiple: 'Enterprise-value multiple', nav_multiple: 'NAV / book equity', nav: 'Net asset value', dcf: 'Discounted cash flow' }[name || ''] || name?.replaceAll('_', ' ') || 'Not recorded')

export function valuationMethodView(valuation: ValuationBlock | null | undefined, name: string | null): ValuationBlock | null | undefined {
  if (!valuation || !name || name === valuation.selected_method) return valuation
  const method = valuation.methods?.find((item) => item.name === name && item.supported && item.status === 'complete')
  if (!method) return valuation
  const today = method.intermediate_results?.implied_today as ValuationResearchContext['implied_today'] | undefined
  return { ...valuation, selected_method: name, scenarios: method.output_prices ?? {}, rationale: method.rationale,
    research_context: { ...valuation.research_context, implied_today: today ?? { status: 'unavailable', scenarios: {} } } }
}

export function libraryTargetIsLoading({ revision, packetProvenance, packetRunId, latestRunId, runStatus, hasPackets }: { revision: string; packetProvenance: unknown; packetRunId?: string; latestRunId?: string; runStatus?: string; hasPackets: boolean }): boolean {
  return revision === 'current' && packetProvenance !== 'canonical_case' && !!latestRunId &&
    ['pending', 'queued', 'running', 'waiting', 'researching', 'ideating'].includes(runStatus || '') &&
    (!hasPackets || packetRunId === latestRunId)
}

export type TargetProsePart = { text: string; sourceId?: string; locator?: string; original?: string }
export type TargetCitationRow = { id: string; number: number; source?: PriceTargetSource; locators: string[] }

export function targetProseParts(value: string): TargetProsePart[] {
  // Library answer segments are already normalized by its backend reader.
  // Valuation prose retains source tokens, so preserve their exact position
  // and original locator here without assigning a bibliography to a claim.
  const pattern = /\bsrc_[A-Za-z0-9_-]+(?:(?:\s*:\s*|\s+)L\d+(?:\s*[-–]\s*L?\d+)?(?:\s*,\s*L\d+(?:\s*[-–]\s*L?\d+)?)*)?/g
  const parts: TargetProsePart[] = []
  let cursor = 0
  for (const match of value.matchAll(pattern)) {
    const raw = match[0].trimEnd()
    const id = raw.match(/^src_[A-Za-z0-9_-]+/)![0]
    const locator = raw.slice(id.length).replace(/^\s*:\s*/, '').trim() || undefined
    let first = match.index!
    let last = first + raw.length
    const wrappers: Record<string, string> = { '[': ']', '(': ')', '`': '`' }
    if (first > cursor && wrappers[value[first - 1]] === value[last]) { first -= 1; last += 1 }
    if (first > cursor) parts.push({ text: value.slice(cursor, first) })
    parts.push({ text: '', sourceId: id, locator, original: value.slice(first, last) })
    cursor = last
  }
  if (cursor < value.length) parts.push({ text: value.slice(cursor) })
  return parts.length ? parts : [{ text: value }]
}

export function targetCitationRows(sourceIds: string[], prose: unknown, sources: PriceTargetSource[]): TargetCitationRow[] {
  const rows = new Map<string, TargetCitationRow>()
  const add = (id: string, locator?: string) => {
    let row = rows.get(id)
    if (!row) {
      const source = sources.find((item) => item.id === id || item.source_ref === id)
      row = { id, number: rows.size + 1, source, locators: source?.locator ? [source.locator] : [] }
      rows.set(id, row)
    }
    if (locator && !row.locators.includes(locator)) row.locators.push(locator)
  }
  sourceIds.forEach((id) => add(id))
  const visit = (value: unknown) => {
    if (typeof value === 'string') targetProseParts(value).forEach((part) => { if (part.sourceId) add(part.sourceId, part.locator) })
    else if (Array.isArray(value)) value.forEach(visit)
    else if (value && typeof value === 'object') Object.values(value).forEach(visit)
  }
  visit(prose)
  return [...rows.values()]
}

export function targetInputDisplay(key: string | undefined, value: unknown): { value: string; unitHandled: boolean } {
  const raw = String(value ?? 'Not recorded')
  if (!/^-?\d+(?:\.\d+)?$/.test(raw) || !Number.isFinite(Number(raw))) return { value: raw, unitHandled: false }
  if (key === 'annual_eps_growth' || key === 'annual_metric_growth') return { value: Number(raw).toLocaleString(undefined, { style: 'percent', maximumFractionDigits: 2 }), unitHandled: true }
  if (['exit_pe', 'exit_multiple', 'exit_ev_multiple'].includes(key || '')) return { value: `${Number(raw).toLocaleString(undefined, { maximumFractionDigits: 2 })}×`, unitHandled: true }
  return { value: raw, unitHandled: false }
}

export function targetInputLabel(key: string): string {
  const labels: Record<string, string> = {
    baseline_eps: 'Starting reported EPS', adjusted_baseline_eps: 'Starting EPS after adjustments',
    annual_eps_growth: 'Assumed annual EPS growth', forecast_diluted_eps: 'Forecast diluted EPS',
    exit_pe: 'Assumed target P/E', horizon_months: 'Holding horizon · months',
    baseline_revenue: 'Reported revenue', baseline_ebitda: 'Reported EBITDA', baseline_nav: 'Reported NAV / book equity',
    baseline_metric: 'Reported baseline', forecast_metric: 'Forecast financial measure', annual_metric_growth: 'Assumed annual growth',
    exit_multiple: 'Assumed target multiple', equity_bridge: 'Cash less debt and other claims',
    forecast_years: 'Years from earnings baseline to forecast',
  }
  return labels[key] ?? key.replaceAll('_', ' ')
}

export function targetTimeExplanation(calculation: ValuationScenarioCalculation | null, horizon: string | null): string | null {
  const years = targetNumber(calculation?.intermediate_results?.forecast_years)
  if (!years) return null
  const span = `${Number(years).toLocaleString(undefined, { maximumFractionDigits: 2 })} ${Number(years) === 1 ? 'year' : 'years'}`
  const baselineInput = calculation?.inputs?.find((input) => ['baseline_eps', 'baseline_revenue', 'baseline_ebitda', 'baseline_nav', 'baseline_operating_income'].includes(input.key || ''))
  const baseline = baselineInput?.period
  const metric = baselineInput?.key === 'baseline_eps' ? 'earnings' : 'financial'
  return `The growth exponent covers ${span} from the ${metric} baseline${baseline ? ` (${baseline})` : ''} to the forecast ${metric} period.${horizon ? ` The holding horizon is ${horizon} from the assessment date.` : ''} These measure different periods.`
}

/** Zero and negative EPS are valid observations; missing values must never become zero. */
export function contextNumber(value: unknown): number | null {
  if (typeof value !== 'number' && typeof value !== 'string') return null
  if (typeof value === 'string' && !/^-?\d+(?:\.\d+)?$/.test(value.trim())) return null
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

export function contextNumberLabel(value: unknown, fractionDigits = 2): string {
  const number = contextNumber(value)
  return number === null ? 'Not available' : number.toLocaleString(undefined, { minimumFractionDigits: fractionDigits, maximumFractionDigits: fractionDigits })
}

export function earningsQuarterLabel(kind: string | undefined): string {
  return kind === 'reported' ? 'Reported actual' : kind === 'projection' ? 'Projection' : 'Evidence missing'
}

export function targetTodayCalculation(context: ValuationResearchContext | null, scenario: ValuationScenarioName, calculation: ValuationScenarioCalculation | null): string | null {
  const today = context?.implied_today
  if (!today || today.status === 'unavailable') return null
  const eps = targetNumber(today.eps)
  const multiple = targetNumber(calculation?.intermediate_results?.exit_pe)
  const price = targetNumber(today.scenarios?.[scenario])
  if (!eps || !multiple || !price) return null
  return `Reported EPS ${contextNumberLabel(eps)} × P/E ${Number(multiple).toLocaleString(undefined, { maximumFractionDigits: 2 })} = ${targetPrice(price, today.currency)}`
}

export function historicalPeSegments(points: NonNullable<ValuationResearchContext['historical_pe']>['points'], sampling: 'monthly' | 'quarterly' | 'annual' = 'monthly') {
  const segments: Array<Array<{ date: string; value: number; index: number }>> = []
  let segment: Array<{ date: string; value: number; index: number }> = []
  for (const [index, point] of (points ?? []).entries()) {
    const value = contextNumber(point.pe)
    if (value === null || value <= 0 || Number.isNaN(Date.parse(point.date))) {
      if (segment.length) segments.push(segment)
      segment = []
    } else {
      const priorDate = segment[segment.length - 1]?.date
      if (priorDate) {
        const prior = new Date(priorDate)
        const next = new Date(point.date)
        const months = (next.getUTCFullYear() - prior.getUTCFullYear()) * 12 + next.getUTCMonth() - prior.getUTCMonth()
        if (months > (sampling === 'annual' ? 12 : sampling === 'quarterly' ? 3 : 1)) { segments.push(segment); segment = [] }
      }
      segment.push({ date: point.date, value, index })
    }
  }
  if (segment.length) segments.push(segment)
  return segments
}

export function historicalMultipleStats(points: Array<{ date: string; pe?: unknown }>, asOf?: string | null) {
  const dates = points.map((point) => Date.parse(point.date)).filter(Number.isFinite)
  const end = asOf && Number.isFinite(Date.parse(asOf)) ? Date.parse(asOf) : Math.max(...dates)
  const start = end - 365.25 * 5 * 24 * 60 * 60 * 1000
  const retained = points.filter((point) => {
    const date = Date.parse(point.date)
    const value = contextNumber(point.pe)
    return date >= start && date <= end && value !== null && value > 0
  })
  const values = retained.map((point) => contextNumber(point.pe)!)
  if (!values.length) return { points: retained, mean: null, deviation: null, bands: [], minimum: 1, maximum: 2 }
  const mean = values.reduce((sum, value) => sum + value, 0) / values.length
  const deviation = values.length > 1 ? Math.sqrt(values.reduce((sum, value) => sum + (value - mean) ** 2, 0) / values.length) : null
  const bands = deviation === null || deviation === 0 ? [{ sigma: 0, value: mean }] : [-3, -2, -1, 0, 1, 2, 3].map((sigma) => ({ sigma, value: mean + sigma * deviation }))
  const extent = [...values, ...bands.map((band) => band.value)]
  const low = Math.min(...extent), high = Math.max(...extent)
  const padding = Math.max((high - low) * 0.08, Math.abs(mean) * 0.015, 0.05)
  return { points: retained, mean, deviation, bands, minimum: low - padding, maximum: high + padding }
}
