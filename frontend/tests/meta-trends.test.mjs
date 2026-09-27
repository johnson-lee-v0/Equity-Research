import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

const require = createRequire(import.meta.url)
const { build } = createRequire(require.resolve('vite'))('esbuild')
const bundle = await build({
  entryPoints: [fileURLToPath(new URL('../src/demo/metaTrends.ts', import.meta.url))],
  bundle: true, write: false, platform: 'node', format: 'cjs', packages: 'external',
  logLevel: 'silent', metafile: true,
})
const compiled = { exports: {} }
new Function('require', 'module', 'exports', bundle.outputFiles[0].text)(require, compiled, compiled.exports)
const { metaQuarterlyFacts, metaAnnualCapexFacts, metaTrendSeries, metaCapexGuidance, metaTrendNotes, metaTrends, META_TRENDS_AS_OF } = compiled.exports
const series = id => metaTrendSeries.find(item => item.id === id)
const close = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-9, `${actual} != ${expected}`)
const sum = values => values.reduce((total, value) => total + value, 0)

test('actual public trend data appears in the correct shared explorer and theme categories', async () => {
  const reviewBundle = await build({
    entryPoints: [fileURLToPath(new URL('../src/panels/research/earningsReviewModel.ts', import.meta.url))],
    bundle: true, write: false, platform: 'node', format: 'cjs', packages: 'external', logLevel: 'silent',
  })
  const module = { exports: {} }
  new Function('require', 'module', 'exports', reviewBundle.outputFiles[0].text)(require, module, module.exports)
  const { groupedEarningsTrends, relatedThemeTrends } = module.exports
  const categories = Object.fromEntries(groupedEarningsTrends(metaTrends).map(category => [category.id, category.series.map(metric => metric.id)]))
  assert.deepEqual(categories, {
    growth: ['revenue', 'revenue_yoy_growth'],
    margins: ['operating_margin'],
    cash: ['diluted_eps', 'operating_cash_flow', 'free_cash_flow'],
    capital: ['capex_quarterly', 'capex'],
    other: [],
  })
  assert.equal(Object.values(categories).flat().length, metaTrendSeries.length)
  assert.deepEqual(relatedThemeTrends(metaTrends, ['Demand and growth']).map(metric => metric.id), ['revenue', 'revenue_yoy_growth'])
  assert.deepEqual(relatedThemeTrends(metaTrends, ['Margins and costs']).map(metric => metric.id), ['operating_margin'])
  assert.deepEqual(relatedThemeTrends(metaTrends, ['Capital and liquidity']).map(metric => metric.id), ['diluted_eps', 'operating_cash_flow', 'free_cash_flow', 'capex_quarterly', 'capex'])
})

