import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'
const source = await readFile(new URL('../src/components/priceTargetModel.ts', import.meta.url), 'utf8')
const javascript = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
const { buildPriceTargetModel, targetNumber, targetPrice, targetSourceUrl, libraryTargetIsLoading, targetProseParts, targetCitationRows, targetInputDisplay, targetInputLabel, targetTimeExplanation, targetTodayCalculation, contextNumber, contextNumberLabel, earningsQuarterLabel, historicalPeSegments, historicalMultipleStats, valuationMethodView, valuationMethodLabel } = await import(`data:text/javascript;base64,${Buffer.from(javascript).toString('base64')}`)
const valuation = {
  status: 'complete', selected_method: 'eps_multiple', currency: 'USD', horizon: '12 months', as_of: '2026-09-25', rationale: 'Growth and the exit multiple determine fair value.',
  scenarios: { bear: '80', base: '120', bull: '160' },
  methods: [{ name: 'eps_multiple', status: 'complete', supported: true, formula: 'Forecast EPS × exit P/E', source_refs: ['annual'],
    inputs: [{ key: 'baseline_eps', value: '4', kind: 'fact', source_refs: ['annual'] }],
    scenario_calculations: {
      base: { inputs: [{ key: 'exit_pe', value: '25', kind: 'assumption', rationale: 'Base multiple.' }], formula: '4.8 × 25', output_price: '120' },
      bear: { inputs: [{ key: 'exit_pe', value: '20', kind: 'assumption', source_refs: ['peer'], rationale: 'Multiple compression.' }], formula: '4 × 20', output_price: '80' },
    },
  }],
}
test('canonical targets carry the selected scenario bridge and source relationships', () => {
  const result = buildPriceTargetModel(valuation)
  assert.equal(result.status, 'complete')
  assert.equal(result.base, '120')
  assert.equal(result.horizon, '12 months')
  assert.equal(result.scenarios[0].calculation.formula, '4 × 20')
  assert.equal(result.scenarios[1].calculation.inputs[0].kind, 'assumption')
  assert.equal(result.scenarios[2].calculation, null)
  assert.deepEqual(result.sourceIds, ['annual', 'peer'])
})
test('missing bear and bull targets remain missing and are never extrapolated', () => {
  const result = buildPriceTargetModel({ ...valuation, scenarios: { base: '120' } })
  assert.deepEqual(result.scenarios.map((item) => item.value), [null, '120', null])
  assert.equal(targetPrice(null, 'USD'), 'Not supplied')
})
test('incomplete canonical valuation cannot silently use a model proposed future target', () => {
  const result = buildPriceTargetModel({ status: 'unavailable', scenarios: {} }, { futureTarget: { value: '900', currency: 'USD' } })
  assert.equal(result.base, null)
  assert.equal(result.status, 'unavailable')
  assert.ok(result.missing.some((item) => item.includes('base price target')))
})
test('earlier uncalculated targets stay explicitly legacy', () => {
  const result = buildPriceTargetModel(null, { futureTarget: { value: '100', currency: 'CAD', horizon: '6 months', basis: 'Earlier analyst view.', source_refs: ['old'] } })
  assert.equal(result.status, 'legacy')
  assert.equal(result.base, '100')
  assert.equal(result.method, null)
  assert.equal(result.rationale, 'Earlier analyst view.')
  assert.deepEqual(result.sourceIds, ['old'])
})
test('invalid price values do not become a zero, number, or completed assessment', () => {
  for (const value of [null, undefined, '', 'N/A', 'USD 120', '1,200', Infinity, NaN, '-5', '0', 0]) assert.equal(targetNumber(value), null)
  const result = buildPriceTargetModel({ ...valuation, scenarios: { base: 'N/A' } })
  assert.equal(result.status, 'unavailable')
  assert.equal(result.base, null)
})
test('an unsupported method or missing denomination cannot be labelled complete', () => {
  assert.equal(buildPriceTargetModel({ ...valuation, currency: null }).status, 'partial')
  assert.equal(buildPriceTargetModel({ ...valuation, methods: [{ name: 'eps_multiple', supported: false }] }).status, 'partial')
})
test('rationale follows recorded valuation then selected method then reconciliation', () => {
  const result = buildPriceTargetModel({ ...valuation, rationale: null, reconciliation_rationale: 'Cross-check agreed.', methods: [{ ...valuation.methods[0], rationale: 'Selected EPS rationale.' }] })
  assert.equal(result.rationale, 'Selected EPS rationale.')
})
test('valuation citation links reject unsafe URLs and embedded credentials', () => {
  for (const url of ['javascript:alert(1)', 'file:///tmp/x', 'data:text/plain,test', 'https://user:secret@example.com']) assert.equal(targetSourceUrl(url), null)
  assert.equal(targetSourceUrl('https://example.com/report'), 'https://example.com/report')
})

