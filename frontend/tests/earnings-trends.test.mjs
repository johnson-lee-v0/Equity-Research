import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'

const source = await readFile(new URL('../src/panels/research/earningsTrendModel.ts', import.meta.url), 'utf8')
const javascript = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText
const { approximateGuidanceComparison, guidanceComparisonLabel, CAPEX_BASIS_NOTE, CAPEX_COMPARISON_NOTE, formatTrendValue, trendPointLabel, trendPointDescription, trendEvidenceLabel, trendGapExplanation, trendScale, reportedTrendChange, seriesForArea, selectTrendSeries, trendSeriesCoverage, trendSeriesCoverageLabel, safeTrendUrl, distinctExplanationText } =
  await import(`data:text/javascript;base64,${Buffer.from(javascript).toString('base64')}`)

test('missing observations remain unavailable, including non-finite input; a real zero is retained', () => {
  for (const value of [null, undefined, NaN, Infinity]) assert.equal(formatTrendValue(value, 'percent'), 'Not available')
  assert.equal(formatTrendValue(0, 'percent'), '0%')
})

test('renewal rates use a zero baseline rather than magnifying a fraction of a percentage point', () => {
  assert.deepEqual(trendScale([
    { value: 93, kind: 'actual' },
    { value: null, kind: 'actual' },
    { value: 92.7, kind: 'actual' },
  ]), { min: 0, max: 93 })
})

test('the chart domain includes negative growth and the full guidance range', () => {
  assert.deepEqual(trendScale([
    { value: -1.5, kind: 'actual' },
    { value: 7.25, kind: 'guidance', low: 7, high: 7.5 },
  ]), { min: -1.5, max: 7.5 })
  assert.equal(trendPointLabel({ value: 7.25, kind: 'guidance', low: 7, high: 7.5 }, 'USD billions'), '$7bn–$7.5bn')
})

test('historical change excludes future guidance and uses percentage points for rates', () => {
  assert.equal(reportedTrendChange({ unit: 'percent', points: [
    { period: 'FY25 Q1', value: 93, kind: 'actual' },
    { period: 'FY25 Q2', value: null, kind: 'actual' },
    { period: 'FY25 Q3', value: 92.7, kind: 'actual' },
    { period: 'FY25 Q4', value: 99, kind: 'guidance' },
  ] }), '0.3 percentage points down from FY25 Q1 to FY25 Q3.')
})

test('a single observation plus a forecast cannot establish a reported trend', () => {
  assert.equal(reportedTrendChange({ unit: 'USD billions', points: [
    { period: 'FY25', value: 6, kind: 'actual' },
    { period: 'FY26', value: 7.5, kind: 'guidance' },
  ] }), null)
})

test('different metrics and accounting bases are selected separately rather than merged', () => {
  const result = { series: [
    { id: 'net_sales_growth', area_ids: ['demand'], basis: 'Quarterly reported net sales growth' },
    { id: 'paid_members_growth', area_ids: ['demand'], basis: 'Paid household growth' },
    { id: 'capex', area_ids: ['capital', 'outlook'], basis: 'Management reported capex' },
    { id: 'capex_cash_ppe', area_ids: ['capital', 'outlook'], basis: 'Cash purchases of PP&E' },
  ] }
  assert.deepEqual(seriesForArea(result, 'capital').map((series) => series.id), ['capex', 'capex_cash_ppe'])
  assert.deepEqual(seriesForArea(result, 'demand').map((series) => series.id), ['net_sales_growth', 'paid_members_growth'])
  assert.deepEqual(seriesForArea(undefined, 'demand'), [])
})

test('source citations allow web evidence but not executable URLs', () => {
  assert.equal(safeTrendUrl('https://investor.example.com/earnings'), 'https://investor.example.com/earnings')
  assert.equal(safeTrendUrl('javascript:alert(1)'), undefined)
  assert.equal(safeTrendUrl('file:///tmp/evidence'), undefined)
})