test('all seven quarterly metrics share six completed standalone periods and retain source pages', () => {
  const quarters = metaTrendSeries.filter(item => item.frequency === 'quarterly')
  assert.equal(quarters.length, 7)
  assert.deepEqual(quarters.map(item => item.id), ['revenue', 'revenue_yoy_growth', 'operating_margin', 'diluted_eps', 'operating_cash_flow', 'free_cash_flow', 'capex_quarterly'])
  for (const metric of quarters) {
    assert.deepEqual(metric.points.map(point => point.period), ['Q1 FY2025', 'Q2 FY2025', 'Q3 FY2025', 'Q4 FY2025', 'Q1 FY2026', 'Q2 FY2026'])
    assert.deepEqual(metric.points.map(point => point.period_end), ['2025-03-31', '2025-06-30', '2025-09-30', '2025-12-31', '2026-03-31', '2026-06-30'])
    for (const point of metric.points) {
      assert.equal(point.kind, 'actual')
      assert.ok(Number.isFinite(point.value))
      assert.ok(point.period_end <= META_TRENDS_AS_OF)
      assert.match(point.url, /Earnings-Presentation-Q2-2026\.pdf#page=(4|8|15)$/)
    }
  }
})

test('quarter cash flows reconcile and roll into independently reported FY2025 and first-half FY2026 totals', () => {
  for (const row of metaQuarterlyFacts) assert.equal(row.operatingCash - row.cashPpe - row.leasePrincipal, row.freeCash)
  const year2025 = metaQuarterlyFacts.filter(row => row.end.startsWith('2025'))
  // Closing FY2025 release: cash from operations $115,800m, PP&E $69,691m,
  // finance leases $2,524m, non-GAAP free cash $43,585m.
  assert.equal(sum(year2025.map(row => row.operatingCash)), 115800)
  assert.equal(sum(year2025.map(row => row.cashPpe)), 69691)
  assert.equal(sum(year2025.map(row => row.leasePrincipal)), 2524)
  assert.equal(sum(year2025.map(row => row.freeCash)), 43585)
  close(sum(series('capex_quarterly').points.slice(0, 4).map(point => point.value)), 72.215)
  close(series('capex_quarterly').points[2].value, 19.374)
  close(sum(series('capex_quarterly').points.slice(4).map(point => point.value)), 50.918)
  close(series('capex_quarterly').points.at(-1).value, 31.078)
  close(series('operating_cash_flow').points.at(-1).value - series('capex_quarterly').points.at(-1).value, 0.784)
  assert.equal(series('capex_quarterly').points.some(point => point.value === 50.918 || point.value === 50.078), false, 'YTD figures must not become quarterly bars')
})

test('growth uses same-quarter prior-year revenue and margin retains reported GAAP precision', () => {
  assert.deepEqual(series('revenue_yoy_growth').points.map(point => point.value), [16, 22, 26, 24, 33, 28])
  for (const [index, row] of metaQuarterlyFacts.entries()) {
    const growth = series('revenue_yoy_growth').points[index]
    assert.equal(growth.value, Math.round((row.revenue - row.priorRevenue) / row.priorRevenue * 100))
    assert.equal(series('operating_margin').points[index].value, Math.round(row.operatingIncome / row.revenue * 100))
    if (index >= 4) assert.equal(row.priorRevenue, metaQuarterlyFacts[index - 4].revenue)
  }
  const firstPrior = series('revenue_yoy_growth').points[0].calculation.inputs
  assert.equal(firstPrior.prior_revenue_usd_millions, 36455)
  assert.match(firstPrior.prior_source.url, /First-Quarter-2025-Results/)
  assert.equal(series('revenue_yoy_growth').unit, 'percent')
})

test('quarterly CapEx passes both source-bound USD operands to the shared calculation reader', () => {
  for (const point of series('capex_quarterly').points) {
    const inputs = point.calculation.inputs
    assert.ok(Array.isArray(inputs))
    assert.deepEqual(inputs.map(input => input.tag), ['PaymentsToAcquirePropertyPlantAndEquipment', 'FinanceLeasePrincipalPayments'])
    assert.ok(inputs.every(input => input.unit === 'USD' && Number.isFinite(input.value)))
    assert.ok(inputs.every(input => input.url.endsWith('#page=15') && input.source_id === point.source_id))
    close(sum(inputs.map(input => input.value)) / 1_000_000_000, point.value)
    assert.match(point.calculation.formula, /1,000,000,000/)
    assert.ok(point.definition_source.url.endsWith('#page=9'))
  }
  // Independently checked Q3 FY2025 table cells: $18,829m PP&E + $545m leases.
  const q3 = series('capex_quarterly').points.find(point => point.period === 'Q3 FY2025')
  assert.deepEqual(q3.calculation.inputs.map(input => input.value), [18_829_000_000, 545_000_000])
})

test('GAAP EPS history keeps the large tax effects visible instead of substituting adjusted earnings', () => {
  assert.deepEqual(series('diluted_eps').points.map(point => point.value), [6.43, 7.14, 1.05, 8.88, 10.44, 6.18])
  assert.equal(series('diluted_eps').unit, 'USD per share')
  assert.match(series('diluted_eps').points[2].rationale, /15\.93bn.*tax charge/)
  assert.match(series('diluted_eps').points[4].rationale, /8\.03bn.*tax benefit/)
  assert.ok(metaTrendNotes.some(note => note.period === 'Q3 FY2025' && note.sourceUrl.endsWith('#page=8')))
  assert.ok(metaTrendNotes.some(note => note.period === 'Q1 FY2026' && note.sourceUrl.endsWith('#page=8')))
})

test('annual CapEx preserves the net PP&E basis in old releases and keeps current-year guidance separate', () => {
  const annual = series('capex')
  assert.equal(annual.frequency, 'annual')
  assert.deepEqual(annual.points.filter(point => point.kind === 'actual').map(point => point.value), [19.244, 32.036, 28.103, 39.225, 72.215])
  for (const row of metaAnnualCapexFacts) assert.equal(row.cashPpe + row.leasePrincipal, row.total)
  for (const row of metaAnnualCapexFacts.slice(0, 3)) assert.match(row.ppeBasis, /net/)
  // FY2022 gross purchases of $31,431m cannot replace the net $31,186m
  // reconciliation row; doing so would incorrectly produce $32.281bn.
  assert.equal(annual.points[1].value, 32.036)
  assert.notEqual(annual.points[1].value, 32.281)
  const current = annual.points.at(-1)
  assert.equal(current.period, 'FY2026')
  assert.equal(current.kind, 'guidance')
  assert.deepEqual([current.low, current.high, current.value], [130, 145, 137.5])
  assert.match(current.rationale, /actual spending is not yet reported/)
})

test('guidance comparisons use early dated ranges, retain revisions, and never grade the incomplete year', () => {
  const completed = metaCapexGuidance.comparisons.filter(row => row.actual !== null)
  assert.deepEqual(completed.map(row => [row.period, row.initial_low, row.initial_high, row.variance]), [
    ['FY2021', 21, 23, -2.756], ['FY2022', 29, 34, 0.536], ['FY2023', 34, 39, -8.397], ['FY2024', 30, 35, 6.725], ['FY2025', 60, 65, 9.715],
  ])
  assert.equal(completed.filter(row => row.actual < row.initial_low).length, 2)
  assert.equal(completed.filter(row => row.within_range).length, 1)
  assert.equal(completed.filter(row => row.actual > row.initial_high).length, 2)
  for (const row of completed) {
    assert.ok(row.initial_source.published_at < row.actual_source.published_at)
    assert.match(row.initiality, /Earliest captured/)
    const midpoint = (row.initial_low + row.initial_high) / 2
    close(row.variance, row.actual - midpoint)
    close(row.variance_pct, row.variance / midpoint * 100)
    const dates = row.revisions.map(revision => revision.published_at)
    assert.deepEqual(dates, [...dates].sort())
    assert.ok(dates.every(date => date > row.initial_source.published_at && date < row.actual_source.published_at))
  }
  const pending = metaCapexGuidance.comparisons.at(-1)
  assert.equal(pending.period, 'FY2026')
  assert.equal(pending.actual, null)
  assert.equal(pending.variance, undefined)
  assert.equal(pending.within_range, undefined)
  assert.deepEqual(pending.revisions.map(row => [row.low, row.high]), [[125, 145], [130, 145]])
  assert.match(metaCapexGuidance.coverage, /not an exhaustive/)
  assert.ok(metaTrends.gaps.some(gap => /FY2021 and FY2022/.test(gap)))
})

test('public provenance is self-contained and every source is dated before the research cutoff', () => {
  assert.equal(Object.keys(bundle.metafile.inputs).length, 1, 'Only authored public facts enter this module')
  assert.equal(META_TRENDS_AS_OF, '2026-09-26')
  const ids = new Set(metaTrends.sources.map(source => source.source_id))
  for (const source of metaTrends.sources) {
    const url = new URL(source.url)
    assert.equal(url.protocol, 'https:')
    assert.ok(['investor.atmeta.com', 's21.q4cdn.com'].includes(url.hostname))
    assert.ok(source.published_at <= META_TRENDS_AS_OF)
  }
  for (const metric of metaTrendSeries) for (const point of metric.points) assert.ok(ids.has(point.source_id))
  assert.equal(metaTrends.series, metaTrendSeries)
  assert.equal(metaTrends.capex_guidance, metaCapexGuidance)
})