test('current preparation shows progress only while that review is active', () => {
  const input = { revision: 'current', packetProvenance: 'saved_analyst_target', packetRunId: 'new', latestRunId: 'new', runStatus: 'running', hasPackets: true }
  assert.equal(libraryTargetIsLoading(input), true)
  assert.equal(libraryTargetIsLoading({ ...input, revision: 'old-revision' }), false)
  assert.equal(libraryTargetIsLoading({ ...input, packetProvenance: 'canonical_case' }), false)
  assert.equal(libraryTargetIsLoading({ ...input, packetRunId: 'old' }), false)
  for (const runStatus of ['completed', 'failed', 'paused', 'cancelled']) assert.equal(libraryTargetIsLoading({ ...input, runStatus }), false)
  assert.equal(libraryTargetIsLoading({ ...input, packetRunId: undefined, hasPackets: false }), true)
})

test('target prose citations preserve source identity, original location and surrounding reasoning', () => {
  const parts = targetProseParts('Annual EPS [src_abc123:L1] supports growth; risk (src_def456 L22-L25) affects the multiple.')
  assert.deepEqual(parts.filter((part) => part.sourceId).map(({ sourceId, locator }) => ({ sourceId, locator })), [
    { sourceId: 'src_abc123', locator: 'L1' }, { sourceId: 'src_def456', locator: 'L22-L25' },
  ])
  assert.equal(parts.filter((part) => !part.sourceId).map((part) => part.text).join(''), 'Annual EPS  supports growth; risk  affects the multiple.')
  assert.equal(parts[1].original, '[src_abc123:L1]')
})
test('citation numbering is stable across scenarios and missing sources stay explicit', () => {
  const sources = [{ id: 'src_annual', title: 'Annual results', locator: 'L1' }, { id: 'src_call', title: 'Call' }, { id: 'src_unrelated', title: 'Unrelated' }]
  const rows = targetCitationRows(['src_annual'], { base: 'EPS src_annual:L1 and growth src_call:L20-L24.', bear: 'Risk [src_call:L30] and src_missing:L8.' }, sources)
  assert.deepEqual(rows.map(({ id, number }) => [id, number]), [['src_annual', 1], ['src_call', 2], ['src_missing', 3]])
  assert.deepEqual(rows[1].locators, ['L20-L24', 'L30'])
  assert.equal(rows[2].source, undefined)
  assert.deepEqual(rows[2].locators, ['L8'])
  assert.equal(rows.some((row) => row.id === 'src_unrelated'), false)
})
test('multiple citations, code wrappers and uncited prose keep their original relationships', () => {
  const parts = targetProseParts('See [src_a:L5, src_b:L8-L10] and `src_a:L12` for the bridge.')
  assert.deepEqual(parts.filter((part) => part.sourceId).map((part) => [part.sourceId, part.locator]), [['src_a', 'L5'], ['src_b', 'L8-L10'], ['src_a', 'L12']])
  assert.deepEqual(targetProseParts('No source is attached to this assumption.'), [{ text: 'No source is attached to this assumption.' }])
})
test('canonical historical price field remains visible only as a legacy target', () => {
  const result = buildPriceTargetModel(null, { futureTarget: { price: '980.5', currency: 'USD', horizon: '12 months' } })
  assert.equal(result.base, '980.5')
  assert.equal(result.status, 'legacy')
  assert.equal(buildPriceTargetModel({ status: 'unavailable' }, { futureTarget: { price: '980.5' } }).base, null)
})
test('visible growth and multiples use financial notation without changing other raw values', () => {
  assert.deepEqual(targetInputDisplay('annual_eps_growth', '0.12'), { value: '12%', unitHandled: true })
  assert.deepEqual(targetInputDisplay('annual_eps_growth', '-0.035'), { value: '-3.5%', unitHandled: true })
  assert.deepEqual(targetInputDisplay('exit_pe', '44'), { value: '44×', unitHandled: true })
  assert.deepEqual(targetInputDisplay('baseline_eps', '19.82'), { value: '19.82', unitHandled: false })
  assert.deepEqual(targetInputDisplay('annual_eps_growth', 'Not supplied'), { value: 'Not supplied', unitHandled: false })
})

