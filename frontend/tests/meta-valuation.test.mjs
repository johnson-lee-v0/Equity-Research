import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

const require = createRequire(import.meta.url)
const { build } = createRequire(require.resolve('vite'))('esbuild')
const bundle = await build({ entryPoints: [fileURLToPath(new URL('../src/demo/metaValuation.ts', import.meta.url))], bundle: true, write: false, platform: 'node', format: 'cjs', logLevel: 'silent' })
const compiled = { exports: {} }
new Function('require', 'module', 'exports', bundle.outputFiles[0].text)(require, compiled, compiled.exports)
const { metaMarket, metaTtm, metaReported, metaEnterpriseBridge, metaBaselineFacts, metaHistoricalContext, metaEarningsBridge, calculateMetaPrice, valuationMethods, metaScenarios, metaEntry } = compiled.exports
const close = (actual, expected, tolerance = 1e-9) => assert.ok(Math.abs(actual - expected) < tolerance, `${actual} is not within ${tolerance} of ${expected}`)

test('public META operands retain actual periods, source links, and common versus diluted share bases', () => {
  assert.equal(metaMarket.price, 751.66)
  assert.equal(metaMarket.date, '2026-09-25')
  close(metaMarket.sharesBn, 2.547506225)
  assert.equal(metaMarket.sharesDate, '2026-07-24')
  assert.notEqual(metaMarket.sharesBn, metaReported.q2_2026.dilutedWeightedAverageSharesMillions / 1000)
  assert.match(metaMarket.sharesSource, /sec\.gov\/Archives\/.*meta-20260630\.htm$/)
  for (const fact of metaBaselineFacts) {
    assert.ok(fact.period && fact.unit && fact.basis)
    assert.ok(fact.sourceUrls.length)
    for (const url of fact.sourceUrls) assert.match(url, /^https:\/\/(investor\.atmeta\.com|www\.sec\.gov|stockanalysis\.com)\//)
  }
})

test('trailing financials combine annual and comparable half-year flows without invented D&A', () => {
  close(metaTtm.dilutedEps, 26.55)
  close(metaTtm.revenueBn, 228.247)
  close(metaTtm.operatingIncomeBn, 86.926)
  close(metaTtm.depreciationAmortizationBn, 22.729)
  close(metaTtm.ebitdaBn, 109.655)
  assert.match(metaTtm.taxNote, /6\.20.*3\.13/)
  assert.match(metaTtm.ebitdaBasis, /cash-flow depreciation and amortization/)
  assert.match(metaTtm.epsBasis, /changing quarterly share weights/)
})

test('today has no growth step and a 12-month target compounds exactly once', () => {
  const result = calculateMetaPrice('P/E', 8, 26, 12)
  close(result.today, 690.30)
  close(result.future, 745.524)
  close(result.upsidePercent, (745.524 / 751.66 - 1) * 100)
  close(calculateMetaPrice('P/E', 8, 26, 0).future, result.today)
  close(calculateMetaPrice('P/E', 8, 26, 6).future, result.today * Math.sqrt(1.08))
  close(calculateMetaPrice('P/E', 8, 26, 24).future, result.today * 1.08 ** 2)
  close(calculateMetaPrice('P/E', 20, 26).today, result.today)
})

test('revenue and book use actual common shares; enterprise claims remain fixed instead of growing with EBITDA', () => {
  close(calculateMetaPrice('P/S', 8, 8).today, 228.247 * 8 / 2.547506225)
  close(calculateMetaPrice('P/book', 8, 7).today, 261.221 * 7 / 2.547506225)
  close(metaEnterpriseBridge.netClaimsBn, 22.058)
  const enterprise = calculateMetaPrice('EV/EBITDA', 8, 17)
  close(enterprise.today, (109.655 * 17 - 22.058) / 2.547506225)
  close(enterprise.future, (109.655 * 1.08 * 17 - 22.058) / 2.547506225)
  assert.equal(metaEnterpriseBridge.preferredClaimsBn, null)
  assert.equal(metaEnterpriseBridge.minorityClaimsBn, null)
  assert.match(metaEnterpriseBridge.basis, /Provider enterprise value minus provider market capitalization/)
  assert.match(metaEnterpriseBridge.limitation, /not an independently audited/)
})

test('no invented NAV or invalid price can leak out of the sensitivity calculation', () => {
  assert.equal(valuationMethods.find(method => method.key === 'P/NAV').available, false)
  assert.equal(calculateMetaPrice('P/NAV', 8, 1).future, null)
  assert.match(calculateMetaPrice('P/NAV', 8, 1).reason, /no sourced equity NAV/)
  for (const args of [['P/E', NaN, 26], ['P/E', 8, Infinity], ['P/E', -100, 26], ['P/E', 8, 0], ['P/E', 8, 26, -12]]) {
    assert.equal(calculateMetaPrice(...args).future, null)
  }
})

test('unfinished FY2026 keeps two reported quarters and projects only Q3/Q4 with disclosed exceptional-tax treatment', () => {
  const bridge = metaEarningsBridge(8)
  assert.deepEqual(bridge.quarters.map(quarter => quarter.kind), ['reported', 'reported', 'projection', 'projection'])
  assert.deepEqual(bridge.quarters.map(quarter => quarter.value), [10.44, 6.18, 7.83, 9.5904])
  close(bridge.reported_total, 16.62)
  close(bridge.projected_total, 17.4204)
  close(bridge.full_year_total, 34.0404)
  assert.match(bridge.quarters[0].rationale, /3\.13.*tax benefit/)
  assert.match(bridge.quarters[2].rationale, /1\.05.*6\.20.*7\.25/)
  assert.match(bridge.coverage_note, /separate.*12-month/)
  assert.deepEqual(metaEarningsBridge(20).quarters.slice(0, 2), bridge.quarters.slice(0, 2))
  assert.equal(metaEarningsBridge(NaN).full_year_total, null)
  for (const quarter of bridge.quarters) assert.ok(quarter.source_refs[0].startsWith('https://investor.atmeta.com/'))
})

test('historical multiples are exactly five dated annual provider observations with no invented months or NAV', () => {
  const context = metaHistoricalContext()
  assert.deepEqual(Object.keys(context.historical_multiples), ['P/E', 'P/S', 'EV/EBITDA', 'P/book'])
  for (const [metric, history] of Object.entries(context.historical_multiples)) {
    assert.equal(history.sampling, 'annual')
    assert.equal(history.provenance, 'secondary_provider')
    assert.equal(history.points.length, 5)
    assert.deepEqual(history.points.map(point => point.date), ['2021-12-31', '2022-12-31', '2023-12-31', '2024-12-31', '2025-12-31'])
    assert.match(history.coverage_note, /restated.*five annual samples only/)
    for (const point of history.points) assert.equal(point.source_url, history.source_url)
    assert.equal(history.min, Math.min(...history.points.map(point => point.multiple)), metric)
  }
  assert.deepEqual(context.historical_multiples['P/E'].points.map(point => point.multiple), [23.77, 13.6, 23.28, 23.7, 27.52])
  assert.deepEqual(context.historical_multiples['EV/EBITDA'].points.map(point => point.multiple), [16.28, 7.11, 15.29, 17.17, 16.39])
})

test('scenario and watchlist outcomes share the same sourced baseline and disclose editable assumptions', () => {
  assert.deepEqual(metaScenarios.map(item => item.label), ['Bear', 'Base', 'Bull'])
  close(metaScenarios[0].future, 477.90)
  close(metaScenarios[1].future, 745.524)
  close(metaScenarios[2].future, 1019.52)
  for (const scenario of metaScenarios) {
    assert.ok(scenario.rationale)
    assert.equal(scenario.horizonMonths, 12)
    close(scenario.future, calculateMetaPrice('P/E', scenario.growthPercent, scenario.multiple).future)
  }
  close(metaEntry.price, 552.24)
  assert.equal(metaEntry.marginOfSafetyPercent, 20)
  assert.match(metaEntry.assumption, /implied price today/)
})

test('enterprise value below fixed claims never becomes a negative market price', () => {
  const result = calculateMetaPrice('EV/EBITDA', 8, 0.1)
  assert.equal(result.today, null)
  assert.equal(result.future, null)
  assert.equal(result.upsidePercent, null)
  assert.match(result.reason, /below the fixed claims/)
  assert.ok(calculateMetaPrice('EV/EBITDA', 8, 17).future > 0)
})