test('a verbatim capex explanation is printed once while distinct commentary is retained', () => {
  const quote = 'This increase reflects the timing of planned warehouse openings.'
  assert.equal(distinctExplanationText(quote, quote), null)
  assert.equal(distinctExplanationText(` ${quote.replaceAll(' ', '\n')} `, quote), null)
  assert.equal(distinctExplanationText('Management attributed the change to project timing.', quote), 'Management attributed the change to project timing.')
  assert.equal(distinctExplanationText(quote, undefined), quote)
})

test('bounded and approximate source figures retain their qualifiers in visible and accessible labels', () => {
  const under = { period: 'FY25', kind: 'actual', value: 5.5, qualifier: 'less_than' }
  assert.equal(trendPointLabel(under, 'USD billions'), '<$5.5bn')
  assert.equal(trendPointDescription(under, 'USD billions'), 'Less than $5.5bn')
  assert.equal(trendPointLabel({ ...under, qualifier: 'approximately' }, 'USD billions'), '≈$5.5bn')
  assert.equal(trendPointDescription({ ...under, qualifier: 'greater_than' }, 'USD billions'), 'Greater than $5.5bn')
})

test('a reported boundary cannot establish an exact historical change', () => {
  assert.equal(reportedTrendChange({ unit: 'USD billions', points: [
    { period: 'FY24', value: 4.71, kind: 'actual' },
    { period: 'FY25', value: 5.5, kind: 'actual', qualifier: 'less_than' },
  ] }), null)
  assert.equal(reportedTrendChange({ unit: 'USD billions', points: [
    { period: 'FY24', value: 4.71, kind: 'actual' },
    { period: 'FY25', value: 5.5, kind: 'actual', qualifier: 'approximately' },
  ] }), 'Approximately 0.79 USD billions up from FY24 to FY25.')
})

test('approximate point guidance is compared without inventing an exact tolerance', () => {
  for (const [initial, actual] of [[4, 3.9], [6.5, 6.4]]) {
    const comparison = { initial_low: initial, initial_high: initial, actual, variance: -0.1, within_range: false, initial_qualifier: 'approximately' }
    assert.equal(approximateGuidanceComparison(comparison), true)
    assert.equal(guidanceComparisonLabel(comparison), 'Compared with approximate guidance')
    assert.equal(comparison.variance, -0.1)
    assert.equal(comparison.within_range, false) // Presentation does not rewrite the observation.
  }
})
test('an approximate actual or retained source qualifier prevents hard range grading', () => {
  const comparison = { initial_low: 4, initial_high: 5, actual: 5.1, within_range: false }
  assert.equal(guidanceComparisonLabel({ ...comparison, actual_qualifier: 'approximately' }), 'Comparison uses approximate actual spending')
  assert.equal(guidanceComparisonLabel({ ...comparison, initial_source: { qualifier: 'approximately' } }), 'Compared with approximate guidance')
  assert.equal(guidanceComparisonLabel({ ...comparison, approximate: true }), 'Comparison uses approximate figures')
})
test('exact ranges keep their recorded within or outside label and points have no invented range', () => {
  const comparison = { initial_low: 4, initial_high: 5, actual: 4.5, within_range: true }
  assert.equal(guidanceComparisonLabel(comparison), 'Within guidance range')
  assert.equal(guidanceComparisonLabel({ ...comparison, actual: 5.1, within_range: false }), 'Outside guidance range')
  assert.equal(guidanceComparisonLabel({ ...comparison, initial_high: 4, actual: 3.9, within_range: false }), 'Below stated guidance')
  assert.equal(guidanceComparisonLabel({ ...comparison, actual: null }), null)
  assert.match(CAPEX_COMPARISON_NOTE, /range boundaries/)
  assert.match(CAPEX_COMPARISON_NOTE, /range midpoint/)
  assert.match(CAPEX_COMPARISON_NOTE, /no stated tolerance/)
})