test('inline rationale resolves a frozen transcript independently from financial operand refs and preserves multiple locators', () => {
  const transcriptId = 'src_b6db1f00d0bb4b9f8b718cce605bb8f8'
  const rationale = `Growth reflects renewals (${transcriptId}:L97-L103,L118-L121).`
  const sources = [
    { id: 'src_eps', title: 'SEC annual EPS', source_version: '1', content_hash: 'eps-hash' },
    { id: transcriptId, title: 'Earnings call', source_version: '1', content_hash: 'call-hash', url: 'https://example.com/call' },
  ]
  const rows = targetCitationRows(['src_eps'], { rationale }, sources)
  assert.deepEqual(rows.map((row) => row.id), ['src_eps', transcriptId])
  assert.equal(rows[1].source, sources[1])
  assert.equal(rows[1].source.content_hash, 'call-hash')
  assert.deepEqual(rows[1].locators, ['L97-L103,L118-L121'])
  const citation = targetProseParts(rationale).find((part) => part.sourceId)
  assert.equal(citation.sourceId, transcriptId)
  assert.equal(citation.locator, 'L97-L103,L118-L121')
  assert.equal(citation.original, `(${transcriptId}:L97-L103,L118-L121)`)
  assert.equal(targetCitationRows(['src_eps'], { rationale }, sources.slice(0, 1))[1].source, undefined)
})

test('earnings forecast span is explained separately from the holding horizon', () => {
  const explanation = targetTimeExplanation({ inputs: [{ key: 'baseline_eps', period: 'FY2025' }], intermediate_results: { forecast_years: '2' } }, '12 months')
  assert.match(explanation, /covers 2 years/)
  assert.match(explanation, /FY2025/)
  assert.match(explanation, /holding horizon is 12 months from the assessment date/)
  assert.equal(targetTimeExplanation({ intermediate_results: {} }, '12 months'), null)
  assert.equal(targetInputLabel('forecast_years'), 'Years from earnings baseline to forecast')
  assert.equal(targetInputLabel('horizon_months'), 'Holding horizon · months')
})

test('quarterly EPS preserves losses and zeros without turning missing values into actuals', () => {
  assert.equal(contextNumber('-0.80'), -0.8)
  assert.equal(contextNumber('0'), 0)
  assert.equal(contextNumberLabel('0'), '0.00')
  for (const value of [null, undefined, '', ' ', 'N/A', NaN, Infinity, '1,200']) assert.equal(contextNumber(value), null)
  assert.equal(earningsQuarterLabel('projection'), 'Projection')
  assert.equal(earningsQuarterLabel('reported'), 'Reported actual')
  assert.equal(earningsQuarterLabel('missing'), 'Evidence missing')
  assert.equal(earningsQuarterLabel('unexpected'), 'Evidence missing')
})

