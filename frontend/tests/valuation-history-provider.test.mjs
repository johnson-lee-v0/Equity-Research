import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

const require = createRequire(import.meta.url)
const { build } = createRequire(require.resolve('vite'))('esbuild')
const bundle = await build({ entryPoints: [fileURLToPath(new URL('../src/components/ValuationResearchContext.tsx', import.meta.url))], bundle: true, write: false, platform: 'node', format: 'cjs', packages: 'external', jsx: 'automatic', logLevel: 'silent' })
const compiled = { exports: {} }
new Function('require', 'module', 'exports', bundle.outputFiles[0].text)(require, compiled, compiled.exports)
const Component = compiled.exports.default
const provider = {
  metric: 'P/S', provenance: 'secondary_provider', provider: 'Stock Analysis', sampling: 'quarterly',
  as_of: '2026-09-26', source_url: 'https://stockanalysis.com/stocks/abc/financials/ratios/?p=quarterly',
  basis: 'Provider-computed quarter-end ratios; financials may be restated.',
  points: [{ date: '2026-03-31', multiple: '7.5', period_label: 'Q1 2026' }, { date: '2026-06-30', multiple: '8.1', period_label: 'Q2 2026' }],
}
const render = (context, props = {}) => renderToStaticMarkup(React.createElement(Component, { context, sourceLinks: () => null, prose: value => value, ...props }))

test('a supported provider history supplies actual chart numbers without inventing accounting operands', () => {
  const html = render({ historical_multiples: { 'P/S': { points: [] } }, provider_multiples: { 'P/S': provider } })
  assert.match(html, /Historical P\/S/)
  assert.match(html, /Provider comparison/)
  assert.match(html, /7.50×/)
  assert.match(html, /8.10×/)
  assert.match(html, /2 quarter-end observations/)
  assert.match(html, /Stock Analysis/)
  assert.doesNotMatch(html, /financials already reported at each observation date|Trailing EPS|<th scope="col">Share price/)
  assert.match(html, /saved decision and target are unchanged/)
})

test('available primary history stays separate from provider values and bands', () => {
  const html = render({ historical_multiples: { 'P/S': { metric: 'P/S', points: [{ date: '2026-06-30', multiple: '4' }] } }, provider_multiples: { 'P/S': provider } })
  assert.match(html, /P\/S · provider/)
  assert.match(html, /4.00×/)
  assert.doesNotMatch(html, /7.50×|8.10×|Provider comparison/)
})

test('five annual provider samples are labeled as year-ends, not monthly or quarterly trading history', () => {
  const annual = {
    ...provider, sampling: 'annual', source_url: 'https://stockanalysis.com/stocks/abc/financials/ratios/',
    basis: 'Provider-computed fiscal year-end ratios.', min: 20, max: 28,
    points: [20, 22, 24, 26, 28].map((multiple, index) => ({ date: `${2021 + index}-12-31`, multiple, period_label: `FY${2021 + index}` })),
  }
  const html = render({ provider_multiples: { 'P/S': annual } })
  assert.match(html, /5 year-end observations/)
  assert.match(html, /Five-year annual sample average<\/dt><dd>24\.00×/)
  assert.match(html, /Each observed year-end has equal weight; this is not a daily trading average/)
  assert.doesNotMatch(html, /quarter-end observations|monthly observations|latest snapshot|60 observations/)
  assert.equal((html.match(/<polyline /g) || []).length, 1)
  assert.equal((html.match(/class="price-target-pe-point"/g) || []).length, 5)
  for (const label of ['+1 SD', '+2 SD', '+3 SD', '−1 SD', '−2 SD', '−3 SD']) assert.ok(html.includes(label))
  assert.match(html, /https:\/\/stockanalysis\.com\/stocks\/abc\/financials\/ratios\//)
})

test('manual refresh is offered only for a valid ticker in the real workspace', () => {
  const context = { provider_multiples: { 'P/S': provider } }
  assert.match(render(context, { namespace: 'real', ticker: 'ABC' }), /Update multiple history/)
  assert.doesNotMatch(render(context, { namespace: 'demo', ticker: 'ABC' }), /Update multiple history/)
  assert.doesNotMatch(render(context, { namespace: 'real', ticker: 'ABC, OTHER' }), /Update multiple history/)
})