test('missing trend observations explain source coverage and do not claim to be reported actuals', () => {
  const missing = { period: 'Q4 FY2025', kind: 'actual', value: null, gap_reason: 'Issuer release did not state this membership metric.' }
  assert.equal(trendEvidenceLabel(missing), 'Missing observation')
  assert.equal(trendGapExplanation(missing), missing.gap_reason)
  assert.match(trendGapExplanation({ ...missing, gap_reason: '' }), /gap is not a zero/)
  assert.equal(trendGapExplanation({ ...missing, value: 0 }), null)
  assert.equal(trendEvidenceLabel({ ...missing, value: 0 }), 'Reported actual')
})

test('forecast observations are identified separately and cannot establish the reported trend', () => {
  const forecast = { period: 'Q4 FY2026', value: 5, kind: 'projection' }
  assert.equal(trendEvidenceLabel(forecast), 'Projection')
  assert.equal(trendGapExplanation(forecast), null)
  assert.equal(reportedTrendChange({ unit: 'EPS', points: [{ period: 'Q3 FY2026', kind: 'actual', value: 4 }, forecast] }), null)
})

function capexSeries(id, values, guidance = []) {
  return { id, frequency: 'annual', points: [
    ...values.map((value, index) => ({ period: `FY${2021 + index}`, kind: 'actual', value })),
    ...guidance.map((value, index) => ({ period: `FY${2026 + index}`, kind: 'guidance', value })),
  ] }
}

test('available cash PP&E history wins over missing or sparse management actuals, even with several forecasts', () => {
  const cash = capexSeries('capex_cash_ppe', [1, 2, 3, 4, 5])
  for (const values of [[null, null, null, null, null], [null, null, null, null, 6]]) {
    const management = capexSeries('capex', values, [7, 8, 9, 10, 11, 12])
    const choices = [management, cash]
    const before = structuredClone(choices)
    assert.equal(selectTrendSeries(choices).id, 'capex_cash_ppe')
    assert.equal(selectTrendSeries(choices, 'capex').id, 'capex', 'An explicit user choice stays selected')
    assert.deepEqual(choices, before, 'Choosing a chart does not merge or mutate either definition')
  }
  assert.match(CAPEX_BASIS_NOTE, /not a substitute for management capex/)
})

test('coverage counts real zero actuals and distinct fiscal years, without counting projections as history', () => {
  const management = capexSeries('capex', [0, null, null, null, null], [8])
  management.points.push({ period: 'FY2021', kind: 'actual', value: 0 })
  management.points.push({ period: 'FY2025', kind: 'projection', value: 7 })
  assert.deepEqual(trendSeriesCoverage(management), { reported: 1, periods: 5, guidance: 1 })
  assert.equal(trendSeriesCoverageLabel(management), '1/5 reported years · 1 guidance year')
  assert.equal(selectTrendSeries([management, capexSeries('capex_cash_ppe', [null])]).id, 'capex')
})

test('equal coverage retains management capex and unrelated metric preferences remain unchanged', () => {
  const management = capexSeries('capex', [1, 2, 3, 4, 5])
  const cash = capexSeries('capex_cash_ppe', [1, 2, 3, 4, 5])
  assert.equal(selectTrendSeries([management, cash]).id, 'capex')
  assert.equal(selectTrendSeries([management, cash], 'removed-metric').id, 'capex')
  assert.equal(selectTrendSeries([{ id: 'members' }, { id: 'net_sales_growth' }], undefined, 'net_sales_growth').id, 'net_sales_growth')
  assert.equal(selectTrendSeries([]), undefined)
})

test('guidance-only history states zero reported years and a verified lease bridge is labeled calculated', () => {
  assert.equal(trendSeriesCoverageLabel(capexSeries('capex', [null, null, null, null, null], [8])), '0/5 reported years · 1 guidance year')
  const bridge = { period: 'FY2025', kind: 'actual', value: 8, measure_basis: 'cash_ppe_plus_finance_lease_principal', calculation: { formula: 'cash + lease' } }
  assert.equal(trendEvidenceLabel(bridge), 'Calculated actual')
  assert.equal(trendEvidenceLabel({ ...bridge, value: null }), 'Missing observation')
})