test('historical multiple chart leaves gaps where price or reported earnings cannot support a P/E', () => {
  const segments = historicalPeSegments([
    { date: '2025-01-31', pe: '35' }, { date: '2025-02-28', pe: '37' },
    { date: '2025-03-31', pe: null }, { date: '2025-04-30', pe: '40' },
    { date: '2025-05-31', pe: '-2' }, { date: 'bad-date', pe: '44' },
    { date: '2025-07-31', pe: '41' },
  ])
  assert.deepEqual(segments.map((segment) => segment.map((point) => point.value)), [[35, 37], [40], [41]])
  assert.equal(segments[2][0].index, 6)
  assert.deepEqual(historicalPeSegments([{ date: '2025-01-31', pe: '35' }, { date: '2025-03-31', pe: '40' }]).map((segment) => segment.length), [1, 1])
})

test('research context source links stay within the saved valuation and are not borrowed by old targets', () => {
  const research_context = {
    earnings_bridge: { quarters: [{ period: 'Q1', kind: 'reported', value: '4.5', source_refs: ['src_ir'] }, { period: 'Q4', kind: 'projection', value: '5', source_refs: ['src_prior_q4'] }] },
    historical_pe: { points: [{ date: '2025-01-31', close: '800', ttm_eps: '20', pe: '40', source_refs: ['src_price', 'src_ir'] }] },
    implied_today: { scenarios: { base: '840' }, source_refs: ['src_ir'] },
  }
  const model = buildPriceTargetModel({ ...valuation, research_context })
  assert.equal(model.researchContext, research_context)
  assert.deepEqual(model.sourceIds, ['annual', 'peer', 'src_ir', 'src_prior_q4', 'src_price'])
  assert.equal(buildPriceTargetModel(valuation).researchContext, null)
  assert.equal(buildPriceTargetModel(null, { futureTarget: { value: '120' } }).researchContext, null)
})

test('implied value today displays its current reported EPS and selected scenario multiple', () => {
  const context = { implied_today: { status: 'available', eps: '20.76', currency: 'USD', scenarios: { base: '871.92', bear: '664.32' } } }
  const base = { intermediate_results: { forecast_diluted_eps: '23.2512', exit_pe: '42' } }
  assert.equal(targetTodayCalculation(context, 'base', base), 'Reported EPS 20.76 × P/E 42 = USD 871.92')
  assert.equal(targetTodayCalculation(context, 'bear', { intermediate_results: { exit_pe: '32' } }), 'Reported EPS 20.76 × P/E 32 = USD 664.32')
  assert.equal(targetTodayCalculation({ implied_today: { ...context.implied_today, status: 'unavailable' } }, 'base', base), null)
  assert.equal(targetTodayCalculation({ implied_today: { ...context.implied_today, eps: null } }, 'base', base), null)
  assert.equal(targetTodayCalculation(context, 'base', { intermediate_results: { forecast_diluted_eps: '23.2512' } }), null)
})

test('current earnings component citations are retained even without historical price observations', () => {
  const research_context = { current_earnings: { source_refs: ['src_annual'], components: [{ value: '20', source_refs: ['src_annual'] }, { value: '4', source_refs: ['src_ytd'] }] } }
  assert.deepEqual(buildPriceTargetModel({ ...valuation, research_context }).sourceIds, ['annual', 'peer', 'src_annual', 'src_ytd'])
})


