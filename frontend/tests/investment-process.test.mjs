import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'

const source = await readFile(new URL('../src/components/investmentProcessModel.ts', import.meta.url), 'utf8')
const javascript = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
const { selectedResearchTicker, earningsReviewForRun, earningsReviewHref, earningsReviewLinkLabel, initialEarningsSelection, replacementEarningsSelection, newerSourceCoverage, earningsStageLabel } = await import(`data:text/javascript;base64,${Buffer.from(javascript).toString('base64')}`)

test('company display names cannot hide the saved earnings receipt or select a different candidate', () => {
  const run = { ticker: 'COST', investment_process: { earnings: [{ ticker: 'COST', workflow_id: 'wf_frozen', status: 'partial' }] } }
  assert.equal(selectedResearchTicker(run, { instrument: 'Costco Wholesale Corporation common stock' }, 1), 'COST')
  assert.equal(earningsReviewForRun(run, selectedResearchTicker(run, { instrument: 'Costco Wholesale Corporation common stock' }, 1)).workflow_id, 'wf_frozen')
  assert.equal(selectedResearchTicker(run, { ticker: 'aapl' }, 2), 'AAPL')
  assert.equal(selectedResearchTicker(run, { instrument: 'Unknown common stock' }, 2), undefined)
})

test('assessment receipt retains its own earnings review instead of a later ticker refresh', () => {
  const run = { ticker: 'COST', origin_ref: 'workflow:wf_older', investment_process: { earnings: [{ ticker: 'COST', workflow_id: 'wf_frozen', status: 'partial', fiscal_period: 'Q4 FY2026' }] } }
  assert.equal(earningsReviewForRun(run, 'COST').workflow_id, 'wf_frozen')
  assert.equal(earningsReviewForRun(run, 'COST').fiscal_period, 'Q4 FY2026')
  assert.equal(earningsReviewForRun(run, 'AAPL'), undefined)
})

test('multi-company assessments select only the matching ticker receipt', () => {
  const run = { investment_process: { earnings: [{ ticker: 'COST', workflow_id: 'wf_cost' }, { ticker: 'AAPL', workflow_id: 'wf_aapl' }] } }
  assert.equal(earningsReviewForRun(run, 'aapl').workflow_id, 'wf_aapl')
  assert.equal(earningsReviewForRun(run), undefined)
})

test('legacy earnings assessments preserve the explicit workflow link', () => {
  assert.deepEqual(earningsReviewForRun({ ticker: 'COST', origin_ref: 'workflow:wf_saved' }, 'COST'), { ticker: 'COST', workflow_id: 'wf_saved', status: 'saved' })
  assert.equal(earningsReviewForRun({ ticker: 'AAPL', origin_ref: 'workflow:wf_saved' }, 'COST'), undefined)
  assert.equal(earningsReviewForRun({ ticker: 'COST' }, 'COST'), undefined)
})

test('an explicit saved-review link wins over stale browser selection', () => {
  assert.equal(initialEarningsSelection('?review=demo&earnings=wf_frozen', 'wf_deleted'), 'wf_frozen')
  assert.equal(initialEarningsSelection('?review=demo', 'wf_older'), 'wf_older')
  assert.equal(initialEarningsSelection('', null), '')
  assert.equal(earningsReviewHref('wf_a&b'), '?earnings=wf_a%26b#research/earnings')
})

test('a removed review recovers to a retained review and an empty history remains empty', () => {
  const newest = { id: 'wf_latest', ticker: 'COST' }
  assert.equal(replacementEarningsSelection([newest, { id: 'wf_old' }], 'wf_deleted'), newest)
  assert.equal(replacementEarningsSelection([], 'wf_deleted'), undefined)
  assert.equal(replacementEarningsSelection([{ id: 'wf_deleted' }], 'wf_deleted'), undefined)
})

test('earnings stage shows collection and evidence gaps without claiming completion', () => {
  assert.equal(earningsStageLabel({ status: 'collecting' }), 'Collecting materials')
  assert.equal(earningsStageLabel({ status: 'partial', workflow_id: 'wf_saved' }), 'Saved · gaps remain')
  assert.equal(earningsStageLabel({ status: 'unavailable' }), 'Evidence unavailable')
  assert.equal(earningsStageLabel(), 'Not yet reviewed')
})

test('links distinguish saved reviews from unfinished, unavailable or inapplicable collection', () => {
  for (const status of ['collecting', 'queued', 'failed', 'unavailable', 'not_applicable']) {
    assert.equal(earningsReviewLinkLabel({ status, workflow_id: 'wf_collection' }), 'View earnings collection')
    assert.equal(earningsReviewLinkLabel({ status }, true), 'View latest earnings collection')
  }
  assert.equal(earningsReviewLinkLabel({ status: 'partial' }), 'Open earnings review used in this assessment')
  assert.equal(earningsReviewLinkLabel({ status: 'completed' }, true), 'Open latest saved earnings review')
})

test('source updates follow explicit revision lineage without replacing frozen assessments', () => {
  const saved = { id: 'wf_saved', ticker: 'COST', status: 'partial', created_at: '2026-09-24' }
  const first = { ...saved, id: 'wf_first', source_refresh_of: saved.id, created_at: '2026-09-25' }
  const second = { ...saved, id: 'wf_second', source_refresh_of: first.id, created_at: '2026-09-26' }
  const other = { ...saved, id: 'wf_other', created_at: '2026-09-27' }
  const history = [second, other, first, saved]
  assert.equal(newerSourceCoverage(history, saved.id, 'COST'), second)
  assert.equal(newerSourceCoverage(history, second.id, 'COST'), undefined)
  assert.equal(newerSourceCoverage(history, saved.id, 'NKE'), undefined)
  assert.equal(newerSourceCoverage([{ ...second, status: 'running' }, first, saved], saved.id, 'COST'), first)
  assert.equal(earningsReviewForRun({ ticker: 'COST', origin_ref: 'workflow:wf_saved' }, 'COST').workflow_id, saved.id)
  assert.deepEqual(history, [second, other, first, saved])
})