test('historical band chart excludes missing and out-of-window samples without pinning axis to zero', () => {
  const stats = historicalMultipleStats([{ date: '2018-01-01', pe: 1000 }, { date: '2026-01-31', pe: 10 }, { date: '2026-02-28', pe: null }, { date: '2026-03-31', pe: 12 }, { date: '2027-01-01', pe: 1000 }], '2026-09-25')
  assert.equal(stats.mean, 11)
  assert.equal(stats.deviation, 1)
  assert.deepEqual(stats.bands.map((band) => band.value), [8, 9, 10, 11, 12, 13, 14])
  assert.ok(stats.minimum > 0 && stats.minimum < 8)
  assert.ok(stats.maximum > 14)
  assert.equal(historicalPeSegments(stats.points).length, 2)
})
test('constant and one-point history keeps a usable nonzero axis and does not fake dispersion', () => {
  const single = historicalMultipleStats([{ date: '2026-01-31', pe: 40 }])
  assert.equal(single.deviation, null)
  assert.ok(single.minimum < 40 && single.maximum > 40 && single.minimum > 0)
  const constant = historicalMultipleStats([{ date: '2026-01-31', pe: 40 }, { date: '2026-02-28', pe: 40 }])
  assert.equal(constant.deviation, 0)
  assert.deepEqual(constant.bands, [{ sigma: 0, value: 40 }])
})
test('switching comparable method changes displayed arithmetic without mutating the saved decision', () => {
  const second = { name: 'ps_multiple', status: 'complete', supported: true, output_prices: { base: '220' }, intermediate_results: { implied_today: { status: 'available', scenarios: { base: '200' } } } }
  const original = { ...valuation, methods: [...valuation.methods, second] }
  const result = valuationMethodView(original, 'ps_multiple')
  assert.equal(result.scenarios.base, '220')
  assert.equal(result.research_context.implied_today.scenarios.base, '200')
  assert.equal(original.selected_method, 'eps_multiple')
  assert.equal(original.scenarios.base, '120')
  assert.equal(valuationMethodView(original, 'missing'), original)
  assert.equal(valuationMethodLabel('ev_ebitda'), 'EV/EBITDA · operating profit')
})

test('quarterly provider history connects consecutive quarters but leaves missing quarters open', () => {
  const points = [{ date: '2025-12-31', pe: 4 }, { date: '2026-03-31', pe: 5 }, { date: '2026-09-30', pe: 6 }]
  assert.deepEqual(historicalPeSegments(points, 'quarterly').map(row => row.length), [2, 1])
  assert.deepEqual(historicalPeSegments(points).map(row => row.length), [1, 1, 1])
})

test('annual provider history connects adjacent year-ends but never fills a missing year', () => {
  const points = [{ date: '2021-12-31', pe: 20 }, { date: '2022-12-31', pe: 22 }, { date: '2024-12-31', pe: 26 }, { date: '2025-12-31', pe: 28 }]
  assert.deepEqual(historicalPeSegments(points, 'annual').map(row => row.map(point => point.date)), [
    ['2021-12-31', '2022-12-31'], ['2024-12-31', '2025-12-31'],
  ])
  assert.deepEqual(historicalPeSegments(points, 'quarterly').map(row => row.length), [1, 1, 1, 1])
  assert.deepEqual(historicalPeSegments(points).map(row => row.length), [1, 1, 1, 1])
})

test('five annual samples receive equal weight in the five-year average and deviation bands', () => {
  const points = [20, 22, 24, 26, 28].map((pe, index) => ({ date: `${2021 + index}-12-31`, pe }))
  const stats = historicalMultipleStats([{ date: '2020-12-31', pe: 1000 }, ...points], '2026-09-26')
  assert.equal(stats.points.length, 5)
  assert.equal(stats.mean, 24)
  assert.equal(stats.deviation, Math.sqrt(8))
  assert.deepEqual(stats.bands.map(band => band.sigma), [-3, -2, -1, 0, 1, 2, 3])
  assert.ok(stats.minimum > 0)
  assert.ok(stats.minimum < stats.bands[0].value && stats.maximum > stats.bands[6].value)
})
